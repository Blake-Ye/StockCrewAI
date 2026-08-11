# StockCrewAI 项目止损与量化保留实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 停止继续扩张未闭环功能，恢复一条稳定、清晰、可真实运行的单公司投研主链路，并把量化模块降为独立、可选、经真实数据验证后才能进入报告的研究旁证。

**Architecture:** `crewai run` 只负责单公司基本面研究和报告，不在运行时临时执行横截面量化回测。量化引擎作为独立批处理管线，预先生成带 `as_of`、股票池版本、数据质量和来源的 `QuantResearchPacket`；报告流程只读取已经存在且验证通过的 Packet，没有 Packet 时完全省略量化章节。

**Tech Stack:** Python 3.10–3.13、CrewAI Flow、Pydantic v2、SEC EDGAR、yfinance、DuckDB/Parquet、SQLite 元数据索引、pandas、NumPy、matplotlib、pytest。

## Global Constraints

- 不增加 Crew 或 Agent；继续保持 3 Crew、4 Agent。
- LLM 只负责请求解析、已验证事实解释和文字组织；数据选择、计算、验证、评级由 Python 决定。
- 不使用 synthetic fixture、固定示例值或 fallback 冒充真实量化结果。
- 不因量化不可用而阻断基本面报告；没有有效 Packet 时不显示量化章节。
- 不让量化结果直接改变 Deterministic Verdict。
- 不新增依赖；继续使用项目已经批准的 pandas、NumPy、DuckDB、exchange-calendars、pytest、Hypothesis、pytest-xdist、Ruff 和 mypy。
- 每个任务完成后停止，先由用户检查产物，再开始下一个任务。
- 所有实施更改必须在新分支完成，不修改或覆盖用户现有的 `investment-report.md`、`run-output.md`、`run-result.json`。

---

## 1. 结论先行

### 1.1 当前量化部分有没有必要保留

**有必要保留，但不能保留当前的产品形态。**

保留的是确定性量化内核、数据契约和防前视测试；退出默认产品的是尚未接通真实数据的量化报告、合成演示数字和正文中的开发者追溯信息。

建议采用以下状态：

| 范围 | 决策 | 原因 |
|---|---|---|
| point-in-time snapshot、日历和存储 | 保留 | 这是避免未来数据泄漏的基础，也是量化项目的重要技术亮点 |
| 因子、标准化、同行排名 | 保留 | 纯 Python、可复现，适合展示量化研究能力 |
| 组合、统计、walk-forward 回测 | 保留为实验模块 | 有离线测试价值，但尚未用真实、无幸存者偏差的股票池验证 |
| `QuantResearchPacket` 契约与验证 | 保留并简化展示 | Packet 是合理的跨模块边界，但字段级追溯不应出现在报告正文 |
| 默认 `crewai run` 注入空 Packet | 移除 | 当前只会产生 `quant_packet_missing`，没有用户价值 |
| `examples/quant/quant-research.md` 作为正式演示 | 退出演示入口 | 文件使用 AAPL 合成 fixture 和固定 `aaaa`/`bbbb` ID，不是产品报告 |
| 报告正文逐字段输出 artifact/evidence/calculation ID | 移至技术附录 | 正文应解释投资含义，追溯信息应供审计使用 |
| 量化结果影响最终评级 | 暂不实现 | 当前数据覆盖、样本外验证和偏差控制均不足 |

**最终判断：现阶段不物理删除量化核心源码，将其完整隔离为 experimental 引擎；默认报告启用比例为 0%。只有真实批处理闭环通过验收后，才恢复报告接入。完成真实闭环后，再根据实际调用覆盖删除未使用接口，不能先凭代码行数决定保留比例。**

### 1.2 为什么不建议全部删除

当前量化目录约 5,228 行源码，已经覆盖快照、因子、排名、组合、统计、回测、Packet 和存储。2026-08-11 在当前分支运行量化相关离线测试，结果为：

```text
263 passed, 32 subtests passed in 7.54s
```

这些代码不是可直接交付的产品，但包含以下可用于实习项目展示的有效工程资产：

- point-in-time 时间边界；
- 明确的 signal date、trade date 和 forward return；
- Profile-aware 因子适用性；
- 行业内标准化和同行排名；
- 换手率与交易成本；
- SPY/股票池基准；
- 数据覆盖和幸存者偏差标签；
- 稳定 JSON/Parquet artifact 与 Pydantic Packet。

全部删除会损失项目中最能体现量化工程能力的部分。正确做法是隔离、缩小承诺、完成一条真实纵向闭环。

