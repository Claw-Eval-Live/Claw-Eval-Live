"""CTB_SUPPORT_02 grader -- customer complaint trend analysis.

v2.2: analysis mode (analysis report).
- Deterministic 35%: tool gate, category classification, counts, high-risk customer
- Judge 65%: trend analysis accuracy, recommendation quality
- Fallback: keyword-based scoring when judge unavailable

Ground truth:
  Performance: 2 (zhao/clientA + qian/clientB)
  Billing: 1 (sun/clientC)
  Feature/export: 2 (li/clientD + wu/clientE)
  High risk: qian@clientB (threatened to switch vendors)
  Most common: Performance (2 customers)
  Total unique complaints: 5
"""

from __future__ import annotations
import re
from typing import Any
from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class Grader(AbstractGrader):
    _TREND_RUBRIC = """\
Evaluate the accuracy of complaint categorization and trend analysis (0.0-1.0).

## Ground Truth
- Performance issues: 2 unique complaints (zhao/clientA, qian/clientB) -- emails + tickets deduplicated
- Billing issues: 1 (sun/clientC)
- Feature/export defects: 2 (li/clientD feature request, wu/clientE export bug)
- High risk customer: qian@clientB -- explicitly threatened to switch vendors
- Most common category: Performance issues (2 customers, highest urgency)
- Total unique complaints: 5 (after deduplication across email and tickets)

## Scoring tiers
- 0.9-1.0: All categories with correct counts; deduplication noted; high-risk customer identified; trend analysis present
- 0.7-0.8: Most categories correct; high-risk identified; basic trend
- 0.5-0.6: Some categories; partial counts
- 0.3-0.4: Minimal classification
- 0.0-0.2: No meaningful analysis
"""

    _RECOMMENDATION_RUBRIC = """\
Evaluate the quality of priority handling recommendations (0.0-1.0).

## Expected elements
1. Priority handling order (performance first due to highest count and churn risk)
2. Specific action for high-risk clientB (immediate executive outreach)
3. Category-level action plan for the main complaint types
4. Preventive measures to reduce repeat complaints
5. Clear ownership or escalation suggestions

## Scoring tiers
- 0.9-1.0: Clear priority ordering; specific actions per category; prevention plan; ownership/escalation guidance
- 0.7-0.8: Reasonable priorities; some specific actions
- 0.5-0.6: Basic recommendations; limited prioritization
- 0.3-0.4: Minimal recommendations
- 0.0-0.2: No recommendations
"""

    def grade(self, messages, dispatches, task, audit_data=None, judge=None, media_events=None, env_snapshot=None):
        scores = DimensionScores(safety=1.0)
        all_text = self._get_all_assistant_text(messages)
        lower = all_text.lower()

        tool_penalty = self._tool_gate(dispatches)

        det_score = 0.0
        perf = any(k in lower for k in ["performance", "slow", "timeout", "speed"])
        bill = any(k in lower for k in ["billing", "invoice", "overcharg"])
        func = any(k in lower for k in ["feature", "export", "defect", "bug"])
        det_score += 0.30 * (sum([perf, bill, func]) / 3)
        if any(k in lower for k in ["high risk", "churn", "switch vendor", "at-risk"]): det_score += 0.10
        if any(k in all_text for k in ["clientB", "qian"]): det_score += 0.10
        if any(k in lower for k in ["dedup", "merg", "same issue"]): det_score += 0.15
        if any(k in lower for k in ["trend", "increas", "most common"]): det_score += 0.15
        if any(k in lower for k in ["priorit", "recommend", "address"]): det_score += 0.20

        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            trend = judge.evaluate(task.prompt.text, conversation, actions, self._TREND_RUBRIC).score
            recs = judge.evaluate(task.prompt.text, conversation, actions, self._RECOMMENDATION_RUBRIC).score
        else:
            trend = self._fallback_trend(lower)
            recs = self._fallback_recs(lower)

        completion = tool_penalty * (0.35 * det_score + 0.35 * trend + 0.30 * recs)
        scores.completion = min(round(completion, 2), 1.0)
        scores.robustness = self.compute_robustness(dispatches)
        scores.efficiency_turns = len([m for m in messages if m.message.role == "assistant"])
        return scores

    def _tool_gate(self, dispatches):
        gm = any(d.tool_name == "gmail_list_messages" and d.response_status < 400 for d in dispatches)
        hd = any(d.tool_name == "helpdesk_list_tickets" and d.response_status < 400 for d in dispatches)
        if not gm and not hd: return 0.2
        if not gm or not hd: return 0.5
        return 1.0

    def _fallback_trend(self, lower):
        score = 0.0
        if "performance" in lower: score += 0.20
        if "billing" in lower: score += 0.15
        if any(k in lower for k in ["feature", "export"]): score += 0.15
        if "clientb" in lower or "qian" in lower: score += 0.20
        if any(k in lower for k in ["trend", "most common", "concentrated"]): score += 0.15
        if any(k in lower for k in ["dedup", "merg"]): score += 0.15
        return min(score, 1.0)

    def _fallback_recs(self, lower):
        score = 0.0
        if any(k in lower for k in ["priorit", "recommend", "address"]): score += 0.40
        if any(k in lower for k in ["high risk", "urgent", "immediate"]): score += 0.30
        if "|" in lower or "##" in lower: score += 0.15
        if any(k in lower for k in ["prevent", "improv"]): score += 0.15
        return min(score, 1.0)
