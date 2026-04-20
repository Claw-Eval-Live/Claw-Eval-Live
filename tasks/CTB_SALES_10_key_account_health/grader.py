"""CTB_SALES_10 grader -- key account health assessment.

v2.2: analysis mode (multi-source analysis report).
- Deterministic 35%: tool gate, customer identification, key financial numbers, risk labels
- Judge 65%: assessment accuracy rubric, report quality rubric
- Fallback: keyword-based scoring when judge unavailable

Ground truth (from reference_solution):
  XinYuan Group: 2.65M cumulative, healthy (8-9), expand 1M
  BYD Electronics: 3.96M cumulative, healthy (8-9), biggest expansion 2M (new factory)
  Zoomlion Heavy: 1.85M cumulative, high-risk (4-5), 3 outages, May renewal
  Hisense Video: 1.5M (-80K comp = 1.42M net), critical (3-4), 20% price cut demand, SAP threat, Apr deadline
  Total annual contract: 9M (2.5M+1.8M+3.2M+1.5M)
  High-risk alert: Hisense (most urgent, Apr), Zoomlion (May)
  Expansion opportunities: BYD (2M), XinYuan (1M)
"""

from __future__ import annotations

import re
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class KeyAccountHealthGrader(AbstractGrader):
    """Grade a multi-dimensional key account health assessment."""

    # -- Judge rubrics ---------------------------------------------------------

    _ASSESSMENT_RUBRIC = """\
Evaluate the accuracy of health assessment for each key account (0.0-1.0).

## Ground Truth -- 4 Key Accounts
### 1. XinYuan Group
- Cumulative revenue: 2.65M
- Satisfaction: high
- Renewal risk: low
- Expansion potential: 1M (supply chain module)
- Health score: 8-9 (healthy)

### 2. BYD Electronics
- Cumulative revenue: 3.96M
- Satisfaction: high
- New factory in Huizhou, 2M expansion opportunity (biggest)
- Health score: 8-9 (healthy, best expansion potential)

### 3. Zoomlion Heavy
- Cumulative revenue: 1.85M
- 3 system outages, complaints
- Renewal in May, threatening to switch vendors
- Possible 500K loss
- Health score: 4-5 (high risk)

### 4. Hisense Video
- Cumulative revenue: 1.5M (net 1.42M after 80K compensation)
- Low satisfaction, demanding 20% price cut
- April deadline (most urgent), SAP competition threat
- Health score: 3-4 (critical risk, most urgent)

## Scoring tiers
- 0.9-1.0: All 4 accounts with correct health classification; key numbers match; risk ordering correct (Hisense > Zoomlion)
- 0.7-0.8: All 4 accounts assessed; most data correct; reasonable health scores
- 0.5-0.6: 3+ accounts assessed; some correct data
- 0.3-0.4: 1-2 accounts with meaningful assessment
- 0.0-0.2: No meaningful assessment
"""

    _REPORT_RUBRIC = """\
Evaluate the quality and completeness of the health report (0.0-1.0).

## Expected report structure
1. Per-account section: name, annual contract, cumulative revenue, health score, dimension scores, key findings, action items
2. Total annual contract value: 9M (2.5M+1.8M+3.2M+1.5M)
3. High-risk alert: Hisense (most urgent, April deadline) and Zoomlion (May renewal)
4. Expansion opportunities: BYD (2M new factory) and XinYuan (1M supply chain)
5. Priority action recommendations

## Scoring tiers
- 0.9-1.0: Complete structured report with all 5 elements; correct total; clear risk prioritization
- 0.7-0.8: Has per-account + summary sections; total mostly correct; some prioritization
- 0.5-0.6: Per-account data present but missing summary or incorrect total
- 0.3-0.4: Partial accounts; no overall summary
- 0.0-0.2: No coherent report
"""

    # -- Main grading ----------------------------------------------------------

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
        clean = final_text.replace(",", "").replace("\uff0c", "").replace(
            "\uffe5", "").replace("\u00a5", "")

        # 1. Tool gate
        tool_penalty = self._tool_gate(dispatches)

        # 2. Deterministic checks (35%)
        det_score = 0.0
        det_score += 0.30 * self._score_customer_names(final_text)        # 4 customers mentioned
        det_score += 0.35 * self._score_financial_numbers(clean, final_text)  # key amounts
        det_score += 0.35 * self._score_risk_labels(final_text)           # correct risk classification

        # 3. Judge scoring (65%)
        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            assessment_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._ASSESSMENT_RUBRIC
            ).score
            report_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._REPORT_RUBRIC
            ).score
        else:
            assessment_score = self._fallback_assessment(clean, final_text)
            report_score = self._fallback_report(clean, final_text)

        # 4. Combine
        completion = tool_penalty * (
            0.35 * det_score
            + 0.35 * assessment_score
            + 0.30 * report_score
        )

        scores.completion = min(round(completion, 2), 1.0)
        scores.robustness = self.compute_robustness(dispatches)
        scores.efficiency_turns = len(
            [m for m in messages if m.message.role == "assistant"]
        )
        return scores

    # -- Deterministic helpers -------------------------------------------------

    @staticmethod
    def _has_bounded(text: str, num: str) -> bool:
        return bool(re.search(rf'(?<!\d){re.escape(num)}(?!\d)', text))

    @staticmethod
    def _get_region(text: str, keyword: str, radius: int = 500) -> str:
        idx = text.find(keyword)
        if idx < 0:
            return ""
        return text[max(0, idx - 100):idx + radius]

    def _tool_gate(self, dispatches: list[ToolDispatch]) -> float:
        """Penalty multiplier: did the agent query all 3 services?"""
        gmail_ok = any(d.tool_name in ("gmail_list_messages", "gmail_get_message")
                       and d.response_status < 400 for d in dispatches)
        crm_ok = any(d.tool_name in ("crm_list_customers", "crm_get_customer")
                     and d.response_status < 400 for d in dispatches)
        fin_ok = any(d.tool_name == "finance_list_transactions"
                     and d.response_status < 400 for d in dispatches)
        svc_count = sum([gmail_ok, crm_ok, fin_ok])
        if svc_count == 0:
            return 0.2
        if svc_count == 1:
            return 0.4
        if svc_count == 2:
            return 0.7
        return 1.0

    def _score_customer_names(self, final_text: str) -> float:
        """Check that all 4 key accounts are mentioned."""
        customers = [
            ("\u946b\u6e90", "XinYuan"),
            ("\u6bd4\u4e9a\u8fea", "BYD"),
            ("\u4e2d\u8054", "Zoomlion"),
            ("\u6d77\u4fe1", "Hisense"),
        ]
        found = sum(1 for zh, en in customers if zh in final_text or en in final_text)
        return found / 4.0

    def _score_financial_numbers(self, clean: str, final_text: str) -> float:
        """Check key financial figures from ground truth."""
        checks = [
            (any(v in clean for v in ["265\u4e07", "2650000", "2.65M", "2,650,000"]), 0.8),
            (any(v in clean for v in ["396\u4e07", "3960000", "3.96M", "3,960,000"]), 0.8),
            (any(v in clean for v in ["900\u4e07", "9000000", "9M", "9,000,000"]), 1.0),
            (any(v in clean for v in ["200\u4e07", "2M", "2,000,000"]) and
             any(k in final_text for k in ["\u6bd4\u4e9a\u8fea", "BYD"]), 0.7),
            (any(v in clean for v in ["100\u4e07", "1M", "1,000,000"]) and
             any(k in final_text for k in ["\u946b\u6e90", "XinYuan"]), 0.7),
        ]
        total_weight = sum(w for _, w in checks)
        found_weight = sum(w for hit, w in checks if hit)
        return min(found_weight / total_weight, 1.0) if total_weight > 0 else 0.0

    def _score_risk_labels(self, final_text: str) -> float:
        """Check that risk classifications are correct."""
        score = 0.0
        # Hisense = critical/highest risk
        hx_region = self._get_region(final_text, "Hisense") or self._get_region(final_text, "\u6d77\u4fe1")
        if hx_region and any(k in hx_region for k in ["\u9ad8\u98ce\u9669", "\u5371\u9669",
                                                       "\u6700\u7d27\u6025", "\u6d41\u5931", "\u4e25\u91cd",
                                                       "high risk", "critical", "most urgent",
                                                       "churn", "severe"]):
            score += 0.30
        # Zoomlion = high risk
        zl_region = self._get_region(final_text, "Zoomlion") or self._get_region(final_text, "\u4e2d\u8054")
        if zl_region and any(k in zl_region for k in ["\u98ce\u9669", "\u9884\u8b66",
                                                       "\u5371\u9669", "\u5b95\u673a",
                                                       "risk", "warning", "danger", "outage"]):
            score += 0.25
        # XinYuan = healthy
        xy_region = self._get_region(final_text, "XinYuan") or self._get_region(final_text, "\u946b\u6e90")
        if xy_region and any(k in xy_region for k in ["\u5065\u5eb7", "\u826f\u597d", "\u6269\u5c55",
                                                       "healthy", "good", "expansion"]):
            score += 0.20
        # BYD = healthy + expansion
        byd_region = self._get_region(final_text, "BYD") or self._get_region(final_text, "\u6bd4\u4e9a\u8fea")
        if byd_region and any(k in byd_region for k in ["\u5065\u5eb7", "\u6269\u5c55",
                                                         "\u673a\u4f1a", "\u589e\u957f",
                                                         "healthy", "expansion",
                                                         "opportunity", "growth"]):
            score += 0.25
        return min(score, 1.0)

    # -- Fallback scorers ------------------------------------------------------

    def _fallback_assessment(self, clean: str, final_text: str) -> float:
        """_fallback_: dev-only keyword scoring for assessment accuracy."""
        score = 0.0
        # XinYuan healthy
        if any(k in final_text for k in ["\u946b\u6e90", "XinYuan"]):
            score += 0.04
            if any(v in clean for v in ["265\u4e07", "2650000", "2.65M"]):
                score += 0.06
            if any(k in final_text for k in ["\u5065\u5eb7", "\u826f\u597d", "healthy", "good"]):
                score += 0.04
        # BYD expansion
        if any(k in final_text for k in ["\u6bd4\u4e9a\u8fea", "BYD"]):
            score += 0.04
            if any(v in clean for v in ["396\u4e07", "3960000", "3.96M"]):
                score += 0.06
            if any(v in clean for v in ["200\u4e07", "2M"]) or \
               any(k in final_text for k in ["\u65b0\u5de5\u5382", "new factory"]):
                score += 0.06
        # Zoomlion risk
        if any(k in final_text for k in ["\u4e2d\u8054", "Zoomlion"]):
            score += 0.04
            if any(k in final_text for k in ["\u5b95\u673a", "3\u6b21", "\u6295\u8bc9",
                                              "outage", "3 times", "complaint"]):
                score += 0.06
            if any(k in final_text for k in ["\u98ce\u9669", "\u9884\u8b66",
                                              "risk", "warning"]):
                score += 0.05
        # Hisense critical
        if any(k in final_text for k in ["\u6d77\u4fe1", "Hisense"]):
            score += 0.04
            if any(k in final_text for k in ["\u964d\u4ef7", "20%", "SAP",
                                              "price cut", "discount"]):
                score += 0.06
            if any(k in final_text for k in ["4\u6708", "\u7d27\u6025", "\u6d41\u5931",
                                              "April", "urgent", "churn"]):
                score += 0.06
            if any(k in final_text for k in ["\u8865\u507f", "8\u4e07",
                                              "compensation", "80K", "80,000"]):
                score += 0.04
        return min(score, 1.0)

    def _fallback_report(self, clean: str, final_text: str) -> float:
        """_fallback_: dev-only keyword scoring for report quality."""
        score = 0.0
        # All 4 customers mentioned
        customers = [
            ("\u946b\u6e90", "XinYuan"),
            ("\u6bd4\u4e9a\u8fea", "BYD"),
            ("\u4e2d\u8054", "Zoomlion"),
            ("\u6d77\u4fe1", "Hisense"),
        ]
        mentioned = sum(1 for zh, en in customers if zh in final_text or en in final_text)
        score += 0.25 * (mentioned / 4)
        # Total annual value
        if any(v in clean for v in ["900\u4e07", "9000000", "9M", "9,000,000"]):
            score += 0.15
        # High-risk alert
        if any(k in final_text for k in ["\u9ad8\u98ce\u9669\u9884\u8b66", "\u98ce\u9669\u5ba2\u6237",
                                          "high-risk alert", "risk account"]):
            score += 0.15
        # Expansion opportunity
        if any(k in final_text for k in ["\u6269\u5c55\u673a\u4f1a", "\u8ffd\u52a0\u9500\u552e",
                                          "expansion opportunit", "upsell"]):
            score += 0.15
        # Health scores present
        if re.search(r'[3-9]\s*[\u5206\u70b9/]|[3-9]\s*/\s*10|score.*[3-9]', final_text):
            score += 0.10
        # Has table
        if "|" in final_text and "---" in final_text:
            score += 0.10
        # Action items
        if any(k in final_text for k in ["\u884c\u52a8", "\u5efa\u8bae", "\u63aa\u65bd",
                                          "action", "recommend", "measure"]):
            score += 0.10
        return min(score, 1.0)
