# StockCrewAI 求职演示脚本

目标是让面试官在 2–3 分钟内看懂项目的职责边界、可审计数据流和复现入口。演示对象是当前仓库版本；离线样例使用 fixture，不把样例数字说成实时数据。

## 1. 开场：项目目标与诚实边界

可以先说：

> StockCrewAI 是一个 evidence-backed research/quant prototype。它把研究请求送入固定的 3 个 Crew、4 个 Agent，再由确定性的 Python 内核负责证据、计算、门禁、Verdict 和报告数字。LLM 负责语言工作，不负责决定事实。

### 固定的 3 Crew / 4 Agent

| Crew | Agent | 只做什么 |
| --- | --- | --- |
| `RequestParserCrew` | `RequestParserAgent` | 把自然语言请求解析成公司候选、ticker、关注方向、语言和期限；不确认 CIK、不查网、不生成财务数字。 |
| `AnalysisCrew` | `FinancialQualityAgent` | 解释已经验证的财务 Evidence/Calculation，产出带来源 ID 的 Claim。 |
| `AnalysisCrew` | `RiskAnalysisAgent` | 解释已经验证的 SEC filing 风险文本，产出带 Evidence ID 的风险 Claim。 |
| `ReportCrew` | `ReportWriterAgent` | 组织叙述草稿；最终数字、状态、来源和 Verdict 由 Python Renderer 注入。 |

### 必须主动说清楚的边界

- LLM 只做请求解析、已验证事实的解释和报告叙述组织。
- Python 负责 SEC/行情来源选择，实体与证券 Profile/Policy，Evidence/Calculation 规范化与验证，`Decimal` 计算，Analysis Gate、Claim Gate、Verdict 以及报告数字渲染。
- 当前版本不宣称覆盖所有美股（full coverage），不宣称无偏 Alpha，不预测未来收益，也不是投资建议。
- 离线报告是 fixture 的可审计演示；真实运行需要本地 `.env` 和外部服务，失败时保留 typed 状态，不把失败改写成成功。

一句可说的话：**“我把 LLM 放在解析、解释和组织的位置，把会影响结论的来源选择、数字和路由放在可测试的 Python 内核里。”**

## 2. 2–3 分钟按秒演示

演示时按下面顺序打开文件；每一步都明确“看到什么、说明什么、不要误解什么”。

### 0:00–0:25：先看 README 的信任边界和数据流

操作：打开 [`README.md`](../README.md)，先看“先看结论”“当前发布状态”“数据流”和“Coverage 与证券边界”。需要命令行展示时：

```bash
sed -n '1,115p' README.md
```

- **看到什么：** README 先写 LLM/Python 分工，再展示 `研究请求 → Profile → SEC/Market → Evidence → Decimal Calculation → Gate → Crew → Renderer` 的数据流，并列出 `full`、`partial`、`evidence_only` 和 `unsupported_security` 的含义。
- **说明什么：** 项目优先保证证据和状态可解释，Agent 不是自由选择数据或数字的黑盒。
- **不要误解什么：** README 的架构边界不是“所有股票都能得到同一种报告”的承诺；离线样例也不是最新 SEC 或行情。
- **口播：** “先看边界再看结果，面试时我不会把 prototype 说成全市场产品。”

### 0:25–0:55：展示 Flow 如何编排，而不是让 Crew 自己互相决定

操作：打开 [`src/stockcrewai/flow.py`](../src/stockcrewai/flow.py)，展示 `@start`、`@listen`、两个 `@router` 以及 `claims_ready` 分支：

```bash
sed -n '1109,1180p;1799,1812p;2198,2211p;2442,2454p' src/stockcrewai/flow.py
```

