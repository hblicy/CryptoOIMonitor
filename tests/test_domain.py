import unittest

from crypto_oi_monitor.domain import (
    HIGH_RISK,
    NORMAL,
    WARNING,
    ContractOpenInterest,
    aggregate_asset,
    is_binance_universe_member,
    risk_status,
)


class RiskStatusTests(unittest.TestCase):
    def test_marks_oi_above_market_cap_as_warning(self) -> None:
        self.assertEqual(risk_status(1.000001), WARNING)

    def test_marks_oi_strictly_above_two_times_market_cap_as_high_risk(self) -> None:
        self.assertEqual(risk_status(2.0), WARNING)
        self.assertEqual(risk_status(2.000001), HIGH_RISK)

    def test_marks_oi_not_above_market_cap_as_normal(self) -> None:
        self.assertEqual(risk_status(1.0), NORMAL)


class BinanceUniverseTests(unittest.TestCase):
    def test_includes_symbol_at_ten_million_usd_turnover(self) -> None:
        self.assertTrue(is_binance_universe_member("PERPETUAL", 10_000_000))

    def test_excludes_lower_turnover_and_non_perpetual_symbols(self) -> None:
        self.assertFalse(is_binance_universe_member("PERPETUAL", 9_999_999.99))
        self.assertFalse(is_binance_universe_member("CURRENT_QUARTER", 50_000_000))


class AggregationTests(unittest.TestCase):
    def test_sums_venue_oi_and_calculates_ratio(self) -> None:
        comparison = aggregate_asset(
            canonical_symbol="ETH",
            market_cap_id="1027",
            market_cap_usd=100,
            contracts=(
                ContractOpenInterest("Binance", "ETHUSDT", 80),
                ContractOpenInterest("OKX", "ETH-USDT-SWAP", 55),
            ),
        )

        self.assertEqual(comparison.total_oi_usd, 135)
        self.assertEqual(comparison.market_cap_id, "1027")
        self.assertEqual(comparison.oi_to_market_cap, 1.35)
        self.assertEqual(comparison.status, WARNING)
        self.assertEqual(comparison.covered_venues, ("Binance", "OKX"))


if __name__ == "__main__":
    unittest.main()
