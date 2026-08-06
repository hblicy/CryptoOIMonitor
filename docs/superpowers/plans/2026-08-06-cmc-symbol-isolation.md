# CoinMarketCap 非法币种隔离 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 防止单个中文或被 CMC 拒绝的币种符号导致整个 CoinMarketCap 数据源失败。

**Architecture:** 在 map 请求前排除 CMC 明确不接受的非字母数字符号；对于无法从错误文案直接提取具体币种的 `symbol` 类 HTTP 400，递归拆分批次直到隔离单个问题币种。非 `symbol` 错误保持原有失败语义。

**Tech Stack:** Python 3、`unittest`、现有 `DataSourceRequestError` 与 CoinMarketCap HTTP 客户端。

---

### Task 1: 隔离非字母数字币种

**Files:**
- Modify: `tests/test_market_caps.py`
- Modify: `src/crypto_oi_monitor/market_caps.py:526-539`

- [ ] **Step 1: 编写失败测试**

在 `CoinMarketCapMarketCapTests` 中增加：

```python
def test_skips_non_alphanumeric_symbol_without_failing_valid_assets(self) -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, str]]] = []

        def get_json(self, url: str, params: dict[str, str]):
            self.calls.append((url, params))
            if url == CMC_ID_MAP_URL:
                return {"data": [{"id": 1027, "symbol": "ETH"}]}
            return {
                "data": [
                    {
                        "id": 1027,
                        "symbol": "ETH",
                        "quote": {"USD": {"market_cap": 300}},
                    }
                ]
            }

    client = FakeClient()
    with self.assertLogs("crypto_oi_monitor.market_caps", "WARNING") as logs:
        result = fetch_market_caps(client, {"ETH", "龙虾"})

    self.assertEqual(result.market_caps["ETH"].market_cap_usd, 300)
    self.assertEqual(result.unmapped_assets, ("龙虾",))
    self.assertIn("龙虾", "\n".join(logs.output))
    self.assertEqual(client.calls[0], (CMC_ID_MAP_URL, {"symbol": "ETH"}))
```

- [ ] **Step 2: 运行测试并确认失败**

Run:

```powershell
$env:PYTHONPATH = 'src'
python -m unittest discover -s tests -p test_market_caps.py -k test_skips_non_alphanumeric_symbol_without_failing_valid_assets -v
```

Expected: FAIL，因为 map 请求仍包含 `龙虾`。

- [ ] **Step 3: 实现最小预过滤逻辑**

将 `_fetch_mapping_data` 改为只提交字母数字符号，并记录被排除币种：

```python
def _fetch_mapping_data(
    client: MarketCapHttpClient, ordered_assets: list[str]
) -> list[dict[str, Any]]:
    supported_symbols = [
        symbol for symbol in ordered_assets if re.fullmatch(r"[A-Za-z0-9]+", symbol)
    ]
    unsupported_symbols = sorted(set(ordered_assets) - set(supported_symbols))
    if unsupported_symbols:
        LOGGER.warning(
            "CoinMarketCap map cannot query non-alphanumeric symbols: %s",
            ", ".join(unsupported_symbols),
        )
    mapping_data: list[dict[str, Any]] = []
    for start in range(0, len(supported_symbols), 100):
        symbols = supported_symbols[start : start + 100]
        mapping_data.extend(_fetch_mapping_batch(client, symbols))
    return mapping_data
```

- [ ] **Step 4: 运行目标测试**

Run:

```powershell
$env:PYTHONPATH = 'src'
python -m unittest discover -s tests -p test_market_caps.py -k test_skips_non_alphanumeric_symbol_without_failing_valid_assets -v
```

Expected: PASS。

### Task 2: 隔离文案未知的 symbol HTTP 400

**Files:**
- Modify: `tests/test_market_caps.py`
- Modify: `src/crypto_oi_monitor/market_caps.py:542-586`

- [ ] **Step 1: 编写批次拆分失败测试**

增加一个 FakeClient：当 map 参数包含 `REJECTED` 时返回 HTTP 400，错误文案只说明 `symbol` 规则、不指出具体币种；断言程序依次拆分 `ETH,REJECTED`，保留 ETH，并只将 REJECTED 列为未映射。