- **看到什么：** `parse_request()` 是 `@start()`；`prepare_evidence()` 由 `@listen(parse_request)` 触发；`route_analysis()` 由 `@router(...)` 选择 `analysis_ready` 或 `analysis_blocked`；`route_claims()` 再选择 `claims_ready` 或 `claims_blocked`；只有 `claims_ready` 才进入 `generate_report()`。
- **说明什么：** Flow 负责顺序、状态、分支、阻断和最终输出；Analysis/Report Crew 只消费允许进入的输入。Gate 通过后才会继续，失败是 fail closed。
- **不要误解什么：** `@router` 不是 LLM 投票，也不是“只要 Crew 有输出就继续”；它读取 Python 校验结果和稳定的 `reason_code`。
- **口播：** “Crew 负责有限的语言任务，Flow 决定什么时候能进入下一阶段。”

### 0:55–1:25：展示一个离线 Profile-aware 报告

操作：打开 [`examples/reports/reit.md`](../examples/reports/reit.md)；若面试官更关心普通企业，也可以改看 [`examples/reports/standard-operating.md`](../examples/reports/standard-operating.md) 或 [`examples/reports/bank.md`](../examples/reports/bank.md)。

```bash
sed -n '1,95p' examples/reports/reit.md
```

- **看到什么：** REIT 样例标明 `synthetic=true`、`offline=true`、`coverage=full`，并展示 FFO/AFFO、P/FFO、入住率等指标的 `evidence_id`、`calculation_id` 或 `source_reference`；P/E 和普通企业 FCF Yield 被标为 `not_applicable`。
- **说明什么：** Profile/Policy 先决定指标是否适用，再让确定性计算和 Renderer 生成数字；这避免把银行或 REIT 硬套成普通企业。
- **不要误解什么：** `coverage=full` 只表示这个离线 fixture 在适用指标上完整，不表示真实市场覆盖或实时可用。
- **口播：** “这里的亮点不是数字看起来漂亮，而是每个数字能回到证据和公式，并且不适用项没有被补成零。”

### 1:25–1:50：展示 Quant sample 的 partial 和幸存者偏差标记

操作：打开 [`examples/quant/quant-research.md`](../examples/quant/quant-research.md)，再看固定 universe 的 bias 标记：

```bash
sed -n '1,35p' examples/quant/quant-research.md
sed -n '1,20p' examples/universes/us-large-cap-v1.json
```

- **看到什么：** Quant 样例明确写出 `synthetic=true`、`offline=true`、`no_network=true`、`coverage=partial`、`survivorship_bias_known`；样例还保留 `as_of`、完整期数/总期数和 artifact ID。
- **说明什么：** point-in-time/quant 结果是有时间边界和质量标签的研究旁证；部分数据和已知偏差必须进入报告。
- **不要误解什么：** 回测或 CAGR 不是未来收益承诺，也不能由固定现存股票池推出无偏结论；`partial` 不能被展示成 `full`。
- **口播：** “量化层强调可复现和限制披露，不能因为有一组回测数字就跳过数据质量审查。”

### 1:50–2:15：最后展示真实 live failure 分类

操作：打开 [`examples/coverage-matrix.md`](../examples/coverage-matrix.md) 的 Matrix 和聚合小结：

```bash
sed -n '1,78p' examples/coverage-matrix.md
```

- **看到什么：** 这次固定 30 个 ticker、每个只请求一次；30 行都是 `status=error`，`error.category=external_dependency`、`error.reason_code=permissionerror`，`stage`、`issuer_profile` 和 `coverage_level` 为 `unavailable`，聚合也是 `external_dependency=30`。
- **说明什么：** 失败被按 typed `category/reason_code` 原样保留，能区分本次环境阻断和业务 Gate；系统没有把外部依赖失败降级成成功报告。
- **不要误解什么：** `external_dependency/permissionerror` 是本次环境的访问阻断，不是代码通过，也不是证券覆盖结论；它需要在真实可访问环境重新运行。
- **口播：** “我把失败也当成证据的一部分：先报告环境事实，再决定是否有资格讨论 coverage。”

### 2:15–2:30：收束与复现入口

