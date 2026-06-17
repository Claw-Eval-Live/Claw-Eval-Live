# Signal-to-Benchmark Pipeline: 标准流程方法论

## 1. 这份文档的作用

这不是一次性的分析报告，而是一套**可复现、可审计、可版本化**的标准流程。

它回答的核心问题是：

> 给定一个公开的 skill/workflow 生态，如何系统地从中推导出 benchmark 应该测什么、侧重什么、什么时候需要更新。

任何人拿到同样的信号源，按同样的流程执行，应该能得到相似的结论。

---

## 2. 流程总览

```
阶段 1          阶段 2              阶段 3            阶段 4             阶段 5
信号采集    →   workflow 聚类    →  Family 权重   →  覆盖缺口分析   →  benchmark 结构决策
                                                                          ↓
                                                                    Core/Live release
                                                                          ↓
                                                                    下一季度重跑 1-4
```

每个阶段都有：
- 明确的**输入**
- 可执行的**方法**（脚本或人工步骤）
- 可审计的**输出**（CSV / MD 文件）
- 明确的**局限性说明**

---

## 3. 阶段 1：信号采集

### 输入

公开 skill / workflow 生态的 API 端点。当前使用：
- ClawHub public API: `https://clawhub.ai/api/search?q={query}`
- ClawHub skill detail: `https://clawhub.ai/api/skill?slug={slug}`

### 方法

1. 预定义一组覆盖主要电脑任务领域的搜索关键词（当前 109 个）
2. 对每个关键词调用搜索 API，收集唯一 skill slug
3. 对每个 slug 调用详情 API，获取 downloads / stars / installs / summary
4. 按 downloads 排序，取 Top N（当前 N=500）

### 脚本

```bash
python scripts/fetch_skill_signals.py --top 500
```

### 输出

- `benchmark/signals/clawhub_top_500.csv`

### 可审计性

- 同一组关键词 + 同一天拉取 = 同一份结果
- 关键词列表在脚本中硬编码，可追溯
- CSV 中每行包含 slug / name / downloads / stars / summary

### 局限性

- 搜索 API 是语义搜索，每次最多返回 10 条，不支持分页
- 依赖关键词覆盖度，可能遗漏小众但重要的 workflow
- downloads ≠ 重要性（流行不等于复杂）
- 仅覆盖 ClawHub 生态，不代表所有 agent 使用场景

### 扩展建议

- 定期重跑（建议每季度），观察趋势变化
- 后续可接入 ClawSkillStore、SkillzWave 等其他信号源
- 可补充内部数据（agent 日志、用户反馈）作为私有信号层

---

## 4. 阶段 2：Workflow Pattern 聚类

### 输入

阶段 1 的 `clawhub_top_500.csv`

### 方法

1. 预定义一组 **Workflow Pattern**（当前 33 个）
2. 每个 Pattern 有：
   - 一组匹配关键词
   - 一个人类可读标签
   - 映射到的 Family
3. 对每个 skill，用 name + summary 的文本匹配关键词
4. 一个 skill 可以匹配多个 pattern（多分类）
5. 未匹配任何 pattern 的 skill 标记为 `uncategorized`

### 33 个 Workflow Pattern（按 Family 分组）

#### Document-Transform
| Pattern | 关键词示例 | 说明 |
|---------|-----------|------|
| doc_summarization | summarize, digest, tldr | 长文档摘要 |
| pdf_processing | pdf | PDF 读写编辑 |
| format_conversion | markdown convert, docx, xlsx | 格式互转 |
| ocr_extraction | ocr, extract text | OCR 与文本提取 |
| content_rewriting | humanize, rewrite, proofread | 内容改写润色 |
| media_processing | video, audio, image, ffmpeg | 多媒体处理 |
| translation | translat, locali, i18n | 翻译与本地化 |

#### Research-to-Artifact
| Pattern | 关键词示例 | 说明 |
|---------|-----------|------|
| web_research | research, competitive analysis | 深度调研 |
| web_search | duckduckgo, tavily, serp | 搜索工具 |
| news_monitoring | news, rss, feed, alert | 新闻监控 |
| academic_research | arxiv, paper, citation | 学术调研 |
| browser_automation | browser, scraping, crawl | 浏览器自动化 |

