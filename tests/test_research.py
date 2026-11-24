import json
import math

import httpx
import numpy as np
import pandas as pd
import pytest

from quantterm import assistant
from quantterm.analytics import factors as fx
from quantterm.analytics import multiasset as ma
from quantterm.analytics import optsignals as osig
from quantterm.analytics import screener as scr
from quantterm.analytics import walkforward as wf
from quantterm.store import Store, alert_hit
from quantterm.trading.alpaca import AlpacaBroker, BrokerError
from quantterm.trading.broker import Broker
from quantterm.trading.paper import PaperBroker


def gbm(n=1500, vol=0.2, seed=1, mu=0.08):
    rng = np.random.default_rng(seed)
    r = rng.normal(mu / 252, vol / np.sqrt(252), n)
    idx = pd.bdate_range("2015-01-01", periods=n)
    c = pd.Series(100 * np.exp(np.cumsum(r)), index=idx)
    return pd.DataFrame({"Open": c.shift().bfill(), "High": c * 1.01, "Low": c * 0.99, "Close": c, "Volume": 1e6})


# ------------------------------------------------------------------ walk-forward
def test_walk_forward_stitches_oos_folds():
    df = gbm(1400)
    res = wf.walk_forward(df, "momentum", {"lookback": [21, 63, 126]}, train_days=500, test_days=150)
    assert res["n_folds"] == math.ceil((1400 - 500) / 150)
    # stitched OOS curve covers exactly the out-of-sample span (+1 base point)
    assert len(res["equity"]) == 1400 - 500 + 1
    assert res["folds"][0]["test_start"] > res["folds"][0]["train_end"]
    for a, b in zip(res["folds"], res["folds"][1:]):
        assert b["test_start"] > a["test_end"]
    assert "Sharpe" in res["stats"]


def test_walk_forward_oos_uses_only_past_parameters():
    """A random walk has no edge: in-sample best Sharpe should exceed what OOS delivers on average."""
    sh_is, sh_oos = [], []
    for seed in range(4):
        res = wf.walk_forward(gbm(1300, seed=seed, mu=0.0), "sma_cross",
                              {"fast": [5, 10, 20], "slow": [50, 100]}, train_days=500, test_days=200)
        sh_is.append(res["avg_is_sharpe"])
        sh_oos.append(res["stats"]["Sharpe"])
    assert np.mean(sh_is) > np.mean(sh_oos)


def test_walk_forward_rejects_short_history():
    with pytest.raises(ValueError):
        wf.walk_forward(gbm(300), "momentum")


# ------------------------------------------------------------------ multi-asset
def _universe(n=900, k=5):
    return pd.DataFrame({f"A{i}": gbm(n, 0.15 + 0.03 * i, seed=10 + i, mu=0.02 * i).Close for i in range(k)})


def test_xs_momentum_weights_and_no_lookahead():
    px = _universe()
    W = ma.target_weights(px, "xs_momentum", top_n=2)
    reb = W.dropna(how="all")
    assert np.allclose(reb.sum(axis=1), 1.0)
    assert ((reb > 0).sum(axis=1) == 2).all()
    # changing prices AFTER a rebalance date must not change that date's weights
    d = reb.index[5]
    px2 = px.copy()
    px2.loc[px2.index > d] *= np.random.default_rng(0).uniform(0.5, 1.5, size=px2.loc[px2.index > d].shape)
    W2 = ma.target_weights(px2, "xs_momentum", top_n=2)
    pd.testing.assert_series_equal(W.loc[d], W2.loc[d])


def test_long_short_is_dollar_neutral_and_run_outputs():
    px = _universe(k=6)
    res = ma.run(px, "long_short", top_n=2)
    reb = ma.target_weights(px, "long_short", top_n=2).dropna(how="all")
    assert np.allclose(reb.sum(axis=1), 0.0)
    assert res["equity"].iloc[0] == 100
    assert res["stats"]["Avg Gross Exposure"] > 1.5


def test_equal_weight_matches_manual():
    px = _universe(k=3)
    res = ma.run(px, "equal_weight", cost_bps=0, rebalance="monthly")
    assert abs(res["stats"]["Avg Gross Exposure"] - 1) < 1e-9
    assert res["equity"].iloc[-1] > 0


def test_dual_momentum_goes_to_cash_when_everything_falls():
    idx = pd.bdate_range("2020-01-01", periods=400)
    falling = pd.DataFrame({f"A{i}": 100 * np.exp(-0.001 * (i + 1) * np.arange(400)) for i in range(3)}, index=idx)
    W = ma.target_weights(falling, "dual_momentum", top_n=2).dropna(how="all")
    assert (W.abs().sum(axis=1) == 0).all()


