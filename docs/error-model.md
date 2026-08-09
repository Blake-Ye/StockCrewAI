# WP00 错误模型与阻断语义

## 1. Typed error

跨工具和 live smoke 的错误至少包含：

```json
{
  "category": "external_dependency",
  "reason_code": "yahoo_rate_limit",
  "message": "可读的非敏感说明",
  "data": null
}
```

`category` 只能按边界使用：`input` 表示请求无效，`external_dependency` 表示 SEC/Yahoo/模型等外部服务失败，`gate` 表示证据或政策不满足，`runtime` 表示程序契约或内部异常。异常详情不能把密钥、完整提示词或响应泄漏到终端报告。

## 2. 稳定 reason code

已使用或冻结的示例包括：`sec_timeout`、`sec_unavailable`、`yahoo_rate_limit`、`market_price_unavailable`、`claims_empty`、`analysis_output_unparseable`、`reverse_dcf_required`、`reverse_dcf_not_applicable`、`evidence_unvalidated`、`calculation_unvalidated`、`ticker_invalid` 和 `result_not_mapping`。同一根因不能一会儿用自然语言、一会儿用不同代码。

## 3. Gate 和报告

阻断是确定性结果，不是 LLM 的意见。Gate `status=blocked` 时，Flow 停止后续 Analysis/Valuation/Report 阶段，不生成正式报告，不制造替代数据；最终 JSON 要保留阻断阶段、`required_data` 和稳定 `reason_code`。可选指标缺失只在 Metric Policy 明确允许时标为 `not_applicable`，不得把“不可适用”当作错误或把真正缺失吞掉。

## 4. 外部失败

外部服务失败必须原样保留为 typed error，并可在显式 live smoke 中观察。默认离线测试使用注入的失败 runner，不触网。没有 fallback、静默降级、旧值回填或“先生成再警告”的路径。
