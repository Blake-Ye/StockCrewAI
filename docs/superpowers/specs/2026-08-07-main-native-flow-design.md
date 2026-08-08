# Main.py 原生 CrewAI Flow 重构设计

## 目标

将 `src/stockcrewai/main.py` 改造成与 CrewAI `Build Your First Flow` 示例相同的
可读结构：在主文件中直接定义 Pydantic State、`Flow[State]`、`@start()`、
`@listen()`、`@router()`、`kickoff()` 和 `plot()`。打开 `main.py` 即可顺序阅读
完整研究流程，不需要先跳转到 `research_flow.py`。

本次只改变代码组织方式，不改变 SEC 数据选择、财务计算、Evidence/Calculation
验证、Analysis Gate、Claim Gate、确定性 Verdict、Agent、Task、Crew、DeepSeek、
Yahoo、EDGAR 或最终输出契约。

## 当前问题

- `ResearchFlowState` 和装饰器链位于 `research_flow.py`，而用户首先查看的
  `main.py` 主要是大量辅助函数，无法像官方示例一样直接看到事件顺序。
- `main.py` 已接近 1500 行。如果把 `research_flow.py` 的约 700 行直接复制进来，
  主文件会超过 2000 行，仍然难以理解。
- 当前 `main.kickoff(request)` 只启动 Request Parser Crew，但官方 Flow 示例中的
  `kickoff()` 表示启动整个 Flow；两个语义冲突。

## 采用方案

采用“薄 `main.py` + 确定性支持模块”方案。

### `src/stockcrewai/main.py`

主文件只承担以下职责：

1. 定义 `ResearchFlowState(BaseModel)`；
2. 定义 `@persist()` 修饰的 `ResearchFlow(Flow[ResearchFlowState])`；
3. 按事件顺序定义 `@start()`、`@listen()` 和 `@router()` 方法；
4. 定义兼容入口 `run_research()`；
5. 定义官方风格入口 `kickoff()` 和 `plot()`，并由 `pyproject.toml` 暴露；
6. 保留 `cli()` 的终端与 `run-output.md` 双写。

主文件不得重新实现 SEC、计算、验证、Claim 解析或 Verdict 算法。Flow 方法只能：

- 读取上游输出或 `self.state`；
- 调用 Crew 或 `pipeline_support` 中的确定性函数；
- 更新 State；
- 返回下游事件数据或稳定路由标签。

### `src/stockcrewai/pipeline_support.py`

从现有 `main.py` 迁入所有非 Flow 编排辅助逻辑，包括：

- Request Parser Crew 调用与解析；
- SEC facts 到计算器输入的适配；
- JSON-safe 序列化和敏感字段脱敏；
- 验证状态同步和已验证 State 构造；
- 财务、风险、估值三个 Analysis 输入构造；
- Analysis 输出解析、Claim schema 与 ID 白名单校验；
- Analysis Gate、阻断结果、估值输入、历史价格和反向 DCF 输入；
- 确定性 Verdict 调用；
- Crew 测试替身/工厂兼容适配；
- CrewAI task-output history 兼容配置。

迁移必须保持现有函数行为和参数不变。除解决 `kickoff` 名称冲突外，不进行大规模
重命名或业务重写。

### 删除旧 Flow 文件

当 `main.py` 的 Flow 测试通过后，删除：

- `src/stockcrewai/research_flow.py`
- `tests/test_research_flow.py`

测试迁移到 `tests/test_main_flow.py`。项目中只能存在一个 `ResearchFlow` 定义，避免
两个 Flow 漂移。删除前必须确认 `rg "stockcrewai.research_flow"` 没有生产引用。

## 严格 Flow 语法

当前项目安装 CrewAI `1.15.11`，必须使用本地已经验证的语法：

