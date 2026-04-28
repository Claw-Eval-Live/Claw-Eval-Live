# 🚀 LiveClawEval

<p align="center">
  <strong>面向持续演化真实工作流的 Live Agent Benchmark。</strong>
</p>

<p align="center">
  <em>Seeking alpha tasks from live workflow signals.</em>
</p>

<p align="center">
  • ClawHub 信号驱动 • 季度重校准 • Workflow + Workspace 任务 • 可解释评分 •
</p>

<p align="center">
  <a href="README.md">English</a> •
  <a href="https://liveclaweval.github.io/#/leaderboard">在线排行榜</a> •
  <a href="#-快速开始">快速开始</a> •
  <a href="#-引用">引用</a>
</p>

<p align="center">
  <a href="https://liveclaweval.github.io/#/leaderboard"><img alt="Tasks" src="https://img.shields.io/badge/tasks-105-blue"></a>
  <a href="https://liveclaweval.github.io/#/leaderboard"><img alt="Families" src="https://img.shields.io/badge/families-17-green"></a>
  <a href="https://liveclaweval.github.io/#/leaderboard"><img alt="Models" src="https://img.shields.io/badge/frontier%20models-13-orange"></a>
  <a href="https://liveclaweval.github.io/"><img alt="Leaderboard" src="https://img.shields.io/badge/leaderboard-live-purple"></a>
  <img alt="Refresh" src="https://img.shields.io/badge/refresh-quarterly-00a6ff">
  <a href="LICENSE"><img alt="License" src="https://img.shields.io/badge/license-CC--BY--4.0-yellow"></a>
</p>

> [!IMPORTANT]
> **🧭 从静态 benchmark 到 live workflow evaluation。** 传统智能体基准往往在发布时冻结，随后逐渐偏离真实企业工作流需求。LiveClawEval 基于持续更新的 ClawHub marketplace signals 构建，并按季度重校准任务分布。
>
> **📈 不是拍脑袋造题。** 任务类别与权重来自市场信号、下载量和工作流模式聚类，而不是人工直觉或委员会投票。
>
> **🔍 分数可解释。** 每个任务都基于可观测执行证据评分：状态变化、工具调用、文件修复、结构化报告质量等均可追溯。

![LiveClawEval overview](docs/assets/liveclaweval-overview.png)

## 🏆 排行榜

按 **Overall Completion Score** 对 105 个任务、13 个 frontier models 排名。

| # | Model | Org | Overall |
|---:|---|---|---:|
| 1 | Claude Opus 4.6 | Anthropic | 83.6 |
| 2 | GPT-5.4 | OpenAI | 81.7 |
| 3 | Claude Sonnet 4.6 | Anthropic | 79.9 |
| 4 | GLM-5 | Zhipu AI | 78.1 |
| 5 | MiniMax M2.7 | MiniMax | 77.5 |
| 6 | MiMo V2 Pro | Xiaomi | 76.9 |
| 7 | Kimi K2.5 | Moonshot AI | 76.2 |
| 8 | Gemini 3.1 Pro | Google | 74.0 |
| 9 | Qwen 3.5 397B | Alibaba | 72.7 |
| 10 | Qwen 3.6 Plus | Alibaba | 71.4 |
| 11 | MiniMax M2.5 | MiniMax | 70.9 |
| 12 | Doubao Seed 2.0 Pro | ByteDance | 70.4 |
| 13 | DeepSeek V3.2 | DeepSeek | 69.3 |

🌐 完整热力图、雷达图、任务级分数见：[liveclaweval.github.io/#/leaderboard](https://liveclaweval.github.io/#/leaderboard)

## 🎬 图示

| Signal-to-Snapshot Pipeline | Family Heatmap | Workflow vs. Workspace |
|---|---|---|
| ![Signal-to-snapshot pipeline](docs/assets/signal-to-snapshot.png) | ![Family heatmap](docs/assets/family-heatmap.png) | ![Workflow vs workspace](docs/assets/workflow-vs-workspace.png) |
| 从 ClawHub 市场信号生成可发布任务快照。 | 不同模型在不同任务族上的完成度差异明显。 | 覆盖服务型 workflow 与 workspace repair 两类典型智能体任务。 |

## 🧩 任务组成

105 个任务覆盖 17 个 family，权重由 ClawHub 需求信号驱动。

| Family | # | Family | # | Family | # |
|---|---:|---|---:|---|---:|
| SHELL | 18 | SUPPORT | 5 | IR | 4 |
| PRODAPP | 17 | WORKFLOW | 5 | MGMT | 4 |
| DATA | 9 | CRM | 4 | PROD | 4 |
| HR | 9 | DOC | 4 | RESEARCH | 3 |
| SALES | 6 | FIN | 4 | OPS | 3 |
| COMM | 5 | | | SEC | 1 |

每个任务包含 `task.yaml`、`grader.py` 和 `fixtures/`，用于复现 prompt、服务状态、评分逻辑和执行证据。

## ⚡ 快速开始

```bash
git clone https://github.com/LiveClawEval/LiveClawEval.git
cd LiveClawEval
pip install -e .

liveclaw-500 list --tasks-dir tasks

liveclaw-500 run \
  --task tasks/CTB_HR_01_onboarding_checklist \
  --config model_configs/claude_opus_46.yaml \
  --trace-dir traces/
```

从 `config_template.yaml` 复制一份模型配置，然后填写 provider 的 `api_key`、`base_url` 和 `model_id`。

## 📖 引用

```bibtex
@misc{liveclaweval2026,
  title  = {LiveClawEval: A Live Agent Benchmark for Evolving Real-World Workflow},
  author = {LiveClawEval Team},
  year   = {2026},
  url    = {https://liveclaweval.github.io/}
}
```

## 📄 License

本项目采用 [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) 许可证。详见 [LICENSE](LICENSE)。
