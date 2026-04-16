# LiveClaw-500

A benchmark for evaluating AI agents on realistic multi-step computer tasks across enterprise workflows.

**105 tasks** | **13 models** | **22 workflow categories** | **18 mock services** | **pass@0.80**

Interactive leaderboard: [`benchmark/leaderboard.html`](benchmark/leaderboard.html)

---

## Leaderboard

| # | Model | Organization | Pass Rate | Overall Completion |
|---|-------|-------------|-----------|-------------------|
| 1 | Claude Opus 4.6 | Anthropic | 66.7% | 83.7 |
| 2 | GPT-5.4 | OpenAI | 63.8% | 81.8 |
| 3 | Claude Sonnet 4.6 | Anthropic | 61.9% | 79.9 |
| 4 | GLM-5 | Zhipu AI | 61.9% | 78.1 |
| 5 | MiniMax M2.7 | MiniMax | 54.3% | 77.6 |
| 6 | Gemini 3.1 Pro | Google | 54.3% | 74.1 |
| 7 | MiMo V2 Pro | Xiaomi | 53.3% | 77.0 |
| 8 | Kimi K2.5 | Moonshot AI | 53.3% | 76.2 |
| 9 | MiniMax M2.5 | MiniMax | 51.4% | 71.0 |
| 10 | DeepSeek V3.2 | DeepSeek | 51.4% | 69.1 |
| 11 | Qwen 3.6 Plus | Alibaba | 50.5% | 71.4 |
| 12 | Qwen 3.5 397B | Alibaba | 49.5% | 72.6 |
| 13 | Doubao Seed 2 | ByteDance | 44.8% | 70.6 |

**Evaluation logic.** Tasks are ranked by **Pass Rate** with **Overall Completion Score** as the tie-breaker. A task is counted as passed when its completion score is **≥ 0.80**.

---

## Tasks

LiveClaw-500 focuses on realistic work-style agent tasks rather than single-turn QA. Agents interact with mock enterprise services, perform multi-step actions, and are graded on both action correctness and output quality.

### Coverage

| Area | Tasks |
|---|---:|
| PRODAPP | 17 |
| SHELL | 12 |
| HR | 8 |
| IR | 6 |
| SALES | 6 |
| COMM | 5 |
| DATA | 5 |
| SUPPORT | 5 |
| W | 5 |
| WORKFLOW | 4 |
| CRM | 4 |
| FIN | 4 |
| PROD | 4 |
| A | 3 |
| D | 3 |
| MGMT | 3 |
| OPS | 3 |
| C | 2 |
| RESEARCH | 2 |
| SEC | 2 |
| DOC | 1 |
| R | 1 |

### Mock services

`calendar`, `caption`, `config`, `contacts`, `crm`, `documents`, `finance`, `gmail`, `helpdesk`, `inventory`, `kb`, `notes`, `ocr`, `rss`, `scheduler`, `todo`, `web`, `web_real`, `web_real_injection`

### Grading

LiveClaw-500 uses a hybrid grading setup:
- **Deterministic checks** verify required tool calls, state changes, and factual correctness.
- **LLM-as-judge** evaluates semantic quality for report-style tasks.
- **Script-first verification** is used for terminal and workspace-heavy tasks.

This combines action-level verification with output-level evaluation.

---

## Dataset

### Repository layout

```text
LiveClaw-500/
├── README.md
├── pyproject.toml
├── benchmark/
├── tasks/
├── mock_services/
├── model_configs/
├── scripts/
├── src/liveclaw_500/
└── config_template.yaml
```

### Included public assets

- `tasks/` — 105 released tasks with `task.yaml`, graders, and fixtures
- `mock_services/` — mock backend services used for task execution
- `benchmark/` — leaderboard and released result tables
- `model_configs/` — public model config examples using environment variables
- `src/liveclaw_500/` — evaluation framework and CLI

---

## Quick Start

### 1. Install

```bash
pip install -e .
```

### 2. Run a single task

```bash
python -m liveclaw_500.cli run \
  --task tasks/CTB_HR_01_onboarding_checklist \
  --config model_configs/claude_opus_46.yaml \
  --trace-dir traces/
```

### 3. Run a batch

```bash
python -m liveclaw_500.cli batch \
  --tasks-dir tasks/ \
  --config model_configs/claude_opus_46.yaml \
  --parallel 4
```

### 4. Re-grade existing traces

```bash
python scripts/regrade_with_judge.py \
  --traces-dir traces/your_model/ \
  --tasks-dir tasks/
```

### 5. Start from a config template

```bash
cp config_template.yaml model_configs/my_model.yaml
```

Then fill in your provider-specific `api_key`, `base_url`, and `model_id`.

---

## Comparison

| | LiveClaw-500 | Claw-Eval | WildClawBench |
|---|---|---|---|
| Tasks | 105 | 300 | 60 |
| Grading | Hybrid (rules + judge) | Judge-heavy hybrid | Script-heavy hybrid |
| Services | 18 mock services | Real + sandbox | Sandbox only |
| Pass threshold | 0.80 | 0.60 | Binary |

---

## Citation

```bibtex
@misc{liveclaw500,
  title={LiveClaw-500: A Benchmark for Real-World AI Agent Evaluation},
  year={2026},
}
```

## License

This work is licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
