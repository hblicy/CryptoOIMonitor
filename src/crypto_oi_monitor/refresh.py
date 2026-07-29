from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
from typing import Callable, Protocol

from .domain import ContractOpenInterest
from .market_caps import MarketCapLookup
from .snapshot import SourceHealth, build_snapshot
from .sources import BinanceInstrument


LOGGER = logging.getLogger(__name__)


class SnapshotWriter(Protocol):
    def save_snapshot(self, snapshot: dict[str, object]) -> None: ...


class RefreshCoordinator:
    def __init__(
        self,
        universe_loader: Callable[[], dict[str, BinanceInstrument]],
        venue_loaders: dict[
            str, Callable[[dict[str, BinanceInstrument]], list[ContractOpenInterest]]
        ],
        market_cap_loader: Callable[[dict[str, BinanceInstrument]], MarketCapLookup],
        store: SnapshotWriter,
        now: Callable[[], str],
        persist: bool = True,
    ) -> None:
        self.universe_loader = universe_loader
        self.venue_loaders = venue_loaders
        self.market_cap_loader = market_cap_loader
        self.store = store
        self.now = now
        self.persist = persist

    def refresh(self) -> dict[str, object]:
        captured_at = self.now()
        try:
            universe = self.universe_loader()
        except Exception as error:
            snapshot = {
                "captured_at": captured_at,
                "complete": False,
                "selected_asset_count": 0,
                "comparisons": [],
                "unmapped_assets": [],
                "sources": {"Binance": SourceHealth.failed(_message(error)).as_dict()},
            }
            if self.persist:
                self.store.save_snapshot(snapshot)
            return snapshot

        selected_assets = set(universe)
        contracts_by_venue: dict[str, list[ContractOpenInterest]] = {}
        health: dict[str, SourceHealth] = {}
        with ThreadPoolExecutor(max_workers=len(self.venue_loaders) + 1) as executor:
            pending = {
                executor.submit(loader, universe): ("venue", name)
                for name, loader in self.venue_loaders.items()
            }
            pending[executor.submit(self.market_cap_loader, universe)] = (
                "market_cap",
                "CoinMarketCap",
            )
            market_cap_lookup: MarketCapLookup | None = None
            for future in as_completed(pending):
                kind, name = pending[future]
                try:
                    value = future.result()
                except Exception as error:
                    LOGGER.exception("%s refresh failed", name)
                    health[name] = SourceHealth.failed(_message(error))
                    continue
                if kind == "venue":
                    contracts_by_venue[name] = value
                    health[name] = SourceHealth.ok(len(value))
                else:
                    market_cap_lookup = value
                    if selected_assets and not value.market_caps:
                        health[name] = SourceHealth.failed(
                            "CoinMarketCap returned no usable market caps for "
                            f"{len(selected_assets)} selected assets"
                        )
                    else:
                        health[name] = SourceHealth.ok(
                            len(value.market_caps), value.refreshed_at
                        )

        if market_cap_lookup is None:
            market_cap_lookup = MarketCapLookup({}, tuple(sorted(selected_assets)))
        snapshot = build_snapshot(
            captured_at,
            selected_assets,
            market_cap_lookup,
            contracts_by_venue,
            health,
        )
        if self.persist:
            self.store.save_snapshot(snapshot)
        return snapshot


def _message(error: Exception) -> str:
    return f"{type(error).__name__}: {error}"
