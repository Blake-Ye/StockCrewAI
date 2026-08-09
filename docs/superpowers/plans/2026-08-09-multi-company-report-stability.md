# Multi-Company Report Stability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 AAPL、NVDA、TSLA 在具备完整可验证 SEC 证据时稳定通过风险分析与报告生成，同时保持现有 Claim Gate、证据校验和最终报告校验强度。

**Architecture:** SEC 工具先从完整 filing 原文中提取 Item 1A 或高确定性 8-K 事件，再给每个 filing 生成确定性风险资格结果；Flow 只把 eligible Evidence 交给 Risk Agent，并在两次空输出后生成受限的披露事实 Claim。报告阶段把完整 canonical context 投影成不超过 24 KiB 的 NarrativeContext 给 LLM，确定性 Renderer 仍使用完整 context；只有 Draft guardrail 耗尽时才使用固定 Safe Draft，Provider 错误仍硬阻断。

**Tech Stack:** Python 3.10–3.13、CrewAI 1.15.x、Pydantic v2、edgartools 5.x、标准库 `unittest`、uv；不新增依赖。

## Global Constraints

- 所有包管理和测试命令使用 `uv`，并设置 `UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache`；不得执行 `uv sync` 或创建新虚拟环境。
- 默认测试完全离线，不访问 SEC、Yahoo、DeepSeek 或其他外部服务。
- LLM 不选择 SEC 文件、不计算或验证数字、不决定 Verdict，也不能绕过 Claim Gate。
- 10-K/10-Q 风险证据必须来自单独提取的完整 Item 1A；filing 预览可以截断，但风险章节本身不能截断。
- 8-K Item 2.02/9.01 附件外壳不得成为风险证据；初始高确定性 allowlist 固定为 `1.03, 2.05, 2.06, 3.01, 4.01, 4.02, 5.02, 8.01`。
- 没有 eligible 风险证据时必须以 `risk_evidence_missing` 阻断；不得生成伪造 Claim 或缺少风险依据的正式报告。
- Safe Draft 只处理 Report Draft guardrail 耗尽；Provider/transport、Renderer 和最终校验错误分别保持硬阻断。
- 不修改或提交用户运行产物 `investment-report.md`、`run-output.md`、`run-result.json`，也不处理无关的未跟踪计划文件。
- 每个 Task 使用新的 Luna Max 实现者和 Terra High 审查者；实现者不得修改其所有权之外的文件。
- 用户要求逐工作包验收：每个 Task 完成、审查和验证后暂停，收到用户确认后才开始下一个 Task。

## File Structure

- `src/stockcrewai/tools/edgar_tool.py`：负责完整 SEC 风险章节提取和 filing 级风险资格结果，不承担 Claim 生成。
- `src/stockcrewai/pipeline_support.py`：负责 eligible risk packet、确定性 Risk Disclosure Claim Builder 和 Analysis Gate 输入。
- `src/stockcrewai/main.py`：负责 Flow 中 Risk Agent 重试、Builder 接入、报告错误路由与诊断状态。
- `src/stockcrewai/crews/report/crew.py`：负责 NarrativeContext、ReportDraft 校验固定错误码和确定性 Safe Draft。
- `src/stockcrewai/crews/report/config/tasks.yaml`：只描述 NarrativeContext 输入和九字段 Draft 输出约束。
- `tests/test_edgar_text_retrieval.py`：风险章节提取与资格规则的离线行为测试。
- `tests/test_analysis_gate.py`、`tests/test_main_flow.py`：Risk Builder、Flow 阻断和报告恢复行为测试。
- `tests/test_crew_configuration.py`：NarrativeContext、ReportDraft guardrail 和 Safe Draft 契约测试。
- `tests/test_run_and_save_output.py`：自定义输出目录不污染项目根目录的负向指纹测试。

---

### Task 1: Risk Evidence Eligibility and Full Item 1A Retrieval

**Files:**
- Modify: `src/stockcrewai/tools/edgar_tool.py`
- Modify: `tests/test_edgar_text_retrieval.py`

