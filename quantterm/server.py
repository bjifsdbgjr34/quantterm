"""QuantTerm HTTP API + static UI."""
from __future__ import annotations

import functools
import inspect
import json
import logging
import math
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.routing import APIRoute
from fastapi.staticfiles import StaticFiles

from . import config
from .analytics import backtest as bt
from .analytics import montecarlo as mc
from .analytics import quant as qt
from .analytics import options as opt
from .analytics import portfolio as pf
from .analytics import risk
from .analytics import timeseries as ts
from .data import providers as P
from . import assistant
from .analytics import factors as fx
from .analytics import multiasset as ma
from .analytics import optsignals as osig
from .analytics import screener as scr
from .analytics import walkforward as wf
from .store import ALERT_KINDS, Store, alert_hit
from .trading.broker import make_broker
from .trading.paper import PaperBroker

log = logging.getLogger("quantterm")
STATIC = Path(__file__).parent / "static"


def clean(o):
    """Make anything JSON-safe (NaN/inf -> null, numpy -> python, timestamps -> iso)."""
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, (np.floating, float)):
        f = float(o)
        return None if math.isnan(f) or math.isinf(f) else f
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, pd.Timestamp):
        return o.strftime("%Y-%m-%d") if o == o.normalize() else o.isoformat()
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    if isinstance(o, pd.Series):
        return clean(o.tolist())
    if isinstance(o, pd.DataFrame):
        return clean(o.to_dict(orient="records"))
    if isinstance(o, np.ndarray):
        return clean(o.tolist())
    return o


class SafeJSON(JSONResponse):
    def render(self, content) -> bytes:
        return json.dumps(clean(content), separators=(",", ":")).encode()


class CleanRoute(APIRoute):
    """Serialize every endpoint result with ``clean`` (bypasses FastAPI's encoder, which chokes on numpy)."""

    def __init__(self, path, endpoint, **kw):
        orig = endpoint

        if inspect.iscoroutinefunction(orig):
            @functools.wraps(orig)
            async def wrapped(*a, **k):
                res = await orig(*a, **k)
                return res if isinstance(res, Response) else SafeJSON(res)
        else:
            @functools.wraps(orig)
            def wrapped(*a, **k):
                res = orig(*a, **k)
                return res if isinstance(res, Response) else SafeJSON(res)

        super().__init__(path, wrapped, **kw)


app = FastAPI(title="QuantTerm", default_response_class=SafeJSON)
app.router.route_class = CleanRoute
app.mount("/static", StaticFiles(directory=STATIC), name="static")
try:
    broker = make_broker()
    BROKER_ERROR = None
except Exception as _e:  # noqa: BLE001  misconfigured real broker -> fall back to paper, tell the UI
    log.error("broker init failed, using paper broker: %s", _e)
    broker, BROKER_ERROR = PaperBroker(), str(_e)
store = Store()


@app.exception_handler(P.DataError)
async def _data_err(_: Request, exc: P.DataError):
    return SafeJSON({"error": str(exc)}, status_code=404)


@app.exception_handler(bt.ExprError)
async def _expr_err(_: Request, exc: bt.ExprError):
    return SafeJSON({"error": f"Expression error: {exc}"}, status_code=400)


@app.exception_handler(scr.FilterError)
async def _filter_err(_: Request, exc: scr.FilterError):
    return SafeJSON({"error": f"Filter error: {exc}"}, status_code=400)


@app.exception_handler(RuntimeError)
async def _rt_err(_: Request, exc: RuntimeError):
    return SafeJSON({"error": str(exc)}, status_code=502)


@app.exception_handler(ValueError)
async def _val_err(_: Request, exc: ValueError):
    return SafeJSON({"error": str(exc)}, status_code=400)


def _dates(idx) -> list[str]:
    return [d.strftime("%Y-%m-%d") for d in idx]


def _syms(s: str) -> list[str]:
    out = [P.norm(x) for x in s.replace(";", ",").replace(" ", ",").split(",") if x.strip()]
    if not out:
        raise HTTPException(400, "no symbols given")
    return list(dict.fromkeys(out))


def _last_price(sym: str) -> float:
    return float(P.history(sym, "5d").data["Close"].iloc[-1])


# ------------------------------------------------------------------------------------ meta
@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(status_code=204)


@app.get("/api/status")
def status():
    return {"mode": config.DATA_MODE, "yahoo": config.DATA_MODE != "demo" and P.online("yahoo"),
            "fred": config.DATA_MODE != "demo" and P.online("fred"), "time": datetime.now().isoformat(timespec="seconds"),
            "broker": broker.name, "broker_live": broker.live, "broker_error": BROKER_ERROR,
            "ai": assistant.ai_available()}


@app.get("/api/search")
def search(q: str):
    r = P.search(q)
    return {"source": r.source, "results": r.data}


# ------------------------------------------------------------------------------------ quotes
@app.get("/api/quote")
def quote(symbols: str):
    r = P.quotes(tuple(_syms(symbols)))
    return {"source": r.source, "quotes": r.data}


@app.get("/api/monitor/{name}")
def monitor(name: str):
    name = name.upper()
    if name not in P.MONITORS:
        raise HTTPException(404, f"unknown monitor {name}. Try: {', '.join(P.MONITORS)}")
    r = P.quotes(tuple(P.MONITORS[name]))
    return {"source": r.source, "name": name, "quotes": r.data}


# ------------------------------------------------------------------------------------ security
@app.get("/api/des/{symbol}")
def des(symbol: str):
    sym = P.norm(symbol)
    inf = P.info(sym)
    h = P.history(sym, "2y")
    c = h.data["Close"]
    one_y = c[c.index >= c.index[-1] - pd.Timedelta(days=365)]
    bench_sym = "^GSPC"
    try:
        b = P.history(bench_sym, "2y").data["Close"].reindex(one_y.index).ffill()
        stats = risk.perf_stats(one_y, config.RISK_FREE, b)
    except Exception:  # noqa: BLE001
        stats = risk.perf_stats(one_y, config.RISK_FREE)
    q = P.quotes((sym,)).data[0]
    return {"source": h.source, "symbol": sym, "info": inf.data, "quote": q, "stats_1y": stats,
            "realized_vol": {"10d": ts.volatility(c, 10).iloc[-1], "30d": ts.volatility(c, 21).iloc[-1],
                             "90d": ts.volatility(c, 63).iloc[-1]},
            "range_52w": {"high": float(one_y.max()), "low": float(one_y.min())},
            "spark": {"dates": _dates(one_y.index), "close": one_y.tolist()}}


