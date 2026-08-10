# StockCrewAI Agent + Quant 目标实施计划

> **执行要求：** 实现代理必须使用 `superpowers:subagent-driven-development` 执行本计划；每个工作包开始前使用 `superpowers:test-driven-development`，遇到失败使用 `superpowers:systematic-debugging`，交付前使用 `superpowers:verification-before-completion`。

**目标：** 将当前仅对普通美国经营企业较稳定的研究 Flow，演进为“固定 3 个 Crew、4 个 LLM Agent + 确定性研究/量化内核”的专业美股投研项目；按证券和行业 Profile 输出 `full`、`partial`、`evidence_only` 或 `unsupported_security` 覆盖结果，并提供 point-in-time、可复现的因子和回测证据。

**架构：** CrewAI Flow 只编排事件和分支；LLM Agent 只负责请求解析、已验证事实解释、风险解释和报告叙事；Python 服务负责公司识别、SEC/行情选择、Profile、公式、Claim Gate、Verdict、point-in-time、因子和回测。共享 Pydantic 契约先冻结，巨型共享文件随后拆分，最后并行建设 Agent Eval、量化链路和行业 Profile。

**技术栈：** Python 3.10–3.13、CrewAI 1.15.x、Pydantic、edgartools、yfinance、matplotlib、SQLite（CrewAI Flow 持久化）、pandas、NumPy、DuckDB/Parquet、exchange-calendars，以及 pytest、Hypothesis、pytest-xdist、Ruff、mypy。财务权威值继续使用 `Decimal`，pandas/NumPy 只进入量化统计边界。

---

## 1. 执行清单（机器可读）

```yaml
schema_version: "1.0"
plan_id: stockcrewai-agent-quant-v1
source_architecture: docs/architecture.md
execution_mode: subagent_driven
package_manager: uv
run_prefix: UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync
implementation_agent: luna_coder
implementation_model: Luna Max
review_agent: luna_worker
review_model: Luna Max
checkpoint_policy: stop_after_each_work_package_for_user_review
max_parallel_implementers: 3
max_parallel_reviewers: 2
shared_file_integrator_count: 1
live_network_in_default_tests: false
fallback_allowed: false
new_llm_agents_allowed: false
approved_production_dependencies:
  - "numpy>=2.0,<3"
  - "pandas>=2.2,<3"
  - "duckdb>=1.4,<2"
  - "exchange-calendars>=4.13,<5"
approved_development_dependencies:
  - "pytest>=8,<10"
  - "hypothesis>=6,<7"
  - "pytest-xdist>=3,<4"
  - "ruff>=0.12,<1"
  - "mypy>=1.15,<3"
dependencies_outside_allowlist_require_user_approval: true
dependency_catalog:
  - {name: numpy, spec: "numpy>=2.0,<3", group: production, install_step: WP05-S01, allowed_paths: ["src/stockcrewai/quant", "tests/test_quant_*"]}
  - {name: pandas, spec: "pandas>=2.2,<3", group: production, install_step: WP05-S01, allowed_paths: ["src/stockcrewai/quant", "src/stockcrewai/services/market_data.py", "tests/test_quant_*"]}
  - {name: duckdb, spec: "duckdb>=1.4,<2", group: production, install_step: WP05-S01, allowed_paths: ["src/stockcrewai/quant/storage.py", "tests/test_quant_storage.py"]}
  - {name: exchange-calendars, spec: "exchange-calendars>=4.13,<5", group: production, install_step: WP05-S01, allowed_paths: ["src/stockcrewai/quant/calendar.py", "tests/test_quant_calendar.py"]}
  - {name: pytest, spec: "pytest>=8,<10", group: development, install_step: WP00-S03, allowed_paths: ["tests", "pyproject.toml"]}
  - {name: hypothesis, spec: "hypothesis>=6,<7", group: development, install_step: WP00-S03, allowed_paths: ["tests/test_numeric_properties.py", "tests/test_point_in_time.py", "tests/test_quant_*"]}
  - {name: pytest-xdist, spec: "pytest-xdist>=3,<4", group: development, install_step: WP00-S03, allowed_paths: ["tests", "pyproject.toml"]}
  - {name: ruff, spec: "ruff>=0.12,<1", group: development, install_step: WP00-S03, allowed_paths: ["pyproject.toml"]}
  - {name: mypy, spec: "mypy>=1.15,<3", group: development, install_step: WP00-S03, allowed_paths: ["pyproject.toml"]}
work_packages:
  - {id: WP00, depends_on: [], title: 可信基线与版本门禁}
  - {id: WP01, depends_on: [WP00], title: 共享领域契约}
  - {id: WP02, depends_on: [WP01], title: Profile Registry 与 Metric Policy}
  - {id: WP03, depends_on: [WP02], title: 共享热点文件拆分}
  - {id: WP04, depends_on: [WP03], title: Agent 工具与安全契约}
  - {id: WP05, depends_on: [WP03], title: Point-in-time Snapshot}
  - {id: WP06, depends_on: [WP04], title: Agent Eval 与运行观测}
  - {id: WP07, depends_on: [WP05], title: 因子计算与行业标准化}
  - {id: WP08, depends_on: [WP07], title: Walk-forward 回测}
  - {id: WP09, depends_on: [WP06, WP08], title: QuantResearchPacket 报告接入}
  - {id: WP10, depends_on: [WP02, WP03], title: REIT Profile}
  - {id: WP11, depends_on: [WP10], title: 银行与保险 Profile}
  - {id: WP12, depends_on: [WP11], title: 其他行业与证券结构 Profile}
  - {id: WP13, depends_on: [WP09, WP12], title: 求职发布版}
step_manifest:
  WP00: [WP00-S01, WP00-S02, WP00-S03, WP00-S04, WP00-S05, WP00-S06, WP00-S07, WP00-S08, WP00-S09]
  WP01: [WP01-S01, WP01-S02, WP01-S03, WP01-S04, WP01-S05, WP01-S06, WP01-S07]
  WP02: [WP02-S01, WP02-S02, WP02-S03, WP02-S04, WP02-S05, WP02-S06, WP02-S07]
  WP03: [WP03-S01, WP03-S02, WP03-S03, WP03-S04, WP03-S05, WP03-S06, WP03-S07]
  WP04: [WP04-S01, WP04-S02, WP04-S03, WP04-S04, WP04-S05, WP04-S06, WP04-S07, WP04-S08]
  WP05: [WP05-S01, WP05-S02, WP05-S03, WP05-S04, WP05-S05, WP05-S06, WP05-S07, WP05-S08]
  WP06: [WP06-S01, WP06-S02, WP06-S03, WP06-S04, WP06-S05, WP06-S06]
  WP07: [WP07-S01, WP07-S02, WP07-S03, WP07-S04, WP07-S05, WP07-S06, WP07-S07]
  WP08: [WP08-S01, WP08-S02, WP08-S03, WP08-S04, WP08-S05, WP08-S06, WP08-S07]
  WP09: [WP09-S01, WP09-S02, WP09-S03, WP09-S04, WP09-S05, WP09-S06]
  WP10: [WP10-S01, WP10-S02, WP10-S03, WP10-S04, WP10-S05, WP10-S06]
  WP11: [WP11-S01, WP11-S02, WP11-S03, WP11-S04, WP11-S05, WP11-S06, WP11-S07]
  WP12: [WP12-S01, WP12-S02, WP12-S03, WP12-S04, WP12-S05, WP12-S06, WP12-S07]
  WP13: [WP13-S01, WP13-S02, WP13-S03, WP13-S04, WP13-S05, WP13-S06, WP13-S07]
```

依赖图：

```mermaid
flowchart TD
    WP00 --> WP01 --> WP02 --> WP03
    WP03 --> WP04 --> WP06
    WP03 --> WP05 --> WP07 --> WP08
    WP06 --> WP09
    WP08 --> WP09
    WP02 --> WP10
    WP03 --> WP10
    WP10 --> WP11 --> WP12
    WP09 --> WP13
    WP12 --> WP13
```

## 2. 全局不可违反规则

### 2.1 信任边界

1. LLM 不选择 SEC filing、不解析最终 CIK、不计算财务指标、不决定 Gate 或 Verdict。
2. 所有进入报告的数字必须引用一个已验证 `evidence_id` 或 `calculation_id`。
3. 所有财务金额和会计比率使用 `Decimal`；只在统计边界将收益序列转换为 `float`。
4. 不得把缺失值写成 0，不得用最新值回填历史，不得用 Agent 猜测 Profile。
5. `not_applicable` 不是错误；只有 Policy 标记为 `required + blocking` 的指标缺失或验证失败才可阻断正式报告。
6. `QuantResearchPacket` 第一版只作为报告旁证，不改变 Deterministic Verdict。
7. 禁止 fallback：真实外部依赖失败必须返回 typed error 和稳定 reason code，不伪造数据、不静默降级。

### 2.2 Crew 和 Agent 数量

固定以下结构，不得增加 Agent：

| Crew | Agent | 输入 | 输出 |
|---|---|---|---|
| Request Parser Crew | RequestParserAgent | 原始中文/英文请求 | `ParsedResearchRequest` |
| Analysis Crew | FinancialQualityAgent | 已验证 Evidence、Calculation、Profile、Policy | `AnalysisClaims` 中的财务质量 Claim |
| Analysis Crew | RiskAnalysisAgent | 已验证 filing section、事件、Profile | `AnalysisClaims` 中的风险 Claim |
| Report Crew | ReportWriterAgent | `ReportContext`，只含 accepted Claims 和确定性结果 | `ReportDraft` 叙事字段 |

估值、验证、量化、Gate 和 Verdict 都是 Python 模块，不创建 `ValuationAgent`、`QuantAgent` 或 `ValidatorAgent`。

### 2.3 环境和命令

所有 Python 命令都使用：

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -m unittest discover -s tests -p 'test_*.py' -v
```

禁止执行：

- 手工执行无目标的 `uv sync` 或批量升级全部依赖；
- 创建或替换 `.venv`；
- 添加第 1 节 allowlist 之外的依赖；
- 默认测试中的真实 DeepSeek、SEC、Yahoo 请求；
- 删除用户已有报告、fixture、运行输出或未提交修改。

依赖安装只在所属 Step 执行：

```bash
# WP00：开发工具。uv add 同时更新 pyproject.toml、uv.lock 和当前项目环境。
uv add --dev "pytest>=8,<10" "hypothesis>=6,<7" "pytest-xdist>=3,<4" "ruff>=0.12,<1" "mypy>=1.15,<3"

# WP05：量化生产工具。
uv add "numpy>=2.0,<3" "pandas>=2.2,<3" "duckdb>=1.4,<2" "exchange-calendars>=4.13,<5"
```

不得在其他 Step 重复运行 `uv add`。若 resolver 改变 CrewAI、Pydantic、edgartools、yfinance 或 Python 约束，立即恢复本 Step 对 `pyproject.toml`/`uv.lock` 的修改并上报，不自行放宽版本。

### 2.4 CrewAI 写码前置门禁

任何会修改 Flow、Crew、Agent、Task 或 Tool 的工作包，开始时必须重新执行并记录：

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -c "import crewai; print(crewai.__version__)"
```

同时读取：

- `https://pypi.org/pypi/crewai/json`；
- `https://docs.crewai.com/en/changelog`；
- 与工作包相关的官方 `concepts/flows`、`concepts/agents`、`concepts/tasks` 或 `concepts/tools` 页面。

本计划编写时安装版为 **1.15.11**，官方最新文档为 **1.15.14**。本计划不授权升级；若实施时版本变化，代理必须报告差异并等待用户决定。

### 2.5 开源工具职责和退出条件