### 1.3 为什么不能继续维持现状

当前用户可调用的 Quant CLI 只有：

```text
collect-sec
collect-market
build
```

它只能校验本地规范化 JSON 并构建 point-in-time snapshot，不能完成：

```text
真实股票池 → 多期快照 → 因子 → 排名 → 回测 → Packet → 报告
```

同时，`crewai run` 没有真实 Packet 构建或加载入口，Flow 默认得到 `None`。因此正式 DUK 报告显示 `quant_packet_missing`，而演示文件中的 AAPL 排名和回测数字来自测试 fixture。继续美化这份 synthetic 报告只会扩大误解。

---

## 2. 当前项目止损审查

### 2.1 产品目标重新冻结

短期唯一产品目标：

```text
输入一家公司
→ 确认 ticker/CIK/Profile
→ 获取并验证 SEC 与市场数据
→ 计算适用的财务、估值和 TTM 指标
→ 生成外行也能读懂的中文 Markdown 报告
→ 数字和结论可追溯
```

短期明确不承诺：

- 任意美股都能获得 full coverage；
- 每家公司都有 DCF、历史估值和量化结果；
- 回测代表未来收益；
- 量化结果直接生成买卖评级；
- 单次 `crewai run` 在线抓取整个股票池并即时回测。

### 2.2 当前模块处置矩阵

| 模块 | 当前价值 | 当前问题 | 处置 |
|---|---|---|---|
| Request Parser Crew | 可用 | 仍依赖 LLM 结构化输出稳定性 | 保留 |
| SEC/EDGAR 与 Evidence | 核心可用资产 | 网络与公司披露差异会影响覆盖 | 保留并优先稳定 |
| 财务计算与验证 | 核心可用资产 | 普通公司指标与特殊行业 Profile 仍有映射差异 | 保留并修正 Profile 契约 |
| 市场价格、历史估值 | 有用 | Yahoo 限流和时点可用性不稳定 | 保留，失败时明确数据状态 |
| Analysis Crew | 有用但易产生结构化输出问题 | Prompt、Gate 和 Claim 契约历史上反复补丁 | 冻结功能，只修真实复现缺陷 |
| Verdict | 有价值 | 规则边界需要保持透明 | 保留，不接收量化输入 |
| Renderer 与图表 | 用户直接感知的关键模块 | DUK 第一张图缺失、内部提示污染正文 | 最高优先级修复 |
| Quant Engine | 有技术价值 | 没有真实端到端入口 | 隔离为 experimental |
| Quant Report Sidecar | 当前不可交付 | synthetic 示例被误解为真实报告 | 从默认报告和主演示移除 |
| Field-level provenance 正文 | 审计有用 | 普通读者无法理解且严重干扰正文 | 移至 JSON/技术附录 |

### 2.3 当前最明显的产品失败

1. 用户首先看到的是 Gate、reason code、artifact ID，而不是研究结论。
2. 报告展示“Renderer 注入”“未提供 Claim”等系统内部文字。
3. DUK 的原始普通财务指标有效，但 utility ReportContext 与固定图表指标不一致，导致第一张图整张消失。
4. 量化引擎有大量离线代码，却没有用户可执行的真实闭环。
5. 合成测试报告被放入演示路径，造成“量化已经真实可用”的错误第一印象。

---

## 3. 恢复路线与任务边界

### Task 0: 冻结基线和保护用户产物

**Files:**
- Create: `docs/baselines/project-salvage-baseline.md`
- Do not modify: `investment-report.md`
- Do not modify: `run-output.md`
- Do not modify: `run-result.json`

**Interfaces:**
- Consumes: 当前分支 HEAD、工作树状态、目标测试结果
- Produces: 可复核的止损基线，记录哪些失败已被真实复现

- [ ] **Step 1: 记录当前 Git 状态**

Run:

```bash
git branch --show-current
git status --short
git log -1 --oneline
```

Expected: 记录当前新分支、HEAD 和三个用户运行产物，不改写它们。

- [ ] **Step 2: 记录基础报告和量化测试基线**

Run:

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync pytest -q \
  tests/test_quant_models.py \
  tests/test_quant_dataset.py \
  tests/test_quant_factors.py \
  tests/test_quant_normalization.py \
  tests/test_quant_ranking.py \
  tests/test_quant_pipeline.py \
  tests/test_quant_portfolio.py \
  tests/test_quant_statistics.py \
  tests/test_quant_backtest.py \
  tests/test_quant_packet.py \
  tests/test_quant_report_integration.py \
  tests/test_report_visuals.py