@app.get("/api/chart/{symbol}")
def chart(symbol: str, period: str = "1y", interval: str = "1d", ind: str = "sma50,sma200,bb,rsi,macd"):
    sym = P.norm(symbol)
    # fetch extra history so long indicators are warm at the start of the window
    fetch = {"1mo": "6mo", "3mo": "1y", "6mo": "2y", "ytd": "2y", "1y": "2y", "2y": "5y", "5y": "10y"}.get(period, period)
    r = P.history(sym, fetch, interval)
    df = r.data
    c, h, low, v = df["Close"], df["High"], df["Low"], df["Volume"]
    out: dict = {"overlays": {}, "panels": {}}
    for tok in [t.strip().lower() for t in ind.split(",") if t.strip()]:
        if tok.startswith("sma") and tok[3:].isdigit():
            out["overlays"][tok.upper()] = ts.sma(c, int(tok[3:]))
        elif tok.startswith("ema") and tok[3:].isdigit():
            out["overlays"][tok.upper()] = ts.ema(c, int(tok[3:]))
        elif tok == "bb":
            bb = ts.bollinger_bands(c)
            out["overlays"]["BB Upper"], out["overlays"]["BB Lower"] = bb["upper"], bb["lower"]
        elif tok == "vwap":
            out["overlays"]["VWAP20"] = ts.vwap(c, v)
        elif tok == "rsi":
            out["panels"]["RSI(14)"] = {"RSI": ts.rsi(c)}
        elif tok == "macd":
            m = ts.macd(c)
            out["panels"]["MACD"] = {"MACD": m["macd"], "Signal": m["signal"], "Hist": m["hist"]}
        elif tok == "atr":
            out["panels"]["ATR(14)"] = {"ATR": ts.atr(h, low, c)}
        elif tok == "vol":
            out["panels"]["Realized Vol"] = {"21d": ts.volatility(c, 21), "63d": ts.volatility(c, 63)}
        elif tok == "stoch":
            s = ts.stochastic(h, low, c)
            out["panels"]["Stochastic"] = {"%K": s["k"], "%D": s["d"]}
        elif tok == "dd":
            out["panels"]["Drawdown %"] = {"DD": ts.drawdown(c) * 100}
    start = P.period_start(period)
    mask = df.index >= pd.Timestamp(start)
    if mask.sum() < 2:
        mask[:] = True
    d = df[mask]
    return {
        "source": r.source, "symbol": sym, "period": period, "dates": _dates(d.index),
        "open": d["Open"], "high": d["High"], "low": d["Low"], "close": d["Close"], "volume": d["Volume"],
        "overlays": {k: s[mask] for k, s in out["overlays"].items()},
        "panels": {k: {kk: s[mask] for kk, s in v.items()} for k, v in out["panels"].items()},
    }


@app.get("/api/hp/{symbol}")
def hp(symbol: str, period: str = "3mo", interval: str = "1d"):
    r = P.history(P.norm(symbol), period, interval)
    df = r.data.copy()
    df["Change %"] = df["Close"].pct_change() * 100
    df = df.iloc[::-1]
    return {"source": r.source, "rows": [{"date": d.strftime("%Y-%m-%d"), **row} for d, row in
                                         zip(df.index, df.to_dict(orient="records"))]}


@app.get("/api/fa/{symbol}")
def fa(symbol: str, quarterly: bool = False):
    r = P.financials(P.norm(symbol), quarterly)
    return {"source": r.source, **r.data}


@app.get("/api/news/{symbol}")
def news(symbol: str):
    r = P.news(P.norm(symbol))
    return {"source": r.source, "items": r.data}


# ------------------------------------------------------------------------------------ cross-asset analytics
@app.get("/api/compare")
def compare(symbols: str, period: str = "1y"):
    syms = _syms(symbols)
    r = P.closes(tuple(syms), period)
    df = r.data.ffill().dropna(how="any")
    if df.empty:
        raise HTTPException(404, "no overlapping history")
    reb = ts.rebase(df)
    bench = df.iloc[:, 0]
    stats = {s: risk.perf_stats(df[s], config.RISK_FREE, bench if s != df.columns[0] else None) for s in df.columns}
    return {"source": r.source, "dates": _dates(df.index), "series": {s: reb[s] for s in df.columns}, "stats": stats}


@app.get("/api/corr")
def corr(symbols: str, period: str = "1y", window: int = 63):
    syms = _syms(symbols)
    r = P.closes(tuple(syms), period)
    df = r.data.ffill().dropna(how="any")
    rets = df.pct_change().dropna()
    m = rets.corr()
    rolling = {}
    cols = list(df.columns)
    for i in range(min(len(cols), 4)):
        for j in range(i + 1, min(len(cols), 4)):
            rolling[f"{cols[i]} / {cols[j]}"] = rets[cols[i]].rolling(window).corr(rets[cols[j]])
    return {"source": r.source, "symbols": cols, "matrix": m.to_numpy(), "dates": _dates(rets.index),
            "rolling": rolling, "window": window}


@app.get("/api/beta")
def beta(symbol: str, bench: str = "^GSPC", period: str = "2y", window: int = 63):
    s, b = P.norm(symbol), P.norm(bench)
    r = P.closes((s, b), period)
    df = r.data.ffill().dropna()
    reg = ts.regression(df[s], df[b])
    rb = ts.beta(df[s], df[b], window)
    rc = ts.correlation(df[s], df[b], window)
    return {"source": r.source, "symbol": s, "bench": b, "regression": reg, "dates": _dates(rb.index),
            "rolling_beta": rb, "rolling_corr": rc, "window": window}


@app.get("/api/vol/{symbol}")
def vol(symbol: str, period: str = "2y"):
    sym = P.norm(symbol)
    r = P.history(sym, "10y" if period in ("5y", "10y", "max") else "5y")
    df = r.data
    c = df["Close"]
    start = pd.Timestamp(P.period_start(period))
    series = {"RV 10d": ts.volatility(c, 10), "RV 21d": ts.volatility(c, 21), "RV 63d": ts.volatility(c, 63),
              "EWMA (0.94)": ts.ewma_volatility(c), "Parkinson 21d": ts.parkinson_volatility(df["High"], df["Low"], 21)}
    mask = c.index >= start
    rets = ts.returns(c).dropna()
    return {"source": r.source, "symbol": sym, "dates": _dates(c.index[mask]),
            "series": {k: v[mask] for k, v in series.items()}, "cone": ts.vol_cone(c),
            "hist": {"returns": (rets[rets.index >= start] * 100).tolist()},
            "var": risk.var_report(rets[rets.index >= start], 1.0)}


# ------------------------------------------------------------------------------------ options
@app.get("/api/options/{symbol}/expiries")
def expiries(symbol: str):
    r = P.option_expiries(P.norm(symbol))
    return {"source": r.source, "expiries": r.data}


def _future_expiries(exps: list[str]) -> list[str]:
    """Drop expiries on/before today: 0DTE chains are thin, noisy and Yahoo often returns them empty."""
    today = date.today().isoformat()
    fut = [e for e in exps if e > today]
    return fut or exps


def _tau(expiry: str) -> float:
    return max((date.fromisoformat(expiry) - date.today()).days + 0.7, 0.5) / 365.0