收束时指向 [`README.md`](../README.md) 的离线门禁和运行产物说明：测试只验证 fixture/注入依赖，正式报告只有在状态和 Gate 满足条件时才写出。若时间只剩 15 秒，复述：

> “这套演示证明的是职责分离、追溯和 fail-closed；不证明实时服务可用、不证明全市场覆盖，也不提供投资建议。”

## 3. 可复制命令

### 3.1 默认离线门禁

以下与 README 的离线路径一致。变量都是 dummy 值；临时存储和缓存放在 `/tmp`，并关闭 tracing/telemetry。命令不包含 live 开关，不访问 DeepSeek、SEC、Yahoo 或付费 API：

```bash
export DEEPSEEK_API_KEY=test-deepseek-key
export DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
export EDGAR_IDENTITY=offline-test@example.com
export CREWAI_STORAGE_DIR=/tmp/stockcrewai-flow-storage
export CREWAI_TRACING_ENABLED=false
export OTEL_SDK_DISABLED=true
export UV_CACHE_DIR=/tmp/stockcrewai-uv-cache

uv run --no-sync pytest -q
uv run --no-sync python -m unittest discover -s tests -p 'test_*.py' -q
uv run --no-sync python -m compileall -q src tests
uv run --no-sync ruff check src tests
```

以本机退出码和原始输出为准；不要把离线 fixture 的通过说成实时数据验证。测试产生的报告、图片和回测 artifact 应在测试临时目录中，不要写入仓库根目录。

### 3.2 只画 Flow 图

```bash
uv run --no-sync crewai flow plot
```

绘图只展示 Flow，不执行 SEC、行情或 LLM 研究；它会生成 HTML 及伴随的 CSS/JS，建议在临时工作目录运行，不要把生成物提交到仓库。

### 3.3 显式真实 live 运行

真实运行必须先在本地准备 `.env`，只在本地填写真实服务配置，不提交 `.env` 或凭据：

```bash
set -a
source .env
set +a
STOCKCREWAI_REQUEST='分析苹果公司未来 3 年投资价值' uv run --no-sync crewai run
```

这条命令会访问外部服务，可能涉及 DeepSeek、SEC 和行情服务；它不属于默认测试，失败时应先看 `status`、`stage`、`error.category`、`error.reason_code` 和 `required_data`。不要因为命令启动或生成摘要，就宣称完整报告成功。

## 4. 可直接放简历的项目描述

### 中文

- 构建基于 CrewAI Flow 的 evidence-backed research/quant prototype，固定 3 个 Crew、4 个 Agent，将请求解析、财务/风险分析和报告组织拆成清晰职责。
- 将 SEC/行情选择、Evidence/Calculation 验证、`Decimal` 计算、Profile/Policy、Gate、Verdict 和报告数字放入确定性 Python 内核，LLM 仅负责解析、解释和组织。
- 增加 point-in-time/quant 研究旁证与离线门禁，显式保留 `partial`、`unavailable` 和 `survivorship_bias_known` 等状态，便于复现和审计。

### English

- Built an evidence-backed research/quant prototype on CrewAI Flow with a fixed 3-Crew/4-Agent architecture for request parsing, analysis, and report composition.
- Kept SEC/market source selection, Evidence/Calculation validation, `Decimal` arithmetic, Profile/Policy decisions, gates, verdicts, and report numbers in deterministic Python; LLMs only parse, explain, and organize.
- Added point-in-time/quant research evidence and offline gates with explicit `partial`, `unavailable`, and `survivorship_bias_known` states for reproducibility and auditability.

## 5. 面试问答

### 1）为什么不让 LLM 计算数字或选择 SEC 文件？

数字计算、申报选择和 CIK/实体确认需要可重复、可测试、可追溯。LLM 的职责是解析请求和解释已经验证的输入；Python 通过固定规则、Evidence/Calculation ID 和 `Decimal` 控制结果，避免提示词变化改变事实口径。

### 2）Crew 和 Flow 的边界是什么？

