"""Rule-based weak labels for the U-Net, with an explicit "not sure" (ignore) class.

Labels exist only where simple radar physics is confident:

  WATER (0)        dark in BOTH HH and HV on this date (calm open water is a mirror).
  FLOODED_VEG (1)  HH rose strongly versus the dry-season reference date (double bounce
                   off water + trunks/stems), HV rose much less than HH, and the pixel is
                   not dark (so it is not open water).
  OTHER (2)        clearly not dark AND almost unchanged in both channels versus the reference.
  IGNORE (255)     everything else (the network has to decide there), and invalid pixels.

All thresholds come from the data of the area itself (Otsu / percentiles), so they
re-tune automatically when calibrated data arrives; they are returned for the notes.
The input is a lightly smoothed (3x3 valid-weighted mean) stack so single speckle
pixels do not flip labels.
"""
from __future__ import annotations

import torch
import torch.nn.functional as F

WATER, FLOODED_VEG, OTHER, IGNORE = 0, 1, 2, 255
CLASS_NAMES = ("open water", "flooded vegetation", "other land")


def smooth(x: torch.Tensor, valid: torch.Tensor, k: int = 3) -> torch.Tensor:
    """Valid-weighted k x k box mean. x [T, C, H, W], valid [T, H, W]."""
    v = valid.float()[:, None]
    num = F.avg_pool2d(x * v, k, 1, k // 2, count_include_pad=False)
    den = F.avg_pool2d(v, k, 1, k // 2, count_include_pad=False)
    return num / den.clamp_min(1e-6)


def otsu(a: torch.Tensor, lo: float = 0.0, hi: float = 1.0, bins: int = 256) -> float:
    """Otsu threshold of a 1-D tensor on [lo, hi]."""
    h = torch.histc(a.float().cpu(), bins, lo, hi)
    p = h / h.sum().clamp_min(1)
    c = torch.linspace(lo, hi, bins + 1)[:-1] + 0.5 * (hi - lo) / bins
    w0 = p.cumsum(0)
    m = (p * c).cumsum(0)
    sb = (m[-1] * w0 - m) ** 2 / (w0 * (1 - w0)).clamp_min(1e-12)
    return c[sb.argmax()].item()


def _q(a: torch.Tensor, q: float, n: int = 2_000_000, seed: int = 0) -> float:
    """Quantile on a random subsample (torch.quantile has an input size limit)."""
    a = a.flatten().cpu()
    if a.numel() > n:
        g = torch.Generator().manual_seed(seed)
        a = a[torch.randint(a.numel(), (n,), generator=g)]
    return torch.quantile(a.float(), q).item()


def thresholds(s: torch.Tensor, valid: torch.Tensor, ref: int = 0) -> dict:
    """Pick thresholds from the smoothed stack s [T, 2, H, W]."""
    hh, hv = s[:, 0][valid], s[:, 1][valid]
    # The first Otsu splits mid from bright land; a second Otsu below it finds the water/land gap.
    o_hh, o_hv = otsu(hh), otsu(hv)
    w_hh = otsu(hh[hh < o_hh], 0, o_hh)
    w_hv = otsu(hv[hv < o_hv], 0, o_hv)
    th = {
        "water_hh": 0.8 * w_hh, "water_hv": 0.8 * w_hv,     # confident water: below 80 % of the gap
        "land_hh": 1.3 * w_hh,                               # confident "not dark"
        "otsu_hh": o_hh, "otsu_hv": o_hv, "gap_hh": w_hh, "gap_hv": w_hv,
    }
    T = s.shape[0]
    others = [t for t in range(T) if t != ref]
    both = valid[others] & valid[ref][None]
    notdark = both & (s[others, 0] > th["land_hh"])
    d_hh = (s[others, 0] - s[ref, 0][None])[notdark]
    d_hv = (s[others, 1] - s[ref, 1][None])[notdark]
    th["flood_dhh"] = max(_q(d_hh, 0.97), 0.08)              # top 3 % HH rises (at least +0.08)
    th["flood_dhv_frac"] = 0.5                               # HV rise must be < half the HH rise
    th["stable_dhh"] = _q(d_hh.abs(), 0.35)                  # calmest 35 % in HH ...
    th["stable_dhv"] = _q(d_hv.abs(), 0.35)                  # ... and in HV
    th["ref_index"] = ref
    return th


def weak_labels(x: torch.Tensor, valid: torch.Tensor, ref: int = 0, th: dict | None = None):
    """x [T, 2, H, W] raw stack -> (labels [T, H, W] uint8, thresholds dict)."""
    s = smooth(x, valid)
    th = th or thresholds(s, valid, ref)
    hh, hv = s[:, 0], s[:, 1]
    d_hh = hh - hh[ref][None]
    d_hv = hv - hv[ref][None]
    both = valid & valid[ref][None]

    lab = torch.full(hh.shape, IGNORE, dtype=torch.uint8)
    other = both & (hh > th["land_hh"]) & (d_hh.abs() < th["stable_dhh"]) & (d_hv.abs() < th["stable_dhv"])
    flood = both & (hh > th["land_hh"]) & (d_hh > th["flood_dhh"]) & (d_hv < th["flood_dhv_frac"] * d_hh)
    water = valid & (hh < th["water_hh"]) & (hv < th["water_hv"])
    lab[other] = OTHER
    lab[flood] = FLOODED_VEG
    lab[water] = WATER
    lab[~valid] = IGNORE
    return lab, th


if __name__ == "__main__":
    import sys
    from scripts.detect.stack import load_preview_stack
    st = load_preview_stack(sys.argv[1] if len(sys.argv) > 1 else "tonle-sap")
    lab, th = weak_labels(st.x, st.valid)
    print({k: round(v, 4) if isinstance(v, float) else v for k, v in th.items()})
    for t, d in enumerate(st.dates):
        n = st.valid[t].sum().item()
        fr = [(lab[t] == c).sum().item() / n for c in (WATER, FLOODED_VEG, OTHER)]
        print(d, "water %.3f  flooded-veg %.3f  other %.3f  ignore %.3f" % (*fr, 1 - sum(fr)))
