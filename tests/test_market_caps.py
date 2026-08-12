import unittest

from crypto_oi_monitor.http_client import DataSourceRequestError
from crypto_oi_monitor.market_caps import (
    CMC_ID_MAP_URL,
    CMC_QUOTES_URL,
    CachedMarketCapLoader,
    MarketCap,
    MarketCapCandidate,
    fetch_market_caps,
    parse_cmc_id_overrides,
    parse_market_caps,
)


class CoinMarketCapMarketCapTests(unittest.TestCase):
    def test_reads_usd_market_cap_from_v3_quote_list(self) -> None:
        result = parse_market_caps(
            {"data": [{"id": 1, "symbol": "BTC"}]},
            {
                "data": [
                    {
                        "id": 1,
                        "quote": [
                            {"symbol": "EUR", "market_cap": 900},
                            {"symbol": "USD", "market_cap": 1_000},
                        ],
                    }
                ]
            },
            {"BTC"},
        )

        self.assertEqual(result.market_caps["BTC"].market_cap_usd, 1_000)

    def test_accepts_string_zero_error_code_as_success(self) -> None:
        result = parse_market_caps(
            {
                "status": {"error_code": "0", "error_message": ""},
                "data": [{"id": 1, "symbol": "BTC"}],
            },
            {
                "status": {"error_code": "0", "error_message": ""},
                "data": [{"id": 1, "quote": {"USD": {"market_cap": 1_000}}}],
            },
            {"BTC"},
        )

        self.assertEqual(result.market_caps["BTC"].market_cap_usd, 1_000)

    def test_uses_unique_cmc_id_mapping_and_usd_market_cap(self) -> None:
        result = parse_market_caps(
            {
                "data": [
                    {"id": 1027, "symbol": "ETH"},
                    {"id": 1, "symbol": "BTC"},
                ]
            },
            {
                "data": [
                    {"id": 1027, "quote": {"USD": {"market_cap": 300_000_000_000}}},
                    {"id": 1, "quote": {"USD": {"market_cap": 2_000_000_000_000}}},
                ]
            },
            {"ETH", "BTC"},
        )

        self.assertEqual(result.market_caps["ETH"].market_cap_id, "1027")
        self.assertEqual(result.market_caps["BTC"].market_cap_usd, 2_000_000_000_000)
        self.assertEqual(result.unmapped_assets, ())

    def test_leaves_ambiguous_symbol_unmapped(self) -> None:
        result = parse_market_caps(
            {"data": [{"id": 1, "symbol": "AAA"}, {"id": 2, "symbol": "AAA"}]},
            {"data": []},
            {"AAA"},
        )

        self.assertEqual(result.market_caps, {})
        self.assertEqual(result.unmapped_assets, ("AAA",))

    def test_uses_the_only_active_cmc_entry_for_a_duplicate_symbol(self) -> None:
        result = parse_market_caps(
            {
                "data": [
                    {"id": 1, "symbol": "BTC", "is_active": 0},
                    {"id": 2, "symbol": "BTC", "is_active": 1},
                ]
            },
            {"data": [{"id": 2, "quote": {"USD": {"market_cap": 1_000}}}]},
            {"BTC"},
        )

        self.assertEqual(result.market_caps["BTC"].market_cap_id, "2")
        self.assertEqual(result.unmapped_assets, ())

    def test_logs_ambiguous_active_cmc_entries(self) -> None:
        with self.assertLogs("crypto_oi_monitor.market_caps", "WARNING") as logs:
            result = parse_market_caps(
                {
                    "data": [
                        {"id": 1, "symbol": "AAA", "is_active": 1},
                        {"id": 2, "symbol": "AAA", "is_active": 1},
                    ]
                },
                {"data": []},
                {"AAA"},
            )

        self.assertEqual(result.unmapped_assets, ("AAA",))
        self.assertIn("AAA has 2 active CoinMarketCap candidates", logs.output[0])

    def test_leaves_non_positive_market_cap_unmapped(self) -> None:
        result = parse_market_caps(
            {"data": [{"id": 1, "symbol": "ZERO"}]},
            {"data": [{"id": 1, "quote": {"USD": {"market_cap": 0}}}]},
            {"ZERO"},
        )

        self.assertEqual(result.market_caps, {})
        self.assertEqual(result.unmapped_assets, ("ZERO",))

    def test_fetches_mapping_then_quotes_by_cmc_id(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.calls: list[tuple[str, dict[str, str]]] = []

            def get_json(self, url: str, params: dict[str, str]):
                self.calls.append((url, params))
                if url == CMC_ID_MAP_URL:
                    return {"data": [{"id": 1027, "symbol": "ETH"}]}
                return {
                    "data": [
                        {"id": 1027, "quote": {"USD": {"market_cap": 300}}}
                    ]
                }

        client = FakeClient()
        result = fetch_market_caps(client, {"ETH"})

        self.assertEqual(result.market_caps["ETH"].market_cap_usd, 300)
        self.assertEqual(
            client.calls,
            [
                (CMC_ID_MAP_URL, {"symbol": "ETH"}),
                (CMC_QUOTES_URL, {"id": "1027", "convert": "USD", "skip_invalid": "true"}),
            ],
        )

    def test_maps_pharos_with_its_cmc_pros_symbol(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.calls: list[tuple[str, dict[str, str]]] = []

            def get_json(self, url: str, params: dict[str, str]):
                self.calls.append((url, params))
                if url == CMC_ID_MAP_URL:
                    return {"data": [{"id": 9999, "symbol": "PROS"}]}
                return {
                    "data": [
                        {"id": 9999, "quote": {"USD": {"market_cap": 300}}}
                    ]
                }

        client = FakeClient()
        result = fetch_market_caps(client, {"PHAROS"})

        self.assertEqual(result.market_caps["PHAROS"].market_cap_usd, 300)
        self.assertEqual(
            client.calls,
            [
                (CMC_ID_MAP_URL, {"symbol": "PROS"}),
                (CMC_QUOTES_URL, {"id": "9999", "convert": "USD", "skip_invalid": "true"}),
            ],
        )

    def test_maps_assets_when_given_an_empty_mapping_cache(self) -> None:
        class FakeClient:
            def get_json(self, url: str, params: dict[str, str]):
                if url == CMC_ID_MAP_URL:
                    return {"data": [{"id": 1027, "symbol": "ETH"}]}
                return {
                    "data": [
                        {"id": 1027, "quote": {"USD": {"market_cap": 300}}}
                    ]
                }

        mapping_cache: dict[str, str] = {}
        result = fetch_market_caps(FakeClient(), {"ETH"}, mapping_cache)

        self.assertEqual(mapping_cache, {"ETH": "1027"})
        self.assertEqual(result.market_caps["ETH"].market_cap_usd, 300)

    def test_caches_mappings_and_throttles_quote_refreshes(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.calls: list[tuple[str, dict[str, str]]] = []

            def get_json(self, url: str, params: dict[str, str]):
                self.calls.append((url, params))
                if url == CMC_ID_MAP_URL:
                    return {"data": [{"id": 1027, "symbol": "ETH"}]}
                return {
                    "data": [
                        {"id": 1027, "quote": {"USD": {"market_cap": 300}}}
                    ]
                }

        now = 0.0
        client = FakeClient()
        loader = CachedMarketCapLoader(
            client, refresh_seconds=600, clock=lambda: now
        )

        loader({"ETH"})
        now = 120.0
        loader({"ETH"})
        now = 600.0
        refreshed = loader({"ETH"})

        self.assertEqual(
            [url for url, _ in client.calls],
            [CMC_ID_MAP_URL, CMC_QUOTES_URL, CMC_QUOTES_URL],
        )
        self.assertIsNotNone(getattr(refreshed, "refreshed_at", None))

    def test_does_not_refresh_quotes_early_when_the_asset_pool_changes(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.calls = []

            def get_json(self, url: str, params: dict[str, str]):
                self.calls.append((url, params))
                if url == CMC_ID_MAP_URL:
                    return {
                        "data": [
                            {"id": index + 1, "symbol": symbol}
                            for index, symbol in enumerate(params["symbol"].split(","))
                        ]
                    }
                return {
                    "data": [
                        {
                            "id": int(market_cap_id),
                            "symbol": "ETH",
                            "quote": {"USD": {"market_cap": 300}},
                        }
                        for market_cap_id in params["id"].split(",")
                    ]
                }

        now = 0.0
        client = FakeClient()
        loader = CachedMarketCapLoader(client, refresh_seconds=600, clock=lambda: now)

        first = loader({"ETH"})
        now = 120.0
        changed = loader({"ETH", "NEW"})

        self.assertEqual(len(client.calls), 2)
        self.assertEqual(changed.market_caps, first.market_caps)
        self.assertEqual(changed.unmapped_assets, ("NEW",))

    def test_rejects_non_finite_market_cap(self) -> None:
        with self.assertRaisesRegex(ValueError, "finite positive"):
            MarketCap("1", float("nan"))

    def test_rejects_non_finite_candidate_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "candidate values must be finite"):
            MarketCapCandidate(
                "AAA", "1", "AAA", "aaa", float("nan"), 100, 1, None
            )

    def test_retries_unmapped_assets_after_one_hour(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.map_calls = 0
                self.quote_calls = 0

            def get_json(self, url: str, params: dict[str, str]):
                if url == CMC_ID_MAP_URL:
                    self.map_calls += 1
                    if self.map_calls == 1:
                        return {"data": []}
                    return {"data": [{"id": 1027, "symbol": "ETH"}]}
                self.quote_calls += 1
                return {
                    "data": [
                        {"id": 1027, "quote": {"USD": {"market_cap": 300}}}
                    ]
                }

        now = 0.0
        client = FakeClient()
        loader = CachedMarketCapLoader(
            client, refresh_seconds=600, clock=lambda: now
        )

        first = loader({"ETH"})
        now = 600.0
        loader({"ETH"})
        now = 3600.0
        retried = loader({"ETH"})

        self.assertEqual(first.unmapped_assets, ("ETH",))
        self.assertEqual(client.map_calls, 2)
        self.assertEqual(client.quote_calls, 1)
        self.assertEqual(retried.market_caps["ETH"].market_cap_usd, 300)

    def test_retries_mapping_after_a_cached_cmc_id_returns_no_market_cap(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.map_calls = 0

            def get_json(self, url: str, params: dict[str, str]):
                if url == CMC_ID_MAP_URL:
                    self.map_calls += 1
                    market_cap_id = 1 if self.map_calls == 1 else 2
                    return {"data": [{"id": market_cap_id, "symbol": "ETH"}]}
                if params["id"] == "1":
                    return {"data": [{"id": 1, "quote": {"USD": {"market_cap": 0}}}]}
                return {
                    "data": [
                        {"id": 2, "quote": {"USD": {"market_cap": 300}}}
                    ]
                }

        now = 0.0
        client = FakeClient()
        loader = CachedMarketCapLoader(
            client, refresh_seconds=600, clock=lambda: now
        )

        first = loader({"ETH"})
        now = 3_600.0
        retried = loader({"ETH"})

        self.assertEqual(first.unmapped_assets, ("ETH",))
        self.assertEqual(client.map_calls, 2)
        self.assertEqual(retried.market_caps["ETH"].market_cap_id, "2")

    def test_exposes_ambiguous_cmc_candidates_for_manual_mapping(self) -> None:
        class FakeClient:
            def get_json(self, url: str, params: dict[str, str]):
                if url == CMC_ID_MAP_URL:
                    return {
                        "data": [
                            {"id": 1, "symbol": "AAA", "name": "Alpha", "slug": "alpha"},
                            {"id": 2, "symbol": "AAA", "name": "Another", "slug": "another"},
                        ]
                    }
                return {
                    "data": [
                        {"id": 1, "quote": {"USD": {"price": 99, "market_cap": 1_000}}},
                        {"id": 2, "quote": {"USD": {"price": 200, "market_cap": 2_000}}},
                    ]
                }

        result = CachedMarketCapLoader(FakeClient(), refresh_seconds=600)(
            {"AAA"}, {"AAA": 100}
        )

        self.assertEqual(
            [
                (
                    candidate.asset,
                    candidate.market_cap_id,
                    candidate.name,
                    candidate.slug,
                    candidate.price_difference_percent,
                )
                for candidate in result.unmapped_candidates
            ],
            [
                ("AAA", "1", "Alpha", "alpha", 1.0),
                ("AAA", "2", "Another", "another", 100.0),
            ],
        )

    def test_refreshes_cached_candidate_prices_with_each_cmc_refresh(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.quote_calls = 0

            def get_json(self, url: str, params: dict[str, str]):
                if url == CMC_ID_MAP_URL:
                    return {
                        "data": [
                            {"id": 1, "symbol": "AAA", "name": "Alpha", "slug": "alpha"},
                            {"id": 2, "symbol": "AAA", "name": "Another", "slug": "another"},
                        ]
                    }
                self.quote_calls += 1
                prices = (99, 200) if self.quote_calls == 1 else (150, 101)
                return {
                    "data": [
                        {"id": 1, "quote": {"USD": {"price": prices[0], "market_cap": 1_000}}},
                        {"id": 2, "quote": {"USD": {"price": prices[1], "market_cap": 2_000}}},
                    ]
                }

        now = 0.0
        loader = CachedMarketCapLoader(
            FakeClient(), refresh_seconds=600, clock=lambda: now
        )

        loader({"AAA"}, {"AAA": 100})
        now = 600.0
        refreshed = loader({"AAA"}, {"AAA": 100})

        self.assertEqual(
            [candidate.market_cap_id for candidate in refreshed.unmapped_candidates],
            ["2", "1"],
        )
        self.assertEqual(refreshed.unmapped_candidates[0].price_usd, 101)
        self.assertEqual(refreshed.unmapped_candidates[0].price_difference_percent, 1)

    def test_uses_configured_cmc_id_override_for_non_alphanumeric_symbol(self) -> None:
        class FakeClient:
            def get_json(self, url: str, params: dict[str, str]):
                if url == CMC_ID_MAP_URL:
                    self.fail("Configured CMC ID must skip symbol mapping")
                return {
                    "data": [
                        {
                            "id": 39671,
                            "symbol": "龙虾",
                            "quote": {"USD": {"market_cap": 1_000}},
                        }
                    ]
                }

            def fail(self, message: str) -> None:
                raise AssertionError(message)

        result = fetch_market_caps(
            FakeClient(), {"龙虾"}, id_overrides={"龙虾": "39671"}
        )

        self.assertEqual(result.market_caps["龙虾"].market_cap_id, "39671")
        self.assertEqual(result.unmapped_assets, ())

    def test_logs_an_unusable_configured_cmc_id_override(self) -> None:
        class FakeClient:
            def get_json(self, url: str, params: dict[str, str]):
                if url == CMC_ID_MAP_URL:
                    raise AssertionError("Configured CMC ID must skip symbol mapping")
                return {
                    "data": [
                        {
                            "id": 1,
                            "symbol": "AAA",
                            "quote": {"USD": {"market_cap": 0}},
                        }
                    ]
                }

        with self.assertLogs("crypto_oi_monitor.market_caps", "WARNING") as logs:
            result = fetch_market_caps(
                FakeClient(), {"AAA"}, id_overrides={"AAA": "1"}
            )

        self.assertEqual(result.unmapped_assets, ("AAA",))
        self.assertIn(
            "CoinMarketCap configured ID override is unusable for AAA: 1",
            logs.output[0],
        )

    def test_rejects_configured_override_for_a_different_cmc_symbol(self) -> None:
        class FakeClient:
            def get_json(self, url: str, params: dict[str, str]):
                if url == CMC_ID_MAP_URL:
                    raise AssertionError("Configured CMC ID must skip symbol mapping")
                return {
                    "data": [
                        {
                            "id": 1,
                            "symbol": "WRONG",
                            "quote": {"USD": {"market_cap": 1_000}},
                        }
                    ]
                }

        with self.assertRaisesRegex(
            RuntimeError,
            "CMC_ID_OVERRIDES AAA:1 returned WRONG, expected AAA",
        ):
            fetch_market_caps(FakeClient(), {"AAA"}, id_overrides={"AAA": "1"})

    def test_parses_cmc_id_overrides(self) -> None:
        self.assertEqual(
            parse_cmc_id_overrides("btc:1, ETH:1027"),
            {"BTC": "1", "ETH": "1027"},
        )

    def test_rejects_invalid_cmc_id_overrides(self) -> None:
        with self.assertRaisesRegex(ValueError, "ASSET:CMC_ID"):
            parse_cmc_id_overrides("BTC=1")

    def test_skips_non_alphanumeric_symbol_without_failing_valid_assets(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.calls: list[tuple[str, dict[str, str]]] = []

            def get_json(self, url: str, params: dict[str, str]):
                self.calls.append((url, params))
                if url == CMC_ID_MAP_URL:
                    return {"data": [{"id": 1027, "symbol": "ETH"}]}
                return {
                    "data": [
                        {
                            "id": 1027,
                            "symbol": "ETH",
                            "quote": {"USD": {"market_cap": 300}},
                        }
                    ]
                }

        client = FakeClient()
        with self.assertLogs("crypto_oi_monitor.market_caps", "WARNING") as logs:
            result = fetch_market_caps(client, {"ETH", "龙虾"})

        self.assertEqual(result.market_caps["ETH"].market_cap_usd, 300)
        self.assertEqual(result.unmapped_assets, ("龙虾",))
        self.assertIn("龙虾", "\n".join(logs.output))
        self.assertEqual(client.calls[0], (CMC_ID_MAP_URL, {"symbol": "ETH"}))

    def test_bisects_generic_symbol_validation_error(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.map_symbols: list[str] = []

            def get_json(self, url: str, params: dict[str, str]):
                if url == CMC_ID_MAP_URL:
                    symbols = params["symbol"]
                    self.map_symbols.append(symbols)
                    if "REJECTED" in symbols:
                        error = DataSourceRequestError("CMC map rejected symbol")
                        error.status_code = 400
                        error.response_payload = {
                            "status": {
                                "error_message": (
                                    '"symbol" should only include comma-separated '
                                    "alphanumeric cryptocurrency symbols"
                                )
                            }
                        }
                        raise error
                    return {"data": [{"id": 1027, "symbol": "ETH"}]}
                return {
                    "data": [
                        {
                            "id": 1027,
                            "symbol": "ETH",
                            "quote": {"USD": {"market_cap": 300}},
                        }
                    ]
                }

        client = FakeClient()
        try:
            result = fetch_market_caps(client, {"ETH", "REJECTED"})
        except DataSourceRequestError as error:
            self.fail(f"symbol validation error escaped batch isolation: {error}")

        self.assertEqual(result.market_caps["ETH"].market_cap_usd, 300)
        self.assertEqual(result.unmapped_assets, ("REJECTED",))
        self.assertEqual(client.map_symbols, ["ETH,REJECTED", "ETH", "REJECTED"])

    def test_does_not_ignore_unrelated_symbol_map_http_400(self) -> None:
        class FakeClient:
            def get_json(self, url: str, params: dict[str, str]):
                error = DataSourceRequestError("CMC request invalid")
                error.status_code = 400
                error.response_payload = {
                    "status": {"error_message": '"symbol" parameter is missing'}
                }
                raise error

        with self.assertRaisesRegex(DataSourceRequestError, "request invalid"):
            fetch_market_caps(FakeClient(), {"ETH"})

    def test_skips_only_symbols_rejected_by_cmc_map(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.calls: list[tuple[str, dict[str, str]]] = []

            def get_json(self, url: str, params: dict[str, str]):
                self.calls.append((url, params))
                if url == CMC_ID_MAP_URL:
                    if "BROCCOLIF3B" in params["symbol"]:
                        error = DataSourceRequestError("CMC map rejected symbols")
                        error.status_code = 400
                        error.response_payload = {
                            "status": {
                                "error_message": (
                                    'Invalid values for "symbol": "BROCCOLIF3B,DODOX"'
                                )
                            }
                        }
                        raise error
                    return {"data": [{"id": 1027, "symbol": "ETH"}]}
                return {
                    "data": [
                        {"id": 1027, "quote": {"USD": {"market_cap": 300}}}
                    ]
                }

        client = FakeClient()
        with self.assertLogs("crypto_oi_monitor.market_caps", "WARNING") as logs:
            result = fetch_market_caps(client, {"BROCCOLIF3B", "DODOX", "ETH"})

        self.assertEqual(result.market_caps["ETH"].market_cap_usd, 300)
        self.assertEqual(result.unmapped_assets, ("BROCCOLIF3B", "DODOX"))
        self.assertIn("BROCCOLIF3B, DODOX", logs.output[0])
        self.assertEqual(
            client.calls,
            [
                (
                    CMC_ID_MAP_URL,
                    {"symbol": "BROCCOLIF3B,DODOX,ETH"},
                ),
                (CMC_ID_MAP_URL, {"symbol": "ETH"}),
                (
                    CMC_QUOTES_URL,
                    {"id": "1027", "convert": "USD", "skip_invalid": "true"},
                ),
            ],
        )

    def test_skips_symbol_rejected_with_singular_cmc_error(self) -> None:
        class FakeClient:
            def get_json(self, url: str, params: dict[str, str]):
                if url == CMC_ID_MAP_URL:
                    if "INVALID" in params["symbol"]:
                        error = DataSourceRequestError("CMC map rejected symbol")
                        error.status_code = 400
                        error.response_payload = {
                            "status": {
                                "error_message": 'Invalid value for "symbol": "INVALID"'
                            }
                        }
                        raise error
                    return {"data": [{"id": 1027, "symbol": "ETH"}]}
                return {
                    "data": [
                        {"id": 1027, "quote": {"USD": {"market_cap": 300}}}
                    ]
                }

        result = fetch_market_caps(FakeClient(), {"ETH", "INVALID"})

        self.assertEqual(result.market_caps["ETH"].market_cap_usd, 300)
        self.assertEqual(result.unmapped_assets, ("INVALID",))

    def test_does_not_ignore_other_cmc_map_errors(self) -> None:
        class FakeClient:
            def get_json(self, url: str, params: dict[str, str]):
                error = DataSourceRequestError("CMC authentication failed")
                error.status_code = 401
                error.response_payload = {
                    "status": {"error_message": "API key is invalid"}
                }
                raise error

        with self.assertRaisesRegex(DataSourceRequestError, "authentication failed"):
            fetch_market_caps(FakeClient(), {"ETH"})


if __name__ == "__main__":
    unittest.main()
