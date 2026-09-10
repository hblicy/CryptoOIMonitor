# 高 OI/MC 开多规则调整 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 取消 `OI / 市值 > 200%` 的交易上限，让高 OI/MC 标的在满足其余全部条件时可以开多或恢复做多，同时保留独立退出风控。

**Architecture:** 保留 `AMBUSH_OI_TO_MARKET_CAP_RATIO` 作为页面风险分区，不再从 `trade_dispatch.py` 引入或用于候选筛选。所有 `OI / 市值 > 90%` 的标的进入同一套 K 线、OI 增长、EMA、成交额、RSI、退出冷却判断；活动仓位仍先回放并执行 ATR、移动保护和 EMA 强制退出。

**Tech Stack:** Python 3.12、`unittest`、SQLite 状态存储、React 18、Vitest、Vite。

---

## 文件职责与变更范围

- `src/crypto_oi_monitor/trade_dispatch.py`：交易候选筛选、K 线加载、状态机扫描和 OI/MC 原因判断。
- `tests/test_trade_dispatch.py`：高 OI/MC 的开多、恢复、持仓、故障和退出回归测试。
- `src/crypto_oi_monitor/notifier.py`、`tests/test_notifier.py`：企业微信交易条件和旧原因码兼容文案。
- `frontend/src/App.jsx`、`frontend/src/sources.test.js`、`frontend/src/trade-condition-panel.test.jsx`：页面交易条件说明和旧原因码兼容显示。
- `README.md`、`docs/2026-09-07-entry-rule-tightening.md`：当前规则和 2026-09-10 规则修订说明。
- `docs/superpowers/specs/2026-09-10-high-oi-long-entry-design.md`：已批准设计，不修改其行为定义。

不修改 `domain.py`：`AMBUSH_OI_TO_MARKET_CAP_RATIO = 2` 和 `risk_status()` 继续服务风险展示。

---

### Task 1: 用回归测试锁定高 OI/MC 的统一交易行为

**Files:**
- Modify: `tests/test_trade_dispatch.py:962-1040`
- Modify: `tests/test_trade_dispatch.py:1575-1685`
- Modify: `tests/test_trade_dispatch.py:1923-1981`
- Modify: `tests/test_trade_dispatch.py:2060-2088`
- Modify: `tests/test_trade_dispatch.py:2183-2222`
- Modify: `tests/test_trade_dispatch.py:2569-2608`
- Test: `tests/test_trade_dispatch.py`

- [ ] **Step 1: 把无仓位的埋伏区拒绝测试改成允许开多测试**

将 `test_does_not_enter_long_in_ambush_zone` 替换为：

```python
def test_enters_long_above_two_times_market_cap(self) -> None:
    store = MemoryStore()
    notifier = RecordingNotifier()
    loaded_symbols = []
    snapshot = {
        "complete": True,
        "comparisons": [
            {
                "canonical_symbol": "PEPE",
                "total_oi_usd": 100,
                "oi_to_market_cap": 2.01,
                "contracts": [{"venue": "Binance", "symbol": "PEPEUSDT"}],
            }
        ],
    }

    result = dispatch_trade_signals(
        snapshot,
        lambda symbol: loaded_symbols.append(symbol) or _long_setup_candles(),
        store,
        notifier,
    )

    self.assertEqual(loaded_symbols, ["PEPEUSDT"])
    self.assertEqual(result.events, ("long",))
    self.assertEqual(result.scans[0].status, CAN_LONG)
    self.assertEqual(result.scans[0].reasons, ())
    self.assertEqual(store.get_trade_signal_state("PEPE").status, "long")
```

- [ ] **Step 2: 把无通知扫描的埋伏区短路测试改成统一 K 线扫描测试**

将 `test_scans_ambush_zone_without_loading_klines` 替换为：

```python
def test_scans_above_two_times_market_cap_with_klines(self) -> None:
    loaded_symbols = []
    snapshot = {
        "complete": True,
        "comparisons": [
            {
                "canonical_symbol": "PEPE",
                "oi_to_market_cap": 2.01,
                "contracts": [{"venue": "Binance", "symbol": "PEPEUSDT"}],
            }
        ],
    }

    result = scan_trade_conditions(
        snapshot,
        lambda symbol: loaded_symbols.append(symbol) or _long_setup_candles(),
    )

    self.assertEqual(loaded_symbols, ["PEPEUSDT"])
    self.assertEqual(result.failures, ())
    self.assertEqual(result.scans[0].status, CAN_LONG)
    self.assertEqual(result.scans[0].reasons, ())
```

