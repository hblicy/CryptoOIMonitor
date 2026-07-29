import unittest

from crypto_oi_monitor.domain import ContractOpenInterest
from crypto_oi_monitor.market_caps import MarketCap, MarketCapCandidate, MarketCapLookup
from crypto_oi_monitor.snapshot import SourceHealth, build_snapshot


class SnapshotTests(unittest.TestCase):
    def test_aggregates_all_venue_oi_and_marks_complete_snapshot(self) -> None:
        snapshot = build_snapshot(
            captured_at="2026-07-24T00:00:00+00:00",
            selected_assets={"ETH"},
            market_cap_lookup=MarketCapLookup(
                {"ETH": MarketCap("1027", 100)}, ()
            ),
            contracts_by_venue={
                "Binance": [ContractOpenInterest("Binance", "ETHUSDT", 120)],
                "OKX": [ContractOpenInterest("OKX", "ETH-USDT-SWAP", 90)],
            },
            health={
                "Binance": SourceHealth.ok(1),
                "OKX": SourceHealth.ok(1),
                "CoinMarketCap": SourceHealth.ok(1),
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
                "CoinMarketCap": SourceHealth.failed("HTTP 429"),
            },
        )

        self.assertFalse(snapshot["complete"])
        self.assertEqual(snapshot["comparisons"], [])
        self.assertEqual(snapshot["unmapped_assets"], ["ETH"])
        self.assertEqual(snapshot["sources"]["CoinMarketCap"]["message"], "HTTP 429")

    def test_uses_explicit_canonical_symbol_for_multiplier_contracts(self) -> None:
        snapshot = build_snapshot(
            captured_at="2026-07-24T00:00:00+00:00",
            selected_assets={"PEPE"},
            market_cap_lookup=MarketCapLookup(
                {"PEPE": MarketCap("2832", 100)}, ()
            ),
            contracts_by_venue={
                "Binance": [
                    ContractOpenInterest("Binance", "1000PEPEUSDT", 120, "PEPE")
                ]
            },
            health={"Binance": SourceHealth.ok(1), "CoinMarketCap": SourceHealth.ok(1)},
        )

        self.assertEqual(snapshot["comparisons"][0]["total_oi_usd"], 120)

    def test_exposes_unmapped_cmc_candidates(self) -> None:
        snapshot = build_snapshot(
            captured_at="2026-07-24T00:00:00+00:00",
            selected_assets={"AAA"},
            market_cap_lookup=MarketCapLookup(
                {},
                ("AAA",),
                unmapped_candidates=(
                    MarketCapCandidate(
                        "AAA", "1", "Alpha", "alpha", 99, 1_000, 100, 1
                    ),
                ),
            ),
            contracts_by_venue={"Binance": []},
            health={"Binance": SourceHealth.ok(0), "CoinMarketCap": SourceHealth.ok(0)},
        )

        self.assertEqual(
            snapshot["unmapped_candidates"],
            [
                {
                    "asset": "AAA",
                    "market_cap_id": "1",
                    "name": "Alpha",
                    "slug": "alpha",
                    "price_usd": 99,
                    "market_cap_usd": 1_000,
                    "binance_price_usd": 100,
                    "price_difference_percent": 1,
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