#### Cross-Tool-Orchestration
| Pattern | 关键词示例 | 说明 |
|---------|-----------|------|
| workflow_automation | workflow, n8n, zapier | 流程编排 |
| calendar_scheduling | calendar, schedule, meeting | 日程管理 |
| task_management | todoist, jira, kanban | 任务管理 |
| note_knowledge | notion, obsidian, wiki | 笔记知识库 |
| multi_service_integration | integration, sync, webhook | 多服务集成 |

#### Data-Analysis-Reporting
| Pattern | 关键词示例 | 说明 |
|---------|-----------|------|
| data_analysis | data analy, visualization, chart | 数据分析可视化 |
| financial_analysis | stock, crypto, portfolio | 金融分析 |
| spreadsheet_database | sql, database, postgres | 表格数据库 |
| accounting_expense | invoice, expense, budget | 财务记账 |

#### Workspace-Repair-Config
| Pattern | 关键词示例 | 说明 |
|---------|-----------|------|
| debugging | debug, troubleshoot, fix bug | 调试排障 |
| devops_infra | docker, kubernetes, deploy | 运维基础设施 |
| git_version_control | git, github, pull request | 版本控制 |
| shell_terminal | shell, terminal, bash, cli | 终端操作 |
| file_system | file manag, backup, archive | 文件管理 |
| config_management | config, env, dotfile | 配置管理 |
| coding_tools | code review, refactor, testing | 编码工具 |

#### Communication-Drafting
| Pattern | 关键词示例 | 说明 |
|---------|-----------|------|
| email_management | email, gmail, inbox | 邮件管理 |
| messaging_chat | slack, discord, telegram | 消息平台 |
| marketing_content | marketing, seo, campaign | 营销内容 |
| crm_sales | crm, sales, lead | 客户销售 |

### 脚本

```bash
python scripts/derive_benchmark_weights.py --input benchmark/signals/clawhub_top_500.csv
```

### 输出

- `benchmark/signals/workflow_patterns.csv`

### 可审计性

- Pattern 定义在脚本中硬编码，可追溯
- 每个 skill 的匹配结果可以逐条检查
- uncategorized 的 skill 单独列出，可人工审核补充

### 局限性

- 关键词匹配可能遗漏语义相近但用词不同的 skill
- 一些 skill 横跨多个 pattern，下载量会被重复计算
- 建议后续补充 LLM-based 语义聚类做交叉验证

---

## 5. 阶段 3：Family 权重推导

### 输入

阶段 2 的 `workflow_patterns.csv`

### 方法

1. 每个 Workflow Pattern 已映射到一个 Family
2. 每个 Family 的权重 = 该 Family 下所有 pattern 的 skill downloads 总和 / 全量 downloads 总和
3. 输出按权重降序排列

### 公式

```
Family_Weight(F) = Σ downloads(skills in patterns mapped to F) / Σ downloads(all skills)
```

### 首次运行结果（2026-03-14）

| Family | 下载量 | 市场权重 |
|--------|--------|---------|
| Workspace-Repair-Config | 1,433,104 | 27.4% |
| Document-Transform | 1,165,154 | 22.3% |
| Cross-Tool-Orchestration | 1,026,411 | 19.6% |
| Research-to-Artifact | 619,482 | 11.8% |
| Data-Analysis-Reporting | 546,468 | 10.5% |
| Communication-Drafting | 365,368 | 7.0% |

### 输出

- `benchmark/signals/family_weights.csv`

### 关键发现

- **Workspace-Repair-Config 是市场权重最高的 Family（27.4%）**
  这说明真实用户对 shell/终端/DevOps/配置管理/调试 的需求远超 benchmark 社区的直觉
- Document-Transform（22.3%）和 Cross-Tool-Orchestration（19.6%）紧随其后
- Research-to-Artifact（11.8%）虽然是典型 agent 叙事，但市场权重没有想象中高
- Communication-Drafting（7.0%）虽然权重最低，但代表极高频日常场景

---

## 6. 阶段 4：覆盖缺口分析

### 输入

- 阶段 3 的 Family 权重
- 当前 benchmark（claw-eval）的任务分布

### 方法

1. 计算每个 Family 的 current_ratio = 当前题数 / 总题数
2. gap = market_weight - current_ratio
3. 按 gap 大小决定优先级：
   - gap > +5%：**PRIORITY** — 严重不足，优先补题
   - gap > +2%：**ADD** — 适度补充
   - gap < -5%：**REDUCE** — 过多，精选最优
   - gap < -2%：**TRIM** — 略多
   - 其余：**OK**

### 首次运行结果（2026-03-14）

