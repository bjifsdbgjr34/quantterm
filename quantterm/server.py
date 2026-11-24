"""QuantTerm HTTP API + static UI."""
from __future__ import annotations

import functools
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

        @functools.wraps(orig)
        def wrapped(*a, **k):
            res = orig(*a, **k)
            return res if isinstance(res, Response) else SafeJSON(res)

        super().__init__(path, wrapped, **kw)


app = FastAPI(title="QuantTerm", default_response_class=SafeJSON)
app.router.route_class = CleanRoute
app.mount("/static", StaticFiles(directory=STATIC), name="static")
broker = PaperBroker()


@app.exception_handler(P.DataError)
async def _data_err(_: Request, exc: P.DataError):
    return SafeJSON({"error": str(exc)}, status_code=404)


@app.exception_handler(bt.ExprError)
async def _expr_err(_: Request, exc: bt.ExprError):
    return SafeJSON({"error": f"Expression error: {exc}"}, status_code=400)


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
            "fred": config.DATA_MODE != "demo" and P.online("fred"), "time": datetime.now().isoformat(timespec="seconds")}


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


def _tau(expiry: str) -> float:
    return max((date.fromisoformat(expiry) - date.today()).days + 0.7, 0.5) / 365.0


@app.get("/api/options/{symbol}")
def chain(symbol: str, expiry: str | None = None, rate: float | None = None):
    sym = P.norm(symbol)
    exps = P.option_expiries(sym).data
    expiry = expiry or exps[min(2, len(exps) - 1)]
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
    exps = P.option_expiries(sym).data
    spot = _last_price(sym)
    src = "yahoo"
    # pick a spread of expiries rather than only the front weeklies
    picks = exps if len(exps) <= n else [exps[int(i)] for i in np.linspace(0, len(exps) - 1, n)]
    smiles = []
    for e in picks:
        r = P.option_chain(sym, e)
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
    return broker.snapshot(prices)


@app.post("/api/paper/order")
def paper_order(body: dict = Body(...)):
    sym = P.norm(body["symbol"])
    last = _paper_prices([sym]).get(sym)
    o = broker.place(sym, body["side"], float(body["qty"]), body.get("type", "market"),
                     float(body["limit_px"]) if body.get("limit_px") else None,
                     float(body["stop_px"]) if body.get("stop_px") else None, last, body.get("note", ""))
    return {"order": o}


@app.delete("/api/paper/order/{oid}")
def paper_cancel(oid: int):
    broker.cancel(oid)
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
