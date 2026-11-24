"""Vectorised single-asset backtester with a safe signal-expression language.

Positions are decided on the close of day t and earn the return of day t+1 (no look-ahead).
Transaction costs are charged on turnover in basis points.
"""
from __future__ import annotations

import ast
import operator as op
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import timeseries as ts
from .risk import monthly_returns, perf_stats

# --------------------------------------------------------------------------------------
# expression language
# --------------------------------------------------------------------------------------
_BIN = {ast.Add: op.add, ast.Sub: op.sub, ast.Mult: op.mul, ast.Div: op.truediv, ast.Pow: op.pow,
        ast.BitAnd: op.and_, ast.BitOr: op.or_, ast.Mod: op.mod}
_CMP = {ast.Gt: op.gt, ast.Lt: op.lt, ast.GtE: op.ge, ast.LtE: op.le, ast.Eq: op.eq, ast.NotEq: op.ne}


def _as_bool(x):
    return x.astype(bool) if isinstance(x, pd.Series) else bool(x)


FUNCS = {
    "sma": lambda x, n=20: ts.sma(x, int(n)),
    "ema": lambda x, n=20: ts.ema(x, int(n)),
    "rsi": lambda x, n=14: ts.rsi(x, int(n)),
    "std": lambda x, n=20: x.rolling(int(n)).std(),
    "ret": lambda x, n=1: x / x.shift(int(n)) - 1,
    "zscore": lambda x, n=20: ts.zscores(x, int(n)),
    "hhv": lambda x, n=20: x.rolling(int(n)).max(),
    "llv": lambda x, n=20: x.rolling(int(n)).min(),
    "lag": lambda x, n=1: x.shift(int(n)),
    "vol": lambda x, n=20: ts.volatility(x, int(n)),
    "macd": lambda x, f=12, s=26, g=9: ts.macd(x, int(f), int(s), int(g))["macd"],
    "macd_signal": lambda x, f=12, s=26, g=9: ts.macd(x, int(f), int(s), int(g))["signal"],
    "bb_upper": lambda x, n=20, k=2: ts.bollinger_bands(x, int(n), float(k))["upper"],
    "bb_lower": lambda x, n=20, k=2: ts.bollinger_bands(x, int(n), float(k))["lower"],
    "abs": lambda x: x.abs() if isinstance(x, pd.Series) else abs(x),
    "sign": lambda x: np.sign(x),
    "clip": lambda x, lo=-1, hi=1: x.clip(lo, hi),
    "where": lambda c, a, b: pd.Series(np.where(_as_bool(c), a, b), index=c.index),
    "hold": lambda entry, exit_: _hold(entry, exit_),
}


def _hold(entry: pd.Series, exit_: pd.Series) -> pd.Series:
    """Stateful: go to 1 when entry is true, back to 0 when exit is true, otherwise keep state."""
    s = pd.Series(np.nan, index=entry.index)
    s[_as_bool(exit_.fillna(False))] = 0.0
    s[_as_bool(entry.fillna(False))] = 1.0
    return s.ffill().fillna(0.0)


class ExprError(ValueError):
    pass


def evaluate(expr: str, data: pd.DataFrame):
    """Evaluate a signal expression against OHLCV data. Only whitelisted names/functions are allowed."""
    env = {"close": data["Close"], "open": data["Open"], "high": data["High"], "low": data["Low"],
           "volume": data["Volume"]}
    try:
        tree = ast.parse(expr.strip(), mode="eval")
    except SyntaxError as e:
        raise ExprError(f"syntax error: {e.msg}") from e

    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float, bool)):
            return node.value
        if isinstance(node, ast.Name):
            if node.id in env:
                return env[node.id]
            raise ExprError(f"unknown variable '{node.id}' (use close, open, high, low, volume)")
        if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
            return _BIN[type(node.op)](ev(node.left), ev(node.right))
        if isinstance(node, ast.UnaryOp):
            v = ev(node.operand)
            if isinstance(node.op, ast.USub):
                return -v
            if isinstance(node.op, (ast.Not, ast.Invert)):
                return ~_as_bool(v) if isinstance(v, pd.Series) else (not v)
        if isinstance(node, ast.BoolOp):
            vals = [ev(v) for v in node.values]
            out = _as_bool(vals[0])
            for v in vals[1:]:
                out = (out & _as_bool(v)) if isinstance(node.op, ast.And) else (out | _as_bool(v))
            return out
        if isinstance(node, ast.Compare):
            left = ev(node.left)
            result = None
            for o, comp in zip(node.ops, node.comparators):
                right = ev(comp)
                if type(o) not in _CMP:
                    raise ExprError("unsupported comparison")
                part = _CMP[type(o)](left, right)
                result = part if result is None else (result & part)
                left = right
            return result
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in FUNCS and not node.keywords:
            return FUNCS[node.func.id](*[ev(a) for a in node.args])
        if isinstance(node, ast.Call):
            name = getattr(node.func, "id", "?")
            raise ExprError(f"unknown function '{name}'. Available: {', '.join(sorted(FUNCS))}")
        raise ExprError(f"unsupported syntax: {type(node).__name__}")

    out = ev(tree)
    if not isinstance(out, pd.Series):
        out = pd.Series(float(out), index=data.index)
    return out.astype(float)