```

Expected: 当前已验证基线为 `263 passed, 32 subtests passed`；未来结果必须重新运行，不得复制旧结论。

- [ ] **Step 3: 提交基线文档并停止**

```bash
git add docs/baselines/project-salvage-baseline.md
git commit -m "docs: freeze project salvage baseline"
```

用户检查点：确认基线没有把 synthetic、offline 或测试通过描述成真实产品通过。

### Task 1: 恢复基本面报告的最小可交付体验

**Files:**
- Modify: `src/stockcrewai/reporting/context.py`
- Modify: `src/stockcrewai/reporting/visuals.py`
- Modify: `src/stockcrewai/reporting/renderer.py`
- Test: `tests/test_report_visuals.py`
- Test: 新增或扩展 utility 报告回归测试

**Interfaces:**
- Consumes: 已验证 ReportContext、Profile、CalculationRecord、TTM 和估值结果
- Produces: 不暴露内部实现文字、Profile-aware 且图表完整的 Markdown 报告

- [ ] **Step 1: 写 DUK/utility 第一张图缺失的失败测试**

测试必须构造 utility ReportContext，并断言报告生成一张行业适用的核心指标图。不得直接把原始未验证 calculations 绕过 ReportContext 传给图表。

- [ ] **Step 2: 运行单测确认 RED**

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync pytest -q \
  tests/test_report_visuals.py -k utility
```

Expected: 因 utility 指标 ID 与固定普通公司图表契约不一致而失败。

- [ ] **Step 3: 实现 Profile-aware 图表 schema**

规则：

- `standard_operating` 使用收入增长、营业利润率、净利率、FCF Margin、现金转换率和股份变化；
- `utility` 使用 utility operating margin、capex intensity、FCF yield 和可用的 utility ROE；
- 不同单位不得强行放在同一坐标轴；
- 非关键指标缺失时只省略该柱，不让整张图消失；
- 所有数字仍必须来自已验证 ReportContext。

- [ ] **Step 4: 删除面向用户的内部 Renderer 文案**

以下文字不得进入正式报告：

```text
由确定性 Renderer 注入已验证内容
未提供可单独展示的文字 Claim
```

没有有效内容的非必要章节直接省略；必要章节使用自然语言状态说明。

- [ ] **Step 5: 运行报告目标测试**

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync pytest -q \
  tests/test_report_visuals.py \
  tests/test_reporting_modules.py \
  tests/test_utility_profile.py
```

- [ ] **Step 6: 使用真实 DUK 数据运行一次并停止**

```bash
STOCKCREWAI_REQUEST='请分析 Duke Energy Corporation（DUK），投资期限为3到5年' \
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache \
uv run --no-sync python -c \
  "from pathlib import Path; from stockcrewai.main import kickoff; raise SystemExit(kickoff(output_path=Path('/private/tmp/stockcrewai-salvage-DUK/run-output.md')) or 0)"
```

验收：从 `/private/tmp/stockcrewai-salvage-DUK/investment-report.md` 检查第一张图存在、没有遮挡、正文没有内部 Renderer 提示；本任务不接入量化，也不覆盖工作区现有运行产物。

### Task 2: 从默认产品隔离未完成量化功能

**Files:**
- Modify: `src/stockcrewai/flow.py`
- Modify: `src/stockcrewai/main.py`
- Modify: `src/stockcrewai/reporting/context.py`
- Modify: `src/stockcrewai/reporting/renderer.py`
- Modify: `docs/demo-script.md`
- Move or replace: `examples/quant/quant-research.md`
- Test: `tests/test_quant_report_integration.py`

**Interfaces:**
- Consumes: 可选 `QuantResearchPacket | None`
- Produces: 没有 Packet 时不显示量化章节；有真实且验证通过的 Packet 时才显示用户版摘要

- [ ] **Step 1: 写“默认报告不显示空量化章节”的失败测试**

断言普通 `crewai run` 没有 Packet 时：

```python
assert "## 量化" not in report
assert "quant_packet_missing" not in report
```

机器可读 JSON 可以保留 typed 状态，但 Markdown 不展示内部 reason code。

- [ ] **Step 2: 运行目标测试确认 RED**

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync pytest -q \
  tests/test_quant_report_integration.py -k missing
```

- [ ] **Step 3: 最小修改默认渲染逻辑**

仅当 `quant.status == "available"` 且 Packet 验证通过时加入量化章节。不要增加 fallback，不读取 examples，不制造空图。

