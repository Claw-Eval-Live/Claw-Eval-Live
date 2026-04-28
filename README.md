# 🚀 LiveClawEval

<p align="center">
  <strong>A Live Agent Benchmark for Evolving Real-World Workflow.</strong>
</p>

<p align="center">
  <em>Seeking alpha tasks from live workflow signals.</em>
</p>

<p align="center">
  • ClawHub-grounded Tasks • Quarterly Re-calibration • Workflow + Workspace Evaluation • Explainable Scores •
</p>

<p align="center">
  <a href="README.zh-CN.md">中文文档</a> •
  <a href="#-leaderboard">Leaderboard</a> •
  <a href="#-visual-tour">Visual Tour</a> •
  <a href="#-tasks">Tasks</a> •
  <a href="#-quick-start">Quick Start</a> •
  <a href="#-citation">Citation</a>
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
> **🧭 From static benchmarks to live workflow evaluation.** Most agent benchmarks freeze at publication time. LiveClawEval is built from continuously updated ClawHub marketplace signals, so the benchmark tracks evolving enterprise workflow demand instead of a one-off task snapshot.
>
> **📈 Signal-to-task, not intuition-to-task.** We derive task families and weights from marketplace signals, re-cluster workflow patterns, and refresh the public snapshot quarterly.
>
> **🔍 Explainable scores.** Each task is graded from observable execution evidence: deterministic checks for state changes and tool use, structured LLM judging for report-style outputs, and script-first verification for terminal/workspace tasks.

![LiveClawEval overview](docs/assets/liveclaweval-overview.png)

## 🧭 Quick Navigation

> [!TIP]
> **I am Human** → continue with this README for the leaderboard, visual tour, quick start, and citation.
>
> **I am Agent** → read [AGENTS.md](AGENTS.md) for structured operating instructions, key files, and command quick reference.

`LiveClawEval` evaluates AI agents on realistic multi-step enterprise workflows. Agents interact with controlled services, workspace files, and task-specific fixtures; scores reflect whether they actually completed the workflow, not just whether they produced plausible text.

- **For live benchmark research**: task distribution is tied to changing marketplace demand.
- **For agent evaluation**: tasks require planning, service interaction, file/workspace repair, and structured outputs.
- **For reproducibility**: every released task includes fixtures, graders, and model configs.

**Status:** public release · `105` tasks · `17` families · `13` frontier models.

## 🏆 Leaderboard

Ranked by **Overall Completion Score** across 105 tasks and 13 frontier models.

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

