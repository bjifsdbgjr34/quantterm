"""ASK: research assistant panel.

With Anthropic credentials configured (ANTHROPIC_API_KEY, or an `ant auth login` profile) the question
goes to Claude with a small set of read-only terminal tools (quotes, vol, options, macro, news,
factors). Claude answers from those numbers and proposes terminal panels to open.

Without credentials a keyword router maps the question to relevant panels and says plainly that no
AI was involved. Market data and your question are sent to Anthropic only in the AI mode.
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Callable

from . import config

log = logging.getLogger("quantterm.ai")

SYSTEM = """You are the research assistant inside QuantTerm, a Bloomberg-style quant terminal.
Answer the user's market question using the terminal tools, which return live (or demo) data.
- Ground every number you state in a tool result. If a result has "source": "demo", say clearly that
  the data is synthetic demo data and the conclusions are illustrative only.
- Be concise: a short direct answer, then 2-5 bullet points with the evidence. Use plain language.
- Distinguish facts (from tools) from interpretation. Never claim to know why a market moved unless a
  tool result (e.g. news) supports it; otherwise offer the likely candidates and say they're unconfirmed.
- Call open_panel for the 1-4 terminal views that best let the user verify your answer
  (e.g. "SPY VOL", "SPY VOLS", "GC", "CORR SPY TLT", "NVDA FF", "EVT AAPL").
