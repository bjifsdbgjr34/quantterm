"""Broker interface. The paper broker and every real-broker adapter implement this protocol, so the
TRADE screen and the API never care which one is active (``QT_BROKER=paper|alpaca``)."""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Broker(Protocol):
    name: str
    live: bool  # True when orders reach a real market (real money)

    def place(self, symbol: str, side: str, qty: float, type_: str = "market", limit_px: float | None = None,
              stop_px: float | None = None, last_px: float | None = None, note: str = "") -> dict: ...

    def cancel(self, order_id) -> None: ...

    def symbols(self) -> list[str]: ...

    def process_open_orders(self, prices: dict[str, float]) -> int: ...

    def snapshot(self, prices: dict[str, float]) -> dict:
        """dict with cash, equity, start_cash, pnl_total, pnl_pct, unrealized, realized, long_mv, short_mv,
        gross_exposure, net_exposure, positions[], orders[], fills[], equity_curve[]"""
        ...

    def reset(self, cash: float | None = None) -> None: ...


def make_broker(kind: str | None = None) -> Broker:
    from .. import config

    kind = (kind or config.BROKER).lower()
    if kind == "alpaca":
        from .alpaca import AlpacaBroker

        return AlpacaBroker.from_env()
    from .paper import PaperBroker

    return PaperBroker()