**Interfaces:**
- Consumes: `EdgarTool._filing_evidence(filing, company_cik, include_text, max_text_chars) -> EdgarFilingEvidence` 和 filing 的 `form`、`items`、`text()`、日期、accession、source。
- Produces: `EdgarRiskEligibility`、`EdgarRiskSection.complete`、`EdgarFilingEvidence.risk_eligibility`；后续 Task 2 只信任 `risk_eligibility.eligibility == "eligible"` 且 `risk_sections[*].complete is True` 的 Evidence。

目标数据契约：

```python
class EdgarRiskSection(BaseModel):
    section_type: Literal["10k_item_1a", "10q_item_1a", "8k_event"]
    section_title: str
    text: str
    complete: bool = True


class EdgarRiskEligibility(BaseModel):
    evidence_id: str
    eligibility: Literal["eligible", "rejected"]
    evidence_kind: Literal["item_1a", "substantive_8k_event"] | None = None
    reason_code: Literal[
        "eligible_item_1a",
        "eligible_8k_event",
        "attachment_shell",
        "truncated",
        "unsupported_item",
        "missing_body",
    ]
    section_title: str | None = None
    filed_at: str | None = None
    source_reference: str
```

`EdgarFilingEvidence` 新增：

```python
risk_eligibility: EdgarRiskEligibility
```

- [ ] **Step 1: 写入 TSLA-shaped 8-K 壳式文件的 RED 测试**

在 `tests/test_edgar_text_retrieval.py` 增加一个真实结构的 fake filing：`items=["2.02", "9.01"]`，正文只包含 Item 2.02、Item 9.01 和“Exhibit 99.1 is furnished herewith”附件说明。测试断言：

```python
self.assertEqual(filing.risk_sections, [])
self.assertEqual(filing.risk_eligibility.eligibility, "rejected")
self.assertEqual(filing.risk_eligibility.reason_code, "attachment_shell")
self.assertIsNone(filing.risk_eligibility.evidence_kind)
```

该测试防止的生产回归：恢复“所有完整 8-K 全文都作为 `8k_event`”的旧分支。

- [ ] **Step 2: 写入完整 Item 1A 独立于 filing 预览截断的 RED 测试**

复用现有长 10-K fake，使 `max_text_chars=1000` 时 `filing.text_truncated is True`。把旧的 `test_truncated_10k_has_no_risk_sections` 改成：

```python
self.assertTrue(filing.text_truncated)
self.assertEqual(len(filing.risk_sections), 1)
self.assertTrue(filing.risk_sections[0].complete)
self.assertIn("Risk body that should be retained.", filing.risk_sections[0].text)
self.assertNotIn("ITEM 1B", filing.risk_sections[0].text)
self.assertEqual(filing.risk_eligibility.eligibility, "eligible")
self.assertEqual(filing.risk_eligibility.reason_code, "eligible_item_1a")
```

该测试防止的生产回归：先截断 filing 再解析 Item 1A，导致真实风险正文永远丢失。

- [ ] **Step 3: 写入高确定性 8-K 正文与不支持 Item 的 RED 测试**

加入两种 fake：

1. `items=["5.02"]`，正文包含 `Item 5.02. Departure of Directors or Certain Officers` 和实质事件段落，断言得到一个 `8k_event`、`section_title` 为该 Item 标题、资格为 `eligible_8k_event`。
2. `items=["1.01"]`，即使正文完整也断言 `risk_sections=[]` 且 `reason_code="unsupported_item"`。

该测试防止的生产回归：allowlist 失效或把整个 8-K 当作风险段落。

- [ ] **Step 4: 运行聚焦测试并确认 RED 原因正确**

Run:

```bash
CREWAI_TRACING_ENABLED=false OTEL_SDK_DISABLED=true UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -m unittest tests.test_edgar_text_retrieval -v
```

Expected: 新增测试因 `risk_eligibility`/`section_title` 不存在、截断 10-K 仍无章节、8-K 旧逻辑无条件放行而失败；不得因 import、网络或 fixture 拼写错误失败。

- [ ] **Step 5: 实现最小风险资格与完整章节提取**

在 `edgar_tool.py` 内完成以下最小改动，不新建模块：

1. 添加上面的 Pydantic 契约和常量：

```python
SUBSTANTIVE_8K_ITEMS = frozenset(
    {"1.03", "2.05", "2.06", "3.01", "4.01", "4.02", "5.02", "8.01"}
)
```

