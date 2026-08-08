# DeepSeek 投资研究 Crew 配置 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 用最小可运行的 CrewAI 骨架替换内容创作模板，建立 3 个目标 Crew、5 个 DeepSeek Agent 和 5 个 Task 配置。

**Architecture:** 每个 Crew 使用 CrewAI 当前 `@CrewBase` + YAML 配置模式。YAML 直接声明 `llm: deepseek/deepseek-v4-flash`，CrewAI 1.15.11 从 `DEEPSEEK_API_KEY` 和可选的 `DEEPSEEK_BASE_URL` 读取连接信息；Flow、SEC、计算和验证模块暂不创建。旧 `content_crew` 和内容生成入口一并删除，避免留下不可用模板。

**Tech Stack:** Python 3.10–3.13, CrewAI 1.15.11, Pydantic v2, YAML, Python 标准库 `unittest`（使用已有环境，不新增依赖）。

## Global Constraints

- 项目必须使用 Python `>=3.10,<3.14`。
- V1 只允许 5 个 LLM Agent 和 3 个 Crew，不添加 Planner、Research、Validator、Manager 或自由自治 Agent。
- LLM 负责理解和解释；SEC、财务计算、Evidence/Claim 验证和评级仍属于后续确定性模块。
- 所有 Agent 使用 `deepseek/deepseek-v4-flash`；API Key 只从 `DEEPSEEK_API_KEY` 读取。
- `DEEPSEEK_BASE_URL` 使用 `https://api.deepseek.com/v1`；不再使用无效的小写 `base_url` 环境变量。
- 测试默认不发起真实 DeepSeek、SEC 或市场数据请求。
- 不创建新依赖、不创建新的 Conda 环境、不执行 `uv sync`。
- 删除 `src/stockcrewai/crews/content_crew/` 及其旧入口引用；不保留兼容空壳。
- 不自动提交 Git commit；保留用户现有 `.env` 中的 API Key 值，不在输出中显示。

---

### Task 1: Add the offline Crew configuration contract test

**Files:**
- Create: `tests/test_crew_configuration.py`

**Interfaces:**
- Consumes: the future `RequestParserCrew`, `AnalysisCrew`, and `ReportCrew` classes.
- Produces: a runnable test contract that checks Crew counts, task binding, provider, model, and environment-sourced API key without making an LLM call.

- [x] **Step 1: Write the failing test**

Create `tests/test_crew_configuration.py` with this test code:

~~~
from stockcrewai.crews.analysis.crew import AnalysisCrew
from stockcrewai.crews.report.crew import ReportCrew
from stockcrewai.crews.request_parser.crew import RequestParserCrew


def _set_deepseek_environment(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-deepseek-key")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")


def _assert_deepseek_agent(agent):
    assert agent.llm.provider == "deepseek"
    assert agent.llm.model == "deepseek-v4-flash"
    assert agent.llm.api_key == "test-deepseek-key"


def test_request_parser_crew_has_one_deepseek_agent_and_bound_task(monkeypatch):
    _set_deepseek_environment(monkeypatch)

    configured_crew = RequestParserCrew().crew()

    assert len(configured_crew.agents) == 1
    assert len(configured_crew.tasks) == 1
    _assert_deepseek_agent(configured_crew.agents[0])
    assert configured_crew.tasks[0].agent is configured_crew.agents[0]


def test_analysis_crew_has_three_deepseek_agents_and_bound_tasks(monkeypatch):
    _set_deepseek_environment(monkeypatch)

    configured_crew = AnalysisCrew().crew()

    assert len(configured_crew.agents) == 3
    assert len(configured_crew.tasks) == 3
    for agent, task in zip(configured_crew.agents, configured_crew.tasks):
        _assert_deepseek_agent(agent)
        assert task.agent is agent


def test_report_crew_has_one_deepseek_agent_and_bound_task(monkeypatch):
    _set_deepseek_environment(monkeypatch)

    configured_crew = ReportCrew().crew()

    assert len(configured_crew.agents) == 1
    assert len(configured_crew.tasks) == 1
    _assert_deepseek_agent(configured_crew.agents[0])
    assert configured_crew.tasks[0].agent is configured_crew.agents[0]
~~~

- [x] **Step 2: Run the test to verify it fails for the missing implementation**

Run:

~~~
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache /Users/yeziqing/.local/bin/uv run --no-sync python -m unittest discover -s tests -p 'test_*.py' -v
~~~

Expected: collection fails because `stockcrewai.crews.analysis`, `report`, and `request_parser` do not exist yet. Do not change the test to make this failure disappear.

---

### Task 2: Implement the three minimal CrewBase skeletons and YAML contracts

