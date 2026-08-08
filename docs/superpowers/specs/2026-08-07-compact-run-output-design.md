# StockCrewAI 紧凑运行输出设计

## 目标

将默认运行输出从约 5,000 行的原生事件与完整 JSON 混合文本，改成可在一屏内
定位执行阶段、决策者和阻断原因的人类诊断视图，同时把完整机器数据独立保存为
JSON。不得改变 SEC、市场价格、计算、验证、Analysis Gate、Claim Gate、Verdict
或 Report 的业务结果。

## 输出契约

### 终端

- 默认隐藏 CrewAI 重复的 Flow Started、Method Running、Method Completed 框；
- 每个逻辑阶段最多输出一个 Rich/CrewAI 风格框；
- 阻断运行目标不超过 120 行；
- 每个框只显示执行者、输入摘要、输出摘要、状态、决策、原因和下一节点；
- 不显示完整 filing、历史价格、Evidence ID 列表、Agent 原始 JSON 或密钥；
- 终端保持实时进度，长时间网络/LLM 调用前可显示阶段名称，但同一阶段不得再输出
  Running 与 Completed 两个大框。

固定阶段：

1. 请求解析；
2. SEC 证据与财务验证；
3. 市场价格与估值；
4. Analysis Gate；
5. Analysis Crew；
6. Claim Gate；
7. Verdict 与 Report，或最终阻断。

### `run-output.md`

- 只保存人类摘要，目标不超过 200 行；
- 必须是无 ANSI 控制码的 UTF-8 Markdown；
- 顶部先显示最终业务状态，再显示请求和时间线；
- 阻断时明确显示：阶段、决策者、域、直接原因、required_data、已完成、未执行和
  下一步；
- 不保存 `analysis_diagnostics.raw_task_outputs`；
- 退出码与业务状态分开显示，避免 `exit_code=0` 被理解为研究成功；
- 指向同目录的完整结果文件 `run-result.json`。

### `run-result.json`

- 保存 `run_research()` 返回的完整 JSON-safe 结果；
- 使用 UTF-8、中文不转义、两空格缩进、禁止 NaN；
- 保留完整证据、估值、Claims 和脱敏诊断；
- 原子性不做额外抽象，本次只需普通覆盖写入；
- 作为运行产物，不加入源代码或提示词。

## 冻结接口

新增 `src/stockcrewai/run_output.py`：

```python
@dataclass(frozen=True)
class RunStageEvent:
    step: int
    title: str
    actor: str
    status: str
    input_summary: str = ""
    output_summary: str = ""
    decision: str = ""
    reason: str = ""
    next_step: str = ""

class CompactRunReporter:
    def __init__(self, terminal_stream: TextIO) -> None: ...
    def emit(self, event: RunStageEvent) -> None: ...
    def finalize(
        self,
        *,
        result: Mapping[str, Any],
        output_path: Path,
        result_path: Path,
        started_at: datetime,
        finished_at: datetime,
        exit_code: int,
    ) -> None: ...

def strip_ansi(text: str) -> str: ...
def summarize_result(result: Mapping[str, Any]) -> dict[str, Any]: ...
```

`CompactRunReporter.emit()` 负责实时终端框和内存事件列表；`finalize()` 负责最终终端
框、Markdown 和完整 JSON。渲染模块不得导入 Crew、Flow 或金融工具。

## Flow 集成

- `ResearchFlow` 默认设置 `suppress_flow_events=True`；
- 增加 `progress_callback` 私有依赖，不写入 Pydantic State/SQLite；
- 每个逻辑节点只提交结构化 `RunStageEvent`，不得把完整对象传给 reporter；
- `run_research()` 保持旧公开返回契约，只新增可选的内部进度回调参数；
- `kickoff()` 捕获 CrewAI/Agent 原始 stdout/stderr，避免噪声进入终端和 Markdown；
- Reporter 必须写入启动前保存的真实终端流，因此捕获期间仍能实时显示紧凑框；
- 未处理异常显示一个 ERROR 框，写入 Markdown 失败摘要，并保持非零退出码；
- 完整 traceback 不在默认终端展开，可写入 Markdown 的短错误类型与消息；不得包含
  环境变量或密钥。

## 摘要规则

- 请求：公司名、ticker、期限、focus 数量；
- 证据：事实数、filing 数、风险章节数、计算数、验证状态；
- 估值：价格、时间戳、币种、P/E、FCF Yield、历史百分位、Reverse DCF 隐含增长；
- Analysis：财务/风险/估值 Claim 数；
- Gate：READY/BLOCKED、required_data、诊断域和 reason_code；
- 报告：Verdict 状态、报告是否生成；
- ID 只显示数量，默认不展开具体值。

## 安全与兼容

- 使用现有 `_json_safe` 和脱敏边界，不打印 `.env` 或 API key；
- 默认离线测试不得调用 SEC、Yahoo 或 DeepSeek；
- `crewai run`、`uv run kickoff`、`run_research()` 依赖注入与 `crewai flow plot` 保持可用；
- CrewAI traces 可以继续收集，但默认摘要不保存带 access code 的临时 trace URL；
- 不新增第三方依赖，Rich 已由 CrewAI 提供；无 Rich 时使用纯文本框或 Markdown 文本。

## 验收

- 紧凑阻断输出不超过 120 行；
- `run-output.md` 不超过 200 行且不包含 ANSI；
- `run-output.md` 不包含 `raw_task_outputs` 或完整 Evidence ID 列表；
- 本轮示例在开头明确显示 `Claim Gate / risk / claims_empty`；
- `run-result.json` 可由 `json.load()` 读取且保留完整 `analysis_diagnostics`；
- 现有全量离线测试继续通过。
