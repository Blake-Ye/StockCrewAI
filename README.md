# stockcrewai

基于 CrewAI 的美国上市公司投资研究系统。当前线路包含请求解析、SEC EDGAR facts/申报文本、确定性 TTM Builder、Decimal 财务计算、确定性验证、yfinance 行情、当前估值、历史估值、反向 DCF 和版本化确定性 Verdict。TTM 本轮只生成并展示，不切换当前估值输入；缺少必要证据时返回结构化 `unavailable`/`insufficient_data`，不补造数据。

## 环境配置

项目使用 uv 管理 Python 环境、依赖和锁文件；项目命令统一通过 uv 执行。

项目使用 DeepSeek 原生 OpenAI-compatible provider：

```dotenv
DEEPSEEK_API_KEY=your_deepseek_api_key
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
EDGAR_IDENTITY=Your Name your.email@example.com
```

本地配置写入 `.env`，不要把真实密钥提交到 Git。模型在每个 `agents.yaml` 中固定为 `deepseek/deepseek-v4-flash`。

## Crew 结构

- `request_parser`：解析公司、Ticker 候选、关注方向、语言和投资期限。
- `analysis`：分别分析财务质量、SEC 风险和已验证估值结果。
- `report`：将已验证 Claims、确定性 Verdict 和来源元数据整理为中文 Markdown 报告。

总计 3 个 Crew、4 个 LLM Agent、4 个 Task：RequestParserAgent、FinancialQualityAgent、RiskAnalysisAgent、ReportWriterAgent。前述 Agent 只解释或整理已验证的结构化结果；估值 Claims 由确定性 Python 逻辑生成，不设置估值 LLM Agent。Agent 不负责编造事实、直接计算或最终评级。

工具位置：

- `src/stockcrewai/tools/edgar_tool.py`：接受公司名或 ticker，输出带 Evidence ID 的 SEC 公司、facts 和固定范围 filings。
- `src/stockcrewai/tools/ttm_tool.py`：根据已验证的 `latest_fy`、`current_ytd` 和 `prior_ytd` Evidence 使用 Decimal 构建并展示 TTM；本轮不替换当前估值、历史估值、反向 DCF 或 Verdict 的输入。
- `src/stockcrewai/tools/calculator_tool.py`：根据结构化 facts 使用 Decimal 计算指标。
- `src/stockcrewai/tools/validation_tool.py`：重算 CalculationResult 并返回验证状态。
- `src/stockcrewai/tools/valuation_tool.py`：绑定市场价格 provenance 和 Evidence ID，计算当前估值。
- `src/stockcrewai/tools/historical_valuation_tool.py`：按五年月末价格与 point-in-time 财务快照计算历史 P/E 统计。
- `src/stockcrewai/tools/reverse_dcf_tool.py`：用 Decimal 二分法计算固定场景的反向 DCF。
- `src/stockcrewai/tools/verdict_tool.py`：执行 v1 确定性数据完备性与估值政策。

## 运行

配置 `.env` 后，可通过以下入口启动完整研究线路；这会调用 DeepSeek、SEC 和市场行情接口：

```bash
set -a; source .env; set +a
uv run --no-sync crewai run          # 启动完整 ResearchFlow
uv run --no-sync crewai flow plot    # 在 CrewAI 临时目录生成并打开流程图，不污染项目目录
```

脚本或 CI 若需要可靠获取失败退出码，请使用 `uv run --no-sync kickoff`；
CrewAI 1.15.11 的外层 `crewai flow kickoff` 会显示子进程错误，但可能不向上传递非零退出码。

每次运行会在终端实时显示七个紧凑阶段，并在项目根目录写入：

- `run-output.md`：无 ANSI 控制码的人类摘要，包含 Gate 决策、阻断原因和下一步；
- `run-result.json`：完整机器结果，供审计和排错使用。

默认请求包含未来 3 年；显式请求未提供投资期限时，结果会标记 `input_requirements.status=needs_input`，不会擅自补充期限。可通过环境变量传入完整请求：

```bash
STOCKCREWAI_REQUEST='分析苹果公司未来 3 年投资价值' uv run --no-sync crewai run
```

当前入口线路是：请求解析 → Edgartools SEC 公司、Company Facts 和申报文本查询 → Decimal 基础财务计算与确定性验证 → TTM Evidence 独立验证与 TTM Builder → yfinance 行情 → 当前估值 → 历史估值/反向 DCF → v1 确定性 Verdict → AnalysisCrew → ReportCrew。TTM Builder 在基础 Evidence 验证后生成并展示可用性，但本轮不切换当前估值输入。运行时关闭 CrewAI 任务输出 SQLite 持久化；工具默认关闭 Edgartools 的本地 HTTP 文件缓存，避免运行时写入 `~/.edgar/_tcache`。需要时可通过 `EDGAR_HTTP_TIMEOUT` 调整单次请求超时。SEC、Yahoo 或 DeepSeek 某个端点不可用时，结果会保留 `partial`/`unavailable`/`insufficient_data` 状态和来源原因，不生成伪造数据。

也可以在 Python 中传入请求：

```python
from stockcrewai.main import run_research

result = run_research("我想分析苹果公司的财务质量和估值")
print(result["edgar"]["status"])
```

## 离线验证

测试只构造 Crew、Agent 和 Task，使用临时测试存储和 dummy API Key，不调用真实模型、SEC 或市场接口：

```bash
uv run --no-sync python -m unittest discover -s tests -p 'test_*.py' -v
```
