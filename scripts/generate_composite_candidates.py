#!/usr/bin/env python3
"""Generate candidate task ideas from composite seeds via an OpenAI-compatible endpoint.

Usage:
    python3 scripts/generate_composite_candidates.py \
        --seed-bank benchmark/seeds/composite_seed_bank_v1.0.csv \
        --config model_configs/kimi_k25.yaml \
        --ideas-per-seed 3 \
        --limit 5
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openai import OpenAI
from pydantic import BaseModel, Field

REPO_ROOT = Path(__file__).resolve().parent.parent

JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", flags=re.IGNORECASE)

# ──────────────────────────────────────────────
# System prompt — adapted for composite seeds
# ──────────────────────────────────────────────

SYSTEM_PROMPT = """\
你在帮助构建一个 computer task benchmark（类似 SWE-bench / WildClawBench）。

你的工作是把一个 **composite seed**（多 pattern 组合的复合工作流描述）扩写成若干 candidate task ideas。

每个 seed 已经包含：
- archetype（任务结构骨架，如 research-synthesize-deliver / diagnose-repair-verify）
- 2-5 个 pattern 组合（如 email_management + crm_sales + data_analysis）
- scenario 提示和 top skills 参考

严格要求：
1. 每个 idea 必须是 **5-15+ 步的复合工作流**，不要是单步小任务。
2. 每个 idea 必须跨至少 2 个不同的 service/tool 边界。
3. 每个 idea 必须有明确 artifact（文件/报告/草稿/结构化输出）。
4. verifier 必须可实现：支持多 checkpoint 评分（如工具调用序列 + 中间产物 + 最终 artifact）。
5. 不要直接复用 top skills 里的具体品牌名（用 mock service 替代）。
6. 要保留 seed 的 archetype 结构（例如 diagnose-repair-verify 就应该有诊断→修复→验证三阶段）。
7. 生成的多个 idea 必须有明显差异，不要只换场景壳。
8. 考虑 mock service 的可行性：每个 idea 要说明需要哪些 mock service。

只输出 JSON，格式：
{
  "ideas": [
    {
      "idea_slug": "ascii_snake_case_slug",
      "idea_title_zh": "中文标题",
      "scenario_zh": "1-2 句场景",
      "user_request_zh": "给 agent 的用户请求，2-3 句即可",
      "workflow_steps_zh": ["步骤1", "步骤2", "步骤3"],
      "required_mock_services": ["gmail", "crm"],
      "environment_plan": "mock_services|local_workspace|hybrid",
      "artifact_spec_zh": "最终交付物描述",
      "grader_checkpoints_zh": ["checkpoint1: 是否正确调用了...", "checkpoint2: 中间产物是否..."],
      "difficulty_band": "medium|medium-hard|hard",
      "difficulty_reason_zh": "为什么这个难度合适",
      "step_count_estimate": 8,
      "tool_call_estimate": 15,
      "risk_flags": ["可选风险"]
    }
  ]
}
"""


class CandidateIdea(BaseModel):
    idea_slug: str = Field(min_length=1)
    idea_title_zh: str = Field(min_length=1)
    scenario_zh: str = Field(min_length=1)
    user_request_zh: str = Field(min_length=1)
    workflow_steps_zh: list[str] = Field(default_factory=list)
    required_mock_services: list[str] = Field(default_factory=list)
    environment_plan: str = Field(min_length=1)
    artifact_spec_zh: str = Field(min_length=1)
    grader_checkpoints_zh: list[str] = Field(default_factory=list)
    difficulty_band: str = Field(default="medium")
    difficulty_reason_zh: str = Field(default="")
    step_count_estimate: int = Field(default=8)
    tool_call_estimate: int = Field(default=15)
    risk_flags: list[str] = Field(default_factory=list)


def _build_user_prompt(seed: dict[str, str], *, idea_count: int, existing_titles: list[str]) -> str:
    existing_block = "\n".join(f"- {t}" for t in existing_titles) or "- None"
    return f"""\
请基于下面这个 composite seed，生成 {idea_count} 个候选 benchmark task ideas。

