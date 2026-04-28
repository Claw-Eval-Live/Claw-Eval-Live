<div align="center">

# LiveClawEval

### *A Live Agent Benchmark for Evolving Real-World Workflow*

[![Tasks](https://img.shields.io/badge/tasks-105-blue)](https://liveclaweval.github.io/#/leaderboard)
[![Families](https://img.shields.io/badge/families-17-green)](https://liveclaweval.github.io/#/leaderboard)
[![Models](https://img.shields.io/badge/models-14-orange)](https://liveclaweval.github.io/#/leaderboard)
[![Leaderboard](https://img.shields.io/badge/leaderboard-live-purple)](https://liveclaweval.github.io/)
[![License](https://img.shields.io/badge/license-CC--BY--4.0-yellow)](./LICENSE)

> **A living benchmark grounded in continuously updated real-world ClawHub marketplace signals — re-calibrated quarterly, so the task distribution keeps tracking what enterprises actually want agents to do.**

</div>

---

## Why LiveClawEval

Most agent benchmarks freeze at publication and quietly drift away from real-world workflow demand. LiveClawEval takes a different bet:

- **Grounded in market signals.** Tasks are derived from continuously updated ClawHub marketplace signals — top skills ranked by download volume — not from author intuition or committee vote.
- **Re-calibrated every quarter.** An automated signal-to-task pipeline re-ingests fresh signals, re-clusters workflow patterns, and regenerates the task set, so family weights track current demand instead of last year's snapshot.
- **Fully explainable scoring.** Rule-based extraction handles deterministic checks; structured LLM-as-judge handles report-style outputs. Every score is traceable.
- **Built for multi-step workflow.** Agents interact with mock enterprise services and are evaluated on operational correctness *and* output quality — not single-turn QA.

> 🌐 Interactive leaderboard, family heatmap, and per-task scores: **[liveclaweval.github.io](https://liveclaweval.github.io/)**

---

## Leaderboard

Ranked by **Overall Completion Score** across 105 tasks · 14 frontier models.

| # | Model | Org | Overall |
|---|---|---|---:|
| 1 | Claude Opus 4.6 | Anthropic | 83.7 |
| 2 | GPT-5.4 | OpenAI | 81.8 |
| 3 | Claude Sonnet 4.6 | Anthropic | 79.9 |
| 4 | GLM-5 | Zhipu AI | 78.1 |
| 5 | MiniMax M2.7 | MiniMax | 77.6 |
| 6 | MiMo V2 Pro | Xiaomi | 77.0 |
| 7 | Kimi K2.5 | Moonshot AI | 76.2 |
| 8 | Gemini 3.1 Pro | Google | 74.1 |
| 9 | Qwen 3.5 397B | Alibaba | 72.6 |
| 10 | Qwen 3.6 Plus | Alibaba | 71.4 |
| 11 | MiniMax M2.5 | MiniMax | 71.0 |
| 12 | Doubao Seed 2.0 Pro | ByteDance | 70.6 |
| 13 | DeepSeek V3.2 | DeepSeek | 69.1 |
| 14 | Grok 4.20 | xAI | — |

> Live results, family-level breakdowns, and heatmap / radar views: [liveclaweval.github.io/#/leaderboard](https://liveclaweval.github.io/#/leaderboard)

---

## Tasks

105 tasks across 17 families, weighted by ClawHub demand signal.

| Family | # | Family | # | Family | # |
|---|---:|---|---:|---|---:|
| SHELL | 18 | SUPPORT | 5 | IR | 4 |
| PRODAPP | 17 | WORKFLOW | 5 | MGMT | 4 |
| DATA | 9 | CRM | 4 | PROD | 4 |
| HR | 9 | DOC | 4 | RESEARCH | 3 |
| SALES | 6 | FIN | 4 | OPS | 3 |
| COMM | 5 | | | SEC | 1 |

Each task ships with:
- `task.yaml` — prompt, services, evaluation rubric
- `grader.py` — deterministic checks + judge-driven rubric
- `fixtures/` — mock service state, attachments, sandbox files

### Scoring

Hybrid by design:

- **Rule-based extraction** for required tool calls, state changes, and factual correctness.
- **Structured LLM-as-judge** for report-style outputs against an explicit rubric.
- **Script-first verification** for terminal- and workspace-heavy tasks.

Per-task scores live in `[0, 1]`; the family- and overall-level numbers on the leaderboard are simple unweighted means within each scope.

---

## Repository Layout

```
LiveClawEval/
├── README.md
├── LICENSE                 # CC BY 4.0
├── pyproject.toml
├── config_template.yaml
├── tasks/                  # 105 published tasks (task.yaml + grader.py + fixtures)
├── mock_services/          # Mock enterprise backends used by tasks
├── model_configs/          # Provider configs (env-var driven)
├── benchmark/              # Released results & leaderboard data
├── scripts/                # Run / regrade / aggregation utilities
└── src/                    # Evaluation framework & CLI
```

---

## Quick Start

```bash
# 1. Install
pip install -e .

# 2. Run a single task
python -m liveclaweval.cli run \
  --task tasks/CTB_HR_01_onboarding_checklist \
  --config model_configs/claude_opus_46.yaml \
  --trace-dir traces/

# 3. Batch run
python -m liveclaweval.cli batch \
  --tasks-dir tasks/ \
  --config model_configs/claude_opus_46.yaml \
  --parallel 4

# 4. Re-grade existing traces
python scripts/regrade_with_judge.py \
  --traces-dir traces/your_model/ \
  --tasks-dir tasks/

# 5. Add your own model
cp config_template.yaml model_configs/my_model.yaml
# then fill in api_key / base_url / model_id for your provider
```

---

## Roadmap

- [ ] Quarterly Q2 2026 refresh — next signal re-ingestion + task regeneration
- [ ] Public Hugging Face dataset mirror
- [ ] Multi-turn extension (simulated user persona)
- [ ] arXiv preprint

---

## Citation

```bibtex
@misc{liveclaweval2026,
  title  = {LiveClawEval: A Live Agent Benchmark for Evolving Real-World Workflow},
  author = {LiveClawEval Team},
  year   = {2026},
  url    = {https://liveclaweval.github.io/}
}
```

---

## License

Released under the [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) license. See [LICENSE](./LICENSE) for details.
