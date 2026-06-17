#!/usr/bin/env python3
"""Prefilter composite candidate task ideas.

Reads composite_candidates_v1.0.jsonl, scores each candidate on multiple
dimensions, produces a ranked shortlist.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INPUT = REPO_ROOT / "benchmark/candidates/composite_candidates_v1.0.jsonl"
OUTPUT_JSONL = REPO_ROOT / "benchmark/candidates/composite_prefilter_v1.0.jsonl"
OUTPUT_MD = REPO_ROOT / "benchmark/candidates/composite_prefilter_v1.0.md"
OUTPUT_SHORTLIST = REPO_ROOT / "benchmark/candidates/composite_shortlist_v1.0.txt"

# Existing mock services in claw-eval
EXISTING_SERVICES = {
    "calendar", "gmail", "email", "contacts", "todo", "kb", "rss",
    "web", "web_real", "crm", "finance", "helpdesk", "inventory",
    "notes", "ocr", "scheduler", "config", "caption",
}

# Normalize service name aliases
SERVICE_ALIASES = {
    "email_service": "gmail", "mock_email": "gmail", "mock_email_service": "gmail",
    "email_client": "gmail", "mail_service": "gmail",
    "calendar_service": "calendar", "mock_calendar": "calendar",
    "crm_service": "crm", "crm_system": "crm", "mock_crm": "crm",
    "notes_service": "notes", "note_service": "notes", "mock_notes": "notes",
    "todo_service": "todo", "task_service": "todo", "project_management_tool": "todo",
    "mock_local_fs": "local", "local_file_system": "local",
    "local_filesystem": "local", "local_workspace": "local",
    "web_search": "web", "web_search_engine": "web", "mock_web_search": "web",
    "finance_service": "finance", "mock_finance": "finance",
    "billing_service": "finance", "mock_invoicing_tool": "finance",
    "mock_bank_statement_api": "finance",
    "helpdesk_service": "helpdesk", "mock_helpdesk": "helpdesk",
    "rss_service": "rss", "mock_rss": "rss",
    "ocr_service": "ocr", "mock_ocr": "ocr",
}

SEVERE_RISK_RE = re.compile(
    r"(人工|人工审批|人工判断|实时外网|外部实时|不可控|随机性很高|需要专有权限)",
    flags=re.IGNORECASE,
)


def normalize_service(name):
    n = name.lower().strip()
    return SERVICE_ALIASES.get(n, n)


def score_candidate(r):
    """Score a candidate on 6 dimensions, each 0-5. Returns dict with scores."""
    scores = {}

    # 1. Workflow completeness (step count)
    steps = r.get("workflow_steps_zh", [])
    step_est = r.get("step_count_estimate", len(steps))
    if step_est >= 8:
        scores["workflow"] = 5
    elif step_est >= 6:
        scores["workflow"] = 4
    elif step_est >= 4:
        scores["workflow"] = 3
    else:
        scores["workflow"] = 1

    # 2. Artifact clarity
    artifact = r.get("artifact_spec_zh", "")
    if len(artifact) >= 40:
        scores["artifact"] = 5
    elif len(artifact) >= 20:
        scores["artifact"] = 3
    else:
        scores["artifact"] = 1

    # 3. Grader feasibility (checkpoints)
    checkpoints = r.get("grader_checkpoints_zh", [])
    if len(checkpoints) >= 5:
        scores["grader"] = 5
    elif len(checkpoints) >= 3:
        scores["grader"] = 4
    elif len(checkpoints) >= 2:
        scores["grader"] = 3
    else:
        scores["grader"] = 1

    # 4. Mock service feasibility
    raw_services = r.get("required_mock_services", [])
    normalized = [normalize_service(s) for s in raw_services]
    non_local = [s for s in normalized if s != "local"]
    existing_count = sum(1 for s in non_local if s in EXISTING_SERVICES)

    if not non_local or existing_count == len(non_local):
        scores["mock_feasibility"] = 5
    elif existing_count >= len(non_local) * 0.7:
        scores["mock_feasibility"] = 4
    elif existing_count >= len(non_local) * 0.5:
        scores["mock_feasibility"] = 3
    else:
        scores["mock_feasibility"] = 2
    scores["_services_normalized"] = normalized
    scores["_services_existing"] = existing_count
    scores["_services_total"] = len(non_local)
    scores["_services_new"] = [s for s in non_local if s not in EXISTING_SERVICES]

    # 5. Difficulty balance
    band = r.get("difficulty_band", "medium").lower()
    risk_flags = r.get("risk_flags", [])
    severe = [f for f in risk_flags if SEVERE_RISK_RE.search(str(f))]
    if severe and band == "hard":
        scores["difficulty"] = 1
    elif band == "medium-hard":
        scores["difficulty"] = 5
    elif band == "medium":
        scores["difficulty"] = 4
    elif band == "hard":
        scores["difficulty"] = 3
    else:
        scores["difficulty"] = 2

    # 6. Uniqueness placeholder
    scores["uniqueness"] = 4

    # Composite score (100-point scale)
    total = (
        scores["workflow"] / 5 * 20
        + scores["artifact"] / 5 * 15
        + scores["grader"] / 5 * 20
        + scores["mock_feasibility"] / 5 * 25
        + scores["difficulty"] / 5 * 15
        + scores["uniqueness"] / 5 * 5
    )
    scores["total"] = round(total, 1)

    # Pass/fail gates
    scores["pass"] = (
        scores["workflow"] >= 3
        and scores["artifact"] >= 3
        and scores["grader"] >= 3
        and scores["mock_feasibility"] >= 2
    )

    return scores


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default=str(INPUT.relative_to(REPO_ROOT)))
    parser.add_argument("--output-jsonl", default=str(OUTPUT_JSONL.relative_to(REPO_ROOT)))
    parser.add_argument("--output-md", default=str(OUTPUT_MD.relative_to(REPO_ROOT)))
    parser.add_argument("--output-shortlist", default=str(OUTPUT_SHORTLIST.relative_to(REPO_ROOT)))
    args = parser.parse_args()

    input_path = REPO_ROOT / args.input
    output_jsonl = REPO_ROOT / args.output_jsonl
    output_md = REPO_ROOT / args.output_md
    output_shortlist = REPO_ROOT / args.output_shortlist

    records = []
    with open(input_path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                records.append(json.loads(line))

    print(f"Loaded {len(records)} candidates\n")

    scored = []
    for r in records:
        s = score_candidate(r)
        scored.append({**r, "_scores": s})

    scored.sort(key=lambda x: -x["_scores"]["total"])

    passed = [s for s in scored if s["_scores"]["pass"]]
    failed = [s for s in scored if not s["_scores"]["pass"]]

    print(f"Passed gate: {len(passed)} / {len(scored)}")
    print(f"Failed gate: {len(failed)}")

    shortlist = passed

    print(f"\n=== Top 30 Shortlisted ===\n")
    print(f"{'Rank':>4} {'Score':>5} {'Diff':>10} {'Steps':>5} {'NewSvc':>12} {'Title'}")
    print("-" * 100)
    for i, item in enumerate(shortlist[:30], 1):
        s = item["_scores"]
        new_svc = ",".join(s["_services_new"][:2]) if s["_services_new"] else "all-exist"
        print(f"{i:>4} {s['total']:>5.1f} {item.get('difficulty_band','?'):>10} "
              f"{item.get('step_count_estimate',0):>5} {new_svc:>12} "
              f"{item['idea_title_zh'][:50]}")

    if failed:
        print(f"\n=== Failed Gate ({len(failed)}) ===\n")
        for item in failed[:10]:
            s = item["_scores"]
            reasons = []
            if s["workflow"] < 3: reasons.append("workflow")
            if s["artifact"] < 3: reasons.append("artifact")
            if s["grader"] < 3: reasons.append("grader")
            if s["mock_feasibility"] < 2: reasons.append("mock")
            print(f"  {item['idea_title_zh'][:45]:45} fail={','.join(reasons)}")

    new_svc_counter = defaultdict(int)
    for item in shortlist:
        for svc in item["_scores"]["_services_new"]:
            new_svc_counter[svc] += 1

    print(f"\n=== New Mock Services Needed ===\n")
    for svc, cnt in sorted(new_svc_counter.items(), key=lambda x: -x[1])[:20]:
        print(f"  {svc:<40} needed by {cnt:>3} candidates")

    print(f"\n=== Shortlist by Archetype ===\n")
    arch_counts = defaultdict(int)
    for item in shortlist:
        arch_counts[item["archetype"]] += 1
    for arch, cnt in sorted(arch_counts.items(), key=lambda x: -x[1]):
        print(f"  {arch:<40} {cnt:>3}")

    # Write outputs
    output_jsonl.parent.mkdir(parents=True, exist_ok=True)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_shortlist.parent.mkdir(parents=True, exist_ok=True)

    with open(output_jsonl, "w", encoding="utf-8") as f:
        for item in shortlist:
            out = {k: v for k, v in item.items() if not k.startswith("_")}
            out["prefilter_score"] = item["_scores"]["total"]
            out["prefilter_pass"] = item["_scores"]["pass"]
            out["mock_services_normalized"] = item["_scores"]["_services_normalized"]
            out["new_services_needed"] = item["_scores"]["_services_new"]
            f.write(json.dumps(out, ensure_ascii=False) + "\n")

    with open(output_shortlist, "w") as f:
        for item in shortlist:
            f.write(item["candidate_id"] + "\n")

    lines = [
        "# Composite Candidate Prefilter Report v1.0",
        "",
        f"- Input: {len(records)} candidates",
        f"- Passed: **{len(passed)}**",
        f"- Failed: {len(failed)}",
        "",
        "## Top 50 Candidates",
        "",
        "| Rank | Score | Archetype | Difficulty | Steps | Tools | Title |",
        "|------|-------|-----------|-----------|-------|-------|-------|",
    ]
    for i, item in enumerate(shortlist[:50], 1):
        s = item["_scores"]
        lines.append(
            f"| {i} | {s['total']:.1f} | {item['archetype']} | "
            f"{item.get('difficulty_band','?')} | {item.get('step_count_estimate',0)} | "
            f"~{item.get('tool_call_estimate',0)} | {item['idea_title_zh'][:50]} |"
        )

    lines.extend(["", "## New Mock Services Needed", "",
                   "| Service | Candidates |", "|---------|-----------|"])
    for svc, cnt in sorted(new_svc_counter.items(), key=lambda x: -x[1])[:15]:
        lines.append(f"| {svc} | {cnt} |")

    with open(output_md, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"\n=== Output ===")
    print(f"  {output_jsonl}")
    print(f"  {output_md}")
    print(f"  {output_shortlist}")
    print(f"  Shortlist: {len(shortlist)} candidates")


if __name__ == "__main__":
    main()
