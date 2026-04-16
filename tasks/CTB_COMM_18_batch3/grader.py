"""CTB_COMM_18 grader -- customer complaint resolution tracking.

Ground truth source: task.yaml reference_solution + fixtures.

v2.2: Claw-Eval mode (analysis report).
- Deterministic 35%: tool gate, company coverage, status keywords
- Judge 65%: complaint status accuracy, report quality
- Fallback: keyword-based scoring when judge unavailable

Ground truth:
  Company X: Replied, data restored -> update status to resolved
  Company Y: Unhandled complaint, no CRM record -> needs creation
  Company Z: API rate limit + VIP -> priority handling + new record
  Pending priority: Z (VIP + feature limitation) > Y (display anomaly)
"""

from __future__ import annotations

import re
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class Grader(AbstractGrader):
    """Grade customer complaint resolution tracking."""

    # ── Judge rubrics ──────────────────────────────────────────────

    _STATUS_ACCURACY_RUBRIC = """\
Evaluate the accuracy of complaint status identification for all 3 companies (0.0-1.0).

## Ground Truth
1. Company X: Complaint has been replied to and data has been restored. Status should be updated to RESOLVED.
2. Company Y: Complaint about display anomaly is UNHANDLED. No CRM record exists. Needs a new CRM record.
3. Company Z: API rate limit complaint from a VIP customer, returns HTTP 500 errors. UNHANDLED. Needs PRIORITY handling and a new CRM record.

## Pending Priority Order
- Z should be higher priority than Y because: Z is VIP + involves feature limitation (API rate limit)
- Y is a display anomaly (lower severity)

## Scoring tiers
- 0.9-1.0: All 3 companies correctly identified with status and reasoning; priority order Z > Y correct
- 0.7-0.8: All 3 identified; status mostly correct; priority mentioned
- 0.5-0.6: 2-3 companies identified; some status correct
- 0.3-0.4: 1-2 companies; partial status
- 0.0-0.2: No meaningful status identification
"""

    _REPORT_QUALITY_RUBRIC = """\
Evaluate the quality of the verification report (0.0-1.0).

## Expected elements
1. Per-company comparison: email complaint vs CRM record
2. Clear status for each: resolved, unhandled, needs creation
3. Severity-based prioritization of pending items
4. Actionable recommendations

## Scoring tiers
- 0.9-1.0: Clear per-company analysis; correct statuses; priority ranking; specific recommendations
- 0.7-0.8: Most elements present; reasonable structure
- 0.5-0.6: Partial analysis; some structure
- 0.3-0.4: Minimal analysis
- 0.0-0.2: No meaningful report
"""

    # ── Main grading ──────────────────────────────────────────────

    def grade(
        self,
        messages: list[TraceMessage],
        dispatches: list[ToolDispatch],
        task: TaskDefinition,
        audit_data: dict[str, dict] | None = None,
        judge: Any | None = None,
        media_events: list[MediaLoad] | None = None,
        env_snapshot: dict | None = None,
    ) -> DimensionScores:
        scores = DimensionScores(safety=1.0)
        all_text = self._get_all_assistant_text(messages)

        # 1. Tool gate
        tool_penalty = self._tool_gate(dispatches)

        # 2. Deterministic checks (35%)
        det_score = 0.0
        det_score += 0.30 * self._score_data_retrieval(dispatches)
        det_score += 0.40 * self._score_company_findings(all_text)
        det_score += 0.30 * self._score_priority_order(all_text)

        # 3. Judge scoring (65%)
        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            status_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._STATUS_ACCURACY_RUBRIC
            ).score
            report_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._REPORT_QUALITY_RUBRIC
            ).score
        else:
            status_score = self._fallback_status(all_text)
            report_score = self._fallback_report(all_text)

        # 4. Combine
        completion = tool_penalty * (
            0.35 * det_score
            + 0.35 * status_score
            + 0.30 * report_score
        )

        scores.completion = min(round(completion, 2), 1.0)
        scores.robustness = self.compute_robustness(dispatches)
        scores.efficiency_turns = len(
            [m for m in messages if m.message.role == "assistant"]
        )
        return scores

    # ── Deterministic helpers ──────────────────────────────────────

    def _tool_gate(self, dispatches: list[ToolDispatch]) -> float:
        email_calls = [d for d in dispatches
                       if d.tool_name == "gmail_get_message" and d.response_status < 400]
        crm_calls = [d for d in dispatches
                     if d.tool_name in ("crm_list_customers", "crm_get_customer", "crm_search_customer")
                     and d.response_status < 400]
        if not email_calls and not crm_calls:
            return 0.2
        if not email_calls or not crm_calls:
            return 0.5
        return 1.0

    def _score_data_retrieval(self, dispatches: list[ToolDispatch]) -> float:
        email_calls = [d for d in dispatches
                       if d.tool_name == "gmail_get_message" and d.response_status < 400]
        crm_calls = [d for d in dispatches
                     if d.tool_name in ("crm_list_customers", "crm_get_customer", "crm_search_customer")
                     and d.response_status < 400]
        return 0.50 * min(len(email_calls) / 3, 1.0) + 0.50 * min(len(crm_calls) / 2, 1.0)

    def _score_company_findings(self, all_text: str) -> float:
        """Check each company's finding with keywords."""
        lower = all_text.lower()
        findings = {
            "X": ["restored", "resolved", "update status"],
            "Y": ["unhandled", "create", "display", "chart"],
            "Z": ["API", "500", "VIP", "priority", "limit"],
        }
        found = 0
        for company, kws in findings.items():
            # Use Company X / Company Y / Company Z patterns
            pattern_found = False
            for prefix in [f"Company {company}", f"company {company}", company]:
                if prefix in all_text:
                    idx = all_text.index(prefix)
                    region = all_text[max(0, idx - 100):idx + 400].lower()
                    kw_hits = sum(1 for kw in kws if kw.lower() in region)
                    if kw_hits >= 2:
                        found += 1
                        pattern_found = True
                    elif kw_hits >= 1:
                        found += 0.5
                        pattern_found = True
                    break
            if not pattern_found:
                # Fallback: check globally
                kw_hits = sum(1 for kw in kws if kw.lower() in lower)
                if kw_hits >= 2:
                    found += 0.5
        return min(found / 3, 1.0)

    def _score_priority_order(self, all_text: str) -> float:
        """Check that Z is higher priority than Y."""
        lower = all_text.lower()
        has_z_priority = any(k in lower for k in ["vip", "priority", "urgent"])
        has_both = "z" in lower and "y" in lower
        score = 0.0
        if has_z_priority:
            score += 0.50
        if has_both:
            score += 0.30
        if any(k in lower for k in ["severity", "priority order", "priority ranking"]):
            score += 0.20
        return min(score, 1.0)

    # ── Fallback scorers ───────────────────────────────────────────

    def _fallback_status(self, all_text: str) -> float:
        """_fallback_: dev-only keyword scoring for status accuracy."""
        score = 0.0
        lower = all_text.lower()
        if any(k in lower for k in ["restored", "resolved"]):
            score += 0.20
        if any(k in lower for k in ["unhandled", "no record"]):
            score += 0.20
        if any(k in lower for k in ["api", "500", "rate limit"]):
            score += 0.20
        if "vip" in lower and "priority" in lower:
            score += 0.15
        return min(score, 1.0)

    def _fallback_report(self, all_text: str) -> float:
        """_fallback_: dev-only keyword scoring for report quality."""
        score = 0.0
        if len(all_text.strip()) >= 200:
            score += 0.20
        lower = all_text.lower()
        if any(k in lower for k in ["recommendation", "action"]):
            score += 0.20
        if any(k in lower for k in ["create", "new record"]):
            score += 0.20
        return min(score, 1.0)
