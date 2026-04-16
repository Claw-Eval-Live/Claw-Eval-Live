#!/usr/bin/env python3
"""Re-grade existing traces using v2.2 graders with LLM judge enabled.

Loads saved trace JSONL files and runs the current (v2.2) graders with a live
LLM judge, then compares new scores against the old scores stored in the trace
end record.

Usage:
    python3 scripts/regrade_with_judge.py \
        --traces-dir traces/your_model/ \
        --judge-model gpt-5.4 \
        --judge-api-key sk-... \
        --output benchmark/regrade_results.json

    # Dry-run — show what would be regraded without calling the judge
    python3 scripts/regrade_with_judge.py \
        --traces-dir traces/your_model/ \
        --dry-run
"""
from __future__ import annotations

import argparse
import inspect
import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from liveclaw_500.config import load_config
from liveclaw_500.graders.llm_judge import LLMJudge
from liveclaw_500.graders.registry import get_grader
from liveclaw_500.models.scoring import compute_task_score, is_pass
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.trace.reader import load_trace

# ---------------------------------------------------------------------------
# The 140 v2 hybrid tasks (deterministic + judge).
# ---------------------------------------------------------------------------
V22_TASK_IDS: list[str] = [
    "CTB_A01_financial_reconciliation",
    "CTB_A02_investment_priority_matrix",
    "CTB_A03_cashflow_risk_memo",
    "CTB_A04_sales_promise_audit",
    "CTB_C01_client_outreach",
    "CTB_C02_meeting_prep_brief",
    "CTB_C05_newsletter_campaign_lead_analysis",
    "CTB_C06_customer_health_churn_risk",
    "CTB_COMM_09_project_retro_prep",
    "CTB_COMM_13_support_ticket_email_sync",
    "CTB_COMM_18_batch3",
    "CTB_COMM_24_meeting_prep_action",
    "CTB_COMM_25_stakeholder_update_compile",
    "CTB_COMM_27_internal_announcement_draft",
    "CTB_CRM_03_renewal_priority_triage",
    "CTB_CRM_04_lead_scoring_validation",
    "CTB_CRM_05_upsell_opportunity_scan",
    "CTB_CRM_08_segment_profitability",
    "CTB_CRM_09_contact_data_cleanup",
    "CTB_D01_multi_doc_merge",
    "CTB_D02_api_changelog",
    "CTB_D03_whitepaper_architecture_report",
    "CTB_DATA_06_contract_revision_verify",
    "CTB_DATA_07_expense_multi_dept_audit",
    "CTB_DATA_08_ecommerce_monthly_reconcile",
    "CTB_DATA_09_ticket_email_crm_reconcile",
    "CTB_DATA_12_customer_churn_predictor",
    "CTB_DATA_20_project_cost_vs_plan",
    "CTB_DOC_03_policy_doc_digest",
    "CTB_DOC_04_multi_doc_dedup",
    "CTB_DOC_05_email_to_report",
    "CTB_DOC_06_kb_consolidation",
    "CTB_DOC_07_changelog_generation",
    "CTB_DOC_09_sop_review",
    "CTB_FIN_10_payment_terms_compliance",
    "CTB_FIN_12_subscription_mrr_analysis",
    "CTB_FIN_17_capex_vs_opex_split",
    "CTB_FIN_19_quarterly_revenue_trend",
    "CTB_FIN_20_working_capital_ratio",
    "CTB_FIN_22_travel_expense_audit",
    "CTB_FIN_24_loan_repayment_schedule",
    "CTB_FIN_25_inventory_valuation",
    "CTB_FIN_29_budget_reforecast",
    "CTB_FIN_30_tax_deduction_review",
    "CTB_HR_01_onboarding_checklist",
    "CTB_HR_02_leave_balance_audit",
    "CTB_HR_03_performance_review_prep",
    "CTB_HR_04_salary_adjustment_review",
    "CTB_HR_05_training_needs_analysis",
    "CTB_HR_06_exit_interview_summary",
    "CTB_HR_07_recruitment_pipeline_status",
    "CTB_HR_09_employee_satisfaction_triage",
    "CTB_HR_10_benefits_enrollment_verify",
    "CTB_HR_11_probation_review_batch",
    "CTB_IR_04_cloud_cost_tool_evaluation",
    "CTB_IR_05_industry_report_digest",
    "CTB_IR_07_vendor_comparison",
    "CTB_IR_12_talent_market_analysis",
    "CTB_IR_13_supply_chain_risk",
    "CTB_IR_14_esg_compliance_report",
    "CTB_IR_15_pricing_benchmark",
    "CTB_IR_16_customer_feedback_mining",
    "CTB_IR_17_product_roadmap_research",
    "CTB_IR_19_industry_benchmark_report",
    "CTB_MGMT_01_quarterly_okr_review",
    "CTB_MGMT_02_budget_allocation_proposal",
    "CTB_MGMT_03_team_capacity_planning",
    "CTB_MGMT_04_strategic_initiative_tracking",
    "CTB_O02_milestone_review_sync",
    "CTB_OPS_01_vendor_sla_compliance",
    "CTB_OPS_02_meeting_room_utilization",
    "CTB_OPS_03_task_completion_velocity",
    "CTB_OPS_04_cross_team_dependency_map",
    "CTB_ORCH_01_lead_to_demo_orchestration",
    "CTB_PRODAPP_01_calendar_conflict_resolve",
    "CTB_PRODAPP_03_task_priority_rebalance",
    "CTB_PRODAPP_04_overdue_task_escalation",
    "CTB_PRODAPP_05_knowledge_article_audit",
    "CTB_PRODAPP_06_action_item_extraction",
    "CTB_PRODAPP_07_weekly_planning",
    "CTB_PRODAPP_08_sprint_retro_prep",
    "CTB_PRODAPP_09_resource_allocation",
    "CTB_PRODAPP_10_project_timeline_adjust",
    "CTB_PRODAPP_11_knowledge_gap_analysis",
    "CTB_PRODAPP_12_standup_preparation",
    "CTB_PRODAPP_13_delegation_review",
    "CTB_PRODAPP_14_meeting_notes_sync",
    "CTB_PRODAPP_15_capacity_forecast",
    "CTB_PRODAPP_16_document_review_schedule",
    "CTB_PRODAPP_17_blocker_resolution",
    "CTB_PRODAPP_19_task_dependency_analysis",
    "CTB_PRODAPP_20_note_consolidation",
    "CTB_PRODAPP_21_milestone_tracking",
    "CTB_PRODAPP_22_workload_balance",
    "CTB_PROD_01_project_kickoff_reconciliation",
    "CTB_PROD_02_weekly_plan_alignment",
    "CTB_PROD_03_sprint_progress_gap",
    "CTB_PROD_04_milestone_status_check",
    "CTB_PROD_05_deadline_risk_alert",
    "CTB_PROD_06_resource_conflict_detect",
    "CTB_PROD_07_weekly_standup_summary",
    "CTB_R02_repository_migration_report",
    "CTB_R03_whiteboard_platform_report",
    "CTB_REPORT_03_project_health_dashboard",
    "CTB_REPORT_04_quarterly_business_review",
    "CTB_REPORT_05_team_productivity_report",
    "CTB_RESEARCH_01_competitor_product_comparison",
    "CTB_RESEARCH_02_market_trend_synthesis",
    "CTB_RESEARCH_03_technology_evaluation",
    "CTB_SALES_01_quota_attainment_report",
    "CTB_SALES_02_deal_risk_assessment",
    "CTB_SALES_04_discount_approval_audit",
    "CTB_SALES_05_customer_reactivation",
    "CTB_SALES_07_cross_sell_opportunity",
    "CTB_SALES_08_commission_dispute_resolve",
    "CTB_SALES_09_quarterly_forecast_vs_actual",
    "CTB_SALES_10_key_account_health",
    "CTB_SEC_01_suspicious_login_alert",
    "CTB_SEC_03_phishing_campaign_analysis",
    "CTB_SEC_04_permission_review",
    "CTB_SEC_05_incident_timeline_reconstruct",
    "CTB_SUPPORT_01_ticket_escalation_review",
    "CTB_SUPPORT_02_customer_complaint_trend",
    "CTB_SUPPORT_03_first_response_time_audit",
    "CTB_SUPPORT_04_resolution_quality_check",
    "CTB_SUPPORT_05_multi_channel_sync",
    "CTB_WORKFLOW_01_end_to_end_onboarding",
    "CTB_WORKFLOW_02_expense_approval_chain",
    "CTB_WORKFLOW_05_quarterly_close_process",
    "CTB_WORKFLOW_06_vendor_onboard_workflow",
    "CTB_WORKFLOW_07_incident_to_resolution",
    "CTB_WORKFLOW_08_campaign_launch_sequence",
    "CTB_WORKFLOW_09_audit_preparation",
    "CTB_WORKFLOW_10_product_launch_checklist",
    "CTB_trisource_gap_reconciliation_audit",
    "CTB_SHELL_08_backup_verification",
    "CTB_SHELL_18_container_restart_analysis",
    "CTB_SHELL_19_log_rotation_audit",
    "CTB_SHELL_22_cache_hit_rate_analysis",
    "CTB_SHELL_23_thread_deadlock_diagnosis",
]