```python
def test_bisects_generic_symbol_validation_error(self) -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.map_symbols: list[str] = []

        def get_json(self, url: str, params: dict[str, str]):
            if url == CMC_ID_MAP_URL:
                symbols = params["symbol"]
                self.map_symbols.append(symbols)
                if "REJECTED" in symbols:
                    error = DataSourceRequestError("CMC map rejected symbol")
                    error.status_code = 400
                    error.response_payload = {
                        "status": {
                            "error_message": (
                                '"symbol" should only include comma-separated '
                                "alphanumeric cryptocurrency symbols"
                            )
                        }
                    }
                    raise error
                return {"data": [{"id": 1027, "symbol": "ETH"}]}
            return {
                "data": [
                    {
                        "id": 1027,
                        "symbol": "ETH",
                        "quote": {"USD": {"market_cap": 300}},
                    }
                ]
            }

    client = FakeClient()
    result = fetch_market_caps(client, {"ETH", "REJECTED"})

    self.assertEqual(result.market_caps["ETH"].market_cap_usd, 300)
    self.assertEqual(result.unmapped_assets, ("REJECTED",))
    self.assertEqual(client.map_symbols, ["ETH,REJECTED", "ETH", "REJECTED"])
```

- [ ] **Step 2: 编写非 symbol HTTP 400 失败测试**

增加测试，FakeClient 返回 HTTP 400 和 `Invalid request parameter`，断言 `DataSourceRequestError` 继续抛出。

```python
def test_does_not_ignore_unrelated_symbol_map_http_400(self) -> None:
    class FakeClient:
        def get_json(self, url: str, params: dict[str, str]):
            error = DataSourceRequestError("CMC request invalid")
            error.status_code = 400
            error.response_payload = {
                "status": {"error_message": '"symbol" parameter is missing'}
            }
            raise error

    with self.assertRaisesRegex(DataSourceRequestError, "request invalid"):
        fetch_market_caps(FakeClient(), {"ETH"})
```

- [ ] **Step 3: 运行两个测试并确认第一个失败、第二个通过**

Run:

```powershell
$env:PYTHONPATH = 'src'
python -m unittest discover -s tests -p test_market_caps.py -k test_bisects_generic_symbol_validation_error -v
python -m unittest discover -s tests -p test_market_caps.py -k test_does_not_ignore_unrelated_symbol_map_http_400 -v
```

Expected: `test_bisects_generic_symbol_validation_error` FAIL；非 symbol 400 测试 PASS。

- [ ] **Step 4: 实现批次拆分**

扩展 `_invalid_map_symbols`：精确文案仍返回具体币种；其他包含 `symbol` 的 HTTP 400 返回空集合。然后在 `_fetch_mapping_batch` 中，空集合表示无法直接识别问题币种，需要拆分；单币种仍失败则输出警告并返回空结果。

```python
invalid_symbols = _invalid_map_symbols(error)
if invalid_symbols is None:
    raise
unsupported_symbols = sorted(set(remaining_symbols) & invalid_symbols)
if unsupported_symbols:
    LOGGER.warning(
        "CoinMarketCap does not support symbols: %s",
        ", ".join(unsupported_symbols),
    )
    remaining_symbols = [
        symbol for symbol in remaining_symbols if symbol not in invalid_symbols
    ]
    continue
if len(remaining_symbols) == 1:
    LOGGER.warning(
        "CoinMarketCap rejected symbol %s: %s",
        remaining_symbols[0],
        error,
    )
    return []
middle = len(remaining_symbols) // 2
return _fetch_mapping_batch(client, remaining_symbols[:middle]) + _fetch_mapping_batch(
    client, remaining_symbols[middle:]
)
```

在 `_invalid_map_symbols` 的精确正则未匹配时增加：

```python
normalized_error_message = error_message.lower()
if "symbol" in normalized_error_message and "alphanumeric" in normalized_error_message:
    return set()
return None
```

- [ ] **Step 5: 运行 CoinMarketCap 单元测试**

Run:

```powershell
$env:PYTHONPATH = 'src'
python -m unittest tests.test_market_caps -v
```

Expected: 全部 PASS。

### Task 3: 全量验证

**Files:**
- Verify: `src/crypto_oi_monitor/market_caps.py`
- Verify: `tests/test_market_caps.py`

- [ ] **Step 1: 运行 Python 全量测试**

Run:

```powershell
$env:PYTHONPATH = 'src'
python -m unittest discover -s tests -v
```

Expected: 全部 PASS，无 traceback。

- [ ] **Step 2: 编译检查**

Run:

```powershell
python -m compileall -q app.py src\crypto_oi_monitor
```

Expected: exit code 0，无输出。

- [ ] **Step 3: 检查改动范围**

Run:

```powershell
git diff -- src/crypto_oi_monitor/market_caps.py tests/test_market_caps.py
git status --short
```

Expected: 仅出现本计划代码、测试和计划文档；保留用户原有未跟踪文件，不纳入提交。
