"""Time-series analytics, following gs_quant.timeseries conventions where sensible
(log returns for vol, vol quoted in percent, 252-day annualisation)."""
from __future__ import annotations

import numpy as np
import pandas as pd

AF = 252


# ---- returns & levels ---------------------------------------------------------------
def returns(x: pd.Series, obs: int = 1, log: bool = False) -> pd.Series:
    return np.log(x / x.shift(obs)) if log else x / x.shift(obs) - 1


def prices(rets: pd.Series, initial: float = 100.0) -> pd.Series:
    return initial * (1 + rets.fillna(0)).cumprod()


def rebase(x: pd.Series | pd.DataFrame, base: float = 100.0):
    return x / x.bfill().iloc[0] * base


def drawdown(x: pd.Series) -> pd.Series:
    """Running drawdown of a price/equity series as a ratio (-0.2 = 20% below peak)."""
    return x / x.cummax() - 1


def max_drawdown(x: pd.Series) -> float:
    return float(drawdown(x).min())


# ---- volatility / dispersion ----------------------------------------------------------
def volatility(x: pd.Series, w: int = 22, af: int = AF) -> pd.Series:
    """Rolling annualised realised vol of log returns, in percent."""
    r = returns(x, log=True)
    return r.rolling(w, min_periods=max(2, w // 2)).std() * np.sqrt(af) * 100


def ewma_volatility(x: pd.Series, lam: float = 0.94, af: int = AF) -> pd.Series:
    r = returns(x, log=True)
    return np.sqrt((r**2).ewm(alpha=1 - lam).mean() * af) * 100


def parkinson_volatility(high: pd.Series, low: pd.Series, w: int = 22, af: int = AF) -> pd.Series:
    hl = np.log(high / low) ** 2
    return np.sqrt(hl.rolling(w).mean() / (4 * np.log(2)) * af) * 100


def vol_cone(x: pd.Series, windows=(10, 21, 42, 63, 126, 252)) -> list[dict]:
    """Distribution of realised vol by horizon: min / 25% / median / 75% / max / current."""
    out = []
    for w in windows:
        v = volatility(x, w).dropna()
        if len(v) < 5:
            continue
        q = v.quantile([0, 0.1, 0.25, 0.5, 0.75, 0.9, 1]).to_numpy()
        out.append({"window": w, "min": q[0], "p10": q[1], "p25": q[2], "p50": q[3], "p75": q[4], "p90": q[5],
                    "max": q[6], "current": float(v.iloc[-1])})
    return out


def zscores(x: pd.Series, w: int | None = None) -> pd.Series:
    if w is None:
        return (x - x.mean()) / x.std()
    return (x - x.rolling(w).mean()) / x.rolling(w).std()


# ---- relationships -------------------------------------------------------------------
def beta(x: pd.Series, bench: pd.Series, w: int | None = None) -> pd.Series | float:
    rx, rb = returns(x), returns(bench)
    df = pd.concat([rx, rb], axis=1, sort=False).dropna()
    if w is None:
        return float(df.iloc[:, 0].cov(df.iloc[:, 1]) / df.iloc[:, 1].var())
    return df.iloc[:, 0].rolling(w).cov(df.iloc[:, 1]) / df.iloc[:, 1].rolling(w).var()


def correlation(x: pd.Series, y: pd.Series, w: int | None = None):
    df = pd.concat([returns(x), returns(y)], axis=1, sort=False).dropna()
    if w is None:
        return float(df.iloc[:, 0].corr(df.iloc[:, 1]))
    return df.iloc[:, 0].rolling(w).corr(df.iloc[:, 1])


def regression(y: pd.Series, x: pd.Series) -> dict:
    """OLS of y returns on x returns: alpha (annualised), beta, R^2, t-stats, residual vol."""
    df = pd.concat([returns(y), returns(x)], axis=1, sort=False).dropna()
    yy, xx = df.iloc[:, 0].to_numpy(), df.iloc[:, 1].to_numpy()
    X = np.column_stack([np.ones_like(xx), xx])
    coef, *_ = np.linalg.lstsq(X, yy, rcond=None)
    resid = yy - X @ coef
    n = len(yy)
    s2 = resid @ resid / max(n - 2, 1)
    cov = s2 * np.linalg.inv(X.T @ X)
    se = np.sqrt(np.diag(cov))
    ss_tot = ((yy - yy.mean()) ** 2).sum()
    return {
        "alpha_ann": float(coef[0] * AF), "beta": float(coef[1]), "r2": float(1 - resid @ resid / ss_tot) if ss_tot else 0.0,
        "t_alpha": float(coef[0] / se[0]), "t_beta": float(coef[1] / se[1]), "corr": float(np.corrcoef(xx, yy)[0, 1]),
        "resid_vol": float(np.std(resid, ddof=2) * np.sqrt(AF) * 100), "n": n,
        "x": xx.tolist(), "y": yy.tolist(),
    }


# ---- technicals ------------------------------------------------------------------------
def sma(x: pd.Series, w: int = 20) -> pd.Series:
    return x.rolling(w).mean()


def ema(x: pd.Series, w: int = 20) -> pd.Series:
    return x.ewm(span=w, adjust=False).mean()


def rsi(x: pd.Series, w: int = 14) -> pd.Series:
    d = x.diff()
    up = d.clip(lower=0).ewm(alpha=1 / w, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / w, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.where(dn != 0, 100.0)


def macd(x: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    line = ema(x, fast) - ema(x, slow)
    sig = ema(line, signal)
    return pd.DataFrame({"macd": line, "signal": sig, "hist": line - sig})


def bollinger_bands(x: pd.Series, w: int = 20, k: float = 2.0) -> pd.DataFrame:
    m, s = sma(x, w), x.rolling(w).std()
    return pd.DataFrame({"mid": m, "upper": m + k * s, "lower": m - k * s})


def atr(high: pd.Series, low: pd.Series, close: pd.Series, w: int = 14) -> pd.Series:
    tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / w, adjust=False).mean()


def vwap(close: pd.Series, volume: pd.Series, w: int = 20) -> pd.Series:
    return (close * volume).rolling(w).sum() / volume.rolling(w).sum()


def stochastic(high, low, close, w: int = 14, smooth: int = 3) -> pd.DataFrame:
    k = 100 * (close - low.rolling(w).min()) / (high.rolling(w).max() - low.rolling(w).min())
    return pd.DataFrame({"k": k, "d": k.rolling(smooth).mean()})
