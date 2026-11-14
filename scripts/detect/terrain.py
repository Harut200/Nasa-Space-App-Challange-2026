"""Terrain mask: where can open water physically exist? (flat, low-lying, visible to the radar)

    .venv/bin/python -m scripts.detect.terrain            # all three areas
    .venv/bin/python -m scripts.detect.terrain sevan      # one area
    .venv/bin/python -m scripts.detect.terrain --report   # also measure the effect vs JRC (reporting only)

Elevation: Copernicus DEM (GLO-30 where public, GLO-90 otherwise -- GLO-30 is NOT published for
Armenia), read remotely from the public AWS bucket via GDAL /vsicurl/ and cached as one GeoTIFF clip
per area in data/external/dem/. All numeric work is PyTorch.

Copernicus DEM (c) DLR e.V. 2010-2014 and (c) Airbus Defence and Space GmbH 2014-2018, provided under
COPERNICUS by the European Union and ESA.

Write-up: docs/eda/terrain.md. Pipeline hook (done by the lead):
    from scripts.detect.terrain import water_possible
    water = water & water_possible(area, (H, W)).to(water.device)
"""
from __future__ import annotations

import json
import math
import pathlib
import sys

import numpy as np
import torch
import torch.nn.functional as F

ROOT = pathlib.Path(__file__).resolve().parents[2]
CACHE = ROOT / "data/external/dem"
AREAS = ["tonle-sap", "sevan", "ararat"]
ATTRIBUTION = ("Copernicus DEM (c) DLR e.V. 2010-2014 and (c) Airbus Defence and Space GmbH 2014-2018, "
               "provided under COPERNICUS by the European Union and ESA")
SOURCES = [  # tried in order; an area uses the first source in which ALL its tiles exist
    ("GLO-30", "https://copernicus-dem-30m.s3.amazonaws.com", "10"),
    ("GLO-90", "https://copernicus-dem-90m.s3.amazonaws.com", "30"),
]
# GLO-30 is not public for Armenia, and over Tonle Sap (flat; 9 tiles) the remote read of GLO-30 ran at
# ~10 kB/s from here. GLO-90 is used everywhere by default so all areas are treated alike; set
# TERRAIN_DEM=GLO-30 to try the 30 m model first (delete the cached clip in data/external/dem/).
import os  # noqa: E402
if os.environ.get("TERRAIN_DEM", "GLO-90") != "GLO-30":
    SOURCES = SOURCES[::-1]
PAD_DEG = 0.06            # DEM read beyond the box so windows / ray-march see past the edge (~6 km)
M_PER_DEG_LAT = 110_574.0
M_PER_DEG_LON_EQ = 111_320.0

# ---------------------------------------------------------------- radar geometry (assumptions)
LOOK_SIDE = "left"        # NISAR is left-looking only (ASF NISAR docs, JPL observation strategy)
INC_NEAR_DEG = 33.0       # L-band incidence range across the 240 km swath
INC_FAR_DEG = 47.0
INC_MID_DEG = 40.0        # used where the footprint is unknown (and in the tests)

# ---------------------------------------------------------------- thresholds (see docs/eda/terrain.md)
SLOPE_MAX_DEG = 5.0       # mean slope of a ~180 m pixel. A lake surface is flat (DEM water bodies are
                          # flattened); a pixel straddling a shore averages flat water and the bank.
SLOPE_P90_MAX_DEG = 15.0  # 90th percentile of native-DEM slope inside the pixel. Steep small slopes hide
                          # in a 180 m mean; a pixel that is mostly flat water has a low p90 even at a shore.
LI_MAX_DEG = 65.0         # local incidence above this = slope tilted away from the radar; backscatter
                          # of rough land falls by >10 dB there and looks as dark as calm water.
SHADOW_FRAC_MAX = 0.25    # share of native pixels in radar shadow (local or cast) tolerated in a pixel
REL_WIN_KM = 4.0          # window for the local-minimum surface ("height above nearest drainage" proxy)
REL_HEIGHT_MAX_M = 60.0   # water sits in the local low; reject pixels > this above the smoothed minimum
SHORE_KEEP_PX = 1         # pixels within this many px of a big flat basin are not removed for slope alone
BASIN_SLOPE_DEG = 1.0     # 'flat basin' (lake surface / floodplain) for the shore guard
BASIN_MIN_KM2 = 0.5


# ================================================================= DEM access
def _tile_names(w, s, e, n):
    out = []
    for lat in range(math.floor(s), math.ceil(n)):
        for lon in range(math.floor(w), math.ceil(e)):
            out.append(f"{'N' if lat >= 0 else 'S'}{abs(lat):02d}_00_{'E' if lon >= 0 else 'W'}{abs(lon):03d}_00")
    return out


def _url(base, code, name):
    t = f"Copernicus_DSM_COG_{code}_{name}_DEM"
    return f"{base}/{t}/{t}.tif"


def _exists(url):
    import urllib.request
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=20) as r:
            return r.status == 200
    except Exception:
        return False


