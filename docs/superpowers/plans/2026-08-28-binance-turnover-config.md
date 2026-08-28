# Binance 成交额门槛配置 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 支持用 `BINANCE_MIN_TURNOVER_USD=5M` 自定义 Binance USDⓈ 永续合约 24 小时成交额门槛，并在页面展示实际生效值。

**Architecture:** 启动层负责解析和校验金额简写，再把数值显式传给应用、数据源和币种池判断，底层模块不读取环境变量。应用摘要返回当前门槛，前端使用统一金额格式函数动态渲染。

**Tech Stack:** Python 3.10、`argparse`、`unittest`、React、Vitest

---

### Task 1: 解析 `5M` 金额简写

**Files:**
- Modify: `app.py:1-20, 503-568`
- Test: `tests/test_app.py:1-25`

- [ ] **Step 1: 写失败的金额解析测试**

在 `tests/test_app.py` 导入 `non_negative_usd_amount`，新增：

```python
class UsdAmountArgumentTests(unittest.TestCase):
    def test_parses_plain_and_suffixed_usd_amounts(self) -> None:
        expected = {
            "0": 0,
            "5000000": 5_000_000,
            "5M": 5_000_000,
            "7.5m": 7_500_000,
            "12K": 12_000,
            "0.02B": 20_000_000,
        }
        for value, amount in expected.items():
            with self.subTest(value=value):
                self.assertEqual(non_negative_usd_amount(value), amount)

    def test_rejects_invalid_usd_amounts(self) -> None:
        for value in ("-1", "NaN", "inf", "5T", "5 M", "5,000,000", "", "1e6"):
            with self.subTest(value=value):
                with self.assertRaises(ArgumentTypeError):
                    non_negative_usd_amount(value)
```

- [ ] **Step 2: 运行测试并确认红灯**

Run: `$env:PYTHONPATH='src'; python -m unittest discover -s tests -p 'test_app.py'`

Expected: ERROR，`app` 尚未导出 `non_negative_usd_amount`。

- [ ] **Step 3: 实现金额解析函数**

在 `app.py` 引入 `re`，新增：

```python
USD_SUFFIX_MULTIPLIERS = {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}


def non_negative_usd_amount(value: str) -> float:
    match = re.fullmatch(r"(?:\d+(?:\.\d*)?|\.\d+)([KMB]?)", value.strip(), re.IGNORECASE)
    if match is None:
        raise argparse.ArgumentTypeError(
            "USD amount must be a non-negative number with optional K, M, or B suffix"
        )
    suffix = match.group(1).upper()
    number_text = value.strip()[:-1] if suffix else value.strip()
    amount = float(number_text) * USD_SUFFIX_MULTIPLIERS[suffix]
    if not math.isfinite(amount):
        raise argparse.ArgumentTypeError("USD amount must be finite")
    return amount
```

- [ ] **Step 4: 验证并提交解析器**

Run: `$env:PYTHONPATH='src'; python -m unittest discover -s tests -p 'test_app.py'`

Expected: `OK`。

```powershell
git add -- app.py tests/test_app.py
git commit -m "支持成交额金额简写"
```

### Task 2: 把自定义门槛传入 Binance 币种池

**Files:**
- Modify: `src/crypto_oi_monitor/domain.py:7-49`
- Modify: `src/crypto_oi_monitor/sources.py:71-97, 346-350`
- Test: `tests/test_domain.py:31-38`
- Test: `tests/test_sources.py:26-95`

- [ ] **Step 1: 写失败的自定义边界测试**

把领域测试改为显式传入门槛：

```python
def test_uses_custom_binance_turnover_threshold(self) -> None:
    self.assertTrue(is_binance_universe_member("PERPETUAL", 8_000_000, 8_000_000))
    self.assertFalse(is_binance_universe_member("PERPETUAL", 7_999_999.99, 8_000_000))
    self.assertFalse(is_binance_universe_member("CURRENT_QUARTER", 50_000_000, 8_000_000))
```

在来源测试中把 ETH/LOW 成交额改为 `8_000_000` 边界，并调用：

