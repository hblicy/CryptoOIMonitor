from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import logging
import math
import re
from time import monotonic
from typing import Any, Callable, Protocol

from .http_client import DataSourceRequestError


CMC_ID_MAP_URL = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/map"
CMC_QUOTES_URL = "https://pro-api.coinmarketcap.com/v3/cryptocurrency/quotes/latest"
CMC_UNMAPPED_RETRY_SECONDS = 3600
CMC_SYMBOL_ALIASES = {"PHAROS": "PROS"}
LOGGER = logging.getLogger(__name__)


class MarketCapHttpClient(Protocol):
    def get_json(self, url: str, params: dict[str, str]) -> Any: ...


@dataclass(frozen=True)
class MarketCap:
    market_cap_id: str
    market_cap_usd: float

    def __post_init__(self) -> None:
        if not math.isfinite(self.market_cap_usd) or self.market_cap_usd <= 0:
            raise ValueError("Market cap must be finite positive")


@dataclass(frozen=True)
class MarketCapCandidate:
    asset: str
    market_cap_id: str
    name: str
    slug: str
    price_usd: float | None
    market_cap_usd: float | None
    binance_price_usd: float | None
    price_difference_percent: float | None

    def __post_init__(self) -> None:
        values = (
            self.price_usd,
            self.market_cap_usd,
            self.binance_price_usd,
            self.price_difference_percent,
        )
        if any(
            value is not None and (not math.isfinite(value) or value < 0)
            for value in values
        ):
            raise ValueError(
                "CoinMarketCap candidate values must be finite non-negative"
            )

    def as_dict(self) -> dict[str, str | float | None]:
        return {
            "asset": self.asset,
            "market_cap_id": self.market_cap_id,
            "name": self.name,
            "slug": self.slug,
            "price_usd": self.price_usd,
            "market_cap_usd": self.market_cap_usd,
            "binance_price_usd": self.binance_price_usd,
            "price_difference_percent": self.price_difference_percent,
        }


@dataclass(frozen=True)
class MarketCapLookup:
    market_caps: dict[str, MarketCap]
    unmapped_assets: tuple[str, ...]
    refreshed_at: str | None = None
    unmapped_candidates: tuple[MarketCapCandidate, ...] = ()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_cmc_id_overrides(value: str | None) -> dict[str, str]:
    if not value:
        return {}
    overrides: dict[str, str] = {}
    for entry in value.split(","):
        asset, separator, market_cap_id = entry.strip().partition(":")
        asset = asset.upper()
        if not separator or not asset or not market_cap_id.isdecimal():
            raise ValueError(
                "CMC_ID_OVERRIDES must use comma-separated ASSET:CMC_ID entries"
            )
        if asset in overrides:
            raise ValueError(f"CMC_ID_OVERRIDES repeats asset: {asset}")
        overrides[asset] = market_cap_id
    return overrides


