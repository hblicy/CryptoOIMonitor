import { describe, expect, it } from "vitest";

import {
  SOURCE_GROUPS,
  TRADE_VENUES,
  groupTradeConditionScans,
  sourceHealthSummary,
  tradeSignalLabel,
  tradeSignalReason,
} from "./App";

describe("data source groups", () => {
  it("separates CEX, DEX, and market-cap sources", () => {
    expect(SOURCE_GROUPS).toEqual([
      {
        label: "CEX",
        sources: ["Binance", "OKX", "Bybit", "Bitget", "Gate", "KuCoin", "BingX", "MEXC"],
      },
      { label: "DEX", sources: ["Hyperliquid", "Aster", "Lighter"] },
      { label: "MC", sources: ["CoinMarketCap"] },
    ]);
  });

  it("uses only OI venues to calculate coverage", () => {
    expect(TRADE_VENUES).toHaveLength(11);
    expect(TRADE_VENUES).not.toContain("CoinMarketCap");
  });

  it("keeps the health denominator at all configured sources", () => {
    expect(sourceHealthSummary({ Binance: { status: "error" } })).toEqual({
      healthy: 0,
      total: 12,
    });
  });

  it("labels long and stop-long signal details for the dashboard", () => {
    expect(tradeSignalLabel("long")).toBe("开多");
    expect(tradeSignalLabel("stop_long")).toBe("停止开多");
    expect(tradeSignalReason("oi_to_market_cap_below_110")).toBe("OI / 市值低于 110%");
    expect(tradeSignalReason("rsi_above_50")).toBe("RSI(14) 超过 50");
    expect(tradeSignalReason("close_below_ema200")).toBe("15m 收盘价低于 EMA200");
  });

  it("groups current trade conditions by their actionable state", () => {
    const scans = [
      { canonical_symbol: "PEPE", status: "can_long" },
      { canonical_symbol: "DOGE", status: "stop_long" },
      { canonical_symbol: "NEW", status: "kline_error" },
    ];

    expect(groupTradeConditionScans(scans)).toEqual({
      canLong: [scans[0]],
      stopLong: [scans[1]],
      exitLong: [],
      errors: [scans[2]],
    });
  });

  it("labels and groups forced exits separately", () => {
    const scan = { canonical_symbol: "KOMA", status: "exit_long" };

    expect(tradeSignalLabel("exit_long")).toBe("必须退出");
    expect(tradeSignalReason("atr_stop_loss")).toBe("触及 2 × ATR 止损");
    expect(groupTradeConditionScans([scan])).toEqual({
      canLong: [],
      stopLong: [],
      exitLong: [scan],
      errors: [],
    });
  });
});