# ------------------------------------------------------------------ factors
def test_factor_regression_recovers_betas():
    rng = np.random.default_rng(3)
    n = 1000
    idx = pd.bdate_range("2020-01-01", periods=n)
    F = pd.DataFrame(rng.normal(0, 0.01, (n, 3)), index=idx, columns=["MKT", "SMB", "HML"])
    y = 0.0002 + 1.2 * F["MKT"] - 0.4 * F["SMB"] + 0.3 * F["HML"] + rng.normal(0, 0.002, n)
    res = fx.factor_model(y, F, rf=0.0, window=126)
    b = {x["factor"]: x["beta"] for x in res["loadings"]}
    assert abs(b["MKT"] - 1.2) < 0.05 and abs(b["SMB"] + 0.4) < 0.05 and abs(b["HML"] - 0.3) < 0.05
    assert res["r2"] > 0.9
    assert abs(sum(res["variance_share"].values()) - 1) < 0.05


def test_factor_returns_from_etf_prices():
    px = pd.DataFrame({s: gbm(300, seed=i).Close for i, s in enumerate(sorted(fx.FACTOR_ETFS))})
    f = fx.factor_returns(px, 0.04)
    assert list(f.columns) == ["MKT", "SMB", "HML", "MOM", "QMJ", "BAB"]
    r = px.pct_change()
    assert np.allclose(f["HML"].dropna(), (r["IWD"] - r["IWF"]).dropna())


# ------------------------------------------------------------------ screener
def test_screener_filter_language():
    df = pd.DataFrame({"pe": [10, 30, None], "mom_12_1": [20, 5, 50], "sector": ["Energy", "Technology", "Energy"]},
                      index=["A", "B", "C"])
    assert list(df.index[scr.apply_filter(df, "pe < 20 and mom_12_1 > 10")]) == ["A"]
    assert list(df.index[scr.apply_filter(df, 'sector == "Energy"')]) == ["A", "C"]
    assert list(df.index[scr.apply_filter(df, "pe > 5 or mom_12_1 > 40")]) == ["A", "B", "C"]
    assert scr.apply_filter(df, "").all()
    for bad in ["__import__('os')", "pe.real > 1", "foo > 1", "[1,2]"]:
        with pytest.raises(scr.FilterError):
            scr.apply_filter(df, bad)


def test_screener_technicals():
    px = pd.DataFrame({"X": gbm(400).Close, "Y": gbm(400, seed=2).Close})
    t = scr.technicals(px, px["X"])
    assert abs(t.loc["X", "beta"] - 1) < 1e-9
    assert t.loc["X", "from_high"] <= 0 and 0 <= t.loc["Y", "rsi14"] <= 100


# ------------------------------------------------------------------ options signals
def _chain(spot=100, T=30 / 365, r=0.03):
    from quantterm.analytics.options import bs_price

    calls, puts = [], []
    for k in np.arange(70, 131, 2.5):
        iv = 0.2 + 0.4 * (math.log(k / spot)) ** 2 - 0.1 * math.log(k / spot)
        calls.append({"strike": float(k), "mid": bs_price(spot, k, T, r, iv, "call"), "iv": iv})
        puts.append({"strike": float(k), "mid": bs_price(spot, k, T, r, iv, "put"), "iv": iv})
    return calls, puts


def test_skew_metrics_sign():
    calls, puts = _chain()
    m = osig.skew_metrics(100, 30 / 365, 0.03, calls, puts)
    assert m["rr25"] < 0  # downward sloping skew: puts richer
    assert m["put_skew_ratio"] > 1


def test_scanner_fair_prices_have_near_zero_ev_at_implied_vol():
    calls, puts = _chain()
    res = osig.scan(100, 30 / 365, 0.03, calls, puts, sigma_forecast=0.2)
    names = {x["name"] for x in res}
    assert {"Long straddle", "Short iron condor", "Bull call spread"} <= names
    straddle = next(x for x in res if x["name"] == "Long straddle")
    assert straddle["max_loss"] == pytest.approx(-straddle["net_premium"], rel=1e-3)
    assert math.isinf(straddle["max_profit"])
    short = next(x for x in res if x["name"] == "Short straddle")
    assert math.isinf(short["max_loss"]) and not short["defined_risk"]
    # long and short of the same structure have opposite EV
    assert straddle["ev"] == pytest.approx(-short["ev"], abs=1e-9)
    # at ATM IV ~20% the ATM straddle is close to fair
    assert abs(straddle["ev_iv"]) < 0.25 * straddle["net_premium"]


def test_term_structure_state():
    assert "backwardation" in osig.term_structure([{"days": 7, "atm_iv": 0.4}, {"days": 90, "atm_iv": 0.25}])["state"]
    assert "contango" in osig.term_structure([{"days": 7, "atm_iv": 0.15}, {"days": 90, "atm_iv": 0.2}])["state"]


# ------------------------------------------------------------------ store / alerts
def test_store_roundtrip(tmp_path):
    s = Store(tmp_path / "u.db")
    s.save_workspace("Morning", 4, ["WEI", "SPY GP"], {"0": "A"})
    assert s.workspaces()[0]["panels"] == ["WEI", "SPY GP"]
    n = s.save_note("thesis", "t", "AAPL")
    s.save_note("thesis v2", "t", "AAPL", n["id"])
    assert s.notes("AAPL")[0]["body"] == "thesis v2"
    a = s.add_alert("NVDA", "above", 150)
    s.mark_triggered(a["id"], 151, keep_active=False)
    assert s.alerts(active_only=True) == []
    with pytest.raises(ValueError):
        s.add_alert("NVDA", "sideways", 1)


