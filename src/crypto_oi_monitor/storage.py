from __future__ import annotations

from datetime import datetime, timedelta
import json
import logging
import shutil
import sqlite3
from pathlib import Path
from typing import Any


LOGGER = logging.getLogger(__name__)
DEFAULT_SNAPSHOT_RETENTION_DAYS = 30
DEFAULT_MIN_FREE_DISK_BYTES = 2 * 1024**3


class SnapshotStore:
    def __init__(
        self,
        database_path: Path,
        retention_days: int = DEFAULT_SNAPSHOT_RETENTION_DAYS,
        min_free_bytes: int = DEFAULT_MIN_FREE_DISK_BYTES,
    ) -> None:
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self.database_path = database_path
        self.retention_days = retention_days
        self.min_free_bytes = min_free_bytes
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
        self._protect_disk_space()
        connection = self._connect()
        try:
            with connection:
                connection.execute(
                    "INSERT INTO snapshots (captured_at, payload) VALUES (?, ?)",
                    (snapshot["captured_at"], json.dumps(snapshot, separators=(",", ":"))),
                )
                removed = self._remove_expired_snapshots(
                    connection, str(snapshot["captured_at"])
                )
        finally:
            connection.close()
        if removed:
            LOGGER.info("清理了 %s 条过期快照", removed)
        self._protect_disk_space()

    def _remove_expired_snapshots(
        self, connection: sqlite3.Connection, captured_at: str
    ) -> int:
        cutoff = datetime.fromisoformat(captured_at) - timedelta(
            days=self.retention_days
        )
        latest_id = connection.execute(
            "SELECT id FROM snapshots ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]
        expired_ids = [
            row[0]
            for row in connection.execute("SELECT id, captured_at FROM snapshots")
            if row[0] != latest_id and datetime.fromisoformat(row[1]) < cutoff
        ]
        if not expired_ids:
            return 0
        placeholders = ", ".join("?" for _ in expired_ids)
        return connection.execute(
            f"DELETE FROM snapshots WHERE id IN ({placeholders})", expired_ids
        ).rowcount

    def _protect_disk_space(self) -> None:
        if shutil.disk_usage(self.database_path).free >= self.min_free_bytes:
            return
        connection = self._connect()
        try:
            with connection:
                latest = connection.execute(
                    "SELECT id FROM snapshots ORDER BY id DESC LIMIT 1"
                ).fetchone()
                if latest is None:
                    LOGGER.error("磁盘空间不足，尚无历史快照可清理")
                    return
                latest_id = latest[0]
                removed = connection.execute(
                    "DELETE FROM snapshots WHERE id != ?", (latest_id,)
                ).rowcount
        finally:
            connection.close()
        if removed:
            LOGGER.warning("磁盘空间不足，清理了 %s 条历史快照", removed)
            self._compact_database()
        free_bytes = shutil.disk_usage(self.database_path).free
        if free_bytes < self.min_free_bytes:
            LOGGER.error(
                "可用磁盘空间仅剩 %s 字节，低于最小阈值 %s 字节",
                free_bytes,
                self.min_free_bytes,
            )

    def _compact_database(self) -> None:
        database_size = self.database_path.stat().st_size
        if shutil.disk_usage(self.database_path).free < database_size:
            LOGGER.error("可用磁盘空间不足，无法收缩监控数据库")
            return
        connection = self._connect()
        try:
            connection.execute("VACUUM")
        finally:
            connection.close()

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

    def clear_states_outside(self, active_assets: set[str]) -> tuple[int, int]:
        with self._connect() as connection:
            if not active_assets:
                removed_alerts = connection.execute("DELETE FROM alert_states").rowcount
                removed_trade_signals = connection.execute(
                    "DELETE FROM trade_signal_states"
                ).rowcount
                return removed_alerts, removed_trade_signals
            placeholders = ", ".join("?" for _ in active_assets)
            parameters = tuple(sorted(active_assets))
            removed_alerts = connection.execute(
                f"DELETE FROM alert_states WHERE canonical_symbol NOT IN ({placeholders})",
                parameters,
            ).rowcount
            removed_trade_signals = connection.execute(
                f"DELETE FROM trade_signal_states WHERE canonical_symbol NOT IN ({placeholders})",
                parameters,
            ).rowcount
            return removed_alerts, removed_trade_signals
