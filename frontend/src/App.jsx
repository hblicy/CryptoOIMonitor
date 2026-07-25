import { useEffect, useMemo, useState } from "react";

import { formatRatio, formatShanghaiTime, formatUsd, riskLabel } from "./format";

const VENUES = ["Binance", "OKX", "Bybit", "Bitget", "Gate", "KuCoin", "MEXC", "Hyperliquid", "Aster"];

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
    setRefreshing(true);
    try {
      const response = await fetch("/api/refresh", { method: "POST" });
      const payload = await response.json();
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
  const ambushCandidateCount = comparisons.filter((item) => item.status === "high_risk").length;
  const healthySources = Object.values(summary?.sources ?? {}).filter(
    (source) => source.status === "ok",
  ).length;
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
          <span>数据最新时间：{formatShanghaiTime(summary?.captured_at)}（UTC+8）</span>
          <button className="refresh-button" type="button" onClick={refresh} disabled={refreshing}>
            <span aria-hidden="true">↻</span> {refreshing ? "刷新中" : "刷新数据"}
          </button>
        </div>
      </header>

      {requestError && <section className="request-error">请求失败：{requestError}</section>}

      <section className="summary-strip" aria-label="监控概要">
        <Metric label="跟踪资产数量" value={summary?.selected_asset_count ?? 0} />
        <Metric label="当前埋伏候选" value={ambushCandidateCount} tone={ambushCandidateCount ? "amber" : "default"} />
        <Metric label="数据源健康状态" value={`${healthySources} / ${Object.keys(summary?.sources ?? {}).length}`} tone={summary?.complete ? "green" : "red"} />
        <Metric label="上次全量更新时间" value={formatShanghaiTime(summary?.captured_at)} wide />
      </section>

      <section className="source-strip" aria-label="数据源健康状态">
        <strong>数据源健康状态</strong>
        <div className="source-list">
          {VENUES.map((venue) => <Source key={venue} name={venue} source={summary?.sources?.[venue]} />)}
        </div>
        <span className={`snapshot-state ${summary?.complete ? "snapshot-ok" : "snapshot-error"}`}>
          {summary?.complete ? "所有数据源完整" : "数据源不完整，已暂停关注提醒推送"}
        </span>
      </section>

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
                <option value="high_risk">埋伏候选（&gt;200%）</option>
                <option value="warning">重点关注（&gt;100%）</option>
                <option value="normal">常规</option>
              </select>
            </label>
            <label className="checkbox-control">
              <input type="checkbox" checked={alertsOnly} onChange={(event) => setAlertsOnly(event.target.checked)} />
              仅看关注项
            </label>
          </div>
          <p className="formula">指标说明：OI / 市值 = 聚合永续合约未平仓量（OI）÷ 代币市值（MC）。当比例 &gt; 100% 为重点关注；&gt; 200% 为埋伏候选区。</p>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>#</th><th>资产</th><th>市值（MC）</th><th>聚合 OI（USD）</th><th>OI / 市值</th><th>关注状态</th><th>9 交易所 OI 覆盖度</th><th>操作</th>
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
          <footer className="table-footer">共 {rows.length} 条 · Binance USDⓈ 永续 24 小时成交额不少于 1,000 万 USD</footer>
        </div>
        <DetailPanel selected={selected} />
      </section>

      <footer className="system-note">
        {summary?.notification?.message || (summary?.notification?.status === "ok" ? "企业微信关注提醒状态正常。" : "企业微信状态待刷新。")}
        {summary?.unmapped_assets?.length ? ` CoinGecko 未映射：${summary.unmapped_assets.join("、")}` : ""}
      </footer>
    </main>
  );
}

function Metric({ label, value, tone = "default", wide = false }) {
  return <div className={`metric metric-${tone} ${wide ? "metric-wide" : ""}`}><span>{label}</span><strong>{value}</strong></div>;
}

function Source({ name, source }) {
  const healthy = source?.status === "ok";
  return <span className={`source ${healthy ? "source-ok" : "source-error"}`}><i />{name} {sourceStatus(source)}</span>;
}

function Coverage({ venues }) {
  const coverage = (venues.length / VENUES.length) * 100;
  return <span className="coverage"><span>{coverage.toFixed(1)}%</span><i><b style={{ width: `${coverage}%` }} /></i></span>;
}

function DetailPanel({ selected }) {
  if (!selected) return <aside className="detail-panel empty-detail">暂无可展示的资产明细。</aside>;
  return (
    <aside className="detail-panel">
      <header className="detail-heading"><div><strong>{selected.canonical_symbol}</strong><span>{selected.coingecko_id}</span></div></header>
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
