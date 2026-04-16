"""CTB_OPS_04 grader -- cross-team dependency map.

Ground truth source: fixtures/gmail + fixtures/todo

v2.2: Claw-Eval mode (operations analysis report).
- Deterministic 35%: tool gate, dependency chains, bottleneck identification, delay risk
- Judge 65%: chain accuracy, risk analysis quality, recommendations
- Fallback: keyword-based scoring when judge unavailable

Ground truth:
  Chain 1: ops -> dba -> backend -> frontend (user service)
  Chain 2: pm -> design -> frontend (design flow)
  Chain 3: security + ops + qa -> payment launch
  Bottleneck: ops team (blocks chains 1 and 3)
  Delay risk: dba migration (3/26->3/29) cascades to backend+frontend
  Critical path: ops -> dba -> backend -> frontend
"""

from __future__ import annotations

import re
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class Grader(AbstractGrader):
    """Grade cross-team dependency map analysis."""

    _CHAIN_RUBRIC = """\
Evaluate the accuracy of dependency chain identification (0.0-1.0).

## Ground Truth -- 3 Dependency Chains
Chain 1 (User Service): ops (cluster deploy) -> dba (user table migration) -> backend (API refactoring) -> frontend (page adaptation)
Chain 2 (Design Flow): pm (requirements confirmation) -> design (mockups) -> frontend (page adaptation)
Chain 3 (Payment Launch): security (penetration test) + ops (production environment) + qa (regression test) -> payment launch

Critical path: ops -> dba -> backend -> frontend
Bottleneck: ops team (blocks both chains 1 and 3)

## Scoring tiers
- 0.9-1.0: All 3 chains correctly mapped; critical path identified; bottleneck (ops) named
- 0.7-0.8: 2-3 chains correct; bottleneck identified
- 0.5-0.6: 1-2 chains partially correct; some blocking noted
- 0.3-0.4: Minimal chain identification
- 0.0-0.2: No dependency mapping
"""

    _RISK_RUBRIC = """\
Evaluate the quality of delay risk analysis and recommendations (0.0-1.0).

## Ground Truth
- DBA migration delayed from 3/26 to 3/29, causing cascade delay to backend and frontend
- Ops team is the bottleneck (appears in chains 1 and 3)
- Recommendations should prioritize unblocking ops first, then accelerating dba migration
- Consider parallel execution where possible

## Scoring tiers
- 0.9-1.0: Delay quantified; cascade impact traced; ops bottleneck addressed; specific unblocking actions
- 0.7-0.8: Delay mentioned; cascade noted; some recommendations
- 0.5-0.6: Some delay awareness; general recommendations
- 0.3-0.4: Minimal risk analysis
- 0.0-0.2: No risk analysis
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
        det_score += 0.30 * self._score_chain_nodes(lower)
        det_score += 0.25 * self._score_dependency_concepts(lower)
        det_score += 0.25 * self._score_bottleneck(lower)
        det_score += 0.20 * self._score_delay_risk(all_text, lower)

        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            chain_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._CHAIN_RUBRIC
            ).score
            risk_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._RISK_RUBRIC
            ).score
        else:
            chain_score = self._fallback_chain(all_text, lower)
            risk_score = self._fallback_risk(all_text, lower)

        completion = tool_penalty * (
            0.35 * det_score
            + 0.35 * chain_score
            + 0.30 * risk_score
        )

        scores.completion = min(round(completion, 2), 1.0)
        scores.robustness = self.compute_robustness(dispatches)
        scores.efficiency_turns = len(
            [m for m in messages if m.message.role == "assistant"]
        )
        return scores

    def _tool_gate(self, dispatches: list[ToolDispatch]) -> float:
        gmail = [d for d in dispatches if d.tool_name in ("gmail_list_messages", "gmail_get_message") and d.response_status < 400]
        todo = [d for d in dispatches if d.tool_name in ("todo_list_tasks", "todo_get_task") and d.response_status < 400]
        if not gmail and not todo:
            return 0.2
        if not gmail or not todo:
            return 0.5
        return 1.0

    def _score_chain_nodes(self, lower: str) -> float:
        nodes = ["ops", "dba", "backend", "frontend", "design", "security", "payment"]
        found = sum(1 for n in nodes if n in lower)
        return min(found / 5, 1.0)

    def _score_dependency_concepts(self, lower: str) -> float:
        dep_kw = ["depend", "block", "wait", "blocked", "blocking", "->"]
        found = sum(1 for kw in dep_kw if kw in lower)
        return min(found / 2, 1.0)

    def _score_bottleneck(self, lower: str) -> float:
        if "ops" in lower and any(kw in lower for kw in ["bottleneck", "critical path", "most critical"]):
            return 1.0
        if any(kw in lower for kw in ["bottleneck", "critical"]):
            return 0.4
        return 0.0

    def _score_delay_risk(self, all_text: str, lower: str) -> float:
        score = 0.0
        if any(kw in lower for kw in ["delay", "postpone", "deferred"]):
            score += 0.33
        if "3/29" in all_text or "March 29" in all_text or "29" in all_text:
            score += 0.33
        if any(kw in lower for kw in ["cascade", "chain reaction", "downstream", "propagat"]):
            score += 0.34
        return min(score, 1.0)

    def _fallback_chain(self, all_text: str, lower: str) -> float:
        """_fallback_: dev-only."""
        score = 0.0
        nodes = ["ops", "dba", "backend", "frontend", "design", "security", "payment"]
        score += 0.35 * min(sum(1 for n in nodes if n in lower) / 5, 1.0)
        dep_kw = ["depend", "block", "->"]
        score += 0.35 * min(sum(1 for kw in dep_kw if kw in lower) / 2, 1.0)
        if "bottleneck" in lower or "critical path" in lower:
            score += 0.30
        return min(score, 1.0)

    def _fallback_risk(self, all_text: str, lower: str) -> float:
        """_fallback_: dev-only."""
        score = 0.0
        if any(kw in lower for kw in ["delay", "postpone", "cascade"]):
            score += 0.30
        rec_kw = ["recommend", "priority", "accelerate", "coordinate", "unblock"]
        score += 0.40 * min(sum(1 for kw in rec_kw if kw in lower) / 2, 1.0)
        if any(c in all_text for c in ["\u2192", "->", "|"]):
            score += 0.15
        if len(all_text.strip()) >= 400:
            score += 0.15
        return min(score, 1.0)
