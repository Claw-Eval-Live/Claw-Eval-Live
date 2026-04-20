"""CTB_SALES_04 grader — discount approval compliance audit.

v2.2: mixed mode (analysis + deterministic classification).
- Deterministic 40%: tool gate, company-level classification accuracy, statistics
- Judge 60%: policy understanding rubric, audit report quality rubric
- Fallback: keyword-based scoring when judge unavailable

Ground truth:
  Policy: <=15% mgr, 15-30% director, >30% VP+ROI; old customer +5%; new customer max 20%
  HuaTeng (25%): needs director approval -> needs_escalation
  JinCheng (20%): needs director approval -> needs_escalation
  BoYuan (50%): >30% needs VP+ROI, missing ROI -> violation
  MingDao (12%): renewal, old customer (<=20%), mgr can approve -> compliant
  XingChen (35%): new customer, >20% limit AND >30% needs VP -> violation
  Stats: compliant=1, needs_escalation=2, violation=2
"""

from __future__ import annotations

import re
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


# Ground truth classifications
COMPANIES = {
    "\u534e\u8150": {  # HuaTeng
        "discount": "25%",
        "status": "needs_escalation",
        "status_kw": ["\u5347\u7ea7", "\u603b\u76d1", "\u9700\u5ba1\u6279"],
    },
    "\u9526\u7a0b": {  # JinCheng
        "discount": "20%",
        "status": "needs_escalation",
        "status_kw": ["\u5347\u7ea7", "\u603b\u76d1", "\u9700\u5ba1\u6279"],
    },
    "\u535a\u8fdc": {  # BoYuan
        "discount": "50%",
        "status": "violation",
        "status_kw": ["\u8fdd\u89c4", "\u4e0d\u5408\u89c4", "\u62d2\u7edd"],
    },
    "\u660e\u9053": {  # MingDao
        "discount": "12%",
        "status": "compliant",
        "status_kw": ["\u5408\u89c4", "\u901a\u8fc7", "\u7ecf\u7406\u53ef\u6279",
                      "\u53ef\u6279\u51c6"],
    },
    "\u661f\u8fb0": {  # XingChen
        "discount": "35%",
        "status": "violation",
        "status_kw": ["\u8fdd\u89c4", "\u4e0d\u5408\u89c4", "\u8d85\u51fa",
                      "\u8d85\u9650"],
    },
}


