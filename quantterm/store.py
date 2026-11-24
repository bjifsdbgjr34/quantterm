"""Local user data: saved workspaces, research notes and price/vol alerts (SQLite in ~/.quantterm)."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from . import config

_lock = threading.Lock()
SCHEMA = """
CREATE TABLE IF NOT EXISTS workspaces (name TEXT PRIMARY KEY, layout INTEGER, panels TEXT, links TEXT, updated TEXT);
CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY AUTOINCREMENT, created TEXT, updated TEXT, symbol TEXT,
  title TEXT, body TEXT);
CREATE TABLE IF NOT EXISTS alerts (id INTEGER PRIMARY KEY AUTOINCREMENT, created TEXT, symbol TEXT, kind TEXT,
  level REAL, note TEXT, active INTEGER DEFAULT 1, triggered TEXT, trigger_value REAL, repeat INTEGER DEFAULT 0);
"""

ALERT_KINDS = {
    "above": "Price at or above level",
    "below": "Price at or below level",
    "move": "Absolute daily move ≥ level %",
    "vol": "10-day realized vol ≥ level % (annualised)",
}


class Store:
    def __init__(self, path: Path | str = config.HOME / "user.db"):
        self.path = str(path)
        with self._c() as c:
            c.executescript(SCHEMA)

    def _c(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    # ---------------------------------------------------------------- workspaces
    def workspaces(self) -> list[dict]:
        with self._c() as c:
            return [{**dict(r), "panels": json.loads(r["panels"]), "links": json.loads(r["links"] or "{}")}
                    for r in c.execute("SELECT * FROM workspaces ORDER BY name")]

    def save_workspace(self, name: str, layout: int, panels: list[str], links: dict | None = None) -> dict:
        name = name.strip()[:60]
        if not name:
            raise ValueError("workspace needs a name")
        with _lock, self._c() as c:
            c.execute("INSERT OR REPLACE INTO workspaces VALUES (?,?,?,?,?)",
                      (name, int(layout), json.dumps(panels), json.dumps(links or {}), _now()))
        return {"name": name}

    def delete_workspace(self, name: str) -> None:
        with _lock, self._c() as c:
            c.execute("DELETE FROM workspaces WHERE name=?", (name,))

    # ---------------------------------------------------------------- notes
    def notes(self, symbol: str | None = None) -> list[dict]:
        with self._c() as c:
            q = "SELECT * FROM notes" + (" WHERE symbol=?" if symbol else "") + " ORDER BY updated DESC"
            return [dict(r) for r in c.execute(q, (symbol,) if symbol else ())]

    def save_note(self, body: str, title: str = "", symbol: str = "", note_id: int | None = None) -> dict:
        with _lock, self._c() as c:
            if note_id:
                c.execute("UPDATE notes SET body=?, title=?, symbol=?, updated=? WHERE id=?", (body, title, symbol, _now(), note_id))
            else:
                note_id = c.execute("INSERT INTO notes (created, updated, symbol, title, body) VALUES (?,?,?,?,?)",
                                    (_now(), _now(), symbol, title, body)).lastrowid
            return dict(c.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone())

    def delete_note(self, note_id: int) -> None:
        with _lock, self._c() as c:
            c.execute("DELETE FROM notes WHERE id=?", (note_id,))

    # ---------------------------------------------------------------- alerts
    def alerts(self, active_only: bool = False) -> list[dict]:
        with self._c() as c:
            q = "SELECT * FROM alerts" + (" WHERE active=1" if active_only else "") + " ORDER BY id DESC"
            return [dict(r) for r in c.execute(q)]

    def add_alert(self, symbol: str, kind: str, level: float, note: str = "", repeat: bool = False) -> dict:
        if kind not in ALERT_KINDS:
            raise ValueError(f"alert kind must be one of {', '.join(ALERT_KINDS)}")
        with _lock, self._c() as c:
            aid = c.execute("INSERT INTO alerts (created, symbol, kind, level, note, repeat) VALUES (?,?,?,?,?,?)",
                            (_now(), symbol, kind, float(level), note, int(repeat))).lastrowid
            return dict(c.execute("SELECT * FROM alerts WHERE id=?", (aid,)).fetchone())

    def delete_alert(self, aid: int) -> None:
        with _lock, self._c() as c:
            c.execute("DELETE FROM alerts WHERE id=?", (aid,))

    def mark_triggered(self, aid: int, value: float, keep_active: bool) -> None:
        with _lock, self._c() as c:
            c.execute("UPDATE alerts SET triggered=?, trigger_value=?, active=? WHERE id=?",
                      (_now(), value, int(keep_active), aid))


def alert_hit(kind: str, level: float, last: float | None, pct: float | None, rv10: float | None) -> float | None:
    """Return the observed value if the alert condition holds, else None."""
    if kind == "above" and last is not None and last >= level:
        return last
    if kind == "below" and last is not None and last <= level:
        return last
    if kind == "move" and pct is not None and abs(pct) >= level:
        return pct
    if kind == "vol" and rv10 is not None and rv10 >= level:
        return rv10
    return None


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")