- [ ] **Step 4: 隔离 synthetic 演示文件**

将现有文件定位为测试审计样例，而不是正式量化报告。演示文档不得要求招聘者或普通用户把 `aaaa`/`bbbb`、`12.34%` 或 `2/10` 当成真实结果。

- [ ] **Step 5: 保留审计信息但移出正文**

完整 artifact/evidence/calculation ID 写入 JSON artifact 或报告末尾的“技术审计附录”；正文只显示数据日期、股票池版本、覆盖率和一条自然语言限制。

- [ ] **Step 6: 运行报告与量化接入测试并停止**

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync pytest -q \
  tests/test_quant_report_integration.py \
  tests/test_report_visuals.py \
  tests/test_reporting_modules.py
```

用户检查点：默认 DUK 报告不再出现 `quant_packet_missing`；synthetic 示例不会被误认为真实运行结果。

### Task 3: 建立独立量化真实纵向闭环

**Files:**
- Modify: `src/stockcrewai/quant/cli.py`
- Modify: `src/stockcrewai/quant/dataset.py`
- Modify: `src/stockcrewai/quant/pipeline.py`
- Modify: `src/stockcrewai/quant/backtest.py`
- Modify: `src/stockcrewai/quant/packet.py`
- Create: `src/stockcrewai/quant/repository.py`
- Test: `tests/test_quant_cli.py`
- Test: `tests/test_quant_repository.py`
- Test: existing `tests/test_quant_*.py`

**Interfaces:**
- Consumes: 显式 universe manifest、本地已验证 SEC/价格历史、`as_of`、回测起止日期和策略版本
- Produces: `QuantResearchPacket` JSON 和 SQLite 索引；不直接修改基本面报告

- [ ] **Step 1: 冻结唯一用户入口契约**

目标命令：

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync \
python -m stockcrewai.quant.cli run \
  --ticker DUK \
  --universe examples/universes/us-large-cap-v1.json \
  --as-of 2026-08-10T00:00:00Z \
  --artifact-root outputs/quant
```

该命令必须执行 snapshot → factor → normalization → ranking → walk-forward → packet，并返回真实 artifact 路径；任何阶段缺数据都返回明确非零退出码和 typed reason，不生成示例值。

- [ ] **Step 2: 先用真实本地缓存数据写端到端失败测试**

测试不得访问网络，不得使用 `aaaa`/`bbbb` 或预填回测结果。输入必须是采集后冻结的真实来源记录，预期由独立手算或第二实现核对。

- [ ] **Step 3: 实现最小 orchestration，不重写现有量化公式**

CLI 只串联已有模块，不增加新策略、不优化参数、不扩展因子数量。

- [ ] **Step 4: 增加 Packet Repository**

SQLite 只保存：

- ticker；
- as_of；
- universe_version；
- strategy_version；
- coverage；
- packet_path；
- packet_hash；
- created_at。

大体积快照和回测序列继续使用稳定 JSON/Parquet artifact，不塞进 SQLite。

- [ ] **Step 5: 运行单一公司真实闭环并人工核对**

验收条件：

- Packet 目标 ticker 与请求一致；
- 所有日期不晚于 as_of；
- peer_count 来自真实 universe；
- 因子值能回到 Evidence/Calculation；
- CAGR、回撤和换手率能回到回测序列；
- 无 synthetic 标志、固定 ID 或测试数值；
- 完整运行时间和失败原因可读。

- [ ] **Step 6: 运行量化完整离线门禁并停止**

```bash
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync pytest -q tests/test_quant_*.py
UV_CACHE_DIR=/private/tmp/stockcrewai-uv-cache uv run --no-sync python -m ruff check src/stockcrewai/quant tests/test_quant_*.py
```

用户检查点：先查看 Packet JSON、同行排名表和回测曲线数据，不生成综合投研报告。

### Task 4: 用真实 Packet 恢复人类可读的量化报告

**Files:**
- Modify: `src/stockcrewai/reporting/renderer.py`
- Modify: `src/stockcrewai/reporting/visuals.py`
- Modify: `src/stockcrewai/flow.py`
- Modify: `src/stockcrewai/main.py`
- Test: `tests/test_quant_report_integration.py`

**Interfaces:**
- Consumes: Repository 返回的验证后 `QuantResearchPacket`
- Produces: 报告中的量化摘要、三张图和技术审计附录

- [ ] **Step 1: 写人类可读报告的失败测试**

正文必须按以下顺序：

