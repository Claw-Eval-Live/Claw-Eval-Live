"""CTB_MGMT_04 grader -- strategic initiative tracking.

Ground truth source: fixtures/notes + fixtures/gmail + fixtures/todo

v2.2: Claw-Eval mode (management report).
- Deterministic 35%: tool gate, initiative coverage, status assignments, CEO actions
- Judge 65%: initiative assessment accuracy, risk analysis quality
- Fallback: keyword-based scoring when judge unavailable

Ground truth:
  Initiative 1 (AI Product Line): Yellow. 60% M2 progress, 92% accuracy, GPU risk.
  Initiative 2 (International Expansion): Red. M1 delayed 2 weeks (3/31->4/14), understaffed.
    Narrow scope to Singapore + Indonesia.
  Initiative 3 (Operational Efficiency): Green. CI/CD done (30% saved), UiPath 120K/year needed.
  CEO decisions: GPU procurement, international scope, UiPath budget.
"""

from __future__ import annotations

import re
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class Grader(AbstractGrader):
    """Grade strategic initiative tracking report."""

    _INITIATIVE_STATUS_RUBRIC = """\
Evaluate the accuracy of per-initiative status assessment (0.0-1.0).

## Ground Truth
Initiative 1 (AI Product Line): YELLOW
- 60% progress on M2, 92% model accuracy achieved
- Risk: GPU procurement delayed 2 weeks, wang.fang overloaded

Initiative 2 (International Expansion): RED
- M1 delayed 2 weeks (3/31 -> 4/14), team understaffed (2 people only)
- M2 will cascade delay to June
- Suggestion: narrow scope to Singapore + Indonesia only

Initiative 3 (Operational Efficiency): GREEN
- CI/CD pipeline done (30% deploy time saved), M1 complete
- RPA evaluation in progress, M2 may finish early by mid-May
- Budget needed: 120,000/year for UiPath license

## Scoring tiers
- 0.9-1.0: All 3 initiatives with correct status colors; key progress numbers; risks identified
- 0.7-0.8: All initiatives covered; most statuses correct; key risks mentioned
- 0.5-0.6: 2+ initiatives; partial status/risk
- 0.3-0.4: 1 initiative correctly assessed
- 0.0-0.2: No meaningful assessment
"""

    _CEO_ACTION_RUBRIC = """\
Evaluate the identification of CEO-required decisions and report structure (0.0-1.0).

## Expected CEO Action Items
1. GPU procurement approval/expediting decision
2. International expansion scope narrowing decision (Singapore + Indonesia focus)
3. UiPath license budget approval (120K/year)

## Report Structure
- Green/Yellow/Red status indicators for each initiative
- Completed and pending milestones listed
- Risk items and blockers clearly identified

## Scoring tiers
- 0.9-1.0: All 3 CEO decisions listed; well-structured with status indicators; milestones tracked
- 0.7-0.8: 2+ CEO items; reasonable structure
- 0.5-0.6: Some CEO items; partial structure
- 0.3-0.4: Minimal CEO items
- 0.0-0.2: No CEO action items
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
        lower = all_text.lower()

        tool_penalty = self._tool_gate(dispatches)

        det_score = 0.0
        det_score += 0.30 * self._score_initiative_coverage(all_text, lower)
        det_score += 0.35 * self._score_status_colors(all_text, lower)
        det_score += 0.35 * self._score_ceo_actions(all_text, lower)

        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            status_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._INITIATIVE_STATUS_RUBRIC
            ).score
            ceo_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._CEO_ACTION_RUBRIC
            ).score
        else:
            status_score = self._fallback_status(all_text, lower)
            ceo_score = self._fallback_ceo(all_text, lower)

        completion = tool_penalty * (
            0.35 * det_score
            + 0.35 * status_score
            + 0.30 * ceo_score
        )

        scores.completion = min(round(completion, 2), 1.0)
        scores.robustness = self.compute_robustness(dispatches)
        scores.efficiency_turns = len(
            [m for m in messages if m.message.role == "assistant"]
        )
        return scores

    def _tool_gate(self, dispatches: list[ToolDispatch]) -> float:
        tools = {"notes": ("notes_list", "notes_get"), "gmail": ("gmail_list_messages", "gmail_get_message"),
                  "todo": ("todo_list_tasks", "todo_get_task")}
        accessed = sum(1 for _, names in tools.items()
                       if any(d.tool_name in names and d.response_status < 400 for d in dispatches))
        if accessed == 0:
            return 0.2
        if accessed < 2:
            return 0.5
        return 1.0

    def _score_initiative_coverage(self, all_text: str, lower: str) -> float:
        ai_kw = ["ai", "product line", "artificial intelligence"]
        intl_kw = ["international", "southeast asia", "overseas"]
        ops_kw = ["operational efficiency", "automation"]
        found = 0
        if any(kw in lower for kw in ai_kw):
            found += 1
        if any(kw in lower for kw in intl_kw):
            found += 1
        if any(kw in lower for kw in ops_kw):
            found += 1
        return found / 3

    def _score_status_colors(self, all_text: str, lower: str) -> float:
        colors = ["green", "yellow", "red"]
        found = sum(1 for c in colors if c in lower)
        return min(found / 2, 1.0)

    def _score_ceo_actions(self, all_text: str, lower: str) -> float:
        action_kw = ["gpu procurement", "approval", "international scope",
                      "uipath budget", "decision", "ceo"]
        found = sum(1 for kw in action_kw if kw in lower)
        return min(found / 2, 1.0)

    def _fallback_status(self, all_text: str, lower: str) -> float:
        """_fallback_: dev-only."""
        score = 0.0
        if "gpu" in lower:
            score += 0.15
        if any(kw in lower for kw in ["delay", "postpone"]) and any(kw in lower for kw in ["international", "overseas"]):
            score += 0.20
        if "singapore" in lower or "indonesia" in lower:
            score += 0.15
        if "ci/cd" in lower or "30%" in all_text:
            score += 0.15
        if "uipath" in lower or "120" in all_text.replace(",", ""):
            score += 0.15
        colors = ["green", "yellow", "red"]
        score += 0.20 * min(sum(1 for c in colors if c in lower) / 2, 1.0)
        return min(score, 1.0)

    def _fallback_ceo(self, all_text: str, lower: str) -> float:
        """_fallback_: dev-only."""
        score = 0.0
        action_kw = ["gpu", "approval", "scope", "uipath", "decision", "ceo"]
        score += 0.50 * min(sum(1 for kw in action_kw if kw in lower) / 2, 1.0)
        struct_kw = ["milestone", "risk", "blocker", "status"]
        score += 0.30 * min(sum(1 for kw in struct_kw if kw in lower) / 2, 1.0)
        if len(all_text.strip()) >= 400:
            score += 0.20
        return min(score, 1.0)