🌐 **Live table, family heatmap, radar view, and per-task scores:** [liveclaweval.github.io/#/leaderboard](https://liveclaweval.github.io/#/leaderboard)

## 🎬 Visual Tour

| Signal-to-Snapshot Pipeline | Family Heatmap | Workflow vs. Workspace |
|---|---|---|
| ![Signal-to-snapshot pipeline](docs/assets/signal-to-snapshot.png) | ![Family heatmap](docs/assets/family-heatmap.png) | ![Workflow vs workspace](docs/assets/workflow-vs-workspace.png) |
| Marketplace signals are converted into a refreshed task snapshot. | Completion patterns vary sharply across families and models. | LiveClawEval covers both service-backed workflow tasks and workspace-repair tasks. |

| Family Distribution | Interactive Site |
|---|---|
| ![Family distribution](docs/assets/family-distribution.png) | [![Leaderboard live](https://img.shields.io/badge/open-live%20leaderboard-purple?style=for-the-badge)](https://liveclaweval.github.io/#/leaderboard) |
| The public release is weighted by ClawHub-derived workflow demand. | The website exposes heatmap, radar, ranking, and task-level evidence views. |

## ✨ Features

LiveClawEval focuses on evaluation scenarios that static QA-style benchmarks miss:

- **📡 Market-grounded construction** — task families are derived from ClawHub demand signals.
- **🔁 Quarterly refresh** — the benchmark is designed to evolve as enterprise workflows evolve.
- **🧰 Multi-surface execution** — agents operate across mock enterprise services, documents, files, and terminal/workspace environments.
- **🧪 Hybrid grading** — deterministic checks, script validation, and structured LLM judging are combined per task.
- **📊 Family-level diagnosis** — leaderboard views include heatmap/radar analysis by task family, not just one aggregate score.

## 🧩 Tasks

105 tasks across 17 families, weighted by ClawHub demand signal.

| Family | # | Family | # | Family | # |
|---|---:|---|---:|---|---:|
| SHELL | 18 | SUPPORT | 5 | IR | 4 |
| PRODAPP | 17 | WORKFLOW | 5 | MGMT | 4 |
| DATA | 9 | CRM | 4 | PROD | 4 |
| HR | 9 | DOC | 4 | RESEARCH | 3 |
| SALES | 6 | FIN | 4 | OPS | 3 |
| COMM | 5 | | | SEC | 1 |

Each released task includes:

- `task.yaml` — prompt, services, fixtures, and task metadata.
- `grader.py` — deterministic checks plus optional judge-backed semantic scoring.
- `fixtures/` — mock service state, attachments, sandbox files, and expected evidence.

## ⚡ Quick Start

```bash
# 1. Clone and install
git clone https://github.com/LiveClawEval/LiveClawEval.git
cd LiveClawEval
pip install -e .

# 2. List tasks
liveclaw-500 list --tasks-dir tasks

# 3. Run a single task
liveclaw-500 run \
  --task tasks/CTB_HR_01_onboarding_checklist \
  --config model_configs/claude_opus_46.yaml \
  --trace-dir traces/

# 4. Batch run
liveclaw-500 batch \
  --tasks-dir tasks \
  --config model_configs/claude_opus_46.yaml \
  --parallel 4

# 5. Re-grade an existing trace
liveclaw-500 grade \
  --trace traces/your_trace.jsonl \
  --task tasks/CTB_HR_01_onboarding_checklist \
  --config model_configs/claude_opus_46.yaml
```

Start from `config_template.yaml`, then fill in your provider `api_key`, `base_url`, and `model_id` in a model config.

## 🗂️ Repository Layout

```text
LiveClawEval/
├── README.md
├── README.zh-CN.md
├── LICENSE
├── docs/assets/             # README and paper-style figures
├── tasks/                   # 105 released tasks
├── mock_services/           # Controlled enterprise-style services
├── model_configs/           # Provider configs
├── benchmark/               # Released results and leaderboard assets
├── scripts/                 # Regrading / aggregation utilities
├── src/liveclaw_500/        # Evaluation framework and CLI
└── config_template.yaml
```

## 🧪 Why This Exists

Enterprise agent workloads move quickly: new tool-use patterns appear, older workflows flatten, and the tasks people pay for change. A benchmark frozen once cannot faithfully represent that moving target. LiveClawEval is an attempt to make agent evaluation *live*: grounded in fresh demand signals, refreshed on a schedule, and transparent enough that researchers can inspect how every score was produced.

## 📚 Docs & Links

- 🌐 Website and leaderboard: [liveclaweval.github.io](https://liveclaweval.github.io/)
- 📊 Leaderboard route: [liveclaweval.github.io/#/leaderboard](https://liveclaweval.github.io/#/leaderboard)
- 🇨🇳 中文 README: [README.zh-CN.md](README.zh-CN.md)
- 🧾 License: [CC BY 4.0](LICENSE)

## ⭐ Star History

<a href="https://star-history.com/#LiveClawEval/LiveClawEval&Date">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=LiveClawEval/LiveClawEval&type=Date&theme=dark" />
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=LiveClawEval/LiveClawEval&type=Date" />
    <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=LiveClawEval/LiveClawEval&type=Date" />
  </picture>
</a>

## 📌 Roadmap

- [ ] Q2 2026 refresh: next ClawHub signal re-ingestion and task-family reweighting.
- [ ] Public dataset mirror.
- [ ] Multi-turn extension with simulated user personas.
- [ ] arXiv preprint link and camera-ready BibTeX.
- [ ] Short demo GIFs for the website and README.

## 📖 Citation

```bibtex
@misc{liveclaweval2026,
  title  = {LiveClawEval: A Live Agent Benchmark for Evolving Real-World Workflow},
  author = {LiveClawEval Team},
  year   = {2026},
  url    = {https://liveclaweval.github.io/}
}
```

## 📄 License

Released under the [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) license. See [LICENSE](LICENSE) for details.

---

<p align="center">
  If LiveClawEval helps your agent research, please give it a ⭐ Star!
</p>

<p align="center">
  <a href="https://github.com/LiveClawEval/LiveClawEval/issues">Report Issues</a> ·
  <a href="https://github.com/LiveClawEval/LiveClawEval/issues/new?labels=enhancement">Feature Requests</a> ·
  <a href="https://liveclaweval.github.io/#/leaderboard">Live Leaderboard</a>
</p>
