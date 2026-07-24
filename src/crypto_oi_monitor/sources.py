from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Protocol

from .domain import ContractOpenInterest, is_binance_universe_member


@dataclass(frozen=True)
class BinanceInstrument:
    canonical_symbol: str
    symbol: str


class PublicHttpClient(Protocol):
    def get_json(self, url: str, params: dict[str, str] | None = None) -> Any: ...

    def post_json(self, url: str, payload: dict[str, str]) -> Any: ...


BINANCE_EXCHANGE_INFO_URL = "https://fapi.binance.com/fapi/v1/exchangeInfo"
BINANCE_TICKERS_URL = "https://fapi.binance.com/fapi/v1/ticker/24hr"
BINANCE_MARK_PRICE_URL = "https://fapi.binance.com/fapi/v1/premiumIndex"
BINANCE_OPEN_INTEREST_URL = "https://fapi.binance.com/fapi/v1/openInterest"

OKX_OPEN_INTEREST_URL = "https://www.okx.com/api/v5/public/open-interest"
BYBIT_TICKERS_URL = "https://api.bybit.com/v5/market/tickers"
BITGET_OPEN_INTEREST_URL = "https://api.bitget.com/api/v3/market/open-interest"
BITGET_TICKERS_URL = "https://api.bitget.com/api/v2/mix/market/tickers"
BITGET_CONTRACTS_URL = "https://api.bitget.com/api/v2/mix/market/contracts"
GATE_TICKERS_URL = "https://api.gateio.ws/api/v4/futures/usdt/tickers"
GATE_CONTRACTS_URL = "https://api.gateio.ws/api/v4/futures/usdt/contracts"
HYPERLIQUID_INFO_URL = "https://api.hyperliquid.xyz/info"

ASTER_EXCHANGE_INFO_URL = "https://fapi.asterdex.com/fapi/v1/exchangeInfo"
ASTER_MARK_PRICE_URL = "https://fapi.asterdex.com/fapi/v1/premiumIndex"
ASTER_OPEN_INTEREST_URL = "https://fapi.asterdex.com/fapi/v1/openInterest"
MAX_OI_WORKERS = 50


def canonical_symbol(raw_symbol: str) -> str:
    symbol = raw_symbol.upper()
    for prefix in ("1000000", "100000", "10000", "1000"):
        if symbol.startswith(prefix) and len(symbol) > len(prefix):
            return symbol[len(prefix) :]
    return symbol


def parse_binance_universe(
    exchange_info: dict[str, Any], tickers: list[dict[str, Any]]
) -> dict[str, BinanceInstrument]:
    turnover_by_symbol = {
        ticker["symbol"]: float(ticker["quoteVolume"]) for ticker in tickers
    }
    universe: dict[str, BinanceInstrument] = {}
    for instrument in exchange_info["symbols"]:
        if instrument["quoteAsset"] != "USDT" or instrument["status"] != "TRADING":
            continue
        turnover = turnover_by_symbol.get(instrument["symbol"])
        if turnover is None or not is_binance_universe_member(
            instrument["contractType"], turnover
        ):
            continue
        canonical = canonical_symbol(instrument["baseAsset"])
        if canonical in universe:
            raise ValueError(f"Binance universe has duplicate canonical symbol: {canonical}")
        universe[canonical] = BinanceInstrument(canonical, instrument["symbol"])
    return universe


def parse_binance_open_interest(
    canonical: str,
    symbol: str,
    open_interest: dict[str, Any],
    mark_prices: dict[str, str],
) -> ContractOpenInterest:
    return _base_oi_with_mark_price(
        "Binance", canonical, symbol, open_interest, mark_prices
    )


def parse_aster_open_interest(
    canonical: str,
    symbol: str,
    open_interest: dict[str, Any],
    mark_prices: dict[str, str],
) -> ContractOpenInterest:
    return _base_oi_with_mark_price("Aster", canonical, symbol, open_interest, mark_prices)


