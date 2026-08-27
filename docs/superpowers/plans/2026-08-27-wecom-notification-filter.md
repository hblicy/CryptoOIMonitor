# 企业微信交易通知精简 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 企业微信只发送做多、恢复做多、必须退出及包含“可以做多/必须退出”的双分组列表，同时完整保留停止做多状态机和 Web 展示。

**Architecture:** 交易扫描仍产生 `STOP_LONG` 扫描结果和状态变化，但分发层不再调用停止做多通知。条件列表分发只用 `can_long` 与 `exit_long` 判断新增和生成通知，存储结构继续保留 `stop_long` 字段以避免数据库迁移并保持现有状态兼容。

**Tech Stack:** Python 3.10、`unittest`、企业微信机器人 Webhook、SQLite 状态存储

---

### Task 1: 停止单币停止做多通知

**Files:**
- Modify: `tests/test_trade_dispatch.py`
- Modify: `src/crypto_oi_monitor/trade_dispatch.py:231-260, 515-563, 739-873`
- Modify: `src/crypto_oi_monitor/notifier.py:40-63, 203-238`
- Modify: `tests/test_notifier.py:219-305`

- [x] **Step 1: 把停止做多行为测试改为断言不发送企业微信**

在 OI/市值阈值、K 线加载失败、数据不完整和收盘价不高于 EMA200 四条状态迁移路径中，保留事件、原因、扫描结果和状态断言，把通知断言改为：

```python
self.assertEqual(result.events, (STOP_LONG,))
self.assertEqual(notifier.stop_longs, [])
self.assertEqual(store.states["PEPE"].status, NO_ADD)
```

- [x] **Step 2: 运行测试并确认红灯**

Run: `$env:PYTHONPATH='src'; python -m unittest discover -s tests -p 'test_trade_dispatch.py'`

Expected: FAIL，断言显示 `notifier.stop_longs` 仍包含 `PEPE`。

- [x] **Step 3: 删除停止做多通知调用，保留状态变化**

从 `TradeSignalNotifier` 协议删除 `send_stop_long`。四条 `STOP_LONG` 路径不再创建通知事件或调用 `_deliver_trade_notification_once`，只执行：

```python
next_state = replace(state, status=NO_ADD, binance_symbol=binance_symbol)
store.set_trade_signal_state(canonical_symbol, next_state)
```

继续追加 `STOP_LONG` 到结果的 `events` 和 `details`，继续生成 `TradeConditionScan(status=STOP_LONG)`；状态写入异常继续进入 `record_candidate_failure`。

- [x] **Step 4: 删除无调用方的停止消息实现和测试**

从 `WeComNotifier` 删除 `send_stop_long`，删除 `_stop_long_message`，并删除 `tests/test_notifier.py` 中直接验证【交易信号：停止开多】文案的测试。停止原因映射继续保留给 Web 使用。

- [x] **Step 5: 验证并提交**

Run: `$env:PYTHONPATH='src'; python -m unittest discover -s tests -p 'test_trade_dispatch.py'; python -m unittest discover -s tests -p 'test_notifier.py'`

Expected: 两组测试均为 `OK`。

```powershell
git add -- src/crypto_oi_monitor/trade_dispatch.py src/crypto_oi_monitor/notifier.py tests/test_trade_dispatch.py tests/test_notifier.py
git commit -m "停止推送停止做多单币通知"
```

### Task 2: 条件列表只保留可以做多和必须退出

**Files:**
- Modify: `tests/test_notifier.py:36-65`
- Modify: `tests/test_trade_dispatch.py:304-328, 947-1145`
- Modify: `src/crypto_oi_monitor/notifier.py:87-106, 178-193`
- Modify: `src/crypto_oi_monitor/trade_dispatch.py:190-202, 317-400`

- [x] **Step 1: 先修改列表消息测试**

```python
notifier.send_trade_condition_list(
    ("AKE", "BULLA"), ("KOMA",), periodic=False
)
self.assertIn("可以做多", content)
self.assertIn("AKE、BULLA", content)
self.assertIn("必须退出", content)
self.assertIn("KOMA", content)
self.assertNotIn("停止做多", content)
```

定时列表调用改为 `notifier.send_trade_condition_list((), (), periodic=True)`。

- [x] **Step 2: 新增停止做多变化不触发列表更新的测试**

