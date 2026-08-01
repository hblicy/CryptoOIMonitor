import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from crypto_oi_monitor.storage import SnapshotStore
from crypto_oi_monitor.trade_dispatch import TradeConditionListState


class SnapshotStoreTests(unittest.TestCase):
    def test_persists_latest_snapshot_and_alert_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SnapshotStore(Path(temp_dir) / "monitor.db")
            snapshot = {"captured_at": "2026-07-24T00:00:00+00:00", "complete": True}

            store.save_snapshot(snapshot)
            store.set_alert_status("ETH", "high_risk")

            self.assertEqual(store.load_latest_snapshot(), snapshot)
            self.assertEqual(store.get_alert_status("ETH"), "high_risk")
            self.assertIsNone(store.get_alert_status("BTC"))

    def test_persists_and_clears_trade_signal_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SnapshotStore(Path(temp_dir) / "monitor.db")

            store.set_trade_signal_state("ETH", "long")

            self.assertEqual(store.get_trade_signal_state("ETH"), "long")
            store.clear_trade_signal_state("ETH")
            self.assertIsNone(store.get_trade_signal_state("ETH"))

    def test_persists_trade_condition_list_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SnapshotStore(Path(temp_dir) / "monitor.db")

            store.set_trade_condition_list_state(
                TradeConditionListState(
                    ("AKE",),
                    ("ON",),
                    datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc),
                )
            )

            state = store.get_trade_condition_list_state()

            self.assertEqual(state.can_long, ("AKE",))
            self.assertEqual(state.stop_long, ("ON",))
            self.assertEqual(
                state.last_sent_at,
                datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc),
            )

    def test_clears_trade_condition_list_symbols_outside_active_comparisons(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SnapshotStore(Path(temp_dir) / "monitor.db")
            store.set_trade_condition_list_state(
                TradeConditionListState(
                    ("AKE", "BULLA"),
                    ("BULLA", "ON"),
                    datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc),
                )
            )

            removed = store.clear_trade_condition_list_state_outside({"AKE", "ON"})
            state = store.get_trade_condition_list_state()

            self.assertEqual(removed, 2)
            self.assertEqual(state.can_long, ("AKE",))
            self.assertEqual(state.stop_long, ("ON",))

    def test_clears_alert_and_trade_states_outside_active_comparisons(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SnapshotStore(Path(temp_dir) / "monitor.db")
            store.set_alert_status("ETH", "high_risk")
            store.set_alert_status("DOGE", "high_risk")
            store.set_trade_signal_state("ETH", "long")
            store.set_trade_signal_state("DOGE", "short")

            removed_alerts, removed_trade_signals = store.clear_states_outside({"ETH"})

            self.assertEqual((removed_alerts, removed_trade_signals), (1, 1))
            self.assertEqual(store.get_alert_status("ETH"), "high_risk")
            self.assertEqual(store.get_trade_signal_state("ETH"), "long")
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
