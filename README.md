# LiveClaw-500

A benchmark for evaluating AI agents on real-world computer tasks across enterprise workflows.

## Leaderboard

105 tasks · 13 models · 22 workflow categories · pass@0.80 · hybrid grading

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

Ranked by Pass Rate (pass ≥ 0.80), ties broken by Overall Completion Score. Interactive leaderboard: [`benchmark/leaderboard.html`](benchmark/leaderboard.html)

## Overview

LiveClaw-500 evaluates AI agents on multi-step workflows using mock enterprise services. Each task requires the agent to interact with one or more services via tool calls, extract and analyze data, and produce structured outputs graded against ground truth.

### Key Features

- **105 tasks** across 22 workflow categories (HR, Finance, Communication, Terminal, etc.)
- **13 models** from 9 organizations
- **18 mock services**: gmail, crm, calendar, finance, helpdesk, notes, todo, web, contacts, documents, inventory, kb, ocr, rss, scheduler, caption, web_real, web_real_injection
- **Hybrid grading** — deterministic rules (30-60%) verify API call correctness via dispatch logs; LLM-as-judge (40-70%) evaluates semantic quality. Terminal/workspace tasks use 100% script verification.
- **Action-based evaluation** — graders check what the agent *did* (tool calls, service mutations), not just what it *said*
- **Ordering-optimized task selection** — 105 tasks selected from 157 candidates via MILP to maximize model differentiation

### Task Categories

| Category | Tasks | Description |
|-----------|-------|--------------|
| PRODAPP | 17 | Calendar, task, and project management |
| SHELL | 12 | Log analysis, system diagnostics, scripting |
| HR | 8 | Onboarding, reviews, leave management |
| IR | 6 | Industry research, vendor comparison |
| SALES | 6 | Deal risk, forecasting, account health |
| COMM | 5 | Email synthesis, stakeholder updates |
| DATA | 5 | Churn prediction, cost analysis, reconciliation |
| SUPPORT | 5 | Ticket triage, complaint trends |
| W | 5 | Config repair, deployment fixes, debugging |
| WORKFLOW | 4 | End-to-end business processes |
| CRM | 4 | Pipeline, renewal, upsell analysis |
| FIN | 4 | Budget, tax, subscription analysis |
| PROD | 4 | Sprint tracking, weekly planning |
| A | 3 | Financial reconciliation, investment analysis |
| D | 3 | Doc merge, API changelog, whitepaper |
| MGMT | 3 | OKR, capacity, strategy tracking |
| OPS | 3 | Meeting utilization, velocity, dependencies |
| C | 2 | Meeting prep, customer health |
| RESEARCH | 2 | Competitor analysis, market synthesis |
| SEC | 2 | Login alerts, incident reconstruction |
| DOC | 1 | SOP review |
| R | 1 | Platform comparison report |

## Scoring Methodology

### Pass Threshold
A task is **passed** if the completion score >= 0.80.

**Overall Completion Score** is the raw mean of completion scores across all 105 tasks (no discount). Ranked by Pass Count; ties broken by Overall Completion Score.

### Grading Architecture
Each task uses one of three grading modes:

1. **Claw-Eval style** (analysis/report tasks): 30-40% deterministic rules + 60-70% LLM-as-judge
2. **WildClawBench style** (operation tasks): 50-70% deterministic rules + 30-50% LLM-as-judge
3. **Script-first** (terminal/workspace tasks): 100% deterministic script verification

### Deterministic Dimensions
- **Tool gate** — penalty multiplier based on whether agent called required APIs
- **Data accuracy** — correct numbers/entities in output (verified against ground truth)
- **Action verification** — required write operations completed (verified via dispatch logs)

### LLM-as-Judge Dimensions
- **Content quality** — semantic accuracy evaluated against detailed rubrics
- **Report structure** — formatting, organization, completeness

### Task Selection
105 tasks selected from 157 candidates via Mixed-Integer Linear Programming (MILP) to:
1. Maximize ordering stability across top models
2. Remove zero-discrimination tasks (all-pass or all-fail)
3. Balance category representation

## Efficiency Metrics

| Model | Avg Tokens | Avg Turns | Avg Cost | Avg Time |
|-------|------------|------------|-----------|-----------|
| Claude Opus 4.6 | 32,715 | 5.0 | $0.90 | 120s |
| GPT-5.4 | 17,833 | 4.0 | $0.06 | 61s |
| Claude Sonnet 4.6 | 29,549 | 4.7 | $0.16 | 139s |
| GLM-5 | 23,324 | 4.9 | $0.02 | 99s |
| Kimi K2.5 | 20,421 | 4.9 | $0.01 | 123s |
| Gemini 3.1 Pro | 39,069 | 5.3 | $0.07 | 95s |
| DeepSeek V3.2 | 14,555 | 5.0 | $0.01 | 65s |

## Directory Structure

```
LiveClaw-500/
├── README.md
├── pyproject.toml                 # Package metadata & CLI entrypoint
├── benchmark/
│   ├── leaderboard.html           # Interactive leaderboard
│   └── results/
│       ├── raw_matrix_v3.csv      # 105×14 score matrix
│       └── regrade_results.csv    # Full scoring results (long format)
├── tasks/
│   └── CTB_*/
│       ├── task.yaml              # Task definition + service config
│       ├── grader.py              # Hybrid grader (det + judge)
│       └── fixtures/              # Mock service data
├── scripts/
│   └── regrade_with_judge.py      # Re-grade with LLM judge
├── src/liveclaw_500/                 # Core evaluation framework
├── mock_services/                 # 18 mock enterprise services
├── model_configs/                 # Model API configurations
└── config_template.yaml           # Config template (copy & fill in your API key)
```

## Running Evaluation

### Prerequisites
- Python 3.11+
- Install: `pip install -e .`

### Run a single task
```bash
python -m liveclaw_500.cli run \
  --task tasks/CTB_HR_01_onboarding_checklist \
  --config model_configs/claude_opus_46.yaml \
  --trace-dir traces/
```

### Run all tasks in batch
```bash
python -m liveclaw_500.cli batch \
  --tasks-dir tasks/ \
  --config model_configs/claude_opus_46.yaml \
  --parallel 4
```

### Re-grade with existing traces
```bash
python scripts/regrade_with_judge.py \
  --traces-dir traces/your_model/ \
  --tasks-dir tasks/
```

## Comparison with Related Benchmarks

| | LiveClaw-500 | Claw-Eval | WildClawBench |
|---|---|---|---|
| Tasks | 105 | 300 | 60 |
| Models | 14 | 12 | 8 |
| Grading | Hybrid (det + judge) | 75% Judge + 25% Rules | 67% Judge + 33% Scripts |
| Judge model | GPT-5.4 | Gemini-3-Flash | GPT-5.4 |
| Services | 18 mock services | Real + sandbox | Sandbox only |
| Pass threshold | 0.80 | 0.60 | Binary |
| Top pass rate | 66.7% | ~55% | ~45% |

## License

This work is licensed under [Creative Commons Attribution 4.0 International (CC BY 4.0)](https://creativecommons.org/licenses/by/4.0/).

## Citation

```bibtex
@misc{liveclaw500,
  title={LiveClaw-500: A Benchmark for Real-World AI Agent Evaluation},
  year={2026},
}
```