@app.get("/api/options/{symbol}")
def chain(symbol: str, expiry: str | None = None, rate: float | None = None):
    sym = P.norm(symbol)
    exps = P.option_expiries(sym).data
    fut = _future_expiries(exps)
    expiry = expiry or fut[min(2, len(fut) - 1)]
    r = P.option_chain(sym, expiry)
    spot = _last_price(sym)
    rf = config.RISK_FREE if rate is None else rate
    T = _tau(expiry)
    rows = {}
    for k in ("calls", "puts"):
        kind = k[:-1]
        df = r.data[k].copy()
        recs = []
        for rec in df.to_dict(orient="records"):
            mid = (rec["bid"] + rec["ask"]) / 2 if rec.get("bid") and rec.get("ask") else rec.get("lastPrice")
            iv = opt.implied_vol(mid, spot, rec["strike"], T, rf, kind) if mid else None
            iv = iv or rec.get("impliedVolatility")
            g = opt.greeks(spot, rec["strike"], T, rf, iv, kind) if iv and iv > 0.01 else {}
            recs.append(rec | {"mid": mid, "iv": iv, "delta": g.get("delta"), "gamma": g.get("gamma"),
                               "vega": g.get("vega"), "theta": g.get("theta")})
        rows[k] = recs
    # put/call stats
    cv = sum((x.get("volume") or 0) for x in rows["calls"])
    pv = sum((x.get("volume") or 0) for x in rows["puts"])
    coi = sum((x.get("openInterest") or 0) for x in rows["calls"])
    poi = sum((x.get("openInterest") or 0) for x in rows["puts"])
    atm = min(rows["calls"], key=lambda x: abs(x["strike"] - spot)) if rows["calls"] else None
    straddle = None
    if atm:
        atm_put = min(rows["puts"], key=lambda x: abs(x["strike"] - atm["strike"]))
        if atm.get("mid") and atm_put.get("mid"):
            straddle = atm["mid"] + atm_put["mid"]
    # max pain
    strikes = sorted({x["strike"] for x in rows["calls"]} | {x["strike"] for x in rows["puts"]})
    pain = []
    for s in strikes:
        loss = sum(max(s - c["strike"], 0) * (c.get("openInterest") or 0) for c in rows["calls"]) + \
               sum(max(p["strike"] - s, 0) * (p.get("openInterest") or 0) for p in rows["puts"])
        pain.append(loss)
    return {"source": r.source, "symbol": sym, "spot": spot, "expiry": expiry, "expiries": exps, "T": T, "rate": rf,
            "calls": rows["calls"], "puts": rows["puts"],
            "summary": {"call_volume": cv, "put_volume": pv, "pc_volume": pv / cv if cv else None,
                        "call_oi": coi, "put_oi": poi, "pc_oi": poi / coi if coi else None,
                        "atm_iv": atm.get("iv") if atm else None, "straddle": straddle,
                        "implied_move_pct": straddle / spot * 100 if straddle else None,
                        "max_pain": strikes[int(np.argmin(pain))] if pain else None}}


@app.get("/api/volsurface/{symbol}")
def volsurface(symbol: str, n: int = 8):
    sym = P.norm(symbol)
    exps = _future_expiries(P.option_expiries(sym).data)
    spot = _last_price(sym)
    src = "yahoo"
    # pick a spread of expiries rather than only the front weeklies
    picks = exps if len(exps) <= n else [exps[int(i)] for i in np.linspace(0, len(exps) - 1, n)]
    smiles = []
    for e in picks:
        try:
            r = P.option_chain(sym, e)
        except P.DataError as ex:  # one bad expiry (Yahoo hiccup) shouldn't kill the surface
            log.warning("volsurface %s %s skipped: %s", sym, e, ex)
            continue
        src = r.source
        T = _tau(e)
        pts = []
        # OTM options only: puts below spot, calls above
        for kind, df in (("put", r.data["puts"]), ("call", r.data["calls"])):
            for rec in df.to_dict(orient="records"):
                k = rec["strike"]
                if (kind == "put" and k > spot) or (kind == "call" and k < spot):
                    continue
                if not (0.6 * spot <= k <= 1.4 * spot):
                    continue
                mid = (rec["bid"] + rec["ask"]) / 2 if rec.get("bid") and rec.get("ask") else rec.get("lastPrice")
                iv = opt.implied_vol(mid, spot, k, T, config.RISK_FREE, kind) if mid else None
                if iv and 0.02 < iv < 3:
                    pts.append({"strike": k, "moneyness": k / spot, "iv": iv})
        pts.sort(key=lambda x: x["strike"])
        if pts:
            atm = min(pts, key=lambda x: abs(x["moneyness"] - 1))
            smiles.append({"expiry": e, "T": T, "days": int(round(T * 365)), "points": pts, "atm_iv": atm["iv"]})
    return {"source": src, "symbol": sym, "spot": spot, "smiles": smiles}


@app.post("/api/pricer")
def pricer(body: dict = Body(...)):
    S, K = float(body["spot"]), float(body["strike"])
    T = float(body.get("days", 30)) / 365
    r_, q = float(body.get("rate", config.RISK_FREE)), float(body.get("div", 0))
    sig = float(body.get("vol", 0.2))
    kind = body.get("kind", "call")
    g = opt.greeks(S, K, T, r_, sig, kind, q)
    amer = opt.binomial_price(S, K, T, r_, sig, kind, q, american=True, steps=400)
    # sensitivity grids
    spots = np.linspace(S * 0.7, S * 1.3, 61)
    by_spot = {"spot": spots, "price": [opt.bs_price(s, K, T, r_, sig, kind, q) for s in spots],
               "delta": [opt.greeks(s, K, T, r_, sig, kind, q)["delta"] for s in spots],
               "gamma": [opt.greeks(s, K, T, r_, sig, kind, q)["gamma"] for s in spots]}
    legs = body.get("legs") or [{"kind": kind, "strike": K, "qty": 1, "T": T, "sigma": sig, "premium": g["price"]}]
    for leg in legs:
        leg.setdefault("T", T)
        leg.setdefault("sigma", sig)
        if leg["kind"] != "stock" and "premium" not in leg:
            leg["premium"] = opt.bs_price(S, leg["strike"], leg["T"], r_, leg["sigma"], leg["kind"], q)
    pay = opt.payoff_profile(legs, S, r_)
    return {"european": g, "american": amer, "early_ex_premium": amer - g["price"], "by_spot": by_spot,
            "payoff": pay, "legs": legs}


# ------------------------------------------------------------------------------------ backtest
@app.get("/api/strategies")
def strategies():
    return {k: {"label": v["label"], "params": v["params"], "expr": v["expr"]} for k, v in bt.STRATEGIES.items()} | \
        {"_functions": sorted(bt.FUNCS)}


@app.post("/api/backtest")
def backtest(body: dict = Body(...)):
    sym = P.norm(body.get("symbol", "SPY"))
    period = body.get("period", "5y")
    r = P.history(sym, period)
    expr = body.get("expr") or bt.strategy_expr(body.get("strategy", "sma_cross"), body.get("params"))
    cfg = bt.BacktestConfig(expr=expr, cost_bps=float(body.get("cost_bps", 5)),
                            allow_short=bool(body.get("allow_short", False)),
                            vol_target=float(body["vol_target"]) / 100 if body.get("vol_target") else None,
                            max_leverage=float(body.get("max_leverage", 2)), rf=config.RISK_FREE)
    res = bt.run(r.data, cfg)
    dd = ts.drawdown(res.equity) * 100
    bdd = ts.drawdown(res.benchmark) * 100
    return {"source": r.source, "symbol": sym, "expr": expr, "dates": _dates(res.equity.index),
            "equity": res.equity, "benchmark": res.benchmark, "drawdown": dd, "bench_drawdown": bdd,
            "position": res.position, "stats": res.stats, "bench_stats": res.bench_stats,
            "trades": res.trades[-200:], "n_trades": len(res.trades), "monthly": res.monthly}


