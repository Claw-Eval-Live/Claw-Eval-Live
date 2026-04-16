"""CTB_DATA_08 grader -- e-commerce monthly three-way reconciliation.

v2.2: hybrid deterministic + judge scoring.
- Deterministic: tool gate, anomaly IDs/amounts, match count
- Judge: reconciliation accuracy, report quality
- Fallback: keyword-based scoring when judge unavailable

Ground truth:
  Matches(4): ORD-001(2580), ORD-002(1990), ORD-005(3200), ORD-007(6800)
  Amount diff(1): ORD-003 order=4500 vs bank=4200 diff=300
  Missing bank+invoice(1): ORD-004 890
  Refund(1): ORD-006 1560 normal
  Bank orphan(1): BANK-999 750 unknown source
"""

from __future__ import annotations

import re
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class EcommerceReconcileGrader(AbstractGrader):

    # -- judge rubrics --

    _DATA_RUBRIC = """\
Evaluate the accuracy of the e-commerce three-way reconciliation data (0.0-1.0).

## Ground Truth
### Matched orders (all three sources agree)
- ORD-001: 2,580 (order = bank = invoice)
- ORD-002: 1,990
- ORD-005: 3,200
- ORD-007: 6,800
Total matched: 4

### Anomalies
1. ORD-003 amount discrepancy: order 4,500 vs bank 4,200 (difference 300)
2. ORD-004 missing bank receipt AND missing invoice: 890
3. BANK-999 bank orphan: 750 with no matching order
4. ORD-006 refund: 1,560 correctly classified as normal

## Scoring tiers
- 0.9-1.0: All 4 anomalies correctly identified with amounts; 4 matches noted; types correct
- 0.7-0.8: 3-4 anomalies identified; most amounts correct
- 0.5-0.6: 2-3 anomalies found; some amounts present
- 0.3-0.4: 1-2 anomalies found
- 0.0-0.2: No meaningful reconciliation
"""

    _REPORT_RUBRIC = """\
Evaluate the quality of the reconciliation report (0.0-1.0).

## Expected elements
1. Summary: count of matched orders (4), count of anomalies (3), anomaly total amount
2. Per-anomaly detail: order ID, anomaly type, amounts from each source
3. ORD-006 refund correctly noted as normal (not an anomaly)
4. Follow-up action recommendations for each anomaly

## Scoring tiers
- 0.9-1.0: All elements present; structured layout; clear anomaly classification
- 0.7-0.8: Has summary and most anomalies; some structure gaps
- 0.5-0.6: Partial report; missing some anomalies or recommendations
- 0.3-0.4: Minimal structure; few details
- 0.0-0.2: No meaningful report
"""

    def grade(self, messages, dispatches, task, audit_data=None, judge=None, media_events=None, env_snapshot=None):
        scores = DimensionScores()
        scores.safety = 1.0
        final_text = self._get_final_assistant_text(messages)
        clean = final_text.replace(",", "").replace("\uff0c", "")

        # 1. Tool gate
        tool_penalty = self._tool_gate(dispatches)

        # 2. Deterministic (30%)
        det_score = self._score_deterministic(clean, final_text)

        # 3. Judge (70%)
        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            data_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._DATA_RUBRIC
            ).score
            report_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._REPORT_RUBRIC
            ).score
        else:
            data_score = self._fallback_data(clean, final_text)
            report_score = self._fallback_report(final_text)

        # 4. Combine
        completion = tool_penalty * (
            0.30 * det_score
            + 0.35 * data_score
            + 0.35 * report_score
        )

        scores.completion = round(min(completion, 1.0), 2)
        scores.robustness = self.compute_robustness(dispatches)
        scores.efficiency_turns = len([m for m in messages if m.message.role == "assistant"])
        return scores

    # -- helpers --

    @staticmethod
    def _has_bounded(text: str, num: str) -> bool:
        return bool(re.search(rf'(?<!\d){re.escape(num)}(?!\d)', text))

    def _tool_gate(self, dispatches):
        calls = [d for d in dispatches
                 if d.tool_name == "finance_list_transactions"
                 and d.response_status < 400]
        return 1.0 if calls else 0.2

    def _score_deterministic(self, clean, final_text):
        """5 dimensions: ORD-003, ORD-004, BANK-999, ORD-006, match count."""
        score = 0.0
        hits = 0
        total = 5

        # D1: ORD-003 with amounts 4500/4200
        if "ORD-003" in final_text:
            if self._has_bounded(clean, "4500") and self._has_bounded(clean, "4200"):
                hits += 1
            elif self._has_bounded(clean, "300"):
                hits += 0.6

        # D2: ORD-004 with amount 890
        if "ORD-004" in final_text and self._has_bounded(clean, "890"):
            hits += 1

        # D3: BANK-999 with amount 750
        if ("BANK-999" in final_text or "BANK999" in final_text) and self._has_bounded(clean, "750"):
            hits += 1

        # D4: ORD-006 classified as refund
        if "ORD-006" in final_text:
            idx = final_text.index("ORD-006")
            region = final_text[max(0, idx - 120):idx + 250]
            if any(kw in region for kw in ["refund", "normal", "退款", "正常"]):
                hits += 1
            else:
                hits += 0.3

        # D5: 4 matches mentioned
        if re.search(r"(?:full match|matched|完全匹配|匹配|fully matched|reconciled).*?[4four]|[4four].*?(?:match|笔|条|order|record).*?(?:匹配|match|reconcil)", final_text, re.IGNORECASE):
            hits += 1

        score = hits / total
        return min(score, 1.0)

    # -- fallbacks (dev-only) --

    def _fallback_data(self, clean, all_text):
        score = 0.0
        if "ORD-003" in all_text and (self._has_bounded(clean, "4500") or self._has_bounded(clean, "4200")):
            score += 0.25
        if "ORD-004" in all_text and self._has_bounded(clean, "890"):
            score += 0.25
        if ("BANK-999" in all_text or "BANK999" in all_text) and self._has_bounded(clean, "750"):
            score += 0.25
        if "ORD-006" in all_text and any(kw in all_text for kw in ["refund", "退款"]):
            score += 0.15
        if self._has_bounded(clean, "300"):
            score += 0.10
        return min(score, 1.0)

    def _fallback_report(self, all_text):
        score = 0.0
        if re.search(r"[4four].*?match|match.*?[4four]|[4四].*?匹配|匹配.*?[4四]|[4four].*?reconcil", all_text, re.IGNORECASE):
            score += 0.2
        if re.search(r"[3three].*?anomal|anomal.*?[3three]|[3三].*?异常|异常.*?[3三]|[3three].*?discrepanc", all_text, re.IGNORECASE):
            score += 0.2
        if "|" in all_text and "---" in all_text:
            score += 0.2
        if any(kw in all_text or kw.lower() in all_text.lower() for kw in ["recommend", "follow-up", "action", "next step", "investigate", "建议", "跟进", "操作"]):
            score += 0.2
        if len(all_text) > 400:
            score += 0.2
        return min(score, 1.0)
