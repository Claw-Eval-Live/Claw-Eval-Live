"""CTB_PRODAPP_15 grader -- capacity forecast.

v2.2: WildClawBench mode.
- Deterministic 55%: tool gate, member coverage, key member analysis, risk identification
- Judge 45%: capacity analysis quality, recommendations quality

Ground truth: 4 members (Xie Ming, Cao Li, Yuan Tao, Zhong Yu).
Xie Ming: 10.5h meetings + 20h tasks. Cao Li: 7h meetings + 30h tasks (highest).
Standard: 40h/week. Risk tasks: where required > available.
"""

from __future__ import annotations

import re
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class Grader(AbstractGrader):

    _CAPACITY_RUBRIC = """\
Evaluate the accuracy of per-person capacity forecast (0.0-1.0).

## Ground Truth
- Xie Ming: 10.5h meetings + 20h tasks = high utilization
- Cao Li: 7h meetings + 30h tasks = highest task load, at risk
- Yuan Tao: moderate load
- Zhong Yu: moderate load
- Standard: 40h/week per person, 8h/day

## Scoring tiers
- 0.9-1.0: All 4 members with meeting hours + task hours + available hours; correct numbers
- 0.7-0.8: All members covered; numbers mostly correct
- 0.5-0.6: 3+ members; partial numbers
- 0.3-0.4: 1-2 members
- 0.0-0.2: No capacity forecast
"""

    _RISK_RUBRIC = """\
Evaluate the quality of risk identification and workload adjustment recommendations (0.0-1.0).

## Expected
- Cao Li at highest risk (30h tasks + 7h meetings leaves little buffer)
- Xie Ming also at risk (20h tasks + 10.5h meetings)
- Tasks that may miss deadlines should be flagged
- Recommendations for workload adjustment

## Scoring tiers
- 0.9-1.0: At-risk tasks identified; mitigation proposed; capacity computation shown
- 0.7-0.8: Key risks identified; some mitigation
- 0.5-0.6: Partial risk awareness
- 0.3-0.4: Generic risk mention
- 0.0-0.2: No risk analysis
"""

    def grade(self, messages, dispatches, task, audit_data=None,
              judge=None, media_events=None, env_snapshot=None):
        scores = DimensionScores(safety=1.0)
        all_text = self._get_all_assistant_text(messages)
        lowered = all_text.lower()

        tool_penalty = self._tool_gate(dispatches)

        det_score = 0.0
        det_score += 0.15 * self._score_data_retrieval(dispatches, audit_data)
        det_score += 0.20 * self._score_member_coverage(all_text, lowered)
        det_score += 0.20 * self._score_xie_ming(all_text)
        det_score += 0.20 * self._score_cao_li(all_text)
        det_score += 0.15 * self._score_risk_flags(lowered)
        det_score += 0.10 * self._score_capacity_computation(all_text, lowered)

        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            cap_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._CAPACITY_RUBRIC
            ).score
            risk_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._RISK_RUBRIC
            ).score
        else:
            cap_score = self._fallback_cap(all_text, lowered)
            risk_score = self._fallback_risk(lowered)

        completion = tool_penalty * (
            0.55 * det_score + 0.25 * cap_score + 0.20 * risk_score
        )

        scores.completion = min(round(completion, 2), 1.0)
        scores.robustness = self.compute_robustness(dispatches)
        scores.efficiency_turns = len([m for m in messages if m.message.role == "assistant"])
        return scores

    def _tool_gate(self, dispatches):
        cal = any(d.tool_name in ("calendar_list_events", "calendar_get_event")
                  and d.response_status < 400 for d in dispatches)
        todo = any(d.tool_name in ("todo_list_tasks", "todo_update_task")
                   and d.response_status < 400 for d in dispatches)
        if not cal and not todo:
            return 0.2
        if not cal or not todo:
            return 0.5
        return 1.0

    def _score_data_retrieval(self, dispatches, audit_data):
        cal_ok = any(d.tool_name in ("calendar_list_events", "calendar_get_event")
                     and d.response_status < 400 for d in dispatches)
        todo_ok = any(d.tool_name in ("todo_list_tasks", "todo_update_task")
                      and d.response_status < 400 for d in dispatches)
        return sum([cal_ok, todo_ok]) / 2.0

    def _score_member_coverage(self, text, lowered):
        pairs = [("\u8c22\u660e", "ming xie", "xie ming"),
                 ("\u66f9\u4e3d", "li cao", "cao li"),
                 ("\u8881\u6d9b", "tao yuan", "yuan tao"),
                 ("\u949f\u745c", "yu zhong", "zhong yu")]
        found = sum(1 for zh, en1, en2 in pairs
                    if zh in text or en1 in lowered or en2 in lowered)
        return found / 4.0

    @staticmethod
    def _has_bounded(text, num):
        return bool(re.search(rf'(?<!\d){re.escape(num)}(?!\d)', text))

    def _score_xie_ming(self, text):
        names = ["\u8c22\u660e", "Ming Xie", "Xie Ming"]
        if not any(n in text for n in names):
            return 0.0
        score = 0.4
        for n in names:
            idx = text.find(n)
            if idx >= 0:
                region = text[max(0, idx - 200):idx + 400]
                if "10.5" in region or "10" in region or "20" in region:
                    return 1.0
        return score

    def _score_cao_li(self, text):
        names = ["\u66f9\u4e3d", "Li Cao", "Cao Li"]
        if not any(n in text for n in names):
            return 0.0
        score = 0.4
        for n in names:
            idx = text.find(n)
            if idx >= 0:
                region = text[max(0, idx - 200):idx + 400]
                if any(k in region for k in ["30", "\u62a5\u8868", "\u81ea\u52a8\u5316",
                                              "report", "automation"]):
                    return 1.0
        return score

    def _score_risk_flags(self, lowered):
        kws = ["\u98ce\u9669", "risk", "at_risk", "\u5ef6\u671f", "\u6765\u4e0d\u53ca",
               "\u4e0d\u8db3", "at risk", "overdue", "delay", "insufficient", "shortfall"]
        return 1.0 if any(kw in lowered for kw in kws) else 0.0

    def _score_capacity_computation(self, text, lowered):
        score = 0.0
        if "40" in text and ("\u5c0f\u65f6" in text or "h" in lowered or "hour" in lowered):
            score += 0.5
        if "8" in text and ("\u5c0f\u65f6" in text or "\u5de5\u65f6" in text or "hour" in lowered):
            score += 0.5
        return min(score, 1.0)

    def _fallback_cap(self, text, lowered):
        score = 0.0
        score += 0.25 * self._score_member_coverage(text, lowered)
        if "10.5" in text or "20" in text:
            score += 0.20
        if "30" in text:
            score += 0.20
        if "40" in text:
            score += 0.15
        if "|" in text and "---" in text:
            score += 0.20
        return min(score, 1.0)

    def _fallback_risk(self, lowered):
        score = 0.0
        if any(kw in lowered for kw in ["\u98ce\u9669", "risk", "at risk"]):
            score += 0.40
        if any(kw in lowered for kw in ["\u5efa\u8bae", "recommend", "\u8c03\u6574"]):
            score += 0.30
        if any(kw in lowered for kw in ["\u4f18\u5148", "priorit", "\u534f\u52a9", "assist"]):
            score += 0.30
        return min(score, 1.0)