- [ ] **Step 3: 新增高 OI/MC 恢复做多测试**

在冷却和恢复相关测试旁加入：

```python
def test_resumes_no_add_above_two_times_market_cap(self) -> None:
    store = MemoryStore()
    notifier = RecordingNotifier()
    candles = _second_long_setup_candles()
    store.states["PEPE"] = TradeSignalState(
        status=NO_ADD,
        entry_price=100.5,
        stop_loss=90,
        entry_atr=1,
        highest_close=100.5,
        last_processed_candle_close_time=candles[-1].close_time,
        binance_symbol="PEPEUSDT",
    )
    snapshot = {
        "complete": True,
        "comparisons": [
            {
                "canonical_symbol": "PEPE",
                "total_oi_usd": 100,
                "oi_to_market_cap": 2.01,
                "contracts": [{"venue": "Binance", "symbol": "PEPEUSDT"}],
            }
        ],
    }

    result = dispatch_trade_signals(snapshot, lambda _: candles, store, notifier)

    self.assertEqual(result.events, (RESUME_LONG,))
    self.assertEqual(store.get_trade_signal_state("PEPE").status, "long")
```

- [ ] **Step 4: 把活动仓位降级测试改成保持 long 的测试**

将 `test_scan_persists_no_add_when_active_long_enters_ambush_zone` 改名为 `test_scan_keeps_active_long_above_two_times_market_cap`，保留现有状态和快照夹具，断言：

```python
self.assertEqual(result.scans[0].status, CAN_LONG)
self.assertEqual(result.scans[0].reasons, ())
self.assertEqual(result.state_updates, ())
```

- [ ] **Step 5: 保留高 OI/MC 下的独立强制退出覆盖**

将 `test_mandatory_exit_takes_priority_over_ambush_zone_stop` 改名为 `test_mandatory_exit_still_applies_above_two_times_market_cap`，保留 `2.01` 比率和 `_atr_exit_candles()`，断言：

```python
self.assertEqual(result.events, (EXIT_LONG,))
self.assertEqual(result.scans[0].status, EXIT_LONG)
self.assertIn("atr_stop_loss", result.scans[0].reasons)
```

保留 `test_scans_active_ambush_position_for_trailing_exit` 的行为并改名为 `test_scans_active_high_ratio_position_for_trailing_exit`。

- [ ] **Step 6: 更新高 OI/MC 故障测试，禁止无依据降级状态**

将以下仅由 `2.01` 触发 `STOP_LONG`/`NO_ADD` 的测试改成故障可见、状态不变：

- `test_scan_stops_active_long_for_oi_threshold_when_kline_load_fails` → `test_scan_preserves_active_long_above_two_times_market_cap_when_kline_load_fails`
- `test_dispatch_stops_active_long_when_oi_is_blocked_during_a_replay_gap` → `test_dispatch_preserves_active_long_above_two_times_market_cap_during_a_replay_gap`。
- `test_scan_stops_active_long_when_oi_is_blocked_during_a_replay_gap` → `test_scan_preserves_active_long_above_two_times_market_cap_during_a_replay_gap`。
- `test_stops_active_long_for_oi_threshold_without_a_binance_contract` → `test_preserves_active_long_above_two_times_market_cap_without_a_binance_contract`
- 高 OI/MC 缺失 Binance 合约：断言 `events == ()`、`KLINE_ERROR`、原状态不变。

派发测试使用这些断言：

```python
self.assertEqual(result.events, ())
self.assertEqual(result.failures[0].canonical_symbol, "PEPE")
self.assertEqual(result.scans[0].status, "kline_error")
self.assertEqual(store.states["PEPE"], state)
```

扫描测试使用这些断言：

```python
self.assertEqual(result.failures[0].canonical_symbol, "PEPE")
self.assertEqual(result.scans[0].status, "kline_error")
self.assertEqual(result.state_updates, ())
```

- [ ] **Step 7: 新增“高 OI 仍受其他条件约束”测试**

