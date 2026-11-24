import math

import numpy as np
import pandas as pd
import pytest

from quantterm.analytics import backtest as bt
from quantterm.analytics import options, portfolio, risk
from quantterm.analytics import timeseries as ts
from quantterm.trading.paper import PaperBroker


def gbm(n=750, vol=0.2, seed=1, mu=0.08):
    rng = np.random.default_rng(seed)
    r = rng.normal(mu / 252, vol / np.sqrt(252), n)
    idx = pd.bdate_range("2020-01-01", periods=n)
    c = pd.Series(100 * np.exp(np.cumsum(r)), index=idx)
    return pd.DataFrame({"Open": c.shift().bfill(), "High": c * 1.01, "Low": c * 0.99, "Close": c, "Volume": 1e6})


def test_volatility_close_to_input():
    df = gbm(2000, 0.25)
    assert 22 < ts.volatility(df.Close, 252).iloc[-1] < 28


def test_drawdown_and_rsi_bounds():
    df = gbm()
    assert ts.max_drawdown(df.Close) <= 0
    r = ts.rsi(df.Close).dropna()
    assert r.between(0, 100).all()


def test_bs_put_call_parity_and_iv_roundtrip():
    S, K, T, r, v = 100, 105, 0.5, 0.03, 0.25
    c = options.bs_price(S, K, T, r, v, "call")
    p = options.bs_price(S, K, T, r, v, "put")
    assert math.isclose(c - p, S - K * math.exp(-r * T), rel_tol=1e-9)
    assert math.isclose(options.implied_vol(c, S, K, T, r, "call"), v, rel_tol=1e-5)
    g = options.greeks(S, K, T, r, v, "call")
    assert 0 < g["delta"] < 1 and g["gamma"] > 0 and g["vega"] > 0 and g["theta"] < 0


def test_binomial_converges_to_bs_for_european():
    bs = options.bs_price(100, 100, 1, 0.05, 0.2, "call")
    assert abs(options.binomial_price(100, 100, 1, 0.05, 0.2, "call", american=False, steps=500) - bs) < 0.02
    # american put >= european put
    assert options.binomial_price(100, 100, 1, 0.05, 0.2, "put") >= options.bs_price(100, 100, 1, 0.05, 0.2, "put")


def test_portfolio_optimizers_sum_to_one():
    px = pd.DataFrame({f"A{i}": gbm(500, 0.15 + 0.05 * i, seed=i).Close for i in range(4)})
    mu, cov, _ = portfolio.estimate(px)
    for m in ("equal", "inverse_vol", "min_var", "max_sharpe", "risk_parity", "max_diversification"):
        w = portfolio.optimize(mu, cov, m)
        assert abs(w.sum() - 1) < 1e-6 and (w >= -1e-9).all(), m
    rc = portfolio.risk_contributions(portfolio.optimize(mu, cov, "risk_parity"), cov)
    assert rc["pct"].std() < 0.02
    assert len(portfolio.efficient_frontier(mu, cov, 10)) > 3


def test_backtest_no_lookahead_and_buy_hold_matches():
    df = gbm()
    res = bt.run(df, bt.BacktestConfig("1", cost_bps=0))
    # signal at day-0 close earns from day 1 -> identical to benchmark
    assert abs(res.equity.iloc[-1] / res.benchmark.iloc[-1] - 1) < 1e-9
    # signal computed from today's return must not earn today's return (position is lagged)
    pos = bt.run(df, bt.BacktestConfig("ret(close,1) > 0", cost_bps=0)).position
    same_day = (pos * ts.returns(df.Close)).sum()
    assert same_day < (ts.returns(df.Close).clip(lower=0)).sum() * 0.8


def test_expression_language():
    df = gbm()
    s = bt.evaluate("sma(close,10) > sma(close,50) and rsi(close) < 70", df)
    assert set(s.dropna().unique()) <= {0.0, 1.0}
    for name in bt.STRATEGIES:
        bt.run(df, bt.BacktestConfig(bt.strategy_expr(name)))
    with pytest.raises(bt.ExprError):
        bt.evaluate("__import__('os')", df)
    with pytest.raises(bt.ExprError):
        bt.evaluate("close.__class__", df)