@app.post("/api/backtest/optimize")
def backtest_optimize(body: dict = Body(...)):
    sym = P.norm(body.get("symbol", "SPY"))
    r = P.history(sym, body.get("period", "10y"))
    strat = body.get("strategy", "sma_cross")
    grid = body.get("grid") or {"sma_cross": {"fast": [10, 20, 30, 50, 75, 100], "slow": [100, 150, 200, 250, 300]},
                                "ema_cross": {"fast": [5, 8, 12, 20, 30], "slow": [26, 40, 60, 100, 150]},
                                "momentum": {"lookback": [21, 42, 63, 126, 189, 252]},
                                "rsi_reversion": {"entry": [20, 25, 30, 35, 40], "exit": [50, 55, 60, 65, 70]},
                                "bollinger_reversion": {"n": [10, 15, 20, 30, 50], "k": [1.5, 2, 2.5, 3]},
                                "donchian_breakout": {"entry": [20, 40, 55, 100], "exit": [10, 20, 40]},
                                }.get(strat)
    if not grid:
        raise HTTPException(400, f"no default grid for {strat}")
    metric = body.get("metric", "Sharpe")
    res = bt.optimize_grid(r.data, strat, grid, float(body.get("cost_bps", 5)), metric)
    return {"source": r.source, "symbol": sym, "strategy": strat, "metric": metric, "grid": grid, "results": res}


# ------------------------------------------------------------------------------------ portfolio
@app.post("/api/portfolio")
def portfolio(body: dict = Body(...)):
    syms = _syms(body.get("symbols", "SPY,TLT,GLD"))
    if len(syms) < 2:
        raise HTTPException(400, "need at least 2 symbols")
    period = body.get("period", "3y")
    method = body.get("method", "max_sharpe")
    long_only = bool(body.get("long_only", True))
    max_w = float(body.get("max_weight", 1.0))
    value = float(body.get("value", 1_000_000))
    r = P.closes(tuple(syms), period)
    px = r.data.ffill().dropna()
    if len(px) < 60:
        raise HTTPException(400, "not enough overlapping history (need 60+ days)")
    mu, cov, rets = pf.estimate(px)
    if body.get("weights"):
        w = pd.Series({P.norm(k): float(v) for k, v in body["weights"].items()}).reindex(px.columns).fillna(0)
        w = w / w.sum()
        method = "custom"
    else:
        w = pf.optimize(mu, cov, method, config.RISK_FREE, long_only, max_w)
    compare = {}
    for m in ("equal", "inverse_vol", "min_var", "max_sharpe", "risk_parity", "max_diversification"):
        try:
            wm = pf.optimize(mu, cov, m, config.RISK_FREE, long_only, max_w)
            ret, vol_ = float(wm @ mu), float(np.sqrt(wm @ cov @ wm))
            compare[m] = {"weights": wm.to_dict(), "ret": ret, "vol": vol_, "sharpe": (ret - config.RISK_FREE) / vol_}
        except Exception as e:  # noqa: BLE001
            log.warning("optimizer %s failed: %s", m, e)
    port_r = rets @ w
    eq = pf.backtest_weights(px, w)
    bench = ts.rebase(px.iloc[:, 0])
    betas = {}
    try:
        spx = P.closes(("^GSPC",), period).data.iloc[:, 0].reindex(px.index).ffill()
        betas = {s: ts.beta(px[s], spx) for s in px.columns}
    except Exception:  # noqa: BLE001
        pass
    rc = pf.risk_contributions(w, cov)
    return {
        "source": r.source, "symbols": list(px.columns), "method": method, "weights": w.to_dict(),
        "exp_return": float(w @ mu), "exp_vol": float(np.sqrt(w @ cov @ w)),
        "assets": [{"symbol": s, "mu": mu[s], "vol": float(np.sqrt(cov.loc[s, s])), "beta": betas.get(s)} for s in px.columns],
        "frontier": pf.efficient_frontier(mu, cov, 25, long_only, max_w), "compare": compare,
        "risk": rc.reset_index(names="symbol").to_dict(orient="records"),
        "corr": {"symbols": list(px.columns), "matrix": rets.corr().to_numpy()},
        "dates": _dates(eq.index), "equity": eq, "bench": bench, "bench_name": px.columns[0],
        "stats": risk.perf_stats(eq, config.RISK_FREE), "var": risk.var_report(port_r, value),
        "var_10d": risk.var_report(port_r, value, horizon=10),
        "stress": risk.stress_scenarios(w.to_dict(), betas, value) if betas else [],
        "allocation": [{"symbol": s, "weight": w[s], "value": w[s] * value,
                        "shares": w[s] * value / px[s].iloc[-1], "price": px[s].iloc[-1]} for s in px.columns],
    }


# ------------------------------------------------------------------------------------ macro
@app.get("/api/curve")
def curve():
    r = P.yield_curve()
    return {"source": r.source, **r.data}


@app.get("/api/eco")
def eco(ids: str = "FEDFUNDS,CPIAUCSL,UNRATE,T10Y2Y", start: str = "2005-01-01", yoy: str = "CPIAUCSL,CPILFESL,PCEPILFE,GDPC1,PAYEMS,M2SL,INDPRO"):
    out, src = {}, "fred"
    yoy_set = {x.strip().upper() for x in yoy.split(",")}
    for sid in [x.strip().upper() for x in ids.split(",") if x.strip()]:
        r = P.fred(sid, start)
        src = r.source if r.source == "demo" else src
        s = r.data
        transformed = False
        if sid in yoy_set and len(s) > 13:
            freq = pd.infer_freq(s.index[-12:]) or ""
            lag = 4 if freq.startswith("Q") else 12
            s = (s / s.shift(lag) - 1) * 100
            transformed = True
        s = s.dropna()
        out[sid] = {"name": P.MACRO_SERIES.get(sid, sid) + (" (YoY %)" if transformed else ""), "dates": _dates(s.index),
                    "values": s.tolist(), "last": float(s.iloc[-1]) if len(s) else None,
                    "last_date": s.index[-1].strftime("%Y-%m-%d") if len(s) else None}
    return {"source": src, "series": out, "catalog": P.MACRO_SERIES}


# ------------------------------------------------------------------------------------ paper trading
def _paper_prices(extra: list[str] | None = None) -> dict[str, float]:
    syms = sorted(set(broker.symbols() + (extra or [])))
    if not syms:
        return {}
    return {q["symbol"]: q["last"] for q in P.quotes(tuple(syms)).data if q.get("last") is not None}


@app.get("/api/paper")
def paper():
    prices = _paper_prices()
    broker.process_open_orders(prices)
    return broker.snapshot(prices) | {"broker": broker.name, "live": broker.live, "broker_error": BROKER_ERROR}


@app.post("/api/paper/order")
def paper_order(body: dict = Body(...)):
    sym = P.norm(body["symbol"])
    last = _paper_prices([sym]).get(sym)
    o = broker.place(sym, body["side"], float(body["qty"]), body.get("type", "market"),
                     float(body["limit_px"]) if body.get("limit_px") else None,
                     float(body["stop_px"]) if body.get("stop_px") else None, last, body.get("note", ""))
    return {"order": o}


@app.delete("/api/paper/order/{oid}")
def paper_cancel(oid: str):
    broker.cancel(int(oid) if oid.isdigit() else oid)
    return {"ok": True}


@app.post("/api/paper/reset")
def paper_reset(body: dict = Body(default={})):
    broker.reset(float(body["cash"]) if body.get("cash") else None)
    return {"ok": True}


