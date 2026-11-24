"""SQLite-backed paper trading broker.

Market orders fill immediately at last price +/- slippage. Limit and stop orders rest until
``process_open_orders`` sees a price that triggers them (called on every account refresh).
Supports long and short positions with average-cost accounting and realised P&L.
"""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from .. import config

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS account (id INTEGER PRIMARY KEY CHECK (id = 1), cash REAL, start_cash REAL, created TEXT);
CREATE TABLE IF NOT EXISTS orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, symbol TEXT, side TEXT, qty REAL, type TEXT,
  limit_px REAL, stop_px REAL, status TEXT, fill_px REAL, fill_ts TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS positions (symbol TEXT PRIMARY KEY, qty REAL, avg_px REAL, realized REAL);
CREATE TABLE IF NOT EXISTS fills (id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER, ts TEXT, symbol TEXT,
  side TEXT, qty REAL, px REAL, commission REAL, realized REAL);
CREATE TABLE IF NOT EXISTS equity_log (ts TEXT PRIMARY KEY, equity REAL);
"""


class PaperBroker:
    def __init__(self, path: Path | str = config.PAPER_DB):
        self.path = str(path)
        with self._conn() as c:
            c.executescript(SCHEMA)
            if not c.execute("SELECT 1 FROM account").fetchone():
                c.execute("INSERT INTO account VALUES (1, ?, ?, ?)",
                          (config.PAPER_START_CASH, config.PAPER_START_CASH, _now()))

    def _conn(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    # ---------------------------------------------------------------- orders
    def place(self, symbol: str, side: str, qty: float, type_: str = "market", limit_px: float | None = None,
              stop_px: float | None = None, last_px: float | None = None, note: str = "") -> dict:
        side, type_ = side.upper(), type_.lower()
        if side not in ("BUY", "SELL"):
            raise ValueError("side must be BUY or SELL")
        if qty <= 0:
            raise ValueError("qty must be positive")
        if type_ not in ("market", "limit", "stop"):
            raise ValueError("type must be market, limit or stop")
        if type_ == "limit" and not limit_px:
            raise ValueError("limit order needs a limit price")
        if type_ == "stop" and not stop_px:
            raise ValueError("stop order needs a stop price")
        with _lock, self._conn() as c:
            cur = c.execute("INSERT INTO orders (ts, symbol, side, qty, type, limit_px, stop_px, status, note) "
                            "VALUES (?,?,?,?,?,?,?, 'OPEN', ?)", (_now(), symbol, side, qty, type_, limit_px, stop_px, note))
            oid = cur.lastrowid
            if type_ == "market":
                if last_px is None:
                    raise ValueError("no price available for market order")
                self._fill(c, oid, last_px)
            return dict(c.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone())

    def cancel(self, order_id: int) -> None:
        with _lock, self._conn() as c:
            c.execute("UPDATE orders SET status='CANCELLED' WHERE id=? AND status='OPEN'", (order_id,))

    def process_open_orders(self, prices: dict[str, float]) -> int:
        n = 0
        with _lock, self._conn() as c:
            for o in c.execute("SELECT * FROM orders WHERE status='OPEN'").fetchall():
                px = prices.get(o["symbol"])
                if px is None:
                    continue
                hit = False
                if o["type"] == "limit":
                    hit = px <= o["limit_px"] if o["side"] == "BUY" else px >= o["limit_px"]
                    fill = min(px, o["limit_px"]) if o["side"] == "BUY" else max(px, o["limit_px"])
                elif o["type"] == "stop":
                    hit = px >= o["stop_px"] if o["side"] == "BUY" else px <= o["stop_px"]
                    fill = px
                if hit:
                    self._fill(c, o["id"], fill, slippage=o["type"] != "limit")
                    n += 1
        return n

    def _fill(self, c: sqlite3.Connection, oid: int, px: float, slippage: bool = True) -> None:
        o = c.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
        sgn = 1 if o["side"] == "BUY" else -1
        fpx = px * (1 + sgn * config.PAPER_SLIPPAGE_BPS / 1e4) if slippage else px
        qty = o["qty"] * sgn
        comm = config.PAPER_COMMISSION
        pos = c.execute("SELECT * FROM positions WHERE symbol=?", (o["symbol"],)).fetchone()
        old_q, old_avg, realized_total = (pos["qty"], pos["avg_px"], pos["realized"]) if pos else (0.0, 0.0, 0.0)
        realized = 0.0
        new_q = old_q + qty
        if old_q == 0 or (old_q > 0) == (qty > 0):  # opening / adding
            new_avg = (old_q * old_avg + qty * fpx) / new_q
        else:  # reducing / closing / flipping
            closed = min(abs(qty), abs(old_q))
            realized = closed * (fpx - old_avg) * (1 if old_q > 0 else -1)
            if abs(qty) > abs(old_q):
                new_avg = fpx  # flipped
            elif new_q == 0:
                new_avg = 0.0
            else:
                new_avg = old_avg
        c.execute("INSERT OR REPLACE INTO positions VALUES (?,?,?,?)",
                  (o["symbol"], new_q, new_avg, realized_total + realized))
        c.execute("UPDATE account SET cash = cash - ? - ?", (qty * fpx, comm))
        c.execute("UPDATE orders SET status='FILLED', fill_px=?, fill_ts=? WHERE id=?", (fpx, _now(), oid))
        c.execute("INSERT INTO fills (order_id, ts, symbol, side, qty, px, commission, realized) VALUES (?,?,?,?,?,?,?,?)",
                  (oid, _now(), o["symbol"], o["side"], o["qty"], fpx, comm, realized))

    # ---------------------------------------------------------------- views
    def symbols(self) -> list[str]:
        with self._conn() as c:
            held = [r[0] for r in c.execute("SELECT symbol FROM positions WHERE qty != 0")]
            pend = [r[0] for r in c.execute("SELECT DISTINCT symbol FROM orders WHERE status='OPEN'")]
        return sorted(set(held + pend))

    def snapshot(self, prices: dict[str, float]) -> dict:
        with self._conn() as c:
            acct = dict(c.execute("SELECT * FROM account").fetchone())
            positions = []
            mv_long = mv_short = unreal = realized = 0.0
            for p in c.execute("SELECT * FROM positions ORDER BY symbol"):
                realized += p["realized"]
                if p["qty"] == 0:
                    continue
                last = prices.get(p["symbol"], p["avg_px"])
                mv = p["qty"] * last
                u = p["qty"] * (last - p["avg_px"])
                unreal += u
                if mv >= 0:
                    mv_long += mv
                else:
                    mv_short += mv
                positions.append({"symbol": p["symbol"], "qty": p["qty"], "avg_px": p["avg_px"], "last": last,
                                  "mkt_value": mv, "unrealized": u, "unrealized_pct": (last / p["avg_px"] - 1) * (1 if p["qty"] > 0 else -1) * 100 if p["avg_px"] else 0,
                                  "realized": p["realized"]})
            equity = acct["cash"] + mv_long + mv_short
            for p in positions:
                p["weight"] = p["mkt_value"] / equity * 100 if equity else 0
            orders = [dict(r) for r in c.execute("SELECT * FROM orders ORDER BY id DESC LIMIT 100")]
            fills = [dict(r) for r in c.execute("SELECT * FROM fills ORDER BY id DESC LIMIT 100")]
            c.execute("INSERT OR REPLACE INTO equity_log VALUES (?, ?)", (datetime.now().strftime("%Y-%m-%d %H:%M"), equity))
            curve = [dict(r) for r in c.execute("SELECT * FROM equity_log ORDER BY ts")]
        return {
            "cash": acct["cash"], "equity": equity, "start_cash": acct["start_cash"],
            "pnl_total": equity - acct["start_cash"], "pnl_pct": (equity / acct["start_cash"] - 1) * 100,
            "unrealized": unreal, "realized": realized, "long_mv": mv_long, "short_mv": mv_short,
            "gross_exposure": (mv_long - mv_short) / equity * 100 if equity else 0,
            "net_exposure": (mv_long + mv_short) / equity * 100 if equity else 0,
            "positions": positions, "orders": orders, "fills": fills, "equity_curve": curve,
        }

    def reset(self, cash: float | None = None) -> None:
        cash = cash or config.PAPER_START_CASH
        with _lock, self._conn() as c:
            for t in ("orders", "positions", "fills", "equity_log"):
                c.execute(f"DELETE FROM {t}")
            c.execute("UPDATE account SET cash=?, start_cash=?, created=?", (cash, cash, _now()))


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")