```python
universe = parse_binance_universe(exchange_info, tickers, 8_000_000)
```

- [ ] **Step 2: 运行测试并确认红灯**

Run: `$env:PYTHONPATH='src'; python -m unittest discover -s tests -p 'test_domain.py'; python -m unittest discover -s tests -p 'test_sources.py'`

Expected: ERROR，现有函数不接受门槛参数。

- [ ] **Step 3: 显式传递门槛**

领域函数改为：

```python
def is_binance_universe_member(
    contract_type: str,
    quote_volume_usd: float,
    min_turnover_usd: float,
) -> bool:
    return contract_type == "PERPETUAL" and quote_volume_usd >= min_turnover_usd
```

删除固定 `BINANCE_MIN_TURNOVER_USD` 常量。`parse_binance_universe` 增加 `min_turnover_usd` 参数并传给领域函数；`fetch_binance_universe` 改为：

```python
def fetch_binance_universe(
    client: PublicHttpClient, min_turnover_usd: float
) -> dict[str, BinanceInstrument]:
    return parse_binance_universe(
        client.get_json(BINANCE_EXCHANGE_INFO_URL),
        client.get_json(BINANCE_TICKERS_URL),
        min_turnover_usd,
    )
```

- [ ] **Step 4: 验证并提交币种池修改**

Run: `$env:PYTHONPATH='src'; python -m unittest discover -s tests -p 'test_domain.py'; python -m unittest discover -s tests -p 'test_sources.py'`

Expected: 两组测试均为 `OK`。

```powershell
git add -- src/crypto_oi_monitor/domain.py src/crypto_oi_monitor/sources.py tests/test_domain.py tests/test_sources.py
git commit -m "允许自定义币安成交额门槛"
```

### Task 3: 接入启动配置与摘要 API

**Files:**
- Modify: `app.py:110-180, 393-397, 529-575`
- Test: `tests/test_app.py:329-350`

- [ ] **Step 1: 写失败的应用接线测试**

初始化应用时传入 `8_000_000`，执行 `application.coordinator.universe_loader()`，断言：

```python
fetch_binance_universe_mock.assert_called_once_with(ANY, 8_000_000)
```

给通过 `__new__` 创建的应用设置当前门槛，验证普通摘要：

```python
application.binance_min_turnover_usd = 8_000_000
application._latest = {"complete": True}
self.assertEqual(
    application.summary()["settings"]["binance_min_turnover_usd"],
    8_000_000,
)

```

在现有 `test_exposes_trade_signal_details_to_the_web_summary` 的应用夹具上设置 `application.binance_min_turnover_usd = 8_000_000`，并对 `application.refresh()` 的返回值增加：

```python
self.assertEqual(
    snapshot["settings"]["binance_min_turnover_usd"],
    8_000_000,
)
```

- [ ] **Step 2: 运行应用测试并确认红灯**

Run: `$env:PYTHONPATH='src'; python -m unittest discover -s tests -p 'test_app.py'`

Expected: FAIL，应用尚未保存、传递或返回门槛。

- [ ] **Step 3: 接入构造函数、命令行和摘要**

`MonitorApplication.__init__` 增加尾部参数 `binance_min_turnover_usd: float = 5_000_000`，保存为实例属性，币种池加载改为：

```python
universe_loader=lambda: fetch_binance_universe(
    public_client, self.binance_min_turnover_usd
),
```

`refresh()` 在协调器返回快照后立即写入配置，使 `/api/refresh` 响应也包含实际值：

```python
snapshot["settings"] = {
    "binance_min_turnover_usd": self.binance_min_turnover_usd,
}
```

`summary()` 返回副本并覆盖配置，保证服务重启后读取旧快照时仍展示当前值：

```python
def summary(self) -> dict[str, Any]:
    summary = dict(self._latest or {
        "state": "waiting_for_first_refresh",
        "message": "等待首次数据刷新。",
    })
    summary["settings"] = {
        "binance_min_turnover_usd": self.binance_min_turnover_usd,
    }
    return summary
```

参数解析器新增：

