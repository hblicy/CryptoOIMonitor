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
    def test_sends_one_entry_and_one_exit_across_hysteresis_band(self) -> None:
        store = MemoryStore()
        notifier = RecordingNotifier()
        snapshots = [
            {"status": "high_risk", "oi_to_market_cap": 2.06},
            {"status": "warning", "oi_to_market_cap": 1.98},
            {"status": "high_risk", "oi_to_market_cap": 2.02},
            {"status": "warning", "oi_to_market_cap": 1.94},
        ]

        dispatched = [
            dispatch_alerts(
                {
                    "complete": True,
                    "comparisons": [{"canonical_symbol": "PEPE", **comparison}],
                },
                store,
                notifier,
            )
            for comparison in snapshots
        ]

        self.assertEqual(dispatched, [["entered_high_risk"], [], [], ["recovered"]])
        self.assertEqual(
            notifier.events,
            [("entered_high_risk", "PEPE"), ("recovered", "PEPE")],
        )
        self.assertEqual(store.statuses["PEPE"], "warning")


if __name__ == "__main__":
    unittest.main()