| Family | 市场权重 | 当前占比 | 缺口 | 现有 | 建议(72题) | 行动 |
|--------|---------|---------|------|------|-----------|------|
| Workspace-Repair-Config | 27.4% | 7.7% | +19.7% | 8 | 20 | **严重不足** |
| Document-Transform | 22.3% | 17.3% | +5.0% | 18 | 16 | 适度补充 |
| Cross-Tool-Orchestration | 19.6% | 15.4% | +4.2% | 16 | 14 | 适度补充 |
| Communication-Drafting | 7.0% | 3.8% | +3.1% | 4 | 5 | 适度补充 |
| Data-Analysis-Reporting | 10.5% | 23.1% | -12.6% | 24 | 8 | **过多，精选** |
| Research-to-Artifact | 11.8% | 32.7% | -20.8% | 34 | 9 | **过多，精选** |

### 输出

- `benchmark/signals/coverage_gap.csv`
- `benchmark/signals/pipeline_report.md`

### 核心结论

**claw-eval 的任务分布与市场需求严重错配：**

- Workspace-Repair-Config 应该是最大的 Family（27.4%），但现在只有 7.7%
- Research-to-Artifact 应该只占 11.8%，但现在占了 32.7%

这不是 claw-eval 的"错误"，因为它有自己的设计目标。但对 Computer Task Benchmark 来说，如果想贴近真实市场需求，任务分布应该做出显著调整。

---

## 7. 阶段 5：Benchmark 结构决策

### 输入

阶段 4 的覆盖缺口分析

### 方法

1. 按 Family 权重分配 Core 集的目标题数
2. 对于已有题目过多的 Family：精选最优，不再新增
3. 对于缺口最大的 Family：优先新建任务
4. 对于 Live 更新：每季度重跑阶段 1-4，根据新的 gap 决定方向

### 输出

- `releases/core_dev_v{version}/manifest.yaml`
- `releases/core_dev_v{version}/weight_justification.md`

---

## 8. 季度更新操作手册

每季度（或每个 Live release 前），执行以下步骤：

```bash
# 1. 重新采集信号
python scripts/fetch_skill_signals.py --top 500 \
    --output benchmark/signals/clawhub_top_500_$(date +%Y%m%d).csv

# 2. 重新计算权重和缺口
python scripts/derive_benchmark_weights.py \
    --input benchmark/signals/clawhub_top_500_$(date +%Y%m%d).csv \
    --output-dir benchmark/signals

# 3. 对比上一季度的 family_weights.csv，观察趋势变化

# 4. 根据新的 gap 决定 Live 新增方向

# 5. 新建任务 → 审核 → 发布
```

### 趋势追踪

建议维护一份 `benchmark/signals/weight_history.csv`：

| date | family | market_weight | current_ratio | gap |
|------|--------|--------------|---------------|-----|
| 2026-03-14 | Workspace-Repair-Config | 27.4% | 7.7% | +19.7% |
| 2026-06-15 | Workspace-Repair-Config | 25.1% | 20.0% | +5.1% |
| ... | ... | ... | ... | ... |

这样你可以看到：
- 哪些 Family 的市场需求在增长
- 你的补题动作有没有缩小 gap
- Live 更新的方向是否在跟上市场变化

---

## 9. 阶段 5 补充：从覆盖缺口到选题的标准流程

### 输入

- 阶段 4 的 `coverage_gap.csv`
- `task_audit_v0.1.md` 中 can_use=yes 的题池

### 方法

#### Step 1: 计算目标题数

```
每个 Family 的目标题数 = round(总题数 × market_weight)
约束：min=2, max=总题数/3
```

#### Step 2: 从现有题池选题

对每个 Family：
1. 筛出 can_use=yes 的题
2. 按以下标准排序（优先级从高到低）：
   - 使用 ≥3 个服务/工具的题优先
   - 有 safety_checks 的题优先
   - 有错误恢复场景的题优先
   - 中英文配对的题优先（一题两用）
3. 取 top N

#### Step 3: 识别需要新建的题

如果某个 Family 的现有可用题不够分配数 → 标记差额为"新建"。

新建题的设计依据：
- 回到阶段 2 的 `workflow_patterns.csv`
- 找到该 Family 下下载量最高但尚未被覆盖的 pattern
- 基于该 pattern 设计新任务

#### Step 4: 输出 manifest

