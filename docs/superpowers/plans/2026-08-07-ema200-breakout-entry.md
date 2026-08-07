# EMA200 突破开多信号 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 只在 Binance 已收盘 15m K 线首次上穿 EMA200，并同时满足聚合 OI、成交额和 RSI 确认时推送开多。

**Architecture:** SQLite 提供目标时间附近的完整历史快照，交易调度使用当前与历史全网聚合 OI；K 线模型增加 USDT 成交额和前一根价格信息。入场判断与持仓后的停止判断分离，前端和企业微信共用明确的原因代码与指标。

**Tech Stack:** Python 3、SQLite、`unittest`、React/Vite、Vitest。

---

### Task 1: 查询 15 分钟前完整快照

**Files:**
- Modify: `src/crypto_oi_monitor/storage.py`
- Test: `tests/test_storage.py`

- [ ] **Step 1: 编写失败测试**

增加测试，保存目标时间前后多个快照，包含不完整快照，并断言选择绝对时间差最小的完整快照；差值相同时选择较早快照。

```python
def test_loads_nearest_complete_snapshot_within_tolerance(self) -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        store = SnapshotStore(Path(temp_dir) / "monitor.db")
        for captured_at, complete, oi in (
            ("2026-08-07T00:13:00+00:00", True, 90),
            ("2026-08-07T00:14:00+00:00", False, 95),
            ("2026-08-07T00:17:00+00:00", True, 100),
        ):
            store.save_snapshot(
                {
                    "captured_at": captured_at,
                    "complete": complete,
                    "comparisons": [
                        {"canonical_symbol": "PEPE", "total_oi_usd": oi}
                    ],
                }
            )

        result = store.load_complete_snapshot_near(
            datetime.fromisoformat("2026-08-07T00:15:00+00:00"),
            timedelta(minutes=5),
        )

        self.assertEqual(result["captured_at"], "2026-08-07T00:13:00+00:00")
```

另加测试，范围内只有不完整快照或范围外快照时返回 `None`。

- [ ] **Step 2: 运行测试并确认失败**

Run:

```powershell
$env:PYTHONPATH = 'src'
python -m unittest discover -s tests -p test_storage.py -k load_complete_snapshot_near -v
```

Expected: FAIL，`SnapshotStore` 尚无该方法。

- [ ] **Step 3: 实现精确查询**

在 `SnapshotStore` 增加：

```python
def load_complete_snapshot_near(
    self, target: datetime, tolerance: timedelta
) -> dict[str, Any] | None:
    lower = (target - tolerance).isoformat()
    upper = (target + tolerance).isoformat()
    with self._connect() as connection:
        rows = connection.execute(
            "SELECT payload FROM snapshots "
            "WHERE captured_at >= ? AND captured_at <= ? ORDER BY captured_at",
            (lower, upper),
        ).fetchall()
    candidates = [json.loads(row[0]) for row in rows]
    complete = [snapshot for snapshot in candidates if snapshot.get("complete") is True]
    if not complete:
        return None
    return min(
        complete,
        key=lambda snapshot: (
            abs(datetime.fromisoformat(snapshot["captured_at"]) - target),
            datetime.fromisoformat(snapshot["captured_at"]) > target,
        ),
    )
```

- [ ] **Step 4: 运行存储测试**

Run:

```powershell
$env:PYTHONPATH = 'src'
python -m unittest discover -s tests -p test_storage.py -v
```

Expected: 全部 PASS。

### Task 2: 解析 15m 成交额并判断首次突破

**Files:**
- Modify: `src/crypto_oi_monitor/trading.py`
- Test: `tests/test_trading.py`
- Test: `tests/test_notifier.py`
- Test: `tests/test_trade_dispatch.py`

- [ ] **Step 1: 编写 K 线成交额解析失败测试**

把 Binance K 线测试数据补齐索引 7，并断言 `quote_volume`：

```python
self.assertEqual(candles[-1].quote_volume, 1001)
self.assertEqual(
    client.calls[0][1],
    {"symbol": "PEPEUSDT", "interval": "15m", "limit": "1002"},
)
```

- [ ] **Step 2: 编写首次突破和成交额增长失败测试**

使用完整 `TradeIndicators` 分别覆盖成功、持续站上、未站上和成交额未增长：

```python
from dataclasses import replace


def test_requires_fresh_ema200_breakout_and_rising_quote_volume(self) -> None:
    indicators = TradeIndicators(
        candle_close_time=1,
        close=101,
        rsi=40,
        previous_rsi=39,
        ema200=100,
        previous_ema200=100,
        atr=2,
        hourly_close=90,
        hourly_ema200=100,
        previous_hourly_ema200=101,
        previous_close=100,
        quote_volume=200,
        previous_quote_volume=100,
    )

    self.assertEqual(entry_reasons(indicators), ())
    self.assertEqual(
        entry_reasons(replace(indicators, previous_close=101)),
        ("ema200_not_crossed_up",),
    )
    self.assertEqual(
        entry_reasons(replace(indicators, quote_volume=100)),
        ("quote_volume_not_increasing",),
    )
```

