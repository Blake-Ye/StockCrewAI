# AnalysisCrew Hard-Gate Design

## Decision

AnalysisCrew is a hard quality gate. If financial, risk, or valuation analysis
does not produce the required validated Claims, the run stops before Verdict
and ReportCrew. The final result is a compact structured `blocked` response
with `report: null` and actionable `required_data` codes. It must not contain
`limitations`, `analysis notice`, or rejected-Claim counts.

## Boundaries

The project remains at five LLM agents and three CrewAI crews. No new
dependency, persistence layer, SQLite database, or manager/validator agent is
introduced. Python owns preflight checks, Claim validation, gating, and the
decision to call Verdict or ReportCrew.

## Analysis Inputs

`main.py` builds three role-specific payloads:

- `financial_analysis_input`: company identity, validated financial facts,
  validated financial calculations, and their allowlists.
- `risk_analysis_input`: company identity, only extracted SEC risk/event
  sections with filing provenance, and the filing Evidence-ID allowlist.
- `valuation_analysis_input`: current valuation, historical valuation,
  reverse DCF, price provenance, and their allowlists.

No agent receives the shared `validated_state`, raw filing list, or another
domain's payload. Task prompts explicitly prohibit using prior task output.

## SEC Risk Sections

EDGAR filing evidence gains structured `risk_sections`. The tool extracts:

- 10-K `Item 1A. Risk Factors`;
- 10-Q `Part II, Item 1A. Risk Factors` or the equivalent Item 1A section;
- complete, non-truncated 8-K event text when available.

Sections retain the parent filing Evidence ID and provenance. A truncated
filing is never treated as a complete risk section. The risk preflight requires
at least one extracted section; otherwise analysis is blocked with
`risk_sections_required`.

## Analysis Output Contract

Each analysis task returns only `{"claims": [...]}`. Agent-produced
`status`, `reason`, `limitations`, and `warnings` are removed.

Claim validation is domain-aware:

- financial claims require nonempty allowlisted Evidence and Calculation IDs;
- risk claims require nonempty allowlisted filing Evidence IDs and no
  Calculation IDs;
- valuation claims require nonempty allowlisted Evidence and Calculation IDs.

The required categories are:

- financial: `financial_quality` and `financial_trend`;
- risk: `risk`;
- valuation: `current_valuation`, `historical_valuation`, and `reverse_dcf`.

The gate rejects malformed output, out-of-domain categories, empty IDs, and
claims that fail their allowlist. Rejection detail is internal only and never
reaches the report input or final result.

## Gate and Downstream Behavior

The deterministic preflight runs before `AnalysisCrew` and requires valid
financial validation, extracted risk sections, and valid current/historical/
reverse valuation results. If it fails, no AnalysisCrew, Verdict, or ReportCrew
is called.

After AnalysisCrew, the deterministic gate requires the complete category set
above. If it fails, Verdict and ReportCrew are not called. The return value is:

```json
{
  "status": "blocked",
  "stage": "analysis",
  "report": null,
  "required_data": ["risk_sections_required"],
  "next_action": "补齐 required_data 后重新运行"
}
```

Only a passed gate can call DeterministicVerdictTool and ReportCrew. The report
does not receive a generic `limitations` list from AnalysisCrew. Standard
non-investment disclosure remains unchanged. Methodology notes such as
period-end shares versus weighted diluted shares do not block analysis and do
not become report limitations.
