import unittest

from crypto_oi_monitor.domain import (
    HIGH_RISK,
    NORMAL,
    WARNING,
    ContractOpenInterest,
    TRADE_ENTRY_OI_TO_MARKET_CAP_RATIO,
    aggregate_asset,
    is_binance_universe_member,
    risk_status,
)


class RiskStatusTests(unittest.TestCase):
    def test_uses_ninety_percent_for_trade_entry(self) -> None:
        self.assertEqual(TRADE_ENTRY_OI_TO_MARKET_CAP_RATIO, 0.9)

    def test_marks_oi_above_110_percent_as_warning(self) -> None:
        self.assertEqual(risk_status(1.1), NORMAL)
        self.assertEqual(risk_status(1.100001), WARNING)

    def test_marks_oi_strictly_above_two_times_market_cap_as_high_risk(self) -> None:
        self.assertEqual(risk_status(2.0), WARNING)
        self.assertEqual(risk_status(2.000001), HIGH_RISK)

    def test_marks_oi_not_above_110_percent_as_normal(self) -> None:
        self.assertEqual(risk_status(1.099999), NORMAL)


class BinanceUniverseTests(unittest.TestCase):
    def test_uses_custom_binance_turnover_threshold(self) -> None:
        self.assertTrue(
            is_binance_universe_member("PERPETUAL", 8_000_000, 8_000_000)
        )
        self.assertFalse(
            is_binance_universe_member("PERPETUAL", 7_999_999.99, 8_000_000)
        )
        self.assertFalse(
            is_binance_universe_member("CURRENT_QUARTER", 50_000_000, 8_000_000)
        )


class AggregationTests(unittest.TestCase):
    def test_rejects_non_finite_or_negative_contract_oi(self) -> None:
        for value in (float("nan"), float("inf"), -1):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "finite non-negative"):
                    ContractOpenInterest("Binance", "ETHUSDT", value, "ETH")

    def test_rejects_an_overflowed_aggregate_oi_total(self) -> None:
        contracts = (
            ContractOpenInterest("Binance", "BTCUSDT", 1e308),
            ContractOpenInterest("OKX", "BTC-USDT-SWAP", 1e308),
        )

        with self.assertRaisesRegex(ValueError, "Aggregate OI must be finite"):
            aggregate_asset("BTC", "1", 1_000_000, contracts)

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