def fetch_dem(area_id, bounds):
    """(float32 array, west, north, res_x, res_y, source name) of the native DEM over bounds + PAD."""
    import rasterio
    from rasterio.transform import from_origin
    from rasterio.windows import Window
    path = CACHE / f"{area_id}_dem.tif"
    if path.exists():
        with rasterio.open(path) as src:
            t = src.transform
            return src.read(1).astype(np.float32), t.c, t.f, t.a, -t.e, src.tags().get("source", "?")
    w, s, e, n = bounds
    box = (w - PAD_DEG, s - PAD_DEG, e + PAD_DEG, n + PAD_DEG)
    names = _tile_names(*box)
    def local(u):  # whole tiles fetched beforehand (e.g. with curl when /vsicurl/ is too slow)
        f = CACHE / "tiles" / u.rsplit("/", 1)[-1]
        return str(f) if f.exists() else "/vsicurl/" + u

    for sname, base, code in SOURCES:
        urls = [_url(base, code, nm) for nm in names]
        if all(not local(u).startswith("/vsicurl/") or _exists(u) for u in urls):
            break
        print(f"  {sname}: not all tiles public for {area_id} ({names}); trying next source")
    else:
        raise RuntimeError(f"no DEM source covers {area_id}")
    # one output array on the tiles' own pixel grid; read only the needed window of each tile
    with rasterio.open(local(urls[0])) as s0:
        res_x, res_y, crs = s0.transform.a, -s0.transform.e, s0.crs
        ox, oy = s0.transform.c, s0.transform.f          # tile grid origin (pixel edges)
    c_w = math.floor((box[0] - ox) / res_x); c_e = math.ceil((box[2] - ox) / res_x)
    r_n = math.floor((oy - box[3]) / res_y); r_s = math.ceil((oy - box[1]) / res_y)
    west0, north0 = ox + c_w * res_x, oy - r_n * res_y
    arr = np.zeros((r_s - r_n, c_e - c_w), np.float32)
    for u in urls:
        with rasterio.open(local(u)) as src:
            col0 = int(round((west0 - src.transform.c) / res_x))
            row0 = int(round((src.transform.f - north0) / res_y))
            c_lo, c_hi = max(col0, 0), min(col0 + arr.shape[1], src.width)
            r_lo, r_hi = max(row0, 0), min(row0 + arr.shape[0], src.height)
            if c_lo >= c_hi or r_lo >= r_hi:
                continue
            a = src.read(1, window=Window(c_lo, r_lo, c_hi - c_lo, r_hi - r_lo))
            arr[r_lo - row0:r_hi - row0, c_lo - col0:c_hi - col0] = a
            print(f"    {u.rsplit('/', 1)[-1]}: window {a.shape}")
    tr = from_origin(west0, north0, res_x, res_y)
    meta = {"crs": crs}
    CACHE.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", driver="GTiff", width=arr.shape[1], height=arr.shape[0], count=1,
                       dtype="float32", crs=meta["crs"], transform=tr, compress="deflate", predictor=3,
                       tiled=True) as dst:
        dst.write(arr, 1)
        dst.update_tags(source=sname, tiles=",".join(names), attribution=ATTRIBUTION)
    print(f"  {sname}: read {len(urls)} tile window(s) (remote or data/external/dem/tiles) -> {arr.shape}, cached {path.relative_to(ROOT)} "
          f"({path.stat().st_size / 1e6:.1f} MB)")
    return arr, tr.c, tr.f, tr.a, -tr.e, sname


def grid_of(area_id):
    """(bounds, H, W) of our detection grid (from the math products if present, else the stack)."""
    p = ROOT / f"data/processed/{area_id}/math/products.pt"
    if p.exists():
        d = torch.load(p, weights_only=False, map_location="cpu")
        return tuple(d["bounds"]), int(d["water"].shape[1]), int(d["water"].shape[2])
    from scripts.detect.stack import load_preview_stack
    st = load_preview_stack(area_id, align=False)
    return st.bounds, st.shape[2], st.shape[3]


# ================================================================= resampling (exact area overlap)
def _overlap(t_edges, s_edges):
    lo = torch.maximum(t_edges[:-1, None], s_edges[None, :-1])
    hi = torch.minimum(t_edges[1:, None], s_edges[None, 1:])
    return (hi - lo).clamp(min=0)


