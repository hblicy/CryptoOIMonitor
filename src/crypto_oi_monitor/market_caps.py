from __future__ import annotations

from dataclasses import dataclass
import logging
import re
from typing import Any, Protocol

from .http_client import DataSourceRequestError


CMC_ID_MAP_URL = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/map"
CMC_QUOTES_URL = "https://pro-api.coinmarketcap.com/v3/cryptocurrency/quotes/latest"
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


def parse_market_caps(
    mapping_payload: dict[str, Any],
    quote_payload: dict[str, Any],
    selected_assets: set[str],
) -> MarketCapLookup:
    mapped_ids, unmapped = _mapped_ids(mapping_payload, selected_assets)
    quotes_by_id = {str(item["id"]): item for item in _data(quote_payload)}
    market_caps: dict[str, MarketCap] = {}
    for asset, market_cap_id in mapped_ids.items():
        quote = quotes_by_id.get(market_cap_id)
        if quote is None:
            unmapped.append(asset)
            continue
        market_cap_usd = _usd_market_cap(quote)
        if market_cap_usd is None:
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
    for item in _data(mapping_payload):
        canonical = item["symbol"].upper()
        if canonical in candidates:
            candidates[canonical].append(item)

    mapped_ids: dict[str, str] = {}
    unmapped: list[str] = []
    for asset in sorted(selected_assets):
        matches = candidates[asset]
        if len(matches) != 1:
            unmapped.append(asset)
            continue
        mapped_ids[asset] = str(matches[0]["id"])

    return mapped_ids, unmapped


def fetch_market_caps(
    client: MarketCapHttpClient, selected_assets: set[str]
) -> MarketCapLookup:
    ordered_assets = sorted(selected_assets)
    mapping_data: list[dict[str, Any]] = []
    for start in range(0, len(ordered_assets), 100):
        symbols = ordered_assets[start : start + 100]
        mapping_data.extend(_fetch_mapping_batch(client, symbols))

    mapping_payload = {"data": mapping_data}
    mapped_ids, _ = _mapped_ids(mapping_payload, selected_assets)
    if not mapped_ids:
        return parse_market_caps(mapping_payload, {"data": []}, selected_assets)

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
    return parse_market_caps(mapping_payload, {"data": quote_data}, selected_assets)


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
    match = re.fullmatch(r'Invalid values for "symbol": "(.+)"', error_message)
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
