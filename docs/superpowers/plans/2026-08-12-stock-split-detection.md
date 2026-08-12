# Stock Split Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect SEC/XBRL stock splits through the existing edgartools facts container, adjust historical share counts before calculating share change, and have validation reject unverifiable adjustments.

**Architecture:** `EdgarTool` emits a small typed `corporate_actions` payload and an explicit scan status. `FinancialCalculatorTool` consumes that payload only for `share_dilution`, applying applicable split factors deterministically. `FinancialValidationTool` receives the same payload and independently recomputes the adjusted result. Existing formula IDs, agent prompts, report templates, and all non-share metrics remain unchanged.

**Tech Stack:** Python 3.12, Pydantic, CrewAI `BaseTool`, edgartools 5.45.1, unittest/pytest, `Decimal`.

## Global Constraints

- Do not add dependencies or make new network calls.
- Do not let an LLM infer split ratios or decide whether an adjustment is valid.
- If split scanning is unavailable, only the share-change calculation becomes unavailable; the full report continues.
- Do not use raw cross-period share change when the scan status is unavailable.
- Preserve the existing `formula_id="share_dilution"` for downstream compatibility.
- Preserve unrelated user changes in the dirty worktree.

### Task 1: Add the EDGAR corporate-action contract

**Files:**
- Modify: `src/stockcrewai/tools/edgar_tool.py`
- Modify: `src/stockcrewai/tools/__init__.py`
- Test: `tests/test_financial_tools.py`

**Interfaces:**
- Produces `CorporateAction`, `corporate_action_scan_status`, and `corporate_actions` on `EdgarResult`.
- The detector consumes an edgartools facts container and returns `checked` plus zero or more stock-split actions; missing detector support returns `unavailable` without fabricating an action.

- [ ] **Step 1: Write the failing EDGAR contract test**

Add a fake facts container whose `_facts` includes one `StockSplitConversionRatio` record and assert that an `EdgarResult` exposes a forward split action with ratio `10` and effective date `2025-11-14`.

- [ ] **Step 2: Run the focused test and confirm it fails**

Run:

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync pytest tests/test_financial_tools.py -k stock_split -q
```

Expected: failure because `EdgarResult` has no corporate-action fields or detector output.

- [ ] **Step 3: Implement the smallest detector**

Add a Pydantic `CorporateAction` model with `action_id`, `action_type`, `direction`, `ratio`, `effective_date`, `evidence_id`, and `source_reference`. Add `corporate_action_scan_status` and `corporate_actions` defaults to `EdgarResult`. Reuse `edgar.ttm.splits.detect_splits` on the already-loaded facts container; create stable action/evidence IDs from ticker, date, and ratio. Keep scan failures as `unavailable` and do not add a fake action.

- [ ] **Step 4: Run the focused test and confirm it passes**

Run the same focused pytest command; expected: PASS.

### Task 2: Make the calculator split-aware

**Files:**
- Modify: `src/stockcrewai/tools/calculator_tool.py`
- Test: `tests/test_financial_tools.py`

**Interfaces:**
- `FinancialCalculatorTool.run(..., corporate_actions=None, corporate_action_scan_status="unavailable")`.
- `CalculationResult` adds optional `adjustment_basis`, `adjustment_factor`, and `corporate_action_ids` fields with backward-compatible defaults.

- [ ] **Step 1: Write three failing calculator tests**

Cover: a verified 10:1 split produces approximately `-2.01%`; a checked scan with no action keeps the raw result; an unavailable scan returns `share_dilution` as unavailable with `corporate_action_scan_unavailable`.

- [ ] **Step 2: Run the focused tests and confirm they fail**

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync pytest tests/test_financial_tools.py -k "calculator and (split or share_dilution)" -q
```

- [ ] **Step 3: Implement the share-only adjustment**

