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
    def test_logs_the_venue_name_and_traceback_when_a_source_fails(self) -> None:
        coordinator = RefreshCoordinator(
            universe_loader=lambda: {"ETH": BinanceInstrument("ETH", "ETHUSDT")},
            venue_loaders={
                "BingX": lambda assets: (_ for _ in ()).throw(
                    RuntimeError("BingX request timed out")
                )
            },
            market_cap_loader=lambda assets: MarketCapLookup(
                {"ETH": MarketCap("ethereum", 100)}, ()
            ),
            store=InMemoryStore(),
            now=lambda: "2026-07-29T00:00:00+00:00",
        )

        with self.assertLogs("crypto_oi_monitor.refresh", "ERROR") as logs:
            snapshot = coordinator.refresh()

        self.assertEqual(snapshot["sources"]["BingX"]["status"], "error")
        self.assertIn("BingX refresh failed", logs.output[0])
        self.assertIn("RuntimeError: BingX request timed out", logs.output[0])

    def test_passes_binance_instruments_to_market_cap_loader(self) -> None:
        universe = {"ETH": BinanceInstrument("ETH", "ETHUSDT", 3_000)}
        received = []
        coordinator = RefreshCoordinator(
            universe_loader=lambda: universe,
            venue_loaders={},
            market_cap_loader=lambda instruments: received.append(instruments)
            or MarketCapLookup({"ETH": MarketCap("ethereum", 100)}, ()),
            store=InMemoryStore(),
            now=lambda: "2026-07-29T00:00:00+00:00",
        )

        coordinator.refresh()

        self.assertEqual(received, [universe])

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
                {"ETH": MarketCap("ethereum", 100)}, (), "2026-07-24T00:00:00+00:00"
            ),
            store=store,
            now=lambda: "2026-07-24T00:00:00+00:00",
        )

        with self.assertLogs("crypto_oi_monitor.refresh", "ERROR") as logs:
            snapshot = coordinator.refresh()

        self.assertFalse(snapshot["complete"])
        self.assertEqual(snapshot["comparisons"][0]["total_oi_usd"], 120)
        self.assertEqual(snapshot["sources"]["OKX"]["status"], "error")
        self.assertIn("source down", snapshot["sources"]["OKX"]["message"])
        self.assertIn("OKX refresh failed", logs.output[0])
        self.assertEqual(
            snapshot["sources"]["CoinMarketCap"]["updated_at"],
            "2026-07-24T00:00:00+00:00",
        )
        self.assertEqual(store.saved, [snapshot])

    def test_marks_empty_cmc_market_caps_as_an_error(self) -> None:
        coordinator = RefreshCoordinator(
            universe_loader=lambda: {"ETH": BinanceInstrument("ETH", "ETHUSDT")},
            venue_loaders={
                "Binance": lambda assets: [
                    ContractOpenInterest("Binance", "ETHUSDT", 120, "ETH")
                ]
            },
            market_cap_loader=lambda assets: MarketCapLookup(
                {}, ("ETH",), "2026-07-24T00:00:00+00:00"
            ),
            store=InMemoryStore(),
            now=lambda: "2026-07-24T00:00:00+00:00",
        )

        snapshot = coordinator.refresh()

        self.assertFalse(snapshot["complete"])
        self.assertEqual(snapshot["sources"]["CoinMarketCap"]["status"], "error")
        self.assertIn("no usable market caps", snapshot["sources"]["CoinMarketCap"]["message"])


if __name__ == "__main__":
    unittest.main()