```python
def test_high_oi_ratio_does_not_bypass_entry_conditions(self) -> None:
    store = MemoryStore()
    notifier = RecordingNotifier()
    snapshot = {
        "complete": True,
        "comparisons": [
            {
                "canonical_symbol": "PEPE",
                "total_oi_usd": 100,
                "oi_to_market_cap": 2.01,
                "contracts": [{"venue": "Binance", "symbol": "PEPEUSDT"}],
            }
        ],
    }

    result = dispatch_trade_signals(
        snapshot, lambda _: _rsi_above_60_candles(), store, notifier
    )

    self.assertEqual(result.events, ())
    self.assertEqual(notifier.signals, [])
    self.assertEqual(result.scans[0].status, STOP_LONG)
    self.assertIn("ema200_not_crossed_up", result.scans[0].reasons)
```

- [ ] **Step 8: 运行新行为测试并确认 RED**

Run:

```powershell
$env:PYTHONPATH='src'; python -m unittest tests.test_trade_dispatch.TradeDispatchTests.test_enters_long_above_two_times_market_cap tests.test_trade_dispatch.TradeDispatchTests.test_resumes_no_add_above_two_times_market_cap tests.test_trade_dispatch.TradeDispatchTests.test_scans_above_two_times_market_cap_with_klines tests.test_trade_dispatch.TradeDispatchTests.test_scan_keeps_active_long_above_two_times_market_cap
```

Expected: FAIL；当前实现会跳过 K 线、返回 `STOP_LONG` 或阻止 `LONG`/`RESUME_LONG`。

---

### Task 2: 删除交易状态机中的 200% 上限

**Files:**
- Modify: `src/crypto_oi_monitor/trade_dispatch.py:11-14`
- Modify: `src/crypto_oi_monitor/trade_dispatch.py:416-489`
- Modify: `src/crypto_oi_monitor/trade_dispatch.py:1207-1285`
- Modify: `src/crypto_oi_monitor/trade_dispatch.py:1422-1465`
- Modify: `src/crypto_oi_monitor/trade_dispatch.py:1780-1800`
- Test: `tests/test_trade_dispatch.py`

- [ ] **Step 1: 从交易模块移除展示阈值依赖**

把 import 改为：

```python
from .domain import TRADE_ENTRY_OI_TO_MARKET_CAP_RATIO
```

- [ ] **Step 2: 允许所有大于 90% 的完整候选进入派发和 K 线加载**

将派发候选条件改为：

```python
if complete and (
    ratio is not None and ratio > TRADE_ENTRY_OI_TO_MARKET_CAP_RATIO
    or state is not None
):
    candidates.append((comparison, state))
```

将 `candidate_comparisons` 改为：

```python
candidate_comparisons = [
    comparison
    for comparison, _ in candidates
    if comparison["canonical_symbol"] not in state_failures_by_symbol
]
```

- [ ] **Step 3: 删除 scan-only 的高 OI/MC K 线短路**

将 `kline_comparisons` 改为：

```python
kline_comparisons = [
    comparison
    for comparison in comparisons
    if comparison["canonical_symbol"] not in failures_by_symbol
]
```

删除 `scan_trade_conditions()` 中 `state is None and ratio > 2` 时直接添加 `STOP_LONG` 的分支。

- [ ] **Step 4: 删除通用扫描构造器中的高 OI/MC 停止分支**

在 `_build_trade_condition_scans()` 中保留 `ratio <= TRADE_ENTRY_OI_TO_MARKET_CAP_RATIO` 的过滤，删除 `ratio > AMBUSH...` 时构造 `STOP_LONG` 并 `continue` 的分支。

- [ ] **Step 5: 让 OI/MC 原因函数只处理 90% 下限**

```python
def _oi_ratio_trade_reasons(ratio: float | None) -> tuple[str, ...]:
    if ratio is None:
        return ()
    if ratio <= TRADE_ENTRY_OI_TO_MARKET_CAP_RATIO:
        return ("oi_to_market_cap_not_above_90",)
    return ()
```

`_kline_independent_stop_reasons()` 保持调用该函数，因此完整快照中只有 `<= 90%` 能在 K 线不可用时独立触发 `long -> no_add`。

- [ ] **Step 6: 运行交易状态机测试并确认 GREEN**

