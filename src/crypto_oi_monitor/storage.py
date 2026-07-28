from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


class SnapshotStore:
    def __init__(self, database_path: Path) -> None:
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self.database_path = database_path
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.database_path)

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS snapshots (
                    id INTEGER PRIMARY KEY,
                    captured_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS alert_states (
                    canonical_symbol TEXT PRIMARY KEY,
                    status TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS trade_signal_states (
                    canonical_symbol TEXT PRIMARY KEY,
                    side TEXT NOT NULL
                )
                """
            )

    def save_snapshot(self, snapshot: dict[str, Any]) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO snapshots (captured_at, payload) VALUES (?, ?)",
                (snapshot["captured_at"], json.dumps(snapshot, separators=(",", ":"))),
            )

    def load_latest_snapshot(self) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM snapshots ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return None if row is None else json.loads(row[0])

    def get_alert_status(self, canonical_symbol: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT status FROM alert_states WHERE canonical_symbol = ?",
                (canonical_symbol,),
            ).fetchone()
        return None if row is None else str(row[0])

    def set_alert_status(self, canonical_symbol: str, status: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO alert_states (canonical_symbol, status)
                VALUES (?, ?)
                ON CONFLICT(canonical_symbol) DO UPDATE SET status = excluded.status
                """,
                (canonical_symbol, status),
            )

    def get_trade_signal_state(self, canonical_symbol: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT side FROM trade_signal_states WHERE canonical_symbol = ?",
                (canonical_symbol,),
            ).fetchone()
        return None if row is None else str(row[0])

    def set_trade_signal_state(self, canonical_symbol: str, side: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO trade_signal_states (canonical_symbol, side)
                VALUES (?, ?)
                ON CONFLICT(canonical_symbol) DO UPDATE SET side = excluded.side
                """,
                (canonical_symbol, side),
            )

    def clear_trade_signal_state(self, canonical_symbol: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM trade_signal_states WHERE canonical_symbol = ?",
                (canonical_symbol,),
            )
