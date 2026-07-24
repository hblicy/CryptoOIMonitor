from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


COINGECKO_MARKETS_URL = "https://api.coingecko.com/api/v3/coins/markets"


class MarketCapHttpClient(Protocol):
    def get_json(self, url: str, params: dict[str, str]) -> Any: ...


@dataclass(frozen=True)
class MarketCap:
    coingecko_id: str
    market_cap_usd: float


@dataclass(frozen=True)
class MarketCapLookup:
    market_caps: dict[str, MarketCap]
    unmapped_assets: tuple[str, ...]


def parse_market_caps(
    payload: list[dict[str, Any]], selected_assets: set[str]
) -> MarketCapLookup:
    candidates: dict[str, list[dict[str, Any]]] = {asset: [] for asset in selected_assets}
    for item in payload:
        canonical = item["symbol"].upper()
        if canonical in candidates and item["market_cap"] is not None:
            candidates[canonical].append(item)

    market_caps: dict[str, MarketCap] = {}
    unmapped: list[str] = []
    for asset in sorted(selected_assets):
        matches = candidates[asset]
        if len(matches) != 1:
            unmapped.append(asset)
            continue
        item = matches[0]
        market_caps[asset] = MarketCap(item["id"], float(item["market_cap"]))
    return MarketCapLookup(market_caps, tuple(unmapped))


def fetch_market_caps(
    client: MarketCapHttpClient, selected_assets: set[str]
) -> MarketCapLookup:
    payload: list[dict[str, Any]] = []
    ordered_assets = sorted(selected_assets)
    for start in range(0, len(ordered_assets), 50):
        symbols = ordered_assets[start : start + 50]
        response = client.get_json(
            COINGECKO_MARKETS_URL,
            {
                "vs_currency": "usd",
                "symbols": ",".join(symbol.lower() for symbol in symbols),
                "order": "market_cap_desc",
                "per_page": "250",
                "page": "1",
                "sparkline": "false",
            },
        )
        payload.extend(response)
    return parse_market_caps(payload, selected_assets)