断言 1h 数据向下不会再产生入场原因，且删除旧的 0.25 ATR 缓冲测试。

- [ ] **Step 3: 编写停止条件分离测试**

```python
def valid_breakout_indicators() -> TradeIndicators:
    return TradeIndicators(
        candle_close_time=1,
        close=101,
        rsi=40,
        previous_rsi=39,
        ema200=100,
        previous_ema200=100,
        atr=2,
        hourly_close=90,
        hourly_ema200=100,
        previous_hourly_ema200=101,
        previous_close=100,
        quote_volume=200,
        previous_quote_volume=100,
    )


def test_stop_long_uses_only_rsi_50_and_ema200(self) -> None:
    indicators = valid_breakout_indicators()

    self.assertEqual(stop_long_reasons(indicators), ())
    self.assertEqual(
        stop_long_reasons(replace(indicators, rsi=50)),
        ("rsi_not_below_50",),
    )
    self.assertEqual(
        stop_long_reasons(replace(indicators, close=100)),
        ("close_not_above_ema200",),
    )
    self.assertEqual(
        stop_long_reasons(replace(indicators, rsi=30, previous_rsi=40)),
        (),
    )
```

- [ ] **Step 4: 运行目标测试并确认失败**

Run:

```powershell
$env:PYTHONPATH = 'src'
python -m unittest discover -s tests -p test_trading.py -v
```

Expected: FAIL，模型尚无成交额和首次突破判断。

- [ ] **Step 5: 实现 K 线与指标字段**

给 `Candle` 增加 `quote_volume`，从 Binance 数组索引 7 读取。给 `TradeIndicators` 增加 `previous_close`、`quote_volume`、`previous_quote_volume`；给 `TradeSetup` 增加相同的入场审计字段和 `previous_ema200`。

```python
@dataclass(frozen=True)
class Candle:
    close_time: int
    high: float
    low: float
    close: float
    quote_volume: float = 0


def entry_reasons(indicators: TradeIndicators) -> tuple[str, ...]:
    reasons = []
    if not (
        indicators.previous_close <= indicators.previous_ema200
        and indicators.close > indicators.ema200
    ):
        reasons.append("ema200_not_crossed_up")
    if indicators.quote_volume <= indicators.previous_quote_volume:
        reasons.append("quote_volume_not_increasing")
    if indicators.rsi < ENTRY_RSI_MIN:
        reasons.append("rsi_below_35")
    if indicators.rsi >= ENTRY_RSI_MAX:
        reasons.append("rsi_not_below_50")
    if indicators.rsi <= indicators.previous_rsi:
        reasons.append("rsi_not_rising")
    return tuple(reasons)


def stop_long_reasons(indicators: TradeIndicators) -> tuple[str, ...]:
    reasons = []
    if indicators.close <= indicators.ema200:
        reasons.append("close_not_above_ema200")
    if indicators.rsi >= ENTRY_RSI_MAX:
        reasons.append("rsi_not_below_50")
    return tuple(reasons)
```

删除 `ENTRY_EMA_ATR_BUFFER`，但保留 ATR 止损和必须退出计算。

- [ ] **Step 6: 运行交易指标测试**

Run:

```powershell
$env:PYTHONPATH = 'src'
python -m unittest discover -s tests -p test_trading.py -v
```

Expected: 全部 PASS。

### Task 3: 聚合 OI 入场确认与状态机

**Files:**
- Modify: `src/crypto_oi_monitor/domain.py`
- Modify: `src/crypto_oi_monitor/trade_dispatch.py`
- Test: `tests/test_domain.py`
- Test: `tests/test_trade_dispatch.py`

- [ ] **Step 1: 将交易入场阈值测试改为 100%**

```python
self.assertEqual(TRADE_ENTRY_OI_TO_MARKET_CAP_RATIO, 1.0)
```

主表 `FOCUS_OI_TO_MARKET_CAP_RATIO == 1.1` 和埋伏阈值 `2.0` 的断言保持不变。

- [ ] **Step 2: 编写聚合 OI 入场测试**

为调度测试提供 15 分钟前快照，并覆盖严格增长、相等、下降和缺失：

