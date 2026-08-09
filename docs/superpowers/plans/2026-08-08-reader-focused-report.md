# 面向读者的正式报告 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不放松证据与确定性边界的前提下，使正式 Markdown 报告展示可读结论、简洁数字、术语解释与三张内嵌图表。

**Architecture:** 历史估值工具先输出可审计的逐月序列。报告 Renderer 读取同一份已验证 `ReportContext` 和确定性 Verdict，调用纯 Python 图表模块生成 data URI 后内嵌 Markdown；临时 PNG 只存在于系统临时目录并立即删除。

**Tech Stack:** Python、Decimal、unittest、Matplotlib、CrewAI 1.15.11（不升级）

## Global Constraints

- 新增且仅新增生产依赖 `matplotlib`；不运行 `uv sync`，不升级 CrewAI、Pydantic 或 Python。
- LLM 不生成图表、数字、术语、评级或交易指令；Python 只使用已验证数据。
- 图表不留在项目目录；成功报告把 PNG 以 data URI 直接内嵌在 `investment-report.md`。
- 只使用标准 `unittest`；默认测试不得访问 SEC、Yahoo 或 LLM。
- 保持用户现有未提交改动，不重置、不覆盖无关文件。

---

### Task 1: 修复历史估值时点并公开图表序列

**Files:**
- Modify: `src/stockcrewai/tools/historical_valuation_tool.py`
- Modify: `tests/test_historical_valuation_tool.py`

**Interfaces:**
- Produces: 成功 `HistoricalValuationResult` 新增 `series: list[dict[str, str]]`，每项严格为 `{"date": ISO 日期, "pe_ratio": 十进制字符串}`，按日期升序，恰好 60 项。
- Produces: `current_value == series[-1]["pe_ratio"]`；新增 `current_date == series[-1]["date"]`。

- [ ] 写两个失败测试：一项断言序列为 60 个升序点且当前值/日期指向最后点；一项断言不足 60 点时没有序列。
- [ ] 运行聚焦测试，确认因字段不存在或断言不成立失败。
- [ ] 最小实现 Pydantic 字段和逐月序列构造；不改变前视偏差过滤逻辑。
- [ ] 运行聚焦测试及历史估值全部测试。

### Task 2: 构建确定性报告图表与显示格式

**Files:**
- Create: `src/stockcrewai/report_visuals.py`
- Modify: `src/stockcrewai/crews/report/crew.py`
- Modify: `tests/test_crew_configuration.py`
- Create: `tests/test_report_visuals.py`

**Interfaces:**
- Consumes: 已验证财务 calculations、TTM metrics、历史估值 `series` 和确定性 Verdict。
- Produces: `build_report_visuals(...) -> dict[str, str]`，键为 `financial_kpis`、`ttm_scale`、`historical_pe`，值为 PNG data URI；单图缺输入时该键省略而不抛异常。
- Produces: Renderer 输出固定“术语说明”文本、总体判断、非个性化行动参考、格式化金额/比率/倍数以及图表 Markdown。

- [ ] 先写失败测试：三张图返回 `data:image/png;base64,`；报告内嵌三图、没有长 Decimal、没有“无已验证 Claim。”、并含 P/E/TTM/DCF 的固定中文解释与 `总体判断：估值偏贵`。
- [ ] 运行聚焦测试，确认失败。
- [ ] 最小实现视觉模块，使用 `matplotlib` Agg 后端、临时文件上下文和 `base64`；不要创建资产目录。
- [ ] 最小修改 Renderer：按章节筛选财务指标；注入 Verdict 文字、术语词典、行动参考与三图；没有输入时只省略图。
- [ ] 运行聚焦测试与报告配置测试。

### Task 3: 接通 Flow 输入并做端到端验收

**Files:**
- Modify: `src/stockcrewai/main.py`
- Modify: `tests/test_main_flow.py`
- Modify: `tests/test_run_and_save_output.py`

**Interfaces:**
- Consumes: state 中已验证 TTM、historical valuation 和 Verdict。
- Produces: 成功 `result["report"]` 内嵌图表；`investment-report.md` 不引用项目内 PNG。

- [ ] 先写失败测试：真实 `report_context` 包含 TTM，成功的报告含三个 data URI，导出 Markdown 不创建图表文件。
- [ ] 运行聚焦测试，确认失败。
- [ ] 最小接线，保证 ReportContext 只收到通过 Validation/Gate 的状态。
- [ ] 运行全量单元测试、`compileall`、`git diff --check`，再运行一次 `crewai run`；读取生成报告确认三图和术语说明。
