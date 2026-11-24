"""Statistical / econometric quant tools:
GARCH(1,1) volatility model, return-distribution diagnostics, stationarity & mean reversion
(ADF, Hurst, OU half-life), pairs trading via Engle-Granger cointegration, PCA factor analysis,
and Kelly position sizing."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
from scipy import stats as sps
from scipy.optimize import minimize

AF = 252


# --------------------------------------------------------------------------------------
# GARCH(1,1)
# --------------------------------------------------------------------------------------
def fit_garch(log_rets: pd.Series, horizon: int = 63) -> dict:
    """Gaussian GARCH(1,1) by maximum likelihood on demeaned daily log returns."""
    r = log_rets.dropna()
    x = (r - r.mean()).to_numpy() * 100  # percent for numerical stability
    var0 = x.var()

    def filt(params):
        omega, alpha, beta = params
        h = np.empty_like(x)
        h[0] = var0
        for t in range(1, len(x)):
            h[t] = omega + alpha * x[t - 1] ** 2 + beta * h[t - 1]
        return h

    def nll(params):
        if params[1] + params[2] >= 0.9999:
            return 1e10
        h = filt(params)
        return 0.5 * np.sum(np.log(2 * np.pi * h) + x**2 / h)

    best = None
    for a0, b0 in ((0.05, 0.9), (0.1, 0.85), (0.03, 0.95)):
        res = minimize(nll, [var0 * (1 - a0 - b0), a0, b0], method="L-BFGS-B",
                       bounds=[(1e-6, var0 * 10), (1e-6, 0.5), (1e-6, 0.9999)])
        if best is None or res.fun < best.fun:
            best = res
    omega, alpha, beta = best.x
    h = filt(best.x)
    persist = alpha + beta
    lr_var = omega / (1 - persist) if persist < 1 else np.nan
    # forecast: E[h_{t+k}] = lr + persist^(k-1) (h_{t+1} - lr)
    h_next = omega + alpha * x[-1] ** 2 + beta * h[-1]
    k = np.arange(1, horizon + 1)
    fc = lr_var + persist ** (k - 1) * (h_next - lr_var)
    term_vol = np.sqrt(np.cumsum(fc) / k * AF)  # avg vol over horizon k, annualised %, x already in %
    n = len(x)
    return {
        "omega": omega / 1e4, "alpha": alpha, "beta": beta, "persistence": persist,
        "long_run_vol": math.sqrt(lr_var * AF) if lr_var == lr_var else None,
        "half_life_days": math.log(0.5) / math.log(persist) if 0 < persist < 1 else None,
        "current_vol": math.sqrt(h_next * AF), "loglik": -best.fun,
        "aic": 2 * 3 + 2 * best.fun, "bic": 3 * math.log(n) + 2 * best.fun,
        "cond_vol": pd.Series(np.sqrt(h * AF), index=r.index),
        "forecast": {"day": k, "daily_vol": np.sqrt(fc * AF), "term_vol": term_vol},
        "std_resid": pd.Series(x / np.sqrt(h), index=r.index),
        # params in model units for the Monte Carlo GARCH simulator (decimal returns)
        "sim_params": {"omega": omega / 1e4, "alpha": alpha, "beta": beta, "last_var": h_next / 1e4,
                       "last_eps": x[-1] / 100},
    }


# --------------------------------------------------------------------------------------
# distribution & serial dependence
# --------------------------------------------------------------------------------------
def acf(x: np.ndarray, nlags: int = 20) -> list[float]:
    x = np.asarray(x) - np.mean(x)
    d = x @ x
    return [float(x[:-k] @ x[k:] / d) for k in range(1, nlags + 1)]


def ljung_box(x: np.ndarray, lags: int = 10) -> dict:
    n = len(x)
    rho = np.array(acf(x, lags))
    q = n * (n + 2) * np.sum(rho**2 / (n - np.arange(1, lags + 1)))
    return {"Q": float(q), "p_value": float(1 - sps.chi2.cdf(q, lags)), "lags": lags}


def hurst(x: pd.Series, max_lag: int = 100) -> float:
    """Hurst exponent from scaling of lagged differences (<0.5 mean-reverting, >0.5 trending)."""
    v = np.log(x.dropna().to_numpy())
    lags = np.unique(np.geomspace(2, min(max_lag, len(v) // 4), 20).astype(int))
    tau = [np.std(v[lag:] - v[:-lag]) for lag in lags]
    return float(np.polyfit(np.log(lags), np.log(tau), 1)[0])


def half_life(spread: pd.Series) -> float | None:
    """Ornstein-Uhlenbeck half-life (days) from AR(1) regression of changes on lagged level."""
    s = spread.dropna()
    lag, d = s.shift(1).iloc[1:], s.diff().iloc[1:]
    b = np.polyfit(lag.to_numpy(), d.to_numpy(), 1)[0]
    return float(-math.log(2) / b) if b < 0 else None


def adf(x: pd.Series) -> dict:
    try:
        from statsmodels.tsa.stattools import adfuller

        stat, p, lags, n, crit, _ = adfuller(x.dropna().to_numpy(), autolag="AIC")
        return {"stat": float(stat), "p_value": float(p), "lags": int(lags), "crit": {k: float(v) for k, v in crit.items()}}
    except ImportError:  # simple DF regression with constant
        s = x.dropna()
        y, l = s.diff().iloc[1:].to_numpy(), s.shift(1).iloc[1:].to_numpy()
        X = np.column_stack([np.ones_like(l), l])
        coef, *_ = np.linalg.lstsq(X, y, rcond=None)
        resid = y - X @ coef
        se = math.sqrt(resid @ resid / (len(y) - 2) * np.linalg.inv(X.T @ X)[1, 1])
        return {"stat": float(coef[1] / se), "p_value": None, "lags": 0, "crit": {"1%": -3.43, "5%": -2.86, "10%": -2.57}}


def return_stats(close: pd.Series) -> dict:
    r = np.log(close / close.shift()).dropna()
    x = r.to_numpy()
    jb = sps.jarque_bera(x)
    nu, loc, scale = sps.t.fit(x)
    hist, edges = np.histogram(x * 100, bins=80, density=True)
    grid = np.linspace(edges[0], edges[-1], 200)
    q = np.linspace(0.005, 0.995, 120)
    z = (x - x.mean()) / x.std()
    weekly = np.log(close.resample("W").last()).diff().dropna()
    vr = (weekly.var() / 5) / r.var() if len(weekly) > 10 else None  # variance ratio (5d)
    return {
        "n": len(x), "mean_ann": x.mean() * AF, "vol_ann": x.std() * math.sqrt(AF), "skew": float(sps.skew(x)),
        "kurtosis": float(sps.kurtosis(x)), "jarque_bera": float(jb.statistic), "jb_p": float(jb.pvalue),
        "t_df": float(nu), "hist": {"x": (edges[:-1] + edges[1:]) / 2, "y": hist},
        "normal_pdf": {"x": grid, "y": sps.norm.pdf(grid, x.mean() * 100, x.std() * 100)},
        "t_pdf": {"x": grid, "y": sps.t.pdf(grid, nu, loc * 100, scale * 100)},
        "qq": {"theoretical": sps.norm.ppf(q), "sample": np.quantile(z, q)},
        "acf_returns": acf(x, 20), "acf_squared": acf(x**2, 20), "acf_band": 1.96 / math.sqrt(len(x)),
        "ljung_box": ljung_box(x, 10), "ljung_box_sq": ljung_box(x**2, 10),
        "hurst": hurst(close), "adf_price": adf(np.log(close)), "variance_ratio_5d": vr,
        "tail_ratio": float(abs(np.percentile(x, 95)) / abs(np.percentile(x, 5))),
    }


# --------------------------------------------------------------------------------------
# Kelly
# --------------------------------------------------------------------------------------
def kelly(close: pd.Series, rf: float = 0.0) -> dict:
    r = close.pct_change().dropna()
    mu, var = r.mean() * AF - rf, r.var() * AF
    f = mu / var if var > 0 else 0.0
    lev = np.linspace(-1, max(3.0, min(abs(f) * 2, 10)), 121)
    growth = rf + lev * mu - 0.5 * lev**2 * var  # continuous-time approx
    # empirical growth by actually compounding daily returns at each leverage
    emp = [float(np.mean(np.log(np.maximum(1 + L * r.to_numpy() + (1 - L) * rf / AF, 1e-9))) * AF) for L in lev]
    return {"full_kelly": float(f), "half_kelly": float(f / 2), "excess_return": float(mu), "variance": float(var),
            "growth_at_kelly": float(rf + 0.5 * mu**2 / var) if var > 0 else None,
            "curve": {"leverage": lev, "growth_theory": growth, "growth_empirical": emp}}


# --------------------------------------------------------------------------------------
# pairs trading
# --------------------------------------------------------------------------------------
def pairs(y: pd.Series, x: pd.Series, z_entry: float = 2.0, z_exit: float = 0.5, window: int = 60,
          cost_bps: float = 5.0) -> dict:
    df = pd.concat([np.log(y), np.log(x)], axis=1, sort=False).dropna()
    df.columns = ["y", "x"]
    X = np.column_stack([np.ones(len(df)), df["x"].to_numpy()])
    (a, b), *_ = np.linalg.lstsq(X, df["y"].to_numpy(), rcond=None)
    spread = df["y"] - b * df["x"] - a
    try:
        from statsmodels.tsa.stattools import coint

        t, p, crit = coint(df["y"], df["x"])
        eg = {"stat": float(t), "p_value": float(p), "crit": {"1%": crit[0], "5%": crit[1], "10%": crit[2]}}
    except ImportError:
        eg = adf(spread) | {"note": "ADF on residuals (statsmodels not installed)"}
    z = (spread - spread.rolling(window).mean()) / spread.rolling(window).std()
    # rolling hedge ratio
    roll_beta = df["y"].diff().rolling(window).cov(df["x"].diff()) / df["x"].diff().rolling(window).var()
    # z-score strategy: short spread when z > entry, long when z < -entry, flat when |z| < exit
    pos = pd.Series(np.nan, index=z.index)
    pos[z > z_entry] = -1
    pos[z < -z_entry] = 1
    pos[z.abs() < z_exit] = 0
    pos = pos.ffill().fillna(0)
    ry, rx = df["y"].diff(), df["x"].diff()  # log returns
    spread_ret = (ry - b * rx) / (1 + abs(b))  # gross-normalised
    lagged = pos.shift(1).fillna(0)
    pnl = lagged * spread_ret - lagged.diff().abs().fillna(0) * cost_bps / 1e4 * 2
    equity = 100 * np.exp(pnl.fillna(0).cumsum())
    from .risk import perf_stats

    return {"alpha": float(a), "hedge_ratio": float(b), "coint": eg, "half_life": half_life(spread),
            "hurst_spread": hurst(np.exp(spread - spread.min() + 1)), "corr": float(ry.corr(rx)),
            "spread": spread, "z": z, "roll_beta": roll_beta, "position": pos, "equity": equity,
            "stats": perf_stats(equity), "dates": df.index, "current_z": float(z.iloc[-1]) if z.notna().any() else None,
            "n_trades": int((pos.diff().abs() > 0).sum())}


# --------------------------------------------------------------------------------------
# PCA
# --------------------------------------------------------------------------------------
def pca(rets: pd.DataFrame, k: int = 5) -> dict:
    R = rets.dropna()
    Z = (R - R.mean()) / R.std()
    C = np.cov(Z.to_numpy().T)
    vals, vecs = np.linalg.eigh(C)
    order = np.argsort(vals)[::-1]
    vals, vecs = vals[order], vecs[:, order]
    # sign convention: make loadings sum positive
    vecs = vecs * np.sign(vecs.sum(axis=0) + 1e-12)
    k = min(k, len(vals))
    scores = Z.to_numpy() @ vecs[:, :k]
    factors = pd.DataFrame(scores, index=R.index, columns=[f"PC{i + 1}" for i in range(k)])
    return {"symbols": list(R.columns), "explained": vals / vals.sum(), "cumulative": np.cumsum(vals) / vals.sum(),
            "loadings": {f"PC{i + 1}": vecs[:, i] for i in range(k)},
            "factor_index": {c: 100 * np.exp(np.cumsum(factors[c] / factors[c].std() * 0.01)) for c in factors.columns},
            "dates": R.index, "absorption_ratio": float(vals[: max(1, len(vals) // 5)].sum() / vals.sum())}
