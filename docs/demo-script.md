# StockCrewAI 求职演示脚本

目标是让面试官在 2–3 分钟内看懂：Flow 如何传递数据、Agent 做什么、Python 如何阻断不可靠结果，以及报告如何写出。

## 1. 开场

> StockCrewAI 是一个 evidence-backed 投研原型。它把研究请求送入固定的 3 个 Crew、4 个 Agent，再由确定性的 Python 内核负责证据、计算、门禁、Verdict 和报告数字。LLM 负责语言工作，不负责决定事实。

当前三个 Crew 的边界：

| Crew | Agent | 任务 |
| --- | --- | --- |
| `RequestParserCrew` | `RequestParserAgent` | 解析公司候选、ticker、关注点、语言和期限 |
| `AnalysisCrew` | `FinancialQualityAgent` | 解释已验证财务记录并输出 Claim |
| `AnalysisCrew` | `RiskAnalysisAgent` | 解释已验证 SEC 风险文本并输出 Claim |
| `ReportCrew` | `ReportWriterAgent` | 组织叙述草稿，不新增数字 |

## 2. 展示 Flow

打开 `src/stockcrewai/flow.py`，展示 `@start`、`@listen` 和 `@router`：

```bash
rg -n "@start|@listen|@router|def route_|def generate_report" src/stockcrewai/flow.py
```

说明：Flow 是跨 Crew 的总调度器。它将每个节点的结果写入 `ResearchFlowState`，Analysis Gate 和 Claim Gate 通过后才继续；阻断时返回结构化结果，不补数据、不写正式报告。

## 3. 展示离线测试

```bash
uv run --no-sync pytest -q
uv run --no-sync python -m compileall -q src tests
```

说明：离线测试只用 fixture 和替身，不把测试通过说成真实 SEC、Yahoo 或 DeepSeek 可用。

## 4. 展示真实运行和产物

```bash
set -a
source .env
set +a
STOCKCREWAI_REQUEST='分析苹果公司未来 3 年投资价值' uv run --no-sync crewai run
```

重点查看：

- `run-output.md`：阶段、状态、Gate 和阻断原因摘要；
- `run-result.json`：完整机器结果和审计字段；
- `investment-report.md`：只有正式报告 Gate 通过时才写入。

## 5. 面试总结

“我把 LLM 放在解析、解释和组织的位置，把来源选择、数字、路由和门禁放在可测试的 Python 内核里。这样出现错误时可以根据 `stage`、`error.category`、`reason_code` 和 `required_data` 定位，而不是把一段看似完整的文字误认为可靠报告。”
