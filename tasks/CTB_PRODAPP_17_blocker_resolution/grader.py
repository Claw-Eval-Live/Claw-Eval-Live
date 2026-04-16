"""CTB_PRODAPP_17 grader -- blocker resolution.

v2.2: WildClawBench mode.
- Deterministic 55%: tool gate, blocker identification, workaround proposals, priority
- Judge 45%: resolution quality, escalation plan

Ground truth: 3 blockers:
  Payment gateway: Mock/sandbox workaround
  Data export: DBA permission issue, test environment or escalation
  Email notification: SMTP down, internal API alternative
  Payment gateway is highest priority (core business, earliest deadline).
  Escalation deadline: April 5.
"""

from __future__ import annotations

import re
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class Grader(AbstractGrader):

    _RESOLUTION_RUBRIC = """\
Evaluate the quality of blocker identification and proposed resolutions (0.0-1.0).

## Ground Truth
3 blockers:
1. Payment gateway: suggest Mock/sandbox service as workaround
2. Data export: DBA permission needed; suggest test environment with small dataset or escalate
3. Email notification: SMTP server down; suggest internal mail API or SendGrid/SES alternative

## Scoring tiers
- 0.9-1.0: All 3 blockers with root cause and specific workaround
- 0.7-0.8: 3 blockers identified; 2+ workarounds
- 0.5-0.6: 2 blockers with workarounds
- 0.3-0.4: 1-2 blockers; vague solutions
- 0.0-0.2: No blocker analysis
"""

    _ESCALATION_RUBRIC = """\
Evaluate the quality of priority ranking and escalation plan (0.0-1.0).

## Ground Truth
- Payment gateway = highest priority (core business + earliest deadline)
- Escalation plan with April 5 deadline
- Director-level escalation for DBA permission if not resolved

## Scoring tiers
- 0.9-1.0: Correct priority ranking; escalation timeline; stakeholders identified
- 0.7-0.8: Priority generally correct; some escalation detail
- 0.5-0.6: Partial priority; mentions escalation
- 0.3-0.4: Mentions priority vaguely
- 0.0-0.2: No priority or escalation
"""

    def grade(self, messages, dispatches, task, audit_data=None,
              judge=None, media_events=None, env_snapshot=None):
        scores = DimensionScores(safety=1.0)
        all_text = self._get_all_assistant_text(messages)
        lowered = all_text.lower()

        tool_penalty = self._tool_gate(dispatches)

        det_score = 0.0
        det_score += 0.25 * self._score_blocker_count(lowered)
        det_score += 0.20 * self._score_payment_workaround(lowered)
        det_score += 0.20 * self._score_dba_workaround(lowered)
        det_score += 0.15 * self._score_smtp_workaround(lowered)
        det_score += 0.20 * self._score_priority_escalation(lowered)

        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            res_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._RESOLUTION_RUBRIC
            ).score
            esc_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._ESCALATION_RUBRIC
            ).score
        else:
            res_score = self._fallback_res(lowered)
            esc_score = self._fallback_esc(lowered)

        completion = tool_penalty * (
            0.55 * det_score + 0.25 * res_score + 0.20 * esc_score
        )

        scores.completion = min(round(completion, 2), 1.0)
        scores.robustness = self.compute_robustness(dispatches)
        scores.efficiency_turns = len([m for m in messages if m.message.role == "assistant"])
        return scores

    def _tool_gate(self, dispatches):
        todo = any(d.tool_name in ("todo_list_tasks", "todo_update_task")
                   and d.response_status < 400 for d in dispatches)
        notes = any(d.tool_name in ("notes_list", "notes_get")
                    and d.response_status < 400 for d in dispatches)
        if not todo and not notes:
            return 0.2
        if not todo or not notes:
            return 0.5
        return 1.0

    def _score_blocker_count(self, lowered):
        pairs = [("\u652f\u4ed8\u7f51\u5173", "payment gateway"),
                 ("\u6570\u636e\u5bfc\u51fa", "data export"),
                 ("\u90ae\u4ef6\u901a\u77e5", "email notif")]
        found = sum(1 for zh, en in pairs if zh in lowered or en in lowered)
        if "smtp" in lowered:
            found = max(found, 1)
        return min(found / 3, 1.0)

    def _score_payment_workaround(self, lowered):
        kws = ["mock", "\u6a21\u62df", "\u66ff\u4ee3", "sandbox",
               "stub", "workaround", "alternative"]
        return 1.0 if any(kw in lowered for kw in kws) else 0.0

    def _score_dba_workaround(self, lowered):
        kws = ["\u6d4b\u8bd5\u73af\u5883", "\u52a0\u6025", "\u603b\u76d1",
               "\u5c0f\u6570\u636e", "test environment", "expedit", "director",
               "small dataset", "sample data", "dba", "permission", "escalat"]
        return 1.0 if any(kw in lowered for kw in kws) else 0.0

    def _score_smtp_workaround(self, lowered):
        kws = ["\u5185\u90e8", "api", "sendgrid", "\u66ff\u4ee3\u65b9\u6848",
               "internal", "alternative", "workaround", "mailgun", "ses"]
        return 1.0 if any(kw in lowered for kw in kws) else 0.0

    def _score_priority_escalation(self, lowered):
        score = 0.0
        prio_kws = ["\u652f\u4ed8", "\u6700\u9ad8", "\u6838\u5fc3", "\u4f18\u5148",
                    "critical", "payment", "highest priority", "most critical",
                    "core business", "top priority", "p0", "p1"]
        if any(kw in lowered for kw in prio_kws):
            score += 0.5
        esc_kws = ["\u5347\u7ea7", "escalat", "\u603b\u76d1", "4\u67085",
                    "april 5", "04-05", "director", "management"]
        if any(kw in lowered for kw in esc_kws):
            score += 0.5
        return score

    def _fallback_res(self, lowered):
        score = 0.0
        if any(kw in lowered for kw in ["mock", "\u6a21\u62df", "sandbox"]):
            score += 0.30
        if any(kw in lowered for kw in ["\u6d4b\u8bd5\u73af\u5883", "test environment", "dba"]):
            score += 0.30
        if any(kw in lowered for kw in ["smtp", "\u5185\u90e8api", "internal api"]):
            score += 0.20
        if any(kw in lowered for kw in ["\u6839\u56e0", "root cause", "\u539f\u56e0"]):
            score += 0.20
        return min(score, 1.0)

    def _fallback_esc(self, lowered):
        score = 0.0
        if any(kw in lowered for kw in ["\u6700\u9ad8", "highest", "\u6838\u5fc3", "core"]):
            score += 0.40
        if any(kw in lowered for kw in ["\u5347\u7ea7", "escalat"]):
            score += 0.30
        if any(kw in lowered for kw in ["4\u67085", "april 5", "\u622a\u6b62", "deadline"]):
            score += 0.30
        return min(score, 1.0)
