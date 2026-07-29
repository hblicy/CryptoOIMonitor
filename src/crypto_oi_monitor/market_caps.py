from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
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


@dataclass(frozen=True)
class MarketCapLookup:
    market_caps: dict[str, MarketCap]
    unmapped_assets: tuple[str, ...]
    refreshed_at: str | None = None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CachedMarketCapLoader:
    def __init__(
        self,
        client: MarketCapHttpClient,
        refresh_seconds: int,
        clock: Callable[[], float] = monotonic,
        timestamp: Callable[[], str] = _utc_now,
        unmapped_retry_seconds: int = CMC_UNMAPPED_RETRY_SECONDS,
    ) -> None:
        self.client = client
        self.refresh_seconds = refresh_seconds
        self.clock = clock
        self.timestamp = timestamp
        self.unmapped_retry_seconds = unmapped_retry_seconds
        self.mapping_cache: dict[str, str] = {}
        self.unmapped_retry_at: dict[str, float] = {}
        self.cached_assets: frozenset[str] | None = None
        self.cached_lookup: MarketCapLookup | None = None
        self.last_refresh_at: float | None = None

    def __call__(self, selected_assets: set[str]) -> MarketCapLookup:
        assets = frozenset(selected_assets)
        now = self.clock()
        if (
            self.cached_lookup is not None
            and self.cached_assets == assets
            and self.last_refresh_at is not None
            and now - self.last_refresh_at < self.refresh_seconds
        ):
            return self.cached_lookup
        mapping_assets = {
            asset
            for asset in assets
            if asset not in self.mapping_cache
            and now >= self.unmapped_retry_at.get(asset, 0)
        }
        fetched = fetch_market_caps(
            self.client, set(assets), self.mapping_cache, mapping_assets
        )
        for asset in mapping_assets:
            if asset in self.mapping_cache:
                self.unmapped_retry_at.pop(asset, None)
            else:
                self.unmapped_retry_at[asset] = now + self.unmapped_retry_seconds
        lookup = MarketCapLookup(
            fetched.market_caps,
            fetched.unmapped_assets,
            self.timestamp(),
        )
        self.cached_assets = assets
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
    return MarketCapLookup(market_caps, tuple(sorted(unmapped)))


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
    candidates: dict[str, list[dict[str, Any]]] = {asset: [] for asset in selected_assets}
    assets_by_cmc_symbol: dict[str, list[str]] = {}
    for asset in selected_assets:
        assets_by_cmc_symbol.setdefault(CMC_SYMBOL_ALIASES.get(asset, asset), []).append(asset)
    for item in _data(mapping_payload):
        for asset in assets_by_cmc_symbol.get(item["symbol"].upper(), []):
            candidates[asset].append(item)

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


def fetch_market_caps(
    client: MarketCapHttpClient,
    selected_assets: set[str],
    mapping_cache: dict[str, str] | None = None,
    mapping_assets: set[str] | None = None,
) -> MarketCapLookup:
    if mapping_cache is None:
        mapping_payload = {
            "data": _fetch_mapping_data(
                client,
                sorted({CMC_SYMBOL_ALIASES.get(asset, asset) for asset in selected_assets}),
            )
        }
        mapped_ids, _ = _mapped_ids(mapping_payload, selected_assets)
    else:
        mapping_targets = selected_assets if mapping_assets is None else mapping_assets
        unknown_assets = sorted(
            asset
            for asset in mapping_targets
            if asset in selected_assets and asset not in mapping_cache
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
        mapped_ids = {
            asset: market_cap_id
            for asset, market_cap_id in mapping_cache.items()
            if asset in selected_assets
        }

    if not mapped_ids:
        return _market_cap_lookup(mapped_ids, {"data": []}, selected_assets)

    quote_data: list[dict[str, Any]] = []
    market_cap_ids = list(mapped_ids.values())
    for start in range(0, len(market_cap_ids), 100):
        ids = market_cap_ids[start : start + 100]
        quote_data.extend(
            _data(
                client.get_json(
                    CMC_QUOTES_URL,
                    {"id": ",".join(ids), "convert": "USD", "skip_invalid": "true"},
                )
            )
        )
    return _market_cap_lookup(mapped_ids, {"data": quote_data}, selected_assets)


def _fetch_mapping_data(
    client: MarketCapHttpClient, ordered_assets: list[str]
) -> list[dict[str, Any]]:
    mapping_data: list[dict[str, Any]] = []
    for start in range(0, len(ordered_assets), 100):
        symbols = ordered_assets[start : start + 100]
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
            unsupported_symbols = sorted(set(remaining_symbols) & invalid_symbols)
            if not unsupported_symbols:
                raise
            LOGGER.warning(
                "CoinMarketCap does not support symbols: %s",
                ", ".join(unsupported_symbols),
            )
            remaining_symbols = [
                symbol for symbol in remaining_symbols if symbol not in invalid_symbols
            ]
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
    if match is None:
        return None
    return {symbol.upper() for symbol in match.group(1).split(",")}


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