class CachedMarketCapLoader:
    def __init__(
        self,
        client: MarketCapHttpClient,
        refresh_seconds: int,
        clock: Callable[[], float] = monotonic,
        timestamp: Callable[[], str] = _utc_now,
        unmapped_retry_seconds: int = CMC_UNMAPPED_RETRY_SECONDS,
        id_overrides: dict[str, str] | None = None,
    ) -> None:
        self.client = client
        self.refresh_seconds = refresh_seconds
        self.clock = clock
        self.timestamp = timestamp
        self.unmapped_retry_seconds = unmapped_retry_seconds
        self.id_overrides = dict(id_overrides or {})
        self.mapping_cache: dict[str, str] = {}
        self.unmapped_retry_at: dict[str, float] = {}
        self.unmapped_candidate_cache: dict[str, tuple[MarketCapCandidate, ...]] = {}
        self.cached_lookup: MarketCapLookup | None = None
        self.last_refresh_at: float | None = None

    def __call__(
        self,
        selected_assets: set[str],
        binance_prices: dict[str, float] | None = None,
    ) -> MarketCapLookup:
        assets = frozenset(selected_assets)
        now = self.clock()
        if (
            self.cached_lookup is not None
            and self.last_refresh_at is not None
            and now - self.last_refresh_at < self.refresh_seconds
        ):
            market_caps = {
                asset: market_cap
                for asset, market_cap in self.cached_lookup.market_caps.items()
                if asset in assets
            }
            return MarketCapLookup(
                market_caps=market_caps,
                unmapped_assets=tuple(sorted(assets - set(market_caps))),
                refreshed_at=self.cached_lookup.refreshed_at,
                unmapped_candidates=tuple(
                    candidate
                    for candidate in self.cached_lookup.unmapped_candidates
                    if candidate.asset in assets
                ),
            )
        mapping_assets = {
            asset
            for asset in assets
            if asset not in self.mapping_cache
            and asset not in self.id_overrides
            and now >= self.unmapped_retry_at.get(asset, 0)
        }
        fetched = fetch_market_caps(
            self.client,
            set(assets),
            self.mapping_cache,
            mapping_assets,
            binance_prices,
            self.id_overrides,
        )
        invalid_cached_assets = (
            set(fetched.unmapped_assets)
            & set(self.mapping_cache)
            - set(self.id_overrides)
        )
        for asset in invalid_cached_assets:
            self.mapping_cache.pop(asset, None)
            self.unmapped_retry_at[asset] = now + self.unmapped_retry_seconds
            LOGGER.warning(
                "CoinMarketCap cached mapping invalidated for %s; retrying in %s seconds",
                asset,
                self.unmapped_retry_seconds,
            )
        for asset in mapping_assets:
            if asset in self.mapping_cache:
                self.unmapped_retry_at.pop(asset, None)
            else:
                self.unmapped_retry_at[asset] = now + self.unmapped_retry_seconds
        fetched_candidates = _group_candidates(fetched.unmapped_candidates)
        for asset in mapping_assets:
            candidates = fetched_candidates.get(asset)
            if candidates:
                self.unmapped_candidate_cache[asset] = candidates
            else:
                self.unmapped_candidate_cache.pop(asset, None)
        for asset in self.mapping_cache:
            self.unmapped_candidate_cache.pop(asset, None)
        for asset in self.id_overrides:
            self.unmapped_candidate_cache.pop(asset, None)
        for asset in tuple(self.unmapped_candidate_cache):
            if asset not in assets:
                self.unmapped_candidate_cache.pop(asset)
        _refresh_cached_candidate_prices(
            self.client,
            self.unmapped_candidate_cache,
            binance_prices or {},
            set(self.unmapped_candidate_cache) - mapping_assets,
        )
        lookup = MarketCapLookup(
            fetched.market_caps,
            fetched.unmapped_assets,
            self.timestamp(),
            tuple(
                candidate
                for asset in fetched.unmapped_assets
                for candidate in self.unmapped_candidate_cache.get(asset, ())
            ),
        )
        self.cached_lookup = lookup
        self.last_refresh_at = now
        LOGGER.info(
            "CoinMarketCap market caps refreshed: %s mapped, %s unmapped",
            len(lookup.market_caps),
            len(lookup.unmapped_assets),
        )
        return lookup


def parse_market_caps(
    mapping_payload: dict[str, Any],
    quote_payload: dict[str, Any],
    selected_assets: set[str],
) -> MarketCapLookup:
    mapped_ids, _ = _mapped_ids(mapping_payload, selected_assets)
    return _market_cap_lookup(mapped_ids, quote_payload, selected_assets)


def _market_cap_lookup(
    mapped_ids: dict[str, str],
    quote_payload: dict[str, Any],
    selected_assets: set[str],
    unmapped_candidates: tuple[MarketCapCandidate, ...] = (),
) -> MarketCapLookup:
    unmapped = sorted(set(selected_assets) - set(mapped_ids))
    quotes_by_id = {str(item["id"]): item for item in _data(quote_payload)}
    market_caps: dict[str, MarketCap] = {}
    for asset, market_cap_id in mapped_ids.items():
        quote = quotes_by_id.get(market_cap_id)
        if quote is None:
            unmapped.append(asset)
            continue
        market_cap_usd = _usd_market_cap(quote)
        if market_cap_usd is None or float(market_cap_usd) <= 0:
            unmapped.append(asset)
            continue
        market_caps[asset] = MarketCap(
            market_cap_id, float(market_cap_usd)
        )
    return MarketCapLookup(
        market_caps,
        tuple(sorted(unmapped)),
        unmapped_candidates=unmapped_candidates,
    )


def _usd_market_cap(quote: dict[str, Any]) -> Any:
    quote_data = quote["quote"]
    if isinstance(quote_data, list):
        for conversion in quote_data:
            if conversion["symbol"] == "USD":
                return conversion["market_cap"]
        raise KeyError("USD quote is missing")
    return quote_data["USD"]["market_cap"]


