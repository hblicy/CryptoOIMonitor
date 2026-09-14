import { useEffect, useMemo, useState } from "react";

import {
  formatChineseUsd,
  formatRatio,
  formatShanghaiTime,
  formatUsd,
  riskLabel,
} from "./format";

export const SOURCE_GROUPS = [
  {
    label: "CEX",
    sources: ["Binance", "OKX", "Bybit", "Bitget", "Gate", "KuCoin", "BingX", "MEXC"],
  },
  { label: "DEX", sources: ["Hyperliquid", "Aster", "Lighter"] },
  { label: "MC", sources: ["CoinMarketCap"] },
];

export const TRADE_VENUES = SOURCE_GROUPS
  .filter((group) => group.label !== "MC")
  .flatMap((group) => group.sources);

const MANUAL_REFRESH_TOKEN_KEY = "crypto-oi-monitor-manual-refresh-token";

export function manualRefreshRequestOptions(token) {
  return {
    method: "POST",
    headers: { "X-Manual-Refresh-Token": token },
  };
}

export function sourceHealthSummary(sources) {
  const sourceNames = SOURCE_GROUPS.flatMap((group) => group.sources);
  return {
    healthy: sourceNames.filter((name) => sources?.[name]?.status === "ok").length,
    total: sourceNames.length,
  };
}

export function tradeSignalLabel(eventType) {
  if (eventType === "exit_long") return "必须退出";
  if (eventType === "resume_long") return "恢复做多";
  if (eventType === "long") return "开多";
  if (eventType === "stop_long") return "停止开多";
  return eventType;
}

export function tradeSignalReason(reason) {
  if (reason === "atr_stop_loss") return "触及 2 × ATR 止损";
  if (reason === "close_below_ema200_exit_buffer") return "15m 收盘价低于 EMA200 − 0.5 × ATR";
  if (reason === "two_closes_below_ema200") return "连续两根 15m 收盘价低于 EMA200";
  if (reason === "trailing_take_profit") return "触及移动止盈保护价";
  if (reason === "ema200_not_crossed_up") return "最近3根已收盘15m K线内未上穿 EMA200，或当前已回到 EMA200 下方";
  if (reason === "ema200_breakout_before_cooldown_end") return "EMA200 突破发生在冷却结束前";
  if (reason === "ema200_not_rising") return "EMA200 未向上倾斜";
  if (reason === "quote_volume_not_increasing") return "15m USDT 成交额未较上一根增加";
  if (reason === "quote_volume_not_above_average") return "15m 成交额未突破前20根均量的2倍";
  if (reason === "aggregate_oi_history_unavailable") return "缺少15分钟前聚合 OI";
  if (reason === "aggregate_oi_not_increasing_after_price_adjustment") return "价格校正后的聚合 OI 未较15分钟前增加";
  if (reason === "close_not_above_ema200") return "15m 收盘价未高于 EMA200";
  if (reason === "rsi_not_below_60") return "RSI(14) 未低于 60";
  if (reason === "rsi_not_rising") return "RSI(14) 未回升";
  if (reason === "oi_to_market_cap_not_above_90") return "OI / 市值未高于 90%";
  if (reason === "oi_to_market_cap_in_ambush_zone") return "历史规则：OI / 市值高于 200%";
  if (reason === "close_below_ema200") return "15m 收盘价低于 EMA200";
  if (reason === "reentry_cooldown_active") return "仍处于必须退出后的动态冷却期";
  if (reason === "data_source_incomplete") return "数据源不完整，暂停新开仓";
  return reason;
}

export function tradeConditionReasons(reasons) {
  return reasons?.map(tradeSignalReason).join("；");
}

export function groupTradeConditionScans(scans) {
  return {
    canLong: scans.filter((scan) => scan.status === "can_long"),
    stopLong: scans.filter((scan) => scan.status === "stop_long"),
    exitLong: scans.filter((scan) => scan.status === "exit_long"),
    errors: scans.filter((scan) => scan.status === "kline_error"),
  };
}