def _base_oi_with_mark_price(
    venue: str,
    canonical: str,
    symbol: str,
    open_interest: dict[str, Any],
    mark_prices: dict[str, str],
) -> ContractOpenInterest:
    return ContractOpenInterest(
        venue=venue,
        symbol=symbol,
        oi_usd=float(open_interest["openInterest"]) * float(mark_prices[symbol]),
        canonical_symbol=canonical,
    )


def parse_okx_open_interest(
    payload: dict[str, Any], selected_assets: set[str]
) -> list[ContractOpenInterest]:
    contracts: list[ContractOpenInterest] = []
    for item in payload["data"]:
        canonical = canonical_symbol(item["instId"].split("-")[0])
        if canonical in selected_assets:
            contracts.append(
                ContractOpenInterest("OKX", item["instId"], float(item["oiUsd"]), canonical)
            )
    return contracts


def parse_bybit_open_interest(
    payload: dict[str, Any], selected_assets: set[str]
) -> list[ContractOpenInterest]:
    contracts: list[ContractOpenInterest] = []
    for item in payload["result"]["list"]:
        symbol = item["symbol"]
        if not symbol.endswith("USDT"):
            continue
        canonical = canonical_symbol(symbol.removesuffix("USDT"))
        if canonical in selected_assets:
            contracts.append(
                ContractOpenInterest("Bybit", symbol, float(item["openInterestValue"]), canonical)
            )
    return contracts


def parse_bitget_open_interest(
    open_interest_payload: dict[str, Any],
    tickers_payload: dict[str, Any],
    contracts_payload: dict[str, Any],
    selected_assets: set[str],
) -> list[ContractOpenInterest]:
    tickers = {item["symbol"]: item for item in tickers_payload["data"]}
    contracts = {item["symbol"]: item for item in contracts_payload["data"]}
    result: list[ContractOpenInterest] = []
    for item in open_interest_payload["data"]["list"]:
        symbol = item["symbol"]
        contract = contracts.get(symbol)
        if contract is None:
            continue
        canonical = canonical_symbol(contract["baseCoin"])
        if canonical not in selected_assets:
            continue
        oi_usd = (
            float(item["openInterest"])
            * float(tickers[symbol]["markPrice"])
        )
        result.append(ContractOpenInterest("Bitget", symbol, oi_usd, canonical))
    return result


def parse_gate_open_interest(
    tickers: list[dict[str, Any]],
    contracts_payload: list[dict[str, Any]],
    selected_assets: set[str],
) -> list[ContractOpenInterest]:
    multipliers = {
        item["name"]: float(item["quanto_multiplier"]) for item in contracts_payload
    }
    result: list[ContractOpenInterest] = []
    for ticker in tickers:
        symbol = ticker["contract"]
        base, settle = symbol.rsplit("_", maxsplit=1)
        if settle != "USDT":
            continue
        canonical = canonical_symbol(base)
        if canonical not in selected_assets:
            continue
        oi_usd = (
            float(ticker["total_size"])
            * multipliers[symbol]
            * float(ticker["mark_price"])
        )
        result.append(ContractOpenInterest("Gate", symbol, oi_usd, canonical))
    return result


def parse_hyperliquid_open_interest(
    payload: list[Any], selected_assets: set[str]
) -> list[ContractOpenInterest]:
    meta, contexts = payload
    result: list[ContractOpenInterest] = []
    for instrument, context in zip(meta["universe"], contexts, strict=True):
        canonical = canonical_symbol(instrument["name"])
        if canonical not in selected_assets:
            continue
        result.append(
            ContractOpenInterest(
                "Hyperliquid",
                instrument["name"],
                float(context["openInterest"]) * float(context["markPx"]),
                canonical,
            )
        )
    return result


def fetch_binance_universe(client: PublicHttpClient) -> dict[str, BinanceInstrument]:
    return parse_binance_universe(
        client.get_json(BINANCE_EXCHANGE_INFO_URL),
        client.get_json(BINANCE_TICKERS_URL),
    )