def _grade_with_optional_params(grader, messages, dispatches, task,
                                *, audit_data, judge, media_events,
                                env_snapshot=None):
    """Call grader.grade, passing optional params only when the grader accepts them.

    Mirrors the identical helper in cli.py.
    """
    params = inspect.signature(grader.grade).parameters
    kwargs = {"audit_data": audit_data, "judge": judge}
    if "media_events" in params:
        kwargs["media_events"] = media_events
    if "env_snapshot" in params and env_snapshot is not None:
        kwargs["env_snapshot"] = env_snapshot
    return grader.grade(messages, dispatches, task, **kwargs)


def _is_judge_infra_error(exc: Exception) -> bool:
    """Detect judge infrastructure errors vs grader logic bugs."""
    try:
        from openai import APIError, APITimeoutError, APIConnectionError, RateLimitError
        if isinstance(exc, (APIError, APITimeoutError, APIConnectionError, RateLimitError)):
            return True
    except ImportError:
        pass
    msg = str(exc).lower()
    return any(kw in msg for kw in [
        "timeout", "rate limit", "rate_limit", "connection",
        "502", "503", "429", "judge evaluation failed",
    ])


def _extract_task_id_from_filename(filename: str) -> str | None:
    """Extract task_id from a trace filename like CTB_W01_log_diagnosis_d4fff3c3.jsonl.

    The pattern is <task_id>_<8-hex-chars>.jsonl.
    """
    stem = filename.removesuffix(".jsonl")
    # The last segment after underscore is the 8-char hex run-id
    parts = stem.rsplit("_", 1)
    if len(parts) == 2 and len(parts[1]) == 8:
        try:
            int(parts[1], 16)
            return parts[0]
        except ValueError:
            pass
    return None


