"""CTB_SALES_02 grader -- deal risk assessment.

Ground truth source: task.yaml reference_solution + fixtures.

v2.2: analysis mode (analysis report).
- Deterministic 35%: tool gate, deal coverage, risk classification, Yunfan exclusion
- Judge 65%: risk analysis accuracy, recommendation quality
- Fallback: keyword-based scoring when judge unavailable

Ground truth:
  High risk: Minghui Mfg (2M, CFO/ROI/CEO approval), Hongyuan Tech (1.2M, restructuring/delay)
  Medium risk: Shengda Logistics (850K, competitor 15% lower)
  Low risk: Tiancheng RE (600K, may self-build, Q3)
  Excluded: Yunfan Education (closed-won)
  Total active: 4.65M
"""

from __future__ import annotations

import re
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage

DEALS = {
    "minghui": {
        "anchors": ["Minghui", "\u660e\u8f89"],
        "risk": "high",
        "signals": ["ROI", "CFO", "cost-cutting", "cost cutting", "CEO approval",
                    "\u6210\u672c\u7f29\u51cf", "CEO\u5ba1\u6279"],
    },
    "hongyuan": {
        "anchors": ["Hongyuan", "\u5b8f\u8fdc"],
        "risk": "high",
        "signals": ["restructur", "postpon", "delay", "budget re-approv",
                    "\u91cd\u7ec4", "\u63a8\u8fdf", "\u9884\u7b97\u91cd\u5ba1"],
    },
    "shengda": {
        "anchors": ["Shengda", "\u76db\u8fbe"],
        "risk": "medium",
        "signals": ["competitor", "lower price", "15%", "competi",
                    "\u7ade\u54c1", "\u4f4e\u4ef7", "\u7ade\u4e89"],
    },
    "tiancheng": {
        "anchors": ["Tiancheng", "\u5929\u6210"],
        "risk": "low",
        "signals": ["in-house", "build in-house", "Q3", "self-build",
                    "\u81ea\u5efa"],
    },
}


