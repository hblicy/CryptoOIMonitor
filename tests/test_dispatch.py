import unittest

from crypto_oi_monitor.dispatch import dispatch_alerts


class MemoryStore:
    def __init__(self) -> None:
        self.statuses = {}

    def get_alert_status(self, symbol):
        return self.statuses.get(symbol)

    def set_alert_status(self, symbol, status):
        self.statuses[symbol] = status


class RecordingNotifier:
    def __init__(self) -> None:
        self.events = []

    def send(self, event, comparison):
        self.events.append((event, comparison["canonical_symbol"]))


class DispatchTests(unittest.TestCase):
    def test_sends_once_on_high_risk_transition_and_persists_status(self) -> None:
        store = MemoryStore()
        notifier = RecordingNotifier()
        snapshot = {
            "complete": True,
            "comparisons": [{"canonical_symbol": "PEPE", "status": "high_risk"}],
        }

        dispatched = dispatch_alerts(snapshot, store, notifier)
        repeated = dispatch_alerts(snapshot, store, notifier)

        self.assertEqual(dispatched, ["entered_high_risk"])
        self.assertEqual(repeated, [])
        self.assertEqual(notifier.events, [("entered_high_risk", "PEPE")])
        self.assertEqual(store.statuses["PEPE"], "high_risk")


if __name__ == "__main__":
    unittest.main()
