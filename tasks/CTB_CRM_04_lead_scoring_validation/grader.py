"""CTB_CRM_04 grader -- lead scoring validation from email activity.

Ground truth source: task.yaml reference_solution + fixtures.

v2.2: analysis mode (analysis report).
- Deterministic 35%: tool gate, lead coverage, key findings
- Judge 65%: scoring accuracy, validation report quality
- Fallback: keyword-based scoring when judge unavailable

Ground truth:
  LEAD-301 (Prospect A, CRM=40): 2 emails + quote + demo -> should be 80+ -> underscored by 40
  LEAD-302 (Prospect B, CRM=70): general inquiry -> should be 30 -> overscored by 40
  LEAD-303 (Prospect C, CRM=50): urgent + $500K + 2 weeks -> should be 90+ -> severely underscored
  LEAD-304 (Prospect D, CRM=60): unsubscribed -> should be 0/invalid -> mark invalid
"""

from __future__ import annotations

import re
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class LeadScoringValidationGrader(AbstractGrader):
    """Grade lead scoring validation report."""

    @staticmethod
    def _has_bounded(text: str, num: str) -> bool:
        return bool(re.search(rf'(?<!\d){re.escape(num)}(?!\d)', text))

    # ── Judge rubrics ──────────────────────────────────────────────

    _SCORING_ACCURACY_RUBRIC = """\
Evaluate the accuracy of lead score validation for all 4 prospects (0.0-1.0).

## Ground Truth
1. Prospect A (CRM score 40): Has 2 email interactions + quote request + demo request -> actual intent is HIGH, should be 80+. UNDERSCORED by ~40 points.
2. Prospect B (CRM score 70): Only 1 general inquiry email -> actual intent is LOW, should be ~30. OVERSCORED by ~40 points.
3. Prospect C (CRM score 50): Urgent need + $500K budget + 2-week timeline -> actual intent is HIGHEST, should be 90+. SEVERELY UNDERSCORED by ~40 points. This is the highest priority lead.
4. Prospect D (CRM score 60): Has unsubscribed/opted out -> should be 0 or INVALID. Must be marked as disqualified.

## Scoring tiers
- 0.9-1.0: All 4 leads correctly assessed with direction (under/over/invalid); C identified as highest priority; D as invalid
- 0.7-0.8: 3-4 leads correctly assessed; C and D handled correctly
- 0.5-0.6: 2-3 leads assessed; some directions correct
- 0.3-0.4: 1-2 leads assessed
- 0.0-0.2: No meaningful validation
"""

    _REPORT_QUALITY_RUBRIC = """\
Evaluate the quality of the validation report (0.0-1.0).

## Expected elements
1. Per-lead comparison: CRM score vs recommended score with evidence
2. Clear adjustment rationale for each lead
3. Operational follow-up recommendations (sales follow-up, nurture, disqualify, score update)
4. Structured summary or table for CRM handoff

## Scoring tiers
- 0.9-1.0: Clear per-lead analysis with evidence; strong operational recommendations; easy-to-use handoff structure
- 0.7-0.8: Most leads analyzed; reasonable recommendations
- 0.5-0.6: Partial analysis; some recommendations
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
        det_score += 0.25 * self._score_data_retrieval(dispatches)
        det_score += 0.30 * self._score_lead_findings(all_text)
        det_score += 0.25 * self._score_prospect_c_priority(all_text)
        det_score += 0.20 * self._score_prospect_d_invalid(all_text)

        # 3. Judge scoring (65%)
        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            scoring_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._SCORING_ACCURACY_RUBRIC
            ).score
            report_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._REPORT_QUALITY_RUBRIC
            ).score
        else:
            scoring_score = self._fallback_scoring(all_text)
            report_score = self._fallback_report(all_text)

        # 4. Combine
        completion = tool_penalty * (
            0.35 * det_score
            + 0.35 * scoring_score
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
                     if d.tool_name in ("crm_list_customers", "crm_get_customer")
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
                     if d.tool_name in ("crm_list_customers", "crm_get_customer")
                     and d.response_status < 400]
        return 0.50 * min(len(email_calls) / 4, 1.0) + 0.50 * min(len(crm_calls) / 3, 1.0)

    def _score_lead_findings(self, all_text: str) -> float:
        """Check that all 4 leads are mentioned with relevant keywords."""
        lower = all_text.lower()
        leads_found = 0
        lead_patterns = [
            ("prospect a", ["quote", "demo", "underscor", "increase", "80"]),
            ("prospect b", ["general", "inquiry", "overscor", "lower", "30"]),
            ("prospect c", ["urgent", "500", "underscor", "90", "highest"]),
            ("prospect d", ["unsubscrib", "invalid", "remov", "0", "opt"]),
        ]
        for name, kws in lead_patterns:
            if name in lower:
                kw_hits = sum(1 for kw in kws if kw in lower)
                if kw_hits >= 1:
                    leads_found += 1
        return min(leads_found / 3, 1.0)

    def _score_prospect_c_priority(self, all_text: str) -> float:
        lower = all_text.lower()
        if any(k in lower for k in ["prospect c", "lead-303"]):
            if any(k in lower for k in ["highest", "90", "priority", "severely", "top", "urgent"]):
                return 1.0
            return 0.3
        return 0.0

    def _score_prospect_d_invalid(self, all_text: str) -> float:
        lower = all_text.lower()
        if any(k in lower for k in ["prospect d", "lead-304"]):
            if any(k in lower for k in ["invalid", "remov", "disqualif", "0", "exclude"]):
                return 1.0
            return 0.3
        return 0.0

    # ── Fallback scorers ───────────────────────────────────────────

    def _fallback_scoring(self, all_text: str) -> float:
        """_fallback_: dev-only keyword scoring."""
        score = 0.0
        lower = all_text.lower()
        if "prospect a" in lower and "underscor" in lower:
            score += 0.20
        if "prospect b" in lower and "overscor" in lower:
            score += 0.20
        if "prospect c" in lower and "urgent" in lower:
            score += 0.20
        if "prospect d" in lower and "invalid" in lower:
            score += 0.20
        return min(score, 1.0)

    def _fallback_report(self, all_text: str) -> float:
        """_fallback_: dev-only keyword scoring for report quality."""
        score = 0.0
        lower = all_text.lower()
        if any(k in lower for k in ["validation", "comparison", "discrepancy"]):
            score += 0.20
        if any(k in lower for k in ["recommendation", "adjust", "action"]):
            score += 0.20
        if len(all_text.strip()) >= 300:
            score += 0.15
        return min(score, 1.0)
