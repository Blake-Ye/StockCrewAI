import json
from collections.abc import Mapping
from typing import Any

from crewai import Agent, Crew, Process, Task
from crewai import TaskOutput
from crewai.agents.agent_builder.base_agent import BaseAgent
from crewai.project import CrewBase, agent, crew, task
from pydantic import BaseModel, ConfigDict, Field, StrictFloat, ValidationError


PARSER_FIELDS = frozenset(
    {
        "company_mention",
        "company_name_guess",
        "ticker_guess",
        "exchange_guess",
        "request_type",
        "investment_horizon",
        "requested_focus",
        "language",
        "confidence",
    }
)


class ParsedRequest(BaseModel):
    """Request Parser 的固定九字段输出契约。"""

    model_config = ConfigDict(extra="forbid")

    company_mention: str
    company_name_guess: str | None
    ticker_guess: str | None
    exchange_guess: str | None
    request_type: str
    investment_horizon: str | None
    requested_focus: list[str]
    language: str
    confidence: StrictFloat = Field(ge=0, le=1)


def _parser_output_payload(output: TaskOutput) -> Any:
    """提取 Parser 输出的 JSON 候选，不做身份或业务判断。"""
    json_dict = getattr(output, "json_dict", None)
    if isinstance(json_dict, Mapping):
        return json_dict

    pydantic_output = getattr(output, "pydantic", None)
    if hasattr(pydantic_output, "model_dump"):
        return pydantic_output.model_dump()

    raw = getattr(output, "raw", None)
    if not isinstance(raw, str):
        return raw
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def validate_parsed_request_output(output: TaskOutput) -> tuple[bool, Any]:
    """本地校验九字段 Parser 结构，并允许同一 Agent 修正重试。"""
    payload = _parser_output_payload(output)
    if not isinstance(payload, Mapping) or set(payload) != PARSER_FIELDS:
        return (
            False,
            "Parser 输出必须是只包含九个固定字段的 JSON 对象，不得包含额外字段。",
        )
    try:
        ParsedRequest.model_validate(payload)
    except (TypeError, ValidationError) as exc:
        return (
            False,
            "Parser 字段类型或 confidence 范围无效；requested_focus 必须是列表。"
            f"错误：{exc}",
        )
    return True, getattr(output, "raw", payload)


@CrewBase
class RequestParserCrew:
    agents: list[BaseAgent]
    tasks: list[Task]

    agents_config = "config/agents.yaml"
    tasks_config = "config/tasks.yaml"

    @agent
    def request_parser_agent(self) -> Agent:
        """装配请求解析 Agent。

        使用 request_parser/config/agents.yaml 的 Agent 配置，接收调用方在
        Crew kickoff 时提供的投资请求输入，返回 CrewAI Agent；本方法只装配
        Agent 对象，不执行请求解析。
        """
        return Agent(
            config=self.agents_config["request_parser_agent"],  # type: ignore[index]
        )

    @task
    def parse_investment_request_task(self) -> Task:
        """装配投资请求解析 Task。

        使用 request_parser/config/tasks.yaml 的任务配置处理 kickoff 输入，
        返回 CrewAI Task；本方法只装配 Task 对象，不执行请求解析。
        """
        return Task(
            config=self.tasks_config["parse_investment_request_task"],  # type: ignore[index]
            guardrail=validate_parsed_request_output,
            guardrail_max_retries=2,
        )

    @crew
    def crew(self) -> Crew:
        """组装请求解析 Crew。

        使用本类按 YAML 配置装配的 agents 和 tasks，返回按顺序执行且关闭
        verbose 的 CrewAI Crew；本方法只装配 Crew 对象，不启动任务。
        """
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=False,
        )
