# Task Seed Generation Methodology v0.1

这份文档定义的不是 runnable task，而是 `market signal -> task seed` 这一层。

核心定位：

- `claw-eval` 是 execution engine，不是题目来源
- benchmark 的可信度，首先来自 seed 的来源可追溯
- 我们要回答的问题不是“这题像不像现有 benchmark”
- 而是“为什么这道题值得被生成、值得被实现、值得被保留”

## 1. 新的主叙事

外部看到的核心贡献应该是：

`自动搜索 -> 自动调研 -> workflow 抽象 -> task seed bank -> candidate tasks -> smoke / retention`

不是：

`从某个已有 benchmark 里挑一批题`

因此，legacy task 库最多只承担三个角色：

- execution reference：参考 `task.yaml / grader.py / fixtures` 怎么写
- baseline reference：对比新题和旧题的稳定性与难度
- regression reference：验证 runner 没坏

它不再是 task seed 的主要来源。

## 2. Seed 层到底是什么

一个 seed 不是最终题目，而是一个“值得展开”的工作流原型。

它至少包含：

- 来自哪个 workflow pattern
- 对应哪个 family
- 市场强度如何
- 推荐先发散多少个 candidate ideas
- 适合什么环境
- 典型 artifact 是什么
- 1-2 条 candidate task frame

一句话：

`seed = 被市场 signal 证明值得展开的一类任务原型`

注意：

- seed 层可以先保持 market-pure
- 不要求每个 seed 当下都能被当前 engine 立刻实现
- 真正进入 runnable task 层时，再根据环境与 verifier 约束做实现筛选

否则会出现一个反向问题：

`因为当前 engine 里有什么，就只生成什么`

这会让 benchmark 再次被现有题库或现有工具能力绑住。

## 3. 标准流程

### 阶段 A：市场信号

输入：

- `benchmark/signals/workflow_patterns.csv`
- `benchmark/signals/family_weights.csv`

含义：

- family weight 决定大方向该投多少 seed budget
- pattern downloads 决定同一 family 里，先在哪些 pattern 上发散

### 阶段 B：自动产出 seed bank

脚本：

```bash
python3 scripts/build_task_seed_bank.py
```

输出：

- `benchmark/seeds/market_task_seed_bank_v0.1.csv`
- `benchmark/seeds/market_task_seed_bank_v0.1.md`

这一步只做一件事：

把 market signal 转成结构化的 task seed inventory。

### 阶段 C：LLM 扩 seed

对每个 seed：

- 先扩 `5-8` 个 candidate task ideas
- 每个 idea 只写一句 workflow、artifact、environment

这里的 LLM 作用是“发散候选”，不是直接产出最终 benchmark task。

### 阶段 D：初筛 candidate ideas

每个 idea 先过四个 hard checks：

- workflow 是否复合
- artifact 是否清楚
- verifier 是否可能写出来
- 预计难度是否不是一眼过 / 一眼全挂

每个 seed 只保留 top `2-3` 个 candidate，进入实现层。

### 阶段 E：实现 runnable task

为保留下来的 candidate 写：

- `task.yaml`
- `fixtures`
- `grader.py`

这一步才允许引入 implementation filter，例如：

- 当前 mock service 是否支持
- 当前 local workspace 是否足以近似
- verifier 能否在现有框架内落地

### 阶段 F：实跑验证

只要一实现，就必须做：

- smoke run
- execution stability 检查
- discrimination / retention 检查

## 4. 为什么这种方法更可信

因为它形成了一条完整证据链：

1. `这个 seed 为什么存在`
   因为某个 workflow pattern 在市场 signal 中强

2. `为什么先展开它`
   因为它在 family 内部强度更高，且 family 本身权重更高

3. `为什么这个 candidate 被实现`
   因为它通过了 artifact / verifier / difficulty 的初筛

4. `为什么这个 task 被保留`
   因为它实际跑过，且稳定、有区分度、不是玄学题

所以，别人信任的不是某个 prompt 本身，而是：

`prompt 背后的生成链路和筛选链路`

## 5. 当前建议的最小实践

对每个 seed：

- candidate idea budget: `5-8`
- implementation budget: `2-3`
- final kept tasks: `1-2`

这能兼顾：

- 足够发散，不容易全军覆没
- 不至于一上来实现成本爆炸

## 6. 对外表述建议

建议对外说：

`We use claw-eval as the execution backend, but our main contribution is a market-signal-driven pipeline that generates and validates task seeds before task implementation.`

不建议对外说：

`We built a new benchmark by curating tasks from claw-eval.`