```python
reference_snapshot = {
    "captured_at": "2026-08-07T00:00:00+00:00",
    "complete": True,
    "comparisons": [
        {"canonical_symbol": "PEPE", "total_oi_usd": 100}
    ],
}
current_comparison["total_oi_usd"] = 101
current_comparison["market_cap_usd"] = 100
current_comparison["oi_to_market_cap"] = 1.01

result = dispatch_trade_signals(
    snapshot,
    reference_snapshot,
    kline_loader,
    store,
    notifier,
)

self.assertEqual(result.events, ("long",))
```

相等时断言原因包含 `aggregate_oi_not_increasing`；缺失时包含 `aggregate_oi_history_unavailable`。

- [ ] **Step 3: 编写已有开多状态的停止测试**

覆盖以下行为：

```python
# OI/市值等于 100% 立即停止，不加载 K 线。
self.assertEqual(result.events, ("stop_long",))
self.assertEqual(result.details[0].reasons, ("oi_to_market_cap_not_above_100",))

# 已有 long 状态下，OI 和成交额下降但 RSI<50、收盘价>EMA200时不停止。
self.assertEqual(result.events, ())
self.assertEqual(store.state.status, "long")
```

另断言 RSI≥50 或收盘价≤EMA200 仍触发停止，首次突破条件不参与已有 `long` 的停止判断。

- [ ] **Step 4: 运行调度测试并确认失败**

Run:

```powershell
$env:PYTHONPATH = 'src'
python -m unittest discover -s tests -p test_trade_dispatch.py -v
```

Expected: FAIL，调度尚未接收历史快照。

- [ ] **Step 5: 实现历史 OI 原因和分离状态机**

将阈值改为：

```python
TRADE_ENTRY_OI_TO_MARKET_CAP_RATIO = 1.0
```

在调度模块增加：

```python
def _historical_oi_by_asset(
    snapshot: dict[str, Any] | None,
) -> dict[str, float]:
    if snapshot is None:
        return {}
    return {
        comparison["canonical_symbol"]: float(comparison["total_oi_usd"])
        for comparison in snapshot["comparisons"]
    }


def _oi_entry_reasons(
    comparison: dict[str, Any], previous_total_oi_usd: float | None
) -> tuple[str, ...]:
    reasons = []
    if comparison["oi_to_market_cap"] <= TRADE_ENTRY_OI_TO_MARKET_CAP_RATIO:
        reasons.append("oi_to_market_cap_not_above_100")
    if previous_total_oi_usd is None:
        reasons.append("aggregate_oi_history_unavailable")
    elif comparison["total_oi_usd"] <= previous_total_oi_usd:
        reasons.append("aggregate_oi_not_increasing")
    return tuple(reasons)
```

修改 `dispatch_trade_signals` 和 `scan_trade_conditions` 接收 `reference_snapshot`。新入场使用 `_oi_entry_reasons + entry_reasons`；已有 `long` 仅使用 OI/市值停止、`stop_long_reasons` 和原有 `exit_reasons`。

`TradeConditionScan` 与 `TradeSignalEvent` 增加当前/历史 OI、当前/上一根成交额和上一根价格/EMA 字段，并由 `as_dict` 输出。

- [ ] **Step 6: 运行领域与调度测试**

Run:

```powershell
$env:PYTHONPATH = 'src'
python -m unittest discover -s tests -p test_domain.py -v
python -m unittest discover -s tests -p test_trade_dispatch.py -v
```

Expected: 全部 PASS。

### Task 4: 应用接入历史快照并更新企业微信

**Files:**
- Modify: `app.py`
- Modify: `src/crypto_oi_monitor/notifier.py`
- Test: `tests/test_app.py`
- Test: `tests/test_notifier.py`

- [ ] **Step 1: 编写应用历史快照接入测试**

模拟 `SnapshotStore.load_complete_snapshot_near` 返回历史快照，断言完整快照扫描和推送收到同一个历史快照；不完整当前快照不查询历史 OI。

- [ ] **Step 2: 编写企业微信文本测试**

更新开多消息断言：

```python
self.assertIn("OI / 市值 > 100%", content)
self.assertIn("15m 首次上穿 EMA200", content)
self.assertIn("聚合 OI（当前）", content)
self.assertIn("聚合 OI（15分钟前）", content)
self.assertIn("15m 成交额（当前）", content)
self.assertIn("15m 成交额（上一根）", content)
self.assertNotIn("1h 趋势", content)
self.assertNotIn("0.25 × ATR", content)
```

停止原因更新为 `OI / 市值未高于 100%` 和 `15m 收盘价未高于 EMA200`。新增历史 OI 缺失、聚合 OI 未增长、未首次突破和成交额未增长的中文标签测试。

- [ ] **Step 3: 运行目标测试并确认失败**

Run:

```powershell
$env:PYTHONPATH = 'src'
python -m unittest discover -s tests -p test_app.py -v
python -m unittest discover -s tests -p test_notifier.py -v
```

