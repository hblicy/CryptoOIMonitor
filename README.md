# 合约 OI / 市值监控

汇总 CEX（Binance、OKX、Bybit、Bitget、Gate、KuCoin、BingX、MEXC）与 DEX（Hyperliquid、Aster、Lighter）的永续合约 OI，并按 CoinMarketCap 市值计算 `OI / MC`。OKX 仅统计 USDT 保证金永续合约，避免与币本位合约重复聚合。

- 币种池：仅 Binance USDⓈ 永续合约，且 24 小时美元成交额不少于 **1,000 万 USD**。
- 黄色重点关注：`OI > 1.1 × MC`。
- 红色埋伏候选：`OI > 2 × MC`。
- 当任一交易所或 CoinMarketCap 本轮请求失败时，页面显示数据源错误并暂停新开仓及交易条件列表推送；已有做多/停止做多状态仍使用 Binance 已收盘 15m K 线执行停止做多和必须退出风控。
- CoinMarketCap 未收录的币种仅排除自身，不影响其余币种的市值计算。
- 数据库保存每轮快照和企业微信交易信号状态；不会复用旧快照冒充本轮数据。

## 启动

```powershell
cd D:\code-web3\06-trading-research\CryptoOIMonitor\frontend
npm install
npm run build

cd ..
$env:COINMARKETCAP_API_KEY = "你的 CoinMarketCap Pro API Key" # 必填
$env:WECOM_ROBOT_WEBHOOK_URL = "你的企业微信机器人 Webhook" # 启用提醒时必填
python app.py --port 8766
```

打开 <http://127.0.0.1:8766>。

页面服务启动后立即刷新，之后按固定 120 秒时间点刷新；若单轮耗时超过 120 秒，会在结束后立即补跑一轮，但不会并发重叠，随后从该轮完成时间重新计算 120 秒周期。Binance、BingX 和 Aster 需按交易对拉取 OI，首轮全量刷新通常需要约一分钟；BingX 单个请求超过 12 秒会将该数据源标记为异常并暂停提醒推送。页面会保留最近一次完整快照并显示其时间。
`COINMARKETCAP_API_KEY` 必须存在且不能是空字符串。`REFRESH_SECONDS`、`CMC_REFRESH_SECONDS`、`SNAPSHOT_RETENTION_DAYS`、`MIN_FREE_DISK_GB`、`LOG_MAX_MB` 和 `LOG_BACKUP_COUNT` 必须是大于 0 的整数；配置为 `0` 或负数时服务会拒绝启动。OI 按 `REFRESH_SECONDS`（默认 120 秒）刷新；CoinMarketCap 市值按 `CMC_REFRESH_SECONDS`（默认 600 秒）独立刷新。网页手动刷新采用非阻塞锁，同一时间只允许一轮刷新，且两次成功的手动刷新至少间隔 `REFRESH_SECONDS`；过于频繁时接口返回 HTTP 429。

同一进程会缓存已确认的 CoinMarketCap 币种 ID，后续市值刷新只请求报价，不会重复请求映射接口；币种池在缓存有效期内变化时不会提前调用 CMC，新币先标记为未映射并在下一次定时刷新处理。未映射或符号歧义的币种每小时会重新尝试映射一次。实际请求市值时会在日志中记录。

CoinMarketCap 健康状态会显示市值的实际更新时间；顶部时间仅表示 OI 快照更新时间。快照默认保留 `SNAPSHOT_RETENTION_DAYS=30` 天。若可用磁盘空间低于 `MIN_FREE_DISK_GB=2`，服务会清除历史快照、仅保留最新快照，并在空间足够时收缩数据库；空间仍不足会记录错误日志。

## Ubuntu VPS 启停

```bash
# 首次部署
# 前端构建需使用受支持的偶数版 Node.js，Ubuntu 建议 Node.js 20 LTS。
cp .env.example .env
nano .env                    # 填写 API Key 和 Webhook

cd frontend
npm ci && npm run build
cd ..

# 启动
bash scripts/start.sh

# 查看日志
tail -f data/monitor.log

# 停止
bash scripts/stop.sh
```

启动脚本使用应用内日志轮转，默认单个 `monitor.log` 最大 50 MB、保留 5 个备份；可在 `.env` 中通过 `LOG_MAX_MB` 和 `LOG_BACKUP_COUNT` 调整。命令行参数解析等日志系统启用前的错误单独写入 `data/startup.log`，启动失败时脚本会同时输出启动日志和应用日志。

可通过 `ENV_FILE` 环境变量指定其他配置文件：

```bash
ENV_FILE=/etc/crypto-oi-monitor.env bash scripts/start.sh
```


## OI 埋伏候选

`OI / 市值 > 200%` 时，页面标识为埋伏候选区；仅用于页面观察，不发送企业微信消息。

## 企业微信交易信号与条件列表（仅做多）

企业微信会推送【交易信号：做多】、【交易信号：恢复做多】、【交易信号：停止开多】、【交易信号：必须退出】和交易条件列表；不会自动下单。信号使用 Binance USDⓈ 永续合约的已收盘 15 分钟 K 线：

