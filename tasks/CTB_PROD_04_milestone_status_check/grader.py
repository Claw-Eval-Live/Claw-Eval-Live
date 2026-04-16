"""CTB_PROD_04 grader -- milestone status check.

v2.2: WildClawBench mode.
- Deterministic 55%: tool gate, M1 status, Alpha risk, UI blocked, API in-progress
- Judge 45%: milestone analysis quality, cascade assessment

Ground truth: M1 done. M2 Alpha at risk: Core API in progress + UI blocked.
M3-M5 cascading risk.
"""

from __future__ import annotations

import re
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class Grader(AbstractGrader):

    _MILESTONE_RUBRIC = """\
Evaluate the accuracy of Q2 milestone status check (0.0-1.0).

## Ground Truth
- M1 (Requirements Freeze): DONE/COMPLETE
- M2 (Alpha Release 5/1): AT RISK -- Core API still in progress (Zhang Wei), UI blocked (Li Na)
- M3-M5: Cascading risk from M2 delay

## Scoring tiers
- 0.9-1.0: M1 done; M2 at risk with both API+UI details; cascade noted; 5/1 deadline awareness
- 0.7-0.8: M1 done; M2 risk identified; some cascade
- 0.5-0.6: M1 or M2 assessed; partial detail
- 0.3-0.4: Minimal analysis
- 0.0-0.2: No milestone check
"""

    _IMPACT_RUBRIC = """\
Evaluate the quality of cascading impact analysis and recommendations (0.0-1.0).

## Expected
- UI team blocked waiting on API completion
- Alpha release 5/1 may slip
- Downstream M3-M5 timeline impact
- Recommendations for unblocking

## Scoring tiers
- 0.9-1.0: Full cascade analysis; specific blocking chain; unblock recommendations
- 0.7-0.8: Key cascade identified; some recommendations
- 0.5-0.6: Partial cascade awareness
- 0.3-0.4: Vague impact mention
- 0.0-0.2: No impact analysis
"""

    def grade(self, messages, dispatches, task, audit_data=None,
              judge=None, media_events=None, env_snapshot=None):
        scores = DimensionScores(safety=1.0)
        all_text = self._get_all_assistant_text(messages)
        lowered = all_text.lower()

        tool_penalty = self._tool_gate(dispatches)

        det_score = 0.0
        det_score += 0.15 * self._score_data_retrieval(dispatches)
        det_score += 0.20 * self._score_m1(all_text, lowered)
        det_score += 0.25 * self._score_alpha_risk(all_text, lowered)
        det_score += 0.20 * self._score_ui_blocked(all_text, lowered)
        det_score += 0.20 * self._score_api_progress(all_text, lowered)

        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            ms_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._MILESTONE_RUBRIC
            ).score
            impact_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._IMPACT_RUBRIC
            ).score
        else:
            ms_score = self._fallback_ms(all_text, lowered)
            impact_score = self._fallback_impact(lowered)

        completion = tool_penalty * (
            0.55 * det_score + 0.25 * ms_score + 0.20 * impact_score
        )

        scores.completion = min(round(completion, 2), 1.0)
        scores.robustness = self.compute_robustness(dispatches)
        scores.efficiency_turns = len([m for m in messages if m.message.role == "assistant"])
        return scores

    def _tool_gate(self, dispatches):
        notes = any(d.tool_name in ("notes_list", "notes_get")
                    and d.response_status < 400 for d in dispatches)
        todo = any(d.tool_name == "todo_list_tasks"
                   and d.response_status < 400 for d in dispatches)
        if not notes and not todo:
            return 0.2
        if not notes or not todo:
            return 0.5
        return 1.0

    def _score_data_retrieval(self, dispatches):
        notes = any(d.tool_name in ("notes_list", "notes_get") and d.response_status < 400
                    for d in dispatches)
        todo = any(d.tool_name == "todo_list_tasks" and d.response_status < 400
                   for d in dispatches)
        return sum([notes, todo]) / 2.0

    def _score_m1(self, text, lowered):
        if "M1" not in text:
            return 0.0
        done_kws = ["done", "\u5b8c\u6210", "complete", "\u9700\u6c42\u51bb\u7ed3",
                     "requirements freeze", "finished", "locked"]
        idx = text.index("M1")
        region = text[max(0, idx - 100):idx + 400].lower()
        return 1.0 if any(kw in region for kw in done_kws) else 0.3

    def _score_alpha_risk(self, text, lowered):
        if "Alpha" not in text and "alpha" not in lowered:
            return 0.0
        risk_kws = ["\u98ce\u9669", "risk", "\u5ef6\u671f", "delay", "blocked",
                    "\u963b\u585e", "at risk", "behind", "slipped"]
        anchor = "Alpha" if "Alpha" in text else "alpha"
        idx = text.lower().index(anchor.lower())
        region = text[max(0, idx - 100):idx + 400].lower()
        return 1.0 if any(kw in region for kw in risk_kws) else 0.3

    def _score_ui_blocked(self, text, lowered):
        if "UI" not in text:
            return 0.0
        blocked_kws = ["blocked", "\u963b\u585e", "\u674e\u5a1c", "li na",
                       "blocking", "stuck", "waiting"]
        idx = text.index("UI")
        region = text[max(0, idx - 100):idx + 400].lower()
        return 1.0 if any(kw in region for kw in blocked_kws) else 0.3

    def _score_api_progress(self, text, lowered):
        if "API" not in text:
            return 0.0
        progress_kws = ["\u8fdb\u884c\u4e2d", "in_progress", "in progress",
                        "\u5f20\u4f1f", "zhang wei", "ongoing", "active"]
        idx = text.index("API")
        region = text[max(0, idx - 100):idx + 400].lower()
        return 1.0 if any(kw in region for kw in progress_kws) else 0.3

    def _fallback_ms(self, text, lowered):
        score = 0.0
        score += 0.25 * self._score_m1(text, lowered)
        score += 0.25 * self._score_alpha_risk(text, lowered)
        score += 0.25 * self._score_ui_blocked(text, lowered)
        score += 0.25 * self._score_api_progress(text, lowered)
        return min(score, 1.0)

    def _fallback_impact(self, lowered):
        score = 0.0
        if any(kw in lowered for kw in ["cascade", "\u8fde\u9501", "downstream"]):
            score += 0.30
        if any(kw in lowered for kw in ["5/1", "may 1", "5\u67081"]):
            score += 0.25
        if any(kw in lowered for kw in ["recommend", "suggest", "action"]):
            score += 0.25
        if any(kw in lowered for kw in ["unblock", "resolve"]):
            score += 0.20
        return min(score, 1.0)
