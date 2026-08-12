# Minimal Report Consistency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Execute each task test-first and do not modify files outside the listed ownership.

**Goal:** Make report prose, charts, and reverse DCF use one deterministic data source while improving reader-facing charts and summary with the smallest possible change.

**Architecture:** Keep the existing `ReportContext`, Renderer, and Matplotlib pipeline. Normalize canonical TTM and historical-valuation values in `context.py`; make `visuals.py` and `renderer.py` consume those values without recomputation. Images remain deterministic Python output and are never sent to the LLM.

**Tech Stack:** Python, Pydantic, Decimal, Matplotlib, pytest, uv. No new dependency, Crew, Agent, model, fallback, or persistence layer.

## Global Constraints

- Preserve all unrelated user changes in the dirty worktree.
- Do not restore deleted quant or unsupported-company code.
- Only ordinary operating-company report behavior is in scope.
- Write a failing test and run it before each production change.
- Do not pass images or base64 data to the Report Agent.
- Do not let the LLM select, calculate, or reconcile numeric values.
- A data conflict must fail with a field-specific error; it must not silently choose a fallback.
- Do not commit or push unless the parent agent explicitly performs the final repository operation.

---

### Task 1: Canonical report values and consistency checks

**Files:**
- Modify: `tests/test_reporting_modules.py`
- Modify: `src/stockcrewai/reporting/context.py`

**Interfaces:**
- Consumes: existing `ttm`, `historical_valuation`, `reverse_dcf`, calculations, and source metadata supplied to `build_report_context()`.
- Produces: existing `ReportContext.ttm`, `ReportContext.historical_valuation`, and `ReportContext.reverse_dcf` with mutually consistent display inputs. Do not add a second report-context class.

- [ ] Add a test where ordinary-period FCF differs from TTM FCF. Assert report context retains the TTM value for the TTM block and that reverse DCF base FCF matches it after unit normalization.
- [ ] Run the new test and confirm it fails for the expected mismatch.
- [ ] Add a test where upstream historical P/E summary values differ from percentiles recomputed from `series`. Assert the context preserves the upstream validated `current_value`, `percentile_25`, `five_year_median`, `percentile_75`, and `current_percentile` exactly.
- [ ] Run the new test and confirm it fails because one or more summary fields are currently omitted or altered.
- [ ] Minimally update context projection so TTM FCF and validated historical summary values survive unchanged. Reuse existing models and helpers.
- [ ] Add a field-specific `ValueError` when valid reverse DCF base FCF and canonical TTM FCF are both present but differ after Decimal/unit normalization. Include `report_ttm_fcf_mismatch` in the message.
- [ ] Run `uv run pytest tests/test_reporting_modules.py -q` and `git diff --check`.

### Task 2: Split KPI chart and stop historical P/E recomputation

**Files:**
- Modify: `tests/test_report_visuals.py`
- Modify: `tests/test_reporting_modules.py`
- Modify: `src/stockcrewai/reporting/visuals.py`
- Modify: `src/stockcrewai/reporting/context.py`

**Interfaces:**
- Consumes: the existing `context["metrics"]`, `context["ttm"]`, and `context["historical_valuation"]` fields produced by Task 1.
- Produces: the same public `build_report_visuals(...) -> dict[str, str]` API and the existing keys `financial_kpis`, `ttm_scale`, and `historical_pe`.

- [ ] Replace the single-axis KPI test with a failing test requiring one PNG containing three axes: growth/capital allocation, profitability, and cash-flow quality.
- [ ] Add failing label-boundary assertions for negative share change and cash conversion above 100%.
- [ ] Add a failing test proving historical chart labels use supplied `percentile_25`, `five_year_median`, and `percentile_75`, even when the series would produce different values.
- [ ] Run the focused tests and confirm each fails for the intended old behavior.
- [ ] Implement three subplots inside the existing `financial_kpis` PNG. Give every subplot an independent x-axis and dynamic padding. Keep percent labels outside bars and inside axes.
- [ ] Preserve the existing validated calculation `adjustment_basis` in `ReportMetric`; show raw or split-adjusted share change only after existing validation, and label split-adjusted values explicitly.
- [ ] Remove percentile calculation from `_historical_pe_png`; use only validated summary fields supplied in the historical payload. Keep the series only for plotting the line.
- [ ] Keep TTM scale chart reading only verified `context["ttm"]` metrics.
- [ ] Run `uv run pytest tests/test_report_visuals.py -q` and `git diff --check`.

### Task 3: Reader-facing deterministic report

**Files:**
- Modify: `tests/test_reporting_modules.py`
- Modify: `src/stockcrewai/reporting/renderer.py`

**Interfaces:**
- Consumes: the validated context from Task 1 and chart keys from Task 2.
- Produces: the existing `render_validated_report(...) -> str` Markdown output.

- [ ] Add a failing test asserting the execution summary does not contain `status=`, `Profile：`, `Policy version：`, or raw internal rule codes.
- [ ] Add a failing test asserting the financial-trend prose/table uses TTM FCF from `context["ttm"]`, not same-named ordinary-period calculations.
- [ ] Add a failing test asserting the summary explains relative historical valuation and reverse-DCF implied growth when those verified values exist, and otherwise omits those sentences rather than guessing.
- [ ] Add a failing test asserting visible risks are capped at five and retain SEC source references; additional risks appear under a compact appendix heading.
- [ ] Run focused tests and confirm failures match the old renderer behavior.
- [ ] Replace the debug-style execution summary with deterministic reader-facing sentences assembled from verified context values. Keep the LLM draft as qualitative prose only.
- [ ] Move status/profile/policy/coverage metadata to the existing sources-and-method section.
- [ ] Render a TTM scale table from `context["ttm"]` so prose and chart share the same values and period.
- [ ] Render at most five risk claims in the main section, ordered as supplied; put remaining sourced claims in an appendix. Do not synthesize probability or impact scores.
- [ ] Update first-chart read guidance to explain the three separate panels.
- [ ] Run `uv run pytest tests/test_reporting_modules.py tests/test_report_visuals.py -q`, `uv run python -m compileall -q src/stockcrewai/reporting`, and `git diff --check`.

### Final verification

- [ ] Run the full offline suite: `uv run pytest -q`.
- [ ] Run one real ordinary-company flow with the existing project command and verify the generated Markdown contains exactly one TTM FCF value across prose/chart/DCF after unit conversion.
- [ ] Inspect the generated KPI and historical P/E images visually.
- [ ] Review only this plan's diff; do not include or revert pre-existing worktree changes.