```yaml
# releases/core_dev_v{version}/manifest.yaml
version: "0.1"
families:
  - family: Workspace-Repair-Config
    allocated: 8
    tasks:
      - task_id: T27zh_api_config_audit
        source: existing
        reason: ...
      - task_id: CTB_W01_log_diagnosis
        source: new
        reason: |
          市场信号：debugging (85k downloads)
          workflow: ...
```

每道题都写明 source（现有/新建）和 reason（为什么选它），让选题过程完全可审计。

### 输出

- `releases/core_dev_v{version}/manifest.yaml`
- `releases/core_dev_v{version}/selection_methodology.md`

---

## 10. 阶段 6：Baseline 实验与校准

### 目的

验证 Core-Dev 选出来的题是否能拉开模型差距、verifier 是否稳定。

### 输入

- `releases/core_dev_v0.1/manifest.yaml`
- 模型配置（`config.local.yaml` 或 `model_configs/*.yaml`）

### 方法

#### Step 1: Quick Validation（一轮 one-per-family）

每个 Family 跑 1 题，验证端到端链路通不通。

```bash
python scripts/run_core_dev.py --one-per-family --config config.local.yaml --no-judge
```

预期：每题能跑完出分，不报基础设施错误。

#### Step 2: Full Baseline（全量 Core-Dev）

对 3-5 个能力梯度不同的模型，分别跑全量 Core-Dev。

```bash
# 模型 A
python scripts/run_core_dev.py --config model_configs/model_a.yaml \
    --output benchmark/baselines/model_a.json

# 模型 B
python scripts/run_core_dev.py --config model_configs/model_b.yaml \
    --output benchmark/baselines/model_b.json
```

#### Step 3: 稳定性验证

同一模型跑 3 次，检查分数方差。

#### Step 4: 分析与校准

从 baseline 结果中识别：
- 所有模型都满分的题 → 太简单，考虑移到 warmup
- 所有模型都零分的题 → 可能太难或 verifier 有 bug
- 方差太大的题 → verifier 不稳定，需要修
- Family 间区分度是否符合预期

### 输出

- `benchmark/baselines/run_{timestamp}.json` — 每次运行结果
- `benchmark/baselines/baseline_report.md` — 分析报告

### 脚本

```bash
# Dry run（不消耗 API 额度）
python scripts/run_core_dev.py --dry-run

# Quick validation
python scripts/run_core_dev.py --one-per-family --config config.local.yaml --no-judge

# Full baseline
python scripts/run_core_dev.py --config config.local.yaml --no-judge
```

---

## 11. 整个 pipeline 的文件结构

```
benchmark/
├── signals/
│   ├── clawhub_top_500.csv              # 阶段 1 输出
│   ├── workflow_patterns.csv            # 阶段 2 输出
│   ├── family_weights.csv              # 阶段 3 输出
│   ├── coverage_gap.csv                # 阶段 4 输出
│   ├── pipeline_report.md             # 阶段 2-4 汇总报告
│   └── weight_history.csv             # 趋势追踪（逐季度累积）
├── releases/
│   └── core_dev_v0.1/
│       ├── manifest.yaml              # 阶段 5 输出：选题清单
│       └── selection_methodology.md   # 选题方法论
├── baselines/
│   ├── one_per_family_demo.json       # 阶段 6 Quick Validation
│   └── run_{timestamp}.json           # 阶段 6 Full Baseline
├── benchmark_charter_v0.1.md          # benchmark 协议
├── task_audit_v0.1.md                 # 现有任务盘点
└── signal_pipeline_methodology.md     # 本文档

scripts/
├── fetch_skill_signals.py             # 阶段 1 脚本
├── derive_benchmark_weights.py        # 阶段 2-4 脚本
└── run_core_dev.py                    # 阶段 6 脚本
```

---

## 10. 这套流程的核心价值

> 它不是一次性分析，而是一个**持续运行的 benchmark 校准系统**。

传统 benchmark 的问题是：作者凭经验选题 → 冻结 → 发论文 → 逐渐过时。

这套流程的意义是：

1. **有数据支撑**：benchmark 侧重什么不是拍脑袋，而是由市场信号推导
2. **可复现**：另一个团队拿到同样的脚本和信号源，能得到相似结论
3. **可审计**：每一步都有中间产出物，可以被质疑和改进
4. **可持续**：每季度重跑就能发现新的趋势和缺口
5. **可版本化**：每次运行的结果都存档，形成历史趋势

这就是 `Computer Task Benchmark` 区别于静态 benchmark 的方法论核心。