def _mapped_ids(
    mapping_payload: dict[str, Any], selected_assets: set[str]
) -> tuple[dict[str, str], list[str]]:
    candidates = _mapping_candidates(mapping_payload, selected_assets)

    mapped_ids: dict[str, str] = {}
    unmapped: list[str] = []
    for asset in sorted(selected_assets):
        matches = candidates[asset]
        active_matches = [
            match for match in matches if match.get("is_active", 1) == 1
        ]
        if len(active_matches) != 1:
            if matches:
                LOGGER.warning(
                    "CoinMarketCap mapping unresolved: %s has %s active "
                    "CoinMarketCap candidates (%s total)",
                    asset,
                    len(active_matches),
                    len(matches),
                )
            unmapped.append(asset)
            continue
        mapped_ids[asset] = str(active_matches[0]["id"])

    return mapped_ids, unmapped


def _mapping_candidates(
    mapping_payload: dict[str, Any], selected_assets: set[str]
) -> dict[str, list[dict[str, Any]]]:
    candidates: dict[str, list[dict[str, Any]]] = {asset: [] for asset in selected_assets}
    assets_by_cmc_symbol: dict[str, list[str]] = {}
    for asset in selected_assets:
        assets_by_cmc_symbol.setdefault(
            CMC_SYMBOL_ALIASES.get(asset, asset), []
        ).append(asset)
    for item in _data(mapping_payload):
        for asset in assets_by_cmc_symbol.get(item["symbol"].upper(), []):
            candidates[asset].append(item)
    return candidates


def _group_candidates(
    candidates: tuple[MarketCapCandidate, ...],
) -> dict[str, tuple[MarketCapCandidate, ...]]:
    grouped: dict[str, list[MarketCapCandidate]] = {}
    for candidate in candidates:
        grouped.setdefault(candidate.asset, []).append(candidate)
    return {asset: tuple(values) for asset, values in grouped.items()}


def _refresh_cached_candidate_prices(
    client: MarketCapHttpClient,
    candidate_cache: dict[str, tuple[MarketCapCandidate, ...]],
    binance_prices: dict[str, float],
    assets: set[str],
) -> None:
    candidate_ids = sorted(
        {
            candidate.market_cap_id
            for asset in assets
            for candidate in candidate_cache[asset]
        }
    )
    if not candidate_ids:
        return
    quotes_by_id = {
        str(item["id"]): item for item in _fetch_quote_data(client, candidate_ids)
    }
    for asset in assets:
        binance_price = binance_prices.get(asset)
        updated_candidates = []
        for candidate in candidate_cache[asset]:
            quote = quotes_by_id.get(candidate.market_cap_id)
            price = _usd_quote_value(quote, "price") if quote is not None else None
            market_cap = (
                _usd_quote_value(quote, "market_cap") if quote is not None else None
            )
            price_difference_percent = None
            if price is not None and binance_price is not None and binance_price > 0:
                price_difference_percent = abs(price - binance_price) / binance_price * 100
            updated_candidates.append(
                replace(
                    candidate,
                    price_usd=price,
                    market_cap_usd=market_cap,
                    binance_price_usd=binance_price,
                    price_difference_percent=price_difference_percent,
                )
            )
        candidate_cache[asset] = tuple(sorted(updated_candidates, key=_candidate_sort_key))