def _find_trace_files(traces_dir: Path) -> list[Path]:
    """Find all .jsonl trace files in the given directory."""
    return sorted(traces_dir.glob("*.jsonl"))


def _make_judge(
    model_id: str,
    api_key: str | None,
    base_url: str | None,
    cfg=None,
) -> LLMJudge:
    """Create an LLMJudge from explicit args, falling back to config."""
    resolved_key = api_key
    if not resolved_key and cfg:
        resolved_key = cfg.judge.api_key
    if not resolved_key:
        resolved_key = os.environ.get("JUDGE_API_KEY")
    if not resolved_key:
        raise ValueError(
            "No judge API key provided. Use --judge-api-key, config.yaml judge.api_key, "
            "or set JUDGE_API_KEY env var."
        )

    resolved_url = base_url
    if not resolved_url and cfg:
        resolved_url = cfg.judge.base_url
    if not resolved_url:
        resolved_url = "https://openrouter.ai/api/v1"

    extra_kwargs: dict = {}
    if cfg:
        if cfg.judge.default_headers:
            extra_kwargs["default_headers"] = cfg.judge.default_headers
        if cfg.judge.default_query:
            extra_kwargs["default_query"] = cfg.judge.default_query
        if cfg.judge.extra_body:
            extra_kwargs["extra_body"] = cfg.judge.extra_body

    return LLMJudge(
        model_id=model_id,
        api_key=resolved_key,
        base_url=resolved_url,
        **extra_kwargs,
    )


