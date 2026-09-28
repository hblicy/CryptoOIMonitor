import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { TradeConditionPanel } from "./App";

const missingDataScan = {
  status: "stop_long",
  canonical_symbol: "BANK",
  candle_close_time: null,
  rsi: 40,
  close: 1,
  ema200: 1.1,
  oi_to_market_cap: null,
  reasons: ["data_source_incomplete"],
};

describe("TradeConditionPanel", () => {
  it("summarizes outside-universe stops without misleading empty-state text", () => {
    const html = renderToStaticMarkup(
      <TradeConditionPanel complete={true} comparisons={[]} unmappedAssets={[]}
        scans={[missingDataScan, { ...missingDataScan, canonical_symbol: "BTR" }]} />,
    );

    expect(html).toContain("已隐藏 2 个已退出当前筛选范围的标的");
    expect(html).toContain("后台仍继续执行风控检查");
    expect(html).not.toContain("BANK");
    expect(html).not.toContain("BTR");
    expect(html).not.toContain("本轮没有 OI / 市值大于 90% 的标的");
    expect(html).not.toContain("trade-condition-groups");
  });

  it("keeps current, unmapped and mixed-reason stops and counts visible cards", () => {
    const html = renderToStaticMarkup(
      <TradeConditionPanel complete={true}
        comparisons={[{ canonical_symbol: "CURRENT" }]}
        unmappedAssets={["UNMAPPED"]}
        scans={[
          missingDataScan,
          { ...missingDataScan, canonical_symbol: "CURRENT" },
          { ...missingDataScan, canonical_symbol: "UNMAPPED" },
          { ...missingDataScan, canonical_symbol: "MIXED", reasons: ["data_source_incomplete", "close_not_above_ema200"] },
          { ...missingDataScan, canonical_symbol: "EMA", reasons: ["close_not_above_ema200"] },
        ]} />,
    );

    expect(html).toContain("已隐藏 1 个已退出当前筛选范围的标的");
    expect(html).not.toContain("BANK");
    for (const symbol of ["CURRENT", "UNMAPPED", "MIXED", "EMA"]) {
      expect(html).toContain(`<strong>${symbol}</strong>`);
    }
    expect(html).toContain("<strong>停止做多</strong><span>4 个</span>");
  });

  it("preserves forced exits and K-line errors outside the universe", () => {
    const html = renderToStaticMarkup(
      <TradeConditionPanel complete={true} comparisons={[]} unmappedAssets={[]}
        scans={[
          missingDataScan,
          { ...missingDataScan, canonical_symbol: "EXIT", status: "exit_long", reasons: ["atr_stop_loss"] },
          { ...missingDataScan, canonical_symbol: "ERROR", status: "kline_error", error: "K线请求失败" },
        ]} />,
    );

    expect(html).not.toContain("BANK");
    expect(html).toContain("<strong>EXIT</strong>");
    expect(html).toContain("<strong>ERROR</strong>");
    expect(html).toContain("K线请求失败");
    expect(html).toContain("<strong>停止做多</strong><span>0 个</span>");
  });

  it("does not infer universe membership from incomplete data", () => {
    const html = renderToStaticMarkup(
      <TradeConditionPanel complete={false} comparisons={[]} unmappedAssets={[]}
        scans={[missingDataScan]} />,
    );

    expect(html).toContain("<strong>BANK</strong>");
    expect(html).toContain("数据源不完整");
    expect(html).not.toContain("已隐藏");
  });

  it("shows a symbol again when it reenters the current universe", () => {
    const props = { complete: true, scans: [missingDataScan], unmappedAssets: [] };
    const outside = renderToStaticMarkup(<TradeConditionPanel {...props} comparisons={[]} />);
    const returned = renderToStaticMarkup(
      <TradeConditionPanel {...props} comparisons={[{ canonical_symbol: "BANK" }]} />,
    );

    expect(outside).not.toContain("<strong>BANK</strong>");
    expect(returned).toContain("<strong>BANK</strong>");
    expect(returned).not.toContain("已隐藏");
  });

  it("describes the three-candle breakout and 2x volume rules without an OI cap", () => {
    const html = renderToStaticMarkup(
      <TradeConditionPanel complete={true} scans={[]} />,
    );

    expect(html).toContain("扫描 OI / 市值 &gt; 90% 的标的");
    expect(html).not.toContain("开多仅限不超过 200%");
    expect(html).toContain("最近3根内上穿 EMA200");
    expect(html).toContain("前20根均量的2倍");
    expect(html).toContain("RSI 回升");
    expect(html).not.toContain("OI / 市值 &gt; 90% 且不超过 200%");
    expect(html).not.toContain("RSI &lt; 60");
    expect(html).not.toContain("当根上穿 EMA200");
    expect(html).not.toContain("前20根均量的1.1倍");
  });

  it("shows active risk results while new entries are paused", () => {
    const html = renderToStaticMarkup(
      <TradeConditionPanel
        complete={false}
        scans={[{
          status: "exit_long",
          canonical_symbol: "PEPE",
          candle_close_time: 1_722_269_700_000,
          rsi: 40,
          close: 97,
          ema200: 100,
          oi_to_market_cap: null,
          reasons: ["atr_stop_loss"],
        }]}
      />,
    );

    expect(html).toContain("\u5df2\u6682\u505c\u65b0\u5f00\u4ed3");
    expect(html).toContain("\u5df2\u6709\u4ea4\u6613\u72b6\u6001\u7684 15m \u98ce\u63a7\u4ecd\u5728\u6267\u884c");
    expect(html).toContain("PEPE");
    expect(html).toContain("trade-signal-exit_long");
  });

  it("shows unavailable legacy-scan indicators as dashes instead of zeroes", () => {
    const html = renderToStaticMarkup(
      <TradeConditionPanel
        complete={true}
        scans={[{
          status: "stop_long",
          canonical_symbol: "PEPE",
          candle_close_time: null,
          rsi: null,
          close: null,
          ema200: null,
          oi_to_market_cap: 2.01,
          reasons: ["oi_to_market_cap_in_ambush_zone"],
        }]}
      />,
    );

    expect(html).toContain("<dt>RSI(14)</dt><dd>—</dd>");
    expect(html).toContain("<dt>收盘价</dt><dd>—</dd>");
    expect(html).toContain("<dt>EMA200</dt><dd>—</dd>");
    expect(html).not.toContain("<dt>RSI(14)</dt><dd>0.00</dd>");
  });

  it("shows only one incomplete-source notice when no scans are available", () => {
    const html = renderToStaticMarkup(
      <TradeConditionPanel complete={false} scans={[]} />,
    );

    expect(html.match(/\u6570\u636e\u6e90\u4e0d\u5b8c\u6574/g)).toHaveLength(1);
  });
});
