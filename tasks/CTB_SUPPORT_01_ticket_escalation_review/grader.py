"""CTB_SUPPORT_01 grader -- ticket escalation review.

v2.2: Claw-Eval mode (analysis report).
- Deterministic 35%: tool gate, ticket coverage, category classification, repeat customer
- Judge 65%: escalation analysis accuracy, recommendation quality
- Fallback: keyword-based scoring when judge unavailable

Ground truth:
  6 escalated tickets: ESC-001 to ESC-006
  Categories: data_loss, performance, billing, security, bug, data_integrity
  Critical: 3 (ESC-001, ESC-002, ESC-004), High: 3 (ESC-003, ESC-005, ESC-006)
  Repeat customer: customer_c (ESC-003 + ESC-006)
"""

from __future__ import annotations
import re
from typing import Any
from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class Grader(AbstractGrader):
    _ANALYSIS_RUBRIC = """\
Evaluate the accuracy of ticket escalation analysis (0.0-1.0).

## Ground Truth
- 6 escalated tickets: ESC-001 through ESC-006
- Categories: data_loss, performance, billing, security, bug, data_integrity
- Severity: 3 Critical (ESC-001, ESC-002, ESC-004), 3 High (ESC-003, ESC-005, ESC-006)
- Repeat customer: customer_c with 2 escalations (ESC-003 and ESC-006)

## Scoring tiers
- 0.9-1.0: All 6 tickets analyzed with categories and severity; repeat customer identified; common causes analyzed
- 0.7-0.8: 5+ tickets covered; categories correct; repeat customer noted
- 0.5-0.6: 3-4 tickets; partial categorization
- 0.3-0.4: Few tickets analyzed
- 0.0-0.2: No meaningful analysis
"""

    _RECOMMENDATION_RUBRIC = """\
Evaluate the quality of escalation reduction recommendations (0.0-1.0).

## Expected elements
1. Common cause analysis (categorize and count by type)
2. Repeat customer identification (customer_c)
3. Improvement recommendations to reduce unnecessary escalations
4. Per-ticket: reason, category, handling team, resolution outcome

## Scoring tiers
- 0.9-1.0: Comprehensive root cause analysis; specific reduction recommendations; per-ticket detail
- 0.7-0.8: Good cause analysis; reasonable recommendations
- 0.5-0.6: Basic analysis; generic recommendations
- 0.3-0.4: Minimal analysis
- 0.0-0.2: No recommendations
"""

    def grade(self, messages, dispatches, task, audit_data=None, judge=None, media_events=None, env_snapshot=None):
        scores = DimensionScores(safety=1.0)
        all_text = self._get_all_assistant_text(messages)
        lower = all_text.lower()

        tool_penalty = self._tool_gate(dispatches)

        det_score = 0.0
        tickets = ["ESC-001", "ESC-002", "ESC-003", "ESC-004", "ESC-005", "ESC-006"]
        det_score += 0.30 * min(sum(1 for t in tickets if t in all_text) / 5, 1.0)
        categories = ["data loss", "performance", "billing", "security", "bug", "data integrity"]
        det_score += 0.25 * min(sum(1 for c in categories if c in lower) / 4, 1.0)
        repeat_score = 0.0
        if "customer_c" in all_text: repeat_score += 0.5
        if any(k in lower for k in ["repeat", "multiple", "twice", "two escalation"]): repeat_score += 0.5
        det_score += 0.25 * min(repeat_score, 1.0)
        severity_kw = ["critical", "high", "urgent", "P1", "P2"]
        det_score += 0.20 * min(sum(1 for k in severity_kw if k in lower) / 2, 1.0)

        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            analysis = judge.evaluate(task.prompt.text, conversation, actions, self._ANALYSIS_RUBRIC).score
            recs = judge.evaluate(task.prompt.text, conversation, actions, self._RECOMMENDATION_RUBRIC).score
        else:
            analysis = self._fallback_analysis(all_text, lower)
            recs = self._fallback_recs(lower)

        completion = tool_penalty * (0.35 * det_score + 0.35 * analysis + 0.30 * recs)
        scores.completion = min(round(completion, 2), 1.0)
        scores.robustness = self.compute_robustness(dispatches)
        scores.efficiency_turns = len([m for m in messages if m.message.role == "assistant"])
        return scores

    def _tool_gate(self, dispatches):
        hd = [d for d in dispatches if d.tool_name in ("helpdesk_list_tickets", "helpdesk_get_ticket") and d.response_status < 400]
        if not hd: return 0.2
        return 1.0 if len(hd) >= 3 else 0.5

    def _fallback_analysis(self, text, lower):
        score = 0.0
        tickets = ["ESC-001", "ESC-002", "ESC-003", "ESC-004", "ESC-005", "ESC-006"]
        score += 0.30 * min(sum(1 for t in tickets if t in text) / 5, 1.0)
        if "customer_c" in text: score += 0.20
        categories = ["data loss", "performance", "billing", "security", "bug"]
        score += 0.30 * min(sum(1 for c in categories if c in lower) / 3, 1.0)
        if any(k in lower for k in ["common", "pattern", "root cause"]): score += 0.20
        return min(score, 1.0)

    def _fallback_recs(self, lower):
        score = 0.0
        kw = ["recommend", "improve", "reduce escalation", "training", "process", "prevent"]
        score += 0.60 * min(sum(1 for k in kw if k in lower) / 3, 1.0)
        if any(k in lower for k in ["common cause", "categoriz", "primary cause"]): score += 0.40
        return min(score, 1.0)
