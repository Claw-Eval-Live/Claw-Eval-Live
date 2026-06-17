#!/usr/bin/env python3
"""Prefilter generated candidate task ideas before task implementation."""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT_JSONL = REPO_ROOT / "benchmark/candidates/market_candidate_task_ideas_v0.1.jsonl"
DEFAULT_OUTPUT_CSV = REPO_ROOT / "benchmark/candidates/market_candidate_task_ideas_prefilter_v0.1.csv"
DEFAULT_OUTPUT_MD = REPO_ROOT / "benchmark/candidates/market_candidate_task_ideas_prefilter_v0.1.md"
DEFAULT_OUTPUT_SHORTLIST = REPO_ROOT / "benchmark/candidates/market_candidate_task_ideas_prefilter_shortlist_v0.1.txt"

ARTIFACT_HINT_RE = re.compile(
    r"(报告|草稿|文件|表|清单|摘要|memo|brief|report|draft|json|csv|md|markdown|diff|patch|配置)",
    flags=re.IGNORECASE,
)
VERIFIER_HINT_RE = re.compile(
    r"(检查|验证|比对|返回码|exit code|文件|字段|contains|匹配|状态|draft|report|grader|执行)",
    flags=re.IGNORECASE,
)
SEVERE_RISK_RE = re.compile(
    r"(人工|人工审批|人工判断|实时外网|外部实时|依赖人工|不可控|随机性很高|需要专有权限)",
    flags=re.IGNORECASE,
)


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records


def _family_weight_to_float(raw: str) -> float:
    try:
        return float(raw.strip().rstrip("%"))
    except Exception:
        return 0.0


def _workflow_score(record: dict[str, Any]) -> tuple[int, bool, str]:
    steps = record.get("workflow_steps_zh") or []
    step_count = len(steps)
    if step_count >= 5:
        return 5, True, "步骤数充足，明显是复合 workflow"
    if step_count == 4:
        return 4, True, "有 4 个关键步骤，workflow 较完整"
    if step_count == 3:
        return 3, True, "达到最小复合 workflow 要求"
    if step_count == 2:
        return 1, False, "只有 2 步，太像窄任务"
    return 0, False, "workflow 步骤不足，未形成完整工作流"


def _artifact_score(record: dict[str, Any]) -> tuple[int, bool, str]:
    artifact = str(record.get("artifact_spec_zh") or "").strip()
    if not artifact:
        return 0, False, "没有明确 artifact"
    if len(artifact) >= 40 and ARTIFACT_HINT_RE.search(artifact):
        return 5, True, "artifact 明确，且可落到具体文件/报告/草稿"
    if len(artifact) >= 20:
        return 3, True, "artifact 基本清楚，但还不算特别具体"
    return 1, False, "artifact 描述太短，交付物不够清楚"


def _verifier_score(record: dict[str, Any]) -> tuple[int, bool, str]:
    verifier = str(record.get("verifier_outline_zh") or "").strip()
    numbered_checks = verifier.count("1)") + verifier.count("2)") + verifier.count("；") + verifier.count(";")
    if len(verifier) >= 60 and VERIFIER_HINT_RE.search(verifier):
        score = 5 if numbered_checks >= 2 or verifier.count("检查") >= 2 else 4
        return score, True, "verifier 轮廓明确，看起来能写 deterministic grader"
    if len(verifier) >= 25 and VERIFIER_HINT_RE.search(verifier):
        return 3, True, "verifier 可行，但还需要细化"
    return 1, False, "verifier 过于模糊，难以直接落地"


def _difficulty_score(record: dict[str, Any]) -> tuple[int, bool, str]:
    band = str(record.get("difficulty_band") or "").strip().lower()
    risk_flags = record.get("risk_flags") or []
    severe_risks = [flag for flag in risk_flags if SEVERE_RISK_RE.search(str(flag))]
    if band not in {"medium", "medium-hard", "hard"}:
        return 1, False, f"难度标记异常：{band or 'missing'}"
    if severe_risks and band == "hard":
        return 1, False, f"高难且存在高风险实现依赖：{' | '.join(severe_risks)}"
    if band == "medium":
        return 4, True, "难度适中，适合优先实现"
    if band == "medium-hard":
        return 5, True, "难度和 benchmark 预期较匹配"
    return 3, True, "偏难，但仍可作为候选"


def _implementability_score(record: dict[str, Any]) -> tuple[int, str]:
    local_flag = str(record.get("local_approximation_feasible") or "").strip().lower()
    env = str(record.get("environment_plan") or "").strip()
    if local_flag == "yes":
        return 5, f"本地近似可行，环境 `{env}`"
    if local_flag == "partial":
        return 3, f"本地近似部分可行，环境 `{env}`"
    if local_flag == "no":
        return 1, f"本地近似弱，环境 `{env}`"
    return 2, f"本地近似信息不清楚，环境 `{env}`"