def fetch_market_caps(
    client: MarketCapHttpClient,
    selected_assets: set[str],
    mapping_cache: dict[str, str] | None = None,
    mapping_assets: set[str] | None = None,
    binance_prices: dict[str, float] | None = None,
    id_overrides: dict[str, str] | None = None,
) -> MarketCapLookup:
    overrides = {
        asset: market_cap_id
        for asset, market_cap_id in (id_overrides or {}).items()
        if asset in selected_assets
    }
    mapping_payload: dict[str, Any] | None = None
    candidate_assets: set[str] = set()
    if mapping_cache is None:
        candidate_assets = set(selected_assets) - set(overrides)
        mapping_payload = {
            "data": _fetch_mapping_data(
                client,
                sorted(
                    {
                        CMC_SYMBOL_ALIASES.get(asset, asset)
                        for asset in candidate_assets
                    }
                ),
            )
        }
        mapped_ids, _ = _mapped_ids(mapping_payload, candidate_assets)
        mapped_ids = {**mapped_ids, **overrides}
    else:
        mapping_targets = selected_assets if mapping_assets is None else mapping_assets
        unknown_assets = sorted(
            asset
            for asset in mapping_targets
            if (
                asset in selected_assets
                and asset not in mapping_cache
                and asset not in overrides
            )
        )
        if unknown_assets:
            mapping_payload = {
                "data": _fetch_mapping_data(
                    client,
                    sorted({CMC_SYMBOL_ALIASES.get(asset, asset) for asset in unknown_assets}),
                )
            }
            mapped_unknown_assets, _ = _mapped_ids(mapping_payload, set(unknown_assets))
            mapping_cache.update(mapped_unknown_assets)
            candidate_assets = set(unknown_assets)
        mapped_ids = {
            asset: market_cap_id
            for asset, market_cap_id in mapping_cache.items()
            if asset in selected_assets
        }
        mapped_ids.update(overrides)

    unmapped_candidates = ()
    if mapping_payload is not None:
        unmapped_candidates = _unmapped_candidates(
            mapping_payload,
            candidate_assets,
            _fetch_quote_data(
                client,
                _ambiguous_active_candidate_ids(mapping_payload, candidate_assets),
            ),
            binance_prices or {},
        )

    quote_data = _fetch_quote_data(client, list(mapped_ids.values()))
    quotes_by_id = {str(item["id"]): item for item in quote_data}
    for asset, market_cap_id in overrides.items():
        quote = quotes_by_id.get(market_cap_id)
        if quote is None:
            continue
        expected_symbol = CMC_SYMBOL_ALIASES.get(asset, asset)
        returned_symbol = str(quote["symbol"]).upper()
        if returned_symbol != expected_symbol:
            raise RuntimeError(
                "CMC_ID_OVERRIDES "
                f"{asset}:{market_cap_id} returned {returned_symbol}, "
                f"expected {expected_symbol}"
            )
    lookup = _market_cap_lookup(
        mapped_ids,
        {"data": quote_data},
        selected_assets,
        unmapped_candidates,
    )
    for asset, market_cap_id in overrides.items():
        if asset in lookup.unmapped_assets:
            LOGGER.warning(
                "CoinMarketCap configured ID override is unusable for %s: %s",
                asset,
                market_cap_id,
            )
    return lookup


def _fetch_quote_data(
    client: MarketCapHttpClient, market_cap_ids: list[str]
) -> list[dict[str, Any]]:
    quote_data: list[dict[str, Any]] = []
    for start in range(0, len(market_cap_ids), 100):
        ids = market_cap_ids[start : start + 100]
        if not ids:
            continue
        quote_data.extend(
            _data(
                client.get_json(
                    CMC_QUOTES_URL,
                    {"id": ",".join(ids), "convert": "USD", "skip_invalid": "true"},
                )
            )
        )
    return quote_data


def _ambiguous_active_candidate_ids(
    mapping_payload: dict[str, Any], selected_assets: set[str]
) -> list[str]:
    candidate_ids = {
        str(match["id"])
        for matches in _mapping_candidates(mapping_payload, selected_assets).values()
        for active_matches in [
            [match for match in matches if match.get("is_active", 1) == 1]
        ]
        if len(active_matches) != 1
        for match in active_matches
    }
    return sorted(candidate_ids)


def _unmapped_candidates(
    mapping_payload: dict[str, Any],
    selected_assets: set[str],
    quote_data: list[dict[str, Any]],
    binance_prices: dict[str, float],
) -> tuple[MarketCapCandidate, ...]:
    quotes_by_id = {str(item["id"]): item for item in quote_data}
    candidates: list[MarketCapCandidate] = []
    for asset, matches in _mapping_candidates(mapping_payload, selected_assets).items():
        active_matches = [
            match for match in matches if match.get("is_active", 1) == 1
        ]
        if len(active_matches) == 1:
            continue
        binance_price = binance_prices.get(asset)
        for match in active_matches:
            quote = quotes_by_id.get(str(match["id"]))
            price = _usd_quote_value(quote, "price") if quote is not None else None
            market_cap = (
                _usd_quote_value(quote, "market_cap") if quote is not None else None
            )
            price_difference_percent = None
            if price is not None and binance_price is not None and binance_price > 0:
                price_difference_percent = abs(price - binance_price) / binance_price * 100
            candidates.append(
                MarketCapCandidate(
                    asset=asset,
                    market_cap_id=str(match["id"]),
                    name=str(match.get("name", "")),
                    slug=str(match.get("slug", "")),
                    price_usd=price,
                    market_cap_usd=market_cap,
                    binance_price_usd=binance_price,
                    price_difference_percent=price_difference_percent,
                )
            )
    return tuple(
        sorted(candidates, key=_candidate_sort_key)
    )


