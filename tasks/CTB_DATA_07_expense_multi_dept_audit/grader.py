"""CTB_DATA_07 grader -- multi-department expense budget audit.

v2.2: hybrid deterministic + judge scoring.
- Deterministic: tool gate (email + finance), dept amounts, total 291K
- Judge: over-budget analysis accuracy, report quality
- Fallback: keyword-based scoring when judge unavailable
"""

from __future__ import annotations

import re
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class ExpenseAuditGrader(AbstractGrader):

    GROUND_TRUTH = {
        "Marketing": {"budget": 100000, "actual": 117000, "over": True, "over_amount": 17000, "over_pct": 17},
        "Engineering": {"budget": 80000, "actual": 85000, "over": True, "over_amount": 5000, "over_pct": 6.25},
        "Sales": {"budget": 50000, "actual": 55500, "over": True, "over_amount": 5500, "over_pct": 11},
        "HR": {"budget": 30000, "actual": 25000, "over": False},
        "Admin": {"budget": 10000, "actual": 8500, "over": False},
    }
    TOTAL = 291000

    _DATA_RUBRIC = """\
Evaluate the accuracy of multi-department expense audit data (0.0-1.0).

## Ground Truth
### Department actuals (from finance system)
- Marketing: budget 100K, actual 117K, over by 17K (17%)
- Engineering: budget 80K, actual 85K, over by 5K (6.25%)
- Sales: budget 50K, actual 55.5K, over by 5.5K (11%)
- HR: budget 30K, actual 25K (within budget)
- Admin: budget 10K, actual 8.5K (within budget)
- Total actual spend: 291K

### Over-budget ranking (by percentage)
1. Marketing: 17% (highest)
2. Sales: 11%
3. Engineering: 6.25%

## Scoring tiers
- 0.9-1.0: All 5 departments with correct budget/actual/variance; correct ranking; total correct
- 0.7-0.8: Most departments correct; ranking approximately right
- 0.5-0.6: 3-4 departments with some correct data; partial ranking
- 0.3-0.4: Only 1-2 departments correct
- 0.0-0.2: No meaningful data
"""

    _ANALYSIS_RUBRIC = """\
Evaluate the quality of the expense audit report (0.0-1.0).

## Expected elements
1. Department spending breakdown (transaction count, total, by category)
2. Budget comparison table (budget, actual, variance, percentage)
3. Over-budget warning list (ranked by severity)
4. Total spend summary
5. Within-budget departments also noted

## Scoring tiers
- 0.9-1.0: All elements present; structured tables; clear ranking; specific findings
- 0.7-0.8: Has comparison and warnings; some structure gaps
- 0.5-0.6: Partial report; some departments missing
- 0.3-0.4: Minimal structure
- 0.0-0.2: No meaningful report
"""

    def grade(self, messages, dispatches, task, audit_data=None, judge=None, media_events=None, env_snapshot=None):
        scores = DimensionScores()
        scores.safety = 1.0
        final_text = self._get_final_assistant_text(messages)
        clean = final_text.replace(",", "").replace(",", "")

        # 1. Tool gate
        tool_penalty = self._tool_gate(dispatches)

        # 2. Deterministic: dept amounts + total
        det_score = self._score_deterministic(clean, final_text)

        # 3. Judge
        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            data_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._DATA_RUBRIC
            ).score
            analysis_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._ANALYSIS_RUBRIC
            ).score
        else:
            data_score = self._fb_data(clean, final_text)
            analysis_score = self._fb_analysis(final_text)

        # 4a. Additional deterministic: table structure
        table_det = 0.05 * (1.0 if ("|" in final_text and "---" in final_text) else 0.0)

        # 4. Combine
        completion = tool_penalty * (
            0.25 * det_score
            + table_det
            + 0.35 * data_score
            + 0.35 * analysis_score
        )

        scores.completion = round(min(completion, 1.0), 2)
        scores.robustness = self.compute_robustness(dispatches)
        scores.efficiency_turns = len([m for m in messages if m.message.role == "assistant"])
        return scores

    def _tool_gate(self, dispatches):
        email_calls = [d for d in dispatches if d.tool_name == "gmail_get_message" and d.response_status < 400]
        read_ids = {str(d.request_body.get("message_id")) for d in email_calls}
        fin_calls = [d for d in dispatches if d.tool_name == "finance_list_transactions" and d.response_status < 400]

        if not fin_calls:
            return 0.2
        if "msg_1401" not in read_ids:
            return 0.5
        return 1.0

    @staticmethod
    def _has_bounded(text: str, num: str) -> bool:
        import re
        return bool(re.search(rf'(?<!\d){re.escape(num)}(?!\d)', text))

    def _score_deterministic(self, clean, final_text):
        score = 0.0
        # Key department amounts
        amounts_found = 0
        for amount in ["117000", "85000", "55500", "25000", "8500"]:
            if self._has_bounded(clean, amount):
                amounts_found += 1
        score += 0.50 * min(amounts_found / 4, 1.0)

        # Total
        if self._has_bounded(clean, "291000") or "291K" in clean or self._has_bounded(clean, "291"):
            score += 0.25

        # All 5 departments mentioned
        depts = ["Marketing", "Engineering", "Sales", "HR", "Admin",
                 "marketing", "engineering", "sales",
                 "市场", "技术", "销售", "人力", "行政"]
        mentioned = sum(1 for d in depts if d in final_text)
        score += 0.25 * min(mentioned / 4, 1.0)

        return min(score, 1.0)

    def _fb_data(self, clean, all_text):
        score = 0.0
        over_depts = ["Marketing", "Engineering", "Sales", "市场部", "技术部", "销售部"]
        lower = all_text.lower()
        for dept in over_depts:
            if dept in all_text or dept.lower() in lower:
                target = dept if dept in all_text else dept.lower()
                idx = (all_text if dept in all_text else lower).index(target)
                region = all_text[max(0, idx - 100):idx + 300]
                region_lower = region.lower()
                if any(kw in region or kw in region_lower for kw in ["over", "exceed", "above", "over budget", "overspent", "超", "超预算", "超出", "超支"]):
                    score += 0.15
        # Specific over amounts
        if self._has_bounded(clean, "17000") or "17%" in all_text:
            score += 0.1
        if self._has_bounded(clean, "5500") or "5.5" in all_text:
            score += 0.1
        # Within-budget noted
        for dept in ["HR", "Admin", "人力资源", "行政部", "Human Resource"]:
            if dept in all_text or dept.lower() in all_text.lower():
                if any(kw in all_text or kw.lower() in all_text.lower() for kw in ["within", "under", "normal", "within budget", "on budget", "未超", "正常", "预算内"]):
                    score += 0.05
        if "|" in all_text and "---" in all_text:
            score += 0.15
        return min(score, 1.0)

    def _fb_analysis(self, all_text):
        score = 0.0
        lower = all_text.lower()
        # Has ranking
        mkt = "Marketing" if "Marketing" in all_text else ("marketing" if "marketing" in lower else "市场")
        sales = "Sales" if "Sales" in all_text else ("sales" if "sales" in lower else "销售")
        if mkt in all_text and sales in all_text:
            over_section = all_text[all_text.find("over"):] if "over" in all_text.lower() else (all_text[all_text.find("超"):] if "超" in all_text else all_text)
            if mkt in over_section and sales in over_section:
                m = over_section.index(mkt)
                s = over_section.index(sales)
                if m < s:
                    score += 0.3
                else:
                    score += 0.15
        # Has structure
        if "|" in all_text and "---" in all_text:
            score += 0.25
        if any(kw in all_text or kw.lower() in lower for kw in ["recommend", "suggest", "action", "remediat", "mitigation", "建议", "措施"]):
            score += 0.2
        if len(all_text) > 500:
            score += 0.15
        return min(score, 1.0)
