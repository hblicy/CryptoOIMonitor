# 合约 OI / 市值监控

汇总 Binance、OKX、Bybit、Bitget、Gate、Hyperliquid、Aster 的永续合约 OI，并按 CoinGecko 市值计算 `OI / MC`。

- 币种池：仅 Binance USDⓈ 永续合约，且 24 小时美元成交额不少于 **1,000 万 USD**。
- 黄色预警：`OI > MC`。
- 红色高危：`OI > 2 × MC`。
- 当任一交易所或 CoinGecko 本轮请求失败时，页面显示数据源错误并禁止企业微信阈值推送。
- 数据库保存每轮快照和企业微信告警状态；不会复用旧快照冒充本轮数据。

## 启动

```powershell
cd D:\code-web3\06-trading-research\CryptoOIMonitor\frontend
npm install
npm run build

cd ..
$env:COINGECKO_API_KEY = "你的 CoinGecko Demo API Key" # 可选，生产环境建议配置
$env:WECOM_ROBOT_WEBHOOK_URL = "你的企业微信机器人 Webhook" # 启用告警时必填
python app.py --port 8766
```

打开 <http://127.0.0.1:8766>。

页面服务启动后立即刷新，之后默认每 120 秒刷新一次。Binance 和 Aster 需按交易对拉取 OI，首轮全量刷新通常需要约一分钟；页面会保留最近一次完整快照并显示其时间。

## 企业微信告警

只在完整快照中触发：

- 首次进入 `OI > 2 × MC` 时推送高危消息。
- 持续高危不重复推送。
- 从高危回落时推送恢复消息。

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
