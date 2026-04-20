"""CTB_SUPPORT_04 grader -- resolution quality check.

v2.2: analysis mode (analysis report).
- Deterministic 35%: tool gate, ticket coverage, quality ratings, problem tickets
- Judge 65%: quality assessment accuracy, recommendation quality
- Fallback: keyword-based scoring when judge unavailable

Ground truth:
  RQ-001: Good -- root cause found (memory), customer confirmed. Initial response (clear cache) was wrong.
  RQ-002: Excellent -- quick, correct fix, customer confirmed.
  RQ-003: Fair -- dismissive first response, 2+ days delay, eventual fix correct.
  RQ-004: Poor -- no root cause, no real fix, closed without resolving.
  RQ-005: Excellent -- quick, correct, customer verified.
  Problems: RQ-003 (dismissive), RQ-004 (closed unresolved)
"""

from __future__ import annotations
import re
from typing import Any
from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class Grader(AbstractGrader):
    _QUALITY_RUBRIC = """\
Evaluate the accuracy of resolution quality assessments (0.0-1.0).

## Ground Truth
- RQ-001: GOOD -- found root cause (insufficient memory), customer confirmed fix. Initial response was wrong (clear cache).
- RQ-002: EXCELLENT -- quick resolution, correct fix, customer confirmed.
- RQ-003: FAIR -- initial response dismissed customer's valid concern, took 2+ days, eventual fix was correct.
- RQ-004: POOR -- no root cause found, no actual fix provided, ticket closed without truly resolving. Customer never confirmed.
- RQ-005: EXCELLENT -- quick, correct solution, customer verified.

## Key Problems to Identify
- RQ-003: Dismissive first response, slow resolution time
- RQ-004: Closed without actually resolving the issue -- this is the most critical quality failure

## Scoring tiers
- 0.9-1.0: All 5 tickets correctly rated; RQ-004 flagged as poor; RQ-003 issues identified; specific justifications
- 0.7-0.8: 4-5 tickets rated; problem tickets identified; reasonable justifications
- 0.5-0.6: 3+ tickets; some quality issues found
- 0.3-0.4: Few tickets analyzed
- 0.0-0.2: No meaningful quality check
"""

    _IMPROVEMENT_RUBRIC = """\
Evaluate improvement recommendations and overall assessment (0.0-1.0).

## Expected elements
1. Overall quality summary (e.g., 2 Excellent, 1 Good, 1 Fair, 1 Poor)
2. Specific improvements for RQ-003 (communication quality) and RQ-004 (resolution verification)
3. Process recommendations (e.g., require customer confirmation before closing)
4. Training needs identified

## Scoring tiers
- 0.9-1.0: Comprehensive assessment; specific per-ticket improvements; process recommendations
- 0.7-0.8: Good overview; some specific improvements
- 0.5-0.6: Basic assessment; generic recommendations
- 0.3-0.4: Minimal recommendations
- 0.0-0.2: No recommendations
"""

    def grade(self, messages, dispatches, task, audit_data=None, judge=None, media_events=None, env_snapshot=None):
        scores = DimensionScores(safety=1.0)
        all_text = self._get_all_assistant_text(messages)
        lower = all_text.lower()

        tool_penalty = self._tool_gate(dispatches)

        det_score = 0.0
        tickets = ["RQ-001", "RQ-002", "RQ-003", "RQ-004", "RQ-005"]
        quality_kw = ["excellent", "good", "fair", "poor", "satisfactory", "unsatisfactory"]
        rated = 0
        for tid in tickets:
            if tid in all_text:
                idx = all_text.index(tid)
                region = all_text[idx:idx + 500].lower()
                if any(k in region for k in quality_kw): rated += 1
                else: rated += 0.4
        det_score += 0.30 * min(rated / 4, 1.0)

        # RQ-004 identified as poor
        if "RQ-004" in all_text:
            idx = all_text.index("RQ-004")
            region = all_text[idx:idx + 500].lower()
            poor_kw = ["poor", "unresolved", "not resolved", "closed without", "fail"]
            det_score += 0.25 * (1.0 if any(k in region for k in poor_kw) else 0.3)

        # RQ-003 identified as having issues
        if "RQ-003" in all_text:
            idx = all_text.index("RQ-003")
            region = all_text[idx:idx + 500].lower()
            issue_kw = ["fair", "dismissive", "slow", "delay", "unprofessional"]
            det_score += 0.20 * (1.0 if any(k in region for k in issue_kw) else 0.3)

        # Overall assessment and recommendations
        if any(k in lower for k in ["overall", "summary", "aggregate"]): det_score += 0.10
        if any(k in lower for k in ["recommend", "improv", "training"]): det_score += 0.15

        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            quality = judge.evaluate(task.prompt.text, conversation, actions, self._QUALITY_RUBRIC).score
            improvement = judge.evaluate(task.prompt.text, conversation, actions, self._IMPROVEMENT_RUBRIC).score
        else:
            quality = self._fallback_quality(all_text, lower)
            improvement = self._fallback_improvement(lower)

        completion = tool_penalty * (0.35 * det_score + 0.35 * quality + 0.30 * improvement)
        scores.completion = min(round(completion, 2), 1.0)
        scores.robustness = self.compute_robustness(dispatches)
        scores.efficiency_turns = len([m for m in messages if m.message.role == "assistant"])
        return scores

    def _tool_gate(self, dispatches):
        hd = [d for d in dispatches if d.tool_name in ("helpdesk_list_tickets", "helpdesk_get_ticket") and d.response_status < 400]
        if not hd: return 0.2
        return 1.0 if len(hd) >= 3 else 0.5

    def _fallback_quality(self, text, lower):
        score = 0.0
        tickets = ["RQ-001", "RQ-002", "RQ-003", "RQ-004", "RQ-005"]
        score += 0.30 * min(sum(1 for t in tickets if t in text) / 4, 1.0)
        quality_kw = ["excellent", "good", "fair", "poor"]
        score += 0.30 * min(sum(1 for k in quality_kw if k in lower) / 3, 1.0)
        if "rq-004" in lower and any(k in lower for k in ["poor", "unresolved"]): score += 0.20
        if "rq-003" in lower and any(k in lower for k in ["fair", "dismissive"]): score += 0.20
        return min(score, 1.0)

    def _fallback_improvement(self, lower):
        score = 0.0
        if any(k in lower for k in ["overall", "summary"]): score += 0.25
        if any(k in lower for k in ["recommend", "improv"]): score += 0.25
        if any(k in lower for k in ["training", "process"]): score += 0.25
        if "|" in lower or "##" in lower: score += 0.25
        return min(score, 1.0)
