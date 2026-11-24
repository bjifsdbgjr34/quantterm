"""Multi-asset / cross-sectional strategy backtests.

Weights are decided on the close of each rebalance bar t from information up to and including t,
and earn returns from bar t+1 onward (same convention as the single-asset engine). Costs are charged
on the sum of absolute weight changes, in basis points.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .risk import monthly_returns, perf_stats
from .timeseries import AF

STRATEGIES = {
    "xs_momentum": "Cross-sectional momentum: long the top N by trailing return (skip the last month)",
    "dual_momentum": "Rotation / dual momentum: top N by trailing return, but only if that return beats cash; else cash",
    "long_short": "Long/short: long top N, short bottom N by trailing return (dollar neutral)",
    "low_vol": "Low volatility: long the N least volatile assets",
    "inverse_vol": "Inverse-volatility weighted basket of all assets",
    "equal_weight": "Equal-weight basket, rebalanced",
}

REBAL = {"weekly": "W-FRI", "monthly": "ME", "quarterly": "QE"}


def _rebalance_dates(idx: pd.DatetimeIndex, freq: str) -> pd.DatetimeIndex:
    """Last available trading day of each period."""
    s = pd.Series(idx, index=idx)
    return pd.DatetimeIndex(s.groupby(pd.Grouper(freq=REBAL[freq])).last().dropna().to_numpy())


def target_weights(px: pd.DataFrame, strategy: str, top_n: int = 3, lookback: int = 126, skip: int = 21,
                   vol_window: int = 63, rf: float = 0.0, rebalance: str = "monthly") -> pd.DataFrame:
    """Target weights on each rebalance date (rows) per asset (columns). NaN rows = not a rebalance day."""
    if strategy not in STRATEGIES:
        raise ValueError(f"unknown strategy {strategy}. Options: {', '.join(STRATEGIES)}")
    n_assets = px.shape[1]
    top_n = max(1, min(int(top_n), n_assets if strategy != "long_short" else n_assets // 2 or 1))
    mom = px.shift(skip) / px.shift(skip + lookback) - 1
    rets = px.pct_change()
    vol = rets.rolling(vol_window).std() * np.sqrt(AF)
    dates = _rebalance_dates(px.index, rebalance)
    W = pd.DataFrame(np.nan, index=px.index, columns=px.columns)
    cash_hurdle = rf * lookback / AF
    for d in dates:
        m, v = mom.loc[d].dropna(), vol.loc[d].dropna()
        w = pd.Series(0.0, index=px.columns)
        if strategy in ("xs_momentum", "dual_momentum", "long_short"):
            if len(m) < top_n:
                continue
            ranked = m.sort_values(ascending=False)
            longs = ranked.index[:top_n]
            if strategy == "dual_momentum":
                longs = [s for s in longs if ranked[s] > cash_hurdle]
            if len(longs):
                w[longs] = 1.0 / top_n  # unfilled slots stay in cash
            if strategy == "long_short":
                w[ranked.index[-top_n:]] = -1.0 / top_n
                w[longs] = 1.0 / top_n
        elif strategy == "low_vol":
            if len(v) < top_n:
                continue
            w[v.sort_values().index[:top_n]] = 1.0 / top_n
        elif strategy == "inverse_vol":
            if v.empty:
                continue
            iv = 1 / v.replace(0, np.nan)
            w[iv.index] = iv / iv.sum()
        else:  # equal_weight
            avail = px.loc[d].dropna().index
            w[avail] = 1.0 / len(avail)
        W.loc[d] = w
    return W


def run(px: pd.DataFrame, strategy: str, top_n: int = 3, lookback: int = 126, skip: int = 21,
        rebalance: str = "monthly", cost_bps: float = 5.0, rf: float = 0.0, bench: pd.Series | None = None) -> dict:
    px = px.ffill().dropna(how="any")
    if len(px) < lookback + skip + 30:
        raise ValueError(f"not enough overlapping history ({len(px)} bars) for a {lookback}d lookback")
    rets = px.pct_change().fillna(0.0)
    targets = target_weights(px, strategy, top_n, lookback, skip, rf=rf, rebalance=rebalance)
    # hold targets until next rebalance; trade at close t -> earn from t+1
    held = targets.ffill().fillna(0.0)
    # drift between rebalances is ignored (daily-rebalanced to target): standard simplification, documented in UI
    pos = held.shift(1).fillna(0.0)
    turnover = pos.diff().abs().sum(axis=1).fillna(pos.abs().sum(axis=1))
    cash_w = (1 - pos.sum(axis=1)).clip(lower=0) if strategy != "long_short" else pd.Series(1.0, index=pos.index)
    port_r = (pos * rets).sum(axis=1) + cash_w * rf / AF - turnover * cost_bps / 1e4
    # start the curve at the first rebalance so the warm-up period doesn't count
    first = targets.dropna(how="all").index
    if not len(first):
        raise ValueError("no rebalance date had enough history")
    start = px.index[px.index.get_loc(first[0])]
    port_r = port_r.loc[start:]
    port_r.iloc[0] = 0.0
    equity = 100 * (1 + port_r).cumprod()
    if bench is None:
        ew = rets.loc[start:].mean(axis=1)
        ew.iloc[0] = 0.0
        bench = 100 * (1 + ew).cumprod()
        bench_name = "Equal-weight universe"
    else:
        bench = bench.reindex(equity.index).ffill()
        bench = 100 * bench / bench.iloc[0]
        bench_name = str(bench.name or "Benchmark")
    reb = targets.loc[start:].dropna(how="all")
    hist = [{"date": d.strftime("%Y-%m-%d"), "weights": {k: float(v) for k, v in row.items() if abs(v) > 1e-9}}
            for d, row in reb.iterrows()]
    contrib = (pos.loc[start:] * rets.loc[start:]).sum()
    return {
        "strategy": strategy, "description": STRATEGIES[strategy], "equity": equity, "benchmark": bench,
        "bench_name": bench_name, "stats": perf_stats(equity, rf, bench) | {
            "Turnover (ann.)": float(turnover.loc[start:].iloc[1:].mean() * AF),
            "Avg Gross Exposure": float(pos.loc[start:].iloc[1:].abs().sum(axis=1).mean())},
        "bench_stats": perf_stats(bench, rf), "weights": pos.loc[start:], "rebalances": hist[-60:],
        "n_rebalances": len(hist), "current": hist[-1]["weights"] if hist else {},
        "contribution": contrib.to_dict(), "monthly": monthly_returns(equity),
    }