## Seed 信息
- seed_id: {seed['seed_id']}
- archetype: {seed['archetype']}
- primary_pattern: {seed['primary_pattern']}
- primary_family: {seed['primary_family']}
- aux_patterns: {seed['aux_patterns']}
- aux_families: {seed['aux_families']}
- pattern_count: {seed['pattern_count']}
- complexity: {seed['complexity']}
- workflow_narrative: {seed['narrative']}
- scenario_hint: {seed['scenario']}
- artifact_hint: {seed['artifact']}
- environment_hint: {seed['environment']}
- top_skills_reference: {seed.get('top_skills_ref', 'N/A')}

## 已存在的 idea 标题
{existing_block}

## 额外要求
- 每个 idea 必须遵循 archetype 的结构骨架（{seed['archetype']}）
- 涉及的 pattern 组合是 {seed['primary_pattern']} + {seed['aux_patterns']}，所有 pattern 都要在 workflow 中体现
- 参考 top_skills_reference 了解用户在每个 pattern 下的真实操作，但不要照搬品牌名
- 面向 computer task benchmark：agent 在电脑环境中执行，有 mock service 可用
- 生成的 idea 之间必须有明显差异
- complexity={seed['complexity']}，如果是 medium 就 5-10 步，如果是 long 就 10-20+ 步
"""


def _parse_response(raw: str) -> list[CandidateIdea]:
    cleaned = JSON_FENCE_RE.sub("", raw.strip())
    parsed = json.loads(cleaned)
    if isinstance(parsed, list):
        parsed = {"ideas": parsed}
    ideas = []
    for item in parsed.get("ideas", []):
        try:
            ideas.append(CandidateIdea.model_validate(item))
        except Exception as e:
            print(f"  [warn] Skipping malformed idea: {e}")
    return ideas


def _validate_config(config_path: str):
    """Load a LiveClaw-style model config without importing the runtime package."""
    import yaml
    with open(config_path) as f:
        cfg = yaml.safe_load(f)
    m = cfg.get("model", {})

    # Resolve env vars in values
    def resolve(val):
        if isinstance(val, str) and val.startswith("${") and val.endswith("}"):
            env_key = val[2:-1]
            return os.environ.get(env_key, val)
        return val

    api_key = resolve(m.get("api_key", "dummy"))
    base_url = m.get("base_url", "https://openrouter.ai/api/v1")
    model_id = m.get("model_id", "moonshotai/kimi-k2.5")
    headers = {k: resolve(v) for k, v in (m.get("default_headers") or {}).items()}
    query = m.get("default_query") or {}
    extra_body = dict(m.get("extra_body") or {})

    if isinstance(extra_body.get("thinking"), dict):
        extra_body["thinking"] = {**extra_body["thinking"], "include_thoughts": False, "budget_tokens": 0}
    default_temperature = 1.0 if model_id.startswith("kimi") else 0.7
    temperature = float(extra_body.pop("temperature", default_temperature))

    client = OpenAI(
        api_key=api_key,
        base_url=base_url,
        default_headers=headers or None,
        default_query=query or None,
        timeout=300,
    )
    return client, model_id, temperature, extra_body


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed-bank", default="benchmark/seeds/composite_seed_bank_v1.0.csv")
    parser.add_argument("--config", default="model_configs/kimi_k25.yaml")
    parser.add_argument("--ideas-per-seed", type=int, default=3)
    parser.add_argument("--offset", type=int, default=0, help="Skip first N seeds before processing")
    parser.add_argument("--limit", type=int, default=None, help="Only process first N seeds")
    parser.add_argument("--output-dir", default="benchmark/candidates")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    seed_bank = list(csv.DictReader(open(args.seed_bank, encoding="utf-8")))
    if args.offset:
        seed_bank = seed_bank[args.offset:]
    if args.limit:
        seed_bank = seed_bank[:args.limit]

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_jsonl = out_dir / "composite_candidates_v1.0.jsonl"
    out_md = out_dir / "composite_candidates_v1.0.md"

    existing_records: list[dict[str, Any]] = []
    if args.resume and out_jsonl.exists():
        with open(out_jsonl, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    existing_records.append(json.loads(line))

    existing_counts: dict[str, int] = defaultdict(int)
    for rec in existing_records:
        existing_counts[rec["seed_id"]] += 1
    completed_seed_ids = {
        seed_id for seed_id, count in existing_counts.items() if count >= args.ideas_per_seed
    }

    print(f"Seeds: {len(seed_bank)}")
    print(f"Offset: {args.offset}")
    print(f"Ideas per seed: {args.ideas_per_seed}")
    print(f"Expected total: ~{len(seed_bank) * args.ideas_per_seed}")
    print(f"Output: {out_jsonl}")

    if args.dry_run:
        for seed in seed_bank:
            print(f"  {seed['seed_id']}: {seed['archetype']} | {seed['primary_pattern']} + {seed['aux_patterns']}")
        print("\n[DRY RUN] No model calls.")
        return 0

    client, model_id, temperature, extra_body = _validate_config(args.config)
    print(f"Model: {model_id}, Temperature: {temperature}\n")

    all_records = list(existing_records)
    failed = []

    for i, seed in enumerate(seed_bank, 1):
        if seed["seed_id"] in completed_seed_ids:
            print(f"[{i}/{len(seed_bank)}] {seed['seed_id']} -> skip existing")
            continue
        print(f"[{i}/{len(seed_bank)}] {seed['seed_id']}")
        existing_titles = [r["idea_title_zh"] for r in all_records if r["seed_id"] == seed["seed_id"]]

        for attempt in range(args.max_retries + 1):
            try:
                resp = client.chat.completions.create(
                    model=model_id,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": _build_user_prompt(seed, idea_count=args.ideas_per_seed, existing_titles=existing_titles)},
                    ],
                    temperature=temperature,
                    max_tokens=8192,
                    extra_body=extra_body or None,
                )
                raw = resp.choices[0].message.content or "{}"
                ideas = _parse_response(raw)
                if not ideas:
                    raise ValueError("zero ideas parsed")

                for rank, idea in enumerate(ideas[:args.ideas_per_seed], 1):
                    record = {
                        "candidate_id": f"{seed['seed_id']}_IDEA_{rank:02d}",
                        "seed_id": seed["seed_id"],
                        "archetype": seed["archetype"],
                        "primary_pattern": seed["primary_pattern"],
                        "primary_family": seed.get("primary_family", "unknown"),
                        "aux_patterns": seed["aux_patterns"],
                        "aux_families": seed.get("aux_families", ""),
                        "pattern_count": seed.get("pattern_count", ""),
                        "families_covered": seed["families_covered"],
                        "model_id": model_id,
                        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "idea_rank": rank,
                        **idea.model_dump(),
                    }
                    all_records.append(record)

                print(f"  -> {len(ideas)} ideas accepted")
                break
            except Exception as e:
                if attempt < args.max_retries:
                    print(f"  [retry {attempt+1}] {type(e).__name__}: {e}")
                    time.sleep(2 ** attempt)
                else:
                    print(f"  [FAILED] {type(e).__name__}: {e}")
                    failed.append(seed["seed_id"])

        # Incremental save
        with open(out_jsonl, "w", encoding="utf-8") as f:
            for rec in all_records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # Write markdown
    lines = [
        "# Composite Candidate Task Ideas v1.0",
        "",
        f"- Model: `{model_id}`",
        f"- Total candidates: **{len(all_records)}**",
        f"- Seeds processed: {len(seed_bank)}",
        f"- Failed: {len(failed)}",
        "",
    ]

    by_arch = defaultdict(list)
    for rec in all_records:
        by_arch[rec["archetype"]].append(rec)

    for arch in sorted(by_arch):
        recs = by_arch[arch]
        lines.append(f"## {arch} ({len(recs)} ideas)")
        lines.append("")
        for rec in recs:
            lines.append(f"### {rec['candidate_id']} | {rec['idea_title_zh']}")
            lines.append("")
            lines.append(f"- **Scenario**: {rec['scenario_zh']}")
            lines.append(f"- **User Request**: {rec['user_request_zh'][:200]}...")
            lines.append(f"- **Steps ({rec['step_count_estimate']}~)**: {' → '.join(rec['workflow_steps_zh'][:5])}")
            lines.append(f"- **Mock Services**: {', '.join(rec['required_mock_services'])}")
            lines.append(f"- **Artifact**: {rec['artifact_spec_zh']}")
            lines.append(f"- **Difficulty**: {rec['difficulty_band']}")
            lines.append(f"- **Grader Checkpoints**: {len(rec['grader_checkpoints_zh'])} points")
            lines.append("")

    with open(out_md, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"\n=== Done ===")
    print(f"Total candidates: {len(all_records)}")
    print(f"Failed seeds: {len(failed)} {failed}")
    print(f"JSONL: {out_jsonl}")
    print(f"MD:    {out_md}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
