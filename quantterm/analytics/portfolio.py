"""Portfolio construction: covariance estimation, optimisers, efficient frontier, risk decomposition."""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from .timeseries import AF


def ledoit_wolf(rets: pd.DataFrame) -> pd.DataFrame:
    """Ledoit-Wolf shrinkage towards constant-correlation target (annualised)."""
    X = rets.dropna().to_numpy()
    t, n = X.shape
    X = X - X.mean(axis=0)
    S = X.T @ X / t
    var = np.diag(S)
    std = np.sqrt(var)
    corr = S / np.outer(std, std)
    rbar = (corr.sum() - n) / (n * (n - 1)) if n > 1 else 0
    F = rbar * np.outer(std, std)
    np.fill_diagonal(F, var)
    # shrinkage intensity (Ledoit & Wolf 2004, simplified pi/rho/gamma estimate)
    Y = X**2
    pi_mat = Y.T @ Y / t - S**2
    pi_hat = pi_mat.sum()
    gamma = np.linalg.norm(S - F, "fro") ** 2
    rho = np.trace(pi_mat)
    kappa = (pi_hat - rho) / gamma if gamma > 0 else 0
    delta = float(np.clip(kappa / t, 0, 1))
    shrunk = delta * F + (1 - delta) * S
    return pd.DataFrame(shrunk * AF, index=rets.columns, columns=rets.columns)


def estimate(prices: pd.DataFrame, shrink: bool = True) -> tuple[pd.Series, pd.DataFrame, pd.DataFrame]:
    rets = prices.pct_change().dropna(how="all").dropna(axis=0)
    mu = rets.mean() * AF
    cov = ledoit_wolf(rets) if shrink else rets.cov() * AF
    return mu, cov, rets


def _port(w, mu, cov):
    ret = float(w @ mu)
    vol = float(np.sqrt(max(w @ cov @ w, 0)))
    return ret, vol


def optimize(mu: pd.Series, cov: pd.DataFrame, method: str = "max_sharpe", rf: float = 0.0,
             long_only: bool = True, max_weight: float = 1.0, target_return: float | None = None) -> pd.Series:
    n = len(mu)
    names = list(mu.index)
    m, C = mu.to_numpy(), cov.to_numpy()
    if method == "equal":
        return pd.Series(np.ones(n) / n, index=names)
    if method == "inverse_vol":
        iv = 1 / np.sqrt(np.diag(C))
        return pd.Series(iv / iv.sum(), index=names)

    lo = 0.0 if long_only else -max_weight
    bounds = [(lo, max_weight)] * n
    cons = [{"type": "eq", "fun": lambda w: w.sum() - 1}]
    x0 = np.ones(n) / n

    if method == "min_var":
        obj = lambda w: w @ C @ w  # noqa: E731
    elif method == "max_sharpe":
        obj = lambda w: -(w @ m - rf) / np.sqrt(max(w @ C @ w, 1e-12))  # noqa: E731
    elif method == "risk_parity":
        def obj(w):
            pv = w @ C @ w
            rc = w * (C @ w) / pv
            return ((rc - 1 / n) ** 2).sum() * 1e3
        bounds = [(1e-6, max_weight)] * n
    elif method == "target_return":
        obj = lambda w: w @ C @ w  # noqa: E731
        cons.append({"type": "eq", "fun": lambda w, t=target_return: w @ m - t})
    elif method == "max_diversification":
        sd = np.sqrt(np.diag(C))
        obj = lambda w: -(w @ sd) / np.sqrt(max(w @ C @ w, 1e-12))  # noqa: E731
    else:
        raise ValueError(f"unknown method {method}")

    res = minimize(obj, x0, method="SLSQP", bounds=bounds, constraints=cons, options={"maxiter": 500, "ftol": 1e-10})
    w = res.x
    w[np.abs(w) < 1e-6] = 0
    w = w / w.sum()
    return pd.Series(w, index=names)


def efficient_frontier(mu: pd.Series, cov: pd.DataFrame, n_points: int = 30, long_only: bool = True,
                       max_weight: float = 1.0) -> list[dict]:
    w_min = optimize(mu, cov, "min_var", long_only=long_only, max_weight=max_weight)
    r_min, _ = _port(w_min.to_numpy(), mu.to_numpy(), cov.to_numpy())
    r_max = float(mu.max()) if long_only else float(mu.max()) * 1.5
    pts = []
    for t in np.linspace(r_min, r_max, n_points):
        try:
            w = optimize(mu, cov, "target_return", long_only=long_only, max_weight=max_weight, target_return=t)
            r, v = _port(w.to_numpy(), mu.to_numpy(), cov.to_numpy())
            if abs(r - t) < 1e-3:
                pts.append({"ret": r, "vol": v})
        except Exception:  # noqa: BLE001
            continue
    return pts


def risk_contributions(w: pd.Series, cov: pd.DataFrame) -> pd.DataFrame:
    C = cov.loc[w.index, w.index].to_numpy()
    wv = w.to_numpy()
    pv = wv @ C @ wv
    mctr = C @ wv / np.sqrt(pv)
    ctr = wv * mctr
    return pd.DataFrame({"weight": wv, "mctr": mctr, "ctr": ctr, "pct": ctr / np.sqrt(pv)}, index=w.index)


def backtest_weights(prices: pd.DataFrame, w: pd.Series, rebalance: str = "ME") -> pd.Series:
    """Equity curve of a fixed-weight portfolio rebalanced at the given pandas frequency (None = daily)."""
    rets = prices[w.index].pct_change().fillna(0)
    if rebalance is None:
        return (1 + rets @ w).cumprod() * 100
    # rebalance on the last trading day of each period
    per = rets.index.to_period({"ME": "M", "QE": "Q", "YE": "Y", "W": "W"}.get(rebalance, "M"))
    is_last = np.r_[per[1:] != per[:-1], True]
    equity, value = [], 100.0
    holdings = w * value
    for (dt, r), last in zip(rets.iterrows(), is_last):
        holdings = holdings * (1 + r)
        value = holdings.sum()
        equity.append(value)
        if last:
            holdings = w * value
    return pd.Series(equity, index=rets.index)