Run:

```powershell
$env:PYTHONPATH='src'; python -m unittest tests.test_trade_dispatch
```

Expected: 当前模块全部通过；高 OI/MC 开多、恢复、保持状态和强制退出场景均为 GREEN。

- [ ] **Step 7: 确认 200% 阈值不再参与交易代码**

Run:

```powershell
rg -n "AMBUSH_OI_TO_MARKET_CAP_RATIO|oi_to_market_cap_in_ambush_zone" src/crypto_oi_monitor/trade_dispatch.py
```

Expected: 无匹配。

- [ ] **Step 8: 提交状态机变更**

```powershell
git add src/crypto_oi_monitor/trade_dispatch.py tests/test_trade_dispatch.py
git commit -m "允许高OI市值比标的开多"
```

---

### Task 3: 更新企业微信与前端规则文案

**Files:**
- Modify: `src/crypto_oi_monitor/notifier.py:127-146`
- Modify: `src/crypto_oi_monitor/notifier.py:206-223`
- Modify: `tests/test_notifier.py:120-180`
- Modify: `frontend/src/App.jsx:49-69`
- Modify: `frontend/src/App.jsx:326-338`
- Modify: `frontend/src/sources.test.js:39-51`
- Modify: `frontend/src/trade-condition-panel.test.jsx:7-22`
- Modify: `frontend/src/trade-condition-panel.test.jsx:47-69`

- [ ] **Step 1: 先修改文案测试并确认 RED**

`tests/test_notifier.py` 的做多通知断言改为：

```python
self.assertIn("OI / 市值 > 90%", content)
self.assertNotIn("不超过 200%", content)
```

`frontend/src/trade-condition-panel.test.jsx` 的首个测试改名为 `describes the same-bar breakout and 2x volume rules without an OI cap`，断言：

```javascript
expect(html).toContain("扫描 OI / 市值 &gt; 90% 的标的");
expect(html).not.toContain("开多仅限不超过 200%");
expect(html).toContain("当根上穿 EMA200");
expect(html).toContain("前20根均量的2倍");
expect(html).toContain("RSI 回升");
```

旧原因码兼容断言改为：

```javascript
expect(tradeSignalReason("oi_to_market_cap_in_ambush_zone"))
  .toBe("历史规则：OI / 市值高于 200%");
```

Run:

```powershell
$env:PYTHONPATH='src'; python -m unittest tests.test_notifier
cd frontend
npm test -- src/sources.test.js src/trade-condition-panel.test.jsx
```

Expected: FAIL；当前企业微信和页面仍显示“不超过 200%”或“禁止开多”。

- [ ] **Step 2: 更新企业微信做多条件和旧原因码兼容文案**

把 `_trade_signal_text()` 中条件开头改为：

```python
f"做多条件：OI / 市值 > {TRADE_ENTRY_OI_TO_MARKET_CAP_RATIO * 100:.0f}%；"
```

保留旧原因码映射以渲染数据库中的历史快照，但改为中性文案：

```python
"oi_to_market_cap_in_ambush_zone": "历史规则：OI / 市值高于 200%",
```

- [ ] **Step 3: 更新前端交易条件说明和旧原因码兼容文案**

`tradeSignalReason()` 保留旧 key 并改为：

```javascript
if (reason === "oi_to_market_cap_in_ambush_zone") return "历史规则：OI / 市值高于 200%";
```

交易条件标题改为：

```jsx
<span>扫描 OI / 市值 &gt; 90% 的标的；当根上穿 EMA200、当前仍在其上方且 EMA200 向上；价格校正 OI 增长；成交额突破前20根均量的2倍；RSI 回升</span>
```

将空指标渲染测试改名为 `shows unavailable legacy-scan indicators as dashes instead of zeroes`，继续用旧原因码验证历史快照兼容性。

- [ ] **Step 4: 运行通知和前端测试并确认 GREEN**

Run:

```powershell
$env:PYTHONPATH='src'; python -m unittest tests.test_notifier
cd frontend
npm test
```

Expected: 通知测试和 16 个前端测试全部通过。

- [ ] **Step 5: 提交文案变更**

