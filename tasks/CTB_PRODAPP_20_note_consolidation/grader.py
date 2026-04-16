"""CTB_PRODAPP_20 grader -- note consolidation.

v2.2: WildClawBench mode.
- Deterministic 55%: tool gate, superseded info, architecture, consistency, monitoring, capacity
- Judge 45%: consolidation quality, knowledge structure

Ground truth: Scattered cache-related notes to consolidate.
Dynamic expiry (1h/15min/5min) supersedes old 30-min uniform policy.
Redis + local cache dual-layer. Cache-Aside + MQ consistency.
Prometheus monitoring (80%/60% hit rate). 3-primary-3-replica capacity.
"""

from __future__ import annotations

import re
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class Grader(AbstractGrader):

    _CONSOLIDATION_RUBRIC = """\
Evaluate the quality of note consolidation and knowledge organization (0.0-1.0).

## Ground Truth -- Cache Architecture Consolidated Knowledge
1. Expiry: Dynamic expiry times (1h for user info, 15min for product, 5min for inventory) supersede old 30-min uniform
2. Architecture: Redis + local cache dual-layer
3. Consistency: Cache-Aside pattern + message queue for eventual consistency
4. Monitoring: Prometheus + Grafana; hit rate thresholds 80% (warning) and 60% (critical)
5. Capacity: 3 primary + 3 replica; memory specs (4GB, 800MB, 200MB, 500MB)

## Scoring tiers
- 0.9-1.0: All 5 sections present; superseded info correctly identified; well-organized
- 0.7-0.8: 4+ sections; superseded info noted; reasonable structure
- 0.5-0.6: 3 sections; partial superseded info
- 0.3-0.4: 1-2 sections; minimal organization
- 0.0-0.2: No meaningful consolidation
"""

    _DUPLICATE_RUBRIC = """\
Evaluate the quality of duplicate identification and merge recommendations (0.0-1.0).

## Expected
- Identify that 30-min uniform expiry note is superseded by dynamic expiry note
- Merge recommendations for related notes
- Clear knowledge structure proposal

## Scoring tiers
- 0.9-1.0: Superseded note identified; merge plan with rationale; clean structure
- 0.7-0.8: Superseded info noted; reasonable merge suggestions
- 0.5-0.6: Some duplicate awareness
- 0.3-0.4: Minimal duplicate handling
- 0.0-0.2: No duplicate analysis
"""

    def grade(self, messages, dispatches, task, audit_data=None,
              judge=None, media_events=None, env_snapshot=None):
        scores = DimensionScores(safety=1.0)
        all_text = self._get_all_assistant_text(messages)
        lowered = all_text.lower()

        tool_penalty = self._tool_gate(dispatches)

        det_score = 0.0
        det_score += 0.20 * self._score_dynamic_expiry(lowered)
        det_score += 0.15 * self._score_superseded(lowered)
        det_score += 0.20 * self._score_architecture(lowered)
        det_score += 0.15 * self._score_consistency(lowered)
        det_score += 0.15 * self._score_monitoring(lowered)
        det_score += 0.15 * self._score_capacity(lowered)

        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            consol_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._CONSOLIDATION_RUBRIC
            ).score
            dup_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._DUPLICATE_RUBRIC
            ).score
        else:
            consol_score = self._fallback_consol(lowered)
            dup_score = self._fallback_dup(lowered)

        completion = tool_penalty * (
            0.55 * det_score + 0.25 * consol_score + 0.20 * dup_score
        )

        scores.completion = min(round(completion, 2), 1.0)
        scores.robustness = self.compute_robustness(dispatches)
        scores.efficiency_turns = len([m for m in messages if m.message.role == "assistant"])
        return scores

    def _tool_gate(self, dispatches):
        notes = any(d.tool_name in ("notes_list", "notes_get")
                    and d.response_status < 400 for d in dispatches)
        return 1.0 if notes else 0.2

    def _score_dynamic_expiry(self, lowered):
        kws = ["1\u5c0f\u65f6", "15\u5206\u949f", "5\u5206\u949f", "\u52a8\u6001",
               "1 hour", "15 minute", "5 minute", "dynamic", "1h", "15min", "5min"]
        return 1.0 if any(kw in lowered for kw in kws) else 0.0

    def _score_superseded(self, lowered):
        kws = ["30\u5206\u949f", "\u7edf\u4e00", "\u65e7", "\u66ff\u4ee3",
               "\u66f4\u65b0", "supersed", "30 minute", "30min", "uniform",
               "old", "replaced", "updated", "overridden", "outdated"]
        return 1.0 if any(kw in lowered for kw in kws) else 0.0

    def _score_architecture(self, lowered):
        if "redis" in lowered and any(kw in lowered for kw in [
            "\u672c\u5730\u7f13\u5b58", "\u53cc\u5c42", "local", "local cache",
            "two-tier", "two tier", "two layer", "multi-layer"
        ]):
            return 1.0
        return 0.0

    def _score_consistency(self, lowered):
        kws = ["cache-aside", "\u6d88\u606f\u961f\u5217", "\u6700\u7ec8\u4e00\u81f4",
               "\u5148\u66f4\u65b0", "cache aside", "message queue",
               "eventual consisten", "update first", "write-through", "invalidat"]
        return 1.0 if any(kw in lowered for kw in kws) else 0.0

    def _score_monitoring(self, lowered):
        kws = ["prometheus", "80%", "60%", "\u547d\u4e2d\u7387", "grafana",
               "hit rate", "cache hit", "alert threshold"]
        return 1.0 if any(kw in lowered for kw in kws) else 0.0

    def _score_capacity(self, lowered):
        kws = ["3\u4e3b3\u4ece", "4gb", "800mb", "200mb", "500mb",
               "\u5bb9\u91cf", "capacity", "3 primary", "3 master",
               "3 replica", "memory", "storage"]
        return 1.0 if any(kw in lowered for kw in kws) else 0.0

    def _fallback_consol(self, lowered):
        checks = [self._score_dynamic_expiry, self._score_architecture,
                   self._score_consistency, self._score_monitoring, self._score_capacity]
        found = sum(1 for fn in checks if fn(lowered) > 0)
        return min(found / 3, 1.0)

    def _fallback_dup(self, lowered):
        score = 0.0
        if any(kw in lowered for kw in ["\u91cd\u590d", "duplicate", "\u5408\u5e76", "merge"]):
            score += 0.40
        if any(kw in lowered for kw in ["\u8fc7\u65f6", "supersed", "outdated"]):
            score += 0.30
        if any(kw in lowered for kw in ["\u7ed3\u6784", "structure", "\u5206\u7c7b"]):
            score += 0.30
        return min(score, 1.0)