Crew 是有限的语言任务集合：请求解析、财务/风险解释、报告叙述。Flow 是唯一总调度器，负责顺序、共享的 `ResearchFlowState`、分支、失败、Gate、Verdict 和 artifact 输出；Crew 不能绕过 Flow 直接决定下游。

### 3）Evidence、Calculation、Claim 如何追溯？

Evidence 带来源和期间等元数据，Calculation 记录公式及其输入，Claim 必须引用上游允许的 Evidence/Calculation 或 filing ID。Claim Gate 按白名单校验引用和 schema，Renderer 再从已验证状态注入数字与来源。

### 4）Gate 如何阻断？

Analysis Gate 检查财务事实/计算、风险章节和估值输入；缺少必需数据就返回 `analysis_blocked`。Claim Gate 检查 Agent 输出的 Claim schema、类别和引用；失败就返回 `claims_blocked`，不执行 Verdict 和 Report Crew，也不补造数字。

### 5）`not_applicable` 和 `unavailable` 有什么区别？

`not_applicable` 是 Profile/Policy 判断该指标不适用于当前证券，例如 REIT 的普通企业 FCF Yield；`unavailable` 是本来可能需要但当前证据、数据或外部依赖不可用。前者不是失败，后者也不能用零或旧值填充。

### 6）银行和 REIT 如何避免套普通企业指标？

先做 issuer/security Profile，再读取对应 Policy。银行看 ROA、ROE、NIM、效率比率、资本和信贷指标；REIT 看 FFO/AFFO、入住率、NOI、杠杆、股息覆盖和 P/FFO。普通企业 FCF Yield 或 P/E 在不适用时明确写 `not_applicable`。

### 7）Quant 为什么不改变 Verdict？

当前 QuantResearchPacket 是报告中的确定性研究旁证，带有 point-in-time、coverage 和偏差标记；Verdict 仍由独立的确定性估值、风险和 Policy 输入生成。这样可以先审计量化数据质量，避免一组部分或有偏样本直接升级成结论。

### 8）live 出现 `permissionerror` 怎么定位？

先看 `status → stage → error.category/reason_code → required_data`，确认它是 `external_dependency/permissionerror` 还是代码、契约或 Gate 问题；再检查本地 `.env`、SEC 身份、网络/服务权限和单次请求日志。重新运行前不改写结果，不把环境失败标成成功。

### 9）为什么默认测试不联网？

为了让测试可重复、无费用、无凭据依赖，并把代码回归和外部服务健康度分开。默认路径使用 fixture、替身或依赖注入；live 只有显式运行并在本地配置外部服务时才执行。

### 10）下一步如何改进？

先在具备真实网络、SEC 身份、行情和模型权限的环境重新跑 coverage smoke，并逐行保留 typed 结果；再补充历史成分股和退市证券数据，降低固定现存 universe 的 survivorship bias，继续保持 point-in-time、离线回归和确定性 Verdict 边界。

## 6. 当前已知限制与下一步

- [`examples/coverage-matrix.md`](../examples/coverage-matrix.md) 记录的是一次 30 个 ticker、每个一次请求的 live smoke：30 次全部为 `external_dependency/permissionerror`，`stage`、`profile` 和 `coverage` 均 `unavailable`。
- 这说明本次环境的外部依赖访问被阻断；它不是代码通过信号，也不是把失败降级为成功，更不能解释成“证券覆盖率为零”或任何证券覆盖结论。需要在真实可访问环境重新运行后，才有资格讨论各证券的 typed coverage。
- Quant 样例本身是 synthetic/offline，且为 `coverage=partial`、`survivorship_bias_known`；它用于方法和 artifact 演示，不是实时行情、未来收益或投资建议。
- 下一步的最小闭环是：真实环境重跑 live matrix → 按 `category/reason_code` 分开外部阻断与业务状态 → 获取有历史成员变更和退市记录的 point-in-time universe → 重新评估量化旁证的适用范围；在此之前不扩大项目声明。
