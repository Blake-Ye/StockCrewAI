# Market Retry and Chart Labels Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Retry transient third-party Yahoo TLS/connection exceptions without weakening valuation gates, and keep all first-chart percentage labels inside the plotting area.

**Architecture:** Extend only `MarketPriceTool` exception classification while preserving its existing retry budget and fail-closed result. Independently use Matplotlib's native x margin in the report visual renderer; neither task changes Flow, Agent, Prompt, Gate, or report contracts.

**Tech Stack:** Python 3.12, CrewAI project tools, yfinance, Matplotlib, `unittest`, uv.

## Global Constraints

- All production code changes must be written by the `luna_coder` Luna Max agent.
- Do not modify Flow, Agent, Prompt, Gate, verdict policy, or report data structures.
- Do not cache stale prices, fabricate values, add a second provider, or downgrade `status=unavailable`.
- Preserve the current retry count, exponential delay, history-first quote order, and info fallback.
- Tests must be deterministic and must not call Yahoo, SEC, DeepSeek, or any paid/live API.
- Use `UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync`; do not run `uv sync` and do not create another virtual environment.
- Preserve unrelated user changes and keep the project free of temporary artifacts.

---

### Task 1: Retry third-party transport exceptions

**Files:**
- Modify: `src/stockcrewai/tools/market_price_tool.py:129-137`
- Test: `tests/test_market_price_tool.py`

**Interfaces:**
- Consumes: `MarketPriceTool._retry_call(operation, budget)` and the existing `max_retries`, `retry_delay`, `sleeper` behavior.
- Produces: `_is_retryable(exc: Exception) -> bool` that recognizes supported third-party transport exception class names through the exception MRO.

- [ ] **Step 1: Write the failing behavior test**

Add a fake third-party exception whose class name is `SSLError` but which is not a subclass of `ssl.SSLError`. Add a quote whose first daily-history call raises it and whose second call returns the same complete DataFrame and metadata shape already used by `_SslRetryTicker`. Assert `status == "ok"`, price/timestamp/currency are complete, history was attempted twice, and `sleep_calls == [0.25]`.

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
CREWAI_TRACING_ENABLED=false OTEL_SDK_DISABLED=true UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -m unittest -v tests.test_market_price_tool.MarketPriceToolTests.test_history_retries_third_party_ssl_error
```

Expected before implementation: FAIL because history runs once and the info fallback cannot produce a valid quote.

- [ ] **Step 3: Implement the minimum classifier change**

Keep the existing `isinstance` branch. Extend the MRO-name allowlist to include exactly these transport names in addition to the existing YFinance names:

```python
{
    "SSLError",
    "ProxyError",
    "ConnectionError",
    "Timeout",
    "ConnectTimeout",
    "ReadTimeout",
    "YFRateLimitError",
    "YFConnectionError",
    "YFTimeoutError",
}
```

Do not change `_retry_call`, retry budgets, warning result fields, or Gate logic.

- [ ] **Step 4: Run focused tests and verify GREEN**

```bash
CREWAI_TRACING_ENABLED=false OTEL_SDK_DISABLED=true UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -m unittest -v tests.test_market_price_tool
```

Expected: all market price tests pass without network access.

- [ ] **Step 5: Self-review and report**

Confirm the new test fails if the six third-party transport names are removed, persistent failures remain unavailable, and only the two owned files changed. Write the exact RED/GREEN commands and outputs to the assigned task report.

---

### Task 2: Keep first-chart percentage labels visible

**Files:**
- Modify: `src/stockcrewai/report_visuals.py:187-213`
- Test: `tests/test_report_visuals.py`

**Interfaces:**
- Consumes: `_financial_kpi_png(records) -> str | None` and `_png_uri(draw, size=...)`.
- Produces: the same PNG data URI contract, with every percentage text extent inside the axes rectangle.

- [ ] **Step 1: Write the failing renderer-level test**

Create verified records for `16.15%`, `33.60%`, `27.85%`, `30.24%`, `115.31%`, and `-1.67%`. Patch `_png_uri` only to execute its real draw callback on a Matplotlib figure, call `figure.canvas.draw()`, and collect each `axes.texts` extent via `text.get_window_extent(renderer)`. Assert the six expected labels exist and every extent satisfies:

```python
extent.x0 >= axes.bbox.x0
extent.x1 <= axes.bbox.x1
```

- [ ] **Step 2: Run the test and verify RED**

```bash
CREWAI_TRACING_ENABLED=false OTEL_SDK_DISABLED=true MPLCONFIGDIR=/private/tmp/stockcrewai-matplotlib UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -m unittest -v tests.test_report_visuals.ReportVisualsTests.test_financial_kpi_percentage_labels_stay_inside_axes
```

Expected before implementation: FAIL for at least one edge label.

- [ ] **Step 3: Implement the minimum Matplotlib change**

Add this native layout instruction after the horizontal bars are created and before rendering text:

```python
axes.margins(x=0.10)
```

Do not add a new helper, dependency, configuration object, or change annotation values.

- [ ] **Step 4: Run focused tests and verify GREEN**

```bash
CREWAI_TRACING_ENABLED=false OTEL_SDK_DISABLED=true MPLCONFIGDIR=/private/tmp/stockcrewai-matplotlib UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -m unittest -v tests.test_report_visuals
```

Expected: all report visual tests pass.

- [ ] **Step 5: Self-review and report**

Generate one temporary first-chart PNG from the test records, visually confirm both edge labels, remove the temporary PNG, and record exact test evidence in the assigned task report.

---

### Task 3: Integrated verification

**Files:**
- No production changes expected.
- Verify: all files changed by Tasks 1 and 2.

**Interfaces:**
- Consumes: Task 1 retry behavior and Task 2 unchanged visual data-URI contract.
- Produces: evidence that the independent fixes coexist without changing gate semantics.

- [ ] **Step 1: Run the full offline suite**

```bash
CREWAI_STORAGE_DIR=/private/tmp/stockcrewai-flow-storage CREWAI_TRACING_ENABLED=false OTEL_SDK_DISABLED=true MPLCONFIGDIR=/private/tmp/stockcrewai-matplotlib UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -m unittest discover -s tests -q
```

- [ ] **Step 2: Run static checks**

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -m compileall -q src tests
git diff --check
```

- [ ] **Step 3: Run one real flow for integration evidence**

```bash
CREWAI_STORAGE_DIR=/private/tmp/stockcrewai-flow-storage CREWAI_TRACING_ENABLED=false OTEL_SDK_DISABLED=true MPLCONFIGDIR=/private/tmp/stockcrewai-matplotlib UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync crewai run
```

Accept `status=ok` as live success. If Yahoo remains unavailable after the configured retries, report the exact warning and fail-closed Gate result; do not alter the Gate or fabricate a report.

