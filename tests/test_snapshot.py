import unittest

from crypto_oi_monitor.domain import ContractOpenInterest
from crypto_oi_monitor.market_caps import MarketCap, MarketCapLookup
from crypto_oi_monitor.snapshot import SourceHealth, build_snapshot


class SnapshotTests(unittest.TestCase):
    def test_aggregates_all_venue_oi_and_marks_complete_snapshot(self) -> None:
        snapshot = build_snapshot(
            captured_at="2026-07-24T00:00:00+00:00",
            selected_assets={"ETH"},
            market_cap_lookup=MarketCapLookup(
                {"ETH": MarketCap("ethereum", 100)}, ()
            ),
            contracts_by_venue={
                "Binance": [ContractOpenInterest("Binance", "ETHUSDT", 120)],
                "OKX": [ContractOpenInterest("OKX", "ETH-USDT-SWAP", 90)],
            },
            health={
                "Binance": SourceHealth.ok(1),
                "OKX": SourceHealth.ok(1),
                "CoinGecko": SourceHealth.ok(1),
            },
        )

        self.assertTrue(snapshot["complete"])
        self.assertEqual(snapshot["comparisons"][0]["total_oi_usd"], 210)
        self.assertEqual(snapshot["comparisons"][0]["status"], "high_risk")
        self.assertEqual(snapshot["comparisons"][0]["covered_venues"], ["Binance", "OKX"])

    def test_exposes_failed_source_and_suppresses_complete_status(self) -> None:
        snapshot = build_snapshot(
            captured_at="2026-07-24T00:00:00+00:00",
            selected_assets={"ETH"},
            market_cap_lookup=MarketCapLookup({}, ("ETH",)),
            contracts_by_venue={"Binance": []},
            health={
                "Binance": SourceHealth.ok(0),
                "CoinGecko": SourceHealth.failed("HTTP 429"),
            },
        )

        self.assertFalse(snapshot["complete"])
        self.assertEqual(snapshot["comparisons"], [])
        self.assertEqual(snapshot["unmapped_assets"], ["ETH"])
        self.assertEqual(snapshot["sources"]["CoinGecko"]["message"], "HTTP 429")

    def test_uses_explicit_canonical_symbol_for_multiplier_contracts(self) -> None:
        snapshot = build_snapshot(
            captured_at="2026-07-24T00:00:00+00:00",
            selected_assets={"PEPE"},
            market_cap_lookup=MarketCapLookup(
                {"PEPE": MarketCap("pepe", 100)}, ()
            ),
            contracts_by_venue={
                "Binance": [
                    ContractOpenInterest("Binance", "1000PEPEUSDT", 120, "PEPE")
                ]
            },
            health={"Binance": SourceHealth.ok(1), "CoinGecko": SourceHealth.ok(1)},
        )

        self.assertEqual(snapshot["comparisons"][0]["total_oi_usd"], 120)


if __name__ == "__main__":
    unittest.main()
