"""CTB_SALES_07 grader -- cross-sell opportunity mining.

Ground truth source: task.yaml reference_solution + fixtures.

v2.2: WildClawBench mode (analysis + recommendations).
- Deterministic 55%: tool gate, customer spend totals, product identification, priority order
- Judge 45%: recommendation quality, report structure
- Fallback: keyword-based scoring when judge unavailable

Ground truth:
  Yintai Holdings: 1800K, has ERP Advanced+HR -> recommend CRM/data analytics
  Dahua Group: 920K, has ERP -> recommend upgrade/HR
  Xinhe: 585K, has CRM -> recommend data analytics
  Tongda Logistics: 350K, has logistics mgmt -> recommend supply chain/ERP
  Kaide Education: 230K, has learning platform -> recommend HR/data analytics
  Priority: Yintai > Dahua > Xinhe > Tongda > Kaide
"""

from __future__ import annotations

import re
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class Grader(AbstractGrader):
    """Grade cross-sell opportunity mining report."""

    _CROSS_SELL_RUBRIC = """\
Evaluate the accuracy of cross-sell analysis and recommendations (0.0-1.0).

## Ground Truth
- Yintai Holdings: 1,800K total spend, owns ERP Advanced + HR -> recommend CRM, Data Analytics
- Dahua Group: 920K total spend, owns ERP Basic -> recommend ERP upgrade, HR
- Xinhe: 585K total spend, owns CRM -> recommend Data Analytics
- Tongda Logistics: 350K total spend, owns Logistics Management -> recommend Supply Chain, ERP
- Kaide Education: 230K total spend, owns Learning Platform -> recommend HR, Data Analytics
- Priority ordering by value: Yintai > Dahua > Xinhe > Tongda > Kaide

## Scoring tiers
- 0.9-1.0: All 5 customers with correct spend, existing products, and relevant recommendations
- 0.7-0.8: 4-5 customers covered; most recommendations relevant
- 0.5-0.6: 3+ customers; some relevant recommendations
- 0.3-0.4: Partial coverage; generic recommendations
- 0.0-0.2: No meaningful cross-sell analysis
"""

    _REPORT_RUBRIC = """\
Evaluate the report quality and strategic value (0.0-1.0).

## Expected elements
1. Per-customer: name, current products, spend, recommended products, rationale
2. Estimated additional revenue from cross-sell
3. Priority sorted by value (high-value customers first)
4. Clear structured format (table or organized list)

## Scoring tiers
- 0.9-1.0: Complete structured report; revenue estimates; priority ordering; actionable insights
- 0.7-0.8: Most elements present; reasonable structure
- 0.5-0.6: Basic per-customer data; missing estimates or ordering
- 0.3-0.4: Incomplete; poor structure
- 0.0-0.2: No meaningful report
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
        clean = all_text.replace(",", "").replace("\uff0c", "").replace("\uffe5", "").replace("\u00a5", "")

        # 1. Tool gate
        tool_penalty = self._tool_gate(dispatches)

        # 2. Deterministic checks (55%)
        det_score = 0.0
        det_score += 0.35 * self._score_customer_spend(clean, all_text)
        det_score += 0.25 * self._score_existing_products(all_text)
        det_score += 0.20 * self._score_recommendations_made(all_text)
        det_score += 0.20 * self._score_priority_order(all_text)

        # 3. Judge scoring (45%)
        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            cross_sell_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._CROSS_SELL_RUBRIC
            ).score
            report_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._REPORT_RUBRIC
            ).score
        else:
            cross_sell_score = self._fallback_cross_sell(clean, all_text)
            report_score = self._fallback_report(all_text)

        # 4. Combine
        completion = tool_penalty * (
            0.55 * det_score
            + 0.25 * cross_sell_score
            + 0.20 * report_score
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

    def _tool_gate(self, dispatches: list[ToolDispatch]) -> float:
        crm_calls = [d for d in dispatches
                     if d.tool_name in ("crm_list_customers", "crm_get_customer")
                     and d.response_status < 400]
        fin_calls = [d for d in dispatches
                     if d.tool_name == "finance_list_transactions"
                     and d.response_status < 400]
        if not crm_calls and not fin_calls:
            return 0.2
        if not crm_calls or not fin_calls:
            return 0.5
        return 1.0

    def _score_customer_spend(self, clean: str, all_text: str) -> float:
        """Check customer spend totals."""
        spend_map = {
            "Yintai": ["1800000", "1800K", "1800"],
            "Dahua": ["920000", "920K", "920"],
            "Xinhe": ["585000", "585K", "585"],
            "Tongda": ["350000", "350K", "350"],
            "Kaide": ["230000", "230K", "230"],
        }
        found = 0
        for name, vals in spend_map.items():
            if name in all_text and any(self._has_bounded(clean, v.replace("K", "")) or v in clean for v in vals):
                found += 1
        return found / len(spend_map)

    def _score_existing_products(self, all_text: str) -> float:
        """Check existing products are identified per customer."""
        lower = all_text.lower()
        checks = [
            "Yintai" in all_text and any(k in lower for k in ["erp", "hr"]),
            "Dahua" in all_text and "erp" in lower,
            "Xinhe" in all_text and "crm" in lower,
            "Tongda" in all_text and any(k in lower for k in ["logistics", "\u7269\u6d41"]),
            "Kaide" in all_text and any(k in lower for k in ["learning", "\u5b66\u4e60"]),
        ]
        return sum(checks) / len(checks)

    def _score_recommendations_made(self, all_text: str) -> float:
        """Check cross-sell recommendations are provided."""
        lower = all_text.lower()
        rec_kw = ["recommend", "suggest", "cross-sell", "upsell",
                  "\u63a8\u8350", "\u5efa\u8bae", "\u4ea4\u53c9\u9500\u552e"]
        customers = ["Yintai", "Dahua", "Xinhe", "Tongda", "Kaide"]
        with_recs = 0
        for name in customers:
            if name in all_text:
                # Check if recommendation keywords appear near the customer
                idx = all_text.find(name)
                region = all_text[max(0, idx - 80):idx + 500]
                if any(k in region.lower() for k in rec_kw):
                    with_recs += 1
        return min(with_recs / 3, 1.0)

    def _score_priority_order(self, all_text: str) -> float:
        """Check priority ordering (Yintai before Kaide)."""
        yt_idx = all_text.find("Yintai")
        kd_idx = all_text.find("Kaide")
        if yt_idx >= 0 and kd_idx >= 0 and yt_idx < kd_idx:
            return 1.0
        if yt_idx >= 0 or kd_idx >= 0:
            return 0.3
        return 0.0

    # ── Fallback scorers ───────────────────────────────────────────

    def _fallback_cross_sell(self, clean: str, all_text: str) -> float:
        """_fallback_: dev-only keyword scoring for cross-sell analysis."""
        score = 0.0
        customers = ["Yintai", "Dahua", "Xinhe", "Tongda", "Kaide"]
        score += 0.30 * (sum(1 for c in customers if c in all_text) / 5)
        products = ["ERP", "CRM", "HR", "Data Analytics", "Logistics",
                    "Learning", "Supply Chain"]
        score += 0.35 * min(sum(1 for p in products if p.lower() in all_text.lower()) / 3, 1.0)
        if any(k.lower() in all_text.lower() for k in ["cross-sell", "recommend", "\u63a8\u8350"]):
            score += 0.20
        if any(k.lower() in all_text.lower() for k in ["revenue", "estimate", "\u6536\u5165"]):
            score += 0.15
        return min(score, 1.0)

    def _fallback_report(self, all_text: str) -> float:
        """_fallback_: dev-only keyword scoring for report structure."""
        score = 0.0
        if "|" in all_text and "---" in all_text:
            score += 0.30
        if any(k.lower() in all_text.lower() for k in ["priority", "sorted", "\u4f18\u5148"]):
            score += 0.25
        if any(k.lower() in all_text.lower() for k in ["total", "estimated", "\u603b\u8ba1"]):
            score += 0.25
        struct_kw = ["current product", "recommended", "spend",
                     "\u73b0\u6709\u4ea7\u54c1", "\u63a8\u8350"]
        score += 0.20 * min(sum(1 for k in struct_kw if k.lower() in all_text.lower()) / 2, 1.0)
        return min(score, 1.0)
