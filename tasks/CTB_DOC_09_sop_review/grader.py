"""CTB_DOC_09 grader -- SOP review and improvement recommendations.

Ground truth source: task.yaml reference_solution + fixtures.

v2.2: Claw-Eval mode (analysis report).
- Deterministic 35%: tool gate, SOP coverage, key findings
- Judge 65%: issue accuracy, improvement quality
- Fallback: keyword-based scoring when judge unavailable

Ground truth: 3 SOPs reviewed
  Ticket SOP: weekends missing, 15% satisfaction, no escalation, vague KB
  Release SOP: no rollback criteria, staging incomplete, narrow notification, 30min too short
  Onboarding SOP: 48h unrealistic, data import slow, training insufficient, no flagship content
"""

from __future__ import annotations

import re
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


REQUIRED_NOTES = {"note_d09_01", "note_d09_02", "note_d09_03"}
REQUIRED_EMAILS = {"msg_d09_01", "msg_d09_02", "msg_d09_03"}


class Grader(AbstractGrader):
    """Grade SOP review and improvement recommendations."""

    # ── Judge rubrics ──────────────────────────────────────────────

    _ISSUE_RUBRIC = """\
Evaluate the accuracy of SOP issue identification across all 3 SOPs (0.0-1.0).

## Ground Truth
1. Ticket Handling SOP:
   - P0 response missing on weekends (no on-call rotation)
   - Customer satisfaction only 15%
   - No clear escalation path
   - KB articles vague and unhelpful

2. Release SOP:
   - No rollback criteria/thresholds defined
   - Staging checklist incomplete (missing mobile testing)
   - Notification scope too narrow (tech support not included)
   - 30-minute post-release monitoring window too short

3. Client Onboarding SOP:
   - 48-hour kickoff target unrealistic for enterprise clients
   - Historical data import is time-consuming
   - Training session insufficient (needs 4 hours)
   - No flagship-edition specific content

## Scoring tiers
- 0.9-1.0: All issues across all 3 SOPs identified with specific details
- 0.7-0.8: Most issues identified for all 3 SOPs
- 0.5-0.6: 2 SOPs covered; some issues identified
- 0.3-0.4: 1 SOP covered; few issues
- 0.0-0.2: No meaningful issues identified
"""

    _IMPROVEMENT_RUBRIC = """\
Evaluate the quality and actionability of improvement recommendations (0.0-1.0).

## Expected improvements
- Ticket: On-call rotation for weekends; multi-channel satisfaction survey; clear escalation path
- Release: Define metric thresholds for rollback; add mobile testing to checklist; notify tech support; extend monitoring to 1 hour
- Onboarding: Dedicated SOP for enterprise/flagship clients; extend training to 4 hours; streamline data import

## Scoring tiers
- 0.9-1.0: Specific improvements for each SOP issue; prioritized by impact; actionable
- 0.7-0.8: Improvements for most issues; mostly specific
- 0.5-0.6: Generic improvements; some SOP-specific
- 0.3-0.4: Vague suggestions
- 0.0-0.2: No meaningful improvements
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
        lower = all_text.lower()

        # 1. Tool gate
        tool_penalty = self._tool_gate(dispatches)

        # 2. Deterministic checks (35%)
        det_score = 0.0
        det_score += 0.25 * self._score_data_retrieval(dispatches)
        det_score += 0.35 * self._score_sop_coverage(lower)
        det_score += 0.40 * self._score_key_findings(all_text, lower)

        # 3. Judge scoring (65%)
        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            issue_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._ISSUE_RUBRIC
            ).score
            improvement_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._IMPROVEMENT_RUBRIC
            ).score
        else:
            issue_score = self._fallback_issues(all_text, lower)
            improvement_score = self._fallback_improvements(lower)

        # 4. Combine
        completion = tool_penalty * (
            0.35 * det_score
            + 0.35 * issue_score
            + 0.30 * improvement_score
        )

        scores.completion = min(round(completion, 2), 1.0)
        scores.robustness = self.compute_robustness(dispatches)
        scores.efficiency_turns = len(
            [m for m in messages if m.message.role == "assistant"]
        )
        return scores

    # ── Deterministic helpers ──────────────────────────────────────

    def _tool_gate(self, dispatches: list[ToolDispatch]) -> float:
        notes_calls = [d for d in dispatches
                       if d.tool_name in ("notes_list_documents", "notes_get_document")
                       and d.response_status < 400]
        email_calls = [d for d in dispatches
                       if d.tool_name in ("gmail_list_messages", "gmail_get_message")
                       and d.response_status < 400]
        if not notes_calls and not email_calls:
            return 0.2
        if not notes_calls or not email_calls:
            return 0.5
        return 1.0

    def _score_data_retrieval(self, dispatches: list[ToolDispatch]) -> float:
        note_gets = [d for d in dispatches
                     if d.tool_name == "notes_get_document" and d.response_status < 400]
        note_ids = {str(d.request_body.get("note_id")) for d in note_gets}
        email_gets = [d for d in dispatches
                      if d.tool_name == "gmail_get_message" and d.response_status < 400]
        email_ids = {str(d.request_body.get("message_id")) for d in email_gets}
        return 0.55 * (len(note_ids & REQUIRED_NOTES) / len(REQUIRED_NOTES)) + \
               0.45 * (len(email_ids & REQUIRED_EMAILS) / len(REQUIRED_EMAILS))

    def _score_sop_coverage(self, lower: str) -> float:
        sops = [
            any(k in lower for k in ["ticket", "support ticket"]),
            any(k in lower for k in ["release", "deployment"]),
            any(k in lower for k in ["onboarding", "client onboard"]),
        ]
        found = sum(1 for s in sops if s)
        return min(found / 3, 1.0)

    def _score_key_findings(self, all_text: str, lower: str) -> float:
        findings = [
            any(k in lower for k in ["on-call", "on call", "weekend"]),
            "15%" in all_text,
            any(k in lower for k in ["escalat"]),
            any(k in lower for k in ["rollback"]),
            any(k in lower for k in ["mobile"]),
            any(k in lower for k in ["1 hour", "1h", "one hour"]),
            any(k in lower for k in ["48"]),
            any(k in lower for k in ["4 hour", "4h", "four hour"]),
            any(k in lower for k in ["flagship", "enterprise"]),
        ]
        found = sum(1 for f in findings if f)
        return min(found / 5, 1.0)

    # ── Fallback scorers ───────────────────────────────────────────

    def _fallback_issues(self, all_text: str, lower: str) -> float:
        """_fallback_: dev-only keyword scoring."""
        score = 0.0
        if any(k in lower for k in ["weekend", "on-call"]):
            score += 0.10
        if "15%" in all_text:
            score += 0.08
        if "rollback" in lower:
            score += 0.10
        if "mobile" in lower:
            score += 0.08
        if "48" in all_text:
            score += 0.08
        if any(k in lower for k in ["flagship", "enterprise"]):
            score += 0.08
        return min(score, 1.0)

    def _fallback_improvements(self, lower: str) -> float:
        """_fallback_: dev-only keyword scoring for improvements."""
        score = 0.0
        if any(k in lower for k in ["recommendation", "improvement", "suggest"]):
            score += 0.20
        if "priority" in lower:
            score += 0.15
        if any(k in lower for k in ["feedback", "gap", "execution"]):
            score += 0.15
        return min(score, 1.0)
