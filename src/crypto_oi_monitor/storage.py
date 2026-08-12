from __future__ import annotations

from datetime import datetime, timedelta
import json
import logging
import shutil
import sqlite3
from pathlib import Path
from typing import Any

from .trade_dispatch import TradeConditionListState, TradeSignalState


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
                    side TEXT NOT NULL,
                    entry_price REAL,
                    stop_loss REAL,
                    cooldown_until_candle_close_time INTEGER
                )
                """
            )
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(trade_signal_states)")
            }
            for column, definition in (
                ("entry_price", "REAL"),
                ("stop_loss", "REAL"),
                ("cooldown_until_candle_close_time", "INTEGER"),
                ("entry_atr", "REAL"),
                ("highest_close", "REAL"),
                ("last_processed_candle_close_time", "INTEGER"),
            ):
                if column not in columns:
                    connection.execute(
                        f"ALTER TABLE trade_signal_states ADD COLUMN {column} {definition}"
                    )
            self._migrate_trade_signal_states(connection)
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS trade_condition_list_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    can_long_symbols TEXT NOT NULL,
                    stop_long_symbols TEXT NOT NULL,
                    exit_long_symbols TEXT NOT NULL DEFAULT '[]',
                    last_sent_at TEXT
                )
                """
            )
            condition_list_columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(trade_condition_list_state)"
                )
            }
            if "exit_long_symbols" not in condition_list_columns:
                connection.execute(
                    "ALTER TABLE trade_condition_list_state "
                    "ADD COLUMN exit_long_symbols TEXT NOT NULL DEFAULT '[]'"
                )

    def _migrate_trade_signal_states(self, connection: sqlite3.Connection) -> None:
        rows = connection.execute(
            """
                SELECT canonical_symbol, side, entry_price, stop_loss,
                   entry_atr, highest_close
            FROM trade_signal_states
            WHERE side IN ('long', 'no_add')
            """
        ).fetchall()
        for symbol, _side, entry_price, stop_loss, entry_atr, highest_close in rows:
            if entry_price is None or stop_loss is None:
                LOGGER.warning(
                    "清除缺少入场价或保护价的旧交易信号状态：%s", symbol
                )
                connection.execute(
                    "DELETE FROM trade_signal_states WHERE canonical_symbol = ?",
                    (symbol,),
                )
                continue
            migrated_entry_atr = entry_atr
            if migrated_entry_atr is None:
                migrated_entry_atr = (float(entry_price) - float(stop_loss)) / 2
            if float(migrated_entry_atr) <= 0:
                LOGGER.warning(
                    "清除无法反推有效入场 ATR 的旧交易信号状态：%s", symbol
                )
                connection.execute(
                    "DELETE FROM trade_signal_states WHERE canonical_symbol = ?",
                    (symbol,),
                )
                continue
            migrated_highest_close = (
                float(entry_price) if highest_close is None else float(highest_close)
            )
            if entry_atr is None or highest_close is None:
                connection.execute(
                    """
                    UPDATE trade_signal_states
                    SET entry_atr = ?, highest_close = ?
                    WHERE canonical_symbol = ?
                    """,
                    (migrated_entry_atr, migrated_highest_close, symbol),
                )
                LOGGER.info("迁移旧交易信号状态的移动止盈参数：%s", symbol)

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

    def load_complete_snapshot_near(
        self, target: datetime, tolerance: timedelta
    ) -> dict[str, Any] | None:
        lower_bound = (target - tolerance).isoformat()
        upper_bound = (target + tolerance).isoformat()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT captured_at, payload
                FROM snapshots
                WHERE captured_at >= ? AND captured_at <= ?
                ORDER BY captured_at
                """,
                (lower_bound, upper_bound),
            ).fetchall()

        candidates: list[tuple[datetime, dict[str, Any]]] = []
        for captured_at, payload in rows:
            snapshot = json.loads(payload)
            if snapshot.get("complete") is True:
                candidates.append((datetime.fromisoformat(captured_at), snapshot))
        if not candidates:
            return None
        return min(
            candidates,
            key=lambda item: (
                abs(item[0] - target),
                item[0] > target,
            ),
        )[1]

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

    def get_trade_signal_state(
        self, canonical_symbol: str
    ) -> TradeSignalState | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT side, entry_price, stop_loss, cooldown_until_candle_close_time,
                       entry_atr, highest_close, last_processed_candle_close_time
                FROM trade_signal_states
                WHERE canonical_symbol = ?
                """,
                (canonical_symbol,),
            ).fetchone()
            if row is None:
                return None
            state = TradeSignalState(
                status=str(row[0]),
                entry_price=None if row[1] is None else float(row[1]),
                stop_loss=None if row[2] is None else float(row[2]),
                cooldown_until_candle_close_time=None if row[3] is None else int(row[3]),
                entry_atr=None if row[4] is None else float(row[4]),
                highest_close=None if row[5] is None else float(row[5]),
                last_processed_candle_close_time=(
                    None if row[6] is None else int(row[6])
                ),
            )
            if state.status in {"long", "no_add"} and (
                state.entry_price is None
                or state.stop_loss is None
                or state.entry_atr is None
                or state.highest_close is None
            ):
                LOGGER.warning(
                    "清除缺少移动止盈参数的交易信号状态：%s", canonical_symbol
                )
                connection.execute(
                    "DELETE FROM trade_signal_states WHERE canonical_symbol = ?",
                    (canonical_symbol,),
                )
                return None
        return state

    def set_trade_signal_state(
        self, canonical_symbol: str, state: TradeSignalState
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO trade_signal_states (
                    canonical_symbol,
                    side,
                    entry_price,
                    stop_loss,
                    cooldown_until_candle_close_time,
                    entry_atr,
                    highest_close,
                    last_processed_candle_close_time
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(canonical_symbol) DO UPDATE SET
                    side = excluded.side,
                    entry_price = excluded.entry_price,
                    stop_loss = excluded.stop_loss,
                    cooldown_until_candle_close_time = excluded.cooldown_until_candle_close_time,
                    entry_atr = excluded.entry_atr,
                    highest_close = excluded.highest_close,
                    last_processed_candle_close_time = excluded.last_processed_candle_close_time
                """,
                (
                    canonical_symbol,
                    state.status,
                    state.entry_price,
                    state.stop_loss,
                    state.cooldown_until_candle_close_time,
                    state.entry_atr,
                    state.highest_close,
                    state.last_processed_candle_close_time,
                ),
            )

    def clear_trade_signal_state(self, canonical_symbol: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM trade_signal_states WHERE canonical_symbol = ?",
                (canonical_symbol,),
            )

    def get_trade_condition_list_state(self) -> TradeConditionListState:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT can_long_symbols, stop_long_symbols, exit_long_symbols, last_sent_at
                FROM trade_condition_list_state
                WHERE id = 1
                """
            ).fetchone()
        if row is None:
            return TradeConditionListState((), (), (), None)
        return TradeConditionListState(
            tuple(json.loads(row[0])),
            tuple(json.loads(row[1])),
            tuple(json.loads(row[2])),
            None if row[3] is None else datetime.fromisoformat(row[3]),
        )

    def set_trade_condition_list_state(self, state: TradeConditionListState) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO trade_condition_list_state (
                    id, can_long_symbols, stop_long_symbols, exit_long_symbols, last_sent_at
                ) VALUES (1, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    can_long_symbols = excluded.can_long_symbols,
                    stop_long_symbols = excluded.stop_long_symbols,
                    exit_long_symbols = excluded.exit_long_symbols,
                    last_sent_at = excluded.last_sent_at
                """,
                (
                    json.dumps(state.can_long, separators=(",", ":")),
                    json.dumps(state.stop_long, separators=(",", ":")),
                    json.dumps(state.exit_long, separators=(",", ":")),
                    None if state.last_sent_at is None else state.last_sent_at.isoformat(),
                ),
            )

    def clear_states_outside(self, active_assets: set[str]) -> tuple[int, int]:
        with self._connect() as connection:
            if not active_assets:
                removed_alerts = connection.execute("DELETE FROM alert_states").rowcount
                removed_trade_signals = connection.execute(
                    "DELETE FROM trade_signal_states"
                ).rowcount
            else:
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
