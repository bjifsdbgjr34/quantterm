"""Walk-forward (out-of-sample) validation for the single-asset strategy library.

Each fold fits parameters on a trailing in-sample window by grid search and then trades the next
out-of-sample window with those parameters. The OOS slices are stitched into one equity curve.
The gap between in-sample and out-of-sample Sharpe shows how much the optimiser flatters results.

Positions still follow backtest.run: decided at the close of bar t, earning bar t+1. Each OOS fold is
evaluated on data that includes the in-sample warm-up history, so indicators are warm on the first
OOS bar, but only OOS returns enter the stitched curve.
"""
from __future__ import annotations

import itertools

import numpy as np
import pandas as pd

from . import backtest as bt
from .risk import perf_stats

DEFAULT_GRIDS = {
    "sma_cross": {"fast": [10, 20, 50, 75, 100], "slow": [100, 150, 200, 250]},
    "ema_cross": {"fast": [5, 8, 12, 20], "slow": [26, 40, 60, 100]},
    "momentum": {"lookback": [21, 63, 126, 189, 252]},
    "rsi_reversion": {"entry": [20, 25, 30, 35], "exit": [50, 55, 60, 65]},
    "bollinger_reversion": {"n": [10, 20, 30, 50], "k": [1.5, 2, 2.5]},
    "donchian_breakout": {"entry": [20, 55, 100], "exit": [10, 20, 40]},
    "macd": {"fast": [8, 12], "slow": [21, 26, 35], "signal": [9]},
}


def _score(stats: dict, metric: str) -> float:
    v = stats.get(metric)
    return -np.inf if v is None or not np.isfinite(v) else float(v)


def _slice_returns(data: pd.DataFrame, expr: str, cost_bps: float, start: pd.Timestamp, end: pd.Timestamp) -> pd.Series:
    """Daily strategy returns for [start, end], using all history up to end for indicator warm-up."""
    hist = data.loc[:end]
    res = bt.run(hist, bt.BacktestConfig(expr, cost_bps=cost_bps))
    r = res.equity.pct_change().fillna(0.0)
    return r.loc[start:end]


def walk_forward(data: pd.DataFrame, strategy: str, grid: dict | None = None, train_days: int = 756,
                 test_days: int = 126, cost_bps: float = 5.0, metric: str = "Sharpe", anchored: bool = False,
                 rf: float = 0.0) -> dict:
    if strategy not in bt.STRATEGIES:
        raise ValueError(f"unknown strategy {strategy}")
    grid = grid or DEFAULT_GRIDS.get(strategy)
    if not grid:
        raise ValueError(f"no parameter grid for {strategy}")
    n = len(data)
    if n < train_days + test_days + 20:
        raise ValueError(f"need at least {train_days + test_days + 20} bars, have {n}. Use a longer period or shorter windows.")
    keys = list(grid)
    combos = [dict(zip(keys, c)) for c in itertools.product(*grid.values())]
    idx = data.index
    folds, oos_parts, is_sharpes, oos_sharpes = [], [], [], []
    start = train_days
    while start < n:
        end = min(start + test_days, n) - 1
        tr0 = 0 if anchored else start - train_days
        train = data.iloc[tr0:start]
        best, best_val, best_stats = None, -np.inf, {}
        for params in combos:
            try:
                res = bt.run(train, bt.BacktestConfig(bt.strategy_expr(strategy, params), cost_bps=cost_bps, rf=rf))
            except Exception:  # noqa: BLE001
                continue
            val = _score(res.stats, metric)
            if val > best_val:
                best, best_val, best_stats = params, val, res.stats
        if best is None:
            break
        expr = bt.strategy_expr(strategy, best)
        oos = _slice_returns(data, expr, cost_bps, idx[start], idx[end])
        oos_eq = 100 * (1 + oos).cumprod()
        oos_stats = perf_stats(pd.concat([pd.Series([100.0], index=[idx[start - 1]]), oos_eq]), rf)
        folds.append({"train_start": idx[tr0].strftime("%Y-%m-%d"), "train_end": idx[start - 1].strftime("%Y-%m-%d"),
                      "test_start": idx[start].strftime("%Y-%m-%d"), "test_end": idx[end].strftime("%Y-%m-%d"),
                      "params": best, "is_metric": best_val, "is_sharpe": best_stats.get("Sharpe"),
                      "oos_sharpe": oos_stats.get("Sharpe"), "oos_return": oos_stats.get("Total Return")})
        is_sharpes.append(best_stats.get("Sharpe"))
        oos_sharpes.append(oos_stats.get("Sharpe"))
        oos_parts.append(oos)
        start = end + 1
    oos_r = pd.concat(oos_parts)
    oos_r = oos_r[~oos_r.index.duplicated()]
    equity = 100 * (1 + oos_r).cumprod()
    first = idx.get_loc(oos_r.index[0])
    base_dt = idx[first - 1]
    equity = pd.concat([pd.Series([100.0], index=[base_dt]), equity])
    close = data["Close"].loc[base_dt:]
    bench = 100 * close / close.iloc[0]
    # in-sample reference: the single best parameter set over the whole period (what a naive optimiser reports)
    full_best, full_val = None, -np.inf
    for params in combos:
        try:
            res = bt.run(data.loc[base_dt:], bt.BacktestConfig(bt.strategy_expr(strategy, params), cost_bps=cost_bps, rf=rf))
        except Exception:  # noqa: BLE001
            continue
        if _score(res.stats, metric) > full_val:
            full_best, full_val = params, _score(res.stats, metric)
    full = bt.run(data, bt.BacktestConfig(bt.strategy_expr(strategy, full_best), cost_bps=cost_bps, rf=rf))
    full_eq = full.equity.loc[base_dt:]
    full_eq = 100 * full_eq / full_eq.iloc[0]
    is_mean = float(np.nanmean([x for x in is_sharpes if x is not None])) if is_sharpes else None
    oos_stats = perf_stats(equity, rf, bench)
    efficiency = oos_stats.get("Sharpe") / is_mean if is_mean and is_mean > 0 and oos_stats.get("Sharpe") is not None else None
    stable = len({tuple(sorted(f["params"].items())) for f in folds})
    return {"strategy": strategy, "metric": metric, "grid": grid, "train_days": train_days, "test_days": test_days,
            "anchored": anchored, "folds": folds, "equity": equity, "benchmark": bench, "in_sample_equity": full_eq,
            "in_sample_params": full_best, "stats": oos_stats, "bench_stats": perf_stats(bench, rf),
            "in_sample_stats": perf_stats(full_eq, rf), "avg_is_sharpe": is_mean,
            "wf_efficiency": efficiency, "distinct_param_sets": stable, "n_folds": len(folds)}