class Aggregator:
    """Area-weighted mean of a native-resolution field onto a target lon/lat grid (Ry @ f @ Rx^T),
    plus per-pixel quantiles from threshold counts."""

    def __init__(self, shape_src, west, north, rx, ry, bounds, H, W):
        w, s, e, n = bounds
        nr, nc = shape_src
        ty = torch.linspace(0, n - s, H + 1, dtype=torch.float64)
        tx = torch.linspace(0, e - w, W + 1, dtype=torch.float64)
        sy = (n - north) + torch.arange(nr + 1, dtype=torch.float64) * ry
        sx = (west - w) + torch.arange(nc + 1, dtype=torch.float64) * rx
        self.Ry = _overlap(ty, sy).float()
        self.Rx = _overlap(tx, sx).float()
        # only the source rows/cols that touch the target grid
        self.r_idx = torch.nonzero(self.Ry.sum(0) > 0)[:, 0]
        self.c_idx = torch.nonzero(self.Rx.sum(0) > 0)[:, 0]
        self.Ry, self.Rx = self.Ry[:, self.r_idx], self.Rx[:, self.c_idx]
        self.norm = (self.Ry.sum(1, keepdim=True) @ self.Rx.sum(1, keepdim=True).T).clamp(min=1e-12)

    def crop(self, f):
        return f[self.r_idx[0]:self.r_idx[-1] + 1, self.c_idx[0]:self.c_idx[-1] + 1]

    def mean(self, f):
        return (self.Ry @ self.crop(f).float() @ self.Rx.T) / self.norm

    def quantile(self, f, q, levels):
        """Smallest level L with area-share(f > L) <= 1 - q (linear interpolation between levels)."""
        fc = self.crop(f)
        prev_lvl, prev_share = None, None
        out = torch.full(self.norm.shape, float(levels[-1]))
        done = torch.zeros(self.norm.shape, dtype=torch.bool)
        for L in levels:
            share = (self.Ry @ (fc > L).float() @ self.Rx.T) / self.norm
            hit = (share <= 1 - q) & ~done
            if prev_lvl is None:
                out[hit] = L
            else:  # interpolate between previous and this level
                frac = ((prev_share - (1 - q)) / (prev_share - share).clamp(min=1e-9)).clamp(0, 1)
                out[hit] = (prev_lvl + frac * (L - prev_lvl))[hit]
            done |= hit
            prev_lvl, prev_share = L, share
            if bool(done.all()):
                break
        return out


# ================================================================= terrain derivatives
def gradient(dem, pixel_m_x, pixel_m_y):
    """(dz/dx_east, dz/dy_north) in m/m by central differences (conv2d, replicate edges).
    dem [H, W] north-up; pixel_m_x float or [H] tensor (lon pixel shrinks with cos(lat)); pixel_m_y float."""
    z = F.pad(dem[None, None].float(), (1, 1, 1, 1), mode="replicate")
    kx = torch.tensor([[[[0, 0, 0], [-0.5, 0, 0.5], [0, 0, 0]]]], dtype=torch.float32)
    ky = torch.tensor([[[[0, 0.5, 0], [0, 0, 0], [0, -0.5, 0]]]], dtype=torch.float32)  # row up = north
    gx = F.conv2d(z, kx)[0, 0]
    gy = F.conv2d(z, ky)[0, 0]
    px = torch.as_tensor(pixel_m_x, dtype=torch.float32)
    px = px[:, None] if px.ndim == 1 else px
    return gx / px, gy / float(pixel_m_y)


def slope_aspect(dem, pixel_m_x, pixel_m_y):
    """Slope (deg from horizontal) and aspect (deg clockwise from north, the DOWNHILL direction the
    face points to; 90 = east-facing)."""
    gx, gy = gradient(dem, pixel_m_x, pixel_m_y)
    slope = torch.rad2deg(torch.atan(torch.sqrt(gx ** 2 + gy ** 2)))
    aspect = torch.rad2deg(torch.atan2(-gx, -gy)) % 360
    return slope, aspect


def local_incidence(gx, gy, look_az_deg, inc_deg):
    """Local incidence angle (deg) and layover flag.

    Look vector l points from the sensor to the ground: horizontal part sin(theta) * (sin az, cos az),
    vertical part -cos(theta). Surface normal n = (-gx, -gy, 1) / |.|. cos(local inc) = n . (-l)
      = (cos theta + sin theta * (gx sin az + gy cos az)) / sqrt(1 + gx^2 + gy^2).
    Shadow: cos <= 0 (face tilted away from the sensor by more than the grazing angle 90 - theta).
    Layover: slope rising along the look direction steeper than theta (top of the face is closer to
    the sensor than its foot).
    """
    az = torch.deg2rad(torch.as_tensor(look_az_deg, dtype=torch.float32))
    th = torch.deg2rad(torch.as_tensor(inc_deg, dtype=torch.float32))
    g_look = gx * torch.sin(az) + gy * torch.cos(az)   # slope rising along the look direction
    cos_li = (torch.cos(th) + torch.sin(th) * g_look) / torch.sqrt(1 + gx ** 2 + gy ** 2)
    li = torch.rad2deg(torch.acos(cos_li.clamp(-1, 1)))
    layover = g_look > torch.tan(th)
    return li, layover


def cast_shadow(dem, pixel_m_x, pixel_m_y, look_az_deg, inc_deg, max_m=8000.0, step_m=None):
    """Ray-march toward the sensor: pixel P is in cast shadow if terrain at horizontal distance d toward
    the sensor rises above z_P + d * cot(theta) (the ray from the sensor to P)."""
    H, W = dem.shape
    px = torch.as_tensor(pixel_m_x, dtype=torch.float32)
    px = (px[:, None] if px.ndim == 1 else px).expand(H, W)
    step_m = step_m or 0.7 * min(float(px.min()), float(pixel_m_y))
    az = math.radians(float(look_az_deg))
    se, sn = -math.sin(az), -math.cos(az)                       # unit vector toward the sensor
    cot = 1.0 / torch.tan(torch.deg2rad(torch.as_tensor(inc_deg, dtype=torch.float32))).expand(H, W)
    yy, xx = torch.meshgrid(torch.arange(H, dtype=torch.float32), torch.arange(W, dtype=torch.float32),
                            indexing="ij")
    z = dem.float()
    shadow = torch.zeros(H, W, dtype=torch.bool)
    img = z[None, None]
    for k in range(1, int(max_m / step_m) + 1):
        d = k * step_m
        c = xx + d * se / px
        r = yy - d * sn / float(pixel_m_y)
        grid = torch.stack([c / (W - 1) * 2 - 1, r / (H - 1) * 2 - 1], -1)[None]
        h = F.grid_sample(img, grid, mode="bilinear", padding_mode="border", align_corners=True)[0, 0]
        shadow |= h > z + d * cot
    return shadow