```python
parser.add_argument(
    "--binance-min-turnover-usd",
    type=non_negative_usd_amount,
    default=os.environ.get("BINANCE_MIN_TURNOVER_USD", "5M"),
)
```

构造应用时用关键字参数传入，避免现有位置参数错位。

- [ ] **Step 4: 验证并提交应用接线**

Run: `$env:PYTHONPATH='src'; python -m unittest discover -s tests -p 'test_app.py'`

Expected: `OK`。

```powershell
git add -- app.py tests/test_app.py
git commit -m "接入成交额启动配置"
```

### Task 4: 前端动态显示实际门槛

**Files:**
- Modify: `frontend/src/format.js:1-18`
- Modify: `frontend/src/format.test.js:1-20`
- Modify: `frontend/src/App.jsx:1-4, 252`

- [ ] **Step 1: 写失败的中文金额格式测试**

导入 `formatChineseUsd`，新增：

```javascript
it("formats configured turnover thresholds in Chinese units", () => {
  expect(formatChineseUsd(5_000_000)).toBe("500 万 USD");
  expect(formatChineseUsd(10_000_000)).toBe("1,000 万 USD");
  expect(formatChineseUsd(2_500)).toBe("2,500 USD");
  expect(formatChineseUsd(null)).toBe("—");
});
```

- [ ] **Step 2: 运行前端测试并确认红灯**

Run: `npm test`

Expected: FAIL，`formatChineseUsd` 尚未导出。

- [ ] **Step 3: 实现格式函数并替换硬编码文案**

```javascript
const compactNumberFormatter = new Intl.NumberFormat("zh-CN", {
  maximumFractionDigits: 2,
});

export function formatChineseUsd(value) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return "—";
  const numeric = Number(value);
  if (Math.abs(numeric) >= 10_000) {
    return `${compactNumberFormatter.format(numeric / 10_000)} 万 USD`;
  }
  return `${compactNumberFormatter.format(numeric)} USD`;
}
```

`App.jsx` 导入该函数，表格底部改为：

```jsx
<footer className="table-footer">
  共 {rows.length} 条 · Binance USDⓈ 永续 24 小时成交额不少于 {formatChineseUsd(summary?.settings?.binance_min_turnover_usd)}
</footer>
```

- [ ] **Step 4: 验证并提交前端修改**

Run: `npm test; npm run build`

Expected: Vitest 全部通过，Vite 构建成功。

```powershell
git add -- frontend/src/format.js frontend/src/format.test.js frontend/src/App.jsx
git commit -m "动态显示成交额门槛"
```

### Task 5: 配置示例、文档与全量验证

**Files:**
- Modify: `.env.example:10-18`
- Modify: `README.md:1-8`

- [ ] **Step 1: 更新配置示例和说明**

`.env.example` 增加：

```env
# Binance USDⓈ 永续合约 24 小时最低成交额，支持 K/M/B 简写；默认 5M。
BINANCE_MIN_TURNOVER_USD=5M
```

README 把固定 500 万说明改为：默认 `5M`，支持纯数字及 `K/M/B` 简写，修改 `.env` 后重启生效；明确不影响 15m 成交额突破均量规则。

- [ ] **Step 2: 搜索残留硬编码**

Run: `rg -n "BINANCE_MIN_TURNOVER_USD =|不少于 500 万|five_million|5_000_000" src app.py frontend/src tests README.md .env.example`

Expected: 只保留默认值、测试样例和文档示例，不存在固定筛选逻辑或固定页面文案。

- [ ] **Step 3: 运行后端全量验证**

Run: `$env:PYTHONPATH='src'; python -m unittest discover -s tests -p 'test_*.py'; python -m compileall -q app.py src tests`

Expected: 全部后端测试为 `OK`，编译检查退出码为 0。

- [ ] **Step 4: 运行前端全量验证**

Run: `Set-Location frontend; npm test; npm run build`

Expected: Vitest 全部通过，Vite 构建成功。

- [ ] **Step 5: 检查并提交文档**

```powershell
git diff --check
git add -- .env.example README.md docs/superpowers/plans/2026-08-28-binance-turnover-config.md
git commit -m "说明成交额门槛配置"
```
