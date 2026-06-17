#!/usr/bin/env python3
"""Generate candidate benchmark task ideas from the market task seed bank."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openai import OpenAI
from pydantic import BaseModel, Field, ValidationError


REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from liveclaw_500.config import load_config


DEFAULT_SEED_BANK = REPO_ROOT / "benchmark/seeds/market_task_seed_bank_v0.1.csv"
DEFAULT_OUTPUT_JSONL = REPO_ROOT / "benchmark/candidates/market_candidate_task_ideas_v0.1.jsonl"
DEFAULT_OUTPUT_CSV = REPO_ROOT / "benchmark/candidates/market_candidate_task_ideas_v0.1.csv"
DEFAULT_OUTPUT_MD = REPO_ROOT / "benchmark/candidates/market_candidate_task_ideas_v0.1.md"
PROMPT_VERSION = "candidate_task_ideas_v0.1"
JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", flags=re.IGNORECASE)
SLUG_RE = re.compile(r"[^a-z0-9]+")

SYSTEM_PROMPT = """\
你在帮助构建一个 computer task benchmark。

你的工作不是直接写 runnable task，而是把一个 market-derived seed 扩写成若干 candidate task ideas。

严格要求：
1. 每个 idea 必须是复合工作流，不要是单步小任务。
2. 每个 idea 必须有明确 artifact，最后能交付一个文件、草稿、报告、结构化表或已完成动作摘要。
3. verifier 必须可想象，不能是纯主观玄学题。
4. 不要直接复用 top skills 里的具体品牌、具体公司、具体答案。
5. 优先保留 seed 的工作流本质，而不是模仿已有 benchmark 题面。
6. 尽量让 candidate ideas 彼此有明显差异，不要只是换个壳。
7. 不要输出 task.yaml，不要输出 grader 代码。