2. 把 `_extract_risk_sections` 改为接收完整 `raw_text` 和标准化 `items`。10-K/10-Q 继续选择最长匹配来避开目录，但不再依据 filing 预览的 `text_truncated` 拒绝；章节来自 `raw_text`，并标记 `complete=True`。
3. 8-K 只提取 allowlist 中实际出现在正文的 Item 段落，边界为下一个 `Item X.XX` 或文件结尾；不得返回完整 filing 全文。
4. 在 `_filing_evidence` 中先建立稳定 `evidence_id`，读取 `raw_text` 后先解析风险章节，再单独生成 `text = raw_text[:max_text_chars]` 和 filing 预览 `text_truncated`。
5. 根据结果生成一个 `EdgarRiskEligibility`：Item 1A 为 `eligible_item_1a`，实质 8-K 为 `eligible_8k_event`；2.02/9.01 壳为 `attachment_shell`；文本不可用或空为 `missing_body`；不在 allowlist 为 `unsupported_item`。`truncated` 仅用于确实无法确认独立风险章节完整性的候选，不得因 filing 预览截断而误标已完整提取的 Item 1A。

- [ ] **Step 6: 运行聚焦测试并确认 GREEN**

Run:

```bash
CREWAI_TRACING_ENABLED=false OTEL_SDK_DISABLED=true UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -m unittest tests.test_edgar_text_retrieval -v
```

Expected: 全部通过，零网络调用。

- [ ] **Step 7: 运行 EDGAR 邻接回归和静态检查**

Run:

```bash
CREWAI_TRACING_ENABLED=false OTEL_SDK_DISABLED=true UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -m unittest tests.test_edgar_text_retrieval tests.test_edgar_tool -v
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -m compileall -q src/stockcrewai/tools/edgar_tool.py tests/test_edgar_text_retrieval.py
git diff --check
```

Expected: unittest 与 compileall 退出码 0，`git diff --check` 无输出。

- [ ] **Step 8: Luna 自审、Terra 审查并提交 Task 1**

Luna 报告必须包含 RED 失败摘要、GREEN 通过数量、修改文件和未解决风险。Terra 重点核查：目录误匹配、Item 边界、2.02/9.01 壳排除、完整 Item 1A 不受 preview 截断影响、没有新增网络测试。Critical/Important 问题修复后提交：

```bash
git add src/stockcrewai/tools/edgar_tool.py tests/test_edgar_text_retrieval.py
git commit -m "fix: qualify complete SEC risk evidence"
```

本步骤完成后暂停，等待用户验收。

---

### Task 2: Deterministic Risk Claim Builder and Flow Integration

**Files:**
- Modify: `src/stockcrewai/pipeline_support.py`
- Modify: `src/stockcrewai/main.py`
- Modify: `tests/test_analysis_gate.py`
- Modify: `tests/test_main_flow.py`

**Interfaces:**
- Consumes: Task 1 的 `EdgarFilingEvidence.risk_eligibility` 与完整 `risk_sections`。
- Produces: `_risk_analysis_input(...)` 只包含 eligible Evidence；`build_deterministic_risk_disclosure_claims(risk_input: Mapping[str, Any]) -> list[dict[str, Any]]`；Analysis Gate 固定阻断码 `risk_evidence_missing`。

- [ ] **Step 1: 写入 eligible packet 和 gate RED 测试**

构造三个 filing：eligible Item 1A、rejected attachment shell、rejected missing body。断言 `_risk_analysis_input` 只保留第一个 Evidence ID；无 eligible filing 时 `_analysis_gate` 返回 `risk_evidence_missing`。

- [ ] **Step 2: 写入 Builder RED 测试**

断言 Builder 对 Item 1A 只生成披露事实 Claim：`category="risk"`、`calculation_ids=[]`、`confidence="1"`、只复制 allowlist 内 Evidence ID；输入无 eligible Evidence 时返回空列表。断言 statement 不包含概率、严重度、评级或买卖建议。

- [ ] **Step 3: 写入 Flow 两次空输出 RED 测试**