def test_var_report_ordering():
    r = gbm(1500).Close.pct_change().dropna()
    rep = risk.var_report(r, 1e6)
    assert rep[1]["historical"] > rep[0]["historical"] > 0


def test_paper_broker(tmp_path):
    b = PaperBroker(tmp_path / "p.db")
    b.place("AAPL", "BUY", 10, last_px=100)
    b.place("AAPL", "SELL", 4, last_px=110)
    snap = b.snapshot({"AAPL": 110})
    pos = snap["positions"][0]
    assert pos["qty"] == 6 and snap["realized"] > 39  # ~ 4 * 10 minus slippage
    b.place("AAPL", "SELL", 10, last_px=110)  # flip short 4
    assert b.snapshot({"AAPL": 100})["positions"][0]["qty"] == -4
    b.place("MSFT", "BUY", 1, "limit", limit_px=50)
    assert b.process_open_orders({"MSFT": 60}) == 0
    assert b.process_open_orders({"MSFT": 49}) == 1


from quantterm.analytics import montecarlo as mc
from quantterm.analytics import quant as qt


def test_mc_european_matches_black_scholes():
    res = mc.mc_option(100, 100, 0.5, 0.03, 0.25, "call", "european", n_paths=200_000)
    assert abs(res["price"] - res["bs_european"]) < 4 * res["std_error"] + 0.01
    # in + out = vanilla for barriers (same seed/paths)
    kw = dict(S0=100, K=100, T=0.5, r=0.03, sigma=0.25, kind="call", barrier=115, n_paths=40_000, steps=252)
    ins = mc.mc_option(style="up_and_in", **kw)["price"]
    out = mc.mc_option(style="up_and_out", **kw)["price"]
    van = mc.mc_option(style="european", **kw | {"steps": 252})["price"]
    assert abs(ins + out - van) < 0.25
    assert mc.mc_option(style="asian", **kw)["price"] < van


def test_mc_paths_all_models():
    h = np.log(gbm(1000).Close).diff().dropna()
    cal = mc.calibrate(h)
    for m in mc.MODELS:
        p = mc.simulate_paths(100, 252, 2000, m, 0.08, 0.2, h, cal, seed=1)
        assert p.shape == (2000, 253) and (p > 0).all()
        assert 0.10 < np.log(p[:, -1] / 100).std() < 0.32, m


def test_garch_recovers_persistence():
    rng = np.random.default_rng(3)
    n, w, a, b = 3000, 1e-6, 0.08, 0.9
    h, e = np.empty(n), np.empty(n)
    h[0] = w / (1 - a - b)
    for t in range(n):
        if t:
            h[t] = w + a * e[t - 1] ** 2 + b * h[t - 1]
        e[t] = np.sqrt(h[t]) * rng.standard_normal()
    g = qt.fit_garch(pd.Series(e, index=pd.bdate_range("2010-01-01", periods=n)))
    assert 0.93 < g["persistence"] < 0.995 and 0.03 < g["alpha"] < 0.15


def test_pairs_detects_cointegration_and_pca():
    rng = np.random.default_rng(5)
    idx = pd.bdate_range("2018-01-01", periods=1000)
    x = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, 1000))), index=idx)
    noise = pd.Series(0.0, index=idx)
    for i in range(1, 1000):  # OU spread
        noise.iloc[i] = 0.9 * noise.iloc[i - 1] + rng.normal(0, 0.01)
    y = x**1.2 * np.exp(noise)
    res = qt.pairs(y, x)
    assert res["coint"]["p_value"] < 0.05 and abs(res["hedge_ratio"] - 1.2) < 0.1
    assert 3 < res["half_life"] < 15
    px = pd.DataFrame({f"S{i}": gbm(500, seed=i).Close for i in range(5)})
    p = qt.pca(px.pct_change().dropna(), 3)
    assert abs(sum(p["explained"]) - 1) < 1e-9


def test_kelly_and_hurst():
    df = gbm(2000, 0.2, mu=0.10)
    k = qt.kelly(df.Close)
    assert k["full_kelly"] == pytest.approx(k["excess_return"] / k["variance"])
    assert 0.35 < qt.hurst(df.Close) < 0.65