```powershell
git add src/crypto_oi_monitor/notifier.py tests/test_notifier.py frontend/src/App.jsx frontend/src/sources.test.js frontend/src/trade-condition-panel.test.jsx
git commit -m "同步高OI市值比开多文案"
```

---

### Task 4: 修订当前规则文档

**Files:**
- Modify: `README.md:75-95`
- Modify: `docs/2026-09-07-entry-rule-tightening.md:1-170`
- Add: `docs/superpowers/plans/2026-09-10-high-oi-long-entry.md`

- [ ] **Step 1: 更新 README 的可执行规则**

把所有 `90% < OI/MC <= 200%`、`开多仅限不超过 200%` 和“埋伏区禁止开多”改成：

```markdown
- 开多和恢复做多要求 `OI / 市值 > 90%`，不设 OI/MC 上限；`> 200%` 仍显示为埋伏区/高风险，但执行与其他候选相同的完整入场条件。
- `OI / 市值 <= 90%` 可触发停止开多；`> 200%` 本身不触发停止开多或必须退出。
- ATR、移动保护和 EMA 结构退出条件独立生效，并优先于开多或恢复信号。
```

- [ ] **Step 2: 给原审计文档增加规则修订记录并消除当前规则冲突**

在文档开头加入：

```markdown
> 2026-09-10 规则修订：经人工交易判断确认，`OI / 市值 > 200%` 不再作为开多上限。该区间仍保留“埋伏/高风险”展示，但在满足其他全部条件时允许开多或恢复做多；高 OI/MC 本身不触发停止或退出。本文中的历史回测结论保留为审计背景，当前可执行规则以第 4 节为准。
```

将第 3.1 节改为“撤销埋伏区交易上限”，明确旧原因码只用于历史快照。第 4 节改为：

```markdown
2. `OI / 市值 > 90%`，不设上限；`> 200%` 仅影响风险展示。
```

停止开多说明只保留 `OI/MC <= 90%`、数据不完整和价格/EMA 等现有停止条件；必须退出仍只列 ATR、移动保护和 EMA 结构条件。

- [ ] **Step 3: 检查文档不存在现行规则矛盾**

Run:

```powershell
rg -n "不超过 200|≤ 200|禁止开多|埋伏区" README.md docs/2026-09-07-entry-rule-tightening.md
```

Expected: 只允许历史背景、2026-09-10 修订说明或“旧规则”语境中的匹配；当前规则段落不得把 `> 200%` 作为交易拦截。

- [ ] **Step 4: 提交文档和实施计划**

```powershell
git add README.md docs/2026-09-07-entry-rule-tightening.md docs/superpowers/plans/2026-09-10-high-oi-long-entry.md
git commit -m "更新高OI市值比交易规则文档"
```

---

### Task 5: 完整验证并更新现有 PR

**Files:**
- Verify: `app.py`
- Verify: `src/crypto_oi_monitor/*.py`
- Verify: `tests/*.py`
- Verify: `frontend/src/*`
- Verify: `README.md`
- Verify: `docs/2026-09-07-entry-rule-tightening.md`

- [ ] **Step 1: 运行后端全量测试**

```powershell
$env:PYTHONPATH='src'; python -m unittest discover -s tests
```

Expected: 238 个测试通过，0 failures，0 errors。测试输出中的异常堆栈来自故障注入用例，不代表测试失败。

- [ ] **Step 2: 运行 Python 编译检查**

```powershell
python -m compileall -q app.py src\crypto_oi_monitor
```

Expected: exit code 0，无输出。

- [ ] **Step 3: 运行前端全量测试与生产构建**

```powershell
cd frontend
npm test
npm run build
```

Expected: 4 个测试文件、16 个测试通过；Vite 构建成功。

- [ ] **Step 4: 检查补丁范围和空白错误**

```powershell
git diff --check HEAD~3..HEAD
git status --short
```

Expected: `git diff --check` 无输出；状态中只剩此前明确排除的无关未跟踪文件。

- [ ] **Step 5: 推送分支并确认 PR #4 已更新**

```powershell
git push origin codex/volume-rsi-entry
gh pr view 4 --repo hblicy/CryptoOIMonitor --json state,url,commits
```

Expected: 推送成功；PR `https://github.com/hblicy/CryptoOIMonitor/pull/4` 为 `OPEN`，包含本计划产生的提交。