1. 一句话量化结论；
2. 公司因子排名与主要贡献；
3. 策略、SPY、同行基准比较；
4. 风险与数据质量；
5. 技术审计附录。

- [ ] **Step 2: 固定正文数字解释**

必须明确：

- rank 的方向；
- score 的范围；
- percentile 的中文含义；
- strategy CAGR 是组合策略历史回测，不是目标公司未来收益；
- 最大回撤相对 SPY/同行基准的差值；
- 年化换手率与交易成本敏感性；
- coverage 和 survivorship bias 的自然语言影响。

- [ ] **Step 3: 主正文只展示缩短后的来源信息**

正文最多展示 `artifact_id` 前 8 位；完整 ID 和字段级追溯仅放技术附录与 JSON。

- [ ] **Step 4: 生成三张决策相关图表**

- 因子得分与同行中位数；
- 策略、SPY、同行基准的累计净值；
- 策略与基准回撤曲线。

图表必须来自真实 Packet 底层序列；如果 Packet 没有净值/回撤序列，应先修正 Packet 契约，不用单个 CAGR 柱状图伪装完整历史。

- [ ] **Step 5: 自动加载最近且不晚于报告 as_of 的 Packet**

Flow 只查询 Repository；不在 `crewai run` 内重新抓整个股票池或运行回测。找不到匹配 Packet 时省略量化章节。

- [ ] **Step 6: 用 DUK 和 AAPL 各运行一次并停止**

验收：

- 报告不包含 synthetic 数据；
- DUK/AAPL 使用各自 Packet；
- 普通读者先看到结论而不是 artifact ID；
- 量化章节不修改 Verdict hash；
- 报告数字逐项等于 Packet。

---

## 4. 最终验收标准

### 4.1 基本面主链路

- `crewai run` 对 AAPL 和 DUK 至少各有一次真实成功记录。
- Profile 专属指标和图表不再被普通公司固定 schema 错误过滤。
- 没有数据的可选章节不会输出内部 reason code 或空模板。
- 报告开头能在一分钟内回答：公司质量如何、估值如何、主要风险是什么、哪些数据不足。
- `run-result.json` 保留机器可读诊断，Markdown 面向普通读者。

### 4.2 量化独立链路

- 存在一条真实可复制命令生成 Packet。
- 股票池、历史成员、as_of、数据覆盖和偏差均明确。
- 因子、排名和回测数值不是 fixture 预填值。
- 真实 Packet 进入报告前通过 Pydantic、来源、日期和 hash 验证。
- 没有 Packet 时不显示量化章节，不使用 fallback。

### 4.3 实习项目展示

面试时应演示两条清晰命令，而不是一个万能但不稳定的命令：

```bash
# 单公司基本面报告
STOCKCREWAI_REQUEST='请分析 Duke Energy Corporation（DUK），投资期限为3到5年' \
uv run --no-sync crewai run

# 独立量化研究批处理
uv run --no-sync python -m stockcrewai.quant.cli run \
  --ticker DUK \
  --universe examples/universes/us-large-cap-v1.json \
  --as-of 2026-08-10T00:00:00Z \
  --artifact-root outputs/quant
```

第一条证明 Agent、SEC、确定性计算、验证和报告能力；第二条证明 point-in-time、横截面因子、回测和可复现 artifact 能力。二者通过验证后的 Packet 连接，但互不拖垮。

---

## 5. 明确禁止事项

- 不再增加 Quant Agent、Planner Agent 或 Manager Agent。
- 不再为了让报告“看起来完整”填充固定 AAPL 数字。
- 不把单个目标公司的财务指标冒充横截面同行排名。
- 不在每次 `crewai run` 中即时下载五年全市场数据并回测。
- 不用更多 Gate、reason code 或字段级 ID 替代用户解释。
- 不在完成真实 Packet 前继续美化 synthetic 量化报告。
- 不把离线测试通过描述成真实 SEC/Yahoo/DeepSeek 全链路通过。
- 不在同一任务中同时修报告、量化、Profile 和网络问题。

## 6. 推荐执行顺序

严格顺序：

```text
Task 0 冻结基线
→ 用户检查
Task 1 修复基本面报告与 DUK 图表
→ 用户检查
Task 2 隔离未完成量化展示
→ 用户检查
Task 3 完成真实量化 Packet 独立闭环
→ 用户检查
Task 4 恢复人类可读量化报告
→ 用户最终验收
```

任何 Task 失败，都停在当前 Task 找根因，不提前开始下一项，也不通过 fallback 绕过。