用 fake Analysis Crew 让 Risk Agent 初次和唯一一次重试均返回 `{"claims": []}`。断言：存在 eligible Evidence 时 Builder 结果继续经过原 `_filter_analysis_claims`/Claim Gate；只有 shell 时在 Analysis Gate 阻断且不会 kickoff Risk Agent。

- [ ] **Step 4: 运行聚焦测试并确认 RED**

```bash
CREWAI_STORAGE_DIR=/private/tmp/stockcrewai-flow-storage CREWAI_TRACING_ENABLED=false OTEL_SDK_DISABLED=true UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -m unittest tests.test_analysis_gate tests.test_main_flow -v
```

- [ ] **Step 5: 实现最小 Builder 和 Flow 接入**

`pipeline_support.py` 负责纯函数 packet/filter/builder；`main.py` 只编排：Risk Agent 首次为空 → 同一 eligible packet 重试一次 → 仍为空时调用 Builder → 仍通过原 Claim Gate。不得让 Builder 接收 rejected Evidence，也不得在无 eligible Evidence 时运行。

- [ ] **Step 6: 运行 GREEN 与邻接回归**

```bash
CREWAI_STORAGE_DIR=/private/tmp/stockcrewai-flow-storage CREWAI_TRACING_ENABLED=false OTEL_SDK_DISABLED=true UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -m unittest tests.test_analysis_gate tests.test_main_flow tests.test_analysis_structured_output -v
git diff --check
```

- [ ] **Step 7: Luna 自审、Terra 审查并提交 Task 2**

```bash
git add src/stockcrewai/pipeline_support.py src/stockcrewai/main.py tests/test_analysis_gate.py tests/test_main_flow.py
git commit -m "fix: stabilize verified risk claims"
```

本步骤完成后暂停，等待用户验收。

---

### Task 3: Compact Narrative Context and Guardrail-Safe Report Recovery

**Files:**
- Modify: `src/stockcrewai/crews/report/crew.py`
- Modify: `src/stockcrewai/crews/report/config/tasks.yaml`
- Modify: `src/stockcrewai/main.py`
- Modify: `tests/test_crew_configuration.py`
- Modify: `tests/test_main_flow.py`

**Interfaces:**
- Consumes: 已通过 Claim Gate 的 accepted claims、ready Verdict 和完整 canonical report context。
- Produces: `build_narrative_context(report_context: Mapping[str, Any], max_bytes: int = 24 * 1024) -> dict[str, Any]`；固定 `REPORT_DRAFT_ERROR_CODES`；guardrail 耗尽时 `draft_source="deterministic_safe_draft"`。

