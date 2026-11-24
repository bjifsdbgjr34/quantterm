"""Alpaca Markets adapter (https://docs.alpaca.markets/reference) implementing the Broker protocol.

Environment:
  ALPACA_KEY_ID, ALPACA_SECRET_KEY   API credentials (create them in the Alpaca dashboard)
  ALPACA_PAPER=1                     paper endpoint (default). Set 0 for live trading, which ALSO
                                     requires QT_ALLOW_LIVE=1 as a deliberate second switch.
"""
from __future__ import annotations

import os
from datetime import datetime

import httpx

PAPER_URL = "https://paper-api.alpaca.markets"
LIVE_URL = "https://api.alpaca.markets"


class BrokerError(RuntimeError):
    pass


class AlpacaBroker:
    name = "alpaca"

    def __init__(self, key_id: str, secret: str, paper: bool = True, client: httpx.Client | None = None):
        if not key_id or not secret:
            raise BrokerError("Alpaca needs ALPACA_KEY_ID and ALPACA_SECRET_KEY")
        self.live = not paper
        self.base = PAPER_URL if paper else LIVE_URL
        self.http = client or httpx.Client(base_url=self.base, timeout=15,
                                           headers={"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret})
        self._curve: list[dict] = []

    @classmethod
    def from_env(cls) -> "AlpacaBroker":
        paper = os.environ.get("ALPACA_PAPER", "1") != "0"
        if not paper and os.environ.get("QT_ALLOW_LIVE") != "1":
            raise BrokerError("Live Alpaca trading needs QT_ALLOW_LIVE=1 as well as ALPACA_PAPER=0")
        return cls(os.environ.get("ALPACA_KEY_ID", ""), os.environ.get("ALPACA_SECRET_KEY", ""), paper)

    def _req(self, method: str, path: str, **kw):
        r = self.http.request(method, path, **kw)
        if r.status_code >= 400:
            try:
                msg = r.json().get("message", r.text)
            except Exception:  # noqa: BLE001
                msg = r.text
            raise BrokerError(f"Alpaca {r.status_code}: {msg}")
        return r.json() if r.content else None

    @staticmethod
    def _sym(s: str) -> str:
        return s.replace("-USD", "/USD")  # crypto pairs: BTC-USD -> BTC/USD

    @staticmethod
    def _order(o: dict) -> dict:
        status = {"filled": "FILLED", "canceled": "CANCELLED", "expired": "CANCELLED", "rejected": "REJECTED"}.get(o["status"], "OPEN")
        return {"id": o["id"], "ts": (o.get("submitted_at") or "")[:19], "symbol": o["symbol"].replace("/USD", "-USD"),
                "side": o["side"].upper(), "qty": float(o.get("qty") or 0), "type": o["type"],
                "limit_px": float(o["limit_price"]) if o.get("limit_price") else None,
                "stop_px": float(o["stop_price"]) if o.get("stop_price") else None, "status": status,
                "fill_px": float(o["filled_avg_price"]) if o.get("filled_avg_price") else None,
                "fill_ts": (o.get("filled_at") or "")[:19] or None, "note": o.get("client_order_id", "")}

    # ------------------------------------------------------------------ Broker protocol
    def place(self, symbol, side, qty, type_="market", limit_px=None, stop_px=None, last_px=None, note=""):
        side, type_ = side.lower(), type_.lower()
        if side not in ("buy", "sell") or qty <= 0 or type_ not in ("market", "limit", "stop"):
            raise ValueError("invalid order (side buy/sell, qty > 0, type market/limit/stop)")
        body = {"symbol": self._sym(symbol), "qty": str(qty), "side": side, "type": type_,
                "time_in_force": "gtc" if "/" in self._sym(symbol) else "day"}
        if type_ == "limit":
            body["limit_price"] = str(limit_px)
        if type_ == "stop":
            body["stop_price"] = str(stop_px)
        return self._order(self._req("POST", "/v2/orders", json=body))

    def cancel(self, order_id) -> None:
        self._req("DELETE", f"/v2/orders/{order_id}")

    def symbols(self) -> list[str]:
        return [p["symbol"].replace("/USD", "-USD") for p in self._req("GET", "/v2/positions")]

    def process_open_orders(self, prices) -> int:
        return 0  # the exchange handles resting orders

    def snapshot(self, prices) -> dict:
        a = self._req("GET", "/v2/account")
        pos = self._req("GET", "/v2/positions")
        orders = self._req("GET", "/v2/orders", params={"status": "all", "limit": 100, "direction": "desc"})
        equity, last_eq = float(a["equity"]), float(a.get("last_equity") or a["equity"])
        positions, long_mv, short_mv, unreal = [], 0.0, 0.0, 0.0
        for p in pos:
            q = float(p["qty"]) * (1 if p.get("side", "long") == "long" else -1) if float(p["qty"]) > 0 else float(p["qty"])
            mv, u = float(p["market_value"]), float(p["unrealized_pl"])
            long_mv, short_mv, unreal = long_mv + max(mv, 0), short_mv + min(mv, 0), unreal + u
            positions.append({"symbol": p["symbol"].replace("/USD", "-USD"), "qty": q, "avg_px": float(p["avg_entry_price"]),
                              "last": float(p["current_price"]), "mkt_value": mv, "unrealized": u,
                              "unrealized_pct": float(p["unrealized_plpc"]) * 100, "realized": 0.0,
                              "weight": mv / equity * 100 if equity else 0})
        self._curve.append({"ts": datetime.now().strftime("%Y-%m-%d %H:%M"), "equity": equity})
        self._curve = self._curve[-500:]
        fills = [self._order(o) for o in orders if o["status"] == "filled"]
        return {"cash": float(a["cash"]), "equity": equity, "start_cash": last_eq, "pnl_total": equity - last_eq,
                "pnl_pct": (equity / last_eq - 1) * 100 if last_eq else 0, "unrealized": unreal, "realized": 0.0,
                "long_mv": long_mv, "short_mv": short_mv,
                "gross_exposure": (long_mv - short_mv) / equity * 100 if equity else 0,
                "net_exposure": (long_mv + short_mv) / equity * 100 if equity else 0,
                "positions": positions, "orders": [self._order(o) for o in orders], "fills": fills,
                "equity_curve": self._curve, "pnl_basis": "since previous close",
                "buying_power": float(a.get("buying_power", 0))}

    def reset(self, cash=None) -> None:
        raise BrokerError("Alpaca accounts can't be reset from QuantTerm. Reset a paper account in the Alpaca dashboard.")