def test_alert_conditions():
    assert alert_hit("above", 150, 151, 0, None) == 151
    assert alert_hit("above", 150, 149, 0, None) is None
    assert alert_hit("below", 150, 149, 0, None) == 149
    assert alert_hit("move", 3, 100, -3.5, None) == -3.5
    assert alert_hit("vol", 30, 100, 0, 31) == 31
    assert alert_hit("vol", 30, 100, 0, None) is None


# ------------------------------------------------------------------ brokers
def test_paper_broker_implements_protocol(tmp_path):
    assert isinstance(PaperBroker(tmp_path / "p.db"), Broker)


def _alpaca(handler):
    client = httpx.Client(base_url="https://paper-api.alpaca.markets", transport=httpx.MockTransport(handler))
    return AlpacaBroker("k", "s", paper=True, client=client)


def test_alpaca_adapter_maps_orders_and_snapshot():
    sent = {}

    def handler(req: httpx.Request):
        if req.method == "POST" and req.url.path == "/v2/orders":
            sent.update(json.loads(req.content))
            return httpx.Response(200, json={"id": "abc", "symbol": "AAPL", "side": "buy", "qty": "10", "type": "limit",
                                             "limit_price": "100", "status": "new", "submitted_at": "2026-01-02T15:00:00Z"})
        if req.url.path == "/v2/account":
            return httpx.Response(200, json={"equity": "101000", "last_equity": "100000", "cash": "50000", "buying_power": "200000"})
        if req.url.path == "/v2/positions":
            return httpx.Response(200, json=[{"symbol": "AAPL", "qty": "10", "side": "long", "market_value": "1500",
                                              "unrealized_pl": "100", "unrealized_plpc": "0.07", "avg_entry_price": "140",
                                              "current_price": "150"}])
        if req.url.path == "/v2/orders":
            return httpx.Response(200, json=[])
        return httpx.Response(404, json={"message": "nope"})

    b = _alpaca(handler)
    assert isinstance(b, Broker) and not b.live
    o = b.place("AAPL", "BUY", 10, "limit", limit_px=100)
    assert sent == {"symbol": "AAPL", "qty": "10", "side": "buy", "type": "limit", "time_in_force": "day", "limit_price": "100"}
    assert o["status"] == "OPEN" and o["side"] == "BUY"
    snap = b.snapshot({})
    assert snap["equity"] == 101000 and snap["pnl_total"] == 1000 and snap["positions"][0]["qty"] == 10
    with pytest.raises(BrokerError):
        b.reset()


def test_alpaca_errors_and_live_guard(monkeypatch):
    b = _alpaca(lambda req: httpx.Response(403, json={"message": "forbidden"}))
    with pytest.raises(BrokerError, match="403"):
        b.cancel("x")
    monkeypatch.setenv("ALPACA_PAPER", "0")
    monkeypatch.delenv("QT_ALLOW_LIVE", raising=False)
    with pytest.raises(BrokerError, match="QT_ALLOW_LIVE"):
        AlpacaBroker.from_env()


# ------------------------------------------------------------------ assistant fallback + events
def test_router_without_ai():
    out = assistant.route("why is NVDA vol rising?")
    assert out["mode"] == "router" and out["commands"][0] == "NVDA VOL"
    assert "not analysis" in out["note"]
    assert assistant.route("what does the yield curve say about recession risk")["commands"][:2] == ["GC", "ECO"]


def test_macro_calendar_sorted_and_flags_estimates():
    from quantterm.data.providers import macro_calendar

    cal = macro_calendar(120)
    assert cal == sorted(cal, key=lambda x: x["date"])
    assert all(x["estimated"] for x in cal if x["kind"] == "macro")


# ------------------------------------------------------------------ data layer robustness (found in the live check)
def test_with_fallback_retries_transient_errors(monkeypatch):
    from quantterm import config
    from quantterm.data import providers as P

    monkeypatch.setattr(config, "DATA_MODE", "live")
    monkeypatch.setattr(P.time, "sleep", lambda s: None)
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 2:
            raise RuntimeError("401 Invalid Crumb")
        return [1, 2]

    assert P._with_fallback("yahoo", flaky, lambda: None).data == [1, 2] and calls["n"] == 2

    def always_empty():
        raise P.DataError("No listed options for XYZ")

    with pytest.raises(P.DataError, match="No listed options"):
        P._with_fallback("yahoo", always_empty, lambda: None, retries=2)


def test_non_stocks_never_request_earnings():
    from quantterm.data import providers as P

    for s in ("SPY", "QQQ", "^VIX", "GC=F", "BTC-USD", "EURUSD", "XLK"):
        assert not P.is_single_stock(s), s
        with pytest.raises(P.DataError):
            P.earnings_date(s)
    assert P.is_single_stock("AAPL") and P.is_single_stock("NESN.SW")


def test_dividend_yield_is_taken_as_percent():
    assert scr.fundamentals_row({"dividendYield": 0.25})["div"] == 0.25  # 0.25%, not 25%
