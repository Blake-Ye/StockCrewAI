# Run Output Gap Remediation Plan

> **For agentic workers:** use focused Luna Max implementation tasks. Each task owns only its listed files and must preserve unrelated changes.

**Goal:** Turn the verified SEC data package into an auditable input for financial analysis, risk analysis, valuation interpretation, and a final report, without SQLite persistence or invented financial data.

**Run evidence:** Apple Inc. / AAPL completed request parsing, SEC fact retrieval, ten Decimal calculations, and validation (`status=valid`, `issues=[]`). No analysis Crew or report Crew was run.

## Global Constraints

- Keep SEC source selection, arithmetic, validation, and verdict gates deterministic Python logic; an LLM may only interpret validated inputs.
- Do not add SQLite, background persistence, paid data providers, or automatic external market-data dependencies.
- Preserve every Evidence ID and source reference. Missing data must remain explicit; do not replace it with a guessed value.
- Default tests must be offline. Live SEC or LLM calls are opt-in integration checks only.
- Use the existing uv project and `crewai.tools.BaseTool`; do not expose `.env` contents.

## Recorded Gaps

1. `main.py` stops after parser → EDGAR → calculator → validator; `AnalysisCrew` and `ReportCrew` are configured but never invoked.
2. Filing metadata is present, but filing text is `null`, so a risk claim cannot cite disclosure text.
3. Fact provenance lacks period start, form, and accession metadata even when the resolved fact is tied to a filing.
4. Duration facts labelled `2026-Q3` are year-to-date values; the result does not expose a duration/period label suitable for reports.
5. Current ratio `1.00329` is displayed as `100.33%`; it must display as a multiple, approximately `1.00x`.
6. No market-price input, valuation calculation, or deterministic readiness gate exists. A report must not claim valuation or investment-worthiness without timestamped, sourced price input.
7. Individual facts and calculations retain `validation_status: unvalidated` after the batch validator returns valid, which is confusing for downstream readers; the batch validation lists remain the source of truth unless per-record state is deliberately synchronized.
8. The parsed request has no investment horizon; the final report must state this limitation rather than personalize advice.

## Parallel Work Packages

### A. SEC evidence and disclosure readiness

**Owns:** `src/stockcrewai/tools/edgar_tool.py`, `tests/test_financial_tools.py`

- Preserve fact period semantics and fill source provenance when Edgartools exposes it.
- Provide a bounded, traceable way to retrieve filing text for risk analysis without silently loading it in the default financial-facts run.
- Keep missing metadata/text explicit and add offline tests.

### B. Financial result presentation

**Owns:** `src/stockcrewai/tools/calculator_tool.py`, `tests/test_calculator_presentation.py`

- Display `current_ratio` as an `x` multiple, not a percentage.
- Keep ratio, percentage, currency, evidence IDs, and Decimal arithmetic unchanged for all other formulas.
- Add a focused offline regression test.

### C. Valuation input and deterministic readiness gate

**Owns:** `src/stockcrewai/tools/valuation_tool.py`, `tests/test_valuation_tool.py`

- Add a no-dependency tool that accepts caller-supplied price, timestamp, currency, and source reference.
- Calculate only transparent, evidence-linked metrics supported by existing facts (market capitalization, P/E when EPS is positive, FCF yield when shares/current FCF exist).
- Return `unavailable`/`not_ready` rather than inventing a price, valuation, or verdict; include a deterministic readiness result.
- Do not modify existing tool exports or main-flow files.

### D. Orchestration and report handoff (after A-C)

**Owns:** `src/stockcrewai/main.py`, `src/stockcrewai/tools/__init__.py`, `tests/test_crew_configuration.py`

- Invoke the analysis and report stages only from validated state.
- Keep risk analysis blocked or limited when filing text is absent; keep valuation unavailable when no sourced price input is supplied.
- Produce an explicit limitations list including unspecified investment horizon and unavailable valuation/risk evidence.
- Do not generate a buy/sell recommendation unless a deterministic verdict policy is separately defined and all its inputs are ready.

## Acceptance Checks

- Existing offline tests continue to pass.
- New tests prove current-ratio display, valuation no-data behavior, and SEC provenance/text behavior.
- AAPL run still validates all existing calculations with no unavailable-calculation warnings.
- Any report-stage output identifies unavailable risk/valuation inputs instead of fabricating claims.

## Final Review Findings — Must Fix Before Delivery

1. Analysis Crew output must be parsed and deterministically filtered before it reaches Report Crew. A Claim is reportable only when it has the required shape and every evidence/calculation ID belongs to the validated allowlist; all rejected or unparsable LLM output must remain outside the report.
2. Analysis agents must not retain live retrieval or calculation tools after the validated snapshot is created. They only interpret the supplied, validated state, risk input, and valuation result.
3. Valuation must validate an ISO-8601 timestamp and currency/unit compatibility. A price in one currency must not be combined with EPS or FCF in another currency, and unknown financial units must make the affected valuation metric unavailable.
4. The deterministic valuation result, including price provenance and readiness state, must be supplied to both analysis and report stages.
