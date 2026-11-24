"""Equity screener: per-security metrics plus a safe filter language.

Filters are boolean expressions over metric names, e.g.
    pe < 25 and mom_12_1 > 10 and vol_63 < 35 and sector == "Technology"
They are evaluated with an AST whitelist (comparisons, and/or/not, arithmetic, numbers, strings);
never with eval().
"""
from __future__ import annotations

import ast
import operator as op

import numpy as np
import pandas as pd

from .timeseries import AF, rsi

METRICS = {
    "last": "Last price", "chg_1d": "1-day change %", "ret_1m": "1-month return %", "ret_3m": "3-month return %",
    "ret_6m": "6-month return %", "ret_ytd": "YTD return %", "mom_12_1": "12-1 month momentum %",
    "vol_63": "63-day realized vol %", "from_high": "% below 52-week high (negative)", "rsi14": "RSI(14)",
    "above_200": "1 if above 200-day SMA else 0", "beta": "Beta vs SPX (1Y)",
    "pe": "Trailing P/E", "fpe": "Forward P/E", "pb": "Price/Book", "mcap_b": "Market cap ($bn)",
    "div": "Dividend yield %", "margin": "Profit margin %", "roe": "Return on equity %", "growth": "Revenue growth %",
    "sector": "Sector (text)", "name": "Name (text)",
}


def technicals(px: pd.DataFrame, bench: pd.Series | None = None) -> pd.DataFrame:
    """Price-based metrics for each column of a close-price frame (daily, >= ~1y of history)."""
    rows = {}
    year_start = pd.Timestamp(px.index[-1].year, 1, 1)
    br = bench.pct_change() if bench is not None else None
    for s in px.columns:
        c = px[s].dropna()
        if len(c) < 30:
            continue
        last = float(c.iloc[-1])

        def back(n, c=c):
            return float(c.iloc[-1 - n]) if len(c) > n else float(c.iloc[0])

        r = c.pct_change().dropna()
        ytd = c[c.index >= year_start]
        hi = float(c.iloc[-252:].max())
        beta = None
        if br is not None:
            j = pd.concat([r, br], axis=1).dropna().iloc[-252:]
            if len(j) > 30 and j.iloc[:, 1].var() > 0:
                beta = float(j.cov().iloc[0, 1] / j.iloc[:, 1].var())
        rows[s] = {
            "last": last, "chg_1d": (last / back(1) - 1) * 100, "ret_1m": (last / back(21) - 1) * 100,
            "ret_3m": (last / back(63) - 1) * 100, "ret_6m": (last / back(126) - 1) * 100,
            "ret_ytd": (last / float(ytd.iloc[0]) - 1) * 100 if len(ytd) else None,
            "mom_12_1": (back(21) / back(252) - 1) * 100,
            "vol_63": float(np.log(c / c.shift()).iloc[-63:].std() * np.sqrt(AF) * 100),
            "from_high": (last / hi - 1) * 100, "rsi14": float(rsi(c).iloc[-1]),
            "above_200": float(last > c.iloc[-200:].mean()) if len(c) >= 200 else None, "beta": beta,
        }
    return pd.DataFrame(rows).T


def fundamentals_row(info: dict) -> dict:
    def pctv(x):
        return None if x is None else float(x) * 100

    dy = info.get("dividendYield")
    return {
        "name": info.get("longName") or info.get("shortName"), "sector": info.get("sector"),
        "pe": info.get("trailingPE"), "fpe": info.get("forwardPE"), "pb": info.get("priceToBook"),
        "mcap_b": info["marketCap"] / 1e9 if info.get("marketCap") else None,
        "div": dy,  # yfinance >= 0.2.50 reports dividendYield in percent already (AAPL 0.31 = 0.31%)
        "margin": pctv(info.get("profitMargins")), "roe": pctv(info.get("returnOnEquity")),
        "growth": pctv(info.get("revenueGrowth")),
    }


_BIN = {ast.Add: op.add, ast.Sub: op.sub, ast.Mult: op.mul, ast.Div: op.truediv}
_CMP = {ast.Gt: op.gt, ast.Lt: op.lt, ast.GtE: op.ge, ast.LtE: op.le, ast.Eq: op.eq, ast.NotEq: op.ne}


class FilterError(ValueError):
    pass


def apply_filter(df: pd.DataFrame, expr: str) -> pd.Series:
    """Boolean mask for rows matching ``expr``. Missing values never match a comparison."""
    expr = (expr or "").strip()
    if not expr:
        return pd.Series(True, index=df.index)
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise FilterError(f"syntax error: {e.msg}") from e

    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float, str)):
            return node.value
        if isinstance(node, ast.Name):
            if node.id not in METRICS:
                raise FilterError(f"unknown field '{node.id}'. Fields: {', '.join(METRICS)}")
            col = df[node.id] if node.id in df else pd.Series(np.nan, index=df.index)
            return col if node.id in ("sector", "name") else pd.to_numeric(col, errors="coerce")
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            return -ev(node.operand)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return ~ev(node.operand).astype(bool)
        if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
            return _BIN[type(node.op)](ev(node.left), ev(node.right))
        if isinstance(node, ast.BoolOp):
            vals = [ev(v).astype(bool) for v in node.values]
            out = vals[0]
            for v in vals[1:]:
                out = (out & v) if isinstance(node.op, ast.And) else (out | v)
            return out
        if isinstance(node, ast.Compare):
            left, result = ev(node.left), None
            for o, comp in zip(node.ops, node.comparators):
                if type(o) not in _CMP:
                    raise FilterError("unsupported comparison")
                right = ev(comp)
                part = _CMP[type(o)](left, right)
                if isinstance(part, pd.Series):
                    part = part.fillna(False).astype(bool)
                result = part if result is None else (result & part)
                left = right
            return result
        raise FilterError(f"unsupported syntax: {type(node).__name__}")

    out = ev(tree)
    if not isinstance(out, pd.Series):
        raise FilterError("filter must be a comparison, e.g. pe < 20")
    return out.fillna(False).astype(bool)


PRESETS = {
    "Momentum leaders": "mom_12_1 > 20 and above_200 == 1",
    "Value": "pe < 18 and pe > 0 and pb < 3",
    "Quality at a price": "roe > 15 and margin > 10 and fpe < 25",
    "Low volatility": "vol_63 < 22",
    "Oversold": "rsi14 < 35",
    "Near 52W high": "from_high > -3",
    "Dividend payers": "div > 2.5",
    "Fallen angels": "from_high < -25 and mcap_b > 50",
}