```python
def test_does_not_send_list_when_only_stop_long_symbols_appear(self) -> None:
    now = datetime(2026, 8, 1, 0, 10, tzinfo=timezone.utc)
    last_sent_at = now - timedelta(minutes=10)
    store = ConditionListStore(
        SimpleNamespace(
            can_long=("AKE",), stop_long=("ON",),
            exit_long=(), last_sent_at=last_sent_at,
        )
    )
    notifier = ConditionListNotifier()
    result = dispatch_trade_condition_list(
        (
            _condition_scan("AKE", CAN_LONG),
            _condition_scan("ON", STOP_LONG),
            _condition_scan("ESPORTS", STOP_LONG),
        ),
        store, notifier, now,
    )
    self.assertIsNone(result)
    self.assertEqual(notifier.lists, [])
    self.assertEqual(store.state.last_sent_at, last_sent_at)
```

测试替身只接收 `(can_long, exit_long, periodic)`；可以做多新增、必须退出新增和每小时列表的期望值同步为双分组元组。

- [x] **Step 3: 运行测试并确认红灯**

Run: `$env:PYTHONPATH='src'; python -m unittest discover -s tests -p 'test_notifier.py'; python -m unittest discover -s tests -p 'test_trade_dispatch.py'`

Expected: FAIL，现有接口仍要求 `stop_long` 参数，正文仍含“停止做多”，停止做多新增仍返回 `updated`。

- [x] **Step 4: 把列表通知接口改为双分组**

`TradeConditionListNotifier` 与 `WeComNotifier.send_trade_condition_list` 改为：

```python
def send_trade_condition_list(
    self,
    can_long: tuple[str, ...],
    exit_long: tuple[str, ...],
    periodic: bool,
) -> None:
    ...
```

正文只生成：

```python
return (
    f"{title}\n\n"
    f"可以做多\n{can_long_content}\n\n"
    f"必须退出\n{exit_long_content}"
)
```

- [x] **Step 5: 从触发和去重中移除停止做多**

`_condition_list_notification_event_id` 的参数与哈希载荷只保留 `can_long` 和 `exit_long`。新增判断改为：

```python
has_new_symbols = bool(
    set(can_long) - set(previous.can_long)
    or set(exit_long) - set(previous.exit_long)
)
```

通知只传 `can_long`、`exit_long` 和 `periodic`。成功推送后仍保存 `TradeConditionListState(can_long, stop_long, exit_long, now)`，避免 SQLite 迁移且不影响 Web。

- [x] **Step 6: 验证并提交**

Run: `$env:PYTHONPATH='src'; python -m unittest discover -s tests -p 'test_notifier.py'; python -m unittest discover -s tests -p 'test_trade_dispatch.py'`

Expected: 两组测试均为 `OK`。

```powershell
git add -- src/crypto_oi_monitor/notifier.py src/crypto_oi_monitor/trade_dispatch.py tests/test_notifier.py tests/test_trade_dispatch.py
git commit -m "精简企业微信交易条件列表"
```

### Task 3: 同步说明并执行全量验证

**Files:**
- Modify: `README.md:70-89`

- [x] **Step 1: 更新企业微信说明**

通知总览只列【交易信号：做多】、【交易信号：恢复做多】、【交易信号：必须退出】和双分组列表。停止开多规则保留，但明确说明只更新内部状态和 Web，不发送单币企业微信通知。

列表规则改为：

```markdown
- 当“可以做多”或“必须退出”相对上一次已成功推送的列表有新增币种时，企业微信立即推送当前两类完整列表；“停止做多”变化不触发企业微信列表更新。没有新增时，每 1 小时推送一次当前列表。
```

- [x] **Step 2: 搜索残留通知实现和过时说明**

Run: `rg -n "交易信号：停止开多|send_stop_long|停止做多.*企业微信" src README.md`

Expected: README 仅保留明确“不推送”的描述，不存在停止做多企业微信实现。

- [x] **Step 3: 运行后端全量验证**

Run: `$env:PYTHONPATH='src'; python -m unittest discover -s tests -p 'test_*.py'; python -m compileall -q app.py src tests`

Expected: 全部后端测试为 `OK`，编译检查退出码为 0。

- [x] **Step 4: 运行前端回归验证**

Run: `Set-Location frontend; npm test; npm run build`

Expected: Vitest 全部通过，Vite 构建成功；Web 的“停止做多”分组测试继续通过。

- [x] **Step 5: 检查并提交文档**

```powershell
git diff --check
git add -- README.md docs/superpowers/plans/2026-08-27-wecom-notification-filter.md
git commit -m "同步企业微信通知规则说明"
```
