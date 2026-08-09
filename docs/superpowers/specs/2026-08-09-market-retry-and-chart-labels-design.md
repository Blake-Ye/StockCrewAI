# 行情重试与图表标签修复设计

## 目标

在不修改 Flow、Agent、Prompt、Gate 和报告数据结构的前提下：

1. 让 Yahoo/yfinance 的第三方 TLS、代理、连接和超时异常进入现有有限重试流程。
2. 保持持续失败时 `status=unavailable` 和 Analysis Gate 严格阻断，不缓存旧价格、不伪造价格、不使用降级行情。
3. 修复第一张财务质量图两端百分比文字被坐标轴裁切的问题。

## 根因

阻断发生在 `MarketPriceTool`：历史失败产物记录了 `SSLError`，市场价格字段为空，随后确定性 Analysis Gate 产生 `current_valuation_required`。图表只在 Claim Gate 通过后的 Report Crew 中生成，无法反向影响更早的行情阶段。

当前 `_is_retryable()` 只直接识别标准库 `ssl.SSLError`，但 yfinance 依赖链可能抛出 `requests.exceptions.SSLError` 或 `curl_cffi.requests.exceptions.SSLError`；两者均不是标准库异常的子类，因此没有进入现有重试。

第一张图的问题独立存在：Matplotlib 根据柱体自动计算横轴范围，但柱外的 `axes.text()` 不参与自动范围计算，最大正值和最小负值标签会越过坐标轴边界。

## 设计

### 行情可靠性

- 保留现有重试次数、指数等待、history-first 和 info fallback。
- 只扩展“可重试异常”的类型识别：沿异常 MRO 识别第三方 `SSLError`、`ProxyError`、`ConnectionError`、`Timeout`、`ConnectTimeout`、`ReadTimeout`，并保留现有 YFinance 异常。
- 持续失败仍返回 `MarketPriceResult(status="unavailable")`；不改变 Gate。
- 使用离线 fake 异常测试瞬时失败后成功，测试不得访问 Yahoo。

### 图表布局

- 仅在 `_financial_kpi_png()` 中使用 Matplotlib 原生 `axes.margins(x=0.10)` 为柱外文字留出双侧空间。
- 不增加新依赖，不创建布局配置层，不修改其他两张图。
- 回归测试实际执行 draw callback，并用 renderer 检查每个百分比文本的像素边界完整位于 `axes.bbox` 内。

## 成功标准

- 第三方 `SSLError` 首次失败、第二次成功时，工具返回完整 `ok` 行情并恰好等待一次。
- 持续限流或连接失败仍不产生任何价格字段。
- `-1.67%` 与 `115.31%` 均完整位于第一张图坐标轴内。
- `tests/test_market_price_tool.py`、`tests/test_report_visuals.py` 和全量离线测试通过。
- 真实运行若外部 Yahoo 持续不可用，可以继续被 Gate 阻断；这属于正确的 fail-closed 行为。