def _market_score(record: dict[str, Any]) -> tuple[int, str]:
    rank = int(record.get("pattern_rank_in_family") or 99)
    family_weight = _family_weight_to_float(str(record.get("family_market_weight") or "0"))
    if rank <= 2 and family_weight >= 20:
        return 5, "高权重 family + 头部 pattern"
    if rank <= 3 and family_weight >= 10:
        return 4, "市场相关性较强"
    if rank <= 5:
        return 3, "市场相关性中等"
    return 2, "市场相关性相对靠后"


def _decision_reason(row: dict[str, Any]) -> str:
    failed = [
        label
        for label, passed in [
            ("workflow", row["workflow_completeness_pass"]),
            ("artifact", row["artifact_clarity_pass"]),
            ("verifier", row["verifier_feasibility_pass"]),
            ("difficulty", row["difficulty_balance_pass"]),
        ]
        if not passed
    ]
    if failed:
        return f"未通过 hard checks: {', '.join(failed)}"
    return "通过 hard checks，按 seed 内综合分排序"


def _score_to_100(
    workflow_score: int,
    artifact_score: int,
    verifier_score: int,
    difficulty_score: int,
    implementability_score: int,
    market_score: int,
) -> float:
    total = (
        workflow_score / 5 * 25
        + artifact_score / 5 * 25
        + verifier_score / 5 * 25
        + difficulty_score / 5 * 10
        + implementability_score / 5 * 10
        + market_score / 5 * 5
    )
    return round(total, 1)


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = [
        "candidate_id",
        "seed_id",
        "family",
        "pattern_id",
        "idea_title_zh",
        "environment_plan",
        "local_approximation_feasible",
        "difficulty_band",
        "workflow_steps_n",
        "workflow_completeness_pass",
        "artifact_clarity_pass",
        "verifier_feasibility_pass",
        "difficulty_balance_pass",
        "workflow_score_0_5",
        "artifact_score_0_5",
        "verifier_score_0_5",
        "difficulty_score_0_5",
        "implementability_score_0_5",
        "market_score_0_5",
        "prefilter_score_100",
        "seed_rank_after_prefilter",
        "decision",
        "decision_reason",
        "workflow_note",
        "artifact_note",
        "verifier_note",
        "difficulty_note",
        "implementability_note",
        "market_note",
        "risk_flags",
        "artifact_spec_zh",
        "verifier_outline_zh",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            export = {key: row.get(key, "") for key in fieldnames}
            export["risk_flags"] = " | ".join(row.get("risk_flags", []))
            writer.writerow(export)


def _write_shortlist(path: Path, rows: list[dict[str, Any]]) -> None:
    shortlisted = [row for row in rows if row["decision"] == "advance_to_implementation"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for row in shortlisted:
            f.write(f"{row['candidate_id']}\n")


def _write_md(path: Path, rows: list[dict[str, Any]], top_n_per_seed: int) -> None:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["seed_id"]].append(row)

    lines = [
        "# Candidate Task Idea Prefilter v0.1",
        "",
        f"- total_candidates: `{len(rows)}`",
        f"- top_n_per_seed: `{top_n_per_seed}`",
        "",
        "## Rules",
        "",
        "- hard checks: workflow / artifact / verifier / difficulty",
        "- 只有通过 hard checks 的 idea 才会参与 seed 内排序",
        "- 每个 seed 默认只推进前 `2` 个 idea 进入实现层",
        "",
    ]

    for seed_id in sorted(grouped):
        seed_rows = sorted(
            grouped[seed_id],
            key=lambda row: (
                0 if row["decision"] == "advance_to_implementation" else 1,
                -float(row["prefilter_score_100"]),
                row["candidate_id"],
            ),
        )
        first = seed_rows[0]
        lines.append(f"## {seed_id} | {first['family']} | {first['pattern_id']}")
        lines.append("")
        for row in seed_rows:
            lines.append(f"### {row['candidate_id']} | {row['idea_title_zh']}")
            lines.append("")
            lines.append(f"- decision: `{row['decision']}`")
            lines.append(f"- score: `{row['prefilter_score_100']}`")
            lines.append(f"- reason: {row['decision_reason']}")
            lines.append(
                "- hard_checks: "
                f"workflow=`{str(row['workflow_completeness_pass']).lower()}` "
                f"artifact=`{str(row['artifact_clarity_pass']).lower()}` "
                f"verifier=`{str(row['verifier_feasibility_pass']).lower()}` "
                f"difficulty=`{str(row['difficulty_balance_pass']).lower()}`"
            )
            lines.append(
                "- sub_scores: "
                f"workflow={row['workflow_score_0_5']} "
                f"artifact={row['artifact_score_0_5']} "
                f"verifier={row['verifier_score_0_5']} "
                f"difficulty={row['difficulty_score_0_5']} "
                f"implementability={row['implementability_score_0_5']} "
                f"market={row['market_score_0_5']}"
            )
            lines.append(f"- artifact: {row['artifact_spec_zh']}")
            lines.append(f"- verifier: {row['verifier_outline_zh']}")
            lines.append(f"- implementability: {row['implementability_note']}")
            if row.get("risk_flags"):
                lines.append(f"- risks: {', '.join(row['risk_flags'])}")
            lines.append("")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Prefilter generated candidate task ideas")
    parser.add_argument("--input-jsonl", default=str(DEFAULT_INPUT_JSONL))
    parser.add_argument("--output-csv", default=str(DEFAULT_OUTPUT_CSV))
    parser.add_argument("--output-md", default=str(DEFAULT_OUTPUT_MD))
    parser.add_argument("--output-shortlist", default=str(DEFAULT_OUTPUT_SHORTLIST))
    parser.add_argument("--top-n-per-seed", type=int, default=2)
    args = parser.parse_args()

    records = _load_jsonl(Path(args.input_jsonl))
    if not records:
        raise SystemExit("No candidate idea records found.")

    scored_rows: list[dict[str, Any]] = []
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for record in records:
        workflow_score, workflow_pass, workflow_note = _workflow_score(record)
        artifact_score, artifact_pass, artifact_note = _artifact_score(record)
        verifier_score, verifier_pass, verifier_note = _verifier_score(record)
        difficulty_score, difficulty_pass, difficulty_note = _difficulty_score(record)
        implementability_score, implementability_note = _implementability_score(record)
        market_score, market_note = _market_score(record)
        total = _score_to_100(
            workflow_score=workflow_score,
            artifact_score=artifact_score,
            verifier_score=verifier_score,
            difficulty_score=difficulty_score,
            implementability_score=implementability_score,
            market_score=market_score,
        )

        row = {
            **record,
            "workflow_steps_n": len(record.get("workflow_steps_zh") or []),
            "workflow_completeness_pass": workflow_pass,
            "artifact_clarity_pass": artifact_pass,
            "verifier_feasibility_pass": verifier_pass,
            "difficulty_balance_pass": difficulty_pass,
            "workflow_score_0_5": workflow_score,
            "artifact_score_0_5": artifact_score,
            "verifier_score_0_5": verifier_score,
            "difficulty_score_0_5": difficulty_score,
            "implementability_score_0_5": implementability_score,
            "market_score_0_5": market_score,
            "prefilter_score_100": total,
            "workflow_note": workflow_note,
            "artifact_note": artifact_note,
            "verifier_note": verifier_note,
            "difficulty_note": difficulty_note,
            "implementability_note": implementability_note,
            "market_note": market_note,
        }
        row["decision_reason"] = _decision_reason(row)
        grouped[row["seed_id"]].append(row)
        scored_rows.append(row)

    for seed_rows in grouped.values():
        passed_rows = [
            row for row in seed_rows
            if row["workflow_completeness_pass"]
            and row["artifact_clarity_pass"]
            and row["verifier_feasibility_pass"]
            and row["difficulty_balance_pass"]
        ]
        passed_rows.sort(
            key=lambda row: (
                -float(row["prefilter_score_100"]),
                row["candidate_id"],
            )
        )
        shortlisted_ids = {
            row["candidate_id"]
            for row in passed_rows[: max(1, args.top_n_per_seed)]
        }
        for rank, row in enumerate(passed_rows, 1):
            row["seed_rank_after_prefilter"] = rank
        for row in seed_rows:
            if row["candidate_id"] in shortlisted_ids:
                row["decision"] = "advance_to_implementation"
            elif (
                row["workflow_completeness_pass"]
                and row["artifact_clarity_pass"]
                and row["verifier_feasibility_pass"]
                and row["difficulty_balance_pass"]
            ):
                row["decision"] = "hold_as_backup"
            else:
                row["decision"] = "drop"
            if "seed_rank_after_prefilter" not in row:
                row["seed_rank_after_prefilter"] = ""

    scored_rows.sort(
        key=lambda row: (
            row["seed_id"],
            0 if row["decision"] == "advance_to_implementation" else 1,
            -float(row["prefilter_score_100"]),
            row["candidate_id"],
        )
    )
    _write_csv(Path(args.output_csv), scored_rows)
    _write_md(Path(args.output_md), scored_rows, args.top_n_per_seed)
    _write_shortlist(Path(args.output_shortlist), scored_rows)

    decision_counts = defaultdict(int)
    for row in scored_rows:
        decision_counts[row["decision"]] += 1

    print(f"Input candidates: {len(scored_rows)}")
    for decision in sorted(decision_counts):
        print(f"- {decision}: {decision_counts[decision]}")
    print(f"Saved CSV: {args.output_csv}")
    print(f"Saved MD: {args.output_md}")
    print(f"Saved shortlist: {args.output_shortlist}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
