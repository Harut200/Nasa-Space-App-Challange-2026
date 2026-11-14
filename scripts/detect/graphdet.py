"""Graph-based and spectral water / change detection on the pixel grid (PyTorch).

    .venv/bin/python -m scripts.detect.graphdet            # all three areas
    .venv/bin/python -m scripts.detect.graphdet sevan      # one area

Input: the filtered log-like stack L [T,2,H,W] (ch0 HH, ch1 HV) and `valid` from
`ops.run_pipeline`. Output: data/processed/<area>/graph/products.pt, figures and
numbers in docs/eda/graph/. Write-up: docs/eda/graph.md.

Five families, each a separate function group below:
  1. pixel graph construction            grid_weights, to_sparse, laplacian_apply, nonlocal_knn_edges
  2. seeded graph segmentation           make_seeds, random_walker, tv_segment, refine_water
  3. spectral methods on the graph       lanczos_smallest, spectral_embed, spectral_clusters, fiedler_cut,
                                         heat_filter
  4. graph change detection              structural_change
  5. temporal spectral analysis          ls_periodogram, eof_modes

Integration (after ops.run_pipeline):
    from scripts.detect import graphdet
    g = graphdet.refine_water(r["L"], r["valid"], r["water"])   # g["water"], g["p_water"]

Device: grid operations (shifted-tensor Laplacian, CG, primal-dual, kNN search) run on MPS when
available; the Lanczos eigensolver uses float64 sparse matrices on the CPU (MPS has no float64
and only partial sparse support).
"""
from __future__ import annotations

import datetime as dt
import json
import math
import resource
import sys
import time

import torch
import torch.nn.functional as F

from scripts.detect import ops
from scripts.detect.stack import ROOT

AREAS = ["tonle-sap", "sevan", "ararat"]
OFFS4 = [(0, 1), (1, 0), (0, -1), (-1, 0)]
OFFS8 = OFFS4 + [(1, 1), (1, -1), (-1, 1), (-1, -1)]

# All choices in one place. Brightness values are preview display units (0..1), as in ops.KNOBS.
GKNOBS = {
    "seed_water_hh": 0.06,     # water seed: HH below this (= ops water_hh_strong) ...
    "seed_water_hv": 0.35,     # ... and HV below this (= ops water_hv_max)
    "seed_min_px": 300,        # ... and part of a strict-dark component >= 300 px (~10 km2)
    "seed_land_hh": 0.20,      # land seed: HH above this (well above ops water_hh_weak 0.12)
    "season_beta": 1.0,        # weight of the season-median features in the edge similarity
    "sigma_scale": 1.0,        # sigma = sigma_scale * median neighbour feature distance
    "rw_tol": 1e-4, "rw_iters": 3000,
    "rw_prior_gamma": 0.0,     # weight of the season prior (share of dates strictly dark); 0 = off (see graph.md)
    "tv_lambda": 0.25,         # pairwise weight of the TV / graph-cut energy
    "tv_floor": 0.1,           # constant added to w in the TV term (so contrast never makes cuts free)
    "tv_iters": 300,
    "fv_hh_rise": 0.20, "fv_hv_rise_max": 0.03,   # strict flooded-veg seeds (3-class random walker)
    "knn_k": 10, "knn_radius": 10, "knn_stride": 2,  # structural change: k nearest in a 21x21 window
}


def _free():
    import gc
    gc.collect()
    if torch.backends.mps.is_available():
        torch.mps.empty_cache()


def device():
    return torch.device("mps" if torch.backends.mps.is_available() else "cpu")


# =============================================================================================
# 1. Pixel graph construction
# =============================================================================================
def shift(x: torch.Tensor, dy: int, dx: int) -> torch.Tensor:
    """y[..., i, j] = x[..., i+dy, j+dx], zero outside the frame (shifted-tensor neighbour access)."""
    H, W = x.shape[-2:]
    r = max(abs(dy), abs(dx))
    if r == 0:
        return x
    xp = F.pad(x, (r, r, r, r))
    return xp[..., r + dy:r + dy + H, r + dx:r + dx + W]