```python
from crewai.flow.flow import Flow, listen, router, start
from crewai.flow.persistence import persist

@persist()
class ResearchFlow(Flow[ResearchFlowState]):
    @start()
    def parse_request(self):
        ...

    @listen(parse_request)
    def prepare_evidence(self, parsed_request):
        ...

    @listen(prepare_evidence)
    def prepare_valuation(self, validated_state):
        ...

    @router(prepare_valuation)
    def route_analysis(self, valuation):
        return "analysis_ready" or "analysis_blocked"

    @listen("analysis_ready")
    def run_analysis(self):
        ...

    @router(run_analysis)
    def route_claims(self, analysis_output):
        return "claims_ready" or "claims_blocked"

    @listen("claims_ready")
    def generate_report(self):
        ...
```

约束：

- 只能有一个无条件 `@start()`；
- 普通顺序必须使用 `@listen(previous_method)`，不得靠 `kickoff()` 手写依次调用；
- 只有确定性分支使用 `@router(previous_method)`；
- 路由只返回稳定标签，不返回 LLM 自由文本；
- `@listen("label")` 只监听明确的 router 标签；
- `kickoff()` 返回最后完成节点的输出；
- 使用 `@persist()`，因为本地 `1.15.11` 的 `persist` 是装饰器工厂，裸
  `@persist` 在本环境不可运行。

## 目标事件链

```text
@start parse_request
  -> @listen prepare_evidence
  -> @listen prepare_valuation
  -> @router route_analysis
       -> analysis_blocked -> @listen finalize_analysis_blocked
       -> analysis_ready   -> @listen run_analysis
                                -> @router route_claims
                                     -> claims_blocked -> @listen finalize_claims_blocked
                                     -> claims_ready   -> @listen generate_report
```

## State 契约

`ResearchFlowState` 保持以下公开字段：

- 请求：`request`、`parsed_request`、`input_requirements`
- SEC/验证：`edgar`、`facts`、`filings`、`calculations`、`validation`
- 市场/估值：`market_price_data`、`valuation`、`historical_valuation`、`reverse_dcf`
- 分析/报告：`analysis`、`analysis_diagnostics`、`verdict`、`report`
- 控制：`status`、`stage`、`required_data`

所有列表和字典使用 `Field(default_factory=...)`。工具、Crew、LLM、Prompt、API key、
token 和原始不可序列化对象只能放在 Flow `PrivateAttr`，不得写入 SQLite State。

## 入口契约

### `run_research(...) -> dict`

保持现有完整签名和依赖注入参数。它只负责：

1. 构造 `ResearchFlow`；
2. `flow.kickoff(inputs={"request": request})`；
3. 转换旧接口需要的 JSON-safe 返回结构。

### `kickoff(request: str | None = None) -> int | None`

成为启动整个 Flow 的官方风格入口，不再表示 Request Parser Crew。它同时保留
`run-output.md` 双写，因此 `crewai run`、`crewai flow kickoff` 和直接执行
`uv run kickoff` 使用同一条入口链。请求优先级保持：

1. 显式参数；
2. `STOCKCREWAI_REQUEST`；
3. 命令行参数；
4. `DEFAULT_REQUEST`。

解析 Crew 的调用迁移到 `pipeline_support.run_request_parser(request)`。

### `plot() -> None`

只在用户显式调用时执行 `ResearchFlow().plot("stockcrewai_flow")`，不在
普通研究运行中自动生成 HTML，避免项目目录出现无用文件。

`pyproject.toml` 必须使用 CrewAI Flow 脚手架兼容的脚本映射：

```toml
[project.scripts]
kickoff = "stockcrewai.main:kickoff"
plot = "stockcrewai.main:plot"
```

因此以下命令必须通过真实子进程验收，而不只通过函数单元测试：

```bash
crewai flow kickoff
crewai flow plot
```

`crewai flow kickoff` 内部会执行 `uv run kickoff`；`crewai flow plot` 内部会执行
`uv run plot`。两者都不得依赖 Conda 环境入口或手工设置 `PYTHONPATH`。

### `cli(output_path: Path | None = None) -> int | None`

