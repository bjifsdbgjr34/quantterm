"""Fama-French style factor regressions using long/short ETF proxies.

Factors are built from liquid ETFs so they update daily from the same price feed:
  MKT  = SPY − rf          (market)
  SMB  = IWM − SPY         (size: small minus big)
  HML  = IWD − IWF         (value minus growth)
  MOM  = MTUM − SPY        (momentum)
  QMJ  = QUAL − SPY        (quality)
  BAB  = USMV − SPY        (low volatility)
These are proxies, not the academic Ken French portfolios: they are long-only ETF spreads and partly
overlap. Loadings are OLS estimates on daily simple returns.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats as sps

from .timeseries import AF

FACTOR_ETFS = {"SPY", "IWM", "IWD", "IWF", "MTUM", "QUAL", "USMV"}
FACTOR_DESC = {
    "MKT": "Market (SPY − cash)", "SMB": "Size (IWM − SPY)", "HML": "Value (IWD − IWF)",
    "MOM": "Momentum (MTUM − SPY)", "QMJ": "Quality (QUAL − SPY)", "BAB": "Low vol (USMV − SPY)",
}


def factor_returns(px: pd.DataFrame, rf: float = 0.0, factors: list[str] | None = None) -> pd.DataFrame:
    r = px.pct_change()
    out = pd.DataFrame(index=r.index)
    spec = {
        "MKT": lambda: r["SPY"] - rf / AF, "SMB": lambda: r["IWM"] - r["SPY"], "HML": lambda: r["IWD"] - r["IWF"],
        "MOM": lambda: r["MTUM"] - r["SPY"], "QMJ": lambda: r["QUAL"] - r["SPY"], "BAB": lambda: r["USMV"] - r["SPY"],
    }
    for f in factors or list(spec):
        try:
            out[f] = spec[f]()
        except KeyError:
            continue
    return out.dropna(how="all")


def ols(y: pd.Series, X: pd.DataFrame) -> dict:
    df = pd.concat([y.rename("y"), X], axis=1).dropna()
    if len(df) < X.shape[1] + 10:
        raise ValueError("not enough overlapping observations for the regression")
    Y = df["y"].to_numpy()
    A = np.column_stack([np.ones(len(df)), df[X.columns].to_numpy()])
    coef, *_ = np.linalg.lstsq(A, Y, rcond=None)
    resid = Y - A @ coef
    n, k = A.shape
    s2 = resid @ resid / (n - k)
    cov = s2 * np.linalg.pinv(A.T @ A)
    se = np.sqrt(np.diag(cov))
    t = coef / se
    p = 2 * (1 - sps.t.cdf(np.abs(t), n - k))
    ss_tot = ((Y - Y.mean()) ** 2).sum()
    r2 = 1 - (resid @ resid) / ss_tot if ss_tot > 0 else np.nan
    names = ["alpha"] + list(X.columns)
    return {
        "n": int(n), "r2": float(r2), "adj_r2": float(1 - (1 - r2) * (n - 1) / (n - k)),
        "alpha_ann": float(coef[0] * AF), "resid_vol": float(resid.std() * np.sqrt(AF)),
        "coef": dict(zip(names, coef.tolist())), "t": dict(zip(names, t.tolist())), "p": dict(zip(names, p.tolist())),
        "resid": pd.Series(resid, index=df.index), "index": df.index,
    }


def factor_model(asset_ret: pd.Series, fac: pd.DataFrame, rf: float = 0.0, window: int = 126) -> dict:
    """Full-period regression, rolling betas and cumulative return attribution."""
    y = asset_ret - rf / AF
    res = ols(y, fac)
    idx = res["index"]
    F = fac.loc[idx]
    contrib = pd.DataFrame({f: res["coef"][f] * F[f] for f in fac.columns})
    contrib["Alpha"] = res["coef"]["alpha"]
    contrib["Residual"] = res["resid"]
    cum = contrib.cumsum() * 100  # additive log-ish attribution in % points
    rolling = {}
    df = pd.concat([y.rename("y"), fac], axis=1).dropna()
    if len(df) > window + 5:
        for end in range(window, len(df) + 1, 5):
            sub = df.iloc[end - window:end]
            A = np.column_stack([np.ones(len(sub)), sub[fac.columns].to_numpy()])
            c, *_ = np.linalg.lstsq(A, sub["y"].to_numpy(), rcond=None)
            rolling[sub.index[-1]] = dict(zip(fac.columns, c[1:]))
    roll = pd.DataFrame(rolling).T
    var_y = float(y.loc[idx].var())
    # variance decomposition: share of variance explained by each factor (beta_i * cov(f_i, y) / var(y))
    var_share = {f: float(res["coef"][f] * np.cov(F[f], y.loc[idx])[0, 1] / var_y) if var_y else None for f in fac.columns}
    var_share["Idiosyncratic"] = float(1 - res["r2"])
    return {
        "n": res["n"], "r2": res["r2"], "adj_r2": res["adj_r2"], "alpha_ann": res["alpha_ann"], "resid_vol": res["resid_vol"],
        "loadings": [{"factor": f, "desc": FACTOR_DESC.get(f, f), "beta": res["coef"][f], "t": res["t"][f], "p": res["p"][f]}
                     for f in fac.columns],
        "alpha_t": res["t"]["alpha"], "alpha_p": res["p"]["alpha"],
        "attribution": {"dates": idx, "series": {k: cum[k] for k in cum.columns}},
        "total_contrib": {k: float(contrib[k].sum() * 100) for k in contrib.columns},
        "rolling": {"dates": roll.index, "series": {f: roll[f] for f in roll.columns}} if len(roll) else None,
        "variance_share": var_share, "window": window,
    }
