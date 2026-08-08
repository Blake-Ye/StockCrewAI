# AnalysisCrew Hard Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make AnalysisCrew a strict, role-isolated quality gate that blocks Verdict and report generation unless all three analysis domains yield validated Claims.

**Architecture:** Preserve five LLM agents and three CrewAI crews. Extend the existing EDGAR evidence with extracted risk sections, build three role-specific analysis payloads in `main.py`, validate Claims by domain, and return a compact `blocked` result rather than accumulating limitations.

**Tech Stack:** Python 3.12, CrewAI 1.15.11, Pydantic v2, unittest, uv.

## Global Constraints

- Do not add dependencies, Agent, Crew, SQLite persistence, output directories, or a manager/validator Agent.
- Do not upgrade CrewAI; 1.15.11 is installed and the configured DeepSeek endpoint must not receive CrewAI `response_format`.
- Keep the existing `AnalysisCrew` YAML/decorator structure and `deepseek/deepseek-v4-flash` model.
- Use test-first implementation; every changed behavior needs an offline unittest that fails before production code changes.
- Do not commit: the repository has no initial Git commit and all baseline files are untracked.
- Do not expose `.env` values or run live SEC/Yahoo/DeepSeek calls in tests.

---

### Task 1: Extract auditable risk sections from filing text

**Files:**
- Modify: `src/stockcrewai/tools/edgar_tool.py`
- Modify: `tests/test_edgar_text_retrieval.py`

**Interfaces:**
- Produces `EdgarRiskSection(section_type: Literal["10k_item_1a", "10q_item_1a", "8k_event"], text: str)`.
- `EdgarFilingEvidence.risk_sections: list[EdgarRiskSection]` defaults to an empty list.
- `EdgarTool._filing_evidence()` attaches extracted sections only when the parent filing text is available and not truncated.

- [ ] **Step 1: Write failing tests**

Add a fake 10-K text containing `Item 1A. Risk Factors`, a risk paragraph, and `Item 1B.`. Assert one `risk_sections` entry has type `10k_item_1a` and contains only the risk paragraph. Add a truncated 10-K case and assert `risk_sections == []`. Add a complete 8-K case and assert one `8k_event` section is produced.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -m unittest tests.test_edgar_text_retrieval -q
```

Expected: tests fail because `risk_sections` and the extractor do not exist.

- [ ] **Step 3: Implement the smallest extractor**

Add `EdgarRiskSection`, add the list field to `EdgarFilingEvidence`, and add a private regex-based extractor. For 10-K extract text between Item 1A and Item 1B; for 10-Q extract Part II Item 1A until the next Item; for non-truncated 8-K retain the complete text as `8k_event`. Do not treat truncated text as an extracted section.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the focused command above. Expected: all tests pass.

### Task 2: Make AnalysisCrew role-scoped and Claims-only

**Files:**
- Modify: `src/stockcrewai/crews/analysis/crew.py`
- Modify: `src/stockcrewai/crews/analysis/config/agents.yaml`
- Modify: `src/stockcrewai/crews/analysis/config/tasks.yaml`
- Modify: `tests/test_analysis_structured_output.py`

**Interfaces:**
- `AnalysisTaskOutput` contains only `claims: list[AnalysisClaim]`.
- Task inputs are named `financial_analysis_input`, `risk_analysis_input`, and `valuation_analysis_input`.
- All three tasks continue leaving `output_json` and `output_pydantic` unset.

- [ ] **Step 1: Write failing tests**

Replace the old status/limitations/warnings test payload with `{"claims": [...]}`. Assert that a payload containing `limitations` or `warnings` fails Pydantic validation. Assert all YAML tasks interpolate only their matching named input and still have no provider structured-output configuration.

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -m unittest tests.test_analysis_structured_output -q
```

Expected: tests fail because the current output model accepts status, reason, limitations, and warnings and YAML references `validated_state`.

- [ ] **Step 3: Implement the narrow contracts and prompts**

Remove `status`, `reason`, `limitations`, and `warnings` from `AnalysisTaskOutput`. Rewrite each Agent goal/backstory and task description to use only its named payload and to return only the JSON object `{"claims": []}`. Financial claims must use `financial_quality` or `financial_trend`; risk claims use `risk`; valuation claims use `current_valuation`, `historical_valuation`, or `reverse_dcf`. Prompts must say that absent Claims are represented by an empty list, never an explanation.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the focused command above. Expected: all tests pass.

### Task 3: Add deterministic analysis preflight and hard report gate

**Files:**
- Modify: `src/stockcrewai/main.py`
- Modify: `src/stockcrewai/crews/report/config/tasks.yaml`
- Modify: `tests/test_crew_configuration.py`
- Modify: `tests/test_runtime_defaults.py`

**Interfaces:**
- Add private builders `_financial_analysis_input`, `_risk_analysis_input`, and `_valuation_analysis_input`.
- Replace `_filter_analysis_claims` with a domain-aware filter that returns accepted Claims and internal gate data, never a limitation string.
- Add `_analysis_gate` returning `{"status": "ready" | "blocked", "required_data": list[str]}`.
- A blocked run sets `status="blocked"`, `stage="analysis"`, `report=None`, and `next_action="补齐 required_data 后重新运行"`; it does not call AnalysisCrew, DeterministicVerdictTool, or ReportCrew when preflight fails, and does not call Verdict or ReportCrew when Claim validation fails.

- [ ] **Step 1: Write failing tests**

Add tests with `RecordingCrew` proving: (a) AnalysisCrew receives three role-scoped payloads and no `validated_state`; (b) missing risk sections blocks before AnalysisCrew; (c) an invalid or empty task output blocks after AnalysisCrew and ReportCrew is never called; (d) valid financial/risk/valuation outputs with every required category call Verdict and ReportCrew; (e) the final blocked result has no `limitations` field and no string containing `analysis notice` or `rejected`.

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -m unittest tests.test_crew_configuration tests.test_runtime_defaults -q
```

Expected: tests fail because the current orchestrator passes the shared state, collects limitations, computes Verdict before analysis, and still calls ReportCrew after invalid Claims.

- [ ] **Step 3: Implement gate orchestration**

Build three payloads after the deterministic tools complete. Require valid financial validation, at least one non-truncated extracted risk section, and valid current/historical/reverse valuation results before AnalysisCrew. Map `tasks_output` by fixed task order to domains and require the configured category set, nonempty IDs, and domain-specific allowlists. On failure return the compact blocked shape and skip all downstream LLM/report work. On success compute Verdict after the gate and call ReportCrew without the former generic limitations input. Remove the `analysis notice` aggregation and report suffix helper. Remove the ReportCrew task's `{limitations}` input and all instructions to preserve a generic limitations list.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the focused command above. Expected: all tests pass.

### Task 4: Regression verification

**Files:**
- No production changes unless a failing regression identifies a defect in Tasks 1-3.

- [ ] **Step 1: Run the full offline suite**

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -m unittest discover -s tests -q
```

- [ ] **Step 2: Compile and check formatting safety**

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -m compileall -q src tests
git diff --check
```

- [ ] **Step 3: Verify the no-report condition without live services**

Run the new mocked hard-gate tests from Task 3. Expected: blocked runs return `report=None`; successful fixtures alone invoke ReportCrew.
