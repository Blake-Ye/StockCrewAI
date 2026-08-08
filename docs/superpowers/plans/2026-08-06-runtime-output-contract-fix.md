# Runtime Output Contract Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development for each task. The two tasks have disjoint file ownership and may run in parallel.

**Goal:** Make the default `uv run --no-sync kickoff` request carry a three-year investment horizon and make every AnalysisCrew task return a locally validated structured Claims object without sending an unsupported `response_format` to DeepSeek.

**Architecture:** Keep orchestration in `main.py`. Use the existing default-request path for the horizon fix, deduplicate repeated parser limitations at the shared filter boundary, and retain shared Pydantic models as the local analysis contract. The three CrewAI tasks must request the JSON shape through their prompts but leave `output_pydantic` and `output_json` unset because DeepSeek rejects CrewAI's API-level `response_format`. Do not add dependencies or persistence.

**Tech Stack:** Python 3.12, CrewAI 1.15.11, Pydantic 2, unittest, uv.

## Global Constraints

- Use `UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache` and `uv run --no-sync`.
- Do not upgrade CrewAI. Installed 1.15.11 supports `output_pydantic`, but the configured DeepSeek endpoint rejects the resulting API-level `response_format`.
- Do not add SQLite, output directories, new dependencies, or unrelated refactors.
- Preserve explicit missing-horizon behavior: only the no-input default request gets `未来 3 年`.
- Preserve genuine analysis parse failures; only duplicate identical limitation strings are collapsed.
- Enforce the shared Pydantic Claim contract inside the deterministic filter;
  invalid or missing confidence must reject the Claim, while valid confidence is
  preserved in the report-bound payload.
- Do not commit changes.

---

### Task 1: Runtime default request and limitation deduplication

**Files:**
- Modify: `src/stockcrewai/main.py`
- Create: `tests/test_runtime_defaults.py`

**Interfaces:**
- Consumes: `main(request: str | None = None)` and `_filter_analysis_claims(...)`.
- Produces: default request `分析苹果公司未来 3 年投资价值`; ordered unique limitation strings.

- [x] **Step 1: Write failing runtime behavior tests**

Add a unittest that patches `run_research`, clears `STOCKCREWAI_REQUEST`, sets `sys.argv` to `['kickoff']`, calls `main()`, and asserts `run_research` receives `分析苹果公司未来 3 年投资价值`.

Add a unittest whose fake AnalysisCrew output has three task outputs containing the same non-JSON text. Assert `_filter_analysis_claims(...)` returns exactly one `analysis output unparseable: expected JSON claims; no claims passed to report.` limitation.

- [x] **Step 2: Verify RED**

Run:

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -m unittest tests.test_runtime_defaults -q
```

Expected: the current default request differs and the repeated limitation count is three.

- [x] **Step 3: Implement the minimum production change**

Change `DEFAULT_REQUEST` to `分析苹果公司未来 3 年投资价值`.

Before `_filter_analysis_claims` returns, normalize limitations with insertion-order deduplication using `list(dict.fromkeys(limitations))`. Do not suppress a unique parse failure.

- [x] **Step 4: Verify GREEN**

Run the focused unittest command again and require all tests to pass.

---

### Task 2: DeepSeek-compatible structured AnalysisCrew outputs

**Files:**
- Modify: `src/stockcrewai/crews/analysis/crew.py`
- Modify: `src/stockcrewai/crews/analysis/config/tasks.yaml`
- Create: `tests/test_analysis_structured_output.py`

**Interfaces:**
- Produces: `AnalysisClaim` and `AnalysisTaskOutput` Pydantic models in `analysis/crew.py`.
- Produces: all three Task objects with `output_pydantic=None` and `output_json=None`; the shared models remain the local validation contract.
- `AnalysisTaskOutput` fields: `status` (`ok`, `unavailable`, `not_ready`), optional `reason`, `claims`, `limitations`, `warnings`.
- `AnalysisClaim` fields: `claim_id`, `category`, `statement`, `evidence_ids`, `calculation_ids`, `confidence` constrained to 0 through 1.

- [x] **Step 1: Write failing structured-output tests**

Add unittest coverage that constructs `AnalysisCrew`, obtains all three task objects, and asserts each task's `output_pydantic` and `output_json` are `None`, preventing CrewAI from sending `response_format` to DeepSeek.

Validate a literal unavailable payload with `claims=[]` and a limitation string, pass its Pydantic representation through `_filter_analysis_claims`, and assert no `analysis output unparseable` limitation is produced.

- [x] **Step 2: Verify RED**

Run:

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -m unittest tests.test_analysis_structured_output -q
```

Expected: the compatibility assertion fails while any task still configures `output_pydantic` or `output_json`.

- [x] **Step 3: Implement the minimum structured contract**

Define the two Pydantic models in `analysis/crew.py` as the shared local contract, but do not pass `output_pydantic` or `output_json` to any task. This preserves deterministic local validation without triggering DeepSeek's unsupported API parameter.

Update each YAML `expected_output` to require one JSON object with this shape:

```json
{"status":"ok","reason":null,"claims":[],"limitations":[],"warnings":[]}
```

For unavailable data, require `status="unavailable"`, `claims=[]`, and the reason repeated in `limitations`. Preserve all existing Evidence and no-hallucination restrictions.

- [x] **Step 4: Verify GREEN**

Run the focused unittest command again and require all tests to pass.

---

### Integration verification

- [x] Run all tests:

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -m unittest discover -s tests -q
```

- [x] Compile source and tests:

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -m compileall -q src tests
```

- [x] Confirm no SQLite artifacts and no trailing whitespace.

- [x] Run the real uv entry point and confirm exit code 0, a parsed three-year
  horizon, no `response_format` error, and no horizon/unparseable limitation.
