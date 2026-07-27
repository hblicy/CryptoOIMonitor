from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


CMC_ID_MAP_URL = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/map"
CMC_QUOTES_URL = "https://pro-api.coinmarketcap.com/v3/cryptocurrency/quotes/latest"


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
        market_cap_usd = quote["quote"]["USD"]["market_cap"]
        if market_cap_usd is None:
            unmapped.append(asset)
            continue
        market_caps[asset] = MarketCap(
            market_cap_id, float(market_cap_usd)
        )
    return MarketCapLookup(market_caps, tuple(sorted(unmapped)))


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
        mapping_data.extend(
            _data(client.get_json(CMC_ID_MAP_URL, {"symbol": ",".join(symbols)}))
        )

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
