"""CTB_D03 grader — compare three technical whitepapers into an architecture report.

v2.2: hybrid deterministic + judge scoring.
- Deterministic: system names present, key performance numbers (420k/710k/160k)
- Judge: data accuracy per system, analysis & recommendation quality
- Fallback: keyword-based scoring when judge unavailable
"""

from __future__ import annotations

import re
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class WhitepaperArchitectureReportGrader(AbstractGrader):
    """Grade a structured technical comparison report."""

    _DATA_RUBRIC = """\
Evaluate the accuracy and completeness of technical data for all three systems (0.0-1.0).

## Ground Truth
### System X (distributed object storage)
- Throughput: 420k ops/s
- P99 latency: 18ms
- Cluster limit: 12 nodes (soft limit)
- Hardware: 64GB RAM + NVMe
- Weakness: cross-region write replication not recommended; metadata compaction window

### System Y (memory-first analytics engine)
- Throughput: 710k ops/s
- P99 latency: 4ms
- Cluster limit: 8 nodes (coherence cost spike beyond 8)
- Hardware: 256GB RAM
- Weakness: coherence overhead grows steeply beyond 8 nodes; high memory cost

### System Z (graph-optimized database)
- Throughput: 160k ops/s
- Traversal latency: 7ms
- Sharding: 6 shards max before write path degrades
- Hardware: 128GB RAM
- Weakness: NOT suitable for write-intensive real-time analytics; re-balancing overhead

## Scoring tiers
- 0.9-1.0: All 3 systems covered with correct throughput, latency, cluster limits, hardware, and weaknesses
- 0.7-0.8: All 3 systems covered, most data correct, 1-2 minor omissions
- 0.5-0.6: 2-3 systems covered but significant data gaps or errors
- 0.3-0.4: Only 1-2 systems with meaningful data
- 0.0-0.2: No meaningful technical data

## Penalty
- Inventing specs not in the whitepapers: deduct 0.2
"""

    _ANALYSIS_RUBRIC = """\
Evaluate the quality of the comparison analysis and recommendation (0.0-1.0).

## Context
The target scenario is "high-concurrency read-write for real-time analytics".
Correct recommendation: System Y (highest throughput 710k, lowest p99 4ms, suitable for real-time analytics).
Caveats: high memory cost, 8-node coherence limit.
System Z should be explicitly noted as NOT suitable for this scenario.

## Expected elements
1. Structured comparison table (dimensions × systems)
2. Clear recommendation of System Y for the target scenario with rationale
3. Explanation of why System Z is not suitable (write-intensive degradation)
4. Mention of System Y's trade-offs (memory cost, node limit)

## Scoring tiers
- 0.9-1.0: Has comparison table + clear Y recommendation + Z exclusion + trade-off discussion
- 0.7-0.8: Has comparison and recommendation, may lack depth on trade-offs
- 0.5-0.6: Some comparison but unstructured; recommendation exists but rationale is thin
- 0.3-0.4: Minimal comparison; no clear recommendation
- 0.0-0.2: No meaningful analysis
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
        final_text = self._get_final_assistant_text(messages)
        normalized = final_text.replace(",", "")

        # 1. No tool gate (pure attachment analysis)

        # 2. Deterministic: system names + key performance numbers
        det_score = self._score_deterministic(normalized, final_text)

        # 3. Judge: data accuracy + analysis quality
        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            data_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._DATA_RUBRIC
            ).score
            analysis_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._ANALYSIS_RUBRIC
            ).score
        else:
            data_score = self._fb_data(normalized, final_text)
            analysis_score = self._fb_analysis(normalized, final_text)

        # 4a. Additional deterministic: recommendation section
        text = final_text
        rec_det = 0.05 * (1.0 if ("recommend" in text.lower() or "conclusion" in text.lower()) else 0.0)

        # 4. Combine: deterministic (25%) + rec (5%) + judge data (35%) + judge analysis (35%)
        completion = (
            0.25 * det_score
            + rec_det
            + 0.35 * data_score
            + 0.35 * analysis_score
        )

        scores.completion = min(round(completion, 2), 1.0)
        scores.robustness = 1.0
        scores.communication = self._score_communication(final_text)
        scores.efficiency_turns = len([m for m in messages if m.message.role == "assistant"])
        return scores

    @staticmethod
    def _has_bounded(text: str, num: str) -> bool:
        import re
        return bool(re.search(rf'(?<!\d){re.escape(num)}(?!\d)', text))

    @staticmethod
    def _has_system(text_lower: str, letter: str) -> bool:
        """Match English system name variants."""
        targets = [f"system {letter}", f"system{letter}",
                   f"system-{letter}", f"system_{letter}"]
        return any(t in text_lower for t in targets)

    def _score_deterministic(self, normalized: str, text: str) -> float:
        lowered = normalized.lower()
        score = 0.0
        # System names mentioned
        names_found = 0
        for letter in ["x", "y", "z"]:
            if self._has_system(lowered, letter):
                names_found += 1
        score += 0.30 * min(names_found / 3, 1.0)

        # Key throughput numbers
        nums_found = 0
        if self._has_bounded(normalized, "420") and ("k" in lowered or "ops" in lowered):
            nums_found += 1
        if self._has_bounded(normalized, "710") and ("k" in lowered or "ops" in lowered):
            nums_found += 1
        if self._has_bounded(normalized, "160") and ("k" in lowered or "ops" in lowered):
            nums_found += 1
        score += 0.40 * min(nums_found / 3, 1.0)

        # Has comparison table
        if "|" in text and "---" in text:
            score += 0.15

        # Has recommendation section
        if "recommend" in text.lower() or "conclusion" in text.lower():
            score += 0.15

        return min(score, 1.0)

    def _fb_data(self, normalized: str, text: str) -> float:
        lowered = normalized.lower()
        score = 0.0
        # System X details
        if "18" in normalized and ("ms" in lowered or "latency" in lowered):
            score += 0.1
        if "12" in normalized and "node" in lowered:
            score += 0.08
        # System Y details
        if "4" in normalized and ("ms" in lowered or "p99" in lowered):
            score += 0.1
        if self._has_bounded(normalized, "256") and ("gb" in lowered or "memory" in lowered or "ram" in lowered):
            score += 0.08
        # System Z details
        if "graph" in lowered:
            score += 0.08
        if "6" in normalized and "shard" in lowered:
            score += 0.08
        # Architecture limitations
        if "cross-region" in lowered:
            score += 0.08
        if "coherence" in lowered:
            score += 0.08
        if "not suitable" in lowered or "re-balanc" in lowered or "write-intensive" in lowered:
            score += 0.08
        # Hardware
        if "64gb" in lowered or "nvme" in lowered:
            score += 0.06
        if "128gb" in lowered:
            score += 0.06
        return min(score, 1.0)

    def _fb_analysis(self, normalized: str, text: str) -> float:
        lowered = normalized.lower()
        score = 0.0
        # Recommends Y
        if self._has_system(lowered, "y") and ("recommend" in lowered or "top choice" in lowered):
            score += 0.35
        # Explains why for target scenario
        if "high-concurrency" in lowered or ("concurren" in lowered and "real-time" in lowered):
            score += 0.15
        if "throughput" in lowered and ("latency" in lowered or "p99" in lowered):
            score += 0.1
        # Z not suitable
        if self._has_system(lowered, "z") and ("not suitable" in lowered or "not recommend" in lowered):
            score += 0.15
        # Trade-offs mentioned
        if "memory cost" in lowered or "node limit" in lowered or "node scale" in lowered:
            score += 0.1
        # Has table
        if "|" in text and "---" in text:
            score += 0.15
        return min(score, 1.0)

    def _score_communication(self, text: str) -> float:
        entities = ["System X", "System Y", "System Z", "420k", "710k", "p99", "real-time analytics"]
        return self.compute_communication_substance(text, entities, 1.0)
