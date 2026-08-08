# 确定性报告渲染设计

## 目标

消除 Report Agent 自由改写数字造成的 Report Gate 误判或漏判。Agent 只写不含
数字的中文叙述草稿，Python 使用已通过 Claim Gate 的 Claims、Verdict 和估值
状态生成最终 Markdown。

## 契约

Report Agent 输出唯一 JSON 对象 `ReportDraft`，固定包含执行摘要、公司质量、
财务趋势、当前估值、历史估值、反向 DCF、主要风险、方法和免责声明九个字符串
字段。每个字符串必须非空、不得包含阿拉伯数字、代码围栏、买入/卖出/持有建议，
也不得生成 Claim ID、数字或新事实。

Python Report Gate 只校验 `ReportDraft` 的字段、类型、非空、无数字和禁止内容。
它不再对 LLM 自由文本执行数字相似度推断。

Python Renderer 使用已验证输入生成最终报告：保留草稿叙述，按固定顺序加入确定性
状态、已验证 Claim 原文、估值结果、来源元数据和非投资建议声明。最终报告中的
数字只来自已验证状态，不由 Report Agent 生成。

## 失败行为

ReportDraft 结构错误或包含数字时，Report Task 本地重试；重试后仍失败则设置
`report_output_invalid` 并阻断最终报告。Report Agent 不会绕过 Claim Gate，Renderer
不接收原始或 rejected Claims。

## 不在范围内

不修改 SEC、Yahoo、计算器、估值公式、Analysis Claim Gate、Verdict 政策或依赖。
