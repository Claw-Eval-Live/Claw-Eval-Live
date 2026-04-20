"""CTB_CRM_05 grader -- upsell opportunity scan.

Ground truth source: task.yaml reference_solution + fixtures.

v2.2: analysis mode (analysis report).
- Deterministic 35%: tool gate, opportunity coverage, revenue estimate
- Judge 65%: opportunity accuracy, ranking + analysis quality
- Fallback: keyword-based scoring when judge unavailable

Ground truth:
  Z: multi-site expansion +2 branches, est. +500K (largest)
  X: user expansion 20->50 users, est. +150K
  Y: plan upgrade + data analytics module, est. +120K
  Total opportunity: 770K
"""

from __future__ import annotations

import re
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class Grader(AbstractGrader):
    """Grade upsell opportunity scan report."""

    @staticmethod
    def _has_bounded(text: str, num: str) -> bool:
        return bool(re.search(rf'(?<!\d){re.escape(num)}(?!\d)', text))

    # ── Judge rubrics ──────────────────────────────────────────────

    _OPPORTUNITY_RUBRIC = """\
Evaluate the accuracy of upsell opportunity identification and revenue estimation (0.0-1.0).

## Ground Truth
1. Customer Z: Multi-site expansion -- adding 2 branch offices. Estimated additional revenue: +500K (largest opportunity). Currently on enterprise plan.
2. Customer X: User expansion from 20 to 50 users. Estimated additional revenue: +150K.
3. Customer Y: Plan upgrade + data analytics add-on module. Estimated additional revenue: +120K.
Total opportunity pipeline: 770K.

## Scoring tiers
- 0.9-1.0: All 3 opportunities correctly identified with type, estimated revenue, and ranked by amount (Z > X > Y)
- 0.7-0.8: All 3 identified; revenue estimates mostly correct; ranking present
- 0.5-0.6: 2-3 opportunities identified; some revenue estimates
- 0.3-0.4: 1-2 opportunities; vague estimates
- 0.0-0.2: No meaningful opportunities identified
"""

    _ANALYSIS_RUBRIC = """\
Evaluate the quality of the opportunity analysis and actionability (0.0-1.0).

## Expected elements
1. Cross-reference with CRM current plans to show upgrade path
2. Revenue estimates with reasoning
3. Ranking by opportunity size (Z=500K > X=150K > Y=120K)
4. Actionable next steps for each opportunity

## Scoring tiers
- 0.9-1.0: CRM cross-reference present; clear revenue justification; correct ranking; specific next steps
- 0.7-0.8: Most elements present; reasonable analysis
- 0.5-0.6: Partial cross-reference; basic analysis
- 0.3-0.4: Minimal analysis
- 0.0-0.2: No meaningful analysis
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
        clean = all_text.replace(",", "").replace("\uff0c", "")

        # 1. Tool gate
        tool_penalty = self._tool_gate(dispatches)

        # 2. Deterministic checks (35%)
        det_score = 0.0
        det_score += 0.25 * self._score_data_retrieval(dispatches)
        det_score += 0.40 * self._score_opportunity_detection(all_text, clean)
        det_score += 0.35 * self._score_revenue_total(clean, all_text)

        # 3. Judge scoring (65%)
        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            opp_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._OPPORTUNITY_RUBRIC
            ).score
            analysis_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._ANALYSIS_RUBRIC
            ).score
        else:
            opp_score = self._fallback_opportunity(all_text, clean)
            analysis_score = self._fallback_analysis(all_text)

        # 4. Combine
        completion = tool_penalty * (
            0.35 * det_score
            + 0.35 * opp_score
            + 0.30 * analysis_score
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
        return 0.50 * min(len(email_calls) / 3, 1.0) + 0.50 * min(len(crm_calls) / 2, 1.0)

    def _score_opportunity_detection(self, all_text: str, clean: str) -> float:
        """Check that the 3 opportunities are found with key details."""
        lower = all_text.lower()
        opps_found = 0
        if any(k in lower for k in ["multi-site", "branch", "expansion"]) and self._has_bounded(clean, "500"):
            opps_found += 1
        elif any(k in lower for k in ["multi-site", "branch"]):
            opps_found += 0.5
        if any(k in lower for k in ["50 user", "50 seat", "user expansion"]) and self._has_bounded(clean, "150"):
            opps_found += 1
        elif any(k in lower for k in ["50 user", "50 seat", "expand"]):
            opps_found += 0.5
        if any(k in lower for k in ["analytics", "upgrade"]) and self._has_bounded(clean, "120"):
            opps_found += 1
        elif any(k in lower for k in ["analytics", "upgrade"]):
            opps_found += 0.5
        return min(opps_found / 3, 1.0)

    def _score_revenue_total(self, clean: str, all_text: str) -> float:
        if any(v in clean for v in ["770000", "770K", "770k", "77万"]):
            return 1.0
        if self._has_bounded(clean, "770"):
            return 1.0
        return 0.0

    # ── Fallback scorers ───────────────────────────────────────────

    def _fallback_opportunity(self, all_text: str, clean: str) -> float:
        """_fallback_: dev-only keyword scoring."""
        score = 0.0
        lower = all_text.lower()
        if any(k in lower for k in ["branch", "multi-site"]):
            score += 0.20
        if "500" in clean and "largest" in lower:
            score += 0.15
        if any(k in lower for k in ["50 user", "50 seat"]):
            score += 0.15
        if "analytics" in lower or "upgrade" in lower:
            score += 0.15
        return min(score, 1.0)

    def _fallback_analysis(self, all_text: str) -> float:
        """_fallback_: dev-only keyword scoring for analysis."""
        score = 0.0
        lower = all_text.lower()
        if any(k in lower for k in ["ranking", "ranked", "order"]):
            score += 0.20
        if any(k in lower for k in ["current plan", "crm", "cross-reference"]):
            score += 0.20
        if len(all_text.strip()) >= 200:
            score += 0.15
        return min(score, 1.0)
