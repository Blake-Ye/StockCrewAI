"""Analysis Crew：把已验证财务和风险数据转换为可审计的 Claims。

本模块中的 Agent 只负责解释上游已经验证过的事实和计算结果，不负责
检索数据、重新计算指标、选择数据来源或生成最终投资评级。估值 Claims
由 ``pipeline_support`` 根据已验证估值结果确定性生成；最终是否允许
所有 Claims 进入 Verdict 和 Report，由 ``main.py`` 中的 Claim Gate 决定。
"""

import json
from collections.abc import Mapping
from typing import Any

from crewai import Agent, Crew, Process, Task
from crewai import TaskOutput
from crewai.agents.agent_builder.base_agent import BaseAgent
from crewai.project import CrewBase, agent, crew, task
from pydantic import BaseModel, ConfigDict, Field, StrictFloat, ValidationError


ANALYSIS_DOMAIN_RULES = {
    "financial": (frozenset({"financial_quality", "financial_trend"}), True),
    "risk": (frozenset({"risk"}), False),
    "valuation": (
        frozenset({"current_valuation", "historical_valuation", "reverse_dcf"}),
        True,
    ),
}


class AnalysisClaim(BaseModel):
    """单条分析 Claim 的固定输出契约。

    ``evidence_ids`` 和 ``calculation_ids`` 是可审计链路，不是 Agent
    自由填写的说明文字：它们必须来自传入的已验证 ID 白名单。额外字段
    被禁止，是为了避免模型自行加入 ``metric``、``value`` 等未经定义
    的字段，导致下游报告无法稳定解析。
    """

    model_config = ConfigDict(extra="forbid")

    claim_id: str
    category: str
    statement: str
    evidence_ids: list[str]
    calculation_ids: list[str]
    confidence: StrictFloat = Field(ge=0, le=1)


class AnalysisTaskOutput(BaseModel):
    """Analysis Task 的顶层输出，只允许包含 Claims 集合。"""

    model_config = ConfigDict(extra="forbid")

    claims: list[AnalysisClaim] = Field(default_factory=list)


def _analysis_output_payload(output: TaskOutput) -> Any:
    """提取 Analysis Task 的结构化候选输出，不执行业务白名单校验。"""
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


def _validate_analysis_output(
    output: TaskOutput,
    *,
    domain: str,
) -> tuple[bool, Any]:
    """按域校验 Analysis 输出结构，并把失败反馈给同一 Agent 重试。

    这里故意只做本地结构校验，不调用第二个 LLM，也不判断 Evidence ID
    或 Calculation ID 是否存在于当前运行的白名单。后者属于主流程 Claim
    Gate 的职责；本 Guardrail 只负责尽早发现字段、类别、空列表规则或
    非 JSON 文本错误，并把明确的修正要求交还给同一个 Agent。
    """
    payload = _analysis_output_payload(output)
    if not (
        isinstance(payload, Mapping)
        and set(payload) == {"claims"}
        and isinstance(payload.get("claims"), list)
    ):
        return (
            False,
            "输出必须是唯一 JSON 对象 {claims: [...]}，不得包含 status、reason、limitations 或解释性文字。",
        )

    try:
        parsed = AnalysisTaskOutput.model_validate(payload)
    except (TypeError, ValidationError) as exc:
        return (
            False,
            "Claim 字段不符合固定契约；每条 Claim 只能包含 claim_id、category、statement、"
            f"evidence_ids、calculation_ids、confidence，且 confidence 必须在 0 到 1 之间。错误：{exc}",
        )

    allowed_categories, requires_calculations = ANALYSIS_DOMAIN_RULES[domain]
    if any(claim.category not in allowed_categories for claim in parsed.claims):
        return (
            False,
            f"{domain} Analysis Agent 的 category 不在允许范围内。",
        )

    if any(not claim.evidence_ids for claim in parsed.claims):
        return (
            False,
            "每条 Claim 都必须提供非空 evidence_ids；只能复制输入中的 ID。",
        )

    if any(
        (requires_calculations and not claim.calculation_ids)
        or (not requires_calculations and claim.calculation_ids)
        for claim in parsed.claims
    ):
        return (
            False,
            "Financial/Valuation Claim 必须提供非空 calculation_ids，Risk Claim 的 calculation_ids 必须为空列表。",
        )

    categories = {claim.category for claim in parsed.claims}
    if parsed.claims and not allowed_categories.issubset(categories):
        return (
            False,
            "有可解释事实时必须覆盖当前域允许的全部 category；没有事实时才输出空 claims 列表。",
        )

    return True, getattr(output, "raw", payload)


