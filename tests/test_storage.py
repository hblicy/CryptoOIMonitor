import tempfile
import unittest
from pathlib import Path

from crypto_oi_monitor.storage import SnapshotStore


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


if __name__ == "__main__":
    unittest.main()