function sourceStatus(source) {
  if (!source) return "未返回";
  return source.status === "ok" ? "正常" : "异常";
}

function riskClass(status) {
  return `risk risk-${status}`;
}

function App() {
  const [summary, setSummary] = useState(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [requestError, setRequestError] = useState("");
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [alertsOnly, setAlertsOnly] = useState(false);
  const [selectedSymbol, setSelectedSymbol] = useState("");

  async function loadSummary() {
    try {
      const response = await fetch("/api/summary");
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "读取监控数据失败");
      setSummary(payload);
      setRequestError("");
    } catch (error) {
      setRequestError(error.message);
    } finally {
      setLoading(false);
    }
  }

  async function refresh() {
    let token = window.sessionStorage.getItem(MANUAL_REFRESH_TOKEN_KEY);
    if (!token) {
      token = window.prompt("请输入 MANUAL_REFRESH_TOKEN 以执行手动刷新：")?.trim();
      if (!token) return;
      window.sessionStorage.setItem(MANUAL_REFRESH_TOKEN_KEY, token);
    }
    setRefreshing(true);
    try {
      const response = await fetch(
        "/api/refresh",
        manualRefreshRequestOptions(token),
      );
      const payload = await response.json();
      if (response.status === 403) {
        window.sessionStorage.removeItem(MANUAL_REFRESH_TOKEN_KEY);
      }
      if (!response.ok) throw new Error(payload.error || "刷新失败");
      setSummary(payload);
      setRequestError("");
    } catch (error) {
      setRequestError(error.message);
    } finally {
      setRefreshing(false);
      setLoading(false);
    }
  }

  useEffect(() => {
    loadSummary();
    const timer = window.setInterval(loadSummary, 30_000);
    return () => window.clearInterval(timer);
  }, []);

  const comparisons = summary?.comparisons ?? [];
  const tradeConditionScans = summary?.notification?.trade_condition_scans ?? [];
  const ambushCandidateCount = comparisons.filter((item) => item.status === "high_risk").length;
  const { healthy: healthySources, total: totalSources } = sourceHealthSummary(summary?.sources);
  const selected = comparisons.find((item) => item.canonical_symbol === selectedSymbol) ?? comparisons[0];
  const rows = useMemo(
    () =>
      comparisons.filter((item) => {
        const matchesQuery = item.canonical_symbol.toLowerCase().includes(query.trim().toLowerCase());
        const matchesStatus = statusFilter === "all" || item.status === statusFilter;
        const matchesAlerts = !alertsOnly || item.status !== "normal";
        return matchesQuery && matchesStatus && matchesAlerts;
      }),
    [alertsOnly, comparisons, query, statusFilter],
  );

  if (loading) {
    return <main className="loading-state">正在读取合约 OI / 市值数据…</main>;
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="product-name">合约 OI / 市值监控 <span aria-label="指标说明" className="info-mark">i</span></div>
        <div className="topbar-actions">
          <span>OI 最新时间：{formatShanghaiTime(summary?.captured_at)}（UTC+8）</span>
          <button className="refresh-button" type="button" onClick={refresh} disabled={refreshing}>
            <span aria-hidden="true">↻</span> {refreshing ? "刷新中" : "刷新数据"}
          </button>
        </div>
      </header>

      {requestError && <section className="request-error">请求失败：{requestError}</section>}

      <section className="summary-strip" aria-label="监控概要">
        <Metric label="跟踪资产数量" value={summary?.selected_asset_count ?? 0} />
        <Metric label="当前埋伏候选" value={ambushCandidateCount} tone={ambushCandidateCount ? "amber" : "default"} />
        <Metric label="数据源健康状态" value={`${healthySources} / ${totalSources}`} tone={summary?.complete ? "green" : "red"} />
        <Metric label="上次 OI 更新时间" value={formatShanghaiTime(summary?.captured_at)} wide />
      </section>

      <section className="source-strip" aria-label="数据源健康状态">
        <strong>数据源健康状态</strong>
        <div className="source-list">
          {SOURCE_GROUPS.map((group) => (
            <div className="source-group" key={group.label}>
              <span className="source-category">{group.label}</span>
              {group.sources.map((source) => (
                <Source key={source} name={source} source={summary?.sources?.[source]} />
              ))}
            </div>
          ))}
        </div>
        <span className={`snapshot-state ${summary?.complete ? "snapshot-ok" : "snapshot-error"}`}>
          {summary?.complete ? "所有数据源完整" : "数据源不完整，已暂停关注提醒推送"}
        </span>
      </section>

      <TradeConditionPanel scans={tradeConditionScans} complete={summary?.complete} />

      <section className="workspace">
        <div className="table-region">
          <div className="toolbar">
            <label className="search-control">
              <span className="sr-only">搜索币种</span>
              <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索币种（如：BTC）" />
              <span aria-hidden="true">⌕</span>
            </label>
            <label className="select-control">
              <span className="sr-only">关注状态</span>
              <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
                <option value="all">全部关注状态</option>
                <option value="high_risk">埋伏候选区（&gt;200%）</option>
                <option value="warning">重点关注（&gt;110%）</option>
                <option value="normal">常规</option>
              </select>
            </label>
            <label className="checkbox-control">
              <input type="checkbox" checked={alertsOnly} onChange={(event) => setAlertsOnly(event.target.checked)} />
              仅看关注项
            </label>
          </div>
          <p className="formula">指标说明：OI / 市值 = 聚合永续合约未平仓量（OI）÷ 代币市值（MC）。当比例 &gt; 110% 为重点关注；&gt; 200% 为埋伏候选区。</p>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>#</th><th>资产</th><th>市值（MC）</th><th>聚合 OI（USD）</th><th>OI / 市值</th><th>关注状态</th><th>{TRADE_VENUES.length} 交易所 OI 覆盖度</th><th>操作</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((item, index) => (
                  <tr key={item.canonical_symbol} className={selected?.canonical_symbol === item.canonical_symbol ? "selected-row" : ""}>
                    <td>{index + 1}</td>
                    <td className="asset-cell"><span className="asset-dot">{item.canonical_symbol.slice(0, 1)}</span>{item.canonical_symbol}</td>
                    <td>{formatUsd(item.market_cap_usd)}</td>
                    <td>{formatUsd(item.total_oi_usd)}</td>
                    <td className={riskClass(item.status)}>{formatRatio(item.oi_to_market_cap)}</td>
                    <td><span className={riskClass(item.status)}>{riskLabel(item.status)}</span></td>
                    <td><Coverage venues={item.covered_venues} /></td>
                    <td><button className="detail-button" type="button" onClick={() => setSelectedSymbol(item.canonical_symbol)} aria-label={`查看 ${item.canonical_symbol} 明细`}>›</button></td>
                  </tr>
                ))}
                {!rows.length && <tr><td colSpan="8" className="empty-cell">当前筛选条件没有可展示的数据。</td></tr>}
              </tbody>
            </table>
          </div>
          <footer className="table-footer">
            共 {rows.length} 条 · Binance USDⓈ 永续 24 小时成交额不少于{" "}
            {formatChineseUsd(summary?.settings?.binance_min_turnover_usd)}
          </footer>
        </div>
        <DetailPanel selected={selected} />
      </section>

      <footer className="system-note">
        {summary?.notification?.message || (summary?.notification?.status === "ok" ? "企业微信关注提醒状态正常。" : "企业微信状态待刷新。")}
        {summary?.unmapped_assets?.length ? ` CoinMarketCap 未映射：${summary.unmapped_assets.join("、")}` : ""}
      </footer>
      <CmcCandidatePanel candidates={summary?.unmapped_candidates ?? []} />
    </main>
  );
}