def _print_table(results: list[dict]) -> None:
    """Print a formatted summary table to stdout."""
    header = (
        f"{'Task ID':<48s} {'Model':<16s} "
        f"{'Old':>6s} {'New':>6s} {'Delta':>7s} "
        f"{'OldP':>5s} {'NewP':>5s} {'Status':<14s}"
    )
    print()
    print("=" * len(header))
    print(header)
    print("-" * len(header))

    for r in results:
        status = r.get("status", "ok")
        old_s = f"{r['old_score']:.2f}" if r["old_score"] is not None else "  n/a"
        new_s = f"{r['new_score']:.2f}" if r["new_score"] is not None else "  n/a"
        delta_val = r.get("score_delta")
        delta_s = f"{delta_val:+.2f}" if delta_val is not None else "   n/a"
        old_p = "PASS" if r.get("old_pass") else "FAIL"
        new_p = "PASS" if r.get("new_pass") else "FAIL"
        if r["old_score"] is None:
            old_p = " n/a"
        if r["new_score"] is None:
            new_p = " n/a"
        print(
            f"{r['task_id']:<48s} {r['model']:<16s} "
            f"{old_s:>6s} {new_s:>6s} {delta_s:>7s} "
            f"{old_p:>5s} {new_p:>5s} {status:<14s}"
        )

    print("=" * len(header))


