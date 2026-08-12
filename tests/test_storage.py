import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from crypto_oi_monitor.storage import SnapshotStore
from crypto_oi_monitor.trade_dispatch import TradeConditionListState, TradeSignalState


class SnapshotStoreTests(unittest.TestCase):
    def test_migrates_existing_condition_list_state_with_empty_must_exit_group(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "monitor.db"
            connection = sqlite3.connect(database_path)
            try:
                connection.execute(
                    """
                    CREATE TABLE trade_condition_list_state (
                        id INTEGER PRIMARY KEY CHECK (id = 1),
                        can_long_symbols TEXT NOT NULL,
                        stop_long_symbols TEXT NOT NULL,
                        last_sent_at TEXT
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO trade_condition_list_state (
                        id, can_long_symbols, stop_long_symbols, last_sent_at
                    ) VALUES (1, '[\"AKE\"]', '[\"ON\"]', NULL)
                    """
                )
                connection.commit()
            finally:
                connection.close()

            state = SnapshotStore(database_path).get_trade_condition_list_state()

            self.assertEqual(state.can_long, ("AKE",))
            self.assertEqual(state.stop_long, ("ON",))
            self.assertEqual(state.exit_long, ())

    def test_persists_latest_snapshot_and_alert_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SnapshotStore(Path(temp_dir) / "monitor.db")
            snapshot = {"captured_at": "2026-07-24T00:00:00+00:00", "complete": True}

            store.save_snapshot(snapshot)
            store.set_alert_status("ETH", "high_risk")

            self.assertEqual(store.load_latest_snapshot(), snapshot)
            self.assertEqual(store.get_alert_status("ETH"), "high_risk")
            self.assertIsNone(store.get_alert_status("BTC"))

    def test_loads_closest_complete_snapshot_near_target_and_prefers_earlier_tie(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SnapshotStore(Path(temp_dir) / "monitor.db")
            earlier = {
                "captured_at": "2026-08-07T00:14:00+00:00",
                "complete": True,
            }
            store.save_snapshot(earlier)
            store.save_snapshot(
                {
                    "captured_at": "2026-08-07T00:15:00+00:00",
                    "complete": False,
                }
            )
            store.save_snapshot(
                {
                    "captured_at": "2026-08-07T00:16:00+00:00",
                    "complete": True,
                }
            )

            result = store.load_complete_snapshot_near(
                datetime(2026, 8, 7, 0, 15, tzinfo=timezone.utc),
                timedelta(minutes=5),
            )

            self.assertEqual(result, earlier)

    def test_returns_none_when_no_complete_snapshot_is_within_tolerance(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SnapshotStore(Path(temp_dir) / "monitor.db")
            store.save_snapshot(
                {
                    "captured_at": "2026-08-07T00:09:59+00:00",
                    "complete": True,
                }
            )
            store.save_snapshot(
                {
                    "captured_at": "2026-08-07T00:15:00+00:00",
                    "complete": False,
                }
            )

            result = store.load_complete_snapshot_near(
                datetime(2026, 8, 7, 0, 15, tzinfo=timezone.utc),
                timedelta(minutes=5),
            )

            self.assertIsNone(result)

    def test_persists_and_clears_trade_signal_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SnapshotStore(Path(temp_dir) / "monitor.db")

            state = TradeSignalState("long", 100, 96, None, 2, 106, 1234)
            store.set_trade_signal_state("ETH", state)

            self.assertEqual(store.get_trade_signal_state("ETH"), state)
            store.clear_trade_signal_state("ETH")
            self.assertIsNone(store.get_trade_signal_state("ETH"))

    def test_migrates_legacy_active_trade_state_with_trailing_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "monitor.db"
            connection = sqlite3.connect(database_path)
            try:
                connection.execute(
                    """
                    CREATE TABLE trade_signal_states (
                        canonical_symbol TEXT PRIMARY KEY,
                        side TEXT NOT NULL,
                        entry_price REAL,
                        stop_loss REAL,
                        cooldown_until_candle_close_time INTEGER
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO trade_signal_states (
                        canonical_symbol, side, entry_price, stop_loss
                    ) VALUES ('ETH', 'long', 100, 96)
                    """
                )
                connection.commit()
            finally:
                connection.close()

            with self.assertLogs("crypto_oi_monitor.storage", level="INFO"):
                state = SnapshotStore(database_path).get_trade_signal_state("ETH")

            self.assertEqual(
                state,
                TradeSignalState("long", 100, 96, None, 2, 100, None),
            )

    def test_clears_legacy_active_state_when_entry_atr_cannot_be_derived(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "monitor.db"
            connection = sqlite3.connect(database_path)
            try:
                connection.execute(
                    """
                    CREATE TABLE trade_signal_states (
                        canonical_symbol TEXT PRIMARY KEY,
                        side TEXT NOT NULL,
                        entry_price REAL,
                        stop_loss REAL,
                        cooldown_until_candle_close_time INTEGER
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO trade_signal_states (
                        canonical_symbol, side, entry_price, stop_loss
                    ) VALUES ('ETH', 'long', 100, 101)
                    """
                )
                connection.commit()
            finally:
                connection.close()

            with self.assertLogs("crypto_oi_monitor.storage", level="WARNING"):
                store = SnapshotStore(database_path)

            self.assertIsNone(store.get_trade_signal_state("ETH"))

    def test_clears_legacy_long_state_without_exit_parameters(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SnapshotStore(Path(temp_dir) / "monitor.db")
            connection = store._connect()
            try:
                connection.execute(
                    "INSERT INTO trade_signal_states (canonical_symbol, side) VALUES (?, ?)",
                    ("ETH", "long"),
                )
                connection.commit()
            finally:
                connection.close()

            with self.assertLogs("crypto_oi_monitor.storage", level="WARNING"):
                self.assertIsNone(store.get_trade_signal_state("ETH"))
            connection = store._connect()
            try:
                self.assertIsNone(
                    connection.execute(
                        "SELECT 1 FROM trade_signal_states WHERE canonical_symbol = ?",
                        ("ETH",),
                    ).fetchone()
                )
            finally:
                connection.close()

    def test_persists_trade_condition_list_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SnapshotStore(Path(temp_dir) / "monitor.db")

            store.set_trade_condition_list_state(
                TradeConditionListState(
                    ("AKE",),
                    ("ON",),
                    ("KOMA",),
                    datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc),
                )
            )

            state = store.get_trade_condition_list_state()

            self.assertEqual(state.can_long, ("AKE",))
            self.assertEqual(state.stop_long, ("ON",))
            self.assertEqual(state.exit_long, ("KOMA",))
            self.assertEqual(
                state.last_sent_at,
                datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc),
            )

    def test_clears_alert_and_trade_states_outside_active_comparisons(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SnapshotStore(Path(temp_dir) / "monitor.db")
            store.set_alert_status("ETH", "high_risk")
            store.set_alert_status("DOGE", "high_risk")
            store.set_trade_signal_state(
                "ETH", TradeSignalState("long", 100, 98, None, 1, 100, 1234)
            )
            store.set_trade_signal_state("DOGE", TradeSignalState("short"))

            removed_alerts, removed_trade_signals = store.clear_states_outside({"ETH"})

            self.assertEqual((removed_alerts, removed_trade_signals), (1, 1))
            self.assertEqual(store.get_alert_status("ETH"), "high_risk")
            self.assertEqual(
                store.get_trade_signal_state("ETH"),
                TradeSignalState("long", 100, 98, None, 1, 100, 1234),
            )
            self.assertIsNone(store.get_alert_status("DOGE"))
            self.assertIsNone(store.get_trade_signal_state("DOGE"))

    def test_prunes_snapshots_older_than_retention_period(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SnapshotStore(Path(temp_dir) / "monitor.db", retention_days=1)
            store.save_snapshot({"captured_at": "2026-07-01T00:00:00+00:00"})
            latest = {"captured_at": "2026-07-03T00:00:00+00:00"}

            store.save_snapshot(latest)

            self.assertEqual(store.load_latest_snapshot(), latest)
            connection = store._connect()
            try:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0], 1)
            finally:
                connection.close()

    def test_keeps_only_latest_snapshot_when_free_disk_is_low(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "crypto_oi_monitor.storage.shutil.disk_usage",
            return_value=SimpleNamespace(free=0),
        ):
            store = SnapshotStore(
                Path(temp_dir) / "monitor.db", min_free_bytes=1
            )
            with patch.object(store, "_compact_database") as compact, self.assertLogs(
                "crypto_oi_monitor.storage", level="ERROR"
            ):
                store.save_snapshot({"captured_at": "2026-07-01T00:00:00+00:00"})
                latest = {"captured_at": "2026-07-01T00:02:00+00:00"}
                store.save_snapshot(latest)

            self.assertEqual(store.load_latest_snapshot(), latest)
            self.assertEqual(compact.call_count, 1)
            connection = store._connect()
            try:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0], 1)
            finally:
                connection.close()

    def test_compacts_after_low_disk_cleanup_when_space_is_available(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "crypto_oi_monitor.storage.shutil.disk_usage",
            side_effect=[
                SimpleNamespace(free=0),
                SimpleNamespace(free=0),
                SimpleNamespace(free=0),
                SimpleNamespace(free=0),
                SimpleNamespace(free=0),
                SimpleNamespace(free=0),
                SimpleNamespace(free=10**9),
                SimpleNamespace(free=10**9),
            ],
        ):
            store = SnapshotStore(
                Path(temp_dir) / "monitor.db", min_free_bytes=1
            )
            with self.assertLogs("crypto_oi_monitor.storage", level="ERROR"):
                store.save_snapshot({"captured_at": "2026-07-01T00:00:00+00:00"})
                latest = {"captured_at": "2026-07-01T00:02:00+00:00"}
                store.save_snapshot(latest)

            self.assertEqual(store.load_latest_snapshot(), latest)
            connection = store._connect()
            try:
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0],
                    1,
                )
            finally:
                connection.close()

    def test_checks_disk_space_before_inserting_a_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SnapshotStore(Path(temp_dir) / "monitor.db")
            store.save_snapshot({"captured_at": "2026-07-01T00:00:00+00:00"})
            store.save_snapshot({"captured_at": "2026-07-01T00:02:00+00:00"})
            snapshot_counts: list[int] = []

            def record_snapshot_count() -> None:
                connection = store._connect()
                try:
                    snapshot_counts.append(
                        connection.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
                    )
                finally:
                    connection.close()

            with patch.object(store, "_protect_disk_space", side_effect=record_snapshot_count):
                store.save_snapshot({"captured_at": "2026-07-01T00:04:00+00:00"})

        self.assertEqual(snapshot_counts, [2, 3])


if __name__ == "__main__":
    unittest.main()