Expected: FAIL，应用和通知仍使用旧条件。

- [ ] **Step 4: 实现应用接入**

完整快照产生后计算并只查询一次历史快照：

```python
captured_at = datetime.fromisoformat(snapshot["captured_at"])
reference_snapshot = self.store.load_complete_snapshot_near(
    captured_at - timedelta(minutes=15),
    timedelta(minutes=5),
)
```

将 `reference_snapshot` 传给 `dispatch_trade_signals` 或 `scan_trade_conditions`。当前快照不完整时保持原有抑制逻辑。

- [ ] **Step 5: 更新企业微信消息**

`send_trade_signal` 增加显式 `previous_total_oi_usd` 参数，消息使用 `TradeSetup` 的前一根价格、EMA 和成交额字段，并显示当前/历史聚合 OI。删除 1h 趋势和 0.25 ATR 入场说明。

原因映射改为：

```python
"oi_to_market_cap_not_above_100": "OI / 市值未高于 100%",
"aggregate_oi_history_unavailable": "缺少15分钟前聚合 OI",
"aggregate_oi_not_increasing": "聚合 OI 未较15分钟前增加",
"ema200_not_crossed_up": "15m 收盘价未首次上穿 EMA200",
"quote_volume_not_increasing": "15m USDT 成交额未较上一根增加",
"close_not_above_ema200": "15m 收盘价未高于 EMA200",
```

- [ ] **Step 6: 运行应用与通知测试**

Run:

```powershell
$env:PYTHONPATH = 'src'
python -m unittest discover -s tests -p test_app.py -v
python -m unittest discover -s tests -p test_notifier.py -v
```

Expected: 全部 PASS。

### Task 5: 更新 Web 交易条件说明

**Files:**
- Modify: `frontend/src/App.jsx`
- Test: `frontend/src/sources.test.js`

- [ ] **Step 1: 编写前端失败测试**

更新中文原因断言并增加：

```javascript
expect(tradeSignalReason("oi_to_market_cap_not_above_100")).toBe("OI / 市值未高于 100%");
expect(tradeSignalReason("aggregate_oi_history_unavailable")).toBe("缺少15分钟前聚合 OI");
expect(tradeSignalReason("aggregate_oi_not_increasing")).toBe("聚合 OI 未较15分钟前增加");
expect(tradeSignalReason("ema200_not_crossed_up")).toBe("15m 收盘价未首次上穿 EMA200");
expect(tradeSignalReason("quote_volume_not_increasing")).toBe("15m USDT 成交额未较上一根增加");
```

- [ ] **Step 2: 运行测试并确认失败**

Run:

```powershell
cd frontend
npm test -- --run
```

Expected: FAIL，页面尚未识别新原因。

- [ ] **Step 3: 更新页面文字和原因**

交易条件标题改为：

```jsx
<span>OI / 市值 &gt; 100%；聚合 OI 较15分钟前增加；15m 首次上穿 EMA200；成交额增加；RSI 在 35-50 且回升</span>
```

空状态改为“本轮没有 OI / 市值大于 100% 的标的”，可以做多说明改为“满足聚合 OI、EMA200 首次突破、成交额与 RSI 回升条件”。删除旧 1h 与 0.25 ATR 原因映射，加入新中文原因映射。

- [ ] **Step 4: 运行前端测试和构建**

Run:

```powershell
cd frontend
npm test -- --run
npm run build
```

Expected: 测试全部 PASS，Vite build exit code 0。

### Task 6: 全量验证与范围审计

**Files:**
- Verify: `app.py`
- Verify: `src/crypto_oi_monitor/domain.py`
- Verify: `src/crypto_oi_monitor/storage.py`
- Verify: `src/crypto_oi_monitor/trading.py`
- Verify: `src/crypto_oi_monitor/trade_dispatch.py`
- Verify: `src/crypto_oi_monitor/notifier.py`
- Verify: `frontend/src/App.jsx`
- Verify: related tests

- [ ] **Step 1: 运行 Python 全量测试**

```powershell
$env:PYTHONPATH = 'src'
python -m unittest discover -s tests -v
```

Expected: 全部 PASS，无 traceback。

- [ ] **Step 2: 运行编译检查**

```powershell
python -m compileall -q app.py src\crypto_oi_monitor
```

Expected: exit code 0，无输出。

- [ ] **Step 3: 运行前端测试与构建**

```powershell
cd frontend
npm test -- --run
npm run build
```

Expected: 全部 PASS，构建成功。

- [ ] **Step 4: 检查改动范围**

```powershell
git diff --check
git status --short
```

Expected: 仅包含本功能代码、测试和计划文件；不暂存或修改原有未跟踪文件。
