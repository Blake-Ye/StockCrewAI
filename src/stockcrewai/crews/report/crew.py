"""Report Crew 装配与历史兼容导出。

报告上下文、确定性渲染、草稿校验和图表实现分别位于 reporting/*。
本模块只保留 CrewAI Agent/Task/Crew 装配，旧导入路径通过 re-export
继续可用。
"""

from __future__ import annotations

from typing import Any

from crewai import Agent, Crew, LLM, Process, Task
from crewai.agents.agent_builder.base_agent import BaseAgent
from crewai.project import CrewBase, agent, crew, task

from stockcrewai.reporting.context import (
    ReportContext,
    ReportMetric,
    build_report_context,
)
from stockcrewai.reporting.renderer import (
    build_deterministic_report_draft,
    build_narrative_context,
    render_validated_report,
)
from stockcrewai.reporting.validator import (
    REPORT_DRAFT_FIELDS,
    REPORT_ERROR_CODES,
    ReportDraft,
    ReportDraftError,
    parse_report_draft,
    validate_rendered_report,
    validate_report_draft,
    validate_report_output,
)

# CrewAI 1.15 validates guardrail annotations with inspect.signature rather
# than get_type_hints; keep the canonical validator implementation unchanged
# while restoring the runtime return annotation expected by that API.
validate_report_draft.__annotations__["return"] = tuple[bool, Any]


@CrewBase
class ReportCrew:
    """装配只负责叙述草稿的 Report Crew。"""

    agents: list[BaseAgent]
    tasks: list[Task]

    agents_config = "config/agents.yaml"
    tasks_config = "config/tasks.yaml"

    @agent
    def report_writer_agent(self) -> Agent:
        """装配 DeepSeek JSON Object 与本地 ReportDraft guardrail。"""
        config = self.agents_config["report_writer_agent"]  # type: ignore[index]
        return Agent(
            config=config,
            llm=LLM(
                model=config["llm"],  # type: ignore[index]
                response_format={"type": "json_object"},
            ),
        )

    @task
    def generate_validated_report_task(self) -> Task:
        """装配 DeepSeek JSON Object 与本地 ReportDraft guardrail。"""
        return Task(
            config=self.tasks_config["generate_validated_report_task"],  # type: ignore[index]
            guardrail=validate_report_draft,
            guardrail_max_retries=2,
        )

    @crew
    def crew(self) -> Crew:
        """组装报告 Crew，不在此处生成最终 Markdown。"""
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=False,
        )


__all__ = [
    "REPORT_DRAFT_FIELDS",
    "REPORT_ERROR_CODES",
    "ReportContext",
    "ReportCrew",
    "ReportDraft",
    "ReportDraftError",
    "ReportMetric",
    "build_deterministic_report_draft",
    "build_narrative_context",
    "build_report_context",
    "parse_report_draft",
    "render_validated_report",
    "validate_rendered_report",
    "validate_report_draft",
    "validate_report_output",
]