保留为兼容函数，由新的 `kickoff()` 复用；负责将 CrewAI 原生 Flow 框体、最终
JSON 和异常复制到终端及 `run-output.md`。它不再直接占用 `kickoff` 脚本名，
避免官方 `crewai flow kickoff` 与项目内部入口语义分裂。

## 阻断和错误处理

- Request Parser 无法解析：`status="blocked"`，停止 SEC/市场/Analysis/Report；
- Analysis Gate 阻断：保留确定性输出，Analysis Crew、Verdict、Report 均不调用；
- Claim Gate 阻断：不传递部分 Claims，Verdict、Report 均不调用；
- 未处理异常：由 `cli()` 输出 traceback 并记录退出码 `1`；
- 不使用 `try/except Exception` 将真实错误伪装为成功结果；
- 不新增 `limitations` 或 `analysis notice` 文本通道，继续使用稳定
  `status/stage/required_data/analysis_diagnostics`。

## 测试要求

### TDD RED

先创建 `tests/test_main_flow.py`，在生产迁移前验证以下断言会失败：

- `ResearchFlowState` 和 `ResearchFlow` 直接定义在 `stockcrewai.main`；
- `parse_request` 是唯一无条件 `@start()`；
- 顺序节点使用直接方法引用的 `@listen`；
- Analysis/Claim 分支使用 `@router` 和稳定标签；
- `kickoff()` 启动整个 Flow，不直接启动 Request Parser Crew；
- `plot()` 使用 `ResearchFlow.plot("stockcrewai_flow")`；
- SQLite 只保存 JSON-safe State，不保存 PrivateAttr。

### GREEN 与回归

- 成功路径；
- Analysis Gate 阻断路径；
- Claim Gate 阻断路径；
- CLI 与 `run-output.md`；
- 现有工具、Crew 配置和 Claims 测试；
- `compileall`；
- `git diff --check`；
- `rg "stockcrewai.research_flow"` 最终无生产引用。
- `crewai flow kickoff` 能进入完整 Flow；离线验收可通过注入/环境开关避免真实网络；
- `crewai flow plot` 成功在 CrewAI 临时目录生成 HTML/CSS/JS 并尝试打开浏览器，
  项目目录不保留流程图产物。

默认测试不得调用真实 SEC、Yahoo 或 DeepSeek。

## 子代理任务和文件所有权

代码代理全部使用 `luna_coder`（Luna Max），按顺序执行，禁止并行修改同一文件。

1. **契约测试代理**
   - 所有权：`tests/test_main_flow.py`
   - 只写 RED 测试，不修改生产代码。
2. **支持模块迁移代理**
   - 所有权：`src/stockcrewai/pipeline_support.py`
   - 机械迁移现有辅助逻辑，不修改 `main.py`。
3. **Main Flow 实现代理**
   - 所有权：`src/stockcrewai/main.py`、`pyproject.toml`
   - 定义 State、Flow、装饰器链和入口；使用前两项固定接口。
4. **测试迁移与清理代理**
   - 所有权：现有测试导入、`README.md`、删除旧 `research_flow.py` 与旧测试。
   - 不改变业务断言，只更新模块位置和公开入口语义。
5. **最终修复代理**
   - 仅处理独立审查发现的问题，文件范围由具体 finding 限定。

每个实现代理必须加载 `superpowers:using-superpowers`、
`superpowers:test-driven-development` 和 `superpowers:verification-before-completion`；
每个任务完成后另用只读审查代理检查需求符合性和代码质量。

## 验收标准

- 打开 `main.py` 能按装饰器从上到下读出完整全链路；
- `main.py` 不包含 SEC、计算、Claim 校验算法的实现细节；
- 项目只有一个 `ResearchFlow` 定义；
- `crewai run`、`run_research()`、`kickoff()`、`plot()` 和 `cli()` 契约清晰；
- 确定性金融边界与现有输出完全保留；
- SQLite State 无密钥和不可序列化依赖；
- 全量离线测试、编译和空白检查通过；
- 不生成或保留无用目录、兼容副本或自动 plot 文件。