class Grader(AbstractGrader):
    """Grade a deal risk assessment report."""

    _RISK_ANALYSIS_RUBRIC = """\
Evaluate the accuracy of deal risk assessments (0.0-1.0).

## Ground Truth
- Minghui Manufacturing (2M, negotiation stage): HIGH RISK -- CFO questions ROI, cost-cutting initiative, requires CEO approval
- Hongyuan Tech (1.2M, negotiation stage): HIGH RISK -- internal restructuring, postponed to May, budget needs re-approval
- Shengda Logistics (850K, proposal stage): MEDIUM RISK -- competitor offering 15% lower price, April 5 deadline
- Tiancheng Real Estate (600K, discovery stage): LOW RISK -- may build in-house IT, Q3 planned start
- Yunfan Education: EXCLUDED (closed-won, should not be in active deal analysis)
- Total active deal amount: 4.65M

## Scoring tiers
- 0.9-1.0: All 4 deals correctly classified with specific risk factors; Yunfan excluded; total correct
- 0.7-0.8: 3-4 deals correctly classified; most risk factors identified
- 0.5-0.6: 2-3 deals classified; some risk factors
- 0.3-0.4: 1-2 deals; minimal risk analysis
- 0.0-0.2: No meaningful risk assessment
"""

    _RECOMMENDATION_RUBRIC = """\
Evaluate the quality of risk countermeasures and report structure (0.0-1.0).

## Expected elements
1. Each deal has specific countermeasures/recommended actions
2. Deals are sorted by risk level (high risk first)
3. Total active deal amount and weighted risk amount provided
4. Actionable next steps for each deal
5. Professional report format

## Scoring tiers
- 0.9-1.0: Specific countermeasures for each deal; proper risk ordering; totals included; actionable
- 0.7-0.8: Most deals have countermeasures; reasonable ordering; totals present
- 0.5-0.6: Some countermeasures; basic structure
- 0.3-0.4: Vague recommendations; poor structure
- 0.0-0.2: No meaningful recommendations
"""

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
        lower = all_text.lower()

        # 1. Tool gate
        tool_penalty = self._tool_gate(dispatches)

        # 2. Deterministic checks (35%)
        det_score = 0.0
        det_score += 0.35 * self._score_deal_coverage(all_text, lower)
        det_score += 0.25 * self._score_risk_classification(all_text, lower)
        det_score += 0.20 * self._score_yunfan_exclusion(all_text, lower)
        det_score += 0.20 * self._score_total_and_order(clean, all_text)

        # 3. Judge scoring (65%)
        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            risk_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._RISK_ANALYSIS_RUBRIC
            ).score
            rec_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._RECOMMENDATION_RUBRIC
            ).score
        else:
            risk_score = self._fallback_risk(all_text, lower, clean)
            rec_score = self._fallback_recommendation(all_text, lower)

        # 4. Combine
        completion = tool_penalty * (
            0.35 * det_score
            + 0.35 * risk_score
            + 0.30 * rec_score
        )

        scores.completion = min(round(completion, 2), 1.0)
        scores.robustness = self.compute_robustness(dispatches)
        scores.efficiency_turns = len(
            [m for m in messages if m.message.role == "assistant"]
        )
        return scores

    # ── Deterministic helpers ──────────────────────────────────────

    def _tool_gate(self, dispatches: list[ToolDispatch]) -> float:
        gmail_calls = [d for d in dispatches
                       if d.tool_name in ("gmail_list_messages", "gmail_get_message")
                       and d.response_status < 400]
        crm_calls = [d for d in dispatches
                     if d.tool_name in ("crm_list_customers", "crm_get_customer")
                     and d.response_status < 400]
        if not gmail_calls and not crm_calls:
            return 0.2
        if not gmail_calls or not crm_calls:
            return 0.5
        return 1.0

    def _score_deal_coverage(self, all_text: str, lower: str) -> float:
        """How many of the 4 deals are mentioned with at least 1 signal."""
        found = 0
        for info in DEALS.values():
            anchor_found = any(a in all_text or a.lower() in lower for a in info["anchors"])
            if anchor_found:
                signal_found = sum(1 for s in info["signals"] if s.lower() in lower)
                if signal_found >= 1:
                    found += 1
        return found / len(DEALS)

    def _score_risk_classification(self, all_text: str, lower: str) -> float:
        """Check risk levels are assigned correctly."""
        score = 0.0
        risk_labels = {
            "high": ["high risk", "\u9ad8\u98ce\u9669"],
            "medium": ["medium risk", "\u4e2d\u98ce\u9669"],
            "low": ["low risk", "\u4f4e\u98ce\u9669"],
        }
        for info in DEALS.values():
            anchor_found = any(a in all_text or a.lower() in lower for a in info["anchors"])
            if not anchor_found:
                continue
            expected = info["risk"]
            labels = risk_labels[expected]
            if any(lbl.lower() in lower for lbl in labels):
                score += 1.0 / len(DEALS)
        return min(score, 1.0)

    def _score_yunfan_exclusion(self, all_text: str, lower: str) -> float:
        """Check that Yunfan Education is properly excluded."""
        yunfan_present = any(k in all_text or k.lower() in lower
                            for k in ["\u4e91\u5e06", "Yunfan"])
        exclusion_kw = ["closed-won", "closed won", "excluded", "already signed",
                        "\u5df2\u6210\u4ea4", "\u6392\u9664", "\u5df2\u7b7e\u7ea6",
                        "not included"]
        if yunfan_present and any(k.lower() in lower for k in exclusion_kw):
            return 1.0
        if not yunfan_present:
            return 0.5  # Not mentioning it is acceptable
        return 0.0  # Mentioned but not excluded is wrong

    def _score_total_and_order(self, clean: str, all_text: str) -> float:
        """Check total amount and risk ordering."""
        score = 0.0
        total_variants = ["4650000", "465", "4.65M", "4.65 million"]
        if any(v in clean or v.lower() in clean.lower() for v in total_variants):
            score += 0.5
        # Check ordering: Minghui before Tiancheng
        mh_idx = self._find_anchor(all_text, ["Minghui", "\u660e\u8f89"])
        tc_idx = self._find_anchor(all_text, ["Tiancheng", "\u5929\u6210"])
        if mh_idx >= 0 and tc_idx >= 0 and mh_idx < tc_idx:
            score += 0.5
        return min(score, 1.0)

    @staticmethod
    def _find_anchor(text: str, anchors: list[str]) -> int:
        lower_text = text.lower()
        for anchor in anchors:
            target = anchor.lower()
            if target in lower_text:
                return lower_text.index(target)
        return -1

    # ── Fallback scorers ───────────────────────────────────────────

    def _fallback_risk(self, all_text: str, lower: str, clean: str) -> float:
        """_fallback_: dev-only keyword scoring for risk analysis."""
        score = 0.0
        for info in DEALS.values():
            anchor_found = any(a in all_text for a in info["anchors"])
            if anchor_found:
                signals = sum(1 for s in info["signals"] if s.lower() in lower)
                if signals >= 2:
                    score += 0.20
                elif signals >= 1:
                    score += 0.10
        total_variants = ["4650000", "465", "4.65M"]
        if any(v in clean for v in total_variants):
            score += 0.10
        return min(score, 1.0)

    def _fallback_recommendation(self, all_text: str, lower: str) -> float:
        """_fallback_: dev-only keyword scoring for recommendations."""
        score = 0.0
        rec_kw = ["recommend", "countermeasure", "action", "mitigation",
                  "\u5efa\u8bae", "\u5e94\u5bf9", "\u63aa\u65bd"]
        score += 0.40 * min(sum(1 for k in rec_kw if k.lower() in lower) / 2, 1.0)
        if "|" in all_text or "##" in all_text:
            score += 0.20
        risk_kw = ["high risk", "medium risk", "low risk"]
        score += 0.40 * min(sum(1 for k in risk_kw if k in lower) / 2, 1.0)
        return min(score, 1.0)
