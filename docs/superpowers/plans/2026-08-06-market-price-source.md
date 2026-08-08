# Market Price Source Implementation Plan

> **For agentic workers:** Execute this plan inline with the existing uv project. Keep the change limited to the market-price-to-valuation path.

**Goal:** Automatically provide a timestamped, currency-qualified, source-linked market price to `ValuationTool` during a `crewai run` whenever a ticker is known.

**Architecture:** Use the existing `yfinance` dependency in one `MarketPriceTool` that reads quote metadata and bounded historical fallback, returns a typed result, and never invents a price. `run_research()` calls it when no explicit `market_price_data` override is supplied and a ticker is known, then forwards the result to the existing valuation tool. The same change normalizes SEC EPS units and propagates the FCF currency unit so a valid price can produce a ready valuation.

**Tech Stack:** yfinance, Pydantic v2, CrewAI `BaseTool`, Decimal, uv, unittest.

## Global Constraints

- Use the existing uv project; do not create or modify a Conda environment.
- Do not add another Python dependency, SQLite, persistence, Agent, Crew, or unrelated pipeline stage.
- Default tests must not access Yahoo Finance or SEC; use injected/fake HTTP responses.
- A missing, malformed, or failed quote remains `not_ready`; never use a guessed or cached price.
- Preserve `market_price`, ISO-8601 `price_timestamp`, `currency`, and exact `source_reference` in the valuation result.

### Task 1: Lock the quote and valuation handoff with failing tests

**Files:**
- Create: `tests/test_market_price_tool.py`
- Modify: `tests/test_valuation_tool.py`
- Modify: `tests/test_crew_configuration.py`

**Interfaces:**
- `MarketPriceTool(yfinance_module=..., max_retries=..., sleeper=...)` accepts injectable market-data behavior for offline tests.
- `MarketPriceTool.run(ticker="AAPL")` returns a result with `status`, `market_price`, `price_timestamp`, `currency`, and `source_reference`.
- `run_research(..., market_price_tool=fake_tool)` uses the fake result when `market_price_data` is absent.

- [ ] Add an offline success test with a fake Yahoo response containing `regularMarketPrice`, `regularMarketTime`, and `currency`; assert the ISO timestamp and exact chart URL.
- [ ] Add an offline failure test; assert `status="unavailable"` and no fabricated price.
- [ ] Add a valuation regression using SEC-style `USD_per_share` EPS and derived USD FCF; assert `readiness="ready"` when a sourced price is supplied.
- [ ] Add a `run_research()` handoff assertion that the injected quote reaches the valuation tool.
- [ ] Run `UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run python -m unittest ... -q` and confirm these tests fail for the missing tool/handoff or unit contract.

### Task 2: Implement the minimal market-price tool

**Files:**
- Create: `src/stockcrewai/tools/market_price_tool.py`

**Interfaces:**
- `MarketPriceResult` exposes `status: Literal["ok", "unavailable"]`, normalized uppercase `ticker`, string `market_price`, UTC ISO timestamp, currency, source URL, and warnings.
- `MarketPriceTool._run(ticker: str)` reads yfinance `Ticker.info` first, then `fast_info`/`history_metadata`/history as a bounded fallback; persistent upstream failures remain `unavailable`.

- [ ] Parse JSON with Decimal-aware parsing, require a positive regular-market price, Unix timestamp, and currency.
- [ ] Convert the Unix timestamp to a `Z`-suffixed ISO-8601 timestamp.
- [ ] Catch transport, JSON, and schema errors into `unavailable` without exposing response bodies or secrets.
- [ ] Re-run the Task 1 tool tests and confirm they pass.

### Task 3: Connect the tool and repair the necessary valuation input contract

**Files:**
- Modify: `src/stockcrewai/main.py`
- Modify: `src/stockcrewai/tools/valuation_tool.py`

**Interfaces:**
- Extend `run_research()` with an optional `market_price_tool` injection point while preserving `market_price_data` as an explicit override.
- When validation is valid and no override is supplied, call the injected/default market tool with the resolved ticker and pass its serialized fields to `_market_price_kwargs()`.

- [ ] Normalize `USD_per_share` into the existing currency-per-share check.
- [ ] Carry the source currency unit from the validated OCF/Capex facts into the derived `current_fcf` valuation fact.
- [ ] Ensure available valuation calculations do not warn merely because market price is not a financial Evidence input.
- [ ] Run the focused integration and valuation tests.

### Task 4: Verify the full uv path

**Files:**
- No additional files.

- [ ] Run `UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run python -m unittest discover -s tests -q`.
- [ ] Run `UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run python -m compileall -q src tests`.
- [ ] Run `git diff --check`.
- [ ] Run one explicit live quote check through the new tool; record only status, price presence, timestamp presence, currency, and source host, never credentials.
- [ ] Confirm the final valuation readiness and report the exact remaining blocker if Yahoo is unreachable.
