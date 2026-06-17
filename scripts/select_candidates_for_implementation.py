#!/usr/bin/env python3
"""Select top candidates from prefiltered list for task implementation.

Usage:
    python3 scripts/select_candidates_for_implementation.py \
        --input benchmark/candidates/composite_candidates_prefilter_v1.0.jsonl \
        --top-n 30 \
        --output benchmark/candidates/selected_for_implementation_v1.0.jsonl
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent


def load_candidates(input_path: Path) -> list[dict[str, Any]]:
    """Load prefiltered candidates from JSONL."""
    candidates = []
    with open(input_path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                candidates.append(json.loads(line.strip()))
    return candidates


def score_candidate(candidate: dict[str, Any]) -> float:
    """Score candidate based on multiple dimensions."""
    # 1. Quality score (from prefilter)
    quality_score = candidate.get("quality_score", 0.0)

    # 2. Family coverage weight
    family_weight = float(candidate.get("family_market_weight", 0.0))

    # 3. Pattern rank (higher rank = better)
    pattern_rank = int(candidate.get("pattern_rank_in_family", 99))
    rank_score = max(0, 5 - pattern_rank) / 5.0  # rank 1 = 1.0, rank 5 = 0.0

    # 4. Archetype diversity bonus
    archetype = candidate.get("archetype", "")
    archetype_bonus = {
        "research-synthesize-deliver": 1.0,  # 基础调研类
        "collect-decide-act": 1.0,           # 日常工作类
        "diagnose-repair-verify": 0.9,       # 排障类
        "multi-source-reconcile": 0.8,       # 对账类
        "orchestrate-sequence": 1.2,        # 编排类（复杂，高价值）
        "ingest-create-publish": 0.9,        # 创作类
        "normal-flow-plus-safety": 1.1,      # 安全类（重要）
    }.get(archetype, 0.7)

    # 5. Implementability bonus
    local_feasible = candidate.get("local_approximation_feasible", "")
    implement_bonus = {
        "yes": 1.2,
        "partial": 1.0,
        "no": 0.7,
    }.get(local_feasible.lower(), 0.8)

    # 6. Difficulty balance (medium-hard preferred)
    difficulty = candidate.get("difficulty_band", "medium")
    difficulty_bonus = {
        "medium": 1.0,
        "medium-hard": 1.1,
        "hard": 0.9,
    }.get(difficulty, 0.8)

    # 7. Step count bonus (5-10 steps ideal)
    steps = candidate.get("step_count_estimate", 8)
    step_bonus = 1.0
    if 5 <= steps <= 10:
        step_bonus = 1.1
    elif steps > 15:
        step_bonus = 0.8

    # Composite score
    score = (quality_score * 0.4 +
             family_weight * 0.2 +
             rank_score * 0.1 +
             archetype_bonus * 0.1 +
             implement_bonus * 0.1 +
             difficulty_bonus * 0.05 +
             step_bonus * 0.05)

    return score


def select_top_candidates(
    candidates: list[dict[str, Any]],
    top_n: int,
    *,
    max_per_family: int,
    max_per_archetype: int,
) -> list[dict[str, Any]]:
    """Select top candidates with configurable diversity constraints."""
    # Score all candidates
    scored = [(score_candidate(c), c) for c in candidates]
    scored.sort(key=lambda x: -x[0])  # Descending by score

    # Apply diversity constraints
    selected = []
    family_counts = defaultdict(int)
    archetype_counts = defaultdict(int)

    for score, candidate in scored:
        family = candidate.get("primary_family", "unknown")
        archetype = candidate.get("archetype", "unknown")

        if family_counts[family] >= max_per_family:
            continue
        if archetype_counts[archetype] >= max_per_archetype:
            continue

        selected.append(candidate)
        family_counts[family] += 1
        archetype_counts[archetype] += 1

        if len(selected) >= top_n:
            break

    return selected


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="benchmark/candidates/composite_prefilter_v1.0.jsonl")
    parser.add_argument("--top-n", type=int, default=30)
    parser.add_argument("--max-per-family", type=int, default=3)
    parser.add_argument("--max-per-archetype", type=int, default=5)
    parser.add_argument("--output", default="benchmark/candidates/selected_for_implementation_v1.0.jsonl")
    args = parser.parse_args()

    input_path = REPO_ROOT / args.input
    output_path = REPO_ROOT / args.output

    if not input_path.exists():
        print(f"Error: Input file {input_path} not found")
        return 1

    print(f"Loading candidates from {input_path}")
    candidates = load_candidates(input_path)
    print(f"Loaded {len(candidates)} prefiltered candidates")

    print(
        f"Selecting top {args.top_n} candidates with diversity constraints "
        f"(max_per_family={args.max_per_family}, max_per_archetype={args.max_per_archetype})..."
    )
    selected = select_top_candidates(
        candidates,
        args.top_n,
        max_per_family=args.max_per_family,
        max_per_archetype=args.max_per_archetype,
    )

    # Add selection metadata
    for i, candidate in enumerate(selected, 1):
        candidate["selection_rank"] = i
        candidate["selection_score"] = score_candidate(candidate)

    # Write selected candidates
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        for candidate in selected:
            f.write(json.dumps(candidate, ensure_ascii=False) + "\n")

    # Generate summary
    families = defaultdict(int)
    archetypes = defaultdict(int)
    for c in selected:
        families[c.get("primary_family", "unknown")] += 1
        archetypes[c.get("archetype", "unknown")] += 1

    print(f"\n=== Selection Summary ===")
    print(f"Selected: {len(selected)} candidates")
    print(f"Families: {dict(families)}")
    print(f"Archetypes: {dict(archetypes)}")
    print(f"Output: {output_path}")

    return 0


if __name__ == "__main__":
    main()