**Files:**
- Create: `src/stockcrewai/crews/__init__.py`
- Create: `src/stockcrewai/crews/request_parser/__init__.py`
- Create: `src/stockcrewai/crews/request_parser/crew.py`
- Create: `src/stockcrewai/crews/request_parser/config/agents.yaml`
- Create: `src/stockcrewai/crews/request_parser/config/tasks.yaml`
- Create: `src/stockcrewai/crews/analysis/__init__.py`
- Create: `src/stockcrewai/crews/analysis/crew.py`
- Create: `src/stockcrewai/crews/analysis/config/agents.yaml`
- Create: `src/stockcrewai/crews/analysis/config/tasks.yaml`
- Create: `src/stockcrewai/crews/report/__init__.py`
- Create: `src/stockcrewai/crews/report/crew.py`
- Create: `src/stockcrewai/crews/report/config/agents.yaml`
- Create: `src/stockcrewai/crews/report/config/tasks.yaml`

**Interfaces:**
- Consumes: the failing test from Task 1 and `DEEPSEEK_API_KEY` / `DEEPSEEK_BASE_URL`.
- Produces: `RequestParserCrew`, `AnalysisCrew`, and `ReportCrew`, each exposing `.crew()` and using `Process.sequential`.

- [x] **Step 1: Add the request parser Crew and configuration**

Use this `crew.py` shape, with the method names exactly matching the YAML keys:

~~~
from crewai import Agent, Crew, Process, Task
from crewai.agents.agent_builder.base_agent import BaseAgent
from crewai.project import CrewBase, agent, crew, task


@CrewBase
class RequestParserCrew:
    agents: list[BaseAgent]
    tasks: list[Task]

    agents_config = "config/agents.yaml"
    tasks_config = "config/tasks.yaml"

    @agent
    def request_parser_agent(self) -> Agent:
        return Agent(
            config=self.agents_config["request_parser_agent"],  # type: ignore[index]
        )

    @task
    def parse_investment_request_task(self) -> Task:
        return Task(
            config=self.tasks_config["parse_investment_request_task"],  # type: ignore[index]
        )

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=False,
        )
~~~

`agents.yaml` must define `request_parser_agent` with `llm: deepseek/deepseek-v4-flash` and `allow_delegation: false`; its role, goal, and backstory must restrict it to parsing company mention, ticker/exchange candidates, request focus, language, and investment horizon. It must leave planning disabled by default.

`tasks.yaml` must define `parse_investment_request_task` assigned to `request_parser_agent`, consume `{request}`, forbid SEC lookup/CIK/financial values/rating, and require a ParsedRequest-shaped JSON object containing `company_mention`, `company_name_guess`, `ticker_guess`, `exchange_guess`, `request_type`, `investment_horizon`, `requested_focus`, `language`, and `confidence`.

- [x] **Step 2: Add the analysis Crew and configuration**

Implement `AnalysisCrew` with three `@agent` methods and three `@task` methods:

~~~
@agent
def financial_quality_agent(self) -> Agent: ...

@agent
def risk_analysis_agent(self) -> Agent: ...

@agent
def valuation_analysis_agent(self) -> Agent: ...

@task
def financial_quality_analysis_task(self) -> Task: ...

@task
def risk_analysis_task(self) -> Task: ...

@task
def valuation_analysis_task(self) -> Task: ...
~~~

Each method must use the matching YAML key and `# type: ignore[index]`. `agents.yaml` must assign all three agents `llm: deepseek/deepseek-v4-flash`, `planning_config: {reasoning_effort: medium, max_attempts: 1}`, and `allow_delegation: false`. The three descriptions must respectively constrain financial-quality interpretation, sourced SEC risk analysis, and interpretation of validated valuation/Reverse DCF results.

`tasks.yaml` must assign each task to the matching agent, consume `{validated_state}`, require Claims with `claim_id`, `category`, `statement`, `evidence_ids`, `calculation_ids`, and `confidence`, and explicitly forbid adding/modifying numbers, using unvalidated data, unsupported web research, calculations, ratings, or buy/sell advice.

- [x] **Step 3: Add the report Crew and configuration**

Implement `ReportCrew` with `report_writer_agent` and `generate_validated_report_task`. The agent must use `deepseek/deepseek-v4-flash`, leave planning disabled by default, and set `allow_delegation: false`.

The task must consume `{validated_claims}`, `{deterministic_verdict}`, `{calculation_results}`, `{source_metadata}`, and `{limitations}`; require Chinese Markdown sections for executive summary, overall rating, financial trend, valuation, Reverse DCF expectations, risks, data/method, limitations, and the non-investment-advice statement; and forbid adding numbers, claims, ratings, or unsourced forecasts. Set `markdown: true` and do not set `output_file`.

