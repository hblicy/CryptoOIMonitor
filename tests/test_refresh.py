import unittest

from crypto_oi_monitor.domain import ContractOpenInterest
from crypto_oi_monitor.market_caps import MarketCap, MarketCapLookup
from crypto_oi_monitor.refresh import RefreshCoordinator
from crypto_oi_monitor.sources import BinanceInstrument


class InMemoryStore:
    def __init__(self) -> None:
        self.saved = []

    def save_snapshot(self, snapshot):
        self.saved.append(snapshot)


class RefreshCoordinatorTests(unittest.TestCase):
    def test_records_source_error_without_using_stale_oi(self) -> None:
        store = InMemoryStore()
        coordinator = RefreshCoordinator(
            universe_loader=lambda: {"ETH": BinanceInstrument("ETH", "ETHUSDT")},
            venue_loaders={
                "Binance": lambda assets: [
                    ContractOpenInterest("Binance", "ETHUSDT", 120, "ETH")
                ],
                "OKX": lambda assets: (_ for _ in ()).throw(RuntimeError("source down")),
            },
            market_cap_loader=lambda assets: MarketCapLookup(
                {"ETH": MarketCap("ethereum", 100)}, ()
            ),
            store=store,
            now=lambda: "2026-07-24T00:00:00+00:00",
        )

        snapshot = coordinator.refresh()

        self.assertFalse(snapshot["complete"])
        self.assertEqual(snapshot["comparisons"][0]["total_oi_usd"], 120)
        self.assertEqual(snapshot["sources"]["OKX"]["status"], "error")
        self.assertIn("source down", snapshot["sources"]["OKX"]["message"])
        self.assertEqual(store.saved, [snapshot])


if __name__ == "__main__":
    unittest.main()
