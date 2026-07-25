const usdFormatter = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 2,
});

export function formatUsd(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return "—";
  }
  const numeric = Number(value);
  if (Math.abs(numeric) >= 1_000_000_000_000) return `$${(numeric / 1_000_000_000_000).toFixed(2)}T`;
  if (Math.abs(numeric) >= 1_000_000_000) return `$${(numeric / 1_000_000_000).toFixed(2)}B`;
  if (Math.abs(numeric) >= 1_000_000) return `$${(numeric / 1_000_000).toFixed(2)}M`;
  return usdFormatter.format(numeric);
}

export function formatRatio(value) {
  return `${(Number(value) * 100).toFixed(2)}%`;
}

export function riskLabel(status) {
  if (status === "high_risk") return "埋伏候选（>200%）";
  if (status === "warning") return "重点关注（>100%）";
  return "常规";
}

export function formatShanghaiTime(value) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date(value));
}