- [ ] **Step 1: 执行 CrewAI 强制版本与官方文档预检**

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -c "import crewai; print(crewai.__version__)"
```

同时检查 PyPI 最新 CrewAI、官方 changelog、Tasks/Guardrails 文档；记录已安装版本和与本任务相关的 API 结论，不升级依赖。若 live docs 与项目 `AGENTS.md` 冲突，以 live docs 为准并先报告用户。

- [ ] **Step 2: 写入 24 KiB NarrativeContext RED 测试**

构造 NVDA-shaped 17 accepted Claims 和超长 canonical context。断言序列化后的 NarrativeContext `<= 24 * 1024` bytes、四类 claim 顺序稳定、保留原始总数、无 evidence 原文/历史价格/source 列表/rejected claims。

- [ ] **Step 3: 写入固定 guardrail code 与 Safe Draft RED 测试**

让 Report Agent 连续返回非 JSON 或含禁用数字的 Draft，断言最终诊断为 `report_guardrail_retries_exhausted`，不保存原始输出，并使用现有 `build_deterministic_report_draft()` 进入 Renderer。让 kickoff 抛 `ConnectionError`，断言 `report_provider_error` 且不得使用 Safe Draft。

- [ ] **Step 4: 运行聚焦测试并确认 RED**

```bash
CREWAI_STORAGE_DIR=/private/tmp/stockcrewai-flow-storage CREWAI_TRACING_ENABLED=false OTEL_SDK_DISABLED=true UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -m unittest tests.test_crew_configuration tests.test_main_flow -v
```

- [ ] **Step 5: 实现最小 NarrativeContext 与错误路由**

NarrativeContext 只包含公司、ticker、期限、Verdict 标签、四类 accepted Claim 摘要、每类原始数量和可用章节。按固定类别顺序逐条加入摘要，加入下一条会超出 24 KiB 时停止该类并保留 count。YAML 输入改为 `{narrative_context}`；Renderer 仍接收完整 canonical context。

ReportDraft 校验只返回设计文档列出的固定 code。`main.py` 只在可确认 guardrail 重试耗尽时调用现有 `build_deterministic_report_draft()`；Provider、Renderer、Final Validator 分别映射到固定错误码并阻断。

- [ ] **Step 6: 运行 GREEN、报告聚焦回归和静态检查**

```bash
CREWAI_STORAGE_DIR=/private/tmp/stockcrewai-flow-storage CREWAI_TRACING_ENABLED=false OTEL_SDK_DISABLED=true MPLCONFIGDIR=/private/tmp/stockcrewai-matplotlib UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -m unittest tests.test_crew_configuration tests.test_main_flow tests.test_report_visuals tests.test_final_report_validator -v
git diff --check
```

- [ ] **Step 7: Luna 自审、Terra 审查并提交 Task 3**

```bash
git add src/stockcrewai/crews/report/crew.py src/stockcrewai/crews/report/config/tasks.yaml src/stockcrewai/main.py tests/test_crew_configuration.py tests/test_main_flow.py
git commit -m "fix: stabilize report draft generation"
```

本步骤完成后暂停，等待用户验收。

---

### Task 4: Output Isolation Regression and Three-Company Acceptance

**Files:**
- Modify: `tests/test_run_and_save_output.py`
- Modify only if the new test proves a defect: `src/stockcrewai/main.py`

**Interfaces:**
- Consumes: `kickoff(inputs=..., output_path=...)` 和三个公共产物文件名。
- Produces: 自定义输出目录调用不会改变项目根目录产物的离线回归保障；不新增 fixture 文件。

- [ ] **Step 1: 写入根目录三文件指纹负向测试**

测试在调用前记录 `investment-report.md`、`run-output.md`、`run-result.json` 的存在性、SHA-256、大小和 `st_mtime_ns`，把 Flow/网络依赖替换为完整离线 fake，然后使用临时目录调用 `kickoff(output_path=temp_dir)`。断言临时目录三产物存在且一致，根目录指纹逐项不变。

- [ ] **Step 2: 运行测试并判断是否存在生产缺陷**

```bash
CREWAI_STORAGE_DIR=/private/tmp/stockcrewai-flow-storage CREWAI_TRACING_ENABLED=false OTEL_SDK_DISABLED=true UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -m unittest tests.test_run_and_save_output -v
```

Expected: 若当前生产实现正确，该特征测试直接通过，只提交测试；若失败，先保留失败证据，再在 `main.py` 做唯一公共写入点的最小修复并重新运行。

- [ ] **Step 3: 运行完整离线回归**

```bash
CREWAI_STORAGE_DIR=/private/tmp/stockcrewai-flow-storage CREWAI_TRACING_ENABLED=false OTEL_SDK_DISABLED=true MPLCONFIGDIR=/private/tmp/stockcrewai-matplotlib UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -m unittest discover -s tests -p 'test_*.py' -v
git diff --check
```

- [ ] **Step 4: Luna 自审、Terra 审查并提交 Task 4**

```bash
git add tests/test_run_and_save_output.py
git commit -m "test: protect custom report output paths"
```

只有 Step 2 真实证明 `main.py` 有缺陷时，才把该文件加入提交。完成后暂停，等待用户批准真实网络验收。

- [ ] **Step 5: 顺序运行 AAPL、NVDA、TSLA 真实验收**

每家公司使用独立 `/private/tmp/stockcrewai-multi-company-acceptance/<ticker>` 输出目录，顺序运行以降低 SEC/Yahoo 限流。每轮检查 `status=ok`、`stage=report`、Claim Gate READY、Verdict ready、`report_status=complete`、Markdown 与 JSON report 相等、manifest 哈希/bytes 正确，并确认 ticker、价格来源和 SEC Evidence 不串写。

外部 SEC、Yahoo 或 DeepSeek 不可用时，记录明确的外部错误和未完成验收状态；不得因此放宽 Gate 或宣称代码回归通过。

