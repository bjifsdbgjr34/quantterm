"""Runtime configuration. Everything can be overridden with environment variables."""
from __future__ import annotations

import os
from pathlib import Path

HOME = Path(os.environ.get("QT_HOME", Path.home() / ".quantterm"))
HOME.mkdir(parents=True, exist_ok=True)

# auto  -> try live sources (Yahoo/FRED); fall back to synthetic demo data if unreachable
# live  -> live only (errors surface in the UI)
# demo  -> synthetic data only (offline development)
DATA_MODE = os.environ.get("QT_DATA", "auto").lower()

HOST = os.environ.get("QT_HOST", "127.0.0.1")
PORT = int(os.environ.get("QT_PORT", "8765"))

RISK_FREE = float(os.environ.get("QT_RISK_FREE", "0.04"))  # annual, used when no curve is available
TRADING_DAYS = 252

PAPER_DB = HOME / "paper.db"
PAPER_START_CASH = float(os.environ.get("QT_PAPER_CASH", "1000000"))
PAPER_SLIPPAGE_BPS = float(os.environ.get("QT_SLIPPAGE_BPS", "2"))
PAPER_COMMISSION = float(os.environ.get("QT_COMMISSION", "1.0"))  # per order, account currency

BROKER = os.environ.get("QT_BROKER", "paper").lower()  # paper | alpaca

# AI assistant (ASK). Uses the Anthropic API when credentials are configured.
AI_MODEL = os.environ.get("QT_AI_MODEL", "claude-opus-5-5")
