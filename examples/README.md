# 真实运行示例

这些文件是 StockCrewAI 真实运行产物的脱敏展示；报告正文、结论和可审计字段保留原始结果。本文档及各示例仅供技术演示，不构成投资建议。

| 示例 | 原始运行状态 | 入口 |
|---|---|---|
| AAPL | `ok` / `report` | [报告](aapl/report.md) · [运行摘要](aapl/run-summary.json) |
| NVDA | `ok` / `report` | [报告](nvda/report.md) · [运行摘要](nvda/run-summary.json) |
| JPM | `blocked` / `analysis` | [阻断结果](jpm/blocked-result.json) · [运行输出](jpm/run-output.md) |

JPM 的阻断原因代码保留在 policy_context.gate.reason_codes，未在顶层新增 domain 或 reason_code。
