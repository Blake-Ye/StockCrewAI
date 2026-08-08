# 正式报告导出 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 每次成功的 `crewai run` 在运行摘要和审计 JSON 之外，自动生成经过验证的 Markdown 正式报告。

**Architecture:** 复用 Flow 中已经验证的顶层 `report` 字符串。`kickoff()` 是唯一 OutputWriter：先保持现有 `CompactRunReporter.finalize()` 的 run-output/run-result 双写，再仅对成功结果写出同目录 `investment-report.md`。

**Tech Stack:** Python 标准库 `pathlib`、unittest、uv

## Global Constraints

- 不新增依赖，不修改 `.env`，不运行 `uv sync`。
- 不让 LLM、Crew 或 Task 直接写正式文件。
- 不修改报告模板正文、估值逻辑、Gate 或 Verdict。
- 默认测试离线；保留用户现有 `run-output.md` 和 `run-result.json`。
- 不提交、不推送。

---

### Task 1: 导出经过验证的 Markdown 报告

**Files:**
- Modify: `src/stockcrewai/main.py:1560-1630`
- Modify: `src/stockcrewai/run_output.py:760-850`
- Modify: `tests/test_main_flow.py:MainEntrypointTests`
- Modify: `tests/test_run_and_save_output.py`

**Interfaces:**
- Consumes: `result["status"]`, `result["stage"]`, `result["report"]`, `output_path: Path`
- Produces: `output_path.with_name("investment-report.md")` containing exactly the validated report string and a trailing newline.

- [ ] **Step 1: Write failing tests**

```python
def test_kickoff_exports_validated_report_next_to_run_output():
    result = {"status": "ok", "stage": "report", "report": "# 投资研究报告"}
    kickoff("request", output_path=Path(temp_dir) / "run-output.md")
    assert (Path(temp_dir) / "investment-report.md").read_text() == "# 投资研究报告\n"

def test_kickoff_does_not_export_report_for_blocked_result():
    result = {"status": "blocked", "stage": "analysis", "report": None}
    kickoff("request", output_path=Path(temp_dir) / "run-output.md")
    assert not (Path(temp_dir) / "investment-report.md").exists()
```

- [ ] **Step 2: Run failing tests**

Run: `UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -m unittest tests.test_main_flow.MainEntrypointTests tests.test_run_and_save_output -v`

Expected: export assertions fail because no report file exists.

- [ ] **Step 3: Implement minimal OutputWriter logic**

```python
report_path = output_path.with_name("investment-report.md")
if result.get("status") == "ok" and result.get("stage") == "report":
    report = result.get("report")
    if isinstance(report, str) and report.strip():
        report_path.write_text(report.rstrip() + "\n", encoding="utf-8")
```

Keep it after `finalize()` and do not add an `output_file` field to CrewAI Task. Add a concise path note to the run summary only when this write succeeds.

- [ ] **Step 4: Run focused tests**

Run the Step 2 command again. Expected: PASS.

- [ ] **Step 5: Run integration checks**

Run:

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -m unittest discover -s tests -p 'test_*.py'
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -m compileall -q src tests
git diff --check
```

Expected: all pass. Do not commit.