def grid_weights(f: torch.Tensor, valid: torch.Tensor, offs=OFFS8, sigma: float | None = None,
                 sigma_scale: float = 1.0):
    r"""Edge weights of the pixel graph, one plane per neighbour offset.

    f [B,F,H,W] features, valid [B,H,W]. For offset d and pixel i with neighbour j = i + d:
        w_ij = exp(-||f_i - f_j||^2 / (2 sigma^2))   if both valid, else 0.
    sigma = sigma_scale * median over valid edges of ||f_i - f_j|| (unless given).
    Returns (w [B,K,H,W], sigma). Symmetric by construction: w_d(i) = w_{-d}(i+d).
    """
    vf = valid.to(f.dtype)
    d2, ev = [], []
    for dy, dx in offs:
        d2.append(((f - shift(f, dy, dx)) ** 2).sum(1))
        ev.append(vf * shift(vf, dy, dx))
    d2, ev = torch.stack(d2, 1), torch.stack(ev, 1)
    if sigma is None:
        d = d2[ev > 0].sqrt()
        d = d[:: max(1, d.numel() // 2_000_000)]
        sigma = sigma_scale * float(d.float().cpu().median())
    sigma = max(sigma, 1e-6)
    return torch.exp(-d2 / (2 * sigma ** 2)) * ev, sigma


def laplacian_apply(w: torch.Tensor, x: torch.Tensor, offs=OFFS8) -> torch.Tensor:
    r"""Matrix-free combinatorial Laplacian: (L x)_i = d_i x_i - \sum_j w_ij x_j, d_i = \sum_j w_ij.

    w [B,K,H,W], x [B,H,W]."""
    out = w.sum(1) * x
    for k, (dy, dx) in enumerate(offs):
        out = out - w[:, k] * shift(x, dy, dx)
    return out


def to_sparse(w: torch.Tensor, valid: torch.Tensor, offs=OFFS8, extra=None):
    """Explicit sparse adjacency W [N,N] (float64, CPU) over valid nodes of one image.

    w [K,H,W], valid [H,W]. extra = optional (rows, cols, vals) in node numbering (non-local edges,
    symmetrised here). Returns (W sparse COO, node_index [H,W] long with -1 for invalid)."""
    H, Wd = valid.shape
    valid = valid.cpu()
    w = w.cpu().double()
    idx = torch.full((H, Wd), -1, dtype=torch.long)
    idx[valid] = torch.arange(int(valid.sum()))
    rows, cols, vals = [], [], []
    for k, (dy, dx) in enumerate(offs):
        nb = shift(idx[None].double() + 1, dy, dx)[0].long() - 1  # neighbour node id, -1 if none
        m = (idx >= 0) & (nb >= 0) & (w[k] > 0)
        rows.append(idx[m]); cols.append(nb[m]); vals.append(w[k][m])
    if extra is not None:
        r, c, v = extra
        rows += [r, c]; cols += [c, r]; vals += [v.double(), v.double()]
    n = int(valid.sum())
    Wm = torch.sparse_coo_tensor(torch.stack([torch.cat(rows), torch.cat(cols)]), torch.cat(vals), (n, n))
    return Wm.coalesce(), idx


def dense_laplacian(Wm: torch.Tensor, normalized: bool = False) -> torch.Tensor:
    r"""L = D - W, or L_sym = I - D^{-1/2} W D^{-1/2} (dense; for tests and small graphs)."""
    Wd = Wm.to_dense() if Wm.is_sparse else Wm
    d = Wd.sum(1)
    if not normalized:
        return torch.diag(d) - Wd
    s = d.clamp_min(1e-12).rsqrt()
    return torch.eye(Wd.shape[0], dtype=Wd.dtype) - s[:, None] * Wd * s[None]


def nonlocal_knn_edges(feat: torch.Tensor, k: int = 5, n_cand: int = 64, sigma: float = 1.0, seed: int = 0):
    r"""Approximate non-local kNN edges in feature space by random sampling.

    For every node, draw n_cand random nodes, keep the k with the smallest ||f_i - f_j|| and weight
    them exp(-||f_i - f_j||^2 / 2 sigma^2). feat [N,F]. Returns (rows, cols, vals).
    What they buy: separate water bodies (or separate patches of the same land cover) are joined
    in the graph, so spectral clustering puts them in one cluster instead of splitting by location.
    """
    g = torch.Generator().manual_seed(seed)
    N = feat.shape[0]
    cand = torch.randint(N, (N, n_cand), generator=g)
    vals, cols = [], []
    for i in range(0, N, 8192):  # chunked to bound memory
        c = cand[i:i + 8192]
        d2 = ((feat[i:i + 8192, None, :] - feat[c]) ** 2).sum(-1)
        d2[c == torch.arange(i, i + c.shape[0])[:, None]] = float("inf")
        v, j = d2.topk(k, dim=1, largest=False)
        vals.append(v); cols.append(c.gather(1, j))
    val = torch.cat(vals)
    rows = torch.arange(N)[:, None].expand(N, k).reshape(-1)
    return rows, torch.cat(cols).reshape(-1), torch.exp(-val.reshape(-1) / (2 * sigma ** 2))


def season_median(L: torch.Tensor, valid: torch.Tensor) -> torch.Tensor:
    """Per-pixel median over valid dates of each channel -> [2,H,W]."""
    med = torch.where(valid[:, None], L, torch.full_like(L, float("nan"))).nanmedian(0).values
    return torch.nan_to_num(med, 0.0)


def season_features(L: torch.Tensor, valid: torch.Tensor, beta: float, med=None) -> torch.Tensor:
    """Per-date edge features f_t = [HH_t, HV_t, beta*median_t HH, beta*median_t HV] -> [T,4,H,W]."""
    med = season_median(L, valid) if med is None else med
    return torch.cat([L, beta * med[None].expand_as(L)], 1)


# =============================================================================================
# 2. Graph-regularised segmentation (seeded)
# =============================================================================================
def make_seeds(L, valid, kn=GKNOBS, three_class=False, med=None):
    """Strict seed labels per date: 0 = water, 1 = land (or 0 water, 1 flooded veg, 2 land), -1 unknown.

    water: HH < seed_water_hh and HV < seed_water_hv, inside a connected strict-dark component of
           >= seed_min_px pixels (isolated dark specks and small dark patches are NOT seeds);
    land : HH > seed_land_hh on this date AND season-median HH > seed_land_hh ("bright and stable":
           a lake patch brightened by wind on one date is not a land seed);
    flooded veg (3 classes): HH rise over the mean of the first two dates > fv_hh_rise, HV rise
           < fv_hv_rise_max, and the pixel was not dark in the reference.
    """
    hh, hv = L[:, 0], L[:, 1]
    dark = (hh < kn["seed_water_hh"]) & (hv < kn["seed_water_hv"]) & valid
    water = ops.remove_small(dark, kn["seed_min_px"])
    med = season_median(L, valid) if med is None else med
    land = (hh > kn["seed_land_hh"]) & (med[0] > kn["seed_land_hh"])[None] & valid & ~water
    lab = torch.full(hh.shape, -1, dtype=torch.long, device=L.device)
    if three_class:
        ref, _ = ops.reference(L, valid, 2)
        fv = ((hh - ref[0]) > kn["fv_hh_rise"]) & ((hv - ref[1]) < kn["fv_hv_rise_max"]) & (ref[0] > 0.12) & land
        lab[land] = 2
        lab[fv] = 1
    else:
        lab[land] = 1
    lab[water] = 0
    return lab


def random_walker(w, labels, K, offs=OFFS8, tol=1e-4, max_iter=3000, verbose=False, prior=None, gamma=0.0):
    r"""Random-walker / label-propagation probabilities (Grady 2006), batched over dates.

    For each label k < K-1, with seeded nodes l and unknown nodes u, solve the Dirichlet problem
        L_uu x_u = -L_ul x_l,   x_l = [label(l) == k]
    by Jacobi-preconditioned conjugate gradients on the grid (matrix-free). x_u is the probability
    that a random walk from u, stepping to neighbours in proportion to w, first hits a seed of k.
    The last label gets 1 - sum of the others. Unknown pixels with no path to any seed keep x = 0,
    i.e. they go to the last label (land). w [B,Kn,H,W], labels [B,H,W] (-1 unknown).
    Random walker with a prior (Grady 2005), K = 2 only: (L_uu + gamma I) x_u = -L_ul x_l + gamma pi_u,
    pi [B,H,W] = prior probability of label 0; equivalent to an extra "phantom seed" attached to every
    pixel with weight gamma that is water with probability pi.
    Returns (prob [B,K,H,W], iterations, final relative residual).
    """
    B, _, H, W = w.shape
    unk = (labels < 0).to(w.dtype)
    deg = w.sum(1)
    use_prior = prior is not None and gamma > 0 and K == 2
    Minv = unk / (deg + (gamma if use_prior else 0.0)).clamp_min(1e-6)
    probs = []
    its, rel = 0, 0.0
    for k in range(K - 1):
        xl = (labels == k).to(w.dtype)
        b = torch.zeros_like(xl)
        for j, (dy, dx) in enumerate(offs):
            b = b + w[:, j] * shift(xl, dy, dx)
        if use_prior:
            b = b + gamma * prior.to(b.dtype)
        b = b * unk
        x = torch.zeros_like(b)
        r = b.clone()
        z = Minv * r
        p = z.clone()
        rz = (r * z).sum((-2, -1))
        bn = b.pow(2).sum((-2, -1)).sqrt().clamp_min(1e-12)
        for it in range(max_iter):
            Ap = laplacian_apply(w, p * unk, offs) * unk
            if use_prior:
                Ap = Ap + gamma * p * unk
            alpha = rz / (p * Ap).sum((-2, -1)).clamp_min(1e-30)
            x = x + alpha[:, None, None] * p
            r = r - alpha[:, None, None] * Ap
            if it % 50 == 49:
                rel = float((r.pow(2).sum((-2, -1)).sqrt() / bn).max())
                if verbose:
                    print(f"    CG label {k} it {it + 1}: rel residual {rel:.2e}")
                if rel < tol:
                    break
            z = Minv * r
            rz_new = (r * z).sum((-2, -1))
            p = z + (rz_new / rz.clamp_min(1e-30))[:, None, None] * p
            rz = rz_new
        its = max(its, it + 1)
        probs.append(torch.where(labels >= 0, xl, x.clamp(0, 1)))
    rest = (1 - torch.stack(probs, 1).sum(1)).clamp(0, 1)
    rest = torch.where(labels >= 0, (labels == K - 1).to(w.dtype), rest)
    return torch.stack(probs + [rest], 1), its, rel


def tv_segment(c, w4, valid, lam, fix_one=None, fix_zero=None, iters=300, floor=0.1):
    r"""Binary graph cut, convex relaxation solved exactly by primal-dual iterations.

    Energy  E(x) = \sum_i D_i(x_i) + \lambda \sum_{(i,j)} w_ij [x_i != x_j],  x in {0,1} (1 = water),
    written as  min_{u in [0,1]^N}  <c, u> + \lambda \sum_{(i,j)} (w_ij + floor) |u_i - u_j|,
    with c_i = D_i(1) - D_i(0). This is the total-variation / continuous max-flow relaxation: it is
    convex, and by the coarea formula thresholding its minimiser at any level in (0,1) gives a
    global minimiser of the binary energy (Chan, Esedoglu, Nikolova 2006). Solved with
    Chambolle-Pock (2011): dual p_e in [-lam (w_e+floor), lam (w_e+floor)] on the 4-neighbour
    edges (right, down), step sizes tau = sigma = 0.99 / sqrt(8 (1+floor)^2).
    c [B,H,W]; w4 [B,2,H,W] weights to the right and down neighbours. fix_one/fix_zero: hard seeds.
    """
    vf = valid.to(c.dtype)
    wr = (w4[:, 0] + floor) * vf * shift(vf, 0, 1)
    wd = (w4[:, 1] + floor) * vf * shift(vf, 1, 0)
    L2 = 8 * (1 + floor) ** 2
    tau = sig = 0.99 / math.sqrt(L2)
    u = (c < 0).to(c.dtype) * vf
    ub = u.clone()
    pr = torch.zeros_like(u)
    pd = torch.zeros_like(u)
    for _ in range(iters):
        pr = (pr + sig * (shift(ub, 0, 1) - ub)).clamp(-1, 1) * (lam * wr > 0)
        pd = (pd + sig * (shift(ub, 1, 0) - ub)).clamp(-1, 1) * (lam * wd > 0)
        # K^T p with the weights folded in: p scaled to unit box, multiply by lam*w
        qr, qd = lam * wr * pr, lam * wd * pd
        kt = -qr + shift(qr, 0, -1) - qd + shift(qd, -1, 0)
        un = (u - tau * (kt + c)).clamp(0, 1) * vf
        if fix_one is not None:
            un = torch.where(fix_one, torch.ones_like(un), un)
        if fix_zero is not None:
            un = torch.where(fix_zero, torch.zeros_like(un), un)
        ub = 2 * un - u
        u = un
    return u


def cut_energy(x, c, w4, valid, lam, floor=0.1):
    """Binary energy sum_i c_i x_i + lam sum (w+floor) [x_i != x_j] (for checking the relaxation)."""
    x = x.to(c.dtype)
    vf = valid.to(c.dtype)
    wr = (w4[:, 0] + floor) * vf * shift(vf, 0, 1)
    wd = (w4[:, 1] + floor) * vf * shift(vf, 1, 0)
    return ((c * x).sum((-2, -1)) + lam * ((wr * (shift(x, 0, 1) - x).abs()).sum((-2, -1))
                                         + (wd * (shift(x, 1, 0) - x).abs()).sum((-2, -1))))


def brightness_unary(L, kn=None):
    """c = D(1) - D(0) from brightness alone: (HH - 0.12)/0.06 clipped to [-1, 1] (negative = water)."""
    kn = kn or ops.KNOBS["preview"]
    return ((L[:, 0] - kn["water_hh_weak"]) / 0.06).clamp(-1, 1)


def dark_frequency(L, valid, kn=GKNOBS):
    """Season prior pi: share of valid dates on which a pixel is strictly dark (HH < seed_water_hh, HV < seed_water_hv)."""
    dark = (L[:, 0] < kn["seed_water_hh"]) & (L[:, 1] < kn["seed_water_hv"]) & valid
    return dark.sum(0).float() / valid.sum(0).clamp_min(1)


def refine_water(L, valid, water_init=None, kn=None, dev=None, three_class=False, verbose=False, med=None,
                 season_L=None, season_valid=None, chunk=5):
    """Graph refinement of per-date water, callable after ops.run_pipeline.

    L [T,2,H,W], valid [T,H,W]. Steps: season-aware edge weights (family 1); strict seeds; random
    walker probability p_water (2a); TV graph cut with unary c = 1 - 2 p_water (2b) -> water.
    With three_class=True also returns 3-class random-walker probabilities (water, flooded veg, land).
    water_init (e.g. ops water) is only used to report what changed ("added", "removed").
    med, season_L, season_valid: pass the full season when L holds only some of its dates
    (season median for features and land seeds; dark frequency for the random-walker prior).
    """
    kn = dict(GKNOBS, **(kn or {}))
    dev = dev or device()
    T = L.shape[0]
    if T > chunk:  # bound memory: process dates in chunks, season statistics from the whole stack
        med = season_median(L.to(dev).float(), valid.to(dev)) if med is None else med
        sL = L if season_L is None else season_L
        sv = valid if season_valid is None else season_valid
        parts = [refine_water(L[i:i + chunk], valid[i:i + chunk], None if water_init is None else water_init[i:i + chunk],
                              kn, dev, three_class, verbose, med, sL, sv, chunk) for i in range(0, T, chunk)]
        out = {}
        for k0, v0 in parts[0].items():
            if torch.is_tensor(v0) and v0.dim() >= 3 and k0 != "prior":
                out[k0] = torch.cat([p_[k0] for p_ in parts])
            elif k0 in ("cg_iters",):
                out[k0] = max(p_[k0] for p_ in parts)
            elif k0 in ("cg_rel_residual",):
                out[k0] = max(p_[k0] for p_ in parts)
            elif k0 == "seconds":
                out[k0] = sum(p_[k0] for p_ in parts)
            else:
                out[k0] = v0
        return out
    L, valid = L.to(dev).float(), valid.to(dev)
    t0 = time.time()
    med = season_median(L, valid) if med is None else med.to(dev)
    f = season_features(L, valid, kn["season_beta"], med)
    w, sigma = grid_weights(f, valid, OFFS8, sigma_scale=kn["sigma_scale"])
    seeds = make_seeds(L, valid, kn, med=med)
    prior = dark_frequency(L if season_L is None else season_L.to(dev), valid if season_valid is None else season_valid.to(dev), kn)
    prob, its, rel = random_walker(w, seeds, 2, OFFS8, kn["rw_tol"], kn["rw_iters"], verbose,
                                   prior=prior[None].expand_as(seeds), gamma=kn["rw_prior_gamma"])
    p = prob[:, 0] * valid
    c = (1 - 2 * p)
    u = tv_segment(c, w[:, :2], valid, kn["tv_lambda"], fix_one=seeds == 0, fix_zero=seeds == 1,
                   iters=kn["tv_iters"], floor=kn["tv_floor"])
    water = (u > 0.5) & valid
    del u, c, prob
    out = {"p_water": p, "water": water, "seeds": seeds, "prior": prior, "sigma": sigma, "cg_iters": its, "cg_rel_residual": rel,
           "seconds": time.time() - t0}
    if three_class:
        s3 = make_seeds(L, valid, kn, three_class=True, med=med)
        p3, _, _ = random_walker(w, s3, 3, OFFS8, kn["rw_tol"], kn["rw_iters"])
        out["p3"] = p3 * valid[:, None]
        out["seeds3"] = s3
    if water_init is not None:
        wi = water_init.to(dev)
        out["added"] = water & ~wi
        out["removed"] = wi & ~water
    return out


# =============================================================================================
# 3. Spectral methods on the graph
# =============================================================================================
def lanczos_smallest(matvec, n, k, m=300, seed=0, dtype=torch.float64):
    r"""k smallest eigenpairs of a symmetric operator by Lanczos with full re-orthogonalisation.

    Builds the Krylov basis V_m (m steps), T_m = V_m^T A V_m tridiagonal, eigh(T_m) -> Ritz pairs.
    Returns (eigenvalues [k], eigenvectors [n,k], residual norms ||A v - theta v|| [k]).
    """
    g = torch.Generator().manual_seed(seed)
    m = min(m, n)
    V = torch.zeros(m + 1, n, dtype=dtype)
    v = torch.randn(n, generator=g, dtype=dtype)
    V[0] = v / v.norm()
    alpha = torch.zeros(m, dtype=dtype)
    beta = torch.zeros(m, dtype=dtype)
    mm = m
    for j in range(m):
        wv = matvec(V[j])
        alpha[j] = wv @ V[j]
        wv = wv - alpha[j] * V[j] - (beta[j - 1] * V[j - 1] if j > 0 else 0)
        for _ in range(2):  # full re-orthogonalisation, twice ("twice is enough")
            wv = wv - V[:j + 1].T @ (V[:j + 1] @ wv)
        beta[j] = wv.norm()
        if beta[j] < 1e-10:
            mm = j + 1
            break
        V[j + 1] = wv / beta[j]
    T = torch.diag(alpha[:mm]) + torch.diag(beta[:mm - 1], 1) + torch.diag(beta[:mm - 1], -1)
    th, S = torch.linalg.eigh(T)
    vecs = V[:mm].T @ S[:, :k]
    vals = th[:k]
    res = torch.stack([(matvec(vecs[:, i]) - vals[i] * vecs[:, i]).norm() for i in range(k)])
    return vals, vecs, res


def sym_laplacian_matvec(Wm):
    r"""x -> L_sym x = x - D^{-1/2} W D^{-1/2} x for sparse W (CPU float64)."""
    d = torch.sparse.sum(Wm, 1).to_dense()
    s = d.clamp_min(1e-12).rsqrt()

    def mv(x):
        return x - s * torch.sparse.mm(Wm, (s * x)[:, None])[:, 0]
    return mv, d


def downsample(L, valid, fct):
    """Block-average L [T,2,H,W] by fct (only blocks fully valid on all dates stay valid)."""
    if fct == 1:
        return L, valid.all(0)
    T, C, H, W = L.shape
    v = valid.all(0).float()[None, None]
    Ld = F.avg_pool2d(L.reshape(1, T * C, H, W) * v, fct, fct) / F.avg_pool2d(v, fct, fct).clamp_min(1e-6)
    vd = F.avg_pool2d(v, fct, fct)[0, 0] > 0.999
    return Ld.reshape(T, C, H // fct, W // fct) * vd, vd


def spectral_embed(L, valid, fct, k=8, nonlocal_k=0, m=300, sigma_scale=1.0):
    r"""Lowest k eigenvectors of L_sym = I - D^{-1/2} W D^{-1/2} on the reduced-resolution grid graph.

    Nodes: fct x fct blocks valid on all dates. Features: the whole 10-date HH+HV series.
    Edges: 8-neighbours with w = exp(-||f_i-f_j||^2 / 2 sigma^2), plus optional non-local kNN edges.
    Returns dict(vals, vecs [N,k], res, node_index [h,w], d, sigma, feat [N,F], seconds).
    """
    t0 = time.time()
    Ld, vd = downsample(L.cpu().float(), valid.cpu(), fct)
    T, C, h, w = Ld.shape
    f = Ld.reshape(1, T * C, h, w)
    wg, sigma = grid_weights(f, vd[None], OFFS8, sigma_scale=sigma_scale)
    feat = f[0].reshape(T * C, -1).T[vd.flatten()]
    extra = None
    if nonlocal_k:
        extra = nonlocal_knn_edges(feat, nonlocal_k, 64, sigma)
    Wm, idx = to_sparse(wg[0], vd, OFFS8, extra)
    mv, d = sym_laplacian_matvec(Wm)
    vals, vecs, res = lanczos_smallest(mv, Wm.shape[0], k, m)
    return {"vals": vals, "vecs": vecs, "res": res, "idx": idx, "d": d, "sigma": sigma, "feat": feat,
            "W": Wm, "seconds": time.time() - t0, "fct": fct, "valid_lr": vd}


def spectral_clusters(emb, n_clusters=6, n_vec=None, seed=0):
    r"""Normalised spectral clustering (Ng, Jordan, Weiss 2002): rows of the eigenvector matrix U
    (k smallest of L_sym) normalised to unit length, then k-means (models.kmeans). Returns [N] labels."""
    from scripts.detect.models import kmeans
    U = emb["vecs"][:, :n_vec or emb["vecs"].shape[1]].float()
    U = U / U.norm(dim=1, keepdim=True).clamp_min(1e-12)
    _, lab = kmeans(U, n_clusters, iters=100, seed=seed)
    return lab


def ncut_value(Wm, d, side):
    """Normalised cut value cut(A,B)/vol(A) + cut(A,B)/vol(B) for a boolean partition."""
    i, j = Wm.indices()
    v = Wm.values()
    cut = float(v[side[i] & ~side[j]].sum())
    va, vb = float(d[side].sum()), float(d[~side].sum())
    return cut / max(va, 1e-12) + cut / max(vb, 1e-12)


def fiedler_cut(emb, n_thr=64):
    r"""Shi-Malik normalised-cut bipartition from the Fiedler vector.

    The generalised eigenvector y = D^{-1/2} v_2 (v_2 = 2nd smallest eigenvector of L_sym) is swept
    over n_thr quantile thresholds; the threshold with the smallest Ncut(A,B) is kept.
    The side whose nodes are darker in mean HH is called water. Returns (water [N] bool, ncut)."""
    y = emb["vecs"][:, 1] * emb["d"].clamp_min(1e-12).rsqrt()
    qs = torch.quantile(y.float(), torch.linspace(0.01, 0.99, n_thr)).double()
    best = (float("inf"), None)
    for q in qs:
        side = y > q
        if side.all() or (~side).all():
            continue
        nc = ncut_value(emb["W"], emb["d"], side)
        if nc < best[0]:
            best = (nc, side)
    side = best[1]
    T2 = emb["feat"].shape[1]
    hh = emb["feat"][:, 0:T2:2].mean(1)
    water = side if hh[side].mean() < hh[~side].mean() else ~side
    return water, best[0]


def to_grid(vals, idx, fill=0):
    out = torch.full(idx.shape, fill, dtype=vals.dtype)
    out[idx >= 0] = vals[idx[idx >= 0]]
    return out


def chebyshev_coeffs(fun, order, M=200):
    """c_k of fun(t) = c_0/2 + sum_k c_k T_k(t) on [-1,1] by Chebyshev-Gauss quadrature."""
    th = (torch.arange(M, dtype=torch.float64) + 0.5) * math.pi / M
    fv = fun(torch.cos(th))
    return torch.stack([2.0 / M * (fv * torch.cos(k * th)).sum() for k in range(order + 1)])


def heat_filter(x, w, s=2.0, order=20, offs=OFFS8):
    r"""Graph low-pass (heat-kernel) filter y = exp(-s L_rw) x, L_rw = I - D^{-1} W, by a Chebyshev
    polynomial of order `order` in L_rw - I (spectrum of L_rw lies in [0,2]).

    Diffusion along strong (similar) edges only: averages speckle within a surface, not across a
    shoreline. x [B,H,W], w [B,K,H,W] (weights from a guide image)."""
    c = chebyshev_coeffs(lambda t: torch.exp(-s * (t + 1)), order)
    deg = w.sum(1).clamp_min(1e-6)

    def A(v):  # (L_rw - I) v = -D^{-1} W v
        acc = torch.zeros_like(v)
        for k, (dy, dx) in enumerate(offs):
            acc = acc + w[:, k] * shift(v, dy, dx)
        return -acc / deg
    t0, t1 = x, A(x)
    y = 0.5 * float(c[0]) * t0 + float(c[1]) * t1
    for k in range(2, order + 1):
        t0, t1 = t1, 2 * A(t1) - t0
        y = y + float(c[k]) * t1
    return y


# =============================================================================================
# 4. Graph-based change detection between dates (structure consistency)
# =============================================================================================
def _knn_scan(fa, others, valid, k, radius, stride):
    r"""For each pixel i, the k most similar pixels j in a (2r+1)^2 window (stride `stride`) at date
    A (distance d_A(i,j) = ||f_A(i) - f_A(j)||). Returns the mean distance of i to ALL pixels
    of its window at A (local spread) and, for each image in `others`, the mean over the kNN j of d_X(i,j)."""
    offs = [(dy, dx) for dy in range(-radius, radius + 1, stride) for dx in range(-radius, radius + 1, stride)
            if (dy, dx) != (0, 0)]
    big = torch.tensor(1e6, device=fa.device)
    best = torch.full((k,) + fa.shape[-2:], 1e6, device=fa.device)
    bo = [torch.zeros_like(best) for _ in others]
    vf = valid.float()
    wsum = torch.zeros(fa.shape[-2:], device=fa.device)
    wcnt = torch.zeros_like(wsum)
    for dy, dx in offs:
        dA = (fa - shift(fa, dy, dx)).norm(dim=0)
        ok = (vf * shift(vf, dy, dx)) > 0
        wsum = wsum + dA * ok
        wcnt = wcnt + ok
        dA = torch.where(ok, dA, big)
        cat = torch.cat([best, dA[None]], 0)
        best, ix = cat.topk(k, dim=0, largest=False)
        for n, fo in enumerate(others):
            dO = (fo - shift(fo, dy, dx)).norm(dim=0)
            bo[n] = torch.cat([bo[n], dO[None]], 0).gather(0, ix)
    okk = best < 1e5
    cnt = okk.sum(0).clamp_min(1)
    return wsum / wcnt.clamp_min(1), [(b * okk).sum(0) / cnt for b in bo]


def structural_change(L, valid, k=10, radius=10, stride=2, pairs=None):
    r"""Structure-consistency change between dates (graph from A applied to B and vice versa).

    Graph at date A: each pixel i is linked to its k most similar pixels N_A(i) within a 21x21 window
    (non-local kNN on the HH,HV features). If nothing changed, they remain similar at date B.
        forward  r_{A->B}(i) = mean_{j in N_A(i)} ||f_B(i) - f_B(j)|| / s_B
        backward r_{B->A}(i) = mean_{j in N_B(i)} ||f_A(i) - f_A(j)|| / s_A
        s_X = median_i mean_{j in window(i)} ||f_X(i) - f_X(j)||   (typical distance between two pixels
              up to 10 px apart in image X: the score is ~0 if i's look-alikes still look alike and ~1
              if they have become as different as arbitrary neighbours)
        change(i) = (r_{A->B}(i) + r_{B->A}(i)) / 2
    A global gain g and offset o on one date (f -> g f + o) cancel exactly: offsets drop out of the
    differences, and the gain scales numerator and s_X alike.
    pairs: list of (a, b) date indices; default consecutive dates. Returns [P,H,W].
    """
    T = L.shape[0]
    pairs = pairs or [(t, t + 1) for t in range(T - 1)]
    need = sorted({a for a, _ in pairs} | {b for _, b in pairs})
    partners = {t: sorted({b for a, b in pairs if a == t} | {a for a, b in pairs if b == t}) for t in need}
    own, cross = {}, {}
    for t in need:
        v = valid[t]
        for u in partners[t]:
            v = v & valid[u]
        m_own, m_oth = _knn_scan(L[t], [L[u] for u in partners[t]], v, k, radius, stride)
        own[t] = m_own
        for u, m in zip(partners[t], m_oth):
            cross[(t, u)] = m  # graph from t, distances measured at u
    scale = {t: float(own[t][valid[t]].median()) for t in need}
    out = []
    for a, b in pairs:
        fwd = cross[(a, b)] / max(scale[b], 1e-9)
        bwd = cross[(b, a)] / max(scale[a], 1e-9)
        out.append(0.5 * (fwd + bwd) * (valid[a] & valid[b]))
    return torch.stack(out)


def robust_threshold(score, k=4.0):
    r"""Change threshold on the pooled structure-consistency scores: median + k * 1.4826 * MAD over
    valid pixels of all date pairs. (Otsu was tried first; the score is unimodal with a long tail,
    so Otsu split the bulk of unchanged pixels and flagged about half the land.)"""
    v = score[score > 0].float()
    v = v[:: max(1, v.numel() // 2_000_000)]
    med = v.median()
    mad = (v - med).abs().median()
    return float(med + k * 1.4826 * mad)


def first_change(cmap_bin):
    """First date index t (1..T-1) whose pair (t-1, t) is flagged; -1 if never. cmap_bin [T-1,H,W]."""
    P = cmap_bin.shape[0]
    idx = torch.arange(1, P + 1, device=cmap_bin.device)[:, None, None].expand_as(cmap_bin)
    big = torch.where(cmap_bin, idx, torch.full_like(idx, 10 ** 6)).min(0).values
    return torch.where(big < 10 ** 6, big, torch.full_like(big, -1)).to(torch.int16)


# =============================================================================================
# 5. Temporal spectral analysis
# =============================================================================================
def day_offsets(dates):
    d0 = dt.date.fromisoformat(dates[0])
    return torch.tensor([(dt.date.fromisoformat(d) - d0).days for d in dates], dtype=torch.float64)


def ls_periodogram(y, t, periods):
    r"""Least-squares (Lomb-Scargle-type) periodogram for unevenly sampled series.

    For each period P: fit y(t) = a + b cos(2 pi t/P) + c sin(2 pi t/P) by least squares (same
    design for all pixels, solved with one pseudo-inverse) and return the amplitude sqrt(b^2+c^2).
    Also returns a linear trend fit y = a + s (t - mean t), slope per 100 days.
    y [T,N] (all dates valid), t [T] days. Returns (amp [len(periods), N], slope [N]).
    """
    amps = []
    y = y.double()
    for P in periods:
        X = torch.stack([torch.ones_like(t), torch.cos(2 * math.pi * t / P), torch.sin(2 * math.pi * t / P)], 1)
        coef = torch.linalg.pinv(X) @ y
        amps.append(coef[1:].norm(dim=0))
    X = torch.stack([torch.ones_like(t), (t - t.mean()) / 100.0], 1)
    slope = (torch.linalg.pinv(X) @ y)[1]
    return torch.stack(amps).float(), slope.float()


def eof_modes(L, valid, r=3, n_sample=200_000, seed=0):
    r"""Empirical orthogonal functions (PCA) of the space-time matrix.

    X [N, 2T]: each pixel's HH and HV series minus the pixel's own temporal mean (anomalies).
    Temporal modes = top-r right singular vectors of a random subsample of rows (SVD); every
    pixel is then projected: scores S = X V_r, low-rank anomaly X_r = S V_r^T.
        amp(i)    = RMS of X_r(i)                   "how much this place changes"
        timing(i) = argmax_t ||X_r(i,t) - X_r(i,t-1)||  (HH,HV)   "when": date of the steepest step
    Returns dict(modes [r,2T], sv, explained, amp [H,W], timing [H,W] int16 (-1 invalid), scores).
    """
    T, C, H, W = L.shape
    v = valid.all(0)
    X = L.permute(2, 3, 0, 1).reshape(H * W, T * C)[v.flatten()].double()
    X = X - X.mean(1, keepdim=True)
    g = torch.Generator().manual_seed(seed)
    sub = X[torch.randperm(X.shape[0], generator=g)[:n_sample]]
    _, s, Vh = torch.linalg.svd(sub, full_matrices=False)
    Vr = Vh[:r]
    S = X @ Vr.T
    Xr = (S @ Vr).reshape(-1, T, C)
    amp = Xr.pow(2).mean((1, 2)).sqrt()
    step = (Xr[:, 1:] - Xr[:, :-1]).norm(dim=2)
    tim = step.argmax(1) + 1
    A = torch.zeros(H * W); A[v.flatten()] = amp.float()
    Tm = torch.full((H * W,), -1, dtype=torch.int16); Tm[v.flatten()] = tim.to(torch.int16)
    return {"modes": Vr.float(), "sv": s.float(), "explained": (s ** 2 / (s ** 2).sum()).float(),
            "amp": A.reshape(H, W), "timing": Tm.reshape(H, W), "scores": S.float()}


# =============================================================================================
# Evaluation helpers
# =============================================================================================
def _r(x, nd=3):
    return round(float(x), nd)


def largest_component(m):
    """Largest 8-connected component of each mask in m [N,H,W]."""
    lab = ops.label_components(m.cpu())
    out = torch.zeros_like(m, device="cpu")
    for i in range(m.shape[0]):
        l = lab[i].flatten()
        if l.max() == 0:
            continue
        cnt = torch.bincount(l); cnt[0] = 0
        out[i] = (lab[i] == int(cnt.argmax()))
    return out


def n_small_blobs(m, max_px=10):
    """Number of 8-connected components with fewer than max_px pixels, per mask in m [N,H,W]."""
    lab = ops.label_components(m.cpu())
    res = []
    for i in range(m.shape[0]):
        cnt = torch.bincount(lab[i].flatten())[1:]
        res.append(int(((cnt > 0) & (cnt < max_px)).sum()))
    return res


def avg_rank(x):
    """Average ranks (ties share the mean rank)."""
    order = torch.argsort(x)
    xs = x[order]
    _, inv, cnt = torch.unique_consecutive(xs, return_inverse=True, return_counts=True)
    ends = cnt.cumsum(0).double()
    mean_rank = ends - (cnt.double() - 1) / 2
    r = torch.empty_like(x, dtype=torch.float64)
    r[order] = mean_rank[inv]
    return r


def spearman(a, b):
    ra, rb = avg_rank(a.double()), avg_rank(b.double())
    ra, rb = ra - ra.mean(), rb - rb.mean()
    return float((ra * rb).sum() / (ra.norm() * rb.norm()).clamp_min(1e-12))


def water_metrics(water, valid, ref, km2, dates):
    """Project-standard checks of a per-date water stack [T,H,W] (CPU) against JRC GSW on our grid."""
    from scripts.detect import validate as V
    T = water.shape[0]
    dom = valid.all(0) & (ref["valid_frac"] >= 0.5)
    nw = (water & valid).sum(0)
    perm = (nw.float() / valid.sum(0).clamp_min(1) >= 0.9) & (nw > 0)
    R = {"water_km2": [_r(float(water[t].sum()) * km2, 1) for t in range(T)]}
    lc = largest_component(water)
    R["largest_body_km2"] = [_r(float(lc[t].sum()) * km2, 1) for t in range(T)]
    R["permanent"] = {}
    for name, refm in [("occ_ge75", ref["occ_ge75_frac"] >= 0.5), ("occ_ge90", ref["occ_ge90_frac"] >= 0.5),
                       ("seasonality12", ref["seas12_frac"] >= 0.5)]:
        c = V.confusion(perm, refm, dom, km2)
        R["permanent"][name] = {k: c[k] for k in ("iou", "precision", "recall", "ours_km2", "ref_km2")}
    b = V.boundary_analysis(perm, ref["occ_ge90_frac"] >= 0.5, dom, km2)
    R["permanent"]["occ_ge90"]["disagree_within_1px_share"] = b["disagree_within_1px_share"]
    late = water[-3:].any(0) & dom
    extm = ref["ext_frac"] >= 0.5
    R["late_share_inside_max_extent"] = _r(float((late & extm).sum()) / max(float(late.sum()), 1))
    ref_any = (ref["ext_frac"] > 0) & dom
    far = dom & ~ops.dilate(ref_any[None], 7)[0]
    R["false_far_km2_late"] = _r(float((late & far).sum()) * km2, 1)
    R["false_far_km2_per_date"] = [_r(float((water[t] & far).sum()) * km2, 1) for t in range(T)]
    fp = ext_frac = ref["ext_frac"]
    plain = dom & (ref["occ_mean"] > 0) & (ref["occ_mean"] < 75) & (fp >= 0.5)
    R["floodplain_px"] = int(plain.sum())
    R["spearman_occ_vs_ndates"] = _r(spearman(ref["occ_mean"][plain], nw[plain].float())) if plain.sum() > 50 else None
    blobs = n_small_blobs(water)
    R["small_blobs_lt10px_mean"] = _r(sum(blobs) / T, 1)
    R["small_blobs_lt10px"] = blobs
    return R


def iou(a, b):
    return float((a & b).sum()) / max(float((a | b).sum()), 1)


# =============================================================================================
# Driver
# =============================================================================================
def load_area_pipeline(area, dev_pipe=None, x_override=None):
    """Load the stack and run the current detector, with the terrain mask when it is cached."""
    from scripts.detect.stack import load_preview_stack
    from scripts.detect.terrain import cached_water_possible
    st = load_preview_stack(area)
    ok = cached_water_possible(area, tuple(st.valid.shape[1:]))
    x = st.x if x_override is None else x_override(st.x)
    r = ops.run_pipeline(x, st.valid, "preview", dev=dev_pipe or torch.device("cpu"), water_ok=ok)
    r["water_ok"] = ok
    return st, r


def run_area(area, figs=True):
    from scripts.detect import validate as V
    dev = device()
    out_dir = ROOT / f"data/processed/{area}/graph"
    out_dir.mkdir(parents=True, exist_ok=True)
    R = {"area": area}
    tm = {}
    t0 = time.time()
    st, r = load_area_pipeline(area)
    tm["load_and_ops_pipeline_s"] = _r(time.time() - t0, 1)
    L, valid, water_ops = r["L"], r["valid"], r["water"]
    T, _, H, W = L.shape
    km2, dates, bounds = st.km2_per_px, st.dates, st.bounds
    ref, _ = V.reference_on_grid(area, bounds, H, W)
    print(f"[{area}] {H}x{W}, T={T}; pipeline {tm['load_and_ops_pipeline_s']} s")

    # ---- 2. seeded segmentation -------------------------------------------------------------
    if dev.type == "mps":
        torch.mps.empty_cache()
    t0 = time.time()
    g = refine_water(L, valid, water_ops, dev=dev, three_class=True)
    if dev.type == "mps":
        torch.mps.synchronize()
    tm["refine_water_s"] = _r(time.time() - t0, 1)
    print(f"  refine_water: {tm['refine_water_s']} s, sigma {g['sigma']:.4f}, CG its {g['cg_iters']}, "
          f"rel res {g['cg_rel_residual']:.1e}")
    p_rw = g["p_water"].cpu()
    water_graph = g["water"].cpu()
    water_rw = (p_rw > 0.5) & valid
    seeds = g["seeds"].cpu()
    p3 = g["p3"].cpu()
    lab3 = p3.argmax(1)
    fv_rw = (lab3 == 1) & valid
    # brightness-only graph cut (no seeds, no random walker): what the pairwise term alone does
    f = season_features(L.to(dev), valid.to(dev), GKNOBS["season_beta"])
    w8, sigma = grid_weights(f, valid.to(dev), OFFS8)
    t0 = time.time()
    ub = tv_segment(brightness_unary(L.to(dev)), w8[:, :2], valid.to(dev), GKNOBS["tv_lambda"],
                    iters=GKNOBS["tv_iters"], floor=GKNOBS["tv_floor"])
    water_cut = ((ub > 0.5) & valid.to(dev)).cpu()
    tm["brightness_cut_s"] = _r(time.time() - t0, 1)
    # relaxation check: thresholded energy vs energy of the ops mask under the same energy
    c0 = brightness_unary(L.to(dev))
    e_cut = cut_energy(ub > 0.5, c0, w8[:, :2], valid.to(dev), GKNOBS["tv_lambda"], GKNOBS["tv_floor"])
    e_ops = cut_energy(water_ops.to(dev), c0, w8[:, :2], valid.to(dev), GKNOBS["tv_lambda"], GKNOBS["tv_floor"])
    R["tv_energy_check"] = {"cut_energy_mean": _r(e_cut.mean(), 1), "ops_mask_energy_mean": _r(e_ops.mean(), 1)}

    ok = r["water_ok"]
    water_ops_noterr = ops.water_masks(L, valid, ops.KNOBS["preview"]).cpu()
    methods = {"ops (current, terrain mask)": water_ops.cpu(), "ops, no terrain mask": water_ops_noterr,
               "random walker p>0.5": water_rw, "graph (RW + TV cut)": water_graph,
               "brightness graph cut": water_cut}
    if ok is not None:
        methods["graph + terrain mask"] = water_graph & ok.cpu()
    R["terrain_mask_available"] = ok is not None
    R["methods"] = {k: water_metrics(m, valid, ref, km2, dates) for k, m in methods.items()}
    R["fv_rw_km2"] = [_r(float(fv_rw[t].sum()) * km2, 1) for t in range(T)]
    R["fv_ops_km2"] = [_r(float(r["flooded_veg"][t].sum()) * km2, 1) for t in range(T)]
    R["sigma"] = _r(g["sigma"], 4)
    R["seed_km2"] = {"water": [_r(float((seeds[t] == 0).sum()) * km2, 1) for t in range(T)],
                     "land": [_r(float((seeds[t] == 1).sum()) * km2, 1) for t in range(T)]}

    med_full = season_median(L, valid)
    # sensitivity of the graph result to sigma, seed size, season weight (dates subset for speed)
    sens = {}
    tsub = [5, 6] if area == "sevan" else [0, T - 1]
    for name, kk in [("sigma x0.5", {"sigma_scale": 0.5}), ("sigma x2", {"sigma_scale": 2.0}),
                     ("seed_min_px 100", {"seed_min_px": 100}), ("seed_min_px 1000", {"seed_min_px": 1000}),
                     ("season_beta 0 (date only)", {"season_beta": 0.0}), ("tv_lambda 1.0", {"tv_lambda": 1.0}),
                     ("seed_land_hh 0.15", {"seed_land_hh": 0.15}), 
                     ("season prior gamma 0.01", {"rw_prior_gamma": 0.01}), ("season prior gamma 0.05", {"rw_prior_gamma": 0.05})]:
        gs = refine_water(L[tsub], valid[tsub], dev=dev, kn=kk, med=med_full, season_L=L, season_valid=valid)
        sens[name] = {dates[t]: _r(float(gs["water"][i].sum()) * km2, 1) for i, t in enumerate(tsub)}
    sens["default"] = {dates[t]: _r(float(water_graph[t].sum()) * km2, 1) for t in tsub}
    R["sensitivity_km2"] = sens

    # robustness: +-10 % gain on one date (applied to L after the pipeline, i.e. to the detector
    # itself), and added speckle (raw input, full pipeline re-run) -- Sevan and Ararat only
    rob = {}
    tg = 4
    for gname, gain in [("gain+10%", 1.1), ("gain-10%", 0.9)]:
        Lg = L.clone(); Lg[tg] = Lg[tg] * gain
        w_ops_g = ops.water_masks(Lg, valid, ops.KNOBS["preview"])[tg]
        if ok is not None:
            w_ops_g = w_ops_g & ok.to(w_ops_g.device)
        gg = refine_water(Lg[tg:tg + 1], valid[tg:tg + 1], dev=dev, med=med_full, season_L=L, season_valid=valid)["water"][0].cpu()
        rob[gname] = {"date": dates[tg],
                      "ops_km2": _r(float(w_ops_g.sum()) * km2, 1), "ops_base_km2": _r(float(water_ops[tg].sum()) * km2, 1),
                      "ops_iou_vs_base": _r(iou(w_ops_g, water_ops[tg].cpu()), 4),
                      "graph_km2": _r(float(gg.sum()) * km2, 1), "graph_base_km2": _r(float(water_graph[tg].sum()) * km2, 1),
                      "graph_iou_vs_base": _r(iou(gg, water_graph[tg]), 4)}
    if area != "tonle-sap":
        gen = torch.Generator().manual_seed(1)
        looks = 4.0

        def speckle(x):
            n = torch.distributions.Gamma(torch.full_like(x, looks), torch.full_like(x, looks)).sample()
            return (x * n).clamp(0, 1)
        torch.manual_seed(1)
        _, rs = load_area_pipeline(area, x_override=speckle)
        gs = refine_water(rs["L"], rs["valid"], dev=dev)["water"].cpu()
        rob["speckle_4looks_on_raw_input"] = {
            "ops_iou_vs_clean_all_dates": _r(iou(rs["water"].cpu(), water_ops.cpu()), 4),
            "graph_iou_vs_clean_all_dates": _r(iou(gs, water_graph), 4),
            "ops_small_blobs_mean": _r(sum(n_small_blobs(rs["water"])) / T, 1),
            "graph_small_blobs_mean": _r(sum(n_small_blobs(gs)) / T, 1)}
    R["robustness"] = rob

    del g, w8, f, ub, c0
    _free()
    # ---- 3. spectral --------------------------------------------------------------------------
    fct = 4 if area == "tonle-sap" else 2
    t0 = time.time()
    emb = spectral_embed(L, valid, fct, k=8, m=300)
    tm["spectral_embed_s"] = _r(time.time() - t0, 1)
    cl = spectral_clusters(emb, 6)
    emb_nl = spectral_embed(L, valid, fct, k=8, m=300, nonlocal_k=5)
    cl_nl = spectral_clusters(emb_nl, 6)
    fw, ncut = fiedler_cut(emb)
    idx = emb["idx"]
    up = lambda a: F.interpolate(a[None, None].float(), size=(H, W), mode="nearest")[0, 0]
    clusters = up(to_grid(cl.to(torch.long) + 1, idx)).to(torch.uint8) * valid.all(0)
    clusters_nl = up(to_grid(cl_nl.to(torch.long) + 1, idx)).to(torch.uint8) * valid.all(0)
    fied_water = (up(to_grid(fw.long(), idx)) > 0) & valid.all(0)
    eig_maps = torch.stack([to_grid(emb["vecs"][:, i].float(), idx) for i in range(6)])
    dom = valid.all(0) & (ref["valid_frac"] >= 0.5)
    perm_ops = (water_ops.sum(0) >= 9)
    R["spectral"] = {"nodes": int(emb["vecs"].shape[0]), "block": fct, "sigma": _r(emb["sigma"], 4),
                     "eigenvalues": [_r(x, 6) for x in emb["vals"]],
                     "ritz_residuals": [float(f"{x:.2e}") for x in emb["res"]],
                     "eigenvalues_nonlocal": [_r(x, 6) for x in emb_nl["vals"]],
                     "fiedler_ncut": _r(ncut, 5),
                     "fiedler_water_km2": _r(float(fied_water.sum()) * km2, 1),
                     "fiedler_vs_jrc_occ75": {k: v for k, v in V.confusion(fied_water, ref["occ_ge75_frac"] >= 0.5, dom, km2).items()
                                              if k in ("iou", "precision", "recall")},
                     "fiedler_vs_ops_permanent_iou": _r(iou(fied_water, perm_ops), 4),
                     "seconds": tm["spectral_embed_s"]}
    # which spectral cluster is "water": cluster purity vs ops permanent water
    cs = {}
    for c in range(1, 7):
        m = clusters == c
        if m.sum() == 0:
            continue
        cs[c] = {"km2": _r(float(m.sum()) * km2, 1), "share_ops_permanent": _r(float((m & perm_ops).sum()) / float(m.sum())),
                 "mean_jrc_occ": _r(ref["occ_mean"][m & dom].mean() if (m & dom).any() else float("nan"), 1)}
    R["spectral"]["clusters"] = cs

    # graph filter vs boxcar / Quegan (raw brightness, shoreline sharpness and noise)
    R["graph_filter"] = filter_comparison(st, r, ref, dev)

    _free()
    # ---- 4. structural change -----------------------------------------------------------------
    t0 = time.time()
    Ld = L.to(dev)
    vd = valid.to(dev)
    sc = structural_change(Ld, vd, GKNOBS["knn_k"], GKNOBS["knn_radius"], GKNOBS["knn_stride"]).cpu()
    tm["structural_change_s"] = _r(time.time() - t0, 1)
    thr = robust_threshold(sc)
    sc_bin = sc > thr
    fsc = first_change(sc_bin)
    diff = (L[1:] - L[:-1]).norm(dim=1)
    diff_bin = (diff > ops.KNOBS["preview"]["change_step_min"]) & valid[1:] & valid[:-1]
    cd = r["change_date"].cpu()
    R["change"] = change_metrics(sc_bin, diff_bin, fsc, cd, water_ops.cpu(), ref, valid, L, km2, dates, bounds)
    R["change"]["threshold_robust_log_score"] = _r(thr, 4)
    # gain robustness of change detectors (pair tg-1 -> tg, gain on date tg)
    R["change"]["gain_demo"] = gain_demo(L, valid, tg, thr, dev, km2, dates)

    del Ld, vd
    _free()
    # ---- 5. temporal spectral -----------------------------------------------------------------
    t0 = time.time()
    eof = eof_modes(L, valid, r=3)
    tday = day_offsets(dates)
    vall = valid.all(0)
    periods = [120.0, 60.0, 40.0, 30.0]
    amp_ls, slope = ls_periodogram(L[:, 0].reshape(T, -1)[:, vall.flatten()], tday, periods)
    tm["temporal_spectral_s"] = _r(time.time() - t0, 1)
    amp_ls_map = torch.zeros(len(periods), H * W); amp_ls_map[:, vall.flatten()] = amp_ls
    slope_map = torch.zeros(H * W); slope_map[vall.flatten()] = slope
    amp, timing = eof["amp"], eof["timing"]
    big = amp > float(torch.quantile(amp[vall][::7], 0.8))
    both = (cd >= 0) & big
    R["temporal"] = {"eof_explained_top5": [_r(x, 4) for x in eof["explained"][:5]],
                     "eof_modes": [[_r(x, 4) for x in m] for m in eof["modes"]],
                     "amp_p80_threshold": _r(float(torch.quantile(amp[vall][::7], 0.8)), 4),
                     "timing_vs_cusum_exact": _r(float((timing[both] == cd[both]).float().mean()) if both.any() else float("nan")),
                     "timing_vs_cusum_within1": _r(float(((timing[both] - cd[both]).abs() <= 1).float().mean()) if both.any() else float("nan")),
                     "cusum_changed_km2": _r(float((cd >= 0).sum()) * km2, 1), "amp_top20_km2": _r(float((big & vall).sum()) * km2, 1),
                     "overlap_cusum_and_amp_top20_km2": _r(float(both.sum()) * km2, 1),
                     "ls_periods_days": periods,
                     "ls_mean_amp_by_period": [_r(a.mean(), 4) for a in amp_ls],
                     "timing_km2_by_date": {dates[t]: _r(float(((timing == t) & big).sum()) * km2, 1) for t in range(1, T)}}
    R["times_s"] = tm
    R["peak_rss_mb"] = _r(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e6, 0)  # macOS: bytes

    products = {"dates": dates, "bounds": bounds, "km2_per_px": km2,
                "p_water_rw": p_rw.half(), "water_graph": water_graph, "water_km2": water_graph.sum((1, 2)).float() * km2,
                "p3_rw": p3.half(), "fv_rw": fv_rw, "water_brightness_cut": water_cut,
                "spectral_clusters": clusters, "spectral_clusters_nonlocal": clusters_nl, "fiedler_water": fied_water,
                "eigvecs": eig_maps.half(), "eigvals": emb["vals"].float(), "eig_block": fct,
                "change_graph": sc.half(), "change_threshold": thr, "first_struct_change": fsc,
                "amp": amp, "timing": timing, "eof_modes": eof["modes"], "amp_ls": amp_ls_map.reshape(-1, H, W).half(),
                "ls_periods_days": periods, "trend_per_100d": slope_map.reshape(H, W),
                "knobs": GKNOBS, "valid": valid.all(0)}
    torch.save(products, out_dir / "products.pt")
    print(f"  wrote {(out_dir / 'products.pt').relative_to(ROOT)}")
    return R, {"st": st, "r": r, "products": products, "ref": ref, "emb": emb, "eof": eof, "methods": methods,
               "sc_bin": sc_bin, "diff_bin": diff_bin}


def filter_comparison(st, r, ref, dev):
    """Raw HH brightness filtered four ways; edge sharpness across the JRC shoreline and noise
    on the lake interior and on stable bright land (forest proxy). Averages over all dates."""
    valid = r["valid"]
    x = st.x[:, 0] * valid  # raw HH brightness [T,H,W]
    T, H, W = x.shape
    box = ops.box_mean(st.x, valid, 3)[:, 0]
    que = ops.quegan_filter(st.x * valid[:, None], valid, 3)[:, 0]
    # guide for the graph weights: the season mean of the boxcar-filtered stack (speckle is
    # independent between dates, so the 10-date mean is a clean guide), plus the date itself
    guide = torch.cat([box[:, None], ops.box_mean(st.x, valid, 3)[:, 1:2]], 1)
    gmean = (guide * valid[:, None]).sum(0) / valid.sum(0).clamp_min(1)[None]
    f = torch.cat([guide, gmean[None].expand_as(guide)], 1)
    w, _ = grid_weights(f.to(dev), valid.to(dev), OFFS8)
    out = {}
    lake = ref["occ_ge90_frac"] >= 0.5
    dom = valid.all(0) & (ref["valid_frac"] >= 0.5)
    inner = ops.erode(lake[None], 11)[0] & dom
    in1 = lake & ~ops.erode(lake[None], 3)[0] & dom          # 1 px inside the shoreline
    out1 = ops.dilate(lake[None], 3)[0] & ~lake & dom        # 1 px outside
    hv_mean = (st.x[:, 1] * valid).sum(0) / valid.sum(0).clamp_min(1)
    sd = st.x[:, 1].std(0)
    far = ~ops.dilate((ref["ext_frac"] > 0)[None], 21)[0]
    vals_ok = dom & far
    forest = vals_ok & (hv_mean > torch.quantile(hv_mean[vals_ok][::5], 0.8)) & (sd < torch.quantile(sd[vals_ok][::5], 0.5))
    for name, y in [("raw", x), ("boxcar 3x3", box), ("Quegan 3x3 (ops)", que)] + \
            [(f"graph heat s={s}", heat_filter(x.to(dev), w, s=s).cpu()) for s in (2.0, 5.0)]:
        y = y.cpu()
        # sharpness: mean step across the shoreline (outside - inside) / (land level - water level)
        step = (y[:, out1].mean(1) - y[:, in1].mean(1))
        full = (y[:, forest].mean(1) - y[:, inner].mean(1)) if forest.any() and inner.any() else torch.ones(T)
        d = {"shore_step_over_contrast": _r((step / full).mean(), 4)}
        if inner.any():
            d["lake_interior_sd"] = _r(torch.stack([y[t][inner].std() for t in range(T)]).mean(), 4)
        if forest.any():
            d["forest_cv"] = _r(torch.stack([y[t][forest].std() / y[t][forest].mean() for t in range(T)]).mean(), 4)
        out[name] = d
    out["_regions_px"] = {"lake_interior": int(inner.sum()), "shore_in": int(in1.sum()), "shore_out": int(out1.sum()),
                          "forest": int(forest.sum())}
    return out


def change_metrics(sc_bin, diff_bin, fsc, cd, water, ref, valid, L, km2, dates, bounds):
    T = valid.shape[0]
    vall = valid.all(0)
    lake_in = ops.erode((ref["occ_ge90_frac"] >= 0.5)[None], 11)[0] & vall & (water.sum(0) == T)
    ref_any = ref["ext_frac"] > 0
    hv_mean = L[:, 1].mean(0)
    sd = L[:, 0].std(0)
    far = vall & ~ops.dilate(ref_any[None], 21)[0] & (cd < 0)
    forest = far & (hv_mean > torch.quantile(hv_mean[far][::5], 0.8)) & (sd < torch.quantile(sd[far][::5], 0.5)) if far.any() else far
    out = {}
    for name, m in [("structural (graph)", sc_bin), ("difference |dL|>0.12", diff_bin)]:
        d = {"changed_km2_per_pair": [_r(float(m[p].sum()) * km2, 1) for p in range(T - 1)]}
        if lake_in.any():
            d["false_rate_lake_interior"] = _r(float(m[:, lake_in].float().mean()), 4)
        if forest.any():
            d["false_rate_stable_forest"] = _r(float(m[:, forest].float().mean()), 4)
        fc = first_change(m)
        both = (cd >= 0) & (fc >= 0)
        d["first_change_vs_cusum_exact"] = _r(float((fc[both] == cd[both]).float().mean()) if both.any() else float("nan"))
        d["first_change_vs_cusum_within1"] = _r(float(((fc[both] - cd[both]).abs() <= 1).float().mean()) if both.any() else float("nan"))
        d["cusum_pixels_also_flagged"] = _r(float(both.sum()) / max(float((cd >= 0).sum()), 1))
        out[name] = d
    out["regions_px"] = {"lake_interior": int(lake_in.sum()), "stable_forest": int(forest.sum())}
    # flood wave: pixels that become water (not water on date 0, water on >= 2 of the last 3 dates)
    newwater = vall & ~water[0] & (water[-3:].sum(0) >= 2)
    w, s, e, n = bounds
    H, W = vall.shape
    yy, xx = torch.meshgrid(torch.arange(H), torch.arange(W), indexing="ij")
    lon = w + (xx + 0.5) / W * (e - w)
    lat = n - (yy + 0.5) / H * (n - s)
    lake = ref["occ_ge90_frac"] >= 0.5
    dist = torch.full((H, W), 99.0)
    cur = lake.clone()
    for k in range(1, 61):  # chessboard distance (px) to the permanent lake, by repeated dilation
        nxt = ops.dilate(cur[None], 3)[0]
        dist[nxt & ~cur] = k
        cur = nxt
    wave = {}
    for name, fc in [("structural first change", first_change(sc_bin)), ("cusum change date", cd)]:
        rows = {}
        for t in range(1, T):
            m = newwater & (fc == t)
            if m.sum() < 20:
                continue
            rows[dates[t]] = {"km2": _r(float(m.sum()) * km2, 1), "lon": _r(lon[m].float().mean(), 3),
                              "lat": _r(lat[m].float().mean(), 3), "median_dist_to_lake_px": _r(dist[m].median(), 1)}
        wave[name] = rows
    out["flood_wave_newwater"] = wave
    out["newwater_km2"] = _r(float(newwater.sum()) * km2, 1)
    return out


def gain_demo(L, valid, tg, thr, dev, km2, dates, gain=1.1, offset=0.02):
    """Apply f -> gain*f + offset to date tg and compare change detectors on pair (tg-1, tg)."""
    Lg = L.clone()
    Lg[tg] = (Lg[tg] * gain + offset) * valid[tg][None]
    res = {"pair": [dates[tg - 1], dates[tg]], "gain": gain, "offset": offset}
    pair = [(tg - 1, tg)]
    s0 = structural_change(L.to(dev), valid.to(dev), pairs=pair).cpu()[0]
    s1 = structural_change(Lg.to(dev), valid.to(dev), pairs=pair).cpu()[0]
    d0 = (L[tg] - L[tg - 1]).norm(dim=0) > 0.12
    d1 = (Lg[tg] - Lg[tg - 1]).norm(dim=0) > 0.12
    v = valid[tg] & valid[tg - 1]
    c0 = ops.cusum_changepoint(L, valid, 0.12, 0.8, 5.0)[0]
    c1 = ops.cusum_changepoint(Lg, valid, 0.12, 0.8, 5.0)[0]
    res["structural_km2"] = [_r(float(((s0 > thr) & v).sum()) * km2, 1), _r(float(((s1 > thr) & v).sum()) * km2, 1)]
    res["structural_iou"] = _r(iou((s0 > thr) & v, (s1 > thr) & v), 4)
    res["structural_score_max_abs_diff"] = float(f"{float((s0 - s1).abs().max()):.2e}")
    res["difference_km2"] = [_r(float((d0 & v).sum()) * km2, 1), _r(float((d1 & v).sum()) * km2, 1)]
    res["difference_iou"] = _r(iou(d0 & v, d1 & v), 4)
    res["cusum_changed_km2"] = [_r(float((c0 >= 0).sum()) * km2, 1), _r(float((c1 >= 0).sum()) * km2, 1)]
    res["cusum_date_agreement"] = _r(float((c0 == c1)[v].float().mean()), 4)
    res["_maps"] = {"s0": s0 > thr, "s1": s1 > thr, "d0": d0 & v, "d1": d1 & v, "c0": c0, "c1": c1}
    return res


def main(areas):
    """One area at a time (memory): products, per-area figures and results.json entry; then the
    cross-area figures from the saved products."""
    for a in areas:
        R, C = run_area(a, figs=False)
        write_report({a: R}, {a: C}, multi=False)
        del R, C
        _free()
    summary_figures()


def summary_figures():
    res = json.loads((FIG_DIR / "results.json").read_text())["areas"]
    ctx = {}
    for a in AREAS:
        pth = ROOT / f"data/processed/{a}/graph/products.pt"
        if a in res and pth.exists():
            P = torch.load(pth, weights_only=False, map_location="cpu")
            ctx[a] = {"products": {k: P[k] for k in ("eigvecs", "spectral_clusters", "spectral_clusters_nonlocal",
                                                     "bounds", "dates")}}
            del P
    write_report({a: res[a] for a in ctx}, ctx, multi=True)



# =============================================================================================
# Figures and results.json
# =============================================================================================
FIG_DIR = ROOT / "docs/eda/graph"


def _save(fig, name):
    import matplotlib.pyplot as plt
    path = FIG_DIR / name
    fig.savefig(path, dpi=fig.dpi, bbox_inches="tight")
    plt.close(fig)
    print(f"  wrote {path.relative_to(ROOT)} ({path.stat().st_size / 1024:.0f} KB)")


def _ext(b):
    w, s, e, n = b
    return (w, e, s, n)


def _strip(obj):
    if isinstance(obj, dict):
        return {str(k): _strip(v) for k, v in obj.items() if not str(k).startswith("_")}
    if isinstance(obj, (list, tuple)):
        return [_strip(v) for v in obj]
    return obj


def write_report(allR, ctx, multi=False):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.colors import ListedColormap
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    have = list(ctx)

    # 1. eigenvectors
    rows = [a for a in ("sevan", "tonle-sap") if a in ctx]
    if rows and multi:
        fig, axs = plt.subplots(len(rows), 5, figsize=(15, 3.2 * len(rows)), dpi=70, squeeze=False)
        for i, a in enumerate(rows):
            E = ctx[a]["products"]["eigvecs"].float().numpy()
            ev = allR[a]["spectral"]["eigenvalues"]
            for j in range(5):
                ax = axs[i, j]
                v = E[j + 1] if j + 1 < E.shape[0] else E[j]
                lim = np.quantile(np.abs(v[v != 0]), 0.99) if (v != 0).any() else 1
                ax.imshow(v, cmap="RdBu_r", vmin=-lim, vmax=lim, interpolation="nearest")
                ax.set_title(f"{a}: eigenvector {j + 2}, lambda={ev[j + 1]:.4f}", fontsize=8)
                ax.set_xticks([]); ax.set_yticks([])
        fig.suptitle("Lowest eigenvectors of the normalised graph Laplacian (10-date HH+HV series as features; "
                     "Sevan 2x2 blocks, Tonle Sap 4x4 blocks). Vector 1 (constant on each component) omitted.", fontsize=9)
        _save(fig, "eigenvectors.png")

    # 2. spectral clusters
    if multi:
        _fig_clusters(allR, ctx, have)
    if multi:
        _fig_areas(allR, ctx, have)
        return
    _area_figs(allR, ctx, have)


def _fig_clusters(allR, ctx, have):
    import matplotlib.pyplot as plt
    from matplotlib.colors import ListedColormap
    fig, axs = plt.subplots(2, len(have), figsize=(5 * len(have), 9), dpi=70, squeeze=False)
    cmap = ListedColormap(["#ffffff", "#1b9e77", "#d95f02", "#7570b3", "#e7298a", "#66a61e", "#e6ab02"])
    for j, a in enumerate(have):
        P = ctx[a]["products"]
        for i, (key, t) in enumerate([("spectral_clusters", "grid graph"), ("spectral_clusters_nonlocal", "grid + non-local kNN edges")]):
            axs[i, j].imshow(P[key].numpy(), cmap=cmap, vmin=0, vmax=6, extent=_ext(P["bounds"]), interpolation="nearest")
            axs[i, j].set_title(f"{a}: 6 spectral clusters, {t}", fontsize=9)
    fig.suptitle("Normalised spectral clustering (k-means on 8 eigenvectors), reduced resolution; colours are arbitrary labels", fontsize=9)
    _save(fig, "spectral_clusters.png")


def _area_figs(allR, ctx, have):
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.colors import ListedColormap

    # 3. Sevan windy dates
    if "sevan" in ctx:
        C = ctx["sevan"]; P = C["products"]; L = C["r"]["L"]; wo = C["r"]["water"]
        dates = P["dates"]
        fig, axs = plt.subplots(2, 4, figsize=(16, 8.4), dpi=70)
        for i, t in enumerate([5, 6]):
            axs[i, 0].imshow(L[t, 0].numpy(), cmap="gray", vmin=0, vmax=0.5, extent=_ext(P["bounds"]))
            axs[i, 0].set_title(f"HH {dates[t]} (windy)", fontsize=9)
            for ax, m, ttl in [(axs[i, 1], wo[t], "current detector (hysteresis threshold)"),
                               (axs[i, 3], P["water_graph"][t], "graph: random walker + TV cut")]:
                img = np.full(m.shape + (3,), 0.85); img[~P["valid"].numpy()] = 0.6
                img[m.numpy()] = [0.1, 0.35, 0.9]
                ax.imshow(img, extent=_ext(P["bounds"]), interpolation="nearest")
                ax.set_title(f"{ttl}: {float(m.sum()) * P['km2_per_px']:.0f} km2", fontsize=9)
            im = axs[i, 2].imshow(P["p_water_rw"][t].float().numpy(), cmap="Blues", vmin=0, vmax=1, extent=_ext(P["bounds"]))
            axs[i, 2].set_title("random-walker P(water)", fontsize=9)
        fig.colorbar(im, ax=axs[:, 2], fraction=0.03)
        fig.suptitle("Lake Sevan on the two windy dates (true area 1,242 km2)", fontsize=10)
        _save(fig, "sevan_windy.png")

    # 4. Tonle Sap shoreline detail
    if "tonle-sap" in ctx:
        C = ctx["tonle-sap"]; P = C["products"]; L = C["r"]["L"]; wo = C["r"]["water"]
        t = len(P["dates"]) - 1
        r0, r1, c0, c1 = 620, 870, 480, 730  # south-east end of the lake and its shore
        fig, axs = plt.subplots(1, 4, figsize=(16, 4.6), dpi=70)
        axs[0].imshow(L[t, 0, r0:r1, c0:c1].numpy(), cmap="gray", vmin=0, vmax=0.6); axs[0].set_title(f"HH {P['dates'][t]}", fontsize=9)
        for ax, m, ttl in [(axs[1], wo[t], "current detector"), (axs[2], P["water_brightness_cut"][t], "brightness graph cut (no seeds)"),
                           (axs[3], P["water_graph"][t], "graph: random walker + TV cut")]:
            mm = m[r0:r1, c0:c1]
            ax.imshow(mm.numpy(), cmap=ListedColormap(["#e8e8e8", "#1f5bd6"]), vmin=0, vmax=1, interpolation="nearest")
            nb = n_small_blobs(m[None, r0:r1, c0:c1])[0]
            ax.set_title(f"{ttl}\n{nb} blobs < 10 px in this crop", fontsize=9)
        for ax in axs:
            ax.set_xticks([]); ax.set_yticks([])
        fig.suptitle(f"Tonle Sap, rows {r0}-{r1}, cols {c0}-{c1} (about 45 x 45 km), {P['dates'][t]}", fontsize=10)
        _save(fig, "tonle_shoreline.png")

    # 5. graph change vs cusum, and gain demo
    for a in [a for a in ("tonle-sap",) if a in ctx]:
        C = ctx[a]; P = C["products"]; cd = C["r"]["change_date"].cpu()
        T = len(P["dates"])
        cm = plt.get_cmap("viridis", T - 1)
        fig, axs = plt.subplots(2, 3, figsize=(16, 10), dpi=65)
        for ax, m, ttl in [(axs[0, 0], P["first_struct_change"], "first structural change (graph, consecutive pairs)"),
                           (axs[0, 1], cd, "existing CUSUM change date (one lasting step)")]:
            mm = np.ma.masked_less(m.numpy().astype(float), 1)
            im = ax.imshow(mm, cmap=cm, vmin=0.5, vmax=T - 0.5, extent=_ext(P["bounds"]), interpolation="nearest")
            ax.set_title(ttl, fontsize=9)
        cb = fig.colorbar(im, ax=axs[0, :2], fraction=0.02, ticks=range(1, T))
        cb.ax.set_yticklabels([d[5:] for d in P["dates"][1:]], fontsize=7)
        p = 4  # pair 25 Jul -> 18 Aug
        axs[0, 2].imshow(P["change_graph"][p - 1 if p - 1 >= 0 else 0].float().numpy(), cmap="magma", vmin=0, vmax=1.2, extent=_ext(P["bounds"]))
        axs[0, 2].set_title(f"structure-consistency score {P['dates'][p - 1]} -> {P['dates'][p]}", fontsize=9)
        gd = allR[a]["change"]["gain_demo"]; M = gd["_maps"] if "_maps" in gd else None
        if M is not None:
            for ax, (k0, k1, nm) in zip(axs[1], [("d0", "d1", "difference |dL| > 0.12"), ("s0", "s1", "structural (graph)")]):
                img = np.full(M[k0].shape + (3,), 0.92)
                a0, a1 = M[k0].numpy(), M[k1].numpy()
                img[a0 & a1] = [0.2, 0.2, 0.2]; img[a1 & ~a0] = [0.9, 0.2, 0.1]; img[a0 & ~a1] = [0.2, 0.5, 0.9]
                ax.imshow(img, extent=_ext(P["bounds"]), interpolation="nearest")
                ax.set_title(f"{nm}: {gd['pair'][0]}->{gd['pair'][1]}\nblack both, red only after gain x{gd['gain']}+{gd['offset']}, blue only before;"
                             f" IoU {gd['difference_iou' if k0 == 'd0' else 'structural_iou']:.2f}", fontsize=8)
            c0, c1 = M["c0"].numpy(), M["c1"].numpy()
            img = np.full(c0.shape + (3,), 0.92)
            img[(c0 >= 0) & (c0 == c1)] = [0.2, 0.2, 0.2]; img[(c0 != c1)] = [0.9, 0.2, 0.1]
            axs[1, 2].imshow(img, extent=_ext(P["bounds"]), interpolation="nearest")
            axs[1, 2].set_title(f"CUSUM change date with the gained date: red = date changed\n"
                                f"agreement {gd['cusum_date_agreement']:.3f}", fontsize=8)
        fig.suptitle(f"{a}: graph structural change vs existing change map (top); robustness to a global gain/offset on one date (bottom)", fontsize=10)
        _save(fig, "change_maps.png")

    # 6. graph filter comparison crop (Sevan)
    if "sevan" in ctx:
        C = ctx["sevan"]; st = C["st"]; r = C["r"]
        valid = r["valid"]; t = 5
        x = st.x[:, 0] * valid
        box = ops.box_mean(st.x, valid, 3)[:, 0]
        que = ops.quegan_filter(st.x * valid[:, None], valid, 3)[:, 0]
        guide = ops.box_mean(st.x, valid, 3)
        gmean = (guide * valid[:, None]).sum(0) / valid.sum(0).clamp_min(1)[None]
        f = torch.cat([guide, gmean[None].expand_as(guide)], 1)
        w, _ = grid_weights(f, valid, OFFS8)
        hk = heat_filter(x, w, s=5.0)
        r0, r1, c0, c1 = 60, 200, 30, 170
        fig, axs = plt.subplots(1, 4, figsize=(16, 4.4), dpi=70)
        gf = allR["sevan"]["graph_filter"]
        for ax, y, nm in [(axs[0], x, "raw"), (axs[1], box, "boxcar 3x3"), (axs[2], que, "Quegan 3x3 (ops)"), (axs[3], hk, "graph heat s=5.0")]:
            ax.imshow(y[t, r0:r1, c0:c1].numpy(), cmap="gray", vmin=0, vmax=0.6)
            d = gf[nm]
            ax.set_title(f"{nm}\nshore step {d['shore_step_over_contrast']:.2f}, lake sd {d.get('lake_interior_sd', float('nan')):.3f}, "
                         f"forest CV {d.get('forest_cv', float('nan')):.3f}", fontsize=8)
            ax.set_xticks([]); ax.set_yticks([])
        fig.suptitle(f"Lake Sevan north-west shore, HH {st.dates[t]}: speckle filters (numbers = mean over all dates)", fontsize=10)
        _save(fig, "graph_filter.png")

    # 7. amplitude / timing
    if "tonle-sap" in ctx:
        a = "tonle-sap"
        C = ctx[a]; P = C["products"]; T = len(P["dates"])
        fig, axs = plt.subplots(1, 3, figsize=(17, 5.2), dpi=65)
        amp = P["amp"].numpy()
        im = axs[0].imshow(amp, cmap="inferno", vmin=0, vmax=np.quantile(amp[amp > 0], 0.99), extent=_ext(P["bounds"]))
        fig.colorbar(im, ax=axs[0], fraction=0.04); axs[0].set_title("EOF amplitude (RMS of rank-3 anomaly)", fontsize=9)
        thr = allR[a]["temporal"]["amp_p80_threshold"]
        tim = np.ma.masked_where((P["timing"].numpy() < 1) | (amp <= thr), P["timing"].numpy().astype(float))
        cm = plt.get_cmap("viridis", T - 1)
        im = axs[1].imshow(tim, cmap=cm, vmin=0.5, vmax=T - 0.5, extent=_ext(P["bounds"]), interpolation="nearest")
        cb = fig.colorbar(im, ax=axs[1], fraction=0.04, ticks=range(1, T)); cb.ax.set_yticklabels([d[5:] for d in P["dates"][1:]], fontsize=7)
        axs[1].set_title("EOF timing: date of steepest step (top-20 % amplitude only)", fontsize=9)
        days = day_offsets(P["dates"]).numpy()
        modes = P["eof_modes"].numpy()
        ex = allR[a]["temporal"]["eof_explained_top5"]
        for i in range(modes.shape[0]):
            axs[2].plot(days, modes[i, 0::2], "o-", label=f"mode {i + 1} HH ({ex[i] * 100:.0f} %)")
            axs[2].plot(days, modes[i, 1::2], "s--", alpha=0.6, label=f"mode {i + 1} HV")
        axs[2].axvspan(38, 52, color="0.85"); axs[2].set_xlabel("days since first pass"); axs[2].legend(fontsize=7)
        axs[2].set_title("EOF temporal modes (grey: no-data gap)", fontsize=9)
        fig.suptitle(f"{a}: temporal principal components of the HH/HV anomalies", fontsize=10)
        _save(fig, "amp_timing.png")

    out = FIG_DIR / "results.json"
    old = json.loads(out.read_text()) if out.exists() else {"areas": {}}
    old["areas"].update(_strip(allR))
    old["generated"] = dt.datetime.now().isoformat(timespec="seconds")
    old["knobs"] = GKNOBS
    out.write_text(json.dumps(old, indent=1))
    print(f"  wrote {out.relative_to(ROOT)}")


def _fig_areas(allR, ctx, have):
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    fig, axs = plt.subplots(1, len(have), figsize=(6 * len(have), 4.4), dpi=75, squeeze=False)
    for ax, a in zip(axs[0], have):
        R = allR[a]; d = [dt.date.fromisoformat(x) for x in ctx[a]["products"]["dates"]]
        for (nm, M), st_ in zip(R["methods"].items(), ["o-", "o--", "s-", "^-", "x--", "d-"]):
            ax.plot(d, M["water_km2"], st_, label=nm, ms=4)
        if a == "sevan":
            ax.plot(d, R["methods"]["graph (RW + TV cut)"]["largest_body_km2"], "^:", color="C2", label="graph, largest body", ms=4)
            ax.plot(d, R["methods"]["ops (current, terrain mask)"]["largest_body_km2"], "o:", color="C0", label="ops, largest body", ms=4)
            ax.axhline(1242, color="k", lw=0.8, ls="--", label="published 1,242 km2")
        ax.axvspan(dt.date(2026, 7, 27), dt.date(2026, 8, 10), color="0.88", label="no passes")
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
        ax.set_title(a, fontsize=10); ax.set_ylabel("open water (km2)"); ax.grid(alpha=0.3)
        ax.legend(fontsize=6)
    fig.suptitle("Water area per pass, all methods", fontsize=10)
    _save(fig, "areas.png")



if __name__ == "__main__":
    if sys.argv[1:] == ["--summary"]:
        summary_figures()
    else:
        main(sys.argv[1:] or AREAS)