# ------------------------------------------------------------------------------------ Monte Carlo
@app.post("/api/mc")
def monte_carlo(body: dict = Body(...)):
    """Price-path simulation for one asset."""
    sym = P.norm(body.get("symbol", "SPY"))
    r = P.history(sym, body.get("period", "5y"))
    close = r.data["Close"]
    lr = np.log(close / close.shift()).dropna()
    cal = mc.calibrate(lr)
    model = body.get("model", "gbm")
    days = int(body.get("days", 252))
    n = int(min(int(body.get("paths", 5000)), 50_000))
    mu = float(body["mu"]) / 100 if body.get("mu") not in (None, "") else cal["mu"] + 0.5 * cal["sigma"] ** 2
    sigma = float(body["sigma"]) / 100 if body.get("sigma") not in (None, "") else cal["sigma"]
    params = dict(cal)
    garch_info = None
    if model == "garch":
        g = qt.fit_garch(lr)
        params |= g["sim_params"]
        garch_info = {k: g[k] for k in ("alpha", "beta", "persistence", "long_run_vol", "current_vol")}
    S0 = float(close.iloc[-1])
    up = S0 * (1 + float(body["up_pct"]) / 100) if body.get("up_pct") else None
    down = S0 * (1 - float(body["down_pct"]) / 100) if body.get("down_pct") else None
    paths = mc.simulate_paths(S0, days, n, model, mu, sigma, lr, params, seed=body.get("seed"))
    s = mc.summarize_paths(paths, S0, up, down)
    start = close.index[-1]
    fut = pd.bdate_range(start, periods=days + 1)
    hist = close.iloc[-min(len(close), days):]
    return {"source": r.source, "symbol": sym, "model": model, "S0": S0, "mu": mu, "sigma": sigma, "days": days,
            "paths": n, "calibration": cal, "garch": garch_info, "up": up, "down": down,
            "dates": _dates(fut), "hist_dates": _dates(hist.index), "hist": hist, **s}


@app.post("/api/mc/option")
def mc_option(body: dict = Body(...)):
    S0 = float(body.get("spot", 100))
    return mc.mc_option(S0, float(body.get("strike", S0)), float(body.get("days", 90)) / 365,
                        float(body.get("rate", config.RISK_FREE)), float(body.get("vol", 0.2)),
                        body.get("kind", "call"), body.get("style", "european"),
                        float(body["barrier"]) if body.get("barrier") else None,
                        int(min(int(body.get("paths", 50000)), 400_000)), q=float(body.get("div", 0)),
                        antithetic=bool(body.get("antithetic", True)))


@app.post("/api/mc/portfolio")
def mc_portfolio(body: dict = Body(...)):
    syms = _syms(body.get("symbols", "SPY,TLT,GLD"))
    r = P.closes(tuple(syms), body.get("period", "10y"))
    px = r.data.ffill().dropna()
    rets = px.pct_change().dropna()
    if body.get("weights"):
        w = pd.Series({P.norm(k): float(v) for k, v in body["weights"].items()}).reindex(px.columns).fillna(0)
    else:
        w = pd.Series(1 / len(px.columns), index=px.columns)
    w = w / w.sum()
    s = mc.portfolio_mc(rets, w, float(body.get("value", 1_000_000)), float(body.get("years", 10)),
                        int(min(int(body.get("paths", 3000)), 20000)), float(body.get("contribution", 0)),
                        body.get("method", "bootstrap"), True,
                        float(body["goal"]) if body.get("goal") else None)
    return {"source": r.source, "symbols": list(px.columns), "weights": w.to_dict(),
            "history_from": px.index[0].strftime("%Y-%m-%d"), **s}


# ------------------------------------------------------------------------------------ quant tools
@app.get("/api/garch/{symbol}")
def garch(symbol: str, period: str = "5y", horizon: int = 63):
    sym = P.norm(symbol)
    r = P.history(sym, period)
    c = r.data["Close"]
    lr = np.log(c / c.shift()).dropna()
    g = qt.fit_garch(lr, horizon)
    rv = ts.volatility(c, 21).reindex(g["cond_vol"].index)
    return {"source": r.source, "symbol": sym, "dates": _dates(g["cond_vol"].index), "cond_vol": g["cond_vol"],
            "realized_21d": rv, "forecast": g["forecast"],
            **{k: g[k] for k in ("omega", "alpha", "beta", "persistence", "long_run_vol", "half_life_days",
                                 "current_vol", "loglik", "aic", "bic")}}


@app.get("/api/stats/{symbol}")
def stats(symbol: str, period: str = "5y"):
    sym = P.norm(symbol)
    r = P.history(sym, period)
    c = r.data["Close"]
    return {"source": r.source, "symbol": sym, **qt.return_stats(c), "kelly": qt.kelly(c, config.RISK_FREE)}


@app.get("/api/pairs")
def pairs(y: str, x: str, period: str = "3y", entry: float = 2.0, exit: float = 0.5, window: int = 60):
    sy, sx = P.norm(y), P.norm(x)
    r = P.closes((sy, sx), period)
    df = r.data.ffill().dropna()
    res = qt.pairs(df[sy], df[sx], entry, exit, window)
    return {"source": r.source, "y": sy, "x": sx, **{k: v for k, v in res.items() if k != "dates"},
            "dates": _dates(res["dates"]), "norm_y": ts.rebase(df[sy]), "norm_x": ts.rebase(df[sx])}


@app.get("/api/pca")
def pca_ep(symbols: str, period: str = "2y", k: int = 5):
    syms = _syms(symbols)
    if len(syms) < 3:
        raise HTTPException(400, "PCA needs at least 3 symbols")
    r = P.closes(tuple(syms), period)
    rets = r.data.ffill().dropna().pct_change().dropna()
    res = qt.pca(rets, k)
    return {"source": r.source, **res, "dates": _dates(res["dates"])}


# ------------------------------------------------------------------------------------ walk-forward / multi-asset
@app.post("/api/walkforward")
def walkforward(body: dict = Body(...)):
    sym = P.norm(body.get("symbol", "SPY"))
    r = P.history(sym, body.get("period", "max"))
    res = wf.walk_forward(r.data, body.get("strategy", "sma_cross"), body.get("grid"),
                          int(body.get("train_days", 756)), int(body.get("test_days", 126)),
                          float(body.get("cost_bps", 5)), body.get("metric", "Sharpe"),
                          bool(body.get("anchored", False)), config.RISK_FREE)
    eq, b, ins = res.pop("equity"), res.pop("benchmark"), res.pop("in_sample_equity")
    return {"source": r.source, "symbol": sym, **res, "dates": _dates(eq.index), "equity": eq,
            "benchmark": b.reindex(eq.index), "in_sample_equity": ins.reindex(eq.index)}


@app.get("/api/mbt/strategies")
def mbt_strategies():
    return {"strategies": ma.STRATEGIES, "rebalance": list(ma.REBAL)}


@app.post("/api/mbt")
def mbt(body: dict = Body(...)):
    syms = _syms(body.get("symbols", "SPY,EFA,EEM,TLT,GLD,DBC,VNQ"))
    if len(syms) < 2:
        raise HTTPException(400, "need at least 2 symbols")
    r = P.closes(tuple(syms), body.get("period", "10y"))
    bench_sym = body.get("bench")
    bench = None
    if bench_sym:
        bs = P.norm(bench_sym)
        bench = P.closes((bs,), body.get("period", "10y")).data.iloc[:, 0].rename(bs)
    res = ma.run(r.data, body.get("strategy", "xs_momentum"), int(body.get("top_n", 3)), int(body.get("lookback", 126)),
                 int(body.get("skip", 21)), body.get("rebalance", "monthly"), float(body.get("cost_bps", 5)),
                 config.RISK_FREE, bench)
    eq, bn, w = res.pop("equity"), res.pop("benchmark"), res.pop("weights")
    return {"source": r.source, "symbols": list(r.data.columns), **res, "dates": _dates(eq.index), "equity": eq,
            "benchmark": bn, "drawdown": ts.drawdown(eq) * 100,
            "weights": {c: w[c] for c in w.columns}}