- 每个 15m EMA200 判断使用截止该时点最近 1001 根已收盘 K 线计算，其中 1000 根用于预热，以对齐 Binance 图表口径；接口单次拉取 1500 根 K 线，为停机后的逐根风控回放保留额外历史。
- 前置条件：当前快照完整且 `OI / 市值 > 90%`。系统在目标时间前后 5 分钟内选取距 15 分钟前最近的完整快照，并用 `当前聚合 OI USD ÷ 当前 Binance 收盘价` 与 `参考聚合 OI USD ÷ 上一根 Binance 收盘价` 比较；价格校正后的持仓量必须增加。缺失历史快照或币种时不发开多信号，并在页面显示原因。
- 做多：上一根已收盘 15m 收盘价不高于当时 EMA200，当前已收盘 15m 收盘价高于当前 EMA200；当前 EMA200 高于 5 根 K 线前的 EMA200；当前 15m USDT 成交额同时高于上一根及前 20 根平均成交额的 `1.2` 倍；RSI(14) 严格低于 60 且高于上一根 RSI，不再设置 RSI 最低值。
- 停止开多：已发做多或恢复做多信号后，`OI / 市值 ≤ 90%`、RSI(14) 达到 60，或 15m 收盘价不高于 EMA200 时推送；该状态表示禁止继续开多或补仓。EMA200 斜率、价格校正 OI 增长、成交额突破和 RSI 回升仅用于确认开多，不会因随后不再满足而单独触发停止开多。
- 恢复做多：停止做多后，只有再次出现新的 EMA200 向上突破并重新满足全部开多条件，才推送一次【交易信号：恢复做多】。该信号仅适用于已经平仓后的重新开仓，不作为亏损仓位补仓依据。
- 初始止损：参考入场价减 `2 × 入场 ATR(14)`。最高已收盘价达到 `入场价 + 3 × 入场 ATR` 后激活移动止盈，活动保护价取旧保护价、`入场价 + 1 × 入场 ATR`、`最高已收盘价 - 2 × 入场 ATR` 三者最大值；保护价只能上移。
- 移动止盈状态会持久化；服务重启后逐根补算停机期间的已收盘 K 线。若 Binance 返回的历史无法连续覆盖缺口，本轮交易信号会明确报错，不会静默漏算期间高点。
- 必须退出：已发做多或恢复做多信号后，已收盘价触及活动保护价、15m 收盘价低于 `EMA200 - 0.5 × 当前 ATR(14)`，或连续两根 15m 收盘价低于各自 EMA200 时推送。
- 动态冷却：必须退出时用当前 `ATR/收盘价` 对比前 96 个该指标的中位数；比值 `≤0.8` 冷却 3 根，`0.8～1.2` 冷却 4 根，`>1.2` 冷却 6 根 15m K 线。冷却结束后仍须出现新的完整开多条件。
- 风险规则：亏损状态不补仓；本系统只提供信号，不会执行实际下单、仓位管理或止损单。
- 杠杆参考：2-3 倍；信号不包含自动下单或仓位金额。
- 同一币种在做多或恢复做多后不会重复推送同类信号；停止后必须出现新的完整突破，才能再次推送恢复做多。
- Web 页面“交易条件扫描”会列出所有 `OI / 市值 > 90%` 的标的，分为可以做多、停止做多、必须退出和 K 线异常，并展示 RSI、收盘价、EMA200、OI / 市值、价格校正 OI、EMA200 斜率及 15m 成交额突破情况。主表的重点关注门槛仍为 `> 110%`，埋伏候选区仍为 `> 200%`。
- 当“可以做多”、“停止做多”或“必须退出”相对上一次已成功推送的列表有新增币种时，企业微信立即推送当前三类完整列表；没有新增时，每 1 小时推送一次当前列表。临时离开列表后又回归的币种不会重复推送。列表只显示币种；K 线扫描存在失败时，不推送不完整列表。
- 完整快照中既不在比较结果、也不在 CoinMarketCap 未映射清单、且没有做多或停止做多状态的币种会清除交易信号状态；已有仓位风控状态即使暂时跌出 Binance 成交额币种池也会保留，并继续使用持久化的 Binance 合约交易对执行 15m 风控。

机器人 Webhook 仅从 `WECOM_ROBOT_WEBHOOK_URL` 环境变量读取，绝不写入代码或提交到仓库。

## 验证

```powershell
$env:PYTHONPATH = 'src'
python -m unittest discover -s tests -v
python -m compileall -q app.py src\crypto_oi_monitor

cd frontend
npm test
npm run build
```

## CoinMarketCap 重名币种映射

同一 ticker 在 CoinMarketCap 有多个活跃候选时，系统不会自动选择，避免取错市值。页面会列出候选的 CMC ID、名称、价格、市值，以及与 Binance 合约价格的价差；价差只用于人工判断，不会自动映射。

确认映射后，在 `.env` 中配置固定 ID 并重启服务：

```bash
CMC_ID_OVERRIDES=BTC:1,ETH:1027
```

若已缓存的 CMC ID 后续返回空市值，系统会使该缓存失效，并在一小时后重新查询映射。