- [x] **Step 4: Run the focused test to verify the implementation passes**

Run:

~~~
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache /Users/yeziqing/.local/bin/uv run --no-sync python -m unittest discover -s tests -p 'test_*.py' -v
~~~

Expected: 3 tests pass, with no network request or real API charge.

---

### Task 3: Remove the content template and leave a minimal entrypoint

**Files:**
- Delete: `src/stockcrewai/crews/content_crew/config/agents.yaml`
- Delete: `src/stockcrewai/crews/content_crew/config/tasks.yaml`
- Delete: `src/stockcrewai/crews/content_crew/content_crew.py`
- Delete: `src/stockcrewai/tools/custom_tool.py`
- Delete: `src/stockcrewai/tools/__init__.py`
- Modify: `src/stockcrewai/main.py`
- Modify: `pyproject.toml`
- Modify: `.env`
- Create: `.env.example`
- Modify: `README.md`

**Interfaces:**
- Consumes: the three Crew classes from Task 2.
- Produces: one `kickoff(request: str = ...)` entrypoint for the request parser and no stale content/trigger/plot scripts.

- [x] **Step 1: Replace `main.py` with the request-parser entrypoint**

Use this minimal entrypoint:

~~~
from stockcrewai.crews.request_parser.crew import RequestParserCrew


def kickoff(request: str = "我想知道苹果公司是否值得投资"):
    return RequestParserCrew().crew().kickoff(inputs={"request": request})


if __name__ == "__main__":
    kickoff()
~~~

Remove `ContentState`, `ContentFlow`, `plot`, and `run_with_trigger`; they belong to the deleted template.

- [x] **Step 2: Remove stale script aliases and normalize DeepSeek environment names**

Keep only this script in `pyproject.toml`:

~~~
[project.scripts]
kickoff = "stockcrewai.main:kickoff"
~~~

In `.env`, preserve the existing `DEEPSEEK_API_KEY` value without displaying it, remove unused `MODEL_NAME`, and rename the lowercase `base_url` entry to `DEEPSEEK_BASE_URL=https://api.deepseek.com/v1`. Create `.env.example` with:

~~~
DEEPSEEK_API_KEY=your_deepseek_api_key
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
~~~

- [x] **Step 3: Rewrite the README around the actual skeleton**

Document only installation, environment setup, the three Crew responsibilities, the `kickoff` command, the no-real-API test command, and the explicit boundary that SEC/financial calculation/validation/Flow modules are not implemented in this slice. Remove all `content_crew`, OpenAI, `crewai install`, and placeholder-template references.

- [x] **Step 4: Remove the exact old template directory and confirm no references remain**

After the new `main.py` is in place, remove the exact directories `src/stockcrewai/crews/content_crew/` and `src/stockcrewai/tools/`, including ignored `__pycache__` files if present. Then run:

~~~
rg -n "content_crew|ContentCrew|ContentFlow|custom_tool|MyCustomTool|^planner:|^writer:|^editor:" src README.md pyproject.toml || true
~~~

Expected: no output.

---

### Task 4: Run full local verification and review the diff

**Files:**
- Verify: all files changed by Tasks 1–3.

**Interfaces:**
- Consumes: the complete minimal Crew skeleton.
- Produces: fresh evidence for test, import, syntax, formatting, and stale-template removal.

- [x] **Step 1: Run the complete offline test suite**

Run:

~~~
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache /Users/yeziqing/.local/bin/uv run --no-sync python -m unittest discover -s tests -p 'test_*.py' -v
~~~

Expected: all collected tests pass and no real API request occurs.

- [x] **Step 2: Compile the source and tests**

Run:

~~~
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache /Users/yeziqing/.local/bin/uv run --no-sync python -m compileall -q src tests
~~~

Expected: exit code 0.

- [x] **Step 3: Run repository lint if available without installing anything**

Run:

~~~
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache /Users/yeziqing/.local/bin/uv run --no-sync ruff check src tests
~~~

If Ruff is unavailable in the existing environment, report that exact result and do not install it.

- [x] **Step 4: Check whitespace, stale references, and secret-safe diff**

Run:

~~~
git diff --check
rg -n "content_crew|ContentCrew|ContentFlow|custom_tool|MyCustomTool|^planner:|^writer:|^editor:" src README.md pyproject.toml || true
git status --short
git diff --stat
~~~

Expected: `git diff --check` succeeds, the stale-reference search has no output, and no command output contains the real `DEEPSEEK_API_KEY` value. Do not commit automatically.
