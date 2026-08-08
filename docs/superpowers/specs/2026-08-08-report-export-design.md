# 正式报告导出设计

## 目标

让每次成功的 `crewai run` 自动生成根目录 `investment-report.md`，内容是 Report Crew 草稿经过 Python Renderer 与最终 Markdown 验证后的完整报告。

## 边界

- 不改变 Report Crew、ReportDraft、Renderer、Claim Gate 或 Verdict 的职责。
- LLM 只生成现有九段叙述草稿；数字、Claims、来源和状态继续由确定性 Renderer 注入。
- 只在 `status == "ok"`、`stage == "report"` 且 `report` 为非空字符串时导出。
- 导出路径与 `run-output.md` 同目录；默认是项目根目录 `investment-report.md`。
- 失败/阻断时不写入新报告，也不删除已有报告；`run-output.md` 必须准确表示未生成本次正式报告。
- 不新增依赖；默认测试离线。

## 数据流

`Report Crew -> parse_report_draft -> render_validated_report -> validate_rendered_report -> Flow state.report -> kickoff OutputWriter -> investment-report.md`

导出实现放在现有 `kickoff()` 末端，而不是 CrewAI Task 的 `output_file`。这样只有已通过最终验证的结果会写入文件，避免草稿或无效 LLM 内容被当成正式报告。