Add the optional input fields. For `share_dilution`, require a checked scan, select actions whose effective date is after `shares_prior.period_end` and on/before `shares_current.period_end`, multiply the prior share count by all applicable ratios, and calculate using the adjusted prior value. Leave all other formulas untouched. Record the adjustment metadata in `CalculationResult` and include the action evidence IDs in `input_evidence_ids`.

- [ ] **Step 4: Run the focused tests and confirm they pass**

Run the same command; expected: PASS.

### Task 3: Verify corporate-action provenance and arithmetic

**Files:**
- Modify: `src/stockcrewai/tools/validation_tool.py`
- Test: `tests/test_financial_tools.py`

**Interfaces:**
- `FinancialValidationTool.run(..., corporate_actions=None, corporate_action_scan_status="unavailable")`.
- Validation recognizes action evidence IDs and validates the adjustment metadata before adding a calculation to `validated_calculation_ids`.

- [ ] **Step 1: Write failing validator tests**

Cover: a correct adjusted calculation validates; a calculation with a wrong factor or wrong adjusted prior value is invalid; an action ID absent from the supplied action list is invalid.

- [ ] **Step 2: Run the validator tests and confirm they fail**

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync pytest tests/test_financial_tools.py -k "validator and split" -q
```

- [ ] **Step 3: Implement deterministic validation**

Build an action index from the supplied corporate actions, reject unknown action evidence, recompute the applicable factor from dates and ratios, verify `shares_prior_comparable`, then call the existing formula recomputation. Do not validate an unavailable share calculation as valid.

- [ ] **Step 4: Run the validator tests and confirm they pass**

Run the same command; expected: PASS.

### Task 4: Pass the contract through the Flow

**Files:**
- Modify: `src/stockcrewai/flow.py`
- Modify: `src/stockcrewai/pipelines/evidence_pipeline.py`
- Modify: `src/stockcrewai/tools/__init__.py`
- Test: `tests/test_flow_tool_integration.py` or `tests/test_main_flow.py`

**Interfaces:**
- The evidence stage passes EDGAR corporate actions and scan status to Calculator and Validation.
- Validated pipeline state preserves `corporate_actions` and `corporate_action_scan_status` for run output without changing agent input schemas.

- [ ] **Step 1: Add a failing flow plumbing assertion**

Inject fake EDGAR actions, run the evidence stage with fake calculator/validator tools, and assert both tools receive the same action payload and scan status.

- [ ] **Step 2: Run the focused integration test and confirm it fails**

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync pytest tests/test_flow_tool_integration.py -k corporate_action -q
```

- [ ] **Step 3: Add only the two arguments and state fields**

Pass `edgar_result.corporate_actions` and `edgar_result.corporate_action_scan_status` at the two existing tool calls and preserve them in the validated state. Do not change Crew/Agent prompts.

- [ ] **Step 4: Run the focused integration test and confirm it passes**

Run the same command; expected: PASS.

### Task 5: Regression and live smoke verification

**Files:**
- No production changes unless a focused regression exposes a contract mismatch.

- [ ] **Step 1: Run all financial and pipeline tests**

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync pytest tests/test_financial_tools.py tests/test_pipeline_modules.py tests/test_flow_tool_integration.py -q
```

- [ ] **Step 2: Run a direct NFLX edgartools smoke check**

Use `uv run --env-file .env` with `EDGAR_LOCAL_DATA_DIR=/private/tmp/stockcrewai-edgar-data`; assert the scan contains a 10:1 action and the adjusted prior shares are `4249263460`.

- [ ] **Step 3: Run `git diff --check` and compile the touched modules**

```bash
git diff --check
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -m compileall -q src/stockcrewai/tools/edgar_tool.py src/stockcrewai/tools/calculator_tool.py src/stockcrewai/tools/validation_tool.py src/stockcrewai/flow.py src/stockcrewai/pipelines/evidence_pipeline.py
```

- [ ] **Step 4: Report exact test results and remaining live-network limitations**