def _print_summary(results: list[dict]) -> None:
    """Print aggregate statistics."""
    scored = [r for r in results if r["status"] == "ok"]
    if not scored:
        print("\nNo successfully scored traces.")
        return

    old_scores = [r["old_score"] for r in scored if r["old_score"] is not None]
    new_scores = [r["new_score"] for r in scored]
    old_passes = [r for r in scored if r.get("old_pass")]
    new_passes = [r for r in scored if r.get("new_pass")]
    deltas = [r["score_delta"] for r in scored if r["score_delta"] is not None]

    skipped = [r for r in results if r["status"] == "skipped"]
    failures = [r for r in results if r["status"] == "judge_failure"]

    print(f"\n{'SUMMARY':=^60}")
    print(f"  Traces found:       {len(results)}")
    print(f"  Successfully scored: {len(scored)}")
    print(f"  Skipped (not v2.2): {len(skipped)}")
    print(f"  Judge failures:     {len(failures)}")
    print()

    if old_scores:
        print(f"  Avg old score:      {sum(old_scores) / len(old_scores):.4f}")
    print(f"  Avg new score:      {sum(new_scores) / len(new_scores):.4f}")
    if deltas:
        print(f"  Avg score delta:    {sum(deltas) / len(deltas):+.4f}")
    if old_scores:
        print(f"  Old pass rate:      {len(old_passes)}/{len(scored)} "
              f"({100 * len(old_passes) / len(scored):.1f}%)")
    print(f"  New pass rate:      {len(new_passes)}/{len(scored)} "
          f"({100 * len(new_passes) / len(scored):.1f}%)")

    # Show tasks where pass/fail status changed
    flips = [r for r in scored if r.get("old_pass") is not None and r["old_pass"] != r["new_pass"]]
    if flips:
        print(f"\n  Pass/fail status changed ({len(flips)}):")
        for r in flips:
            direction = "FAIL->PASS" if r["new_pass"] else "PASS->FAIL"
            print(f"    {r['task_id']:<46s} {direction}  "
                  f"({r['old_score']:.2f} -> {r['new_score']:.2f})")

    # Show tasks with large score changes (>0.2)
    big_deltas = [r for r in scored if r["score_delta"] is not None and abs(r["score_delta"]) > 0.2]
    if big_deltas:
        print(f"\n  Large score changes (|delta| > 0.2): {len(big_deltas)}")
        for r in sorted(big_deltas, key=lambda x: abs(x["score_delta"]), reverse=True):
            print(f"    {r['task_id']:<46s} {r['score_delta']:+.2f}  "
                  f"({r['old_score']:.2f} -> {r['new_score']:.2f})")

    print("=" * 60)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Re-grade existing traces using v2.2 graders with LLM judge.",
    )
    parser.add_argument(
        "--traces-dir", required=True, type=str,
        help="Directory containing trace .jsonl files",
    )
    parser.add_argument(
        "--tasks-dir", type=str, default="tasks",
        help="Tasks directory (default: tasks)",
    )
    parser.add_argument(
        "--judge-model", type=str, default="gpt-5.4",
        help="Judge model ID (default: gpt-5.4)",
    )
    parser.add_argument(
        "--judge-base-url", type=str, default=None,
        help="Judge API base URL (default: from config.yaml or openrouter)",
    )
    parser.add_argument(
        "--judge-api-key", type=str, default=None,
        help="Judge API key (default: from config.yaml or JUDGE_API_KEY env)",
    )
    parser.add_argument(
        "--output", type=str, default="benchmark/regrade_results.json",
        help="Output JSON file path (default: benchmark/regrade_results.json)",
    )
    parser.add_argument(
        "--tasks", type=str, default=None,
        help="Comma-separated list of task IDs to regrade (default: all 135 v2 hybrid tasks)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be regraded without calling the judge",
    )
    args = parser.parse_args()

    # Resolve paths relative to repo root
    traces_dir = Path(args.traces_dir)
    if not traces_dir.is_absolute():
        traces_dir = REPO / traces_dir
    tasks_dir = Path(args.tasks_dir)
    if not tasks_dir.is_absolute():
        tasks_dir = REPO / tasks_dir
    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = REPO / output_path

    if not traces_dir.is_dir():
        print(f"ERROR: traces directory does not exist: {traces_dir}")
        sys.exit(1)
    if not tasks_dir.is_dir():
        print(f"ERROR: tasks directory does not exist: {tasks_dir}")
        sys.exit(1)

    # Determine which task IDs to regrade
    if args.tasks:
        target_task_ids = set(args.tasks.split(","))
    else:
        target_task_ids = set(V22_TASK_IDS)

    # Load config for judge defaults
    cfg = load_config()

    # Create judge (unless dry-run)
    judge = None
    if not args.dry_run:
        try:
            judge = _make_judge(
                model_id=args.judge_model,
                api_key=args.judge_api_key,
                base_url=args.judge_base_url,
                cfg=cfg,
            )
            print(f"[judge] model={judge.model_id}  base_url={judge.client.base_url}")
        except ValueError as e:
            print(f"ERROR: {e}")
            sys.exit(1)

    # Discover trace files
    trace_files = _find_trace_files(traces_dir)
    if not trace_files:
        print(f"No .jsonl trace files found in {traces_dir}")
        sys.exit(1)

    print(f"Found {len(trace_files)} trace file(s) in {traces_dir}")
    print(f"Target v2.2 tasks: {len(target_task_ids)}")
    if args.dry_run:
        print("[DRY-RUN] No judge calls will be made.\n")

    results: list[dict] = []
    scored_count = 0
    skipped_count = 0
    failure_count = 0

    for i, trace_path in enumerate(trace_files, 1):
        # Determine task_id: try filename first, then read trace_start
        task_id = _extract_task_id_from_filename(trace_path.name)

        if task_id is None:
            # Fall back to reading the trace_start event
            try:
                start, *_ = load_trace(trace_path)
                task_id = start.task_id
            except Exception as exc:
                print(f"[{i}/{len(trace_files)}] SKIP {trace_path.name} - cannot parse: {exc}")
                results.append({
                    "task_id": "unknown",
                    "model": "unknown",
                    "trace_file": trace_path.name,
                    "old_score": None,
                    "new_score": None,
                    "old_pass": None,
                    "new_pass": None,
                    "score_delta": None,
                    "status": "skipped",
                    "reason": f"cannot parse trace: {exc}",
                })
                skipped_count += 1
                continue

        # Check if task is in our target set (with prefix fallback for short filenames)
        if task_id not in target_task_ids:
            # Try prefix match: CTB_IR_05 → CTB_IR_05_industry_report_digest
            prefix_matches = [t for t in target_task_ids if t.startswith(task_id + "_") or t == task_id]
            if len(prefix_matches) == 1:
                old_id = task_id
                task_id = prefix_matches[0]
                print(f"[{i}/{len(trace_files)}] PREFIX {old_id} → {task_id}")
            else:
                # Try reading trace header as last resort
                try:
                    start, *_ = load_trace(trace_path)
                    if start.task_id in target_task_ids:
                        print(f"[{i}/{len(trace_files)}] HEADER {task_id} → {start.task_id}")
                        task_id = start.task_id
                except Exception:
                    pass

        if task_id not in target_task_ids:
            print(f"[{i}/{len(trace_files)}] SKIP {trace_path.name} - "
                  f"{task_id} not in v2.2 task set")
            results.append({
                "task_id": task_id,
                "model": "unknown",
                "trace_file": trace_path.name,
                "old_score": None,
                "new_score": None,
                "old_pass": None,
                "new_pass": None,
                "score_delta": None,
                "status": "skipped",
                "reason": "not in v2.2 task set",
            })
            skipped_count += 1
            continue

        # Check that the grader and task.yaml exist
        task_yaml = tasks_dir / task_id / "task.yaml"
        if not task_yaml.exists():
            print(f"[{i}/{len(trace_files)}] SKIP {trace_path.name} - "
                  f"no task.yaml at {task_yaml}")
            results.append({
                "task_id": task_id,
                "model": "unknown",
                "trace_file": trace_path.name,
                "old_score": None,
                "new_score": None,
                "old_pass": None,
                "new_pass": None,
                "score_delta": None,
                "status": "skipped",
                "reason": f"no task.yaml at {task_yaml}",
            })
            skipped_count += 1
            continue

        # Load trace
        try:
            start, messages, dispatches, media_events, end, audit_data = load_trace(trace_path)
        except Exception as exc:
            print(f"[{i}/{len(trace_files)}] SKIP {trace_path.name} - "
                  f"trace load error: {exc}")
            results.append({
                "task_id": task_id,
                "model": "unknown",
                "trace_file": trace_path.name,
                "old_score": None,
                "new_score": None,
                "old_pass": None,
                "new_pass": None,
                "score_delta": None,
                "status": "skipped",
                "reason": f"trace load error: {exc}",
            })
            skipped_count += 1
            continue

        model = start.model

        # Extract old score from trace end record
        old_score = None
        old_pass = None
        if end is not None:
            old_score = end.task_score
            old_pass = end.passed

        # Dry-run: just report what would be done
        if args.dry_run:
            print(f"[{i}/{len(trace_files)}] WOULD regrade {task_id} "
                  f"(model={model}, old_score={old_score})")
            results.append({
                "task_id": task_id,
                "model": model,
                "trace_file": trace_path.name,
                "old_score": old_score,
                "new_score": None,
                "old_pass": old_pass,
                "new_pass": None,
                "score_delta": None,
                "status": "dry_run",
                "reason": "dry-run mode",
            })
            continue

        # Load task definition and grader
        try:
            task = TaskDefinition.from_yaml(task_yaml)
            grader = get_grader(task.task_id, tasks_dir=tasks_dir,
                                task_dir=task_yaml.parent)
        except Exception as exc:
            print(f"[{i}/{len(trace_files)}] SKIP {task_id} - "
                  f"grader load error: {exc}")
            results.append({
                "task_id": task_id,
                "model": model,
                "trace_file": trace_path.name,
                "old_score": old_score,
                "new_score": None,
                "old_pass": old_pass,
                "new_pass": None,
                "score_delta": None,
                "status": "skipped",
                "reason": f"grader load error: {exc}",
            })
            skipped_count += 1
            continue

        # Grade with judge
        t0 = time.monotonic()
        try:
            scores = _grade_with_optional_params(
                grader, messages, dispatches, task,
                audit_data=audit_data,
                judge=judge,
                media_events=media_events,
            )
            new_score = compute_task_score(scores)
            new_pass = is_pass(new_score)
            elapsed = time.monotonic() - t0

            score_delta = None
            if old_score is not None:
                score_delta = round(new_score - old_score, 4)

            print(f"[{i}/{len(trace_files)}] {task_id:<46s} "
                  f"old={old_score}  new={new_score:.4f}  "
                  f"delta={score_delta:+.4f}  ({elapsed:.1f}s)"
                  if score_delta is not None else
                  f"[{i}/{len(trace_files)}] {task_id:<46s} "
                  f"old=n/a  new={new_score:.4f}  ({elapsed:.1f}s)")

            results.append({
                "task_id": task_id,
                "model": model,
                "trace_file": trace_path.name,
                "old_score": old_score,
                "new_score": round(new_score, 4),
                "old_pass": old_pass,
                "new_pass": new_pass,
                "score_delta": score_delta,
                "completion": round(scores.completion, 4),
                "robustness": round(scores.robustness, 4),
                "communication": round(scores.communication, 4),
                "safety": round(scores.safety, 4),
                "status": "ok",
            })
            scored_count += 1

        except Exception as exc:
            elapsed = time.monotonic() - t0
            if _is_judge_infra_error(exc):
                print(f"[{i}/{len(trace_files)}] JUDGE_FAILURE {task_id} - "
                      f"{type(exc).__name__}: {exc} ({elapsed:.1f}s)")
                results.append({
                    "task_id": task_id,
                    "model": model,
                    "trace_file": trace_path.name,
                    "old_score": old_score,
                    "new_score": None,
                    "old_pass": old_pass,
                    "new_pass": None,
                    "score_delta": None,
                    "status": "judge_failure",
                    "reason": f"{type(exc).__name__}: {exc}",
                })
                failure_count += 1
            else:
                # Grader logic bug -- still record and continue
                print(f"[{i}/{len(trace_files)}] GRADER_ERROR {task_id} - "
                      f"{type(exc).__name__}: {exc} ({elapsed:.1f}s)")
                results.append({
                    "task_id": task_id,
                    "model": model,
                    "trace_file": trace_path.name,
                    "old_score": old_score,
                    "new_score": None,
                    "old_pass": old_pass,
                    "new_pass": None,
                    "score_delta": None,
                    "status": "grader_error",
                    "reason": f"{type(exc).__name__}: {exc}",
                })
                failure_count += 1

    # Print summary table
    _print_table(results)
    _print_summary(results)

    # Write JSON output
    output_data = {
        "traces_dir": str(traces_dir),
        "tasks_dir": str(tasks_dir),
        "judge_model": args.judge_model,
        "dry_run": args.dry_run,
        "total_traces": len(trace_files),
        "scored": scored_count,
        "skipped": skipped_count,
        "judge_failures": failure_count,
        "results": results,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)
    print(f"\nResults written to {output_path}")


if __name__ == "__main__":
    main()