def validate_financial_analysis_output(
    output: TaskOutput,
) -> tuple[bool, Any]:
    """校验财务 Analysis 输出的结构、类别和 Calculation 列表规则。"""
    return _validate_analysis_output(output, domain="financial")


def validate_risk_analysis_output(output: TaskOutput) -> tuple[bool, Any]:
    """校验风险 Analysis 输出的结构、风险类别和空 Calculation 列表规则。"""
    return _validate_analysis_output(output, domain="risk")


@CrewBase
class AnalysisCrew:
    agents: list[BaseAgent]
    tasks: list[Task]

    agents_config = "config/agents.yaml"
    tasks_config = "config/tasks.yaml"

    @agent
    def financial_quality_agent(self) -> Agent:
        """创建财务质量分析 Agent。

        Agent 的 role、goal 和 backstory 位于 analysis/config/agents.yaml；
        这里仅负责把 YAML 配置装配成 CrewAI Agent。它接收的业务输入由
        ``main.py`` 的 ``_financial_analysis_input`` 构造，内容应当只包含
        公司身份、已验证财务事实、已验证计算结果及对应 ID 白名单。
        """

        return Agent(
            config=self.agents_config["financial_quality_agent"],  # type: ignore[index]
        )

    @agent
    def risk_analysis_agent(self) -> Agent:
        """装配风险分析 Agent。

        使用 analysis/config/agents.yaml 的风险分析 Agent 配置，接收调用方
        在 Crew kickoff 时提供的分析输入，返回 CrewAI Agent；本方法只装配
        Agent 对象，不执行风险分析。
        """
        return Agent(
            config=self.agents_config["risk_analysis_agent"],  # type: ignore[index]
        )

    @task
    def financial_quality_analysis_task(self) -> Task:
        """创建财务质量分析 Task。

        Task 的详细指令位于 analysis/config/tasks.yaml。该 Task 的职责是
        将财务事实解释成 ``financial_quality`` 或 ``financial_trend``
        Claim；它不负责判断数据是否真实有效，也不负责生成 Verdict。
        Task 完成后，``main.py`` 会再次使用本模块的 Pydantic 契约和
        已验证 ID 白名单进行确定性校验。
        """

        return Task(
            config=self.tasks_config["financial_quality_analysis_task"],  # type: ignore[index]
            # 本地 Guardrail 只校验结构并给同一 Agent 修正机会；最终的
            # Evidence/Calculation 白名单校验仍由 main.py 的 Claim Gate 执行。
            guardrail=validate_financial_analysis_output,
            guardrail_max_retries=2,
        )

    @task
    def risk_analysis_task(self) -> Task:
        """装配风险分析 Task。

        使用 analysis/config/tasks.yaml 的风险分析任务配置处理 kickoff 输入，
        返回 CrewAI Task；本方法只装配 Task 对象，不执行风险分析。
        """
        return Task(
            config=self.tasks_config["risk_analysis_task"],  # type: ignore[index]
            guardrail=validate_risk_analysis_output,
            guardrail_max_retries=2,
        )

    @crew
    def crew(self) -> Crew:
        """组装分析 Crew。

        使用本类按 analysis 配置 YAML 装配的 agents 和 tasks，返回按顺序执行
        且关闭 verbose 的 CrewAI Crew；本方法只装配 Crew 对象，不启动分析。
        """
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=False,
        )
