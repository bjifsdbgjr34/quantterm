"""Performance & risk statistics."""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats as sps

from .timeseries import AF, drawdown


def perf_stats(equity: pd.Series, rf: float = 0.0, bench: pd.Series | None = None) -> dict:
    """Standard performance table for an equity/price curve."""
    equity = equity.dropna()
    r = equity.pct_change().dropna()
    if len(r) < 2:
        return {}
    years = max((equity.index[-1] - equity.index[0]).days / 365.25, 1 / 365.25)
    total = equity.iloc[-1] / equity.iloc[0] - 1
    cagr = (1 + total) ** (1 / years) - 1 if total > -1 else -1.0
    vol = r.std() * np.sqrt(AF)
    ex = r - rf / AF
    downside = np.sqrt((np.minimum(ex, 0) ** 2).mean()) * np.sqrt(AF)
    dd = drawdown(equity)
    mdd = dd.min()
    # longest time under water (trading days)
    under = (dd < 0).astype(int)
    runs = under.groupby((under != under.shift()).cumsum()).cumsum()
    var95 = -np.percentile(r, 5)
    cvar95 = -r[r <= -var95].mean() if (r <= -var95).any() else var95
    out = {
        "Total Return": total, "CAGR": cagr, "Volatility": vol,
        "Sharpe": (ex.mean() * AF) / vol if vol > 0 else np.nan,
        "Sortino": (ex.mean() * AF) / downside if downside > 0 else np.nan,
        "Max Drawdown": mdd, "Calmar": cagr / abs(mdd) if mdd < 0 else np.nan,
        "Longest DD (days)": int(runs.max()) if len(runs) else 0,
        "Skew": float(sps.skew(r)), "Excess Kurtosis": float(sps.kurtosis(r)),
        "VaR 95% (1d)": var95, "CVaR 95% (1d)": cvar95,
        "Best Day": r.max(), "Worst Day": r.min(), "Hit Rate": (r > 0).mean(),
        "Days": int(len(r)),
    }
    if bench is not None:
        b = bench.reindex(equity.index).ffill().pct_change()
        df = pd.concat([r, b], axis=1, sort=False).dropna()
        if len(df) > 10:
            beta = df.iloc[:, 0].cov(df.iloc[:, 1]) / df.iloc[:, 1].var()
            active = df.iloc[:, 0] - df.iloc[:, 1]
            te = active.std() * np.sqrt(AF)
            out |= {"Beta": beta, "Correlation": df.iloc[:, 0].corr(df.iloc[:, 1]),
                    "Alpha (ann.)": (df.iloc[:, 0].mean() - beta * df.iloc[:, 1].mean()) * AF,
                    "Tracking Error": te, "Information Ratio": active.mean() * AF / te if te > 0 else np.nan}
    return {k: (float(v) if v is not None and not (isinstance(v, float) and np.isnan(v)) else None) for k, v in out.items()}


def monthly_returns(equity: pd.Series) -> dict:
    """Year x month return grid for heatmaps."""
    m = equity.resample("ME").last().pct_change().dropna()
    if m.empty:
        return {"years": [], "months": [], "values": []}
    df = pd.DataFrame({"y": m.index.year, "m": m.index.month, "r": m.to_numpy()})
    grid = df.pivot(index="y", columns="m", values="r").reindex(columns=range(1, 13))
    yearly = equity.resample("YE").last().pct_change()
    yearly.iloc[0] = equity.resample("YE").last().iloc[0] / equity.iloc[0] - 1
    grid["YR"] = yearly.groupby(yearly.index.year).last().reindex(grid.index).to_numpy()
    return {"years": [int(y) for y in grid.index], "months": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug",
                                                              "Sep", "Oct", "Nov", "Dec", "Year"],
            "values": [[None if pd.isna(v) else float(v) for v in row] for row in grid.to_numpy()]}


def var_report(returns: pd.Series, value: float = 1.0, horizon: int = 1, levels=(0.95, 0.99)) -> list[dict]:
    """Historical, parametric (normal) and Cornish-Fisher VaR / CVaR."""
    r = returns.dropna()
    mu, sd = r.mean(), r.std()
    s, k = sps.skew(r), sps.kurtosis(r)
    out = []
    for cl in levels:
        a = 1 - cl
        z = sps.norm.ppf(a)
        zcf = z + (z**2 - 1) * s / 6 + (z**3 - 3 * z) * k / 24 - (2 * z**3 - 5 * z) * s**2 / 36
        h = np.sqrt(horizon)
        hist = -np.percentile(r, a * 100)
        hist_cvar = -r[r <= -hist].mean()
        out.append({
            "level": cl,
            "historical": hist * h * value, "historical_cvar": hist_cvar * h * value,
            "parametric": -(mu * horizon + z * sd * h) * value,
            "parametric_cvar": (-(mu * horizon) + sd * h * sps.norm.pdf(z) / a) * value,
            "cornish_fisher": -(mu * horizon + zcf * sd * h) * value,
        })
    return out


def stress_scenarios(weights: dict[str, float], betas: dict[str, float], value: float) -> list[dict]:
    """Beta-based market shock P&L for a portfolio."""
    shocks = {"Equity -5%": -0.05, "Equity -10%": -0.10, "Equity -20% (bear)": -0.20, "Black Monday 1987 (-20.5%)": -0.205,
              "COVID crash Feb-Mar 2020 (-34%)": -0.34, "GFC peak-trough (-57%)": -0.57, "Equity +10%": 0.10}
    port_beta = sum(weights[s] * betas.get(s, 1.0) for s in weights)
    return [{"scenario": k, "shock": v, "pnl": port_beta * v * value, "beta": port_beta} for k, v in shocks.items()]