只输出 JSON，不要输出任何额外解释。JSON 结构必须为：
{
  "ideas": [
    {
      "idea_slug": "ascii_slug",
      "idea_title_zh": "中文标题",
      "scenario_zh": "1-2 句场景说明",
      "user_request_zh": "给 agent 的用户请求草稿，1 段",
      "workflow_summary_zh": "1 句说明这条复合工作流",
      "workflow_steps_zh": ["步骤1", "步骤2", "步骤3"],
      "required_services_or_tools": ["tool_or_service_1", "tool_or_service_2"],
      "environment_plan": "local_workspace|mock_services|attachments_or_documents_service|mock_services_or_web|attachments_or_mock_services|hybrid",
      "artifact_spec_zh": "最终交付物是什么",
      "verifier_outline_zh": "grader 可以如何检查",
      "difficulty_band": "medium|medium-hard|hard",
      "difficulty_reason_zh": "为什么既不太简单也不纯随机",
      "market_rationale_zh": "为什么它和这个 seed / top skills 对齐",
      "local_approximation_feasible": "yes|partial|no",
      "contamination_guard_zh": "如何避免直接复用 top skills 里的具体实体",
      "risk_flags": ["可选风险1", "可选风险2"]
    }
  ]
}
"""


class CandidateIdea(BaseModel):
    idea_slug: str = Field(min_length=1)
    idea_title_zh: str = Field(min_length=1)
    scenario_zh: str = Field(min_length=1)
    user_request_zh: str = Field(min_length=1)
    workflow_summary_zh: str = Field(min_length=1)
    workflow_steps_zh: list[str] = Field(default_factory=list)
    required_services_or_tools: list[str] = Field(default_factory=list)
    environment_plan: str = Field(min_length=1)
    artifact_spec_zh: str = Field(min_length=1)
    verifier_outline_zh: str = Field(min_length=1)
    difficulty_band: str = Field(min_length=1)
    difficulty_reason_zh: str = Field(min_length=1)
    market_rationale_zh: str = Field(min_length=1)
    local_approximation_feasible: str = Field(min_length=1)
    contamination_guard_zh: str = Field(min_length=1)
    risk_flags: list[str] = Field(default_factory=list)


class CandidateIdeaBatch(BaseModel):
    ideas: list[CandidateIdea] = Field(default_factory=list)


def _load_seed_rows(path: Path) -> list[dict[str, str]]:
    with path.open() as f:
        return list(csv.DictReader(f))


def _selected_seed_rows(
    rows: list[dict[str, str]],
    *,
    seed_ids: set[str] | None,
    limit: int | None,
) -> list[dict[str, str]]:
    selected = []
    for row in rows:
        if seed_ids and row["seed_id"] not in seed_ids:
            continue
        selected.append(row)
        if limit is not None and len(selected) >= limit:
            break
    return selected


def _recommended_idea_count(row: dict[str, str], override: int | None) -> int:
    if override is not None:
        return max(1, override)
    try:
        return max(1, int(row.get("recommended_seed_ideas", "1")))
    except ValueError:
        return 1


def _strip_json_fence(raw: str) -> str:
    return JSON_FENCE_RE.sub("", raw.strip())


def _slugify(text: str) -> str:
    slug = SLUG_RE.sub("_", text.lower()).strip("_")
    return slug or "candidate_idea"


def _normalize_list(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        values = raw
    else:
        values = [raw]
    result = []
    for value in values:
        text = str(value).strip()
        if text:
            result.append(text)
    return result


def _normalize_text(raw: Any, fallback: str = "") -> str:
    text = str(raw or "").strip()
    return text or fallback


def _normalize_idea(raw: dict[str, Any]) -> CandidateIdea:
    payload = {
        "idea_slug": _normalize_text(raw.get("idea_slug") or raw.get("slug"), "candidate_idea"),
        "idea_title_zh": _normalize_text(raw.get("idea_title_zh") or raw.get("title_zh") or raw.get("title")),
        "scenario_zh": _normalize_text(raw.get("scenario_zh") or raw.get("scenario")),
        "user_request_zh": _normalize_text(raw.get("user_request_zh") or raw.get("user_request")),
        "workflow_summary_zh": _normalize_text(raw.get("workflow_summary_zh") or raw.get("workflow_summary")),
        "workflow_steps_zh": _normalize_list(raw.get("workflow_steps_zh") or raw.get("workflow_steps")),
        "required_services_or_tools": _normalize_list(
            raw.get("required_services_or_tools") or raw.get("required_tools_or_services")
        ),
        "environment_plan": _normalize_text(raw.get("environment_plan")),
        "artifact_spec_zh": _normalize_text(raw.get("artifact_spec_zh") or raw.get("artifact_spec")),
        "verifier_outline_zh": _normalize_text(raw.get("verifier_outline_zh") or raw.get("verifier_outline")),
        "difficulty_band": _normalize_text(raw.get("difficulty_band"), "medium"),
        "difficulty_reason_zh": _normalize_text(raw.get("difficulty_reason_zh") or raw.get("difficulty_reason")),
        "market_rationale_zh": _normalize_text(raw.get("market_rationale_zh") or raw.get("market_rationale")),
        "local_approximation_feasible": _normalize_text(
            raw.get("local_approximation_feasible"),
            "partial",
        ),
        "contamination_guard_zh": _normalize_text(
            raw.get("contamination_guard_zh") or raw.get("anti_contamination_note")
        ),
        "risk_flags": _normalize_list(raw.get("risk_flags")),
    }
    return CandidateIdea.model_validate(payload)


def _parse_response(raw: str) -> CandidateIdeaBatch:
    cleaned = _strip_json_fence(raw)
    parsed = json.loads(cleaned)
    if isinstance(parsed, list):
        parsed = {"ideas": parsed}
    ideas = [_normalize_idea(item) for item in parsed.get("ideas", [])]
    return CandidateIdeaBatch(ideas=ideas)


def _load_existing_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _write_csv(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "candidate_id",
        "seed_id",
        "family",
        "pattern_id",
        "pattern_label",
        "idea_rank_in_seed",
        "idea_title_zh",
        "difficulty_band",
        "environment_plan",
        "local_approximation_feasible",
        "artifact_spec_zh",
        "verifier_outline_zh",
        "market_rationale_zh",
        "required_services_or_tools",
        "workflow_steps_zh",
        "risk_flags",
        "model_id",
        "generated_at",
    ]
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for record in records:
            row = {key: record.get(key, "") for key in fieldnames}
            row["required_services_or_tools"] = " | ".join(record.get("required_services_or_tools", []))
            row["workflow_steps_zh"] = " | ".join(record.get("workflow_steps_zh", []))
            row["risk_flags"] = " | ".join(record.get("risk_flags", []))
            writer.writerow(row)


def _write_md(
    path: Path,
    records: list[dict[str, Any]],
    seeds_by_id: dict[str, dict[str, str]],
    *,
    model_id: str,
    config_path: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    grouped: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for record in records:
        grouped[record["family"]][record["seed_id"]].append(record)

    lines = [
        "# Market Candidate Task Ideas v0.1",
        "",
        f"- generated_model: `{model_id}`",
        f"- config: `{config_path}`",
        f"- total_candidates: `{len(records)}`",
        "",
    ]

    for family in sorted(grouped):
        lines.append(f"## {family}")
        lines.append("")
        for seed_id, seed_records in sorted(grouped[family].items()):
            seed = seeds_by_id[seed_id]
            lines.append(f"### {seed_id} | {seed['pattern_label']}")
            lines.append("")
            lines.append(f"- market_weight: `{seed['family_market_weight']}`")
            lines.append(f"- pattern_downloads: `{seed['pattern_total_downloads']}`")
            lines.append(f"- environment_hint: `{seed['environment_hint']}`")
            lines.append(f"- artifact_hint: `{seed['artifact_hint']}`")
            lines.append(f"- top_skills: `{seed['top_skills']}`")
            lines.append("")
            for record in sorted(seed_records, key=lambda item: item["idea_rank_in_seed"]):
                lines.append(
                    f"#### {record['candidate_id']} | {record['idea_title_zh']}"
                )
                lines.append("")
                lines.append(f"- 场景：{record['scenario_zh']}")
                lines.append(f"- 用户请求：{record['user_request_zh']}")
                lines.append(f"- Workflow：{record['workflow_summary_zh']}")
                lines.append(f"- Artifact：{record['artifact_spec_zh']}")
                lines.append(f"- Verifier：{record['verifier_outline_zh']}")
                lines.append(f"- 环境：`{record['environment_plan']}`")
                lines.append(f"- 难度：`{record['difficulty_band']}`，{record['difficulty_reason_zh']}")
                lines.append(f"- 本地近似：`{record['local_approximation_feasible']}`")
                lines.append(f"- 市场关联：{record['market_rationale_zh']}")
                lines.append(f"- 防污染：{record['contamination_guard_zh']}")
                if record.get("required_services_or_tools"):
                    lines.append(
                        f"- 可能依赖：{', '.join(record['required_services_or_tools'])}"
                    )
                if record.get("workflow_steps_zh"):
                    lines.append(
                        f"- 步骤：{' -> '.join(record['workflow_steps_zh'])}"
                    )
                if record.get("risk_flags"):
                    lines.append(f"- 风险：{', '.join(record['risk_flags'])}")
                lines.append("")

    path.write_text("\n".join(lines).rstrip() + "\n")


def _validate_runtime_config(config_path: str) -> tuple[OpenAI, str, float, dict[str, Any], int]:
    config = load_config(config_path)
    unresolved_header_keys = [
        key for key, value in config.model.default_headers.items() if value in (None, "")
    ]
    if unresolved_header_keys:
        joined = ", ".join(unresolved_header_keys)
        raise SystemExit(
            f"Config has unresolved header values for: {joined}. "
            "Export the required env vars before running real generation."
        )

    extra_body = dict(config.model.extra_body or {})
    if isinstance(extra_body.get("thinking"), dict):
        extra_body["thinking"] = {
            **extra_body["thinking"],
            "include_thoughts": False,
            "budget_tokens": 0,
        }

    default_temperature = 0.6 if config.model.model_id.startswith("kimi-") else 0.7
    temperature = float(extra_body.pop("temperature", default_temperature))

    client = OpenAI(
        api_key=config.model.api_key or "dummy",
        base_url=config.model.base_url,
        default_headers=config.model.default_headers or None,
        default_query=config.model.default_query or None,
        timeout=180,
    )
    return client, config.model.model_id, temperature, extra_body, 4096


def _build_user_prompt(
    seed: dict[str, str],
    *,
    idea_count: int,
    existing_titles: list[str],
) -> str:
    existing_block = "\n".join(f"- {title}" for title in existing_titles) or "- None"
    return f"""\