def rel_height(dem, pixel_m, win_km=REL_WIN_KM):
    """Height above a smoothed local minimum: dem - avgpool(minpool(dem)), window win_km.
    A cheap stand-in for 'height above nearest drainage' (water collects in local lows)."""
    k = max(3, int(round(win_km * 1000 / pixel_m)) | 1)
    z = dem[None, None].float()
    zmin = -F.max_pool2d(F.pad(-z, (k // 2,) * 4, mode="replicate"), k, 1)
    zmin = F.avg_pool2d(F.pad(zmin, (k // 2,) * 4, mode="replicate"), k, 1)
    return (dem - zmin[0, 0]).clamp(min=0)


# ================================================================= geometry from the footprint
def geometry(area_id):
    """Ground heading, look azimuth and a function lon,lat -> incidence (deg) from the footprint
    polygon of the first pass. Two edges of the 4-corner polygon run along track."""
    from scripts.detect.stack import _polygon, load_area
    a = load_area(area_id)
    meta = json.loads((ROOT / f"data/raw/previews/T{a['track']}_F{a['frame']}/meta.json").read_text())
    poly = _polygon(meta["passes"][0]["wkt"])
    lat0 = sum(p[1] for p in poly) / len(poly)
    kx = M_PER_DEG_LON_EQ * math.cos(math.radians(lat0))

    def xy(p):
        return (p[0] * kx, p[1] * M_PER_DEG_LAT)

    edges = [(poly[i], poly[(i + 1) % 4]) for i in range(4)]
    lens = [math.dist(xy(p), xy(q)) for p, q in edges]
    # along-track edges: the two closest to north-south (near-polar orbit; frame length ~ swath width,
    # so edge length alone cannot tell them apart)
    longs = sorted(range(4), key=lambda i: -abs(xy(edges[i][1])[1] - xy(edges[i][0])[1]) / lens[i])[:2]
    hd = []
    for i in longs:
        p, q = edges[i]
        if q[1] < p[1]:
            p, q = q, p                                     # orient south -> north (ascending)
        (x1, y1), (x2, y2) = xy(p), xy(q)
        hd.append(math.degrees(math.atan2(x2 - x1, y2 - y1)) % 360)
    heading = (sum(((h + 180) % 360) - 180 for h in hd) / 2) % 360
    look_az = (heading - 90) % 360 if LOOK_SIDE == "left" else (heading + 90) % 360
    # near-range edge = long edge on the sensor side (sensor lies opposite to the look direction)
    se, sn = -math.sin(math.radians(look_az)), -math.cos(math.radians(look_az))
    mid = [tuple(np.mean([xy(edges[i][0]), xy(edges[i][1])], 0)) for i in longs]
    near = longs[0] if (mid[0][0] - mid[1][0]) * se + (mid[0][1] - mid[1][1]) * sn > 0 else longs[1]
    far = longs[1] if near == longs[0] else longs[0]
    (nx1, ny1), (nx2, ny2) = xy(edges[near][0]), xy(edges[near][1])
    ux, uy = (nx2 - nx1), (ny2 - ny1)
    L = math.hypot(ux, uy)
    nvec = (-uy / L, ux / L)                                 # unit normal of near edge
    fx, fy = mid[longs.index(far)]
    width = (fx - nx1) * nvec[0] + (fy - ny1) * nvec[1]

    def incidence(lon, lat):
        d = ((lon * kx - nx1) * nvec[0] + (lat * M_PER_DEG_LAT - ny1) * nvec[1]) / width
        return INC_NEAR_DEG + (INC_FAR_DEG - INC_NEAR_DEG) * d.clamp(0, 1)

    return {"heading_deg": heading, "look_az_deg": look_az, "swath_km": abs(width) / 1000,
            "incidence": incidence, "look_side": LOOK_SIDE}


# ================================================================= the mask
def _basin_guard(flat, km2_per_px):
    """Pixels next to a large flat basin (lake surface, floodplain): the shore guard."""
    from scripts.detect.ops import dilate, remove_small
    big = remove_small(flat[None], max(1, int(BASIN_MIN_KM2 / km2_per_px)))
    return dilate(big, 2 * SHORE_KEEP_PX + 1)[0]


def classify(slope_mean, slope_p90, dim_frac, shadow_frac, relh, km2_per_px):
    """The decision rule (all [H, W] on our grid). Open water is NOT possible where
      steep     : mean native slope > SLOPE_MAX_DEG or its p90 > SLOPE_P90_MAX_DEG
      high      : > REL_HEIGHT_MAX_M above the smoothed local minimum within REL_WIN_KM
      backslope : most of the pixel faces away from the radar (local incidence > LI_MAX_DEG) or more than
                  SHADOW_FRAC_MAX of it is in radar shadow (local or cast)
    except that 'steep' and 'high' are waived within SHORE_KEEP_PX of a flat basin >= BASIN_MIN_KM2
    (lake / reservoir surfaces are flattened in the DEM, so a reservoir held above the valley by a dam and
    its 180 m shore pixels are kept). Shadow is never waived: the radar cannot see there."""
    flat = (slope_mean <= BASIN_SLOPE_DEG) & (slope_p90 <= 2 * BASIN_SLOPE_DEG)
    shore = _basin_guard(flat, km2_per_px)
    steep = (slope_mean > SLOPE_MAX_DEG) | (slope_p90 > SLOPE_P90_MAX_DEG)
    backslope = (dim_frac > 0.5) | (shadow_frac > SHADOW_FRAC_MAX)
    high = relh > REL_HEIGHT_MAX_M
    possible = ~(((steep | high) & ~shore) | backslope)
    return {"steep": steep, "high": high, "backslope": backslope, "shore_guard": shore, "water_possible": possible}


def compute(area_id, verbose=True):
    bounds, H, W = grid_of(area_id)
    w, s, e, n = bounds
    dem30, west, north, rx, ry, sname = fetch_dem(area_id, bounds)
    nr, nc = dem30.shape
    z = torch.from_numpy(dem30)
    # --- native resolution derivatives (lon pixel size per row)
    lat_rows = north - (torch.arange(nr, dtype=torch.float64) + 0.5) * ry
    pmx = (rx * M_PER_DEG_LON_EQ * torch.cos(torch.deg2rad(lat_rows))).float()
    pmy = ry * M_PER_DEG_LAT
    geo = geometry(area_id)
    gx, gy = gradient(z, pmx, pmy)
    slope30 = torch.rad2deg(torch.atan(torch.sqrt(gx ** 2 + gy ** 2)))
    lon_cols = west + (torch.arange(nc, dtype=torch.float64) + 0.5) * rx
    inc30 = geo["incidence"](lon_cols[None, :].float(), lat_rows[:, None].float())
    li30, lay30 = local_incidence(gx, gy, geo["look_az_deg"], inc30)
    # cast shadow on a ~2x coarser copy when the DEM is 30 m (cost), at native for 90 m
    f = 2 if rx < 0.0005 else 1
    zc = F.avg_pool2d(z[None, None], f)[0, 0] if f > 1 else z
    relief = float(z.max() - z.min())
    cs = cast_shadow(zc, pmx[::f][: zc.shape[0]] * f, pmy * f, geo["look_az_deg"],
                     inc30[::f, ::f][: zc.shape[0], : zc.shape[1]], max_m=min(8000.0, 1.1 * relief + 100))
    if f > 1:
        cs = F.interpolate(cs[None, None].float(), size=(nr, nc), mode="nearest")[0, 0] > 0.5
    shadow30 = (li30 >= 90) | cs
    del gx, gy
    # --- onto our grid
    agg = Aggregator((nr, nc), west, north, rx, ry, bounds, H, W)
    dem = agg.mean(z)
    slope_mean = agg.mean(slope30)
    slope_p90 = agg.quantile(slope30, 0.9, [float(v) for v in np.arange(0, 45.5, 0.5)])
    li_mean = agg.mean(li30)
    shadow_frac = agg.mean(shadow30)
    dim_frac = agg.mean(li30 > LI_MAX_DEG)
    layover_frac = agg.mean(lay30)
    # --- slope at OUR pixel size from the averaged DEM, and relative height
    lat_g = n - (torch.arange(H, dtype=torch.float64) + 0.5) * (n - s) / H
    px_x = ((e - w) / W * M_PER_DEG_LON_EQ * torch.cos(torch.deg2rad(lat_g))).float()
    px_y = (n - s) / H * M_PER_DEG_LAT
    slope_g, aspect_g = slope_aspect(dem, px_x, px_y)
    # relative height on an extended copy of our grid (so the window sees past the box edge)
    P = int(math.ceil(0.8 * PAD_DEG / ((e - w) / W)))
    dx, dy = (e - w) / W, (n - s) / H
    agg_ext = Aggregator((nr, nc), west, north, rx, ry, (w - P * dx, s - P * dy, e + P * dx, n + P * dy),
                         H + 2 * P, W + 2 * P)
    relh = rel_height(agg_ext.mean(z), float(px_x.mean()))[P:-P, P:-P]
    km2 = (e - w) / W * M_PER_DEG_LON_EQ * math.cos(math.radians((s + n) / 2)) / 1000 * px_y / 1000
    c = classify(slope_mean, slope_p90, dim_frac, shadow_frac, relh, km2)
    steep, backslope, high, shore, possible = c["steep"], c["backslope"], c["high"], c["shore_guard"], c["water_possible"]
    out = {"area": area_id, "bounds": bounds, "km2_per_px": km2, "dem_source": sname,
           "dem_res_deg": (rx, ry), "geometry": {k: v for k, v in geo.items() if k != "incidence"},
           "thresholds": {k: globals()[k] for k in ("SLOPE_MAX_DEG", "SLOPE_P90_MAX_DEG", "LI_MAX_DEG",
                          "SHADOW_FRAC_MAX", "REL_WIN_KM", "REL_HEIGHT_MAX_M", "SHORE_KEEP_PX",
                          "BASIN_SLOPE_DEG", "BASIN_MIN_KM2", "INC_NEAR_DEG", "INC_FAR_DEG")},
           "dem": dem, "slope_deg": slope_g, "aspect_deg": aspect_g, "slope_mean_native_deg": slope_mean,
           "slope_p90_deg": slope_p90, "local_incidence_deg": li_mean, "dim_frac": dim_frac,
           "shadow_frac": shadow_frac, "layover_frac": layover_frac,
           "shadow": shadow_frac > SHADOW_FRAC_MAX, "rel_height_m": relh,
           "steep": steep, "backslope": backslope, "high": high, "shore_guard": shore,
           "water_possible": possible}
    path = ROOT / f"data/processed/{area_id}/terrain.pt"
    path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(out, path)
    if verbose:
        tot = H * W * km2
        print(f"{area_id}: {sname} {nr}x{nc} -> grid {H}x{W}; heading {geo['heading_deg']:.1f} deg, look az "
              f"{geo['look_az_deg']:.1f} deg ({LOOK_SIDE}-looking), swath {geo['swath_km']:.0f} km")
        print(f"  elevation {float(dem.min()):.0f}..{float(dem.max()):.0f} m; slope p90 median "
              f"{float(slope_p90.median()):.1f} deg; water_possible {float(possible.float().mean()) * 100:.1f} % "
              f"of {tot:.0f} km2 (steep {float((steep & ~shore).float().mean()) * 100:.1f} %, back-slope/shadow "
              f"{float(backslope.float().mean()) * 100:.1f} %, high {float(high.float().mean()) * 100:.1f} %)")
        print(f"  saved {path.relative_to(ROOT)}")
    return out


def terrain_mask(area_id, recompute=False):
    """Dict of [H, W] tensors on our grid (see compute); cached in data/processed/<area>/terrain.pt."""
    path = ROOT / f"data/processed/{area_id}/terrain.pt"
    if path.exists() and not recompute:
        return torch.load(path, weights_only=False, map_location="cpu")
    return compute(area_id)


def load_dem(area_id):
    """Elevation (m) area-averaged onto our [H, W] grid."""
    return terrain_mask(area_id)["dem"]


def cached_water_possible(area_id, shape):
    """The mask if it was already built for this area and grid, else None (never downloads)."""
    if not (ROOT / "data/processed" / area_id / "terrain.pt").exists():
        return None
    try:
        return water_possible(area_id, shape)
    except ValueError:
        return None


def water_possible(area_id, shape=None):
    """[H, W] bool: open water physically plausible. Pipeline hook: water = water & water_possible(area)."""
    m = terrain_mask(area_id)["water_possible"]
    if shape is not None and tuple(m.shape) != tuple(shape):
        raise ValueError(f"terrain grid {tuple(m.shape)} != detection grid {tuple(shape)}; rerun terrain")
    return m




# ================================================================= measuring the effect (reporting only)
# JRC Global Surface Water is used ONLY here, to measure; it never enters the mask above.
OUT = ROOT / "docs/eda/terrain"
TITLES = {"tonle-sap": "Tonle Sap", "sevan": "Lake Sevan", "ararat": "Ararat valley"}


def hillshade(dem, px_x, px_y, az=315.0, alt=45.0):
    gx, gy = gradient(dem, px_x, px_y)
    slope = torch.atan(torch.sqrt(gx ** 2 + gy ** 2))
    aspect = torch.atan2(-gx, -gy)
    a, h = math.radians(az), math.radians(alt)
    return (math.sin(h) * torch.cos(slope) + math.cos(h) * torch.sin(slope) * torch.cos(a - aspect)).clamp(0, 1)


def _r(x, nd=1):
    return round(float(x), nd)


def measure(area_id):
    from scripts.detect import validate as V
    p = torch.load(ROOT / f"data/processed/{area_id}/math/products.pt", weights_only=False, map_location="cpu")
    tm = terrain_mask(area_id)
    wp = tm["water_possible"]
    water, cls, km2, dates = p["water"], p["classes"], p["km2_per_px"], p["dates"]
    _, H, W = water.shape
    assert tuple(wp.shape) == (H, W)
    ref, _ = V.reference_on_grid(area_id, p["bounds"], H, W)
    dom = p["valid"].all(0) & (ref["valid_frac"] >= 0.5)
    extm = ref["ext_frac"] >= 0.5
    ref90 = ref["occ_ge90_frac"] >= 0.5
    perm = cls == 1
    R = {"area": area_id, "km2_per_px": km2, "water_possible_share_of_domain": _r(float((wp & dom).sum() / dom.sum()), 4),
         "per_date": []}
    for t, d in enumerate(dates):
        w0 = water[t] & dom
        w1 = w0 & wp
        R["per_date"].append({"date": d, "before_km2": _r(w0.sum() * km2), "after_km2": _r(w1.sum() * km2),
                              "false_before_km2": _r((w0 & ~extm).sum() * km2),
                              "false_after_km2": _r((w1 & ~extm).sum() * km2),
                              "true_lost_km2": _r((w0 & extm & ~wp).sum() * km2)})
    for name, m in [("any_date", water.any(0) & dom), ("late_last3", water[-3:].any(0) & dom)]:
        R[name] = {"before_km2": _r(m.sum() * km2), "after_km2": _r((m & wp).sum() * km2),
                   "false_before_km2": _r((m & ~extm).sum() * km2), "false_after_km2": _r((m & wp & ~extm).sum() * km2),
                   "true_before_km2": _r((m & extm).sum() * km2), "true_lost_km2": _r((m & extm & ~wp).sum() * km2)}
        fb = float((m & ~extm).sum())
        R[name]["false_removed_share"] = _r(float((m & ~extm & ~wp).sum()) / max(fb, 1), 3)
        R[name]["true_lost_share"] = _r(float((m & extm & ~wp).sum()) / max(float((m & extm).sum()), 1), 4)
    R["permanent_vs_occ90"] = {"before": V.confusion(perm, ref90, dom, km2), "after": V.confusion(perm & wp, ref90, dom, km2)}
    R["any_date_vs_max_extent"] = {"before": V.confusion(water.any(0), extm, dom, km2),
                                   "after": V.confusion(water.any(0) & wp, extm, dom, km2)}
    # why was false water removed (pixels can have several reasons)
    fm = water.any(0) & dom & ~extm
    R["removed_false_by_reason_km2"] = {k: _r((fm & tm[k] & ~wp).sum() * km2) for k in ("steep", "high", "backslope")}
    ref_any = (ref["ext_frac"] > 0) & dom
    R["remaining_false_late"] = V.where_outside(water[-3:].any(0) & dom & wp & ~extm, dom, ref_any, p["bounds"], km2, H, W)
    # terrain at the remaining false water
    rem = fm & wp
    if rem.any():
        R["remaining_false_terrain"] = {"slope_mean_deg_median": _r(tm["slope_mean_native_deg"][rem].median(), 2),
                                        "rel_height_m_median": _r(tm["rel_height_m"][rem].median()),
                                        "share_flat_lt2deg": _r(float((tm["slope_mean_native_deg"][rem] < 2).float().mean()), 3)}
    maps = {"water_any": water.any(0) & dom, "extm": extm, "dom": dom, "wp": wp, "tm": tm, "bounds": p["bounds"]}
    return R, maps


def _save(fig, name):
    import matplotlib.pyplot as plt
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / name
    fig.savefig(path, dpi=fig.dpi, bbox_inches="tight")
    plt.close(fig)
    print(f"  wrote {path.relative_to(ROOT)} ({path.stat().st_size / 1024:.0f} KB)")


def _shade(tm, bounds):
    w, s, e, n = bounds
    H, W = tm["dem"].shape
    lat = n - (torch.arange(H, dtype=torch.float64) + 0.5) * (n - s) / H
    px = ((e - w) / W * M_PER_DEG_LON_EQ * torch.cos(torch.deg2rad(lat))).float()
    return hillshade(tm["dem"], px, (n - s) / H * M_PER_DEG_LAT).numpy()


def fig_terrain(area_id, maps):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import ListedColormap
    tm, (w, s, e, n) = maps["tm"], maps["bounds"]
    ext = (w, e, s, n)
    hs = _shade(tm, maps["bounds"])
    fig, axs = plt.subplots(1, 3, figsize=(14, 4.6), dpi=80)
    axs[0].imshow(hs, cmap="gray", extent=ext)
    im = axs[0].imshow(tm["dem"].numpy(), cmap="terrain", alpha=0.45, extent=ext)
    fig.colorbar(im, ax=axs[0], shrink=0.8, label="elevation (m)")
    axs[0].set_title("Copernicus DEM, hillshade + elevation", fontsize=9)
    im = axs[1].imshow(tm["slope_p90_deg"].numpy(), cmap="magma_r", vmin=0, vmax=30, extent=ext)
    fig.colorbar(im, ax=axs[1], shrink=0.8, label="deg")
    axs[1].set_title("90th percentile of native-DEM slope per pixel", fontsize=9)
    code = np.zeros(tm["dem"].shape, np.uint8)            # 0 possible
    code[(tm["high"] & ~tm["shore_guard"]).numpy()] = 1
    code[(tm["steep"] & ~tm["shore_guard"]).numpy()] = 2
    code[tm["backslope"].numpy()] = 3
    cmap = ListedColormap(["#4f9bd9", "#d9c38f", "#a0522d", "#222222"])
    axs[2].imshow(hs, cmap="gray", extent=ext)
    axs[2].imshow(code, cmap=cmap, vmin=-0.5, vmax=3.5, alpha=0.7, extent=ext, interpolation="nearest")
    from matplotlib.patches import Patch
    axs[2].legend(handles=[Patch(color=c, label=l) for c, l in zip(cmap.colors, [
        "water possible", f"> {REL_HEIGHT_MAX_M:.0f} m above local low", "too steep", "faces away / shadow"])],
        loc="lower left", fontsize=7, framealpha=0.9)
    axs[2].set_title("water_possible mask and why not", fontsize=9)
    for ax in axs:
        ax.set_xlabel("longitude", fontsize=8); ax.tick_params(labelsize=7)
    fig.suptitle(f"{TITLES[area_id]}: terrain. {ATTRIBUTION}", fontsize=7)
    _save(fig, f"{area_id}_terrain.png")


def fig_before_after(area_id, R, maps):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch
    w, s, e, n = maps["bounds"]
    hs = _shade(maps["tm"], maps["bounds"])
    wa, extm, wp, dom = maps["water_any"], maps["extm"], maps["wp"], maps["dom"]
    img = np.stack([hs * 0.5 + 0.45] * 3, -1)
    img[~dom.numpy()] = [0.8, 0.8, 0.8]
    cols = {"kept, inside reference extent": ((wa & wp & extm), [0.13, 0.4, 0.8]),
            "kept, outside (false water left)": ((wa & wp & ~extm), [0.95, 0.6, 0.0]),
            "removed, outside (false water removed)": ((wa & ~wp & ~extm), [0.85, 0.1, 0.1]),
            "removed, inside (true water lost)": ((wa & ~wp & extm), [0.6, 0.1, 0.7])}
    for m, c in cols.values():
        img[m.numpy()] = c
    fig, ax = plt.subplots(figsize=(7.5, 6.2), dpi=80)
    ax.imshow(img, extent=(w, e, s, n), interpolation="nearest")
    a = R["any_date"]
    ax.set_title(f"{TITLES[area_id]}: water on any date, terrain mask applied\n"
                 f"false water {a['false_before_km2']:.0f} -> {a['false_after_km2']:.0f} km2; true water lost "
                 f"{a['true_lost_km2']:.1f} of {a['true_before_km2']:.0f} km2", fontsize=9)
    ax.legend(handles=[Patch(color=c, label=k) for k, (_, c) in cols.items()], loc="lower left", fontsize=7,
              framealpha=0.9)
    ax.text(0.99, 0.01, "reference: JRC GSW max extent 1984-2021 (Source: EC JRC/Google), used only to measure",
            transform=ax.transAxes, ha="right", fontsize=6, color="0.3")
    _save(fig, f"{area_id}_before_after.png")


def fig_bars(allR):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axs = plt.subplots(1, 2, figsize=(10, 3.8), dpi=80)
    names = [R["area"] for R in allR]
    x = np.arange(len(names))
    for ax, key, title in [(axs[0], "any_date", "water on any date"), (axs[1], "late_last3", "water on any of the last 3 dates")]:
        fb = [R[key]["false_before_km2"] for R in allR]
        fa = [R[key]["false_after_km2"] for R in allR]
        tl = [R[key]["true_lost_km2"] for R in allR]
        ax.bar(x - 0.27, fb, 0.27, color="#d62728", label="false water before")
        ax.bar(x, fa, 0.27, color="#ff9f1c", label="false water after")
        ax.bar(x + 0.27, tl, 0.27, color="#7b2d8e", label="true water lost")
        for i in range(len(names)):
            ax.text(x[i] - 0.27, fb[i], f"{fb[i]:.0f}", ha="center", va="bottom", fontsize=7)
            ax.text(x[i], fa[i], f"{fa[i]:.0f}", ha="center", va="bottom", fontsize=7)
            ax.text(x[i] + 0.27, tl[i], f"{tl[i]:.1f}", ha="center", va="bottom", fontsize=7)
        ax.set_xticks(x, [TITLES[nm] for nm in names], fontsize=8)
        ax.set_ylabel("km2"); ax.set_title(title, fontsize=9); ax.grid(axis="y", alpha=0.3)
    axs[0].legend(fontsize=7)
    fig.suptitle("False water = detected outside the JRC GSW 1984-2021 maximum extent (Source: EC JRC/Google)",
                 fontsize=8)
    _save(fig, "false_water_bars.png")


def report(areas):
    allR = []
    for a in areas:
        R, M = measure(a)
        allR.append(R)
        if a != "tonle-sap":
            fig_terrain(a, M)
        fig_before_after(a, R, M)
        print(json.dumps({k: R[k] for k in ("any_date", "late_last3")}, indent=None))
    fig_bars(allR)
    for R in allR:  # keep the measured effect next to the mask (key 'effect' of terrain.pt)
        path = ROOT / f"data/processed/{R['area']}/terrain.pt"
        d = torch.load(path, weights_only=False)
        d["effect"] = R
        torch.save(d, path)
        print(f"  {R['area']}: effect stored in {path.relative_to(ROOT)} ['effect']")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--report-only" not in sys.argv:
        for a in args or AREAS:
            compute(a)
    if "--report" in sys.argv or "--report-only" in sys.argv:
        report(args or AREAS)
