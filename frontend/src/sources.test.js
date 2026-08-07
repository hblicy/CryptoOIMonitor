import { describe, expect, it } from "vitest";

import {
  SOURCE_GROUPS,
  TRADE_VENUES,
  groupTradeConditionScans,
  sourceHealthSummary,
  tradeConditionReasons,
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
    expect(tradeSignalReason("oi_to_market_cap_not_above_100")).toBe("OI / 市值未高于 100%");
    expect(tradeSignalReason("aggregate_oi_history_unavailable")).toBe("缺少15分钟前聚合 OI");
    expect(tradeSignalReason("aggregate_oi_not_increasing")).toBe("聚合 OI 未较15分钟前增加");
    expect(tradeSignalReason("ema200_not_crossed_up")).toBe("15m 收盘价未首次上穿 EMA200");
    expect(tradeSignalReason("quote_volume_not_increasing")).toBe("15m USDT 成交额未较上一根增加");
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

  it("shows current condition reasons in Chinese", () => {
    expect(
      tradeConditionReasons([
        "close_below_ema200_exit_buffer",
        "two_closes_below_ema200",
        "rsi_not_below_50",
        "rsi_not_rising",
      ]),
    ).toBe(
      "15m 收盘价低于 EMA200 − 0.5 × ATR；连续两根 15m 收盘价低于 EMA200；RSI(14) 未低于 50；RSI(14) 未回升",
    );
  });
});