function CmcCandidatePanel({ candidates }) {
  if (!candidates.length) return null;
  return (
    <section className="cmc-candidate-panel">
      <header>
        <strong>CoinMarketCap 待确认映射</strong>
        <span>按与 Binance 价格的价差排序；确认后填入 .env 的 CMC_ID_OVERRIDES</span>
      </header>
      <div className="cmc-candidate-scroll">
        <table>
          <thead>
            <tr>
              <th>资产</th><th>Binance 价格</th><th>CMC 候选</th><th>CMC ID</th><th>CMC 价格</th><th>价差</th><th>市值</th><th>配置值</th>
            </tr>
          </thead>
          <tbody>
            {candidates.map((candidate) => (
              <tr key={`${candidate.asset}-${candidate.market_cap_id}`}>
                <td>{candidate.asset}</td>
                <td>{formatTradePrice(candidate.binance_price_usd)}</td>
                <td>{candidate.name}{candidate.slug ? ` (${candidate.slug})` : ""}</td>
                <td>{candidate.market_cap_id}</td>
                <td>{formatTradePrice(candidate.price_usd)}</td>
                <td>{candidate.price_difference_percent == null ? "—" : `${candidate.price_difference_percent.toFixed(3)}%`}</td>
                <td>{candidate.market_cap_usd == null ? "—" : formatUsd(candidate.market_cap_usd)}</td>
                <td><code>{candidate.asset}:{candidate.market_cap_id}</code></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Metric({ label, value, tone = "default", wide = false }) {
  return <div className={`metric metric-${tone} ${wide ? "metric-wide" : ""}`}><span>{label}</span><strong>{value}</strong></div>;
}

function Source({ name, source }) {
  const healthy = source?.status === "ok";
  const updatedAt = name === "CoinMarketCap" ? source?.updated_at : null;
  return <span className={`source ${healthy ? "source-ok" : "source-error"}`}><i />{name} {sourceStatus(source)}{updatedAt ? `（市值更新：${formatShanghaiTime(updatedAt)}）` : ""}</span>;
}

function Coverage({ venues }) {
  const coverage = (venues.length / TRADE_VENUES.length) * 100;
  return <span className="coverage"><span>{coverage.toFixed(1)}%</span><i><b style={{ width: `${coverage}%` }} /></i></span>;
}

export function TradeConditionPanel({ scans, complete }) {
  const { canLong, stopLong, exitLong, errors } = groupTradeConditionScans(scans);
  return (
    <section className="trade-signal-panel" aria-label="交易条件扫描">
      <header className="trade-signal-heading">
        <strong>交易条件扫描</strong>
        <span>扫描 OI / 市值 &gt; 90% 的标的；最近3根内上穿 EMA200、当前仍在其上方且 EMA200 向上；价格校正 OI 增长；成交额突破前20根均量的2倍；RSI 回升</span>
      </header>
      {!complete && <p className="trade-signal-empty">数据源不完整：已暂停新开仓，已有交易状态的 15m 风控仍在执行。</p>}
      {!scans.length
        ? complete
          ? <p className="trade-signal-empty">本轮没有 OI / 市值大于 90% 的标的。</p>
          : null
        : <div className="trade-condition-groups">
            <TradeConditionGroup title="可以做多" status="can_long" scans={canLong} />
            <TradeConditionGroup title="停止做多" status="stop_long" scans={stopLong} />
            <TradeConditionGroup title="必须退出" status="exit_long" scans={exitLong} />
            <TradeConditionGroup title="K 线异常" status="kline_error" scans={errors} />
          </div>}
    </section>
  );
}

function TradeConditionGroup({ title, status, scans }) {
  return (
    <section className={`trade-condition-group trade-condition-${status}`}>
      <header><strong>{title}</strong><span>{scans.length} 个</span></header>
      {scans.length
        ? <div className="trade-condition-list">
          {scans.map((scan) => <TradeConditionCard key={`${scan.status}-${scan.canonical_symbol}-${scan.candle_close_time ?? "error"}`} scan={scan} />)}
        </div>
        : <p className="trade-condition-empty">暂无标的</p>}
    </section>
  );
}

function TradeConditionCard({ scan }) {
  const reasons = tradeConditionReasons(scan.reasons);
  return (
    <article className={`trade-signal-card trade-signal-${scan.status}`}>
      <header>
        <strong>{scan.canonical_symbol}</strong>
        <time>{scan.candle_close_time ? formatShanghaiTime(new Date(scan.candle_close_time).toISOString()) : "—"}</time>
      </header>
      {scan.status === "kline_error"
        ? <>
          <p className="trade-condition-error">{scan.error}</p>
          <p className="trade-signal-note">OI / 市值：{formatRatio(scan.oi_to_market_cap)}</p>
        </>
        : <>
          <dl>
            <div><dt>RSI(14)</dt><dd>{scan.rsi == null ? "—" : Number(scan.rsi).toFixed(2)}</dd></div>
            <div><dt>收盘价</dt><dd>{formatTradePrice(scan.close)}</dd></div>
            <div><dt>EMA200</dt><dd>{formatTradePrice(scan.ema200)}</dd></div>
            <div><dt>OI / 市值</dt><dd>{formatRatio(scan.oi_to_market_cap)}</dd></div>
          </dl>
          {scan.status === "can_long"
            ? <p className="trade-signal-note">
              满足最近3根内上穿 EMA200、当前仍在其上方、EMA200 向上、RSI 回升、价格校正 OI 增长及成交额突破均量条件；
              聚合 OI {formatUsd(scan.previous_aggregate_oi_usd)} → {formatUsd(scan.aggregate_oi_usd)}；
              价格校正 OI 指数 {formatTradePrice(scan.previous_adjusted_aggregate_oi)} → {formatTradePrice(scan.adjusted_aggregate_oi)}；
              15m成交额 {formatUsd(scan.quote_volume)}，前20根均量 {formatUsd(scan.average_quote_volume)}；
              5根前 EMA200 {formatTradePrice(scan.ema200_slope_reference)}。
            </p>
            : <p className="trade-signal-note">{scan.status === "exit_long" ? "必须退出原因" : "停止原因"}：{reasons}。</p>}
        </>}
    </article>
  );
}

function formatTradePrice(value) {
  if (value == null) return "—";
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "—";
  return numeric.toLocaleString("en-US", { maximumFractionDigits: 8 });
}

function DetailPanel({ selected }) {
  if (!selected) return <aside className="detail-panel empty-detail">暂无可展示的资产明细。</aside>;
  return (
    <aside className="detail-panel">
      <header className="detail-heading"><div><strong>{selected.canonical_symbol}</strong><span>{selected.market_cap_id}</span></div></header>
      <dl className="detail-metrics">
        <div><dt>市值（MC）</dt><dd>{formatUsd(selected.market_cap_usd)}</dd></div>
        <div><dt>聚合 OI（USD）</dt><dd>{formatUsd(selected.total_oi_usd)}</dd></div>
        <div><dt>OI / 市值</dt><dd className={riskClass(selected.status)}>{formatRatio(selected.oi_to_market_cap)}</dd></div>
      </dl>
      <div className="detail-tabs"><strong>交易所明细</strong><span>数据按本轮快照聚合</span></div>
      <div className="venue-breakdown">
        <div className="venue-head"><span>交易所</span><span>OI（USD）</span><span>占比</span></div>
        {[...selected.contracts].sort((left, right) => right.oi_usd - left.oi_usd).map((contract) => (
          <div className="venue-row" key={`${contract.venue}-${contract.symbol}`}><span><i className="venue-dot" />{contract.venue}</span><span>{formatUsd(contract.oi_usd)}</span><span>{formatRatio(contract.oi_usd / selected.total_oi_usd)}</span></div>
        ))}
      </div>
      <div className="detail-total"><span>聚合 OI</span><strong>{formatUsd(selected.total_oi_usd)}</strong></div>
      <p className="detail-note">同一交易所的所有相关永续合约按 USD OI 口径汇总；缺少交易对不会按零伪造数据。</p>
    </aside>
  );
}

export default App;
