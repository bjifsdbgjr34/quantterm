"""Monte Carlo engines.

* Price-path simulation under several models (GBM, Student-t, bootstrap, Merton jump-diffusion, GARCH(1,1))
* Monte Carlo option pricing for path-dependent payoffs (Asian, barrier, lookback) with antithetic variates
* Multi-asset portfolio simulation with correlated block bootstrap and contributions/withdrawals
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
from scipy import stats as sps

from .options import bs_price

AF = 252
MODELS = ("gbm", "student_t", "bootstrap", "jump", "garch")


# --------------------------------------------------------------------------------------
# calibration
# --------------------------------------------------------------------------------------
def calibrate(log_rets: pd.Series) -> dict:
    r = log_rets.dropna().to_numpy()
    mu_d, sd_d = r.mean(), r.std(ddof=1)
    # Student-t degrees of freedom by MLE (location/scale free)
    try:
        df_t, _, _ = sps.t.fit(r)
        df_t = float(np.clip(df_t, 2.5, 50))
    except Exception:  # noqa: BLE001
        df_t = 5.0
    # crude jump estimate: moves beyond 3 sd
    jumps = r[np.abs(r - mu_d) > 3 * sd_d]
    lam = len(jumps) / len(r) * AF
    return {"mu": mu_d * AF, "sigma": sd_d * math.sqrt(AF), "t_df": df_t, "jump_lambda": lam,
            "jump_mean": float(jumps.mean()) if len(jumps) else 0.0, "jump_std": float(jumps.std()) if len(jumps) > 1 else 0.0,
            "n": len(r)}


# --------------------------------------------------------------------------------------
# path simulation
# --------------------------------------------------------------------------------------
def simulate_paths(S0: float, days: int, n_paths: int, model: str = "gbm", mu: float = 0.07, sigma: float = 0.2,
                   hist: pd.Series | None = None, params: dict | None = None, seed: int | None = None,
                   block: int = 5) -> np.ndarray:
    """Return array (n_paths, days+1) of simulated prices. mu/sigma are annual (log-return drift ~ mu - s^2/2)."""
    rng = np.random.default_rng(seed)
    dt = 1 / AF
    p = params or {}
    if model == "gbm":
        z = rng.standard_normal((n_paths, days))
        lr = (mu - 0.5 * sigma**2) * dt + sigma * math.sqrt(dt) * z
    elif model == "student_t":
        nu = p.get("t_df", 5.0)
        z = rng.standard_t(nu, (n_paths, days)) * math.sqrt((nu - 2) / nu)  # unit variance
        lr = (mu - 0.5 * sigma**2) * dt + sigma * math.sqrt(dt) * z
    elif model == "bootstrap":
        if hist is None or len(hist.dropna()) < 30:
            raise ValueError("bootstrap needs historical returns")
        h = hist.dropna().to_numpy()
        h = h - h.mean() + (mu - 0.5 * sigma**2) * dt  # recentre to requested drift
        n_blocks = math.ceil(days / block)
        starts = rng.integers(0, len(h) - block, (n_paths, n_blocks))
        idx = (starts[:, :, None] + np.arange(block)).reshape(n_paths, -1)[:, :days]
        lr = h[idx]
    elif model == "jump":
        lam, jm, js = p.get("jump_lambda", 1.0), p.get("jump_mean", -0.03), max(p.get("jump_std", 0.03), 1e-4)
        k = math.exp(jm + 0.5 * js**2) - 1
        diff_sigma = math.sqrt(max(sigma**2 - lam * (jm**2 + js**2), (0.5 * sigma) ** 2))
        z = rng.standard_normal((n_paths, days))
        nj = rng.poisson(lam * dt, (n_paths, days))
        jumps = nj * jm + np.sqrt(nj) * js * rng.standard_normal((n_paths, days))
        lr = (mu - lam * k - 0.5 * diff_sigma**2) * dt + diff_sigma * math.sqrt(dt) * z + jumps
    elif model == "garch":
        omega, alpha, beta = p.get("omega"), p.get("alpha", 0.08), p.get("beta", 0.9)
        var_d = (sigma**2) / AF
        if omega is None:
            omega = var_d * (1 - alpha - beta)
        h = np.full(n_paths, p.get("last_var", var_d))
        eps_prev = np.zeros(n_paths) if p.get("last_eps") is None else np.full(n_paths, p["last_eps"])
        lr = np.empty((n_paths, days))
        for t in range(days):
            h = omega + alpha * eps_prev**2 + beta * h
            eps = np.sqrt(h) * rng.standard_normal(n_paths)
            lr[:, t] = mu * dt - 0.5 * h + eps
            eps_prev = eps
    else:
        raise ValueError(f"unknown model {model}; choose from {MODELS}")
    paths = np.empty((n_paths, days + 1))
    paths[:, 0] = S0
    paths[:, 1:] = S0 * np.exp(np.cumsum(lr, axis=1))
    return paths


def summarize_paths(paths: np.ndarray, S0: float, up: float | None = None, down: float | None = None,
                    n_sample: int = 40) -> dict:
    pct = [5, 25, 50, 75, 95]
    fan = {f"p{q}": np.percentile(paths, q, axis=0) for q in pct}
    fan["mean"] = paths.mean(axis=0)
    term = paths[:, -1]
    ret = term / S0 - 1
    run_max = np.maximum.accumulate(paths, axis=1)
    mdd = (paths / run_max - 1).min(axis=1)
    hist, edges = np.histogram(ret * 100, bins=60)
    out = {
        "fan": fan, "sample": paths[: min(n_sample, len(paths))],
        "terminal": {"mean": term.mean(), "median": np.median(term), "std": term.std(),
                     "p1": np.percentile(term, 1), "p5": np.percentile(term, 5), "p95": np.percentile(term, 95),
                     "p99": np.percentile(term, 99)},
        "prob_up": float((term > S0).mean()), "exp_return": float(ret.mean()), "median_return": float(np.median(ret)),
        "var95": float(-np.percentile(ret, 5)), "cvar95": float(-ret[ret <= np.percentile(ret, 5)].mean()),
        "var99": float(-np.percentile(ret, 1)),
        "median_max_dd": float(np.median(mdd)), "p95_max_dd": float(np.percentile(mdd, 5)),
        "hist": {"x": ((edges[:-1] + edges[1:]) / 2), "y": hist},
    }
    if up:
        out["prob_touch_up"] = float((paths.max(axis=1) >= up).mean())
        out["prob_end_above_up"] = float((term >= up).mean())
    if down:
        out["prob_touch_down"] = float((paths.min(axis=1) <= down).mean())
        out["prob_end_below_down"] = float((term <= down).mean())
    return out


# --------------------------------------------------------------------------------------
# Monte Carlo option pricing
# --------------------------------------------------------------------------------------
OPTION_STYLES = ("european", "asian", "up_and_out", "down_and_out", "up_and_in", "down_and_in", "lookback_floating", "digital")


def mc_option(S0: float, K: float, T: float, r: float, sigma: float, kind: str = "call", style: str = "european",
              barrier: float | None = None, n_paths: int = 50_000, steps: int = 252, q: float = 0.0,
              antithetic: bool = True, seed: int | None = 7) -> dict:
    rng = np.random.default_rng(seed)
    steps = max(int(steps * T), 4) if style != "european" else 1
    dt = T / steps
    half = n_paths // 2 if antithetic else n_paths
    z = rng.standard_normal((half, steps))
    if antithetic:
        z = np.vstack([z, -z])
    lr = (r - q - 0.5 * sigma**2) * dt + sigma * math.sqrt(dt) * z
    paths = S0 * np.exp(np.cumsum(lr, axis=1))
    ST = paths[:, -1]
    intrinsic = (lambda s: np.maximum(s - K, 0)) if kind == "call" else (lambda s: np.maximum(K - s, 0))
    if style == "european":
        pay = intrinsic(ST)
    elif style == "asian":  # arithmetic average price
        pay = intrinsic(paths.mean(axis=1))
    elif style in ("up_and_out", "down_and_out", "up_and_in", "down_and_in"):
        if not barrier:
            raise ValueError("barrier option needs a barrier level")
        hit = (paths.max(axis=1) >= barrier) if style.startswith("up") else (paths.min(axis=1) <= barrier)
        pay = intrinsic(ST) * (~hit if style.endswith("out") else hit)
    elif style == "lookback_floating":
        pay = ST - paths.min(axis=1) if kind == "call" else paths.max(axis=1) - ST
    elif style == "digital":
        pay = ((ST > K) if kind == "call" else (ST < K)).astype(float)
    else:
        raise ValueError(f"unknown style {style}")
    disc = math.exp(-r * T) * pay
    if antithetic:  # pair the antithetic draws for a correct standard error
        pair = (disc[:half] + disc[half:]) / 2
        price, se = pair.mean(), pair.std(ddof=1) / math.sqrt(half)
        conv_src = pair
    else:
        price, se = disc.mean(), disc.std(ddof=1) / math.sqrt(n_paths)
        conv_src = disc
    marks = np.unique(np.geomspace(100, len(conv_src), 40).astype(int))
    csum = np.cumsum(conv_src)
    return {"price": float(price), "std_error": float(se), "ci95": [float(price - 1.96 * se), float(price + 1.96 * se)],
            "bs_european": bs_price(S0, K, T, r, sigma, kind, q), "paths": int(len(disc)), "steps": steps,
            "convergence": {"n": marks, "price": csum[marks - 1] / marks},
            "sample": paths[:30, :: max(1, steps // 120)], "prob_payoff": float((pay > 0).mean())}


# --------------------------------------------------------------------------------------
# portfolio simulation
# --------------------------------------------------------------------------------------
def portfolio_mc(rets: pd.DataFrame, weights: pd.Series, value: float, years: float = 5, n_paths: int = 5000,
                 contribution: float = 0.0, method: str = "bootstrap", rebalance: bool = True, goal: float | None = None,
                 seed: int | None = 11, block: int = 21) -> dict:
    """Simulate portfolio value. contribution = annual cash flow (negative = withdrawal), paid monthly."""
    rng = np.random.default_rng(seed)
    days = int(years * AF)
    R = rets[weights.index].dropna().to_numpy()
    w = weights.to_numpy()
    if method == "bootstrap":  # correlated block bootstrap keeps cross-asset and serial structure
        n_blocks = math.ceil(days / block)
        starts = rng.integers(0, len(R) - block, (n_paths, n_blocks))
        idx = (starts[:, :, None] + np.arange(block)).reshape(n_paths, -1)[:, :days]
        sim = R[idx]  # (paths, days, assets)
    else:  # multivariate normal
        mu, cov = R.mean(axis=0), np.cov(R.T)
        L = np.linalg.cholesky(cov + 1e-12 * np.eye(len(w)))
        sim = mu + rng.standard_normal((n_paths, days, len(w))) @ L.T
    if rebalance:
        port_r = sim @ w
    else:
        growth = np.cumprod(1 + sim, axis=1) * w
        tot = growth.sum(axis=2)
        port_r = np.diff(np.concatenate([np.ones((n_paths, 1)), tot], axis=1), axis=1) / np.concatenate(
            [np.ones((n_paths, 1)), tot[:, :-1]], axis=1)
    vals = np.empty((n_paths, days + 1))
    vals[:, 0] = value
    monthly_cf = contribution / 12
    v = np.full(n_paths, value, dtype=float)
    for t in range(days):
        v = v * (1 + port_r[:, t])
        if monthly_cf and (t + 1) % 21 == 0:
            v = np.maximum(v + monthly_cf, 0)
        vals[:, t + 1] = v
    step = max(1, days // 250)
    s = summarize_paths(vals[:, ::step], value, up=goal)
    s["t_years"] = np.arange(0, days + 1, step) / AF
    term = vals[:, -1]
    invested = value + contribution * years
    s["prob_loss"] = float((term < invested).mean())
    s["prob_ruin"] = float((term <= value * 0.01).mean())
    s["cagr_median"] = float((np.median(term) / value) ** (1 / years) - 1) if contribution == 0 else None
    s["invested"] = invested
    if goal:
        s["prob_goal"] = float((term >= goal).mean())
    return s
