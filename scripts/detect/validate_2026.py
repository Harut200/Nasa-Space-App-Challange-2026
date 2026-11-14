"""Independent 2026 validation of three radar claims (wind at Sevan, 6 Oct in Armenia,
Tonle Sap flood timing) against weather reanalysis, station reports, river gauges and
satellite altimetry.

Run:  .venv/bin/python -m scripts.detect.validate_2026

Light by design: web requests (cached under data/external/weather and data/external/levels)
and small arrays only. No radar image stacks are loaded. Writes
docs/eda/validation2026/results.json and figures.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import math
import re
import time
import urllib.request
from pathlib import Path

import numpy as np
from scipy import stats

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.dates as mdates  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
WX = ROOT / "data/external/weather"
LV = ROOT / "data/external/levels"
OUT = ROOT / "docs/eda/validation2026"
ACCESS_DATE = "2026-11-14"
UA = "validate_2026/1.0 (research script; python urllib)"

# chart tokens (dataviz reference palette, light mode)
C = {"s1": "#2a78d6", "s2": "#eb6834", "s3": "#1baf7a", "s4": "#eda100",
     "ink": "#0b0b0b", "ink2": "#52514e", "grid": "#e4e3df", "surf": "#fcfcfb",
     "hl": "#f0efec"}

ARM_POINTS = {  # name: (lat, lon)
    "sevan_mid": (40.35, 45.30),
    "sevan_nw_bay": (40.55, 44.98),
    "gavar_shore": (40.35, 45.13),
    "martuni": (40.14, 45.30),
    "ararat_valley": (40.07, 44.43),
    "yerevan": (40.18, 44.51),
}
LAKE_POINTS = ["sevan_mid", "sevan_nw_bay", "gavar_shore", "martuni"]
VALLEY_POINTS = ["ararat_valley", "yerevan"]
TONLE_POINT = (12.8, 104.1)
BASIN_POINTS = {  # daily rainfall for the Tonle Sap / lower Mekong basin
    "tonle_sap_lake": (12.80, 104.10),
    "battambang": (13.10, 103.20),
    "kratie": (12.49, 106.02),
    "pakse": (15.12, 105.80),
    "vientiane": (17.97, 102.60),
}
SYNOP = {  # WMO id: (name, lat, lon, group)
    "37717": ("Sevan (Tsamakaberd), NW bay shore", 40.566, 45.008, "lake"),
    "37801": ("Gavar (Kamo), W shore", 40.35, 45.133, "lake"),
    "37792": ("Hrazdan", 40.50, 44.767, "lake-near"),
    "37788": ("Yerevan Arabkir", 40.151, 44.404, "valley"),
    "37785": ("Ashtarak", 40.290, 44.350, "valley"),
    "37787": ("Armavir", 40.136, 43.903, "valley"),
    "37871": ("Artashat", 39.932, 44.521, "valley"),
}
FLAGGED = ["2026-08-31", "2026-09-12"]
WIN0, WIN1 = "2026-06-15", "2026-10-20"

SOURCES: list[dict] = []


# ---------------------------------------------------------------- downloads
def fetch(url: str, path: Path, sleep: float = 0.0, label: str = "", provider: str = "") -> Path | None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ok = True
    if not path.exists() or path.stat().st_size < 50:
        try:
            if "ogimet" in url:
                time.sleep(22)  # Ogimet allows one slow query per IP every 20 s
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=120) as r:
                data = r.read()
            path.write_bytes(data)
            if sleep:
                time.sleep(sleep)
        except Exception as e:  # noqa: BLE001
            print("FAILED", url, e)
            ok = False
    SOURCES.append({"label": label or path.name, "provider": provider, "url": url,
                    "cache": str(path.relative_to(ROOT)), "ok": ok and path.exists(),
                    "accessed": ACCESS_DATE})
    return path if ok and path.exists() else None


def openmeteo_hourly(name, lat, lon):
    url = ("https://archive-api.open-meteo.com/v1/archive?"
           f"latitude={lat}&longitude={lon}&start_date={WIN0}&end_date={WIN1}"
           "&hourly=wind_speed_10m,wind_gusts_10m,wind_direction_10m,precipitation,"
           "temperature_2m,dew_point_2m,relative_humidity_2m"
           "&models=era5,ecmwf_ifs&timezone=UTC&wind_speed_unit=ms")
    p = fetch(url, WX / f"openmeteo_{name}.json", label=f"Open-Meteo ERA5 + ECMWF IFS hourly, {name}",
              provider="Open-Meteo (ECMWF ERA5 0.25 deg; ECMWF IFS analysis 9 km)")
    d = json.loads(p.read_text())
    h = d["hourly"]
    t = [dt.datetime.fromisoformat(x).replace(tzinfo=dt.timezone.utc) for x in h["time"]]
    out = {"time": t, "grid_lat": d["latitude"], "grid_lon": d["longitude"], "elev": d.get("elevation")}
    for k, v in h.items():
        if k != "time":
            out[k] = np.array([np.nan if x is None else x for x in v], float)
    return out


def power_hourly(name, lat, lon):
    url = ("https://power.larc.nasa.gov/api/temporal/hourly/point?parameters=WS10M,PRECTOTCORR,T2M,T2MDEW"
           f"&community=RE&longitude={lon}&latitude={lat}&start=20260615&end=20261020&format=JSON&time-standard=UTC")
    p = fetch(url, WX / f"power_hourly_{name}.json", label=f"NASA POWER hourly, {name}",
              provider="NASA LaRC POWER (MERRA-2 / GEOS-IT, 0.5 x 0.625 deg)")
    d = json.loads(p.read_text())
    par = d["properties"]["parameter"]
    keys = sorted(par["WS10M"].keys())
    t = [dt.datetime.strptime(k, "%Y%m%d%H").replace(tzinfo=dt.timezone.utc) for k in keys]
    out = {"time": t, "sources": d.get("header", {}).get("sources")}
    for v in par:
        out[v] = np.array([np.nan if par[v][k] == -999 else par[v][k] for k in keys], float)
    return out


# ---------------------------------------------------------------- SYNOP parsing
def _num(s):
    try:
        return int(s)
    except ValueError:
        return None


def parse_synop_file(path: Path):
    """Ogimet getsynop CSV: id,Y,M,D,h,m,report. Returns list of dicts."""
    obs = []
    for line in path.read_text(errors="replace").splitlines():
        p = line.strip().split(",", 6)
        if len(p) < 7 or not p[0].isdigit():
            continue
        t = dt.datetime(int(p[1]), int(p[2]), int(p[3]), int(p[4]), int(p[5]), tzinfo=dt.timezone.utc)
        g = p[6].rstrip("=").split()
        if len(g) < 5 or g[0] != "AAXX" or "NIL" in g:
            continue
        iw = g[1][-1]
        ir, ix = g[3][0], g[3][1]
        o = {"time": t, "iR": ir, "ix": ix, "wind": None, "dir": None, "T": None, "Td": None,
             "precip": None, "precip_h": None, "ww": None, "W1": None, "W2": None}
        nddff = g[4]
        dd, ff = _num(nddff[1:3]), _num(nddff[3:5])
        i = 5
        if ff == 99 and len(g) > 5 and g[5].startswith("00"):
            ff = _num(g[5][2:5])
            i = 6
        if ff is not None:
            o["wind"] = ff * (0.514444 if iw in "34" else 1.0)
            o["dir"] = None if dd is None else (dd * 10 if dd not in (0, 99) else dd)
        sec = 1
        while i < len(g):
            tok = g[i]
            i += 1
            if tok == "333":
                sec = 3
                continue
            if tok == "555":
                break
            if len(tok) != 5:
                continue
            if sec == 1:
                if tok[0] == "1" and tok[1] in "01" and tok[2:].isdigit():
                    o["T"] = (1 if tok[1] == "0" else -1) * int(tok[2:]) / 10
                elif tok[0] == "2" and tok[1] in "01" and tok[2:].isdigit():
                    o["Td"] = (1 if tok[1] == "0" else -1) * int(tok[2:]) / 10
                elif tok[0] == "6" and tok[1:4].isdigit() and tok[4].isdigit():
                    rrr = int(tok[1:4])
                    o["precip"] = rrr if rrr < 989 else (0.0 if rrr == 990 else ((rrr - 990) / 10 if rrr > 990 else 989))
                    o["precip_h"] = {"1": 6, "2": 12, "3": 18, "4": 24, "5": 1, "6": 2, "7": 3, "8": 9, "9": 15}.get(tok[4])
                elif tok[0] == "7" and tok[1:3].isdigit():
                    o["ww"] = int(tok[1:3])
                    o["W1"] = _num(tok[3])
                    o["W2"] = _num(tok[4])
        if ir == "3" and o["precip"] is None:
            o["precip"] = 0.0  # iR=3: precipitation amount is zero
        obs.append(o)
    return obs


def wet_code(o):
    ww, w1, w2 = o["ww"], o["W1"], o["W2"]
    if ww is not None and (20 <= ww <= 27 or ww == 29 or ww >= 50):
        return True
    return any(w is not None and w >= 5 for w in (w1, w2))


# ---------------------------------------------------------------- helpers
def pass_times(track):
    m = json.loads((ROOT / f"data/raw/previews/{track}/meta.json").read_text())
    return [(p["date"], dt.datetime.fromisoformat(p["startTime"].replace("Z", "+00:00"))) for p in m["passes"]]


def idx_hour(times, t):
    t0 = times[0]
    return int(round((t - t0).total_seconds() / 3600))


def mwu(flag, rest):
    r = stats.mannwhitneyu(flag, rest, alternative="greater", method="exact")
    return {"U": float(r.statistic), "p_one_sided": float(r.pvalue), "n_flagged": len(flag), "n_other": len(rest),
            "min_possible_p": 1 / math.comb(len(flag) + len(rest), len(flag))}


def corr(x, y):
    x, y = np.asarray(x, float), np.asarray(y, float)
    m = np.isfinite(x) & np.isfinite(y)
    if m.sum() < 4:
        return None
    pr = stats.pearsonr(x[m], y[m])
    sr = stats.spearmanr(x[m], y[m])
    return {"n": int(m.sum()), "pearson_r": round(float(pr.statistic), 3), "pearson_p": round(float(pr.pvalue), 4),
            "spearman_rho": round(float(sr.statistic), 3), "spearman_p": round(float(sr.pvalue), 4)}


def r2(v, n=2):
    if v is None:
        return None
    if isinstance(v, (list, tuple, np.ndarray)):
        return [r2(x, n) for x in v]
    v = float(v)
    return None if not math.isfinite(v) else round(v, n)


def style(ax):
    ax.set_facecolor(C["surf"])
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(C["ink2"])
    ax.tick_params(colors=C["ink2"], labelsize=8)
    ax.grid(axis="y", color=C["grid"], lw=0.8)
    ax.set_axisbelow(True)


def savefig(fig, name):
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / name
    fig.savefig(p, dpi=110, facecolor=C["surf"], bbox_inches="tight")
    plt.close(fig)
    return {"file": str(p.relative_to(ROOT)), "kB": round(p.stat().st_size / 1024, 1)}


# ---------------------------------------------------------------- repo values
def our_values():
    ts = {a: json.loads((ROOT / f"src/app/public/data/{a}/timeseries.json").read_text())
          for a in ("tonle-sap", "sevan", "ararat")}
    st = json.loads((ROOT / "docs/eda/stats.json").read_text())["sevan_lake"]
    # wind-suspect km2 from DETECTION.md section 6.2 table (last column)
    md = (ROOT / "docs/DETECTION.md").read_text()
    sec = md.split("### 6.2 Lake Sevan", 1)[1].split("###", 1)[0]
    ws = {}
    for line in sec.splitlines():
        m = re.match(r"\|\s*(2026-\d\d-\d\d)\s*\|.*\|\s*([\d.,]+)\s*\|\s*$", line)
        if m:
            ws[m.group(1)] = float(m.group(2).replace(",", ""))
    facts = lambda a, lab: next(f["values"] for f in ts[a]["facts"] if f["label"] == lab)  # noqa: E731
    return {
        "tonle_dates": ts["tonle-sap"]["dates"],
        "tonle_open_water_km2": facts("tonle-sap", "Open water"),
        "tonle_flooded_veg_km2": facts("tonle-sap", "Flooded vegetation"),
        "arm_dates": ts["sevan"]["dates"],
        "sevan_open_water_km2_app": facts("sevan", "Open water"),
        "ararat_open_water_km2_app": facts("ararat", "Open water"),
        "sevan_lake_mean_HH": st["lake_mean_HH"],
        "sevan_lake_HH_p90": st["lake_HH_p90"],
        "sevan_lake_mean_HV": st["lake_mean_HV"],
        "sevan_wind_suspect_km2": [ws.get(d) for d in ts["sevan"]["dates"]],
        "provenance": {"areas": "src/app/public/data/<area>/timeseries.json facts",
                       "lake_HH": "docs/eda/stats.json sevan_lake.lake_mean_HH (fixed lake mask, preview units)",
                       "wind_suspect": "docs/DETECTION.md section 6.2 table"},
    }


# ================================================================ TEST 1 + 2 (Armenia)
def armenia(ours, R):
    passes = pass_times("T85_F22")
    dates = [d for d, _ in passes]
    assert dates == ours["arm_dates"], (dates, ours["arm_dates"])
    om = {n: openmeteo_hourly(n, *ll) for n, ll in ARM_POINTS.items()}
    pw = {n: power_hourly(n, *ARM_POINTS[n]) for n in ("sevan_mid", "ararat_valley")}
    syn = {}
    for sid in SYNOP:
        url = f"https://www.ogimet.com/cgi-bin/getsynop?block={sid}&begin=202606150000&end=202610202359"
        p = fetch(url, WX / f"syn_{sid}.txt", label=f"SYNOP {sid} {SYNOP[sid][0]}",
                  provider="Ogimet (WMO GTS SYNOP reports)")
        syn[sid] = parse_synop_file(p) if p else []
    metar_url = ("https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?station=UDYZ&data=sknt&data=gust&data=drct"
                 "&data=tmpf&data=dwpf&data=wxcodes&data=p01m&year1=2026&month1=6&day1=15&year2=2026&month2=10&day2=21"
                 "&tz=Etc/UTC&format=onlycomma&latlon=no&missing=M&trace=T&report_type=3&report_type=4")
    mp = fetch(metar_url, WX / "metar_UDYZ.csv", label="METAR UDYZ Yerevan Zvartnots",
               provider="Iowa Environmental Mesonet (METAR archive)")
    metar = list(csv.DictReader(io.StringIO(mp.read_text()))) if mp else []
    for r in metar:
        r["t"] = dt.datetime.strptime(r["valid"], "%Y-%m-%d %H:%M").replace(tzinfo=dt.timezone.utc)

    grid = {n: {"grid_lat": om[n]["grid_lat"], "grid_lon": om[n]["grid_lon"], "grid_elevation_m": om[n]["elev"]}
            for n in om}

    # ---- Test 1: wind
    per = []
    season = {}
    for n in LAKE_POINTS:
        for model in ("era5", "ecmwf_ifs"):
            w = om[n][f"wind_speed_10m_{model}"]
            hrs = np.array([t.hour for t in om[n]["time"]])
            season[f"{n}_{model}"] = w[hrs == 2]
    for (d, T) in passes:
        row = {"date": d, "pass_utc": T.strftime("%H:%M"), "flagged": d in FLAGGED}
        for n in LAKE_POINTS:
            i = idx_hour(om[n]["time"], T)
            for model in ("era5", "ecmwf_ifs"):
                w = om[n][f"wind_speed_10m_{model}"]
                g = om[n][f"wind_gusts_10m_{model}"]
                row[f"{n}_{model}_ws"] = r2(w[i])
                row[f"{n}_{model}_ws_max4h"] = r2(np.nanmax(w[i - 3:i + 1]))
                row[f"{n}_{model}_gust"] = r2(g[i])
                row[f"{n}_{model}_dir"] = r2(om[n][f"wind_direction_10m_{model}"][i], 0)
                s = season[f"{n}_{model}"]
                row[f"{n}_{model}_ws_season_pct"] = r2(100 * np.mean(s[np.isfinite(s)] <= w[i]), 0)
        ip = idx_hour(pw["sevan_mid"]["time"], T)
        row["power_sevan_mid_ws"] = r2(pw["sevan_mid"]["WS10M"][ip])
        row["power_sevan_mid_ws_max4h"] = r2(np.nanmax(pw["sevan_mid"]["WS10M"][ip - 3:ip + 1]))
        for sid in ("37717", "37801"):
            ob = {o["time"]: o for o in syn[sid]}
            d0 = T.replace(hour=0, minute=0, second=0)
            o00, o03 = ob.get(d0), ob.get(d0 + dt.timedelta(hours=3))
            w00 = o00["wind"] if o00 else None
            w03 = o03["wind"] if o03 else None
            frac = (T - d0).total_seconds() / 10800
            row[f"syn{sid}_00z"] = w00
            row[f"syn{sid}_00z_dir"] = o00["dir"] if o00 else None
            row[f"syn{sid}_03z"] = w03
            row[f"syn{sid}_03z_dir"] = o03["dir"] if o03 else None
            row[f"syn{sid}_pass"] = r2(w00 + (w03 - w00) * frac) if (w00 is not None and w03 is not None) else (w00 if w03 is None else w03)
            prev = [ob[t]["wind"] for t in (d0 - dt.timedelta(hours=h) for h in (9, 6, 3, 0)) if t in ob and ob[t]["wind"] is not None]
            row[f"syn{sid}_max_prev12h"] = max(prev) if prev else None
            # windiest synoptic report in the 24 h before the pass
            prev24 = [ob[t]["wind"] for t in (d0 - dt.timedelta(hours=h) for h in range(0, 24, 3)) if t in ob and ob[t]["wind"] is not None]
            row[f"syn{sid}_max_prev24h"] = max(prev24) if prev24 else None
        per.append(row)

    def series(key):
        return [r[key] for r in per]

    flag_i = [i for i, r in enumerate(per) if r["flagged"]]
    calm_i = [i for i, r in enumerate(per) if not r["flagged"]]
    tests = {}
    keys = ["sevan_mid_era5_ws", "sevan_mid_ecmwf_ifs_ws", "sevan_nw_bay_era5_ws", "sevan_nw_bay_ecmwf_ifs_ws",
            "gavar_shore_ecmwf_ifs_ws", "martuni_ecmwf_ifs_ws", "sevan_mid_ecmwf_ifs_ws_max4h",
            "sevan_mid_ecmwf_ifs_gust", "sevan_mid_era5_gust", "power_sevan_mid_ws",
            "syn37717_pass", "syn37717_max_prev12h", "syn37717_max_prev24h", "syn37801_pass", "syn37801_max_prev12h"]
    for k in keys:
        v = np.array([np.nan if x is None else x for x in series(k)], float)
        if np.isnan(v).any():
            tests[k] = {"note": "missing values", "values": r2(v)}
            continue
        mw = mwu(v[flag_i], v[calm_i])
        rank_desc = stats.rankdata(-v, method="min")
        tests[k] = {"flagged_values": r2(v[flag_i]), "other_mean": r2(v[calm_i].mean()), "other_max": r2(v[calm_i].max()),
                    "flagged_ranks_of_10_(1=windiest)": [int(rank_desc[i]) for i in flag_i],
                    "mann_whitney_flagged_greater": {k2: r2(v2, 4) for k2, v2 in mw.items()},
                    "corr_with_lake_mean_HH": corr(v, ours["sevan_lake_mean_HH"]),
                    "corr_with_wind_suspect_km2": corr(v, ours["sevan_wind_suspect_km2"])}
    n_over3 = {k: int(np.sum(np.array([x or 0 for x in series(k)]) >= 3)) for k in keys}
    R["test1_wind_sevan"] = {"per_date": per, "tests": tests, "dates_with_wind_ge_3ms": n_over3,
                             "model_grid": {n: grid[n] for n in LAKE_POINTS},
                             "power_sources": pw["sevan_mid"]["sources"],
                             "lake_mean_HH": ours["sevan_lake_mean_HH"],
                             "wind_suspect_km2": ours["sevan_wind_suspect_km2"]}

    # ---- Test 2: rain before each pass + temperature
    per2 = []
    for (d, T) in passes:
        row = {"date": d}
        for n in LAKE_POINTS + VALLEY_POINTS:
            i = idx_hour(om[n]["time"], T.replace(minute=0))
            for model in ("era5", "ecmwf_ifs"):
                pr = om[n][f"precipitation_{model}"]
                row[f"{n}_{model}_p48h"] = r2(np.nansum(pr[i - 47:i + 1]), 1)
                row[f"{n}_{model}_p24h"] = r2(np.nansum(pr[i - 23:i + 1]), 1)
            row[f"{n}_ecmwf_ifs_T"] = r2(om[n]["temperature_2m_ecmwf_ifs"][i], 1)
            row[f"{n}_ecmwf_ifs_Td"] = r2(om[n]["dew_point_2m_ecmwf_ifs"][i], 1)
            row[f"{n}_ecmwf_ifs_RH"] = r2(om[n]["relative_humidity_2m_ecmwf_ifs"][i], 0)
        for n in ("sevan_mid", "ararat_valley"):
            ip = idx_hour(pw[n]["time"], T.replace(minute=0))
            row[f"power_{n}_p48h"] = r2(np.nansum(pw[n]["PRECTOTCORR"][ip - 47:ip + 1]), 1)
            row[f"power_{n}_T"] = r2(pw[n]["T2M"][ip], 1)
            row[f"power_{n}_Td"] = r2(pw[n]["T2MDEW"][ip], 1)
        d0 = T.replace(hour=0, minute=0, second=0)
        for sid in SYNOP:
            ob = {o["time"]: o for o in syn[sid]}
            # 12-h amounts reported at 06 and 18 UTC; four windows = 06Z two days before to 06Z pass day
            ends = [d0 - dt.timedelta(hours=h) for h in (42, 30, 18, 6)] + [d0 + dt.timedelta(hours=6)]
            ends = ends[1:]  # 18Z D-2, 06Z D-1, 18Z D-1, 06Z D
            amts = []
            for e in ends:
                o = ob.get(e)
                amts.append(None if o is None else (o["precip"] if o["precip_h"] in (12, None) else None))
            tot = None if any(a is None for a in amts) else round(sum(amts), 1)
            win = [o for t, o in ob.items() if d0 - dt.timedelta(hours=46) <= t <= T]
            row[f"syn{sid}_p48h"] = tot
            row[f"syn{sid}_wet_reports_48h"] = sum(wet_code(o) for o in win)
            row[f"syn{sid}_n_reports_48h"] = len(win)
            o00 = ob.get(d0)
            o03 = ob.get(d0 + dt.timedelta(hours=3))
            row[f"syn{sid}_T00z"] = o00["T"] if o00 else None
            row[f"syn{sid}_Td00z"] = o00["Td"] if o00 else None
            row[f"syn{sid}_T03z"] = o03["T"] if o03 else None
        mw = [r for r in metar if T - dt.timedelta(hours=48) <= r["t"] <= T]
        row["metar_UDYZ_wx_48h"] = sorted({r["wxcodes"] for r in mw if r["wxcodes"] not in ("M", "")})
        row["metar_UDYZ_precip_codes_48h"] = sum(1 for r in mw if re.search(r"RA|DZ|SH|TS|SN|GR", r["wxcodes"] or ""))
        mp_ = [r for r in metar if abs((r["t"] - T).total_seconds()) <= 1800]
        if mp_:
            r0 = min(mp_, key=lambda r: abs((r["t"] - T).total_seconds()))
            f2c = lambda f: None if f in ("M", "") else round((float(f) - 32) / 1.8, 1)  # noqa: E731
            row["metar_UDYZ_T_pass"], row["metar_UDYZ_Td_pass"] = f2c(r0["tmpf"]), f2c(r0["dwpf"])
            row["metar_UDYZ_wind_pass_ms"] = None if r0["sknt"] == "M" else round(float(r0["sknt"]) * 0.514444, 1)
        per2.append(row)
    oct6 = next(r for r in per2 if r["date"] == "2026-10-06")
    summ = {}
    for k in [k for k in per2[0] if k.endswith("_p48h")]:
        v = [r[k] for r in per2]
        if any(x is None for x in v):
            summ[k] = {"values": v}
            continue
        others = [x for r, x in zip(per2, v) if r["date"] != "2026-10-06"]
        summ[k] = {"oct6": oct6[k], "others_max": max(others), "others_mean": round(float(np.mean(others)), 2),
                   "oct6_rank_of_10_(1=wettest)": int(stats.rankdata([-x for x in v], method="min")[dates.index("2026-10-06")])}
    rain_vs_hh = {k: corr([r[k] for r in per2], ours["sevan_lake_mean_HH"])
                  for k in ("syn37717_p48h", "syn37792_p48h", "sevan_mid_ecmwf_ifs_p48h", "sevan_mid_era5_p48h")}
    R["test2_armenia_oct6"] = {"per_date": per2, "p48h_summary": summ, "corr_lake_mean_HH_vs_rain48h": rain_vs_hh,
                               "station_notes": {"37788": "all 2026 reports NIL (station silent); Yerevan covered by METAR UDYZ",
                                                 "37717": "dew point coded equal to air temperature in ~95 % of night reports; Td not used",
                                                 "precip_window": "SYNOP: four 12-h gauge totals ending 18Z D-2, 06Z D-1, 18Z D-1, 06Z D (pass ~02:04Z, so the last 4 h are after the pass); models: 48 hourly sums ending at the pass hour"},
                               "ours_ararat_open_water_km2_app": ours["ararat_open_water_km2_app"],
                               "ours_sevan_lake_mean_HV": ours["sevan_lake_mean_HV"]}

    # ---- figures 1-3
    x = np.arange(len(dates))
    lab = [dt.date.fromisoformat(d).strftime("%-d %b") for d in dates]
    fig, ax = plt.subplots(figsize=(8.4, 3.9))
    style(ax)
    for i in flag_i:
        ax.axvspan(i - 0.45, i + 0.45, color=C["hl"], zorder=0)
    ser = [("syn37717_pass", "Station Sevan 37717 (NW bay), interpolated to pass", C["s1"], "o"),
           ("syn37801_pass", "Station Gavar 37801 (W shore)", C["s2"], "s"),
           ("sevan_mid_ecmwf_ifs_ws", "ECMWF IFS 9 km, mid-lake", C["s3"], "D"),
           ("sevan_mid_era5_ws", "ERA5 0.25 deg, mid-lake", C["s4"], "^")]
    for j, (k, name, col, mk) in enumerate(ser):
        v = [np.nan if r[k] is None else r[k] for r in per]
        ax.plot(x + (j - 1.5) * 0.12, v, mk, color=col, ms=7, mec=C["surf"], mew=1, label=name, zorder=3)
    vprev = [np.nan if r["syn37717_max_prev12h"] is None else r["syn37717_max_prev12h"] for r in per]
    ax.plot(x - 0.18, vprev, "o", mfc="none", mec=C["s1"], mew=1.5, ms=9,
            label="Station Sevan 37717, strongest report in 12 h before pass", zorder=3)
    ax.axhline(3, color=C["ink2"], lw=1, ls="--")
    ax.text(len(dates) - 0.5, 3.08, "~3 m/s: L-band water starts to brighten", ha="right", va="bottom", fontsize=7.5, color=C["ink2"])
    ax.set_xticks(x, lab)
    for i in flag_i:
        ax.get_xticklabels()[i].set_fontweight("bold")
    ax.set_ylabel("10 m wind (m/s)", fontsize=8.5, color=C["ink2"])
    ax.set_ylim(0, 6.5)
    ax.set_title("Lake Sevan: wind at the ~06:04 local radar pass (shaded = dates we flagged as windy)", fontsize=9.5, color=C["ink"], loc="left")
    ax.legend(fontsize=7.5, frameon=False, loc="upper left", ncol=2)
    figs = [savefig(fig, "fig1_sevan_wind_per_pass.png")]

    fig, axs = plt.subplots(1, 2, figsize=(8.4, 3.5), sharey=True)
    hh = np.array(ours["sevan_lake_mean_HH"])
    wsk = np.array([w or 0 for w in ours["sevan_wind_suspect_km2"]])
    for ax, (k, name, col) in zip(axs, [("syn37717_pass", "Station Sevan 37717", C["s1"]),
                                        ("sevan_mid_ecmwf_ifs_ws", "ECMWF IFS mid-lake", C["s3"])]):
        style(ax)
        v = np.array([r[k] for r in per], float)
        ax.scatter(v, hh, s=30 + wsk * 2, color=col, edgecolor=C["surf"], lw=1.5, zorder=3)
        for i in range(len(dates)):
            if per[i]["flagged"] or hh[i] > 0.02:
                ax.annotate(lab[i], (v[i], hh[i]), xytext=(5, 3), textcoords="offset points", fontsize=7.5, color=C["ink2"])
        c_ = tests[k]["corr_with_lake_mean_HH"]
        ax.set_title(f"{name}\nSpearman rho = {c_['spearman_rho']}, Pearson r = {c_['pearson_r']} (n = 10)", fontsize=8.5, loc="left", color=C["ink"])
        ax.set_xlabel("wind at pass time (m/s)", fontsize=8.5, color=C["ink2"])
    axs[0].set_ylabel("mean lake HH brightness (preview units)", fontsize=8.5, color=C["ink2"])
    fig.text(0.01, -0.04, "Marker area grows with the wind-suspect area (36 km2 on 31 Aug, 67 km2 on 12 Sep).", fontsize=7.5, color=C["ink2"])
    figs.append(savefig(fig, "fig2_wind_vs_lake_brightness.png"))

    fig, ax = plt.subplots(figsize=(8.4, 3.6))
    style(ax)
    i6 = dates.index("2026-10-06")
    ax.axvspan(i6 - 0.45, i6 + 0.45, color=C["hl"], zorder=0)
    ser2 = [("ararat_valley_ecmwf_ifs_p48h", "ECMWF IFS 9 km, Ararat Valley", C["s1"]),
            ("syn37787_p48h", "Station Armavir 37787 (valley, rain gauge)", C["s2"]),
            ("sevan_mid_ecmwf_ifs_p48h", "ECMWF IFS 9 km, Sevan mid-lake", C["s3"]),
            ("syn37717_p48h", "Station Sevan 37717 (rain gauge)", C["s4"])]
    w = 0.19
    for j, (k, name, col) in enumerate(ser2):
        v = [np.nan if r[k] is None else r[k] for r in per2]
        ax.bar(x + (j - 1.5) * w, v, w * 0.9, color=col, label=name, zorder=3)
    ax.set_xticks(x, lab)
    ax.get_xticklabels()[i6].set_fontweight("bold")
    ax.set_ylabel("precipitation in 48 h before pass (mm)", fontsize=8.5, color=C["ink2"])
    ax.set_title("Armenia: rain in the 48 h before each pass (shaded = 6 Oct, the HV-anomaly date)", fontsize=9.5, loc="left", color=C["ink"])
    ax.legend(fontsize=7.5, frameon=False, loc="upper left")
    figs.append(savefig(fig, "fig3_armenia_rain_before_pass.png"))
    return figs


# ================================================================ TEST 3 + 4 (levels)
def parse_ffw(path):
    t = path.read_text()
    recs = []
    for body in re.findall(r"\{([^}]*)\}", t):
        d = {}
        for k, v in re.findall(r'(\w+):("[^"]*"|[^,]*)', body):
            v = v.strip('"')
            try:
                d[k] = float(v)
            except ValueError:
                d[k] = v
        recs.append(d)
    return recs


def parse_gwm(path):
    rows = []
    for line in path.read_text().splitlines():
        p = line.split()
        if len(p) >= 16 and p[2].isdigit() and p[2] != "99999999":
            if float(p[14]) > 9000:
                continue
            t = dt.datetime.strptime(p[2], "%Y%m%d") + dt.timedelta(hours=int(p[3]), minutes=int(p[4]))
            rows.append({"mission": p[0], "cycle": int(p[1]), "time": t, "h_rel": float(p[5]), "err": float(p[6]),
                         "h_egm2008": float(p[14])})
    return rows


def lag_days(level_2026, t_days, clim_days, clim_level):
    """Days after the climatological curve first reached this level on its rising limb."""
    pk = int(np.nanargmax(clim_level))
    cl, cd = clim_level[:pk + 1], clim_days[:pk + 1]
    hit = np.where(cl >= level_2026)[0]
    if not len(hit):
        return None
    return round(float(t_days - cd[hit[0]]), 1)


def tonle(ours, R):
    passes = pass_times("T83_F8")
    dates = [d for d, _ in passes]
    assert dates == ours["tonle_dates"]
    area = np.array(ours["tonle_open_water_km2"], float)
    fv = np.array(ours["tonle_flooded_veg_km2"], float)
    tp = np.array([(T - dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)).total_seconds() / 86400 for _, T in passes])

    ffw = {}
    for code, name in [("PRE", "Prek Kdam (Tonle Sap River)"), ("KRA", "Kratie (Mekong)"), ("PPP", "Phnom Penh Port")]:
        p = fetch(f"https://ffw.mrcmekong.org/fetchwet_st.php?StCode={code}", LV / f"ffw_{code}.txt",
                  label=f"MRC FFW daily water level {name}", provider="Mekong River Commission flood forecasting (ffw.mrcmekong.org)")
        recs = parse_ffw(p)
        day = np.array([(dt.datetime.fromisoformat(r["date_gmt"]) - dt.datetime(2026, 1, 1)).days for r in recs], float)
        lv = np.array([r.get("2026", np.nan) if isinstance(r.get("2026"), float) else np.nan for r in recs])
        avg = np.array([r.get("AVG", np.nan) for r in recs], float)
        years = sorted(k for k in recs[0] if k.isdigit() and k != "2026")
        yrs = np.array([[r.get(y, np.nan) if isinstance(r.get(y), float) else np.nan for y in years] for r in recs])
        n_zero = int(np.sum(lv == 0.0))
        lv[lv == 0.0] = np.nan  # exact 0.00 in Jun-Oct = missing reading (e.g. 4 Sep, 23 Sep, 1 Oct 2026)
        yrs[yrs == 0.0] = np.nan
        ffw[code] = {"name": name, "day": day, "lv": lv, "avg": avg, "max": np.array([r.get("Max", np.nan) for r in recs], float),
                     "min": np.array([r.get("Min", np.nan) for r in recs], float), "years": years, "yrs": yrs,
                     "first": recs[0]["date_gmt"], "last": recs[-1]["date_gmt"], "n_zero_2026_treated_missing": n_zero}

    gp = fetch("https://earth.gsfc.nasa.gov/gwm/timeseries/lake000319.10d.2.txt", LV / "gwm_lake000319.10d.2.txt",
               label="G-REALM Tonle Sap (000319) 10-day altimetry", provider="NASA GSFC Global Water Monitor / USDA FAS G-REALM (Sentinel-6A MF in 2026)")
    alt = parse_gwm(gp)
    at = np.array([(r["time"] - dt.datetime(2026, 1, 1)).total_seconds() / 86400 for r in alt])
    ah = np.array([r["h_egm2008"] for r in alt])
    y26 = np.array([r["time"].year == 2026 for r in alt])

    # level at pass time (daily readings assumed at 00 UTC of date_gmt -> linear interpolation)
    def at_pass(code, arr):
        f = ffw[code]
        m = np.isfinite(arr)
        return np.interp(tp, f["day"][m], arr[m])

    lvl = {c: at_pass(c, ffw[c]["lv"]) for c in ffw}
    lavg = {c: at_pass(c, ffw[c]["avg"]) for c in ffw}
    alt_pass = np.interp(tp, at[y26], ah[y26])

    # altimetry climatology 2008-2025 (Jason-2 era onward), per day of year
    clim_days = np.arange(120, 330)
    curves = []
    for yr in range(2008, 2026):
        m = np.array([r["time"].year == yr for r in alt])
        if m.sum() < 20:
            continue
        doy = np.array([(r["time"] - dt.datetime(yr, 1, 1)).total_seconds() / 86400 for r in np.array(alt)[m]])
        curves.append(np.interp(clim_days, doy, ah[m], left=np.nan, right=np.nan))
    curves = np.array(curves)
    alt_clim = np.nanmean(curves, 0)
    alt_clim_sd = np.nanstd(curves, 0)
    alt_clim_pass = np.interp(tp, clim_days, alt_clim)

    pre = ffw["PRE"]
    m26 = np.isfinite(pre["lv"])
    out = {"pass_dates": dates, "open_water_km2": area.tolist(), "flooded_veg_km2": fv.tolist(),
           "pass_utc": [T.strftime("%Y-%m-%dT%H:%M") for _, T in passes],
           "level_at_pass_m": {c: r2(lvl[c]) for c in ffw}, "level_avg_at_pass_m": {c: r2(lavg[c]) for c in ffw},
           "level_anomaly_at_pass_m": {c: r2(lvl[c] - lavg[c]) for c in ffw},
           "altimetry_egm2008_at_pass_m": r2(alt_pass), "altimetry_clim_2008_2025_at_pass_m": r2(alt_clim_pass),
           "altimetry_anomaly_at_pass_m": r2(alt_pass - alt_clim_pass),
           "altimetry_2026_points": [{"time": r["time"].strftime("%Y-%m-%d %H:%M"), "h_egm2008": r["h_egm2008"], "mission": r["mission"]}
                                     for r in alt if r["time"] >= dt.datetime(2026, 5, 1)],
           "altimetry_clim_years": len(curves),
           "ffw_coverage": {c: [ffw[c]["first"], ffw[c]["last"]] for c in ffw},
           "ffw_years_in_table": ffw["PRE"]["years"]}
    # AVG check: mean of the individual years in the table
    out["ffw_PRE_avg_vs_mean_of_listed_years_maxdiff_m"] = r2(np.nanmax(np.abs(np.nanmean(pre["yrs"], 1) - pre["avg"])))
    out["corr_area_vs"] = {f"{c}_level": corr(area, lvl[c]) for c in ffw}
    out["corr_area_vs"]["altimetry"] = corr(area, alt_pass)
    out["corr_flooded_veg_vs_PRE_level"] = corr(fv, lvl["PRE"])
    out["corr_flooded_veg_vs_altimetry"] = corr(fv, alt_pass)
    out["corr_open_plus_fv_vs_altimetry"] = corr(area + fv, alt_pass)
    out["ffw_zero_values_treated_missing"] = {c: ffw[c]["n_zero_2026_treated_missing"] for c in ffw}

    # timing
    def onset(day, v, base_end=181, thr=0.5):
        base = np.nanmean(v[(day >= 151) & (day <= base_end)])
        ok = np.where(np.isfinite(v) & (v >= base + thr) & (day > base_end))[0]
        return (None, base) if not len(ok) else (day[ok[0]], base)

    def fastest(day, v, w=12):
        best, bd = -1e9, None
        for i in range(len(day)):
            j = np.where(day == day[i] + w)[0]
            if len(j) and np.isfinite(v[i]) and np.isfinite(v[j[0]]) and v[j[0]] - v[i] > best:
                best, bd = v[j[0]] - v[i], day[i]
        return bd, best

    d2s = lambda d: None if d is None else (dt.date(2026, 1, 1) + dt.timedelta(days=float(d))).isoformat()  # noqa: E731
    timing = {}
    for c in ffw:
        f = ffw[c]
        o26, b26 = onset(f["day"], f["lv"])
        oav, bav = onset(f["day"], f["avg"])
        fd26, fv26 = fastest(f["day"], f["lv"])
        fdav, fvav = fastest(f["day"], f["avg"])
        pk26 = int(np.nanargmax(np.where(np.isfinite(f["lv"]), f["lv"], -1e9)))
        pkav = int(np.nanargmax(f["avg"]))
        lags = [lag_days(lvl[c][i], tp[i], f["day"], f["avg"]) for i in range(len(tp))]
        timing[c] = {"onset_rule": "first day level >= June mean + 0.5 m",
                     "onset_2026": d2s(o26), "onset_avg": d2s(oav), "june_mean_2026": r2(b26), "june_mean_avg": r2(bav),
                     "fastest_12d_rise_2026": {"start": d2s(fd26), "rise_m": r2(fv26)},
                     "fastest_12d_rise_avg": {"start": d2s(fdav), "rise_m": r2(fvav)},
                     "peak_2026_in_record": {"date": d2s(f["day"][pk26]), "m": r2(f["lv"][pk26]), "record_ends": f["last"]},
                     "peak_avg": {"date": d2s(f["day"][pkav]), "m": r2(f["avg"][pkav])},
                     "lag_vs_avg_at_pass_days": lags}
    # altimetry timing
    a26d, a26h = at[y26], ah[y26]
    lags_alt = [lag_days(alt_pass[i], tp[i], clim_days.astype(float), alt_clim) for i in range(len(tp))]
    pk = int(np.argmax(a26h))
    timing["altimetry"] = {"peak_2026": {"date": d2s(a26d[pk]), "m": r2(a26h[pk])},
                           "peak_clim": {"date": d2s(clim_days[int(np.nanargmax(alt_clim))]), "m": r2(np.nanmax(alt_clim))},
                           "lag_vs_clim_at_pass_days": lags_alt,
                           "largest_10d_rise_2026": None}
    steps = [(a26h[i + 1] - a26h[i], a26d[i], a26d[i + 1]) for i in range(len(a26d) - 1)]
    s = max(steps)
    timing["altimetry"]["largest_10d_rise_2026"] = {"from": d2s(s[1]), "to": d2s(s[2]), "rise_m": r2(s[0])}
    # area timing
    da = np.diff(area)
    gap = np.diff(tp)
    rate = da / gap
    j = int(np.argmax(da))
    jr = int(np.argmax(rate))
    timing["area"] = {"onset_rule": "first pass with open water >= 110 % of 19 Jun",
                      "onset": next(d for d, a in zip(dates, area) if a >= 1.1 * area[0]),
                      "largest_step": {"from": dates[j], "to": dates[j + 1], "km2": r2(da[j], 1)},
                      "fastest_rate": {"from": dates[jr], "to": dates[jr + 1], "km2_per_day": r2(rate[jr], 1)},
                      "steps_km2": r2(da, 1), "rate_km2_per_day": r2(rate, 1)}
    # same-interval level steps (for comparing step timing)
    timing["PRE_level_step_between_passes_m"] = r2(np.diff(lvl["PRE"]))
    timing["altimetry_step_between_passes_m"] = r2(np.diff(alt_pass))
    out["timing"] = timing

    # fits area = a + b * level
    fits = {}
    for nm, xv in [("PRE", lvl["PRE"]), ("altimetry_egm2008", alt_pass)]:
        b, a = np.polyfit(xv, area, 1)
        res = area - (a + b * xv)
        q = np.polyfit(xv, area, 2)
        resq = area - np.polyval(q, xv)
        fits[nm] = {"linear": {"intercept_km2": r2(a, 1), "slope_km2_per_m": r2(b, 1), "rmse_km2": r2(np.sqrt(np.mean(res ** 2)), 1),
                               "r2": r2(1 - res.var() / area.var(), 3)},
                    "quadratic_coeffs_high_to_low": r2(q, 3), "quadratic_rmse_km2": r2(np.sqrt(np.mean(resq ** 2)), 1),
                    "level_range_m": [r2(xv.min()), r2(xv.max())]}
    out["fit_area_vs_level"] = fits

    # basin rainfall: Open-Meteo ERA5 daily 2008-2026 + NASA POWER daily
    lats = ",".join(str(v[0]) for v in BASIN_POINTS.values())
    lons = ",".join(str(v[1]) for v in BASIN_POINTS.values())
    url = ("https://archive-api.open-meteo.com/v1/archive?"
           f"latitude={lats}&longitude={lons}&start_date=2008-01-01&end_date=2026-10-31&daily=precipitation_sum&models=era5&timezone=UTC")
    p = fetch(url, WX / "openmeteo_basin_daily.json", label="Open-Meteo ERA5 daily precipitation, 5 basin points 2008-2026",
              provider="Open-Meteo (ECMWF ERA5 0.25 deg)")
    dd = json.loads(p.read_text())
    rain = {}
    for name, d in zip(BASIN_POINTS, dd):
        t = np.array([dt.date.fromisoformat(x) for x in d["daily"]["time"]])
        v = np.array([np.nan if x is None else x for x in d["daily"]["precipitation_sum"]], float)
        rain[name] = (t, v)
    pw_rain = {}
    for name, (la, lo) in BASIN_POINTS.items():
        u = ("https://power.larc.nasa.gov/api/temporal/daily/point?parameters=PRECTOTCORR&community=AG"
             f"&longitude={lo}&latitude={la}&start=20080101&end=20261031&format=JSON")
        pp = fetch(u, WX / f"power_daily_{name}.json", label=f"NASA POWER daily precipitation {name}",
                   provider="NASA LaRC POWER (PRECTOTCORR, 0.5 x 0.625 deg)")
        if pp:
            pr = json.loads(pp.read_text())["properties"]["parameter"]["PRECTOTCORR"]
            ks = sorted(pr)
            pw_rain[name] = (np.array([dt.date(int(k[:4]), int(k[4:6]), int(k[6:])) for k in ks]),
                             np.array([np.nan if pr[k] < 0 else pr[k] for k in ks], float))
    rain_out = {}
    for src, R_ in (("era5", rain), ("power", pw_rain)):
        months = range(5, 11)
        mtot = {m: [] for m in months}
        clim = {m: [] for m in months}
        cum26 = None
        half = {}
        tot26, totc = [], []
        for name, (t, v) in R_.items():
            yrs = np.array([x.year for x in t])
            mos = np.array([x.month for x in t])
            for m in months:
                mtot[m].append(np.nansum(v[(yrs == 2026) & (mos == m)]))
                clim[m].append(np.mean([np.nansum(v[(yrs == y) & (mos == m)]) for y in range(2008, 2026)]))
        # basin-mean daily May-Oct, and half-season date
        def season_curve(year):
            s = []
            for name, (t, v) in R_.items():
                m = np.array([(x.year == year) and (5 <= x.month <= 10) for x in t])
                s.append(v[m])
            return np.nanmean(np.array(s), 0)
        c26 = np.cumsum(season_curve(2026))
        half26 = int(np.searchsorted(c26, c26[-1] / 2))
        halfc = [int(np.searchsorted(np.cumsum(season_curve(y)), np.cumsum(season_curve(y))[-1] / 2)) for y in range(2008, 2026)]
        d0 = dt.date(2026, 5, 1)
        rain_out[src] = {"basin_mean_monthly_2026_mm": {dt.date(2026, m, 1).strftime("%b"): r2(np.mean(mtot[m]), 0) for m in months},
                         "basin_mean_monthly_clim_2008_2025_mm": {dt.date(2026, m, 1).strftime("%b"): r2(np.mean(clim[m]), 0) for m in months},
                         "ratio_2026_to_clim": {dt.date(2026, m, 1).strftime("%b"): r2(np.mean(mtot[m]) / np.mean(clim[m]), 2) for m in months},
                         "may_oct_total_2026_mm": r2(c26[-1], 0),
                         "may_oct_total_clim_mm": r2(np.mean([np.nansum(season_curve(y)) for y in range(2008, 2026)]), 0),
                         "half_season_date_2026": (d0 + dt.timedelta(days=half26)).isoformat(),
                         "half_season_date_clim_median": (d0 + dt.timedelta(days=int(np.median(halfc)))).isoformat(),
                         "half_season_date_clim_range": [(d0 + dt.timedelta(days=min(halfc))).isoformat(), (d0 + dt.timedelta(days=max(halfc))).isoformat()]}
        if src == "era5":
            cum26_era5 = c26
    out["basin_rainfall"] = rain_out
    out["basin_points"] = BASIN_POINTS
    R["test3_tonle_sap"] = out

    # ---- Sevan level (test 4)
    sp = fetch("https://earth.gsfc.nasa.gov/gwm/timeseries/lake000260.27a.2.txt", LV / "gwm_lake000260.27a.2.txt",
               label="G-REALM Sevan (000260) 27-day Sentinel-3A altimetry", provider="NASA GSFC Global Water Monitor / G-REALM (Sentinel-3A)")
    sev = parse_gwm(sp)
    s26 = [r for r in sev if r["time"] >= dt.datetime(2025, 12, 1)]
    same25 = {r["time"].strftime("%m"): r["h_egm2008"] for r in sev if r["time"].year == 2025}
    gauge = [("2026-01-01", 1900.22), ("2026-06-04", 1900.60), ("2026-06-21", 1900.72), ("2026-07-07", 1900.78), ("2026-07-19", 1900.81)]
    g_t = np.array([(dt.datetime.fromisoformat(d) - dt.datetime(2026, 1, 1)).days for d, _ in gauge], float)
    s_t = np.array([(r["time"] - dt.datetime(2026, 1, 1)).total_seconds() / 86400 for r in s26])
    s_h = np.array([r["h_egm2008"] for r in s26])
    alt_at_gauge = np.interp(g_t, s_t, s_h)
    arm_tp = np.array([(T - dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)).total_seconds() / 86400 for _, T in pass_times("T85_F22")])
    R["test4_sevan_level"] = {
        "altimetry_points": [{"date": r["time"].strftime("%Y-%m-%d"), "h_egm2008_m": r["h_egm2008"], "err_m": r["err"]} for r in s26],
        "altimetry_2025_by_month": same25,
        "altimetry_interp_at_pass_m": r2(np.interp(arm_tp, s_t, s_h)),
        "gauge_readings_from_science_brief": [{"date": d, "m": v, "source": "EcoLur / Armenpress, see docs/research/science-brief.md"} for d, v in gauge],
        "altimetry_minus_gauge_at_gauge_dates_m": r2(alt_at_gauge[1:] - np.array([v for _, v in gauge])[1:]),
        "change_jul18_to_oct07_m": r2(next(r["h_egm2008"] for r in s26 if r["time"].strftime("%m%d") == "1007")
                                      - next(r["h_egm2008"] for r in s26 if r["time"].strftime("%m%d") == "0718")),
        "peak_2026": max(({"date": r["time"].strftime("%Y-%m-%d"), "m": r["h_egm2008"]} for r in s26 if r["time"].year == 2026), key=lambda z: z["m"]),
        "app_open_water_km2": ours["sevan_open_water_km2_app"],
        "corr_app_area_vs_level": corr(ours["sevan_open_water_km2_app"], np.interp(arm_tp, s_t, s_h)),
    }

    # ---- figures 4-5
    base = dt.datetime(2026, 1, 1)
    todt = lambda d: [base + dt.timedelta(days=float(x)) for x in d]  # noqa: E731
    fig, (a1, a2, a3) = plt.subplots(3, 1, figsize=(8.4, 7.6), sharex=True, gridspec_kw={"height_ratios": [1.15, 1, 0.7]})
    out_s, out_e = dt.datetime(2026, 7, 27), dt.datetime(2026, 8, 11)
    for ax in (a1, a2, a3):
        style(ax)
        ax.axvspan(out_s, out_e, color=C["hl"], zorder=0)
    a1.fill_between(todt(pre["day"]), pre["min"], pre["max"], color=C["s1"], alpha=0.10, lw=0, label="Prek Kdam, min-max of listed years")
    a1.plot(todt(pre["day"]), pre["avg"], color=C["ink2"], lw=1.5, ls="--", label="Prek Kdam, MRC average")
    a1.plot(todt(pre["day"][m26]), pre["lv"][m26], color=C["s1"], lw=2, label="Prek Kdam 2026 (gauge)")
    a1.plot(todt(at[y26]), ah[y26] - 2.0, "o", color=C["s2"], ms=5, mec=C["surf"], label="Lake altimetry 2026 (EGM2008 height minus 2 m)")
    a1.set_ylabel("water level (m)", fontsize=8.5, color=C["ink2"])
    a1.set_title("Tonle Sap 2026: water level (top) and our radar open-water area (middle); shaded = no radar passes", fontsize=9.5, loc="left", color=C["ink"])
    a1.legend(fontsize=7, frameon=False, loc="upper left")
    pt = [T.replace(tzinfo=None) for _, T in passes]
    a2.plot(pt, area, "-o", color=C["s3"], lw=2, ms=6, mec=C["surf"], label="open water (radar)")
    a2.plot(pt, fv, "-s", color=C["s4"], lw=2, ms=6, mec=C["surf"], label="flooded vegetation (radar)")
    a2.set_ylabel("area (km2)", fontsize=8.5, color=C["ink2"])
    a2.legend(fontsize=7, frameon=False, loc="upper left")
    t_e, v_e = rain["tonle_sap_lake"]
    sel = np.array([(x.year == 2026) and 5 <= x.month <= 10 for x in t_e])
    basin_daily = np.nanmean(np.array([rain[n][1][sel] for n in BASIN_POINTS]), 0)
    days = [dt.datetime(x.year, x.month, x.day) for x in t_e[sel]]
    a3.bar(days, basin_daily, width=1, color=C["s1"], label="ERA5 basin-mean daily rain (5 points)")
    a3.set_ylabel("rain (mm/day)", fontsize=8.5, color=C["ink2"])
    a3.legend(fontsize=7, frameon=False, loc="upper left")
    a3.set_xlim(dt.datetime(2026, 6, 1), dt.datetime(2026, 11, 10))
    a3.xaxis.set_major_formatter(mdates.DateFormatter("%-d %b"))
    figs = [savefig(fig, "fig4_tonle_level_and_area.png")]

    fig, axs = plt.subplots(1, 2, figsize=(8.4, 3.5), sharey=True)
    for ax, (nm, xv, lab_, col) in zip(axs, [("PRE", lvl["PRE"], "Prek Kdam gauge level (m)", C["s1"]),
                                             ("altimetry_egm2008", alt_pass, "lake altimetry height, EGM2008 (m)", C["s2"])]):
        style(ax)
        ax.scatter(xv, area, s=40, color=col, edgecolor=C["surf"], lw=1.5, zorder=3)
        f = fits[nm]["linear"]
        xx = np.linspace(xv.min(), xv.max(), 20)
        ax.plot(xx, f["intercept_km2"] + f["slope_km2_per_m"] * xx, color=C["ink2"], lw=1.2, ls="--")
        c_ = out["corr_area_vs"]["PRE_level" if nm == "PRE" else "altimetry"]
        ax.set_title(f"{f['slope_km2_per_m']:.0f} km2 per m, R2 = {f['r2']}\nPearson {c_['pearson_r']}, Spearman {c_['spearman_rho']} (n = 10)", fontsize=8.5, loc="left", color=C["ink"])
        ax.set_xlabel(lab_, fontsize=8.5, color=C["ink2"])
        for i in (0, 3, 4, 9):
            ax.annotate(dt.date.fromisoformat(dates[i]).strftime("%-d %b"), (xv[i], area[i]), xytext=(5, -10), textcoords="offset points", fontsize=7, color=C["ink2"])
    axs[0].set_ylabel("radar open water (km2)", fontsize=8.5, color=C["ink2"])
    figs.append(savefig(fig, "fig5_area_vs_level.png"))
    return figs


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    ours = our_values()
    R = {"generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "access_date": ACCESS_DATE,
         "ours": ours}
    figs = armenia(ours, R)
    figs += tonle(ours, R)
    R["figures"] = figs
    seen, src = set(), []
    for s in SOURCES:
        if s["url"] not in seen:
            seen.add(s["url"])
            src.append(s)
    R["sources"] = src
    (OUT / "results.json").write_text(json.dumps(R, indent=1, default=str))
    print("wrote", OUT / "results.json")
    for f in figs:
        print(f)


if __name__ == "__main__":
    main()