| 工具 | 引入 Step | 必须使用的职责 | 验收证据 | 退出条件 |
|---|---|---|---|---|
| pandas | `WP05-S05` | point-in-time 表格、横截面和时间序列对齐 | 与 Decimal golden fixture 一致 | 造成 Decimal 丢失或 Python 3.10 不兼容 |
| NumPy | `WP07-S03` | `float64` 向量统计、排名和相关性 | 与手算/第二实现容差一致 | 被用于 Evidence 财务金额计算 |
| DuckDB | `WP05-S04` | 本地查询 Parquet snapshot | as-of SQL 与 Python 选择结果一致 | 需要联网扩展或无法确定性读取 |
| Parquet | `WP05-S04` | 量化 dataset artifact | schema/hash manifest 可复现 | artifact 不可版本化或丢失精度 |
| exchange-calendars | `WP05-S03` | XNYS/XNAS session 和月末调仓日 | 节假日/半日 fixture 通过 | 日历范围不覆盖目标年份 |
| pytest | `WP00-S03` | 运行现有 unittest 和新增测试 | 收集数量与 unittest 基线一致 | 不适用；它是统一 runner |
| Hypothesis | `WP00-S04` | 公式/Gate/日期性质测试 | 固定 profile 可复现失败例 | 产生无法复现的随机失败 |
| pytest-xdist | `WP00-S05` | 文件隔离的离线测试并行 | 串行/并行结果一致 | 测试共享 artifact 或 live network |
| Ruff | `WP00-S06` | lint 和 import 检查 | 授权文件零新增问题 | `--fix` 会改写范围外文件 |
| mypy | `WP01-S06` | 新增核心模块渐进类型门禁 | 新模块无类型错误 | 第三方缺 stub 时局部、解释性隔离 |

明确不在第一版采用：OpenBB、vectorbt、backtrader、QuantStats、MLflow、Langfuse、DVC、PyArrow、Polars 和 Pandera。原因不是这些工具无价值，而是当前目标已有更小的组合覆盖；若后续出现具体缺口，必须新建独立设计/评测 Step，不能在实现中顺手加入。

官方资料仅作为实施时的 API/兼容性权威来源：