请基于下面这个 market task seed，生成 {idea_count} 个候选 benchmark task ideas。

## Seed metadata
- seed_id: {seed['seed_id']}
- family: {seed['family']}
- family_market_weight: {seed['family_market_weight']}
- family_seed_budget: {seed['family_seed_budget']}
- pattern_rank_in_family: {seed['pattern_rank_in_family']}
- pattern_id: {seed['pattern_id']}
- pattern_label: {seed['pattern_label']}
- pattern_total_downloads: {seed['pattern_total_downloads']}
- workflow_archetype: {seed['workflow_archetype']}
- environment_hint: {seed['environment_hint']}
- artifact_hint: {seed['artifact_hint']}
- seed_frame_1: {seed['seed_frame_1']}
- seed_frame_2: {seed['seed_frame_2']}
- top_skills: {seed['top_skills']}

## 已经存在的 idea 标题
{existing_block}

## 额外要求
- 每个 idea 都要面向“computer task benchmark”，不是普通聊天题。
- 用户请求要像真实工作请求，且能被 agent 在电脑环境中执行。
- 如果 seed 更偏 workspace / config，也尽量给出本地近似是否可行。
- 不要直接照抄 seed_frame，用它来展开成更具体的任务。
- 不要复用 top skills 中的具体实体名或答案内容。
- 生成的多个 idea 必须明显不同。
"""


def _call_model_for_seed(
    client: OpenAI,
    *,
    model_id: str,
    temperature: float,
    extra_body: dict[str, Any],
    max_tokens: int,
    seed: dict[str, str],
    needed: int,
    existing_titles: list[str],
    max_retries: int,
) -> CandidateIdeaBatch:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": _build_user_prompt(
                seed,
                idea_count=needed,
                existing_titles=existing_titles,
            ),
        },
    ]

    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            response = client.chat.completions.create(
                model=model_id,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                extra_body=extra_body or None,
            )
            raw = response.choices[0].message.content or "{}"
            batch = _parse_response(raw)
            if not batch.ideas:
                raise ValueError("model returned zero ideas")
            return batch
        except (ValidationError, json.JSONDecodeError, KeyError, IndexError, ValueError) as exc:
            last_exc = exc
        except Exception as exc:
            last_exc = exc

        if attempt == max_retries:
            break
        delay = min(2 ** attempt, 16)
        print(
            f"[retry] seed={seed['seed_id']} attempt={attempt + 1}/{max_retries} "
            f"reason={type(last_exc).__name__}: {last_exc}"
        )
        time.sleep(delay)

    raise RuntimeError(f"Failed to generate ideas for {seed['seed_id']}: {last_exc}") from last_exc


def _generate_with_fallback(
    client: OpenAI,
    *,
    model_id: str,
    temperature: float,
    extra_body: dict[str, Any],
    max_tokens: int,
    seed: dict[str, str],
    needed: int,
    existing_titles: list[str],
    max_retries: int,
) -> CandidateIdeaBatch:
    """Generate ideas for one seed, falling back to smaller batches if needed.

    Large batches are more likely to produce malformed JSON on some models.
    When that happens, degrade to one-idea-at-a-time generation instead of
    aborting the whole run.
    """
    try:
        return _call_model_for_seed(
            client,
            model_id=model_id,
            temperature=temperature,
            extra_body=extra_body,
            max_tokens=max_tokens,
            seed=seed,
            needed=needed,
            existing_titles=existing_titles,
            max_retries=max_retries,
        )
    except RuntimeError:
        if needed <= 1:
            raise

    print(
        f"[fallback] seed={seed['seed_id']} batch_size={needed} -> single_idea_mode"
    )
    collected: list[CandidateIdea] = []
    seen_titles = list(existing_titles)
    for index in range(needed):
        single = _call_model_for_seed(
            client,
            model_id=model_id,
            temperature=temperature,
            extra_body=extra_body,
            max_tokens=max_tokens,
            seed=seed,
            needed=1,
            existing_titles=seen_titles,
            max_retries=max_retries,
        )
        idea = single.ideas[0]
        collected.append(idea)
        seen_titles.append(idea.idea_title_zh)
        print(
            f"[fallback-progress] seed={seed['seed_id']} generated={index + 1}/{needed}"
        )
    return CandidateIdeaBatch(ideas=collected)


def _build_record(
    seed: dict[str, str],
    idea: CandidateIdea,
    *,
    idea_rank_in_seed: int,
    model_id: str,
) -> dict[str, Any]:
    candidate_id = f"{seed['seed_id']}_IDEA_{idea_rank_in_seed:02d}"
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    idea_slug = _slugify(idea.idea_slug or idea.idea_title_zh)
    return {
        "candidate_id": candidate_id,
        "seed_id": seed["seed_id"],
        "family": seed["family"],
        "family_market_weight": seed["family_market_weight"],
        "family_seed_budget": seed["family_seed_budget"],
        "pattern_rank_in_family": seed["pattern_rank_in_family"],
        "pattern_id": seed["pattern_id"],
        "pattern_label": seed["pattern_label"],
        "pattern_total_downloads": seed["pattern_total_downloads"],
        "environment_hint": seed["environment_hint"],
        "artifact_hint": seed["artifact_hint"],
        "top_skills": seed["top_skills"],
        "model_id": model_id,
        "prompt_version": PROMPT_VERSION,
        "generated_at": generated_at,
        "idea_rank_in_seed": idea_rank_in_seed,
        "idea_slug": idea_slug,
        "idea_title_zh": idea.idea_title_zh,
        "scenario_zh": idea.scenario_zh,
        "user_request_zh": idea.user_request_zh,
        "workflow_summary_zh": idea.workflow_summary_zh,
        "workflow_steps_zh": idea.workflow_steps_zh,
        "required_services_or_tools": idea.required_services_or_tools,
        "environment_plan": idea.environment_plan,
        "artifact_spec_zh": idea.artifact_spec_zh,
        "verifier_outline_zh": idea.verifier_outline_zh,
        "difficulty_band": idea.difficulty_band,
        "difficulty_reason_zh": idea.difficulty_reason_zh,
        "market_rationale_zh": idea.market_rationale_zh,
        "local_approximation_feasible": idea.local_approximation_feasible,
        "contamination_guard_zh": idea.contamination_guard_zh,
        "risk_flags": idea.risk_flags,
    }


def _record_sort_key(
    record: dict[str, Any],
    seed_order: dict[str, int],
) -> tuple[int, int, str]:
    return (
        seed_order.get(record["seed_id"], 10**9),
        int(record.get("idea_rank_in_seed", 9999)),
        record["candidate_id"],
    )


def _save_outputs(
    *,
    all_records: list[dict[str, Any]],
    seed_rows: list[dict[str, str]],
    output_jsonl: Path,
    output_csv: Path,
    output_md: Path,
    model_id: str,
    config_path: str,
) -> None:
    seed_order = {row["seed_id"]: index for index, row in enumerate(seed_rows)}
    seeds_by_id = {row["seed_id"]: row for row in seed_rows}
    all_records.sort(key=lambda record: _record_sort_key(record, seed_order))
    _write_jsonl(output_jsonl, all_records)
    _write_csv(output_csv, all_records)
    _write_md(
        output_md,
        all_records,
        seeds_by_id,
        model_id=model_id,
        config_path=config_path,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate candidate task ideas from the market task seed bank")
    parser.add_argument("--seed-bank", default=str(DEFAULT_SEED_BANK))
    parser.add_argument("--config", default="model_configs/kimi_k25.yaml")
    parser.add_argument("--limit", type=int, default=None, help="Only process the first N selected seeds")
    parser.add_argument("--seed-ids", default=None, help="Comma-separated seed ids to generate")
    parser.add_argument(
        "--ideas-per-seed",
        type=int,
        default=None,
        help="Override recommended_seed_ideas from the seed bank",
    )
    parser.add_argument("--resume", action="store_true", help="Reuse existing JSONL and only top up missing ideas")
    parser.add_argument("--dry-run", action="store_true", help="Show the generation plan without calling the model")
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--output-jsonl", default=str(DEFAULT_OUTPUT_JSONL))
    parser.add_argument("--output-csv", default=str(DEFAULT_OUTPUT_CSV))
    parser.add_argument("--output-md", default=str(DEFAULT_OUTPUT_MD))
    args = parser.parse_args()

    seed_bank_path = Path(args.seed_bank)
    output_jsonl = Path(args.output_jsonl)
    output_csv = Path(args.output_csv)
    output_md = Path(args.output_md)

    seed_rows = _load_seed_rows(seed_bank_path)
    seed_ids = (
        {seed_id.strip() for seed_id in args.seed_ids.split(",") if seed_id.strip()}
        if args.seed_ids
        else None
    )
    selected_rows = _selected_seed_rows(seed_rows, seed_ids=seed_ids, limit=args.limit)
    if not selected_rows:
        raise SystemExit("No seeds selected.")

    existing_records = _load_existing_records(output_jsonl) if args.resume else []
    existing_by_seed: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in existing_records:
        existing_by_seed[record["seed_id"]].append(record)

    print(f"Seed bank: {seed_bank_path}")
    print(f"Selected seeds: {len(selected_rows)}")
    print(f"Output JSONL: {output_jsonl}")
    print(f"Output CSV: {output_csv}")
    print(f"Output MD: {output_md}")
    print(f"Resume mode: {'on' if args.resume else 'off'}")
    print()

    for row in selected_rows:
        target = _recommended_idea_count(row, args.ideas_per_seed)
        existing = len(existing_by_seed.get(row["seed_id"], []))
        missing = max(0, target - existing)
        print(
            f"- {row['seed_id']} | {row['family']} | {row['pattern_label']} "
            f"| target={target} existing={existing} missing={missing}"
        )

    if args.dry_run:
        print("\n[DRY RUN] No model calls executed.")
        return 0

    client, model_id, temperature, extra_body, max_tokens = _validate_runtime_config(args.config)
    print(f"\nModel: {model_id}")
    print(f"Temperature: {temperature}")
    print(f"Prompt version: {PROMPT_VERSION}")
    print()

    all_records = list(existing_records)
    total_new = 0
    failed_seeds: list[str] = []
    for row in selected_rows:
        target = _recommended_idea_count(row, args.ideas_per_seed)
        existing = existing_by_seed.get(row["seed_id"], [])
        missing = target - len(existing)
        if missing <= 0:
            print(f"[skip] {row['seed_id']} already has {len(existing)} ideas")
            continue

        print(f"[generate] {row['seed_id']} need={missing}")
        try:
            batch = _generate_with_fallback(
                client,
                model_id=model_id,
                temperature=temperature,
                extra_body=extra_body,
                max_tokens=max_tokens,
                seed=row,
                needed=missing,
                existing_titles=[record["idea_title_zh"] for record in existing],
                max_retries=args.max_retries,
            )
            accepted = 0
            next_rank = len(existing) + 1
            for idea in batch.ideas:
                if accepted >= missing:
                    break
                record = _build_record(
                    row,
                    idea,
                    idea_rank_in_seed=next_rank,
                    model_id=model_id,
                )
                all_records.append(record)
                existing_by_seed[row["seed_id"]].append(record)
                next_rank += 1
                accepted += 1
                total_new += 1
            print(f"          accepted={accepted}")
            _save_outputs(
                all_records=all_records,
                seed_rows=seed_rows,
                output_jsonl=output_jsonl,
                output_csv=output_csv,
                output_md=output_md,
                model_id=model_id,
                config_path=args.config,
            )
        except Exception as exc:  # noqa: BLE001
            failed_seeds.append(row["seed_id"])
            print(f"[seed-failed] {row['seed_id']}: {type(exc).__name__}: {exc}")

    _save_outputs(
        all_records=all_records,
        seed_rows=seed_rows,
        output_jsonl=output_jsonl,
        output_csv=output_csv,
        output_md=output_md,
        model_id=model_id,
        config_path=args.config,
    )

    print()
    print(f"Generated new ideas: {total_new}")
    print(f"Total stored ideas: {len(all_records)}")
    if failed_seeds:
        print(f"Failed seeds: {', '.join(failed_seeds)}")
    print(f"Saved JSONL: {output_jsonl}")
    print(f"Saved CSV: {output_csv}")
    print(f"Saved MD: {output_md}")
    return 1 if failed_seeds else 0


if __name__ == "__main__":
    raise SystemExit(main())
