"""CTB_WORKFLOW_10 grader -- product launch checklist.

v2.2: WildClawBench mode (operation + analysis).
- Deterministic 55%: tool gate, checklist status, risk, go/no-go, timeline, lessons
- Judge 45%: launch readiness quality
- Fallback: keyword-based scoring when judge unavailable

Ground truth:
  Completed: functional test (42 cases), security scan (2 CVE), staging (48h), rollback plan
  In progress: release notes (3/28), monitoring thresholds (tomorrow)
  Pending: canary deployment (3/29), customer notification (3/29)
  Risk: batch import connection pool issue (temp expansion, v2.3 fix)
  v2.1 lessons: advance VIP notice, API monitoring, rollback drill
  Go/No-Go: Go -- critical items completed
  Timeline: 09:30 freeze -> 09:45 build -> 10:00 canary 10% -> 10:30 50% -> 11:00 full -> 12:00 confirm
"""

from __future__ import annotations
import re
from typing import Any
from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class Grader(AbstractGrader):
    _LAUNCH_RUBRIC = """\
Evaluate the quality of launch readiness assessment (0.0-1.0).

## Ground Truth
- Completed items: functional testing (42 test cases), security scan (2 CVEs found), staging environment (48h stable), rollback plan
- In progress: release notes (due 3/28), monitoring thresholds (due tomorrow)
- Pending: canary deployment (3/29), customer notification (3/29)
- Risk: batch import connection pool issue (temporary expansion applied, permanent fix in v2.3)
- Lessons from v2.1: advance VIP notification, API monitoring enhancement, rollback drill
- Decision: GO -- critical items completed
- Launch timeline: 09:30 code freeze -> 09:45 build -> 10:00 canary 10% -> 10:30 canary 50% -> 11:00 full rollout -> 12:00 confirmation

## Scoring tiers
- 0.9-1.0: All checklist items with correct status; risk assessed; v2.1 lessons applied; Go decision justified; timeline complete
- 0.7-0.8: Most items covered; risk noted; Go decision present; basic timeline
- 0.5-0.6: Partial checklist; some status tracking
- 0.3-0.4: Minimal coverage
- 0.0-0.2: No meaningful assessment
"""

    def grade(self, messages, dispatches, task, audit_data=None, judge=None, media_events=None, env_snapshot=None):
        scores = DimensionScores(safety=1.0)
        all_text = self._get_all_assistant_text(messages)
        lower = all_text.lower()

        svc = set()
        for d in dispatches:
            if d.response_status < 400:
                for s in ["gmail", "todo", "notes", "calendar"]:
                    if s in d.tool_name: svc.add(s)
        tool_penalty = 1.0 if len(svc) >= 3 else (0.5 if len(svc) >= 2 else 0.2)

        det_score = 0.0

        # Checklist status tracking
        completed = ["\u529f\u80fd\u6d4b\u8bd5", "functional test", "\u5b89\u5168\u626b\u63cf", "security scan", "staging", "\u56de\u6eda", "rollback"]
        det_score += 0.12 * min(sum(1 for c in completed if c.lower() in lower) / 3, 1.0)

        in_progress = ["\u53d1\u5e03\u8bf4\u660e", "release note", "\u76d1\u63a7", "monitor"]
        det_score += 0.08 * min(sum(1 for i in in_progress if i.lower() in lower) / 2, 1.0)

        pending = ["\u7070\u5ea6", "canary", "grayscale", "\u5ba2\u6237\u901a\u77e5", "customer notification"]
        det_score += 0.08 * min(sum(1 for p in pending if p.lower() in lower) / 1, 1.0)

        # Risk assessment
        rk = 0.0
        if any(k in lower for k in ["\u6279\u91cf\u5bfc\u5165", "batch import"]) and any(k in lower for k in ["\u8fde\u63a5\u6c60", "connection pool", "\u98ce\u9669", "risk"]): rk += 0.4
        if any(k in lower for k in ["\u4e34\u65f6", "temporary"]) and any(k in lower for k in ["\u6269\u5bb9", "expansion"]): rk += 0.3
        if "v2.3" in all_text: rk += 0.3
        det_score += 0.10 * min(rk, 1.0)

        # v2.1 lessons
        vl = 0.0
        if "VIP" in all_text and any(k in lower for k in ["\u901a\u77e5", "notif"]): vl += 0.30
        if "API" in all_text and any(k in lower for k in ["\u76d1\u63a7", "monitor"]): vl += 0.25
        if any(k in lower for k in ["\u56de\u6eda", "rollback"]) and any(k in lower for k in ["\u6f14\u7ec3", "drill", "test"]): vl += 0.25
        if "v2.1" in all_text: vl += 0.20
        det_score += 0.08 * min(vl, 1.0)

        # Go/No-Go decision
        gn = 0.0
        if "Go" in all_text: gn += 0.35
        if any(k in lower for k in ["\u5173\u952e", "critical"]) and any(k in lower for k in ["\u5b8c\u6210", "complete"]): gn += 0.35
        if any(k in lower for k in ["\u5efa\u8bae", "recommend"]) and any(k in lower for k in ["\u53d1\u5e03", "launch"]): gn += 0.30
        det_score += 0.08 * min(gn, 1.0)

        # Launch timeline
        times = ["09:30", "09:45", "10:00", "10:30", "11:00", "12:00"]
        tl = min(sum(1 for t in times if t in all_text) / 4, 1.0) * 0.5
        if any(k in lower for k in ["\u7070\u5ea6", "canary"]) and ("10%" in all_text or "50%" in all_text): tl += 0.25
        if any(k in lower for k in ["\u5168\u91cf", "full rollout", "100%"]): tl += 0.25
        det_score += 0.08 * min(tl, 1.0)

        # Status keywords
        status_kw = ["\u5df2\u5b8c\u6210", "\u8fdb\u884c\u4e2d", "\u5f85\u5b8c\u6210", "completed", "pending", "in progress"]
        det_score += 0.06 * min(sum(1 for k in status_kw if k in lower or k in all_text) / 2, 1.0)

        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            launch = judge.evaluate(task.prompt.text, conversation, actions, self._LAUNCH_RUBRIC).score
        else:
            launch = self._fallback(lower, all_text)

        completion = tool_penalty * (0.55 * det_score + 0.45 * launch)
        scores.completion = min(round(completion, 2), 1.0)
        scores.robustness = self.compute_robustness(dispatches)
        scores.efficiency_turns = len([m for m in messages if m.message.role == "assistant"])
        return scores

    def _fallback(self, lower, text):
        """_fallback_: dev-only keyword scoring for launch readiness."""
        score = 0.0
        items = ["functional test", "\u529f\u80fd\u6d4b\u8bd5", "security scan", "staging", "rollback", "canary"]
        score += 0.25 * min(sum(1 for i in items if i.lower() in lower) / 3, 1.0)
        if "Go" in text: score += 0.15
        if any(k in lower for k in ["\u6279\u91cf\u5bfc\u5165", "batch import"]): score += 0.10
        if "v2.1" in text: score += 0.10
        times = ["09:30", "10:00", "11:00"]
        score += 0.20 * min(sum(1 for t in times if t in text) / 2, 1.0)
        if any(k in lower for k in ["completed", "pending", "in progress"]): score += 0.10
        if "|" in text or "##" in text: score += 0.10
        return min(score, 1.0)
