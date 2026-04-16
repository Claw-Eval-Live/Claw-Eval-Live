"""CTB_R03 grader — evaluate whiteboard platform options from mock web data.

v2.2: hybrid deterministic + judge scoring.
- Deterministic: tool gate (search/fetch), candidate names, critical URL fetches
- Judge: content accuracy, recommendation & rollout quality
- Fallback: keyword-based scoring when judge unavailable
"""

from __future__ import annotations

from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class WhiteboardPlatformReportGrader(AbstractGrader):
    """Grade a structured whiteboard platform recommendation report."""

    CRITICAL_FETCH = {
        "https://docs.boardflow.dev/platform-overview",
        "https://github.example.com/boardflow/platform",
        "https://docs.canvasgrid.io/platform-overview",
        "https://docs.syncslate.dev/platform-overview",
        "https://ops-playbook.example.com/eu-data-residency-cutover",
    }

    _DATA_RUBRIC = """\
Evaluate the accuracy of whiteboard platform comparison data (0.0-1.0).

## Ground Truth
### BoardFlow (recommended as primary choice)
- CRDT + PostgreSQL + object storage
- 120 concurrent editors
- Region pinning for EU data residency, audit log
- 7.2k GitHub stars, monthly releases, security patches avg 4 days

### CanvasGrid (recommended as backup)
- OT + Redis streams
- 60 concurrent editors
- EU data residency plugin (beta)
- 4.8k stars, quarterly releases, patches avg 16 days

### SyncSlate (not recommended)
- Single-node relay architecture
- 35 concurrent editors max
- No region pinning, no audit log
- 2.1k stars, only 2 releases in past 12 months

## Scoring tiers
- 0.9-1.0: All 3 platforms with correct concurrency, architecture, GDPR capabilities, and community stats
- 0.7-0.8: All 3 covered, most data correct, minor omissions
- 0.5-0.6: 2-3 platforms but significant gaps
- 0.3-0.4: Only 1-2 platforms with partial data
- 0.0-0.2: No meaningful data
"""

    _ANALYSIS_RUBRIC = """\
Evaluate the quality of recommendation, GDPR analysis, and rollout plan (0.0-1.0).

## Context
Target scenario: 50+ concurrent editors, EU member data localization.

## Expected recommendation
- Primary: BoardFlow (CRDT, 120 concurrent, region pinning, audit log)
- Backup: CanvasGrid (60 concurrent, EU plugin beta)
- Not recommended: SyncSlate (35 concurrent, no region pinning)

## Expected elements
1. GDPR / data sovereignty analysis section
2. Deployment decision tree or structured comparison
3. Phased rollout plan
4. Multi-region deployment strategy / data replication

## Scoring tiers
- 0.9-1.0: Correct recommendation for the scenario; GDPR analysis; decision tree; phased rollout
- 0.7-0.8: Correct recommendation; some GDPR discussion; partial rollout plan
- 0.5-0.6: Mostly correct but missing GDPR or rollout details
- 0.3-0.4: Vague recommendation; minimal planning
- 0.0-0.2: No meaningful recommendation
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

        # 1. Tool gate
        tool_penalty = self._tool_gate(dispatches)

        # 2. Deterministic
        det_score = self._score_deterministic(dispatches, final_text)

        # 3. Judge
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
            data_score = self._fb_data(final_text)
            analysis_score = self._fb_analysis(final_text)

        # 4a. Additional deterministic: table structure
        table_det = 0.05 * (1.0 if ("|" in final_text and "---" in final_text) else 0.0)

        # 4. Combine
        completion = tool_penalty * (
            0.25 * det_score
            + table_det
            + 0.35 * data_score
            + 0.35 * analysis_score
        )

        scores.completion = round(min(completion, 1.0), 2)
        scores.robustness = self.compute_robustness(dispatches)
        scores.communication = self._score_communication(final_text)
        scores.efficiency_turns = len([m for m in messages if m.message.role == "assistant"])
        return scores

    @staticmethod
    def _has_bounded(text: str, num: str) -> bool:
        import re
        return bool(re.search(rf'(?<!\d){re.escape(num)}(?!\d)', text))

    def _tool_gate(self, dispatches):
        search_calls = [d for d in dispatches if d.tool_name == "web_search"]
        fetch_calls = [d for d in dispatches if d.tool_name == "web_fetch" and d.response_status < 400]
        if not search_calls:
            return 0.2
        if not fetch_calls:
            return 0.4
        return 1.0

    def _score_deterministic(self, dispatches, text):
        score = 0.0
        names = sum(1 for name in ["BoardFlow", "CanvasGrid", "SyncSlate"] if name in text)
        score += 0.50 * min(names / 3, 1.0)
        fetched = {str(d.request_body.get("url")) for d in dispatches
                   if d.tool_name == "web_fetch" and d.response_status < 400}
        critical_hits = len(fetched & self.CRITICAL_FETCH)
        score += 0.50 * min(critical_hits / 4, 1.0)
        return min(score, 1.0)

    def _fb_data(self, text):
        lowered = text.lower()
        score = 0.0
        if any(kw in lowered for kw in ["crdt", "websocket", "ot"]):
            score += 0.1
        if any(kw in lowered for kw in ["postgresql", "redis", "docker", "kubernetes"]):
            score += 0.1
        if "stars" in lowered or "release" in lowered or "发布" in lowered:
            score += 0.1
        if any(kw in lowered for kw in ["gdpr", "数据主权", "data sovereignty", "数据驻留", "data residency", "审计日志", "audit log"]):
            score += 0.15
        if "50+" in text or self._has_bounded(text, "120") or "concurrent" in lowered or "并发" in text:
            score += 0.1
        if "|" in text and "---" in text:
            score += 0.15
        return min(score, 1.0)

    def _fb_analysis(self, text):
        lowered = text.lower()
        score = 0.0
        if "BoardFlow" in text and ("首选" in text or "推荐" in text or "primary" in lowered or "recommend" in lowered):
            score += 0.2
        if "CanvasGrid" in text and ("备选" in text or "次选" in text or "backup" in lowered or "secondary" in lowered or "alternative" in lowered):
            score += 0.15
        if "SyncSlate" in text and any(kw in lowered for kw in ["不建议", "不推荐", "风险高", "not recommend", "not advised"]):
            score += 0.15
        rollout_hits = sum(1 for kw in ["阶段", "phase", "路线图", "roadmap", "试点", "pilot", "双轨", "dual-track", "dual track", "回滚", "rollback", "数据副本", "replica", "replication", "欧盟", "eu"]
                          if kw.lower() in lowered or kw in text)
        score += 0.30 * min(rollout_hits / 4, 1.0)
        if "50+" in text and ("欧盟" in text or "eu" in lowered or "gdpr" in lowered):
            score += 0.1
        return min(score, 1.0)

    def _score_communication(self, text: str) -> float:
        has_table = "|" in text and "---" in text
        section_hits = sum(1 for kw in ["执行摘要", "executive summary", "对比", "comparison", "合规", "compliance", "决策树", "decision tree", "路线图", "roadmap", "首选", "primary", "备选", "backup"]
                          if kw.lower() in text.lower() or kw in text)
        format_score = 0.0
        if has_table:
            format_score += 0.40
        format_score += 0.35 * min(section_hits / 4, 1.0)
        return self.compute_communication_substance(
            text, ["BoardFlow", "CanvasGrid", "SyncSlate", "GDPR", "CRDT", "50+", "回滚", "rollback"],
            min(format_score, 1.0),
        )