# --------------------------------------------------------------------------------------
# built-in strategies -> expression strings (so users can learn & tweak them)
# --------------------------------------------------------------------------------------
STRATEGIES = {
    "buy_hold": {"label": "Buy & Hold", "params": {}, "expr": "1"},
    "sma_cross": {"label": "SMA Crossover", "params": {"fast": 50, "slow": 200},
                  "expr": "sma(close,{fast}) > sma(close,{slow})"},
    "ema_cross": {"label": "EMA Crossover", "params": {"fast": 12, "slow": 26},
                  "expr": "ema(close,{fast}) > ema(close,{slow})"},
    "momentum": {"label": "Time-Series Momentum", "params": {"lookback": 126},
                 "expr": "sign(ret(close,{lookback}))"},
    "rsi_reversion": {"label": "RSI Mean Reversion", "params": {"n": 14, "entry": 30, "exit": 55},
                      "expr": "hold(rsi(close,{n}) < {entry}, rsi(close,{n}) > {exit})"},
    "bollinger_reversion": {"label": "Bollinger Reversion", "params": {"n": 20, "k": 2},
                            "expr": "hold(close < bb_lower(close,{n},{k}), close > sma(close,{n}))"},
    "donchian_breakout": {"label": "Donchian Breakout", "params": {"entry": 55, "exit": 20},
                          "expr": "hold(close > lag(hhv(high,{entry}),1), close < lag(llv(low,{exit}),1))"},
    "macd": {"label": "MACD Trend", "params": {"fast": 12, "slow": 26, "signal": 9},
             "expr": "macd(close,{fast},{slow},{signal}) > macd_signal(close,{fast},{slow},{signal})"},
}


def strategy_expr(name: str, params: dict | None = None) -> str:
    s = STRATEGIES[name]
    p = s["params"] | {k: v for k, v in (params or {}).items() if k in s["params"]}
    return s["expr"].format(**p)


@dataclass
class BacktestConfig:
    expr: str
    cost_bps: float = 5.0
    allow_short: bool = False
    vol_target: float | None = None  # annual, e.g. 0.15
    max_leverage: float = 2.0
    rf: float = 0.0


@dataclass
class BacktestResult:
    equity: pd.Series
    benchmark: pd.Series
    position: pd.Series
    stats: dict
    bench_stats: dict
    trades: list[dict] = field(default_factory=list)
    monthly: dict = field(default_factory=dict)


def run(data: pd.DataFrame, cfg: BacktestConfig) -> BacktestResult:
    close = data["Close"]
    sig = evaluate(cfg.expr, data).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    if not cfg.allow_short:
        sig = sig.clip(lower=0)
    sig = sig.clip(-cfg.max_leverage, cfg.max_leverage)
    if cfg.vol_target:
        rv = ts.returns(close).rolling(20).std() * np.sqrt(ts.AF)
        scale = (cfg.vol_target / rv).clip(upper=cfg.max_leverage).fillna(0)
        sig = (sig * scale).clip(-cfg.max_leverage, cfg.max_leverage)
    pos = sig.shift(1).fillna(0.0)  # trade at close t, earn t+1
    r = ts.returns(close).fillna(0.0)
    turnover = pos.diff().abs().fillna(pos.abs())
    strat_r = pos * r - turnover * cfg.cost_bps / 1e4
    equity = 100 * (1 + strat_r).cumprod()
    bench = 100 * close / close.iloc[0]
    return BacktestResult(
        equity=equity, benchmark=bench, position=pos,
        stats=perf_stats(equity, cfg.rf, bench) | {"Avg Exposure": float(pos.abs().mean()),
                                                   "Turnover (ann.)": float(turnover.mean() * ts.AF)},
        bench_stats=perf_stats(bench, cfg.rf),
        trades=_trades(pos, close), monthly=monthly_returns(equity),
    )


def _trades(pos: pd.Series, close: pd.Series) -> list[dict]:
    """Round trips derived from sign changes of the position (entry/exit at close prices)."""
    side = np.sign(pos.round(6))
    trades, cur, entry_dt, entry_px = [], 0.0, None, None
    for dt, s in side.items():
        if s != cur:
            px_prev = close.shift(1).get(dt)  # positions are lagged by one bar; trade happened on prior close
            if cur != 0 and entry_dt is not None:
                trades.append({"entry": entry_dt.strftime("%Y-%m-%d"), "exit": dt.strftime("%Y-%m-%d"),
                               "side": "LONG" if cur > 0 else "SHORT", "entry_px": float(entry_px),
                               "exit_px": float(px_prev), "ret": float(cur * (px_prev / entry_px - 1)),
                               "bars": int(((side.index > entry_dt) & (side.index <= dt)).sum())})
            if s != 0:
                entry_dt, entry_px = dt, px_prev
            cur = s
    if cur != 0 and entry_dt is not None:
        trades.append({"entry": entry_dt.strftime("%Y-%m-%d"), "exit": "open", "side": "LONG" if cur > 0 else "SHORT",
                       "entry_px": float(entry_px), "exit_px": float(close.iloc[-1]),
                       "ret": float(cur * (close.iloc[-1] / entry_px - 1)), "bars": int((side.index > entry_dt).sum())})
    return trades


def optimize_grid(data: pd.DataFrame, strategy: str, grid: dict[str, list], cost_bps: float = 5.0,
                  metric: str = "Sharpe") -> list[dict]:
    """Brute-force parameter sweep (2 params max) - returns a heatmap-friendly list."""
    import itertools

    keys = list(grid)
    out = []
    for combo in itertools.product(*grid.values()):
        params = dict(zip(keys, combo))
        try:
            res = run(data, BacktestConfig(strategy_expr(strategy, params), cost_bps=cost_bps))
            out.append(params | {"value": res.stats.get(metric)})
        except Exception:  # noqa: BLE001
            out.append(params | {"value": None})
    return out
