# 确定性报告数据上下文设计

## 目标

让报告中的数字只来自已经通过验证的事实和计算结果。ReportWriterAgent 仍然负责中文叙述，但不再负责搬运、重算或改写数字；Python 根据稳定的指标 ID 把数字插入对应报告章节。

## 当前问题

当前 Report Crew 虽然收到 `valuation`、`historical_valuation` 和 `reverse_dcf`，但 Renderer 主要把整个映射序列化到 Markdown，同时直接使用 Analysis Claim 的自由文本。这样会带来两个风险：

1. 报告缺少清晰的“指标 → 数值 → 来源”映射；
2. Agent 写入 Claim statement 的数字没有被明确绑定到 Calculation ID。

## 设计

在 `report/crew.py` 增加确定性 `ReportMetric` 和 `ReportContext` 契约。

每个可展示指标至少保存：

- `metric_id`：稳定的展示指标名；
- `display_value`：由工具产生或由 Python 基于已验证值格式化的字符串；
- `unit`：单位；
- `as_of`：时间或期间；
- `source_reference`：来源 URL 或来源标识；
- `evidence_ids`：输入证据白名单；
- `calculation_id`：对应 Calculation ID；市场价格等原始观察值可为空。

`ReportContext` 只包含公司身份、已接受 Claims、确定性 Verdict 状态、规范化指标和来源元数据。`main.py` 在调用 Report Crew 前构造一次，并把同一份 JSON-safe context 同时交给 Report Crew 和 Renderer，避免两条数据路径发生漂移。

当前估值指标来自 `ValuationTool.calculations`，财务指标来自已验证基础 Calculation，历史估值和反向 DCF 结果补充稳定的 Calculation ID 后进入同一指标注册表。Renderer 按固定章节和指标 ID 输出可读的指标行，不再把完整原始映射作为唯一展示方式。

ReportDraft 继续只允许九个无数字中文叙述字段。Agent 的叙述不负责提供数字；数字、状态和来源由 Renderer 注入。Renderer 拒绝缺少来源、验证状态或指标 ID 的数据。

## 流程

```text
validated state
  -> build_report_context()
  -> ReportCrew(report_context)
  -> parse ReportDraft
  -> render_validated_report(report_context, draft)
  -> final Markdown
```

## 兼容性和错误处理

- 保留 `render_validated_report()` 的现有公开调用语义，增加 context 参数时由主流程和测试统一迁移；
- 保留 `valuation`、`historical_valuation`、`reverse_dcf` 原始 state，便于调试和既有调用；
- 缺少必要指标时返回 `report_output_invalid`，不补造数字；
- 历史估值或反向 DCF 不可用时，其结果仍可进入上下文，但不得生成伪造的展示指标；
- 不改变 SEC、Yahoo、Analysis Gate、Claim Gate 和 Verdict 的业务规则。

## 验收标准

1. 报告可以展示价格、P/E、FCF Yield、历史百分位和反向 DCF 隐含增长等已有确定性结果；
2. 每个展示数字都能通过 `metric_id`、`calculation_id`、`evidence_ids` 和来源字段回溯；
3. 报告 Agent 输出任意新数字时，Draft Gate 阻断；
4. Claim statement 中的自由数字不会成为最终指标来源；
5. 历史估值和反向 DCF Claims 不再因为缺少稳定 Calculation ID 而被动失效；
6. 现有离线测试和全量测试保持通过。