# ------------------------------------------------------------------------------------ factor model
@app.get("/api/factors")
def factors(symbols: str, weights: str = "", period: str = "3y", window: int = 126):
    syms = _syms(symbols)
    need = tuple(sorted(set(syms) | fx.FACTOR_ETFS))
    r = P.closes(need, period)
    px = r.data.ffill().dropna(how="all")
    if weights:
        w = pd.Series([float(x) for x in weights.split(",")], index=syms[: len(weights.split(","))])
    else:
        w = pd.Series(1 / len(syms), index=syms)
    w = w / w.sum()
    rets = px[w.index].pct_change()
    asset = (rets * w).sum(axis=1, min_count=len(w))
    fac = fx.factor_returns(px, config.RISK_FREE)
    res = fx.factor_model(asset, fac, config.RISK_FREE, window)
    att, rol = res.pop("attribution"), res.pop("rolling")
    label = syms[0] if len(syms) == 1 else "Portfolio (" + ", ".join(f"{s} {v:.0%}" for s, v in w.items()) + ")"
    return {"source": r.source, "label": label, "symbols": syms, "weights": w.to_dict(), **res,
            "attribution": {"dates": _dates(att["dates"]), "series": att["series"]},
            "rolling": {"dates": _dates(rol["dates"]), "series": rol["series"]} if rol else None,
            "factor_desc": fx.FACTOR_DESC}


# ------------------------------------------------------------------------------------ screener & heatmap
def _universe_frame(name: str) -> tuple[pd.DataFrame, str]:
    syms = tuple(P.universe(name))
    r = P.closes(syms + ("^GSPC",), "2y")
    px = r.data.ffill()
    tech = scr.technicals(px.drop(columns=["^GSPC"], errors="ignore"), px.get("^GSPC"))
    inf = P.bulk_info(syms)
    fund = pd.DataFrame({s: scr.fundamentals_row(d) for s, d in inf.data.items()}).T
    df = tech.join(fund, how="left")
    df["sector"] = df["sector"].fillna(pd.Series(P.UNIVERSE_SECTOR)).fillna("Other")
    src = "demo" if "demo" in (r.source, inf.source) else r.source
    return df, src


@app.post("/api/screener")
def screener(body: dict = Body(default={})):
    df, src = _universe_frame(body.get("universe", "SPX100"))
    mask = scr.apply_filter(df, body.get("filter", ""))
    out = df[mask].copy()
    sort = body.get("sort", "mom_12_1")
    if sort in out:
        out = out.sort_values(sort, ascending=bool(body.get("ascending", False)), na_position="last")
    out = out.reset_index(names="symbol")
    return {"source": src, "count": int(mask.sum()), "total": len(df), "rows": out.to_dict(orient="records"),
            "fields": scr.METRICS, "presets": scr.PRESETS}


@app.get("/api/heatmap")
def heatmap(universe: str = "SPX100", metric: str = "chg_1d"):
    df, src = _universe_frame(universe)
    if metric not in df:
        raise HTTPException(400, f"metric must be one of {', '.join(c for c in df.columns if c not in ('name', 'sector'))}")
    rows = [{"symbol": s, "sector": r["sector"], "name": r.get("name"), "value": r[metric],
             "mcap_b": r.get("mcap_b"), "last": r["last"]} for s, r in df.iterrows()]
    sectors = df.groupby("sector")[metric].mean().sort_values(ascending=False)
    return {"source": src, "metric": metric, "rows": rows, "sectors": sectors.to_dict(),
            "breadth": {"up": int((df["chg_1d"] > 0).sum()), "down": int((df["chg_1d"] < 0).sum()),
                        "above_200": float(df["above_200"].mean() * 100)}}


# ------------------------------------------------------------------------------------ intraday & streaming
@app.get("/api/intraday/{symbol}")
def intraday(symbol: str, interval: str = "5m", days: int = 1):
    r = P.intraday(symbol, interval, days)
    df = r.data
    prev = P.history(symbol, "5d").data["Close"]
    prev_close = float(prev.iloc[-2]) if len(prev) > 1 else float(df["Open"].iloc[0])
    vwap = (df["Close"] * df["Volume"]).cumsum() / df["Volume"].cumsum().replace(0, np.nan)
    return {"source": r.source, "symbol": P.norm(symbol), "interval": interval,
            "times": [t.strftime("%Y-%m-%d %H:%M") for t in df.index], "open": df["Open"], "high": df["High"],
            "low": df["Low"], "close": df["Close"], "volume": df["Volume"], "vwap": vwap, "prev_close": prev_close,
            "last": float(df["Close"].iloc[-1]), "pct": (float(df["Close"].iloc[-1]) / prev_close - 1) * 100}


@app.get("/api/live")
def live(symbols: str):
    r = P.live_quotes(tuple(_syms(symbols)))
    return {"source": r.source, "quotes": r.data}


@app.get("/api/stream")
async def stream(symbols: str, interval: float = 5.0):
    """Server-sent events: a quote snapshot every ``interval`` seconds (min 2s) until the client disconnects."""
    import asyncio

    from fastapi.responses import StreamingResponse
    from starlette.concurrency import run_in_threadpool

    syms = tuple(_syms(symbols))[:40]
    interval = max(2.0, float(interval))

    async def gen():
        while True:
            try:
                r = await run_in_threadpool(P.live_quotes, syms)
                yield f"data: {json.dumps(clean({'source': r.source, 'quotes': r.data}))}\n\n"
            except Exception as e:  # noqa: BLE001
                yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"
            await asyncio.sleep(interval)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


# ------------------------------------------------------------------------------------ alerts
@app.get("/api/alerts")
def alerts_list():
    return {"alerts": store.alerts(), "kinds": ALERT_KINDS}


@app.post("/api/alerts")
def alerts_add(body: dict = Body(...)):
    return {"alert": store.add_alert(P.norm(body["symbol"]), body.get("kind", "above"), float(body["level"]),
                                     body.get("note", ""), bool(body.get("repeat", False)))}


@app.delete("/api/alerts/{aid}")
def alerts_delete(aid: int):
    store.delete_alert(aid)
    return {"ok": True}


@app.get("/api/alerts/check")
def alerts_check():
    """Evaluate active alerts against current prices. The UI polls this and raises notifications."""
    active = store.alerts(active_only=True)
    if not active:
        return {"fired": [], "source": None}
    syms = tuple(sorted({a["symbol"] for a in active}))
    q = P.live_quotes(syms)
    last = {x["symbol"]: x for x in q.data}
    rv: dict[str, float | None] = {}
    fired = []
    for a in active:
        s = a["symbol"]
        if a["kind"] == "vol" and s not in rv:
            try:
                rv[s] = float(ts.volatility(P.history(s, "3mo").data["Close"], 10).iloc[-1])
            except Exception:  # noqa: BLE001
                rv[s] = None
        x = last.get(s, {})
        hit = alert_hit(a["kind"], a["level"], x.get("last"), x.get("pct"), rv.get(s))
        if hit is not None:
            store.mark_triggered(a["id"], hit, keep_active=bool(a["repeat"]))
            fired.append(a | {"value": hit, "last": x.get("last")})
    return {"fired": fired, "source": q.source, "checked": len(active)}


# ------------------------------------------------------------------------------------ workspaces & notes
@app.get("/api/workspaces")
def ws_list():
    return {"workspaces": store.workspaces()}