def _candidate_sort_key(candidate: MarketCapCandidate) -> tuple[str, float, str]:
    return (
        candidate.asset,
        float("inf")
        if candidate.price_difference_percent is None
        else candidate.price_difference_percent,
        candidate.market_cap_id,
    )


def _usd_quote_value(quote: dict[str, Any], field: str) -> float | None:
    quote_data = quote["quote"]
    if isinstance(quote_data, list):
        for conversion in quote_data:
            if conversion["symbol"] == "USD":
                value = conversion.get(field)
                return None if value is None else float(value)
        return None
    value = quote_data["USD"].get(field)
    return None if value is None else float(value)


def _fetch_mapping_data(
    client: MarketCapHttpClient, ordered_assets: list[str]
) -> list[dict[str, Any]]:
    supported_symbols = [
        symbol for symbol in ordered_assets if re.fullmatch(r"[A-Za-z0-9]+", symbol)
    ]
    unsupported_symbols = sorted(set(ordered_assets) - set(supported_symbols))
    if unsupported_symbols:
        LOGGER.warning(
            "CoinMarketCap map cannot query non-alphanumeric symbols: %s",
            ", ".join(unsupported_symbols),
        )
    mapping_data: list[dict[str, Any]] = []
    for start in range(0, len(supported_symbols), 100):
        symbols = supported_symbols[start : start + 100]
        mapping_data.extend(_fetch_mapping_batch(client, symbols))
    return mapping_data


def _fetch_mapping_batch(
    client: MarketCapHttpClient, symbols: list[str]
) -> list[dict[str, Any]]:
    remaining_symbols = symbols
    while remaining_symbols:
        try:
            return _data(
                client.get_json(
                    CMC_ID_MAP_URL,
                    {"symbol": ",".join(remaining_symbols)},
                )
            )
        except DataSourceRequestError as error:
            invalid_symbols = _invalid_map_symbols(error)
            if invalid_symbols is None:
                raise
            if invalid_symbols:
                unsupported_symbols = sorted(set(remaining_symbols) & invalid_symbols)
                if not unsupported_symbols:
                    raise
                LOGGER.warning(
                    "CoinMarketCap does not support symbols: %s",
                    ", ".join(unsupported_symbols),
                )
                remaining_symbols = [
                    symbol
                    for symbol in remaining_symbols
                    if symbol not in invalid_symbols
                ]
                continue
            if len(remaining_symbols) == 1:
                LOGGER.warning(
                    "CoinMarketCap rejected symbol %s: %s",
                    remaining_symbols[0],
                    error,
                )
                return []
            middle = len(remaining_symbols) // 2
            return _fetch_mapping_batch(
                client, remaining_symbols[:middle]
            ) + _fetch_mapping_batch(client, remaining_symbols[middle:])
    return []


def _invalid_map_symbols(error: DataSourceRequestError) -> set[str] | None:
    if error.status_code != 400 or not isinstance(error.response_payload, dict):
        return None
    status = error.response_payload.get("status")
    if not isinstance(status, dict):
        return None
    error_message = status.get("error_message")
    if not isinstance(error_message, str):
        return None
    match = re.fullmatch(r'Invalid values? for "symbol": "(.+)"', error_message)
    if match is not None:
        return {symbol.upper() for symbol in match.group(1).split(",")}
    normalized_error_message = error_message.lower()
    if "symbol" in normalized_error_message and "alphanumeric" in normalized_error_message:
        return set()
    return None


def _data(payload: dict[str, Any]) -> list[dict[str, Any]]:
    status = payload.get("status")
    if isinstance(status, dict):
        error_code = status.get("error_code")
        if error_code is not None and int(error_code) != 0:
            raise RuntimeError(
                status.get("error_message") or "CoinMarketCap API rejected request"
            )
    data = payload["data"]
    if not isinstance(data, list):
        raise TypeError("CoinMarketCap API data must be a list")
    return data