def fetch_binance_open_interest(
    client: PublicHttpClient, universe: dict[str, BinanceInstrument]
) -> list[ContractOpenInterest]:
    mark_prices = {
        item["symbol"]: item["markPrice"]
        for item in client.get_json(BINANCE_MARK_PRICE_URL)
    }
    return _fetch_base_oi_for_universe(
        client,
        universe,
        BINANCE_OPEN_INTEREST_URL,
        mark_prices,
        parse_binance_open_interest,
    )


def fetch_okx_open_interest(
    client: PublicHttpClient, selected_assets: set[str]
) -> list[ContractOpenInterest]:
    return parse_okx_open_interest(
        client.get_json(OKX_OPEN_INTEREST_URL, {"instType": "SWAP"}), selected_assets
    )


def fetch_bybit_open_interest(
    client: PublicHttpClient, selected_assets: set[str]
) -> list[ContractOpenInterest]:
    return parse_bybit_open_interest(
        client.get_json(BYBIT_TICKERS_URL, {"category": "linear"}), selected_assets
    )


def fetch_bitget_open_interest(
    client: PublicHttpClient, selected_assets: set[str]
) -> list[ContractOpenInterest]:
    params = {"productType": "USDT-FUTURES"}
    return parse_bitget_open_interest(
        client.get_json(BITGET_OPEN_INTEREST_URL, {"category": "USDT-FUTURES"}),
        client.get_json(BITGET_TICKERS_URL, params),
        client.get_json(BITGET_CONTRACTS_URL, params),
        selected_assets,
    )


def fetch_gate_open_interest(
    client: PublicHttpClient, selected_assets: set[str]
) -> list[ContractOpenInterest]:
    return parse_gate_open_interest(
        client.get_json(GATE_TICKERS_URL),
        client.get_json(GATE_CONTRACTS_URL),
        selected_assets,
    )


def fetch_hyperliquid_open_interest(
    client: PublicHttpClient, selected_assets: set[str]
) -> list[ContractOpenInterest]:
    return parse_hyperliquid_open_interest(
        client.post_json(HYPERLIQUID_INFO_URL, {"type": "metaAndAssetCtxs"}),
        selected_assets,
    )


def fetch_aster_open_interest(
    client: PublicHttpClient, selected_assets: set[str]
) -> list[ContractOpenInterest]:
    instruments = client.get_json(ASTER_EXCHANGE_INFO_URL)["symbols"]
    symbols = {
        canonical_symbol(item["baseAsset"]): item["symbol"]
        for item in instruments
        if item["quoteAsset"] == "USDT"
        and item["contractType"] == "PERPETUAL"
        and item["status"] == "TRADING"
        and canonical_symbol(item["baseAsset"]) in selected_assets
    }
    mark_prices = {
        item["symbol"]: item["markPrice"]
        for item in client.get_json(ASTER_MARK_PRICE_URL)
    }
    return _fetch_base_oi_for_universe(
        client,
        {
            canonical: BinanceInstrument(canonical, symbol)
            for canonical, symbol in symbols.items()
        },
        ASTER_OPEN_INTEREST_URL,
        mark_prices,
        parse_aster_open_interest,
    )


def _fetch_base_oi_for_universe(
    client: PublicHttpClient,
    universe: dict[str, BinanceInstrument],
    open_interest_url: str,
    mark_prices: dict[str, str],
    parser: Any,
) -> list[ContractOpenInterest]:
    def fetch_one(instrument: BinanceInstrument) -> ContractOpenInterest:
        return parser(
            instrument.canonical_symbol,
            instrument.symbol,
            client.get_json(open_interest_url, {"symbol": instrument.symbol}),
            mark_prices,
        )

    with ThreadPoolExecutor(max_workers=MAX_OI_WORKERS) as executor:
        return list(executor.map(fetch_one, universe.values()))
