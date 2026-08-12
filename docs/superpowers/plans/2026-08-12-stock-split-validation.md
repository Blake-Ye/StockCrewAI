# Stock Split Comparability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add SEC-backed stock-split metadata and make share-change calculations split-aware and independently verifiable.

**Architecture:** `EdgarTool` extracts validated corporate-action metadata from fetched filing text. `FinancialCalculatorTool` consumes the metadata and adjusts the prior share count before calculating the existing share-change formula. `FinancialValidationTool` validates the action metadata and recomputes the adjusted formula.

**Tech Stack:** Python 3.10+, Pydantic, CrewAI `BaseTool`, `Decimal`, existing unittest/pytest test suite, no new dependency.

## Global Constraints

- Preserve raw SEC facts and existing `CalculationResult` IDs.
- Never infer a split from a large ratio alone.
- Never use LLM output or Yahoo as the primary corporate-action source.
- Unknown or conflicting corporate-action data must produce an unavailable share-change calculation.
- Preserve unrelated working-tree changes.

### Task 1: Add failing corporate-action and calculator tests

**Files:**
- Modify: `tests/test_financial_tools.py`
- Modify: `tests/test_pipeline_modules.py`

**Interfaces:**
- The tests will require `EdgarResult.corporate_action_scan_status`, `EdgarResult.corporate_actions`, and calculator input fields for those values.

- [ ] **Step 1: Add a failing EDGAR result-contract test**

  Build an `EdgarResult` with a 10-for-1 action and assert the serialized result exposes the scan status and adjustment factor.

- [ ] **Step 2: Add a failing split-aware calculator test**

  Provide current shares, prior shares, period end dates, a checked 10-for-1 action, and assert `shares_prior_comparable` equals `4,249,263,460` and the result is approximately `-0.020079`.

- [ ] **Step 3: Add a failing unverified-scan test**

  Provide the two share facts without a checked corporate-action scan and assert the share calculation is unavailable with `share_count_comparability_unverified`.

- [ ] **Step 4: Run only the new tests**

  Run: `UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run pytest tests/test_financial_tools.py -q`

  Expected: FAIL because the new fields and split-aware behavior do not exist yet.

### Task 2: Implement EDGAR corporate-action output

**Files:**
- Modify: `src/stockcrewai/tools/edgar_tool.py`
- Modify: `src/stockcrewai/tools/__init__.py`
- Modify: `tests/test_financial_tools.py`

**Interfaces:**
- Add `CorporateAction` and `CorporateActionScanStatus` models.
- Add `EdgarResult.corporate_action_scan_status` and `EdgarResult.corporate_actions`.
- Parse complete raw filing text before truncation; preserve filing evidence IDs and source references.

- [ ] **Step 1: Add the Pydantic corporate-action models**
- [ ] **Step 2: Add deterministic ratio/date extraction for numeric and English split forms**
- [ ] **Step 3: Scan fetched 8-K/10-Q/10-K text and populate `EdgarResult`**
- [ ] **Step 4: Add tests for forward split, reverse split, no split, and unavailable text**
- [ ] **Step 5: Run the EDGAR unit tests**

  Run: `UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run pytest tests/test_financial_tools.py -q`

### Task 3: Implement split-aware calculator and validator

**Files:**
- Modify: `src/stockcrewai/tools/calculator_tool.py`
- Modify: `src/stockcrewai/tools/validation_tool.py`
- Modify: `src/stockcrewai/flow.py`
- Modify: `tests/test_financial_tools.py`

**Interfaces:**
- Preserve `formula_id="share_dilution"` for downstream compatibility.
- Add adjustment metadata to `CalculationResult`.
- Pass EDGAR corporate-action metadata from Flow to Calculator and Validation.

- [ ] **Step 1: Add adjustment metadata fields to `CalculationResult`**
- [ ] **Step 2: Select applicable actions by effective date and compute the Decimal adjustment factor**
- [ ] **Step 3: Return unavailable for unchecked, missing, or conflicting corporate-action data**
- [ ] **Step 4: Recompute adjusted prior shares in `FinancialValidationTool`**
- [ ] **Step 5: Add tampered-factor and tampered-result tests**
- [ ] **Step 6: Run calculator and validator tests**

  Run: `UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run pytest tests/test_financial_tools.py tests/test_pipeline_modules.py -q`

### Task 4: Regression verification

**Files:**
- No production files unless a regression is found.

- [ ] **Step 1: Run focused tests**
- [ ] **Step 2: Run the full offline test suite**

  Run: `UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run pytest -q`

- [ ] **Step 3: Run compile and diff checks**

  Run: `UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run python -m compileall -q src tests`
  Run: `git diff --check`

- [ ] **Step 4: Run a deterministic Netflix calculation fixture**

  Confirm the output contains the split factor `10`, comparable prior shares `4249263460`, and a result near `-2.01%`, with no `879.92%` share-change calculation.