- This is research, not investment advice. Do not tell the user to buy or sell anything."""

TOOLS = [
    {"name": "get_quotes", "description": "Last price, daily/weekly/YTD % change for up to 20 symbols. "
     "Accepts tickers and aliases like SPX, VIX, EURUSD, GOLD, BTC.",
     "input_schema": {"type": "object", "properties": {"symbols": {"type": "array", "items": {"type": "string"}}},
                      "required": ["symbols"], "additionalProperties": False}},
    {"name": "get_risk_profile", "description": "1Y performance and risk for one symbol: return, vol, Sharpe, "
     "max drawdown, beta to S&P 500, realized vol 10/30/90d, 52-week range.",
     "input_schema": {"type": "object", "properties": {"symbol": {"type": "string"}}, "required": ["symbol"],
                      "additionalProperties": False}},
    {"name": "get_volatility", "description": "Volatility diagnostics for one symbol: realized vol series "
     "summary, where current vol sits in its historical cone (percentiles), GARCH conditional vol and "
     "long-run vol, and the current VIX level for context.",
     "input_schema": {"type": "object", "properties": {"symbol": {"type": "string"}}, "required": ["symbol"],
                      "additionalProperties": False}},
    {"name": "get_options", "description": "Options market view for one symbol: ATM implied vol, implied move, "
     "put/call ratios, 25-delta skew and the ATM implied-vol term structure.",
     "input_schema": {"type": "object", "properties": {"symbol": {"type": "string"}}, "required": ["symbol"],
                      "additionalProperties": False}},
    {"name": "get_macro", "description": "Latest values and 1-month / 1-year changes for FRED series, e.g. "
     "DGS10, DGS2, T10Y2Y, FEDFUNDS, CPIAUCSL (YoY), UNRATE, BAMLH0A0HYM2 (HY spread), VIXCLS.",
     "input_schema": {"type": "object", "properties": {"series": {"type": "array", "items": {"type": "string"}}},
                      "required": ["series"], "additionalProperties": False}},
    {"name": "get_news", "description": "Recent news headlines for a symbol.",
     "input_schema": {"type": "object", "properties": {"symbol": {"type": "string"}}, "required": ["symbol"],
                      "additionalProperties": False}},
    {"name": "get_factor_exposure", "description": "Factor loadings (market, size, value, momentum, quality, low "
     "vol) for a symbol from ETF-proxy regressions over 2 years, with R² and alpha.",
     "input_schema": {"type": "object", "properties": {"symbol": {"type": "string"}}, "required": ["symbol"],
                      "additionalProperties": False}},
    {"name": "open_panel", "description": "Open a terminal panel for the user, e.g. 'SPY VOL', 'GC', "
     "'CORR SPY TLT GLD', 'AAPL OMON'. Use the terminal's TICKER FUNCTION syntax.",
     "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"],
                      "additionalProperties": False}},
]


def ai_available() -> bool:
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    if any(os.environ.get(k) for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE")):
        return True
    return (Path.home() / ".config" / "anthropic").exists()


def ask(question: str, handlers: dict[str, Callable[[dict], dict]], context: dict | None = None,
        max_rounds: int = 8) -> dict:
    question = (question or "").strip()
    if not question:
        raise ValueError("ask a question, e.g. 'why is SPY vol rising?'")
    if not ai_available():
        return route(question, context)
    import anthropic

    client = anthropic.Anthropic()
    ctx = ""
    if context:
        ctx = f"\n\n(Terminal context: open panels = {context.get('panels')}, watchlist = {context.get('watchlist')})"
    messages = [{"role": "user", "content": question + ctx}]
    commands, log_, sources = [], [], set()
    for _ in range(max_rounds):
        try:
            resp = client.beta.messages.create(
                model=config.AI_MODEL, max_tokens=16000, system=SYSTEM, tools=TOOLS, messages=messages,
                output_config={"effort": "medium"},
                betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            )
        except anthropic.AuthenticationError:
            out = route(question, context)
            out["note"] = "Anthropic credentials were rejected - showing keyword routing instead. " + out["note"]
            return out
        except anthropic.APIConnectionError:
            out = route(question, context)
            out["note"] = "Could not reach the Anthropic API - showing keyword routing instead. " + out["note"]
            return out
        if resp.stop_reason == "refusal":
            return {"mode": "ai", "answer": "The model declined to answer this question.", "commands": commands,
                    "tools": log_, "demo": "demo" in sources, "note": ""}
        messages.append({"role": "assistant", "content": resp.content})
        uses = [b for b in resp.content if b.type == "tool_use"]
        if resp.stop_reason != "tool_use" or not uses:
            text = "\n".join(b.text for b in resp.content if b.type == "text").strip()
            return {"mode": "ai", "model": resp.model, "answer": text, "commands": commands, "tools": log_,
                    "demo": "demo" in sources, "note": ""}
        results = []
        for u in uses:
            args = u.input if isinstance(u.input, dict) else json.loads(u.input)
            try:
                if u.name == "open_panel":
                    cmd = str(args.get("command", "")).strip().upper()[:80]
                    if cmd and cmd not in commands:
                        commands.append(cmd)
                    res = {"ok": True, "opened": cmd}
                else:
                    res = handlers[u.name](args)
                    sources.add(res.get("source", ""))
                log_.append({"tool": u.name, "input": args})
                results.append({"type": "tool_result", "tool_use_id": u.id, "content": json.dumps(res, default=str)[:20000]})
            except Exception as e:  # noqa: BLE001
                log_.append({"tool": u.name, "input": args, "error": str(e)})
                results.append({"type": "tool_result", "tool_use_id": u.id, "content": f"error: {e}", "is_error": True})
        messages.append({"role": "user", "content": results})
    return {"mode": "ai", "answer": "Stopped after too many tool rounds; partial results are in the opened panels.",
            "commands": commands, "tools": log_, "demo": "demo" in sources, "note": ""}


# ------------------------------------------------------------------ offline keyword router
KEYWORDS = [
    (r"\bvol|volatil|vix|garch", ["{s} VOL", "{s} GARCH"]),
    (r"option|implied|skew|iv\b|straddle|put|call", ["{s} OMON", "{s} VOLS", "{s} SKEW"]),
    (r"earning|report|eps|event|calendar", ["EVT {s}"]),
    (r"correl|diversif|hedge", ["CORR {s} TLT GLD"]),
    (r"factor|value|momentum|size|exposure|style", ["{s} FF"]),
    (r"yield|curve|rate|fed|treasur|bond|inflation|cpi|macro|recession", ["GC", "ECO"]),
    (r"backtest|strategy|signal|trend", ["BT {s}", "WF {s}"]),
    (r"sector|heat|breadth|market today|movers", ["MAP", "SECTORS"]),
    (r"screen|cheap|find stocks", ["EQS"]),
    (r"simulat|monte carlo|probab|scenario", ["MC {s}"]),
    (r"news|why", ["{s} N"]),
]
TICKER = re.compile(r"\b[A-Z]{1,5}(?:[.=-][A-Z]{1,3})?\b|\^[A-Z]+")
STOP = {"I", "A", "THE", "AND", "OR", "IS", "ARE", "WHY", "WHAT", "HOW", "VOL", "IV", "US", "ETF", "FX", "AI", "VS", "TO", "OF", "IN", "ON"}


def route(question: str, context: dict | None = None) -> dict:
    from .data.providers import ALIASES, DEMO_META, UNIVERSE_SECTOR

    known = set(ALIASES) | set(DEMO_META) | set(UNIVERSE_SECTOR)
    toks = [t for t in TICKER.findall(question.upper()) if t not in STOP]
    syms = [t for t in toks if t in known] or [t for t in toks if len(t) >= 2][:1] or ["SPY"]
    s = syms[0]
    q = question.lower()
    cmds: list[str] = []
    for pat, tmpl in KEYWORDS:
        if re.search(pat, q):
            for t in tmpl:
                c = t.format(s=s)
                if c not in cmds:
                    cmds.append(c)
    if not cmds:
        cmds = [f"{s} DES", f"{s} GP"]
    return {"mode": "router", "answer": f"Opening views relevant to {s}: " + ", ".join(cmds[:4]) + ".",
            "commands": cmds[:4], "tools": [], "demo": False,
            "note": "AI is off: no Anthropic credentials found. Set ANTHROPIC_API_KEY (or run `ant auth login`) "
                    "and restart to get written answers. This reply is a keyword match, not analysis."}