class DiscountAuditGrader(AbstractGrader):
    """Grade a discount approval compliance audit report."""

    # ── Judge rubrics ──────────────────────────────────────────────

    _POLICY_RUBRIC = """\
Evaluate whether the agent correctly understood and applied the discount policy (0.0-1.0).

## Ground Truth — Discount Policy
1. Discount <= 15%: sales manager can approve directly
2. Discount 15%-30%: needs sales director approval
3. Discount > 30%: needs VP approval AND written ROI justification
4. Renewal for existing customers: extra 5% allowance (so effective limit is +5%)
5. New customer first order: max 20% discount

## Company Assessments
1. HuaTeng (Huateng Group): 25% discount on 80wan → needs director (15-30% range) → needs_escalation
2. JinCheng (Jincheng Logistics): 20% discount on 45wan → needs director (15-30% range) → needs_escalation
3. BoYuan (Boyuan Consulting): 50% discount on 30wan → >30% needs VP+ROI, no ROI provided → violation
4. MingDao (Mingdao Software): 12% renewal on 25wan → existing customer, within 20% (15%+5%) → compliant
5. XingChen (Xingchen Education): 35% new customer on 60wan → new customer max 20%, AND >30% → violation

## Scoring tiers
- 0.9-1.0: All 5 correctly classified with correct reasoning per policy rule
- 0.7-0.8: 4-5 correct classifications; reasoning mostly sound
- 0.5-0.6: 3-4 correct; some policy misapplication
- 0.3-0.4: Only 1-2 correct
- 0.0-0.2: No meaningful policy application
"""

    _REPORT_RUBRIC = """\
Evaluate the quality and structure of the audit report (0.0-1.0).

## Expected report elements
1. Policy summary at the top
2. Per-company audit: customer name, original amount, discount rate, discounted amount, applicant, status, reasoning
3. Summary statistics: compliant=1, needs_escalation=2, violation=2
4. Handling recommendations (e.g., reject BoYuan, require ROI; flag XingChen)

## Scoring tiers
- 0.9-1.0: All elements present; clear structure (table or formatted list); correct statistics; actionable recommendations
- 0.7-0.8: Most elements present; statistics mostly correct
- 0.5-0.6: Has per-company assessments but incomplete structure
- 0.3-0.4: Partial assessments; no statistics
- 0.0-0.2: No coherent report
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
        final_text = self._get_final_assistant_text(messages)
        clean = final_text.replace(",", "").replace("\uff0c", "")

        # 1. Tool gate
        tool_penalty = self._tool_gate(dispatches)

        # 2. Deterministic checks (40%)
        det_score = 0.0
        det_score += 0.55 * self._score_classifications(final_text)  # 5 companies classified
        det_score += 0.25 * self._score_statistics(final_text)       # correct stats
        det_score += 0.20 * self._score_policy_numbers(final_text)   # policy thresholds

        # 3. Judge scoring (60%)
        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            policy_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._POLICY_RUBRIC
            ).score
            report_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._REPORT_RUBRIC
            ).score
        else:
            policy_score = self._fallback_policy(final_text)
            report_score = self._fallback_report(final_text)

        # 4. Combine
        completion = tool_penalty * (
            0.40 * det_score
            + 0.30 * policy_score
            + 0.30 * report_score
        )

        scores.completion = min(round(completion, 2), 1.0)
        scores.robustness = self.compute_robustness(dispatches)
        scores.efficiency_turns = len(
            [m for m in messages if m.message.role == "assistant"]
        )
        return scores

    # ── Deterministic helpers ──────────────────────────────────────

    @staticmethod
    def _has_bounded(text: str, num: str) -> bool:
        return bool(re.search(rf'(?<!\d){re.escape(num)}(?!\d)', text))

    @staticmethod
    def _get_region(text: str, keyword: str, radius: int = 400) -> str:
        idx = text.find(keyword)
        if idx < 0:
            return ""
        return text[max(0, idx - 80):idx + radius]

    def _tool_gate(self, dispatches: list[ToolDispatch]) -> float:
        """Penalty multiplier: did the agent read the emails?"""
        email_calls = [d for d in dispatches
                       if d.tool_name in ("gmail_list_messages", "gmail_get_message")
                       and d.response_status < 400]
        if not email_calls:
            return 0.2
        if len(email_calls) < 3:
            return 0.5
        return 1.0

    def _score_classifications(self, final_text: str) -> float:
        """Check that each company has the correct status label in its region."""
        correct = 0
        for name, truth in COMPANIES.items():
            if name not in final_text:
                continue
            region = self._get_region(final_text, name)
            if any(kw in region for kw in truth["status_kw"]):
                correct += 1
        return correct / 5.0

    def _score_statistics(self, final_text: str) -> float:
        """Check summary statistics: violation=2, compliant=1, needs_escalation=2."""
        score = 0.0
        if re.search(
            r"\u8fdd\u89c4.{0,15}2|2.{0,8}\u8fdd\u89c4", final_text
        ):
            score += 0.40
        if re.search(
            r"\u5408\u89c4.{0,15}1|1.{0,8}\u5408\u89c4", final_text
        ):
            score += 0.30
        if re.search(
            r"\u5347\u7ea7.{0,15}2|2.{0,8}\u5347\u7ea7", final_text
        ):
            score += 0.30
        return min(score, 1.0)

    def _score_policy_numbers(self, final_text: str) -> float:
        """Check that key policy thresholds are mentioned."""
        score = 0.0
        if "15%" in final_text:
            score += 0.35
        if "30%" in final_text:
            score += 0.35
        if any(kw in final_text for kw in ["\u65b0\u5ba2\u6237", "\u65b0\u5ba2\u9996\u5355",
                                             "20%\u4e0a\u9650"]):
            score += 0.30
        return min(score, 1.0)

    # ── Fallback scorers ───────────────────────────────────────────

    def _fallback_policy(self, final_text: str) -> float:
        """_fallback_: dev-only keyword scoring for policy application."""
        score = 0.0
        for name, truth in COMPANIES.items():
            if name not in final_text:
                continue
            region = self._get_region(final_text, name)
            sub = 0.0
            # Discount rate mentioned
            if truth["discount"] in region:
                sub += 0.3
            # Correct status
            if any(kw in region for kw in truth["status_kw"]):
                sub += 0.5
            # Has reasoning
            if any(kw in region for kw in ["\u56e0\u4e3a", "\u7531\u4e8e",
                                           "\u539f\u56e0", "\u6839\u636e"]):
                sub += 0.2
            score += min(sub, 1.0) / 5
        return min(score, 1.0)

    def _fallback_report(self, final_text: str) -> float:
        """_fallback_: dev-only keyword scoring for report quality."""
        score = 0.0
        # All 5 companies mentioned
        mentioned = sum(1 for name in COMPANIES if name in final_text)
        score += 0.30 * (mentioned / 5)
        # Has table or structure
        if "|" in final_text and "---" in final_text:
            score += 0.20
        # Statistics present
        if re.search(r"\u8fdd\u89c4.{0,15}2", final_text):
            score += 0.15
        if re.search(r"\u5408\u89c4.{0,15}1", final_text):
            score += 0.10
        # Recommendations
        if any(kw in final_text for kw in ["\u5efa\u8bae", "\u5904\u7406",
                                            "\u6267\u884c"]):
            score += 0.15
        # ROI mentioned for BoYuan
        if "ROI" in final_text or "\u8bba\u8bc1" in final_text:
            score += 0.10
        return min(score, 1.0)