- [pandas 安装与依赖](https://pandas.pydata.org/pandas-docs/stable/getting_started/install.html)
- [NumPy 文档](https://numpy.org/doc/stable/)
- [DuckDB Python API](https://duckdb.org/docs/current/clients/python/overview)
- [DuckDB Parquet](https://duckdb.org/docs/current/data/parquet/overview)
- [exchange-calendars](https://github.com/gerrymanoim/exchange_calendars)
- [pytest](https://docs.pytest.org/en/stable/)
- [Hypothesis](https://hypothesis.readthedocs.io/en/latest/)
- [Ruff](https://docs.astral.sh/ruff/)
- [mypy](https://mypy.readthedocs.io/en/stable/)

### 2.6 每一个 Step 的强制格式

下面工作包中的每个 `WPxx-Syy` 都是独立、可审计的执行单元。子代理不得跳步或合并 Step。每个 Step 必须输出：

```text
step_id
executor
preconditions
read_paths
write_paths
commands_before
red_evidence
operations
artifacts
commands_after
acceptance_evidence
commit_or_no_commit
stop_condition
```

执行顺序固定：

1. 父代理检查 preconditions 和工作树；
2. 实现代理只读取 `read_paths`；
3. 实现代理运行 `commands_before`；
4. 测试 Step 先生成 `red_evidence`；
5. 实现代理执行 `operations`，只写 `write_paths`；
6. 实现代理生成 `artifacts`；
7. 实现代理运行 `commands_after`；
8. 两个只读代理核对 `acceptance_evidence`；
9. 单一集成代理接线；
10. 父代理提交该工作包并执行 `stop_condition`。

除明确写有“独立 commit”的 `WP12-S01`～`WP12-S05` 外，中间 Step 的 `commit_or_no_commit` 均为 `no_commit`；只有该工作包最后一个 Step 可以提交。任何 Step 的命令失败即触发 `stop_condition=report_failure`，不得继续后续 Step 或用 fallback 绕过。

### 2.7 每个工作包的统一完成定义

每个工作包必须依次满足：

1. 读取 `AGENTS.md`、`docs/Expectayion_Projects.md`、`docs/architecture.md`、本文件及任务相关代码和测试；
2. 先写能证明需求的失败测试；
3. 运行目标测试并记录 RED 证据；
4. 写最小实现；
5. 目标测试通过；
6. 相关回归和完整离线测试通过；
7. `compileall` 和 `git diff --check` 通过；
8. 实现代理没有修改独占范围之外的文件；
9. 只读规格审查和质量审查均无阻断项；
10. 单一集成代理完成共享入口接线；
11. 形成独立 commit；
12. 父代理停止，向用户展示 diff、命令、结果和剩余风险，等待批准后才进入下一工作包。

完整离线门禁命令：

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync pytest -q
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -m unittest discover -s tests -p 'test_*.py' -v
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -m compileall -q src tests
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync ruff check src tests
git diff --check
```

---

## 3. 目标目录与模块边界

最终目录使用以下最小结构；没有实际职责的空目录不得创建：

```text
src/stockcrewai/
├── main.py                         # CLI、kickoff、plot；不含业务算法
├── flow.py                         # ResearchFlow、@start/@listen/@router、结构化状态
├── models/
│   ├── request.py                  # ParsedResearchRequest
│   ├── evidence.py                 # Evidence、Calculation、Claim
│   ├── profile.py                  # 三层 Profile、Coverage、ProfileResult
│   ├── policy.py                   # MetricPolicy、PolicyDecision、GateResult
│   └── quant.py                    # Snapshot、Factor、Backtest、Quant packet
├── services/
│   ├── company_resolver.py         # 公司名/ticker/CIK 的确定性解析
│   ├── evidence_store.py           # 当前运行已验证对象的只读索引
│   ├── market_data.py              # 行情记录规范化与验证边界
│   └── runtime_metrics.py          # 延迟、token、重试、失败分类
├── pipelines/
│   ├── evidence_pipeline.py        # SEC/TTM/行情编排
│   ├── profile_registry.py         # 确定性 Profile 分类
│   ├── metric_registry.py          # Profile-aware 指标适用性
│   ├── analysis_pipeline.py        # Analysis Crew 输入与 accepted Claim 汇总
│   └── valuation_pipeline.py       # 估值输入和确定性结果汇总
├── calculations/
│   ├── financial.py                # 通用财务公式
│   └── valuation.py                # 当前估值、历史估值、反向 DCF 公式
├── validators/
│   ├── claim_gate.py               # Claim schema/ID/数字/类别验证
│   ├── analysis_gate.py            # Metric Policy 驱动的分析门禁
│   └── report_gate.py              # 最终报告确定性验证
├── quant/
│   ├── dataset.py                  # 固定股票池数据集构建和命令行入口
│   ├── storage.py                  # DuckDB/Parquet repository
│   ├── calendar.py                 # exchange-calendars 交易日适配
│   ├── point_in_time.py            # 无前视 Snapshot
│   ├── factors.py                  # 版本化因子公式
│   ├── normalization.py            # winsorize、z-score、percentile
│   ├── portfolio.py                # 调仓、权重、交易成本
│   ├── backtest.py                 # walk-forward 主流程
│   ├── statistics.py               # CAGR、波动率、Sharpe、回撤、IC
│   └── packet.py                   # QuantResearchPacket 构建
├── profiles/
│   ├── reit.py                     # REIT 指标和适用性
│   ├── bank.py                     # 银行指标和适用性
│   ├── insurance.py                # 保险指标和适用性
│   ├── utility.py                  # 公用事业指标和适用性
│   ├── commodity_producer.py       # 商品生产商指标和适用性
│   ├── foreign_issuer.py           # ADR、20-F、IFRS 映射
│   ├── holding_company.py          # 控股公司指标和适用性
│   └── spac.py                     # SPAC 证券结构和适用性
├── reporting/
│   ├── context.py                  # canonical ReportContext
│   ├── renderer.py                 # Markdown 确定性拼装
│   ├── validator.py                # 报告数字和禁用内容检查
│   └── visuals.py                  # 三张临时图及自适应布局
├── crews/                          # 固定 3 Crew / 4 Agent
├── tools/                          # BaseTool 薄适配器，不放业务逻辑
└── evals/
    ├── agent_eval.py               # 离线 Agent 输出评测
    └── live_smoke.py               # 显式启动的真实网络冒烟
```

`pipeline_support.py` 在迁移期仅保留兼容 re-export，所有调用迁移完成后再由单独清理任务删除；不得在 WP03 一次性删除。

---

## 4. 核心数据契约

WP01 必须实现以下语义，不得由后续代理各自定义近似版本。

### 4.1 公司身份

`CompanyIdentity` 必须包含：

```text
company_name
ticker
cik
exchange
security_type
source_reference
status = resolved | ambiguous | unsupported | unavailable
reason_code
```

RequestParserAgent 只产生候选值；`services/company_resolver.py` 使用 SEC ticker/CIK 映射和证券元数据确定最终身份。候选冲突或多证券类别无法唯一确定时返回 typed status，不能选择置信度最高的 LLM 猜测。

### 4.2 Profile 与 Coverage

```python
class IssuerProfile(str, Enum):
    STANDARD_OPERATING = "standard_operating"
    BANK = "bank"
    INSURANCE = "insurance"
    REIT = "reit"
    UTILITY = "utility"
    COMMODITY_PRODUCER = "commodity_producer"
    PRE_REVENUE = "pre_revenue"
    HOLDING_COMPANY = "holding_company"
    UNKNOWN = "unknown"

class SecurityProfile(str, Enum):
    COMMON_STOCK = "common_stock"
    MULTI_CLASS = "multi_class"
    ADR = "adr"
    SPAC = "spac"
    RECENT_LISTING = "recent_listing"
    UNSUPPORTED_FUND_SECURITY = "unsupported_fund_security"
    UNKNOWN = "unknown"

class ReportingProfile(str, Enum):
    DOMESTIC_US_GAAP = "domestic_us_gaap"
    FOREIGN_PRIVATE_ISSUER_IFRS = "foreign_private_issuer_ifrs"
    INVESTMENT_COMPANY_REPORTING = "investment_company_reporting"
    UNKNOWN = "unknown"

class CoverageLevel(str, Enum):
    FULL = "full"
    PARTIAL = "partial"
    EVIDENCE_ONLY = "evidence_only"
    UNSUPPORTED_SECURITY = "unsupported_security"
```

`ProfileResult` 必须包含：

```text
issuer_profile
security_profile
reporting_profile
coverage_level
classification_evidence_ids
reason_codes
registry_version
```

### 4.3 Metric Policy

```python
class Applicability(str, Enum):
    REQUIRED = "required"
    OPTIONAL = "optional"
    NOT_APPLICABLE = "not_applicable"

class GateEffect(str, Enum):
    BLOCKING = "blocking"
    NON_BLOCKING = "non_blocking"
```

`MetricPolicy` 必须包含：

```text
metric_id
issuer_profile
security_profile
reporting_profile
applicability
required_evidence
formula_id
period_basis
unit_policy
gate_effect
reason_code
policy_version
```

`PolicyDecision` 必须包含：

```text
metric_id
status = available | unavailable | not_applicable | invalid
evidence_ids
calculation_ids
reason_code
blocking
```

Gate 只能读取 `list[PolicyDecision]`，不得解析 warning、limitations 或 Agent 自然语言。

`GateResult` 必须包含：

```text
status = ready | blocked | evidence_only | unsupported
coverage_level
blocking_decisions
non_blocking_decisions
reason_codes
policy_version
```

### 4.4 市场价格

`MarketPriceRecord` 必须包含：

```text
ticker
price
currency
price_timestamp
source_reference
adjustment_basis = raw | split_adjusted | total_return_adjusted
validation_status
```

`price` 使用 `Decimal`；`price_timestamp` 必须带时区；缺少价格、时间、币种或来源时不能构造 validated record。

### 4.5 Quant

`PointInTimeSnapshot`：

```text
snapshot_id
as_of
cik
ticker
issuer_profile
security_profile
reporting_profile
filing_cutoff
price_cutoff
available_evidence_ids
available_calculation_ids
financial_features
market_features
data_quality
builder_version
```

`FactorObservation`：

```text
factor_id
formula_version
snapshot_id
as_of
ticker
raw_value
normalized_value
peer_group
peer_count
evidence_ids
calculation_ids
status
reason_code
```

`QuantResearchPacket`：

```text
as_of
universe_id
strategy_version
coverage
factor_summary
ranking_summary
backtest_summary
benchmark_summary
data_quality
limitations
artifact_ids
```

`UniverseManifest`：

```text
universe_id
tickers
selection_as_of
membership_source
membership_basis
known_biases
manifest_version
```

第一版固定现存股票池的 `known_biases` 必须包含 `survivorship_bias_known`。

---

## 5. 工作包详细规格

## WP00 — 可信基线与版本门禁

**目标：** 固定当前行为，修复已知确定性误阻断，不引入新架构行为。

**执行方式：** 1 个 `luna_coder` 实现；2 个 `luna_worker` 分别做失败分类和测试审查；本工作包不并行修改共享源文件。

**独占写入：**

- `pyproject.toml`（只增加批准的开发依赖和工具配置）；
- `uv.lock`（由 `uv add` 生成）；
- `tests/test_baseline_contract.py`（新建）；
- `tests/test_numeric_properties.py`（新建）；
- `tests/conftest.py`（新建，Hypothesis profile 和 live marker）；
- `tests/fixtures/baseline/`（新建，纯离线 JSON）；
- `src/stockcrewai/main.py`（单一实现代理，仅限 Profile 传递）；
- `src/stockcrewai/pipeline_support.py`（同一实现代理，仅限 Gate 根因）；
- `src/stockcrewai/crews/report/crew.py`（同一实现代理，仅限建议文本误判）；
- `src/stockcrewai/evals/live_smoke.py`（新建显式 live runner）；
- `tests/test_live_smoke_cli.py`（新建，只测 CLI 和 mock，不联网）；
- `docs/baseline-status.md`（新建）；
- `docs/data-contracts.md`（新建，先记录当前契约，WP01 再冻结目标契约）；
- `docs/numeric-conventions.md`（新建）；
- `docs/error-model.md`（新建）；
- `docs/testing-strategy.md`（新建）。
- `docs/dependency-policy.md`（新建，记录 allowlist、版本、许可证和退出条件）。

**禁止：** allowlist 外依赖、Profile 扩展、量化业务代码、fallback、真实网络进入默认测试。

**逐步操作：**

| Step | 执行者 | 操作与精确文件 | 命令/RED 证据 | 完成证据 |
|---|---|---|---|---|
| `WP00-S01` | 父代理 | 检查 `git status`；读取强制文档、`pyproject.toml` 和现有测试；记录 Python、CrewAI、uv 版本，不写文件 | `git status --short`；`uv --version`；`UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -VV`；CrewAI 版本命令 | 工作树范围明确；版本写入执行记录；发现用户修改时不覆盖 |
| `WP00-S02` | Luna Coder A | 创建 `docs/data-contracts.md`、`docs/numeric-conventions.md`、`docs/error-model.md`、`docs/testing-strategy.md`、`docs/dependency-policy.md` 和 `docs/baseline-status.md`；只描述当前基线和已冻结目标 | 对六个精确路径逐个执行 `test -f`；对对应文件检查 `Decimal`、`reason_code`、`offline`、`live`、`license` | 五类规范均有权威定义、示例和禁止项；不存在未填写占位符 |
| `WP00-S03` | 集成 Luna Coder | 运行批准的开发依赖安装命令；检查 `pyproject.toml`/`uv.lock` 只增加 pytest、Hypothesis、xdist、Ruff、mypy；将解析后的版本、license expression、项目 URL、用途和退出条件写入 `docs/dependency-policy.md`；用 pytest 收集现有 unittest | `uv add --dev "pytest>=8,<10" "hypothesis>=6,<7" "pytest-xdist>=3,<4" "ruff>=0.12,<1" "mypy>=1.15,<3"`；`UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync pytest --collect-only -q`；`UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -m unittest discover -s tests -p 'test_*.py' -v` | 两个 runner 收集同一现有测试集合；每个包的许可证已记录；CrewAI/Pydantic/业务依赖未被升级 |
| `WP00-S04` | Luna Coder B | 创建 `tests/test_numeric_properties.py`；在 `tests/conftest.py` 注册 Hypothesis `ci` profile：`max_examples=200`、`derandomize=True`、`deadline=None`；覆盖 Decimal 公式的零、负数、极值和顺序不变量 | 先引用不存在的 property helper 运行 `pytest -q tests/test_numeric_properties.py` 得到 RED | 失败例可复现；不使用 NaN/Infinity 伪装财务缺失；golden fixture 仍保留 |
| `WP00-S05` | Luna Coder B | 对测试目录做共享文件审计；将只写 `tmp_path` 的测试标记为可并行；live 测试标记 `live` 且默认跳过；本项目并行 worker 固定为 3 | `pytest -q` 与 `pytest -q -n 3` | 串行/并行通过数量和失败集合一致；无测试写根目录正式 artifact |
| `WP00-S06` | Luna Coder C | 在 `pyproject.toml` 增加最小 Ruff 配置；先只检查本工作包授权文件，不执行全仓库 `--fix`；记录旧代码 lint 基线 | `ruff check` 授权文件；故意保留一个临时未使用 import 证明 RED 后删除 | 授权文件 lint 为 0；旧代码问题记录但不顺手批改 |
| `WP00-S07` | Luna Coder A | 在 `tests/test_baseline_contract.py` 和 `tests/fixtures/baseline/` 写 6 类离线回归；运行并保存 TSLA 文本误判、反向 DCF、Profile 传递、外部错误的 RED | `pytest -q tests/test_baseline_contract.py` | 每个已知问题至少有一个修复前失败断言，错误指向具体契约 |
| `WP00-S08` | Luna Coder A | 只修改 `main.py`、`pipeline_support.py`、`crews/report/crew.py` 中被 S07 证明的根因；创建 `evals/live_smoke.py` 与 mock CLI 测试 | 重复 S07；`pytest -q tests/test_live_smoke_cli.py` | S07/S08 全绿；无 fallback；live runner 未被默认测试调用 |
| `WP00-S09` | 父代理 + 两个只读审查 | 连续运行 fixture 5 次，比较数字/Gate/Verdict/artifact hash；运行完整离线门禁；审查 staged diff | `UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync pytest -q`；同命令增加 `-n 3`；`UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -m unittest discover -s tests -p 'test_*.py' -v`；Ruff 授权文件；`git diff --check` | 5 次 hash 一致；审查无 blocker；只提交 WP00 文件，然后停止等待用户 |

**目标命令：**

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync pytest -q tests/test_baseline_contract.py tests/test_numeric_properties.py tests/test_live_smoke_cli.py
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -m unittest discover -s tests -p 'test_*.py' -v
```

**验收：** 所有离线测试通过；相同 fixture 连续 5 次的确定性数字、Gate、Verdict 和 artifact hash 一致；真实错误保留明确 reason code。

**提交：** `test: freeze trustworthy research baseline`

**用户检查点：** 展示 baseline 表和完整测试结果后停止。

## WP01 — 共享领域契约

**目标：** 创建 Profile、Policy、Evidence、Quant 公共 Pydantic 类型，但不改变现有运行行为。

**执行方式：** 3 个实现代理可并行拥有 `profile.py`、`policy.py`、`quant.py`；单一集成代理负责 `__init__.py` 和旧类型适配。

**独占写入：**

- `src/stockcrewai/models/__init__.py`；
- `src/stockcrewai/models/request.py`；
- `src/stockcrewai/models/evidence.py`；
- `src/stockcrewai/models/profile.py`；
- `src/stockcrewai/models/policy.py`；
- `src/stockcrewai/models/quant.py`；
- `tests/test_request_models.py`；
- `tests/test_evidence_models.py`；
- `tests/test_profile_models.py`；
- `tests/test_policy_models.py`；
- `tests/test_quant_models.py`；
- `docs/data-contracts.md`（由集成代理更新为冻结后的目标契约）。

**步骤：**

| Step | 执行者 | 操作与精确文件 | 命令/RED 证据 | 完成证据 |
|---|---|---|---|---|
| `WP01-S01` | Luna Coder A | 先创建 `tests/test_profile_models.py`、`tests/test_policy_models.py`，逐字段测试 Enum、reason code、JSON schema 和非法值 | `pytest -q tests/test_profile_models.py tests/test_policy_models.py` 必须因模块不存在而 RED | RED 只源于待建契约，不源于 fixture 拼写错误 |
| `WP01-S02` | Luna Coder A | 创建 `models/profile.py`、`models/policy.py`，严格实现第 4 节字段；金额/比率拒绝 binary float | 重复 S01 | schema 与第 4 节逐字段一致；`model_dump(mode="json")` 成功 |
| `WP01-S03` | Luna Coder B | 创建 `tests/test_request_models.py`、`tests/test_evidence_models.py`，覆盖公司身份、Evidence、Calculation、Claim、source/as_of/validation | 目标测试先 RED | 所有权威记录有稳定 ID、来源、时间和验证状态 |
| `WP01-S04` | Luna Coder B | 创建 `models/request.py`、`models/evidence.py` 和兼容构造器；旧 dict 可解析，新对象稳定 JSON 化 | 重复 S03 | 旧 fixture 可加载；缺字段返回 Pydantic error，不填默认假数据 |
| `WP01-S05` | Luna Coder C | 创建 `tests/test_quant_models.py` 和 `models/quant.py`；覆盖 Snapshot、Factor、Universe、Packet | 目标测试先 RED 后 GREEN | Decimal/string 与 float 统计字段边界明确；日期带时区 |
| `WP01-S06` | Luna Coder C | 在 `pyproject.toml` 配置 mypy 渐进范围；运行新 `models/`，第三方缺 stub 只能做精确 module override | `mypy src/stockcrewai/models` | 新模型无 mypy error；禁止全局 `ignore_missing_imports = true` |
| `WP01-S07` | 集成 Luna Coder | 创建 `models/__init__.py` 公共导出，更新 `docs/data-contracts.md`；运行完整回归并比较 WP00 hash | 目标测试 + `pytest -q` + hash 比较 | 零行为变化；提交 `feat: add shared research domain contracts` 后停止 |

**目标命令：**

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync pytest -q tests/test_request_models.py tests/test_evidence_models.py tests/test_profile_models.py tests/test_policy_models.py tests/test_quant_models.py
```

**验收：** 契约测试通过；完整回归与 WP00 artifact hash 一致。

**提交：** `feat: add shared research domain contracts`

**用户检查点：** 展示模型字段、JSON 示例和零行为变化证据后停止。

## WP02 — Profile Registry 与 Metric Policy

**目标：** 用确定性 Profile 和统一 Metric Policy 替代“所有公司必须拥有同一组指标”。

**执行方式：** Profile Registry、Metric Registry、Gate 测试由 3 个实现代理并行；单一集成代理接入现有 pipeline。

**独占写入：**

- `src/stockcrewai/services/company_resolver.py`；
- `src/stockcrewai/pipelines/profile_registry.py`；
- `src/stockcrewai/pipelines/metric_registry.py`；
- `src/stockcrewai/validators/analysis_gate.py`；
- `src/stockcrewai/main.py`（仅集成代理传递 Profile/Policy）；
- `src/stockcrewai/pipeline_support.py`（仅集成代理兼容接线）；
- `src/stockcrewai/tools/verdict_tool.py`（仅集成代理改为消费 PolicyDecision）；
- `src/stockcrewai/crews/report/crew.py`（仅集成代理展示 Coverage）；
- `tests/fixtures/profiles/`；
- `tests/test_profile_registry.py`；
- `tests/test_company_resolver.py`；
- `tests/test_metric_registry.py`；
- `tests/test_profile_aware_gate.py`。

**只读依赖：** `models/`、现有 EDGAR 结果结构、`pipeline_support.py` 的 `_analysis_gate`。

**必需接口：**

```python
def resolve_company(
    parsed_request: ParsedResearchRequest,
    sec_candidates: Sequence[CompanyIdentity],
    security_metadata: Mapping[str, Any],
) -> CompanyIdentity: ...

def classify_profiles(source_metadata: Mapping[str, Any]) -> ProfileResult: ...

def resolve_metric_policies(profile: ProfileResult) -> tuple[MetricPolicy, ...]: ...

def evaluate_policy_decisions(
    policies: Sequence[MetricPolicy],
    evidence: Sequence[EvidenceRecord],
    calculations: Sequence[CalculationRecord],
) -> tuple[PolicyDecision, ...]: ...

def evaluate_analysis_gate(
    profile: ProfileResult,
    decisions: Sequence[PolicyDecision],
) -> GateResult: ...
```

**分类输入优先级：** CIK/SEC registrant metadata → SIC → filing forms/taxonomy → ticker/exchange/security metadata。不能调用 LLM。

**第一批行为：**

- `standard_operating`：维持当前完整指标；
- `pre_revenue`：收入增长、P/E 可为 `not_applicable`，现金消耗和 runway 成为适用指标；
- `multi_class`：股价类别与股数口径无法对齐时，市值为 typed unavailable，不阻断证据报告；
- `recent_listing`：五年历史估值为 `not_applicable`；
- 负 EPS：P/E 为 `not_applicable`；
- 负 FCF：FCF yield 可为有效负值，不能因符号为负自动判无效。

**步骤：**

| Step | 执行者 | 操作与精确文件 | 命令/RED 证据 | 完成证据 |
|---|---|---|---|---|
| `WP02-S01` | Luna Coder A | 创建 resolver fixture 和 `tests/test_company_resolver.py`，覆盖 company/ticker/CIK 一致、冲突、多类别、不支持证券 | `pytest -q tests/test_company_resolver.py` 先 RED | 每个输入只有 resolved/ambiguous/unsupported/unavailable 之一 |
| `WP02-S02` | Luna Coder A | 实现 `services/company_resolver.py::resolve_company`；SEC 映射优先于 LLM 候选 | 重复 S01 | 冲突不靠 confidence 猜测；结果有 source_reference/reason_code |
| `WP02-S03` | Luna Coder B | 创建 6 类 Profile fixture 和 `tests/test_profile_registry.py`；实现 `pipelines/profile_registry.py` | `pytest -q tests/test_profile_registry.py` RED→GREEN | 分类只读取 CIK/SIC/form/taxonomy/security metadata，结果含证据 ID/version |
| `WP02-S04` | Luna Coder C | 创建 `tests/test_metric_registry.py`；实现 `pipelines/metric_registry.py` 的集中 Policy 表 | 目标测试 RED→GREEN | 每个已发布 Profile/metric 有 applicability、formula、period、gate effect、version |
| `WP02-S05` | Luna Coder C | 创建 `tests/test_profile_aware_gate.py`；实现 `validators/analysis_gate.py`，只消费 `PolicyDecision` | 用 limitations 文本诱导 Gate 的 fixture 必须 RED | `not_applicable` 从不 blocking；warning 文本变化不改变 Gate |
| `WP02-S06` | 集成 Luna Coder | 修改现有 pipeline 接线，使估值、分析 Gate、Verdict、Coverage 读取同一 Policy；不得改 Agent prompt | 运行 resolver/profile/policy/gate 四组测试 | 单一 policy_version 贯穿最终状态；普通企业 artifact hash 不变 |
| `WP02-S07` | 父代理 + 审查代理 | 输出 6 类 Profile/Policy/Gate 矩阵，运行完整门禁，审查是否虚假宣称全行业支持 | `pytest -q`；`mypy` 新模块；`ruff check` 授权路径 | 矩阵和测试证据齐全；提交后停止 |

**目标命令：**

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync pytest -q tests/test_company_resolver.py tests/test_profile_registry.py tests/test_metric_registry.py tests/test_profile_aware_gate.py
```

**验收：** `not_applicable` 永不触发 blocking；所有阻断都有 `metric_id + reason_code + policy_version`；普通企业回归不变。

**提交：** `feat: make analysis gates profile aware`

**用户检查点：** 展示 6 种公司的 Profile/Policy/Gate 矩阵后停止。

## WP03 — 共享热点文件拆分

**目标：** 在零行为变化前提下拆分 `main.py`、`pipeline_support.py` 和 Report Crew，为并行开发建立独占边界。

**执行方式：** 3 个实现代理分别创建 Flow、Pipeline/Validator、Reporting 新模块；只有 1 个集成代理可以修改旧热点文件。

**独占写入分组：**

- Flow 代理：`src/stockcrewai/flow.py`、`tests/test_flow_module.py`；
- Pipeline 代理：`pipelines/evidence_pipeline.py`、`pipelines/analysis_pipeline.py`、`pipelines/valuation_pipeline.py`、`validators/claim_gate.py`；
- Reporting 代理：`reporting/context.py`、`reporting/renderer.py`、`reporting/validator.py`、`reporting/visuals.py`；
- 集成代理：`main.py`、`pipeline_support.py`、`crews/report/crew.py`、公共入口测试。

**迁移规则：**

1. 先复制纯函数并让新测试直调新模块。
2. 再将旧模块改为薄调用或 re-export；公共函数名和 CLI 不变。
3. `ResearchFlow` 移到 `flow.py`，保留官方 `@persist()`、`@start()`、`@listen()`、`@router()` 语法和 JSON-safe state；运行时对象继续放在 `PrivateAttr`，不得写入 SQLite state。
4. `main.py` 最终只保留 `run_research`、`main`、`kickoff`、`plot`、`cli` 和参数处理。
5. Report Crew 最终只定义 Agent/Task/Crew；Context、Renderer、Validator 不留在 crew.py。
6. 本工作包不改变 Prompt、公式、Gate 规则、报告文本或图形结果。

**逐步操作：**

| Step | 执行者 | 操作与精确文件 | 命令/RED 证据 | 完成证据 |
|---|---|---|---|---|
| `WP03-S01` | 父代理 | 用 WP00 fixture 生成迁移前 JSON/Markdown/图表 hash；记录 `main.py`、`pipeline_support.py`、Report Crew 行数和导入图 | 当前完整测试与 artifact hash | 建立不可变迁移基准；不写业务源码 |
| `WP03-S02` | Luna Coder A | 先建 `tests/test_flow_module.py`；创建 `flow.py` 并移动 `ResearchFlowState`、`ResearchFlow`、stage helpers；保留 `@persist()`/`@start()`/`@listen()`/`@router()` | 新测试先因 `flow.py` 不存在而 RED | Flow state 仅 JSON-safe；PrivateAttr 保留运行对象；路由标签不变 |
| `WP03-S03` | Luna Coder B | 为新 pipeline/validator 写直调测试；移动 Evidence、Analysis、Valuation 和 Claim Gate 纯函数到指定文件 | 新模块测试 RED→GREEN | 新模块不导入 Crew；公式和 reason code 输出 hash 不变 |
| `WP03-S04` | Luna Coder C | 为 reporting context/renderer/validator/visuals 写直调测试；从 Report Crew 移动对应函数 | 新模块测试 RED→GREEN | Report Crew 只剩 Agent/Task/Crew；Markdown 和图表 hash 不变 |
| `WP03-S05` | 集成 Luna Coder | 修改 `main.py` 为 CLI 薄入口；将旧热点改为 re-export/薄调用；修复内部 import | `pytest -q tests/test_main_flow.py tests/test_flow_module.py` | `crewai run` 入口签名不变；循环导入为 0 |
| `WP03-S06` | 集成 Luna Coder | 执行离线 `crewai flow plot`，检查节点和两条 blocked 路由；比较迁移前后 artifacts；确认目标恰为本 Step 生成的 `stockcrewai_flow.html` 后删除 | `UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync crewai flow plot`；`test -f stockcrewai_flow.html`；`shasum -a 256` 比较固定 artifacts；`rm -- stockcrewai_flow.html` | Flow 图包含全部官方事件节点；所有 hash 一致；工作区不遗留临时 HTML |
| `WP03-S07` | 父代理 + 审查代理 | 审查旧热点是否只剩兼容层，运行完整门禁和 import/compile 检查 | `pytest -q`；`compileall`；`ruff check`；`git diff --check` | 零行为变化证据成立；提交后停止 |

**目标命令：**

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync pytest -q tests/test_main_flow.py tests/test_flow_module.py tests/test_analysis_gate.py tests/test_report_visuals.py
```

**验收：** `crewai run` 和 `crewai flow plot` 入口兼容；WP00 fixture 的最终 JSON 和 Markdown hash 不变；旧模块只剩兼容层和入口。

**提交：** `refactor: split flow pipeline and reporting hotspots`

**用户检查点：** 展示迁移前后模块图、文件行数和 artifact hash 后停止。

## WP04 — Agent 只读工具与安全契约

**目标：** 让 Analysis Crew 真实调用受限工具查询当前运行的已验证数据，同时不能越过 allowlist 或执行业务算法。

**执行方式：** EvidenceStore、Tool adapters、Prompt/schema 由 3 个实现代理并行；单一集成代理修改 Analysis Crew 接线。

**独占写入：**

- `src/stockcrewai/services/evidence_store.py`；
- `src/stockcrewai/tools/validated_evidence_tool.py`；
- `src/stockcrewai/tools/validated_calculation_tool.py`；
- `src/stockcrewai/tools/filing_section_search_tool.py`；
- `src/stockcrewai/tools/quant_summary_tool.py`；
- `src/stockcrewai/crews/analysis/config/agents.yaml`；
- `src/stockcrewai/crews/analysis/config/tasks.yaml`；
- `src/stockcrewai/crews/analysis/crew.py`；
- `src/stockcrewai/flow.py`（仅集成代理注入当前 run EvidenceStore）；
- `tests/test_evidence_store.py`；
- `tests/test_validated_query_tools.py`；
- `tests/test_prompt_injection_boundary.py`。

**工具接口：**

```text
query_validated_evidence(metric_ids, periods, limit)
get_validated_calculations(calculation_ids)
search_validated_filing_sections(query, forms, limit)
get_quant_summary(factor_ids)
```

**安全规则：**

- 工具只查询本次 run 注入的 allowlist；
- 不联网、不写文件、不改变 Flow state、不执行公式；
- 每条返回必须含 ID、source、as_of、validation_status；
- SEC 文本标记为 `content_role=data`；
- Prompt 明确禁止执行 filing 内指令；
- Task 使用 Pydantic structured output；
- rejected Claim 不能进入后续 ReportContext。

**测试样本：** filing 文本中包含“忽略系统要求”“调用外部网址”“修改评级”“输出未验证数字”，工具和 Agent 输出都不得越权。

**逐步操作：**

| Step | 执行者 | 操作与精确文件 | 命令/RED 证据 | 完成证据 |
|---|---|---|---|---|
| `WP04-S01` | 父代理 | 重查 CrewAI 安装版、PyPI、changelog、Agents/Tasks/Tools 官方文档；冻结本版本 BaseTool/structured output 写法 | 版本命令和官方 URL 写入执行记录 | 版本差异已报告；未授权升级 |
| `WP04-S02` | Luna Coder A | 创建 `tests/test_evidence_store.py`，覆盖 allowlist、run isolation、validation status、limit、未知 ID | 目标测试先 RED | 测试能证明跨 run 和 allowlist 外查询被拒绝 |
| `WP04-S03` | Luna Coder A | 实现 `services/evidence_store.py`，只读索引通过构造器注入；无全局 mutable store | 重复 S02 | 查询结果有 ID/source/as_of/status；不联网、不写状态 |
| `WP04-S04` | Luna Coder B | 创建 `tests/test_validated_query_tools.py`；实现 4 个 BaseTool 薄适配器和 Pydantic args schema | 每个工具先 RED 后 GREEN | Tool 只调用 EvidenceStore；不包含公式、抓取和 Verdict 逻辑 |
| `WP04-S05` | Luna Coder C | 创建 prompt injection fixture 和 `tests/test_prompt_injection_boundary.py`；加入 filing data envelope | 4 类攻击样本先让缺少边界的实现 RED | `content_role=data`、source/evidence ID 保留；攻击文本不改变任务 |
| `WP04-S06` | Luna Coder C | 修改 Analysis Crew agents/tasks YAML，规定工具、schema、禁止行为和 prompt/schema version；更新 crew.py structured output | 配置测试和 structured output 测试 | 仍为 2 个 Analysis Agent；输出只符合 Pydantic schema |
| `WP04-S07` | 集成 Luna Coder | 将当前 run allowlist 注入 Crew，串起 Tool call→Claim Gate；工具返回不得直接进入 ReportContext | 离线 mock crew 集成测试 | rejected Claim 为 0 入报告；工具越权 reason code 稳定 |
| `WP04-S08` | 父代理 + 审查代理 | 运行全部安全、工具、Analysis 测试；输出每个 Tool 输入/输出及攻击矩阵 | 目标命令 + `pytest -q` + Ruff/mypy 授权路径 | 绕过率 0；提交后停止 |

**目标命令：**

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync pytest -q tests/test_evidence_store.py tests/test_validated_query_tools.py tests/test_prompt_injection_boundary.py tests/test_analysis_structured_output.py
```

**验收：** allowlist 外 ID 命中数为 0；Prompt injection 绕过率为 0；固定 3 Crew/4 Agent；无网络 fixture 可完整测试工具。

**提交：** `feat: add bounded validated evidence tools`

**用户检查点：** 展示每个 Tool 的输入/输出示例和越权测试后停止。

## WP05 — Point-in-time Snapshot

**目标：** 从 SEC Evidence 和行情历史构造任意 `as_of` 时点可复现、无前视的数据快照。

**执行方式：** filing 选择、价格选择、snapshot builder 由 3 个实现代理并行；量化审查代理只读复核前视风险。

**独占写入：**

- `pyproject.toml`（只增加批准的量化生产依赖）；
- `uv.lock`（由 `uv add` 生成）；
- `src/stockcrewai/quant/dataset.py`；
- `src/stockcrewai/quant/storage.py`；
- `src/stockcrewai/quant/calendar.py`；
- `src/stockcrewai/quant/point_in_time.py`；
- `src/stockcrewai/services/market_data.py`；
- `examples/universes/us-large-cap-v1.json`；
- `tests/fixtures/quant/point_in_time/`；
- `tests/test_quant_dataset.py`；
- `tests/test_quant_storage.py`；
- `tests/test_quant_calendar.py`；
- `tests/test_point_in_time.py`。

**必需接口：**

```python
def build_point_in_time_dataset(
    *,
    universe: UniverseManifest,
    rebalance_dates: Sequence[datetime],
    evidence_by_cik: Mapping[str, Sequence[EvidenceRecord]],
    calculations_by_cik: Mapping[str, Sequence[CalculationRecord]],
    prices_by_ticker: Mapping[str, Sequence[MarketPriceRecord]],
    builder_version: str,
) -> tuple[PointInTimeSnapshot, ...]: ...

def build_point_in_time_snapshot(
    *,
    as_of: datetime,
    profile: ProfileResult,
    evidence: Sequence[EvidenceRecord],
    calculations: Sequence[CalculationRecord],
    prices: Sequence[MarketPriceRecord],
    builder_version: str,
) -> PointInTimeSnapshot: ...
```

**硬规则：**

- 只允许 `filed_at <= as_of`；
- 只允许 `price_timestamp <= as_of`；
- 同一指标按 period、filed_at、amendment policy 确定性选择；
- 不将未来重述值写回过去；
- 不前向填充，不用 0 替代缺失；
- split/dividend 采用一个明确复权口径并保存版本；
- snapshot_id 由规范化输入和 builder_version 确定性生成。
- `UniverseManifest` 保存固定 ticker、选择日期、成员来源和 `survivorship_bias_known`；第一版不声称历史成分股无偏。
- SEC 与市场历史采集允许分两次显式执行并落入同一规范化 dataset，便于分别使用可访问的网络线路；任何一步失败都记录 typed error，不自动改用另一来源。

**测试：** 同日多 filing、未来 10-Q、10-K/A 修订、拆股前后、价格缺口、时区边界、重复输入顺序变化。

**逐步操作：**

| Step | 执行者 | 操作与精确文件 | 命令/RED 证据 | 完成证据 |
|---|---|---|---|---|
| `WP05-S01` | 父代理 + 集成 Luna Coder | 用 `uv tree` 记录当前关键版本；执行批准的 4 个生产依赖安装；审查 lock diff 和 Python 3.10–3.13 marker | `uv tree`；`uv add "numpy>=2.0,<3" "pandas>=2.2,<3" "duckdb>=1.4,<2" "exchange-calendars>=4.13,<5"`；`UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -c "import duckdb, exchange_calendars, numpy, pandas; print(duckdb.__version__, exchange_calendars.__version__, numpy.__version__, pandas.__version__)"` | CrewAI/Pydantic/edgartools/yfinance 未被非必要升级；四个包可导入 |
| `WP05-S02` | Luna Coder A | 创建 universe/financial/price fixture 和 `tests/test_quant_dataset.py`；定义 dataset schema、manifest、hash | 目标测试先 RED | fixture 含 source ID、filed_at、price timestamp、known bias |
| `WP05-S03` | Luna Coder B | 创建 `tests/test_quant_calendar.py`；实现 `quant/calendar.py`，使用 XNYS/XNAS calendar 选择 session/月末调仓日 | 节假日、周末、半日、时区 fixture RED→GREEN | 不存在手写美国节假日表；输出日期/时区确定性 |
| `WP05-S04` | Luna Coder C | 创建 `tests/test_quant_storage.py`；实现 `quant/storage.py`，用 DuckDB 写/读 Parquet 和 manifest；禁止联网 extension | as-of SQL、schema、hash、临时目录测试 RED→GREEN | DuckDB 查询和 Python fixture 行数/值一致；artifact 只写 `tmp_path` |
| `WP05-S05` | Luna Coder A | 实现 `quant/dataset.py` 和 `point_in_time.py`；用 pandas 做表格对齐，进入模型前恢复明确类型 | `pytest -q tests/test_quant_dataset.py tests/test_point_in_time.py` | `filed_at/price_timestamp <= as_of`；Decimal 权威值未变成 float |
| `WP05-S06` | Luna Coder B | 用 Hypothesis 增加输入顺序、未来日期、修订、拆股和缺失价格性质测试 | 注入 future filing 的测试必须 RED 于错误实现 | 任意顺序 snapshot hash 一致；未来记录永不被选择 |
| `WP05-S07` | 集成 Luna Coder | 在 dataset CLI 增加 `collect-sec`、`collect-market`、`build` 三个显式子命令；两种网络采集可分开运行，build 只读本地规范化输入 | CLI mock 测试；默认 pytest 中网络调用计数为 0 | SEC/Yahoo 失败各有 typed error；不自动切来源；不覆盖正式报告 |
| `WP05-S08` | 父代理 + 审查代理 | 生成两个 as_of 的离线 snapshot diff、DuckDB schema 和 manifest；运行完整门禁 | WP05 目标测试、pytest、Ruff/mypy 新模块、diff check | 无 look-ahead 证据和依赖审计齐全；提交后停止 |

**目标命令：**

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync pytest -q tests/test_quant_dataset.py tests/test_quant_storage.py tests/test_quant_calendar.py tests/test_point_in_time.py
```

**验收：** 未来 filing/价格无法进入历史 snapshot；输入顺序变化不改变 snapshot hash；每个 feature 可追溯 ID。

**提交：** `feat: build point in time research snapshots`

**用户检查点：** 展示一个日期切换前后的 Apple 或固定虚构 fixture 快照 diff 后停止。

## WP06 — Agent Eval 与运行观测

**目标：** 用离线 fixture 测量 Agent schema、Claim、证据、注入安全和运行成本，而不是只看单次报告是否顺眼。

**执行方式：** Eval runner、runtime metrics、fixture 三个实现代理并行；评测方法审查代理只读。

**独占写入：**

- `src/stockcrewai/evals/agent_eval.py`；
- `src/stockcrewai/services/runtime_metrics.py`；
- `src/stockcrewai/flow.py`（仅集成代理接入事件/评测开关）；
- `tests/fixtures/agent_eval/`；
- `tests/test_agent_eval.py`；
- `tests/test_runtime_metrics.py`。

**指标：**

- RequestParser：ticker/company、期限、语言、focus 准确率；
- FinancialQuality：schema 通过率、Claim 接受率、Evidence 覆盖率、数字幻觉率；
- RiskAnalysis：风险章节召回、无来源风险率、事件状态准确率；
- ReportWriter：章节覆盖、数字一致率、禁止建议命中率、Claim 新增率；
- 全链路：成功率、重试、延迟、token、成本、确定性 hash 一致率。

**发布门槛：** 数字 Evidence 覆盖率 100%；Calculation 复算率 100%；rejected Claim 入报告为 0；同 fixture 5 次确定性 hash 一致；Agent schema 通过率至少 95%；注入绕过率 0。

**逐步操作：**

| Step | 执行者 | 操作与精确文件 | 命令/RED 证据 | 完成证据 |
|---|---|---|---|---|
| `WP06-S01` | Luna Coder A | 为 4 个 Agent 建固定输入、期望 schema、accepted/rejected ID 的 eval fixture | `tests/test_agent_eval.py` 先 RED | 每个 fixture 标注 agent/task/prompt/schema version |
| `WP06-S02` | Luna Coder A | 实现 `evals/agent_eval.py` 的 fixture loader、逐指标 scorer、阈值和 JSON 输出；不调用真实 LLM | 逐 scorer 单测 RED→GREEN | 同输入分数和排序确定；阈值失败退出非零 |
| `WP06-S03` | Luna Coder B | 创建 `tests/test_runtime_metrics.py`；实现 latency/token/retry/cost/failure category event collector | 模拟 CrewAI events 的测试 RED→GREEN | 不记录 prompt secret；事件与 run_id/crew/agent/task 关联 |
| `WP06-S04` | Luna Coder C | 增加 5 次重复运行 evaluator 和 artifact hash scorer；实现 injection bypass、new claim、numeric mismatch 指标 | 错误 fixture 必须命中对应指标 | 指标定义不依赖自然语言 warning；每项有 numerator/denominator |
| `WP06-S05` | 集成 Luna Coder | 接入 Flow 非业务 state 或 event listener；eval 默认读取离线 capture，不改变正常 `crewai run` 输出 | Flow/runtime metrics 集成测试 | 关闭 eval 时零行为变化；开启时生成独立 JSON artifact |
| `WP06-S06` | 父代理 + 审查代理 | 运行完整 eval，生成四 Agent 分数卡和最差 fixture；核对发布门槛 | 目标命令 + `pytest -q` | 门槛逐项 pass/fail；不以平均分掩盖数字/注入硬门禁；提交后停止 |

**目标命令：**

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync pytest -q tests/test_agent_eval.py tests/test_runtime_metrics.py
```

**验收：** 评测结果输出稳定 JSON；门槛失败返回非零退出状态；不要求真实 LLM 才能跑默认评测。

**提交：** `feat: add deterministic agent evaluation harness`

**用户检查点：** 展示各 Agent 分数卡和最差 fixture 后停止。

## WP07 — 因子计算与行业标准化

**目标：** 对 snapshot 计算透明、版本化、Profile-aware 的价值、质量、成长、动量和风险因子。

**执行方式：** 因子、标准化、排名测试由 3 个实现代理并行；单一集成代理输出 observation 集合。

**独占写入：**

- `src/stockcrewai/quant/factors.py`；
- `src/stockcrewai/quant/normalization.py`；
- `src/stockcrewai/quant/ranking.py`；
- `docs/numeric-conventions.md`（仅量化审查代理更新公式版本）；
- `tests/fixtures/quant/factors/`；
- `tests/test_quant_factors.py`；
- `tests/test_quant_normalization.py`；
- `tests/fixtures/quant/ranking/`；
- `tests/test_quant_ranking.py`。

**第一版因子：**

- Value：earnings yield、FCF yield、适用 Profile 的 P/B、证据完整时的 EV/EBITDA；
- Quality：ROE、ROIC、operating margin、FCF margin、cash conversion、debt/equity；
- Growth：3 年 revenue CAGR、EPS growth、FCF growth；
- Market/Risk：12-1 momentum、12 月波动率、beta、最大回撤。

**必需接口：**

```python
def compute_factor_observations(
    snapshots: Sequence[PointInTimeSnapshot],
    formula_version: str,
) -> tuple[FactorObservation, ...]: ...

def normalize_cross_section(
    observations: Sequence[FactorObservation],
    winsor_lower: Decimal,
    winsor_upper: Decimal,
    normalization_version: str,
) -> tuple[FactorObservation, ...]: ...
```

**规则：** 先按 `as_of + Profile + industry` 分组；固定分位 winsorize；行业内 percentile 或 z-score；保存原始值、标准化值和 peer_count；样本不足返回 `insufficient_peer_sample`；不强行跨不同行业比较。

**逐步操作：**

| Step | 执行者 | 操作与精确文件 | 命令/RED 证据 | 完成证据 |
|---|---|---|---|---|
| `WP07-S01` | 父代理 + 量化审查 | 在 `docs/numeric-conventions.md` 冻结每个因子的公式、方向、period、Profile applicability、Decimal→float64 边界和容差 | 文档字段完整性检查 | 公式版本唯一；没有“合理计算”等自由裁量词 |
| `WP07-S02` | Luna Coder A | 创建手算 factor fixture 和 `tests/test_quant_factors.py`；覆盖正、负、零、not_applicable、缺 Evidence | 目标测试先 RED | 每个因子至少一个独立手算值和一个 unavailable 情形 |
| `WP07-S03` | Luna Coder A | 实现 `quant/factors.py`；pandas 仅组织 snapshot，NumPy `float64` 仅用于统计值；保存 provenance | 重复 S02 | raw factor 与手算一致；Evidence/Calculation ID 完整 |
| `WP07-S04` | Luna Coder B | 创建 normalization fixture/测试；实现固定分位 winsorize、z-score/percentile、peer_count | 极端值、常数列、小样本测试 RED→GREEN | 小样本 typed unavailable；无除零/NaN 泄漏 |
| `WP07-S05` | Luna Coder C | 创建 `quant/ranking.py`、ranking fixture 和 `tests/test_quant_ranking.py`；实现版本化 composite score/ranking；同分处理规则固定；输入顺序性质测试用 Hypothesis | 打乱输入和并列分数测试 | 排名对输入顺序不敏感；同分 ticker secondary key 固定 |
| `WP07-S06` | 集成 Luna Coder | 串联 Snapshot→FactorObservation→Normalization→Ranking，写稳定 JSON/Parquet artifact | 10 股票离线 fixture 集成测试 | artifact 包含 raw/normalized/peer/formula version/hash |
| `WP07-S07` | 父代理 + 审查代理 | 第二实现或手算复核全部公式，运行目标/完整门禁 | pytest、mypy quant、Ruff quant | 容差内 100% 一致；提交并展示 10 股票表后停止 |

**目标命令：**

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync pytest -q tests/test_quant_factors.py tests/test_quant_normalization.py
```

**验收：** 公式有独立手算 fixture；输入顺序不改变结果；同一数据重复运行完全一致；不适用因子不阻断其他因子。

**提交：** `feat: add profile aware factor engine`

**用户检查点：** 展示 10 只虚构股票的原始值、行业标准化值和排名后停止。

## WP08 — Walk-forward 回测

**目标：** 用严格时间顺序验证 Quality + Value + Momentum 规则，并明确交易成本和幸存者偏差。

**执行方式：** portfolio、statistics、backtest 三个实现代理并行；量化审查代理复核 look-ahead 和统计定义。

**独占写入：**

- `src/stockcrewai/quant/portfolio.py`；
- `src/stockcrewai/quant/statistics.py`；
- `src/stockcrewai/quant/backtest.py`；
- `docs/numeric-conventions.md`（仅量化审查代理更新回测协议）；
- `docs/testing-strategy.md`（仅量化审查代理更新防前视门禁）；
- `tests/fixtures/quant/backtest/`；
- `tests/test_quant_portfolio.py`；
- `tests/test_quant_statistics.py`；
- `tests/test_quant_backtest.py`。

**协议：** 50–100 只美国普通股、至少 5 年、月度调仓、综合评分前 20% 等权、SPY 与同股票池等权基准、固定双边成本和敏感性分析。

**统计：** CAGR/年化收益、年化波动率、Sharpe、最大回撤、超额收益、IC、换手率、分位数组合收益。

**规则：** 交易信号只能使用调仓日前 snapshot；收益从下一可交易时点开始；费用随换手变化；统计层可把已验证 Decimal 收益转换为 float，并记录转换版本和容差。

**逐步操作：**

| Step | 执行者 | 操作与精确文件 | 命令/RED 证据 | 完成证据 |
|---|---|---|---|---|
| `WP08-S01` | 量化审查代理 | 冻结 rebalance、signal cutoff、execution price、weight、cost、benchmark 和 missing return policy | 更新 numeric/testing 文档并检查字段 | 协议没有可由实现代理调参的空白 |
| `WP08-S02` | Luna Coder A | 创建 portfolio fixture 和 `tests/test_quant_portfolio.py`；实现 top 20% 等权、现金、换手、双边成本 | 费用/换手手算测试 RED→GREEN | 权重和为 1 或明确现金；成本随 turnover 单调增加 |
| `WP08-S03` | Luna Coder B | 创建 statistics fixture 和测试；实现 CAGR、volatility、Sharpe、drawdown、excess、IC、quantile returns | 手算/第二实现 RED→GREEN | 空样本、零波动、短历史都有 typed outcome，无 silent NaN |
| `WP08-S04` | Luna Coder C | 创建 future signal/price fixture；实现 `quant/backtest.py` walk-forward 循环 | 使用同日未来收益的错误实现必须 RED | 每次持仓保存 signal_as_of、trade_date、source snapshot IDs |
| `WP08-S05` | Luna Coder C | 加入 SPY 和股票池等权基准、成本敏感性、survivorship flag | 基准缺失/费用变化测试 | gross/net/benchmark 三条序列日期对齐；偏差显式 |
| `WP08-S06` | 集成 Luna Coder | 生成稳定 backtest JSON/Parquet，禁止 Agent 或 Report 修改统计 | golden fixture hash 测试 | 重复运行 hash 一致；不做参数搜索 |
| `WP08-S07` | 父代理 + 审查代理 | 复核无 look-ahead、统计、成本；运行目标/完整门禁 | pytest、mypy quant、Ruff quant | 所有统计通过双重验证；提交并展示成本敏感性后停止 |

**目标命令：**

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync pytest -q tests/test_quant_portfolio.py tests/test_quant_statistics.py tests/test_quant_backtest.py
```

**验收：** future signal fixture 必须失败；成本升高会降低净收益；所有统计通过手算或第二实现 golden fixture；输出标记 `survivorship_bias_known`。

**提交：** `feat: add walk forward factor backtest`

**用户检查点：** 展示 gross/net/benchmark 曲线数据和成本敏感性表后停止。

## WP09 — QuantResearchPacket 报告接入

**目标：** 将量化结果作为经验证旁证加入报告，不影响现有 Verdict，也不让 LLM改写数字。

**执行方式：** packet builder、报告 context/renderer、图表由 3 个实现代理并行；单一集成代理修改 Flow 接线。

**独占写入：**

- `src/stockcrewai/quant/packet.py`；
- `src/stockcrewai/reporting/context.py`；
- `src/stockcrewai/reporting/renderer.py`；
- `src/stockcrewai/reporting/visuals.py`；
- `src/stockcrewai/flow.py`（仅集成代理）；
- `tests/test_quant_packet.py`；
- `tests/test_quant_report_integration.py`。

**报告新增内容：** 当前因子位置、行业百分位、历史验证摘要、基准比较、最大回撤、换手和成本、数据覆盖与幸存者偏差。图表由 matplotlib 硬编码生成并插入 Markdown，生成报告后只删除临时图片，不删除报告和 JSON artifact。

**规则：** LLM 只解释 `QuantResearchPacket`；所有数值由 renderer 直接插值；Verdict 输入不增加 quant 字段；quant unavailable 时显示 typed status，不生成假图、不把报告伪装为 full coverage。

**逐步操作：**

| Step | 执行者 | 操作与精确文件 | 命令/RED 证据 | 完成证据 |
|---|---|---|---|---|
| `WP09-S01` | Luna Coder A | 创建 `tests/test_quant_packet.py`，定义 coverage/factor/rank/backtest/benchmark/quality/artifact ID 映射 | 目标测试先 RED | Packet 不接受无 provenance 数字；unavailable 是 typed status |
| `WP09-S02` | Luna Coder A | 实现 `quant/packet.py`，只从验证后的量化 artifacts 构建 Pydantic packet | 重复 S01 | packet hash 稳定；不含 Verdict 输入字段 |
| `WP09-S03` | Luna Coder B | 扩展 `reporting/context.py`/renderer 测试；数字直接从 packet 插值，LLM 只接收叙事上下文 | 数字被 LLM 改写的 fixture 必须 RED | Markdown 数字逐项等于 packet；无新增 Claim |
| `WP09-S04` | Luna Coder C | 扩展 visuals：因子百分位、净值/基准、回撤或成本图；用临时目录和自适应轴/标签 | 负值、极端值、中文、长标签图片测试 | 图无裁切/遮挡；报告生成后临时图片删除，artifact 保留 |
| `WP09-S05` | 集成 Luna Coder | 在 `flow.py` 报告前注入可选 packet；对缺 packet 返回 partial/evidence status，不伪造 | `tests/test_quant_report_integration.py` | 接入前后 Verdict hash 相同；无 quant 仍产生契约允许的报告 |
| `WP09-S06` | 父代理 + 审查代理 | 生成离线正式报告并逐项核对 packet、Markdown、图片、Verdict；运行完整门禁 | 目标命令 + pytest/Ruff/mypy/diff | 报告清楚区分事实、分析、量化历史验证和非建议；提交后停止 |

**目标命令：**

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync pytest -q tests/test_quant_packet.py tests/test_quant_report_integration.py tests/test_report_visuals.py
```

**验收：** 报告数字与 packet 逐项一致；Verdict hash 与接入前一致；图表对负值、极端值和中文标签无重叠。

**提交：** `feat: add quant evidence sidecar to reports`

**用户检查点：** 生成并展示一份离线完整报告后停止。

## WP10 — REIT Profile

**目标：** 发布第一个非普通经营企业 Profile，验证架构确实支持行业差异。

**执行方式：** Evidence mapping、Metric Policy、报告/量化适用性由 3 个实现代理并行；Registry 聚合由单一集成代理完成。

**独占写入：**

- `src/stockcrewai/profiles/reit.py`；
- `src/stockcrewai/pipelines/profile_registry.py`（仅集成代理登记 REIT）；
- `src/stockcrewai/reporting/context.py`（仅集成代理增加 REIT 字段）；
- `src/stockcrewai/reporting/renderer.py`（仅集成代理增加 REIT 章节）；
- `docs/data-contracts.md`（REIT 契约）；
- `docs/numeric-conventions.md`（REIT 公式）；
- `tests/fixtures/profiles/reit/`；
- `tests/test_reit_profile.py`；
- `tests/test_reit_report.py`。

**指标：** FFO/AFFO（仅在证据可审计时）、same-store NOI（可选）、occupancy（可选）、net debt/EBITDA、dividend coverage、P/FFO。普通企业 FCF、P/E 不得作为无条件 blocking 指标。

**逐步操作：**

| Step | 执行者 | 操作与精确文件 | 命令/RED 证据 | 完成证据 |
|---|---|---|---|---|
| `WP10-S01` | Profile 审查代理 | 冻结 REIT 分类证据、US-GAAP tags、FFO/AFFO 口径、Metric Policy 和不可用原因 | 更新 data-contracts/numeric 文档；字段检查 | 明确 NAREIT 调整项只在 SEC Evidence 可审计时使用 |
| `WP10-S02` | Luna Coder A | 创建完整 REIT、缺 AFFO、负 FFO、不同物业类型 fixture 和 `test_reit_profile.py` | 目标测试先 RED | fixture 只用固定 SEC-like 数据，不联网 |
| `WP10-S03` | Luna Coder A | 实现 `profiles/reit.py` 的 Evidence mapping、Policy 和公式 adapter | 重复 S02 | 不用普通企业 FCF/P-E 做无条件 blocking |
| `WP10-S04` | Luna Coder B | 创建/实现 REIT Gate 与 registry integration tests；只由集成代理登记 Profile | REIT 分类→Policy→Gate 测试 | 缺 optional disclosure 不阻断；缺 required 项给稳定 reason code |
| `WP10-S05` | Luna Coder C | 创建 `test_reit_report.py`；扩展 ReportContext 和 Quant applicability 展示 FFO/AFFO/P-FFO | 报告口径/来源缺失测试 RED→GREEN | 报告解释术语、期间、来源；不新增未验证数字 |
| `WP10-S06` | 父代理 + 审查代理 | 比较 REIT/standard policy，生成离线样例报告，运行完整门禁 | WP10 目标命令 + 全门禁 | Policy 差异和报告 artifact 可审计；提交后停止 |

**验收：** 至少一个完整 fixture 和一个缺失 AFFO fixture；缺失可选披露不阻断；报告明确口径和来源；量化 P/B/P/FFO 适用性由 Policy 决定。

**目标命令：**

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync pytest -q tests/test_reit_profile.py tests/test_reit_report.py
```

**提交：** `feat: add reit research profile`

**用户检查点：** 展示 REIT 与普通企业 Policy 差异表和一份样例报告后停止。

## WP11 — 银行与保险 Profile

**目标：** 支持无法套用普通企业 FCF/current ratio 的金融机构。

**执行方式：** 银行和保险由 2 个实现代理独占不同文件并行；第三代理写共同金融 fixture 工具；单一集成代理接 Registry。

**独占写入：**

- `src/stockcrewai/profiles/bank.py`；
- `src/stockcrewai/profiles/insurance.py`；
- `src/stockcrewai/pipelines/profile_registry.py`（仅集成代理登记银行/保险）；
- `src/stockcrewai/reporting/context.py`（仅集成代理增加金融 Profile 字段）；
- `src/stockcrewai/reporting/renderer.py`（仅集成代理增加金融 Profile 章节）；
- `docs/data-contracts.md`（银行/保险契约）；
- `docs/numeric-conventions.md`（银行/保险公式）；
- `tests/fixtures/profiles/bank/`；
- `tests/fixtures/profiles/insurance/`；
- `tests/test_bank_profile.py`；
- `tests/test_insurance_profile.py`。

**银行指标：** ROA、ROE、net interest margin、efficiency ratio、CET1（有来源时）、贷款/存款、不良资产和拨备覆盖。普通企业 capex/FCF/current ratio 为 `not_applicable`。

**保险指标：** combined ratio、loss ratio、expense ratio、ROE、book value、investment income、偿付能力披露（有来源时）。普通企业 FCF/current ratio 不作统一 blocking。

**逐步操作：**

| Step | 执行者 | 操作与精确文件 | 命令/RED 证据 | 完成证据 |
|---|---|---|---|---|
| `WP11-S01` | Profile 审查代理 | 分别冻结银行/保险分类证据、会计口径、Metric Policy、不可用原因；禁止共用普通企业模板 | 更新 data-contracts/numeric 文档 | 银行和保险 policy_version 独立；指标定义有来源字段 |
| `WP11-S02` | Luna Coder A | 创建银行完整/缺 CET1/负拨备/无 capex fixture 和 `test_bank_profile.py` | 目标测试先 RED | fixture 覆盖银行没有普通 current assets/capex 的合法结构 |
| `WP11-S03` | Luna Coder A | 实现 `profiles/bank.py` 的 Evidence mapping、Policy 和公式；接入前不改 Registry | 重复 S02 | ROA/NIM/efficiency/loan-deposit 等仅在证据完整时计算 |
| `WP11-S04` | Luna Coder B | 创建保险完整/缺 combined ratio/再保险变化 fixture 和 `test_insurance_profile.py` | 目标测试先 RED | fixture 区分 insurer 与普通金融控股公司 |
| `WP11-S05` | Luna Coder B | 实现 `profiles/insurance.py` 的 Evidence mapping、Policy 和公式 | 重复 S04 | loss/expense/combined ratio 口径一致；缺值 typed unavailable |
| `WP11-S06` | 集成 Luna Coder | 在 Profile Registry 登记银行/保险；扩展 ReportContext/renderer 和量化 applicability | 银行/保险端到端离线测试 | 缺普通企业指标不再阻断；报告不混用两类指标 |
| `WP11-S07` | 父代理 + 审查代理 | 分别生成 PolicyDecision 和报告样例，运行完整门禁 | WP11 目标命令 + 全门禁 | 两类 Profile 独立通过；提交后停止 |

**验收：** 银行和保险都能在缺少 `operating_income/capex/short_term_investments/current_assets/current_liabilities` 时按各自 Policy 继续；不允许用 LLM 自由发明替代公式。

**目标命令：**

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync pytest -q tests/test_bank_profile.py tests/test_insurance_profile.py
```

**提交：** `feat: add bank and insurance profiles`

**用户检查点：** 展示银行、保险各一份 PolicyDecision 和报告样例后停止。

## WP12 — 其他行业与证券结构 Profile

**目标：** 依次补齐 utility、commodity producer、ADR/20-F/IFRS、holding company、SPAC，并明确不支持的基金证券。

**执行方式：** 每轮最多 3 个独占 Profile 实现代理；每个 Profile 必须单独测试和 commit；Registry 仍由单一集成代理写。

**独占写入：**

- `src/stockcrewai/profiles/utility.py`；
- `src/stockcrewai/profiles/commodity_producer.py`；
- `src/stockcrewai/profiles/foreign_issuer.py`；
- `src/stockcrewai/profiles/holding_company.py`；
- `src/stockcrewai/profiles/spac.py`；
- `src/stockcrewai/pipelines/profile_registry.py`（每个子 Profile 完成后仅由集成代理登记）；
- `src/stockcrewai/reporting/context.py`（仅集成代理增加已发布 Profile 字段）；
- `src/stockcrewai/reporting/renderer.py`（仅集成代理增加已发布 Profile 章节）；
- `docs/data-contracts.md`（每个子 Profile 独立更新）；
- `docs/numeric-conventions.md`（每个子 Profile 独立更新）；
- `tests/fixtures/profiles/utility/`；
- `tests/fixtures/profiles/commodity_producer/`；
- `tests/fixtures/profiles/foreign_issuer/`；
- `tests/fixtures/profiles/holding_company/`；
- `tests/fixtures/profiles/spac/`；
- `tests/test_utility_profile.py`；
- `tests/test_commodity_producer_profile.py`；
- `tests/test_foreign_issuer_profile.py`；
- `tests/test_holding_company_profile.py`；
- `tests/test_spac_profile.py`。

**顺序：** utility → commodity producer → ADR/20-F/IFRS → holding company → SPAC。每个 Profile 完成后都执行完整门禁并让用户检查，不一次性交付。

**证券边界：** ETF、共同基金、封闭式基金和其他 `investment_company_reporting` 默认返回 `unsupported_security`；不生成看似专业但口径错误的普通股票报告。

**逐步操作：**

| Step | 执行者 | 操作与精确文件 | 命令/RED 证据 | 完成证据 |
|---|---|---|---|---|
| `WP12-S01` | Luna Coder A | utility：先建完整/监管资本/缺 rate-base fixture，再实现 `profiles/utility.py`、Policy、Gate、报告和量化适用性 | `pytest -q tests/test_utility_profile.py` RED→GREEN | 监管资产、资本开支和利息覆盖口径明确；独立 commit，停止验收 |
| `WP12-S02` | Luna Coder B | commodity producer：先建价格周期/储量/减值 fixture，再实现 `commodity_producer.py` 全链路 | `pytest -q tests/test_commodity_producer_profile.py` RED→GREEN | 区分产量、实现价格、储量和普通收入增长；独立 commit，停止验收 |
| `WP12-S03` | Luna Coder C | foreign issuer：先建 ADR ratio、20-F、6-K、IFRS taxonomy fixture，再实现 `foreign_issuer.py` | `pytest -q tests/test_foreign_issuer_profile.py` RED→GREEN | ADR 股价/股数口径匹配；20-F/IFRS 不伪装 US-GAAP；独立 commit，停止验收 |
| `WP12-S04` | Luna Coder A | holding company：建立 NAV/子公司/双重计算 fixture，实现 `holding_company.py` | `pytest -q tests/test_holding_company_profile.py` RED→GREEN | 避免对子公司和母公司价值重复计算；独立 commit，停止验收 |
| `WP12-S05` | Luna Coder B | SPAC：建立 pre/post-merger、trust cash、warrant dilution fixture，实现 `spac.py` | `pytest -q tests/test_spac_profile.py` RED→GREEN | pre-merger 不输出普通经营分析；稀释来源可追溯；独立 commit，停止验收 |
| `WP12-S06` | 集成 Luna Coder | 增加 investment company/ETF/fund fixture，Registry 返回 `unsupported_security`；不得进入 Analysis Crew | 对不支持证券运行离线 Flow | 返回结构化原因和原始身份信息，不生成普通股票报告 |
| `WP12-S07` | 父代理 + 审查代理 | 每个子 Profile 单独执行目标测试、全门禁、报告样例和用户检查；最后检查 Registry 无顺序依赖 | 全部 WP12 命令 + `pytest -q` | 五个独立 commit/验收证据；最终聚合 commit 后停止 |

**验收：** 每个 Profile 有完整 fixture、缺失数据 fixture、Gate 测试、报告样例、量化适用性；未知 Profile 返回 `evidence_only` 或明确 unsupported，不猜测。

**目标命令：**

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync pytest -q tests/test_utility_profile.py
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync pytest -q tests/test_commodity_producer_profile.py
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync pytest -q tests/test_foreign_issuer_profile.py
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync pytest -q tests/test_holding_company_profile.py
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync pytest -q tests/test_spac_profile.py
```

**提交：** 每个 Profile 一个 commit，例如 `feat: add utility research profile`。

**用户检查点：** 每个 Profile 独立停止验收。

## WP13 — 求职发布版

**目标：** 让面试官在五分钟内看懂架构、复现离线演示并评估 Agent/量化工程质量。

**执行方式：** README/演示、CI/复现、样例 artifact 由 3 个实现代理并行；最终只读审查检查安全和可复现性。

**独占写入：**

- `README.md`；
- `.github/workflows/test.yml`；
- `docs/data-contracts.md`；
- `docs/numeric-conventions.md`；
- `docs/error-model.md`；
- `docs/testing-strategy.md`；
- `docs/demo-script.md`；
- `examples/reports/`；
- `examples/quant/`；
- `examples/coverage-matrix.md`；
- `src/stockcrewai/pipeline_support.py`（只由最终集成代理清理兼容层）。

**交付物：**

1. 三份代表性报告：普通企业、REIT、金融机构；
2. 一份量化研究报告；
3. 30–50 家公司覆盖矩阵，区分代码错误和外部网络错误；
4. 架构图、数据契约、错误模型、测试策略和精确复现命令；
5. 2–3 分钟演示脚本；
6. 简历项目描述和面试问答；
7. GitHub Actions 只运行离线测试，不读取生产 API key。
8. 使用 `rg` 确认仓库内部已无兼容函数调用后，最终集成代理删除 `pipeline_support.py` 中无调用的 re-export；仍有调用则保留并记录，不为追求目录美观破坏兼容性。

**逐步操作：**

| Step | 执行者 | 操作与精确文件 | 命令/RED 证据 | 完成证据 |
|---|---|---|---|---|
| `WP13-S01` | Luna Coder A | 重写 README：5 分钟定位、3 Crew/4 Agent、数据流、coverage、离线 demo、真实运行、artifact 路径和失败解释 | 按 README 从干净临时目录执行文档命令 | 命令可复制；无个人绝对路径；新用户能找到报告和 JSON |
| `WP13-S02` | Luna Coder B | 创建 GitHub Actions：Python 支持矩阵、uv lock、pytest、Ruff、mypy 新模块；不注入生产 secret | 本地解析 YAML；必要时用 `gh workflow` 只读检查 | CI 只跑离线 fixture；并行结果稳定；缓存不包含 `.env` |
| `WP13-S03` | Luna Coder C | 用固定 fixture 生成普通企业、REIT、金融机构三份报告和一份 quant 报告；复制最终 artifact，不提交临时图 | 重建后比较 hash | 样例包含 source/as_of/coverage/limitations；数字可追溯 |
| `WP13-S04` | Luna Coder C | 执行 30–50 公司显式 live coverage runner；输出 `examples/coverage-matrix.md`，分开代码、SEC、Yahoo、LLM、Profile 原因 | live 命令单独执行；失败不改默认测试 | 每家公司有最终 stage/profile/coverage/reason_code；网络失败不算代码通过 |
| `WP13-S05` | Luna Coder A | 创建 `docs/demo-script.md`、简历项目描述和面试问题；演示 2–3 分钟内展示成功与 typed failure | 按秒演练脚本 | 不宣称无偏 Alpha 或所有证券 full coverage |
| `WP13-S06` | 集成 Luna Coder | 用 `rg` 检查旧 compatibility caller；仅在零 caller 时清理 `pipeline_support.py` re-export；扫描 secret、trace code、临时图、个人路径 | `rg` caller/secret/path 检查；`git status` | 不误删兼容入口；仓库无 secret 和运行垃圾 |
| `WP13-S07` | 父代理 + 两个审查代理 | 从新用户视角复现 README、运行完整 CI 等价门禁、核对所有最终 checklist | pytest/unittest/Ruff/mypy/compileall/diff check | 所有 checklist 有命令或 artifact 证据；提交发布版并停止 |

**验收：** 新用户五分钟内能理解 3 Crew/4 Agent 与确定性内核边界；配置合法环境后可运行 `crewai run`；无网络时离线演示仍可复现；仓库不包含 `.env`、trace secret、临时图或个人路径。

**目标命令：**

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -m unittest discover -s tests -p 'test_*.py' -v
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync pytest -q
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -m compileall -q src tests
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync ruff check src tests
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync mypy src/stockcrewai/models src/stockcrewai/services src/stockcrewai/pipelines src/stockcrewai/validators src/stockcrewai/quant src/stockcrewai/reporting
git diff --check
```

**提交：** `docs: publish reproducible agent quant portfolio project`

**用户检查点：** 展示最终 README、CI、样例报告和覆盖矩阵后停止。

---

## 6. 多 Luna Max 分工协议

### 6.1 父代理职责

父代理只负责：

- 冻结接口、验收条件和独占文件；
- 判断根因是否与架构一致；
- 分派 Luna Max；
- 审查子代理证据和最终 diff；
- 由单一集成代理接线后运行总门禁；
- 每个工作包结束向用户汇报并停止。

父代理不承担常规代码编写；只有 Luna Max 运行时不可用且用户明确同意改变执行方式时，才可调整。

### 6.2 子代理类型

| 类型 | 自定义代理 | 写权限 | 数量 | 用途 |
|---|---|---:|---:|---|
| 实现 | `luna_coder` / Luna Max | 独占路径 | 最多 3 | RED→GREEN→REFACTOR |
| 规格审查 | `luna_worker` / Luna Max | 只读 | 1 | 检查是否完成需求、是否越界 |
| 质量审查 | `luna_worker` / Luna Max | 只读 | 1 | 检查根因、类型、测试、复杂度 |
| 集成 | `luna_coder` / Luna Max | 共享入口 | 恰好 1 | 修改 main/flow/registry 公共接线 |

Luna Max 不可用、线程配额满或模型名不可调度时必须报告阻塞，不能静默替换模型。

项目文档中的“不要创建子代理”约束适用于被分派的实现代理：`luna_coder` 和 `luna_worker` 不得继续创建孙代理。只有直接面向用户的父代理可以按本计划创建有界、互斥的 Luna Max 子任务。

### 6.3 实现 Prompt 固定模板

```text
你是 StockCrewAI 的 Luna Max 实现子代理。仓库中还有其他代理工作；不要回退、覆盖或格式化他人的修改。

工作包：使用本计划中的精确 WP 编号和标题。
单一目标：复制该工作包的“目标”。
独占写入：复制该工作包分配给你的精确路径。
只读依赖：列出所需现有模块和测试。
禁止修改：所有未列入独占写入的路径；共享入口由集成代理负责。

开始前完整阅读 AGENTS.md、docs/Expectayion_Projects.md、docs/architecture.md、
docs/implementation-plan.md、相关源码和测试。若修改 CrewAI 代码，执行版本和官方文档前置门禁。

使用 superpowers:using-superpowers、test-driven-development、systematic-debugging、
verification-before-completion 和 ponytail。先给 RED 证据，再做最小实现。

禁止真实 SEC/Yahoo/DeepSeek 进入默认测试；禁止增加 allowlist 外依赖、Agent、fallback、伪造数据、
未验证 ID 和二进制浮点财务计算。若需要改独占范围外文件，立即停止并上报接口缺口。

完成时返回：根因/设计、修改文件、RED 命令与结果、GREEN 命令与结果、完整回归结果、
git diff --check 结果、剩余风险。不要自行修改共享入口，不要自行进入下一工作包。
```

### 6.4 审查 Prompt 固定模板

```text
你是 StockCrewAI 只读 Luna Max 审查代理，不修改任何文件。
对照 docs/architecture.md、docs/implementation-plan.md 中指定 WP 和实现 diff 审查。

规格审查：逐条核对目标、接口、信任边界、文件所有权和验收条件。
质量审查：检查根因、类型、Decimal、reason code、测试隔离、无 fallback、无过度设计。

按 blocker/high/medium/low 输出 findings，每项包含精确文件和行号。
没有阻断项时明确写“无阻断项”。不要用个人风格偏好代替正确性问题。
```

### 6.5 集成 Prompt 固定模板

```text
你是唯一允许修改本工作包共享入口的 Luna Max 集成代理。
先确认所有实现子任务目标测试通过，且两个只读审查均无 blocker。

只做接线和兼容层，不重写已通过审查的业务模块，不增加 fallback。
运行该工作包目标测试、完整离线测试、compileall、git diff --check。
如果接线需要改变已冻结接口，停止并报告，不自行重定义契约。
```

---

## 7. 最高效率执行架构

### 7.1 关键路径

`WP00 → WP01 → WP02 → WP03` 必须串行。这四步分别冻结当前行为、公共类型、Profile/Policy 和文件边界；过早并发会让多个代理同时修改 `main.py`、`pipeline_support.py` 和 Report Crew，冲突成本高于编码收益。

### 7.2 第一并行窗口

WP03 通过后，可以并行推进：

- Agent 线：`WP04 → WP06`；
- Quant 线：`WP05 → WP07 → WP08`；
- Profile 线：`WP10 → WP11 → WP12`。

三条线只共享 WP01/WP02 契约。任何共享契约变更必须先暂停全部实现代理，由父代理发起版本化变更。

### 7.3 第二并行窗口

WP09 内可同时建设 packet、report context 和 visuals，但 `flow.py` 只由单一集成代理修改。WP13 内 README、CI 和样例 artifact 可并行。

### 7.4 推荐节奏

每个工作包采用：

```text
父代理冻结接口（15–30 分钟）
  → 2–3 个 Luna Max 独占实现（并行）
  → 2 个 Luna Max 只读审查（并行）
  → 原实现代理修正
  → 1 个 Luna Max 集成
  → 父代理运行总门禁并审查 diff
  → 停止，用户验收
```

最高有效并发是 3 个实现 + 2 个只读审查 + 1 个父代理。继续增加代理会放大上下文重复、共享文件冲突和测试资源争用。

---

## 8. 里程碑与完成比例

| 里程碑 | 完成工作包 | 可对外表述 |
|---|---|---|
| M0 可信研究 MVP | WP00–WP03 | 普通经营企业的确定性、可追溯研究 Flow |
| M1 Agent 工程版 | WP04、WP06 | 有受限工具、结构化输出、安全边界和 Eval 的 Agent 系统 |
| M2 Quant 验证版 | WP05、WP07–WP09 | 有 point-in-time 因子、walk-forward 和报告旁证 |
| M3 多行业版 | WP10–WP12 | 按 Profile 支持主要美股发行人，不硬套统一指标 |
| M4 求职发布版 | WP13 | 可复现、可演示、可面试验证的 Agent + Quant 项目 |

进度计算按工作包验收，不按代码行数。WP13 完成前不能宣称“任何美股都能生成 full professional report”；正确表述是“对美股证券先分类，再按 coverage level 输出专业、部分、证据或不支持结果”。

---

## 9. 最终验收清单

- [ ] 固定 3 Crew、4 Agent，无新增自治层。
- [ ] `crewai run` 和 `crewai flow plot` 可用。
- [ ] Flow 使用官方 `@start()`、`@listen()`、`@router()` 和结构化 state。
- [ ] 普通企业、REIT、银行、保险及其他已发布 Profile 都有独立 Policy 和 fixture。
- [ ] `not_applicable` 不触发误阻断。
- [ ] 每个报告数字都有 Evidence/Calculation ID。
- [ ] rejected Claim 进入报告数量为 0。
- [ ] 同一离线输入连续 5 次确定性 artifact hash 一致。
- [ ] Prompt injection 绕过率为 0。
- [ ] Point-in-time 测试证明无未来 filing 和未来价格泄漏。
- [ ] 回测含基准、成本、换手、IC、回撤和幸存者偏差声明。
- [ ] Quant 结果不改变 Verdict v1。
- [ ] 默认测试不访问 SEC、Yahoo、DeepSeek 或付费服务。
- [ ] 无 fallback、伪造数据、静默吞错和缺失值填 0。
- [ ] 依赖只包含批准 allowlist；lock diff 无非必要核心升级。
- [ ] README、CI、样例报告和覆盖矩阵可复现。
- [ ] 仓库不包含 `.env`、API key、临时图、个人绝对路径或 trace 访问码。

本计划的执行起点是 **WP00**。在用户明确批准 WP00 前，不开始任何业务代码实现。