@app.post("/api/workspaces")
def ws_save(body: dict = Body(...)):
    return store.save_workspace(body["name"], int(body.get("layout", 4)), list(body.get("panels", [])), body.get("links"))


@app.delete("/api/workspaces/{name}")
def ws_delete(name: str):
    store.delete_workspace(name)
    return {"ok": True}


@app.get("/api/notes")
def notes_list(symbol: str | None = None):
    return {"notes": store.notes(P.norm(symbol) if symbol else None)}


@app.post("/api/notes")
def notes_save(body: dict = Body(...)):
    return {"note": store.save_note(body.get("body", ""), body.get("title", ""),
                                    P.norm(body["symbol"]) if body.get("symbol") else "", body.get("id"))}


@app.delete("/api/notes/{nid}")
def notes_delete(nid: int):
    store.delete_note(nid)
    return {"ok": True}


# ------------------------------------------------------------------------------------ export
@app.post("/api/export/xlsx")
def export_xlsx(body: dict = Body(...)):
    """Turn panel tables (sent by the UI as lists of rows) into an Excel workbook."""
    import io as _io

    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    wb = Workbook()
    wb.remove(wb.active)
    for i, sh in enumerate(body.get("sheets", [])[:20]):
        ws = wb.create_sheet((sh.get("name") or f"Table {i + 1}")[:31].replace("/", "-"))
        for j, row in enumerate(sh.get("rows", [])[:20000]):
            ws.append([_cell(v) for v in row])
            if j == 0:
                for c in ws[1]:
                    c.font = Font(bold=True, color="000000")
                    c.fill = PatternFill("solid", fgColor="FB8B1E")
    if not wb.sheetnames:
        wb.create_sheet("Empty")
    buf = _io.BytesIO()
    wb.save(buf)
    name = "".join(ch for ch in body.get("filename", "quantterm") if ch.isalnum() or ch in "-_ ")[:60] or "quantterm"
    return Response(buf.getvalue(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{name}.xlsx"'})


def _cell(v):
    if isinstance(v, str):
        t = v.replace(",", "").replace("%", "").replace("$", "").strip()
        if t.startswith("+"):
            t = t[1:]
        try:
            return float(t) if t and t not in ("—", "-") else (None if t in ("—",) else v)
        except ValueError:
            return v
    return v


# ------------------------------------------------------------------------------------ events & earnings
@app.get("/api/events")
def events(symbols: str = "", days: int = 45):
    syms = _syms(symbols) if symbols.strip() else []
    rows, src = [], "static"
    today = date.today().isoformat()
    for s in syms[:40]:
        try:
            r = P.earnings_date(s)
        except P.DataError:
            continue
        src = "demo" if r.source == "demo" else (src if src == "demo" else r.source)
        nxt = [x for x in r.data if x["date"] >= today]
        if nxt and nxt[0]["date"] <= (date.today() + pd.Timedelta(days=days)).isoformat():
            rows.append({"date": nxt[0]["date"], "event": f"{s} earnings", "kind": "earnings", "symbol": s,
                         "eps_est": nxt[0].get("eps_est"), "estimated": False})
    rows += P.macro_calendar(days)
    rows.sort(key=lambda x: x["date"])
    return {"source": src, "events": rows}


@app.get("/api/earnings/{symbol}")
def earnings(symbol: str):
    sym = P.norm(symbol)
    r = P.earnings_date(sym)
    today = date.today().isoformat()
    past = [x for x in r.data if x["date"] < today]
    nxt = next((x for x in r.data if x["date"] >= today), None)
    px = P.history(sym, "5y").data["Close"]
    moves = []
    for x in past:
        d = pd.Timestamp(x["date"])
        before, after = px[px.index < d], px[px.index > d]
        if len(before) and len(after):
            moves.append(x | {"move_pct": (float(after.iloc[0]) / float(before.iloc[-1]) - 1) * 100})
    implied = None
    if nxt:
        try:
            exps = P.option_expiries(sym).data
            exp = next((e for e in exps if e >= nxt["date"]), None)
            if exp:
                c = chain(sym, exp)
                implied = {"expiry": exp, "move_pct": c["summary"]["implied_move_pct"], "atm_iv": c["summary"]["atm_iv"],
                           "straddle": c["summary"]["straddle"], "spot": c["spot"]}
        except Exception as e:  # noqa: BLE001
            implied = {"error": str(e)}
    abs_moves = [abs(m["move_pct"]) for m in moves]
    return {"source": r.source, "symbol": sym, "next": nxt, "history": moves, "implied": implied,
            "avg_abs_move": float(np.mean(abs_moves)) if abs_moves else None,
            "max_abs_move": float(np.max(abs_moves)) if abs_moves else None,
            "beat_rate": float(np.mean([m["eps_act"] > m["eps_est"] for m in moves if m.get("eps_act") is not None
                                        and m.get("eps_est") is not None]) * 100) if any(m.get("eps_act") is not None for m in moves) else None}


# ------------------------------------------------------------------------------------ options: skew & scanner
@app.get("/api/skew/{symbol}")
def skew(symbol: str, n: int = 8):
    sym = P.norm(symbol)
    exps = _future_expiries(P.option_expiries(sym).data)
    picks = exps if len(exps) <= n else [exps[int(i)] for i in np.linspace(0, len(exps) - 1, n)]
    rows, src = [], "yahoo"
    for e in picks:
        try:
            c = chain(sym, e)
        except P.DataError as ex:
            log.warning("skew %s %s skipped: %s", sym, e, ex)
            continue
        src = c["source"]
        m = osig.skew_metrics(c["spot"], c["T"], c["rate"], c["calls"], c["puts"])
        rows.append({"expiry": e, "days": int(round(c["T"] * 365)), **m,
                     "implied_move_pct": c["summary"]["implied_move_pct"], "pc_oi": c["summary"]["pc_oi"]})
    if not rows:
        raise P.DataError(f"no usable option chains for {sym}")
    rv = P.history(sym, "1y").data["Close"]
    rv30 = float(ts.volatility(rv, 21).iloc[-1]) / 100
    ts_ = osig.term_structure(rows)
    front = rows[0] if rows else {}
    vrp = (front.get("atm_iv") - rv30) if front.get("atm_iv") else None
    return {"source": src, "symbol": sym, "expiries": rows, "term": ts_, "rv30": rv30, "vrp": vrp,
            "signals": _skew_signals(rows, ts_, vrp)}


def _skew_signals(rows, term, vrp) -> list[str]:
    out = []
    if vrp is not None:
        out.append(f"Front IV is {abs(vrp) * 100:.1f} vol pts {'above' if vrp > 0 else 'below'} 30d realized: "
                   + ("options look rich vs recent movement (premium sellers' environment)." if vrp > 0.02 else
                      "options look cheap vs recent movement (premium buyers' environment)." if vrp < -0.02 else "roughly fair."))
    if term:
        out.append(f"Term structure is in {term['state']}.")
    rr = [r.get("rr25") for r in rows if r.get("rr25") is not None]
    if rr:
        out.append(f"Front 25Δ risk reversal {rr[0] * 100:+.1f} vol pts: "
                   + ("downside puts carry a strong premium (crash protection bid)." if rr[0] < -0.05 else
                      "calls are bid over puts (upside chase / squeeze risk)." if rr[0] > 0.01 else "typical equity skew."))
    return out


@app.post("/api/optscan")
def optscan(body: dict = Body(...)):
    sym = P.norm(body.get("symbol", "SPY"))
    c = chain(sym, body.get("expiry")) if body.get("expiry") else chain(sym)
    px = P.history(sym, "1y").data["Close"]
    rv = float(ts.volatility(px, int(body.get("rv_window", 21))).iloc[-1]) / 100
    sigma = float(body["vol"]) / 100 if body.get("vol") else rv
    res = osig.scan(c["spot"], c["T"], c["rate"], c["calls"], c["puts"], sigma, float(body.get("width_pct", 5)) / 100)
    return {"source": c["source"], "symbol": sym, "spot": c["spot"], "expiry": c["expiry"], "expiries": c["expiries"],
            "days": int(round(c["T"] * 365)), "forecast_vol": sigma, "rv": rv, "atm_iv": c["summary"]["atm_iv"],
            "strategies": res}


# ------------------------------------------------------------------------------------ morning briefing
@app.get("/api/brief")
def brief(watch: str = ""):
    out: dict = {"source": "yahoo", "asof": datetime.now().isoformat(timespec="minutes")}
    movers = []
    for g in ("WEI", "FX", "CMDT", "CRYPTO", "RATES"):
        r = P.quotes(tuple(P.MONITORS[g]))
        out["source"] = "demo" if r.source == "demo" else out["source"]
        out[g] = [q for q in r.data if q.get("last") is not None]
        movers += [q | {"group": g} for q in out[g]]
    out["biggest"] = sorted(movers, key=lambda q: -abs(q.get("pct") or 0))[:6]
    if watch.strip():
        out["watch"] = P.quotes(tuple(_syms(watch))).data
    out["events"] = events(watch, 7)["events"]
    try:
        vix = P.history("^VIX", "1y").data["Close"]
        out["vix"] = {"last": float(vix.iloc[-1]), "pctile_1y": float((vix < vix.iloc[-1]).mean() * 100),
                      "chg_5d": float(vix.iloc[-1] - vix.iloc[-6])}
    except Exception:  # noqa: BLE001
        out["vix"] = None
    try:
        cur = P.yield_curve().data
        t = cur["tenors"]
        c = cur["curves"]["Current"]
        out["curve"] = {"2Y": c[t.index("2Y")], "10Y": c[t.index("10Y")], "2s10s_bp": (c[t.index("10Y")] - c[t.index("2Y")]) * 100}
    except Exception:  # noqa: BLE001
        out["curve"] = None
    # portfolio risk from the active broker
    try:
        snap = paper()
        pos = [p for p in snap["positions"] if p["qty"]]
        risk_ = None
        if pos:
            syms = tuple(p["symbol"] for p in pos)
            px = P.closes(syms, "1y").data.ffill().dropna()
            rets = px.pct_change().dropna()
            mv = pd.Series({p["symbol"]: p["mkt_value"] for p in pos}).reindex(rets.columns).fillna(0)
            pnl = rets @ mv
            risk_ = {"var95_1d": float(-np.percentile(pnl, 5)), "var99_1d": float(-np.percentile(pnl, 1)),
                     "worst_day": float(pnl.min()), "n_positions": len(pos)}
        out["portfolio"] = {"equity": snap["equity"], "pnl_total": snap["pnl_total"], "pnl_pct": snap["pnl_pct"],
                            "gross": snap["gross_exposure"], "net": snap["net_exposure"], "risk": risk_,
                            "top": sorted(pos, key=lambda p: -abs(p["mkt_value"]))[:5], "broker": broker.name}
    except Exception as e:  # noqa: BLE001
        out["portfolio"] = {"error": str(e)}
    out["alerts"] = [a for a in store.alerts() if a["triggered"]][:8]
    return out


# ------------------------------------------------------------------------------------ AI assistant
def _ai_handlers() -> dict:
    def quotes_h(a):
        r = P.quotes(tuple(_syms(",".join(a["symbols"][:20]))))
        return clean({"source": r.source, "quotes": [{k: q.get(k) for k in ("symbol", "name", "last", "pct", "wtd", "ytd", "time")}
                                                     for q in r.data]})

    def risk_h(a):
        d = des(a["symbol"])
        return clean({"source": d["source"], "symbol": d["symbol"], "name": d["info"].get("longName"),
                      "quote": {k: d["quote"].get(k) for k in ("last", "pct", "ytd")}, "stats_1y": d["stats_1y"],
                      "realized_vol_pct": d["realized_vol"], "range_52w": d["range_52w"]})

    def vol_h(a):
        v = vol(a["symbol"], "2y")
        g = garch(a["symbol"], "5y", 21)
        out = {"source": v["source"], "symbol": v["symbol"],
               "realized_vol_now_pct": {k: (s.dropna().iloc[-1] if len(s.dropna()) else None) for k, s in v["series"].items()},
               "realized_vol_1m_ago_pct": {k: (s.dropna().iloc[-22] if len(s.dropna()) > 22 else None) for k, s in v["series"].items()},
               "vol_cone": v["cone"], "garch": {k: g[k] for k in ("current_vol", "long_run_vol", "persistence", "half_life_days")}}
        try:
            vx = P.history("^VIX", "3mo").data["Close"]
            out["vix"] = {"last": vx.iloc[-1], "1m_ago": vx.iloc[-22]}
        except Exception:  # noqa: BLE001
            pass
        return clean(out)

    def opt_h(a):
        c = chain(a["symbol"])
        sk = skew(a["symbol"], 6)
        return clean({"source": c["source"], "symbol": c["symbol"], "spot": c["spot"], "front_expiry": c["expiry"],
                      "summary": c["summary"], "skew_by_expiry": sk["expiries"], "term_structure": sk["term"],
                      "rv30": sk["rv30"], "signals": sk["signals"]})

    def macro_h(a):
        out, src = {}, "fred"
        for sid in a["series"][:10]:
            e = eco(sid, (date.today() - pd.Timedelta(days=800)).isoformat())
            src = e["source"] if e["source"] == "demo" else src
            s = e["series"][sid.upper()]
            vals, dts = s["values"], s["dates"]
            ser = pd.Series(vals, index=pd.to_datetime(dts))
            last_dt = ser.index[-1]

            def ago(days, ser=ser, last_dt=last_dt):
                sub = ser[ser.index <= last_dt - pd.Timedelta(days=days)]
                return float(sub.iloc[-1]) if len(sub) else None

            out[sid.upper()] = {"name": s["name"], "last": s["last"], "date": s["last_date"], "1m_ago": ago(30), "1y_ago": ago(365)}
        return clean({"source": src, "series": out})

    def news_h(a):
        r = P.news(P.norm(a["symbol"]))
        return clean({"source": r.source, "items": [{k: n.get(k) for k in ("title", "publisher", "time")} for n in r.data[:10]]})

    def fac_h(a):
        f = factors(a["symbol"], "", "2y")
        return clean({"source": f["source"], "symbol": a["symbol"], "r2": f["r2"], "alpha_ann": f["alpha_ann"],
                      "loadings": f["loadings"], "variance_share": f["variance_share"]})

    return {"get_quotes": quotes_h, "get_risk_profile": risk_h, "get_volatility": vol_h, "get_options": opt_h,
            "get_macro": macro_h, "get_news": news_h, "get_factor_exposure": fac_h}


@app.post("/api/ask")
def ask(body: dict = Body(...)):
    return assistant.ask(body.get("question", ""), _ai_handlers(), body.get("context"))


@app.get("/api/broker")
def broker_info():
    return {"name": broker.name, "live": broker.live, "error": BROKER_ERROR}
