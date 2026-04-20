"""CTB_HR_07 grader -- recruitment pipeline status.

Ground truth source: task.yaml reference_solution + fixtures.

v2.2: analysis mode (HR analysis report).
- Deterministic 35%: tool gate, candidate coverage, DevOps urgency, action items
- Judge 65%: pipeline data accuracy, bottleneck analysis, report quality
- Fallback: keyword-based scoring when judge unavailable

Ground truth:
  JOB-701 Senior Frontend: Xu Wenbo (88pts, pending HR final), Zhong Haiyan (55pts, eliminated),
    Fang Zheng (round-1 passed, pending round-2)
  JOB-702 Product Manager: Shen Siyu (offer 25K, awaiting), Han Lei (backup)
  JOB-703 Data Analyst: Wu Yutong (accepted, start Apr 15), Ma Chao (pending round-1)
  JOB-704 DevOps: urgent, no candidates
"""

from __future__ import annotations

import re
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class Grader(AbstractGrader):
    """Grade recruitment pipeline status report."""

    _PIPELINE_DATA_RUBRIC = """\
Evaluate the accuracy of candidate and position tracking data (0.0-1.0).

## Ground Truth -- 4 Positions, 7 Candidates
- JOB-701 Senior Frontend: Xu Wenbo (tech interview 88 pts, pending HR final), Zhong Haiyan (55 pts, eliminated), Fang Zheng (round-1 passed, pending round-2)
- JOB-702 Product Manager: Shen Siyu (offer sent at 25,000/month, awaiting response), Han Lei (backup candidate)
- JOB-703 Data Analyst: Wu Yutong (offer accepted, start date April 15), Ma Chao (pending round-1)
- JOB-704 DevOps: URGENT -- no candidates yet, need to post immediately

## Scoring tiers
- 0.9-1.0: All 4 positions with correct candidate statuses and key details
- 0.7-0.8: All positions covered; most candidate data correct
- 0.5-0.6: 3+ positions; some candidate data
- 0.3-0.4: Partial coverage
- 0.0-0.2: No meaningful pipeline data
"""

    _ACTION_RUBRIC = """\
Evaluate the quality of bottleneck identification and action recommendations (0.0-1.0).

## Expected elements
- JOB-704 DevOps urgently needs candidate sourcing
- Follow up on Shen Siyu's offer response
- Schedule HR final interview for Xu Wenbo
- Schedule round-2 for Fang Zheng
- Pipeline funnel metrics across stages

## Scoring tiers
- 0.9-1.0: All urgent actions identified; DevOps flagged as critical; pipeline health metrics
- 0.7-0.8: Most actions listed; DevOps urgency noted
- 0.5-0.6: Some actions; partial urgency
- 0.3-0.4: Minimal action items
- 0.0-0.2: No action items
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
        det_score += 0.35 * self._score_candidate_coverage(all_text)
        det_score += 0.35 * self._score_devops_urgency(all_text, lower)
        det_score += 0.30 * self._score_action_items(all_text, lower)

        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            pipeline_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._PIPELINE_DATA_RUBRIC
            ).score
            action_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._ACTION_RUBRIC
            ).score
        else:
            pipeline_score = self._fallback_pipeline(all_text, lower)
            action_score = self._fallback_actions(all_text, lower)

        completion = tool_penalty * (
            0.35 * det_score
            + 0.35 * pipeline_score
            + 0.30 * action_score
        )

        scores.completion = min(round(completion, 2), 1.0)
        scores.robustness = self.compute_robustness(dispatches)
        scores.efficiency_turns = len(
            [m for m in messages if m.message.role == "assistant"]
        )
        return scores

    def _tool_gate(self, dispatches: list[ToolDispatch]) -> float:
        gmail = [d for d in dispatches if d.tool_name in ("gmail_list_messages", "gmail_get_message") and d.response_status < 400]
        crm = [d for d in dispatches if d.tool_name in ("crm_list_customers", "crm_get_customer") and d.response_status < 400]
        if not gmail and not crm:
            return 0.2
        if not gmail or not crm:
            return 0.5
        return 1.0

    def _score_candidate_coverage(self, all_text: str) -> float:
        candidates = ["Xu Wenbo", "Zhong Haiyan", "Fang Zheng", "Shen Siyu", "Wu Yutong"]
        found = sum(1 for c in candidates if c in all_text)
        return min(found / 4, 1.0)

    def _score_devops_urgency(self, all_text: str, lower: str) -> float:
        if "DevOps" not in all_text and "devops" not in lower:
            return 0.0
        urgent_kw = ["urgent", "no candidate", "no applicant", "unfilled", "immediately",
                      "critical", "open"]
        if any(kw in lower for kw in urgent_kw):
            return 1.0
        return 0.3

    def _score_action_items(self, all_text: str, lower: str) -> float:
        actions = ["post", "follow up", "follow-up", "schedule", "final interview",
                    "round-2", "round 2"]
        found = sum(1 for a in actions if a in lower)
        return min(found / 3, 1.0)

    def _fallback_pipeline(self, all_text: str, lower: str) -> float:
        """_fallback_: dev-only."""
        score = 0.0
        candidates = ["Xu Wenbo", "Shen Siyu", "Wu Yutong", "Fang Zheng", "Zhong Haiyan"]
        score += 0.40 * min(sum(1 for c in candidates if c in all_text) / 4, 1.0)
        positions = ["frontend", "product manager", "data analy", "devops"]
        score += 0.35 * min(sum(1 for p in positions if p in lower) / 3, 1.0)
        if "88" in all_text or "25000" in all_text.replace(",", ""):
            score += 0.25
        return min(score, 1.0)

    def _fallback_actions(self, all_text: str, lower: str) -> float:
        """_fallback_: dev-only."""
        score = 0.0
        if "DevOps" in all_text and any(kw in lower for kw in ["urgent", "no candidate"]):
            score += 0.40
        actions = ["post", "follow up", "schedule", "interview"]
        score += 0.35 * min(sum(1 for a in actions if a in lower) / 2, 1.0)
        if any(kw in lower for kw in ["pipeline", "funnel"]):
            score += 0.25
        return min(score, 1.0)
