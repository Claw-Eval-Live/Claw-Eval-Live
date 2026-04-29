# AGENTS.md

Structured operating notes for AI agents working with `Claw-Eval-Live`.

## Mission

`Claw-Eval-Live` is a live benchmark for evaluating AI agents on evolving real-world workflows. The public release contains 105 tasks across 17 families and reports 13 frontier models on the live leaderboard.

## Key Files

- `README.md` — human-facing project overview.
- `README.zh-CN.md` — Chinese project overview.
- `tasks/` — released tasks; each task contains `task.yaml`, `grader.py`, and fixtures.
- `mock_services/` — controlled enterprise-style services used by tasks.
- `model_configs/` — provider configs; copy `config_template.yaml` when adding a model.
- `benchmark/` — released results and leaderboard assets.
- `scripts/regrade_with_judge.py` — utility for regrading existing traces.
- `src/liveclaw_500/` — evaluation framework and CLI implementation.

## Command Quick Reference

```bash
pip install -e .
liveclaw-500 list --tasks-dir tasks
liveclaw-500 run --task tasks/CTB_HR_01_onboarding_checklist --config model_configs/claude_opus_46.yaml --trace-dir traces/
liveclaw-500 batch --tasks-dir tasks --config model_configs/claude_opus_46.yaml --parallel 4
liveclaw-500 grade --trace traces/your_trace.jsonl --task tasks/CTB_HR_01_onboarding_checklist --config model_configs/claude_opus_46.yaml
```

## Editing Rules

- Keep branding as `Claw-Eval-Live`.
- Use `13 frontier models`, not 14.
- Do not reintroduce `LiveClaw-500`, `pass@0.80`, or single-letter task families such as `A/C/D/R/W` in user-facing docs.
- Prefer linking to the live site for dynamic leaderboard details: `https://claw-eval-live.github.io/#/leaderboard`.
- If README figures change, place assets under `docs/assets/` and use relative links.
