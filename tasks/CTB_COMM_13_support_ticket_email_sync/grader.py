"""CTB_COMM_13 grader -- support ticket vs email sync check.

Ground truth source: task.yaml reference_solution + fixtures.

v2.2: WildClawBench mode (operations with CRM task creation).
- Deterministic 55%: tool gate, sync findings, task creation
- Judge 45%: sync check accuracy, report quality
- Fallback: keyword-based scoring when judge unavailable

Ground truth:
  Alpha: Email "crash" vs ticket "slow loading" -> mismatch, new high-priority ticket
  Beta: Email "ERROR-4001" -> no ticket -> needs creation
  Gamma: Email "already fixed" -> ticket resolved -> consistent
  Delta: Email "permission configuration" -> no ticket -> needs creation
  Must create: 3 new tasks (Alpha, Beta, Delta)
"""

from __future__ import annotations

import re
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class SupportTicketEmailSyncGrader(AbstractGrader):
    """Grade support ticket vs email sync check with task creation."""

    SYNC_CHECKS = {
        "Alpha": {"action": "new_ticket", "keywords": ["crash", "mismatch", "slow loading"]},
        "Beta": {"action": "new_ticket", "keywords": ["ERROR-4001", "4001", "no ticket"]},
        "Gamma": {"action": "confirm_close", "keywords": ["fixed", "resolved", "close", "consistent"]},
        "Delta": {"action": "new_ticket", "keywords": ["permission", "configuration", "no ticket"]},
    }

    # ── Judge rubrics ──────────────────────────────────────────────

    _SYNC_ACCURACY_RUBRIC = """\
Evaluate the accuracy of the email-vs-ticket sync check for all 4 customers (0.0-1.0).

## Ground Truth
1. Alpha: Email reports app crash 3 times; CRM ticket says "slow loading" -> MISMATCH. Crash is a new issue, needs a new high-priority ticket.
2. Beta: Email reports ERROR-4001 code error; CRM has no ticket -> needs new ticket creation.
3. Gamma: Email confirms issue is already fixed and thanks support -> ticket can be marked as resolved/closed. CONSISTENT.
4. Delta: Email reports permission configuration issue; CRM has no ticket -> needs new ticket creation.

## Scoring tiers
- 0.9-1.0: All 4 customers correctly identified with right action (3 need new tickets, 1 consistent)
- 0.7-0.8: 3-4 correct identifications; actions mostly right
- 0.5-0.6: 2-3 correct identifications
- 0.3-0.4: 1-2 correct
- 0.0-0.2: No meaningful sync check
"""

    _REPORT_RUBRIC = """\
Evaluate the quality of the sync check report and recommendations (0.0-1.0).

## Expected elements
1. Per-customer comparison: email issue vs CRM ticket status
2. Clear action items: create new tickets for Alpha/Beta/Delta; close Gamma's ticket
3. Priority guidance: Alpha crash should be high priority
4. Summary of sync gaps

## Scoring tiers
- 0.9-1.0: Clear per-customer comparison; all actions correct; priority guidance present
- 0.7-0.8: Most comparisons present; actions mostly correct
- 0.5-0.6: Partial comparisons; some actions
- 0.3-0.4: Minimal comparison
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

        # 2. Deterministic checks (55%)
        det_score = 0.0
        det_score += 0.20 * self._score_data_retrieval(dispatches)
        det_score += 0.40 * self._score_sync_findings(all_text)
        det_score += 0.40 * self._score_task_creation(dispatches, audit_data)

        # 3. Judge scoring (45%)
        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            sync_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._SYNC_ACCURACY_RUBRIC
            ).score
            report_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._REPORT_RUBRIC
            ).score
        else:
            sync_score = self._fallback_sync(all_text)
            report_score = self._fallback_report(all_text)

        # 4. Combine
        completion = tool_penalty * (
            0.55 * det_score
            + 0.25 * sync_score
            + 0.20 * report_score
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
        return 0.50 * min(len(email_calls) / 3, 1.0) + 0.50 * min(len(crm_calls) / 3, 1.0)

    def _score_sync_findings(self, all_text: str) -> float:
        """Check each customer's sync finding is present with supporting keywords."""
        found = 0
        for name, check in self.SYNC_CHECKS.items():
            if name not in all_text:
                continue
            idx = all_text.index(name)
            region = all_text[max(0, idx - 100):idx + 400].lower()
            kw_hits = sum(1 for kw in check["keywords"] if kw.lower() in region)
            if kw_hits >= 2:
                found += 1
            elif kw_hits >= 1:
                found += 0.5
        return min(found / 3, 1.0)

    def _score_task_creation(self, dispatches: list[ToolDispatch],
                             audit_data: dict[str, dict] | None) -> float:
        """Check CRM tasks were created (expected: 3)."""
        task_calls = [d for d in dispatches
                      if d.tool_name == "crm_create_task" and d.response_status < 400]
        tasks = self.get_service_actions(audit_data, "crm", "tasks")
        total = len(task_calls) + len(tasks)
        return min(total / 3, 1.0)

    # ── Fallback scorers ───────────────────────────────────────────

    def _fallback_sync(self, all_text: str) -> float:
        """_fallback_: dev-only keyword scoring for sync accuracy."""
        score = 0.0
        lower = all_text.lower()
        if "alpha" in lower and any(k in lower for k in ["crash", "mismatch"]):
            score += 0.20
        if "beta" in lower and any(k in lower for k in ["4001", "error"]):
            score += 0.20
        if "gamma" in lower and any(k in lower for k in ["fixed", "resolved", "close"]):
            score += 0.20
        if "delta" in lower and any(k in lower for k in ["permission", "configuration"]):
            score += 0.20
        return min(score, 1.0)

    def _fallback_report(self, all_text: str) -> float:
        """_fallback_: dev-only keyword scoring for report quality."""
        score = 0.0
        lower = all_text.lower()
        if any(k in lower for k in ["create", "new ticket"]):
            score += 0.25
        if any(k in lower for k in ["consistent", "resolved"]):
            score += 0.20
        if any(k in lower for k in ["high priority", "high-priority", "urgent"]):
            score += 0.15
        if len(all_text.strip()) >= 300:
            score += 0.15
        return min(score, 1.0)
