"""CTB_PRODAPP_04 grader — overdue task escalation.

Ground truth source: task.yaml reference_solution is empty for this task.
Rubric ground truth derived from fixture data: fixtures/todo/tasks.json, fixtures/notes/meetings.json, fixtures/calendar/events.json

v2.3: WildClawBench mode (has operations — todo_update_task).
- Deterministic 55%: tool gate (3 services), overdue task names identified,
                     escalation rules referenced, critical flagged, assignees, task updates,
                     overdue classification bonus
- Judge 45%: escalation plan quality (date-aware), remediation recommendations
- NOTE: task.yaml does NOT specify "today's date". Agent may use system date.
  Deterministic checks only verify time-INVARIANT facts (task names, assignees, rules).
  Date-dependent logic (overdue days, specific L1/L2/L3 per task) is evaluated by judge
  based on internal consistency with whatever date the agent uses.
- v2.3 fix: _check_overdue_classification no longer penalizes "all tasks overdue" answers.
  Since all 5 task due dates are <= 2026-04-05, any agent running after that date
  correctly classifies all as overdue. The old _check_non_overdue_handling required
  explicit mention of non-overdue tasks, which was a false penalty.
"""

from __future__ import annotations

import re

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.trace import DimensionScores


class Grader(AbstractGrader):

    # ── Judge rubrics ──────────────────────────────────────────────

    _ESCALATION_PLAN_RUBRIC = """\
Evaluate the quality of the escalation plan for overdue tasks (0.0-1.0).

## Fixture facts (time-invariant)
The todo list contains 5 tasks:
1. Payment Module Refactoring — Qiang Wang — due 2026-03-25, high priority
2. User Feedback System Bug Fix — Na Li — due 2026-03-27, high priority
3. Data Migration Verification — Min Zhao — due 2026-03-28, critical priority
4. Performance Testing Report — Lei Chen — due 2026-03-30, medium priority
5. New Feature Requirements Documentation — Wei Zhang — due 2026-04-05, medium priority

## Escalation rules (from notes fixture)
- L1: 1-3 days overdue → notify assignee + direct supervisor
- L2: 4-7 days overdue → escalate to PM, 48h remediation plan
- L3: >7 days → escalate to department head
- Critical priority → immediately L2 (minimum)
- Customer-facing → immediately L3

## IMPORTANT: Date-aware evaluation
Task.yaml does NOT fix "today's date". The agent may use the system date at runtime.
Evaluate whether the agent's overdue analysis is INTERNALLY CONSISTENT with whatever
date it assumes. For example:
- If agent assumes today=2026-03-28: tasks 1-3 are overdue (3d, 1d, 0d), tasks 4-5 are not
- If agent assumes today=2026-04-07: all 5 tasks are overdue (13d, 11d, 10d, 8d, 2d)
Both are correct IF the escalation levels match the rules for the computed overdue days.

## Scoring tiers
- 0.9-1.0: Overdue tasks correctly identified for the assumed date; escalation levels internally consistent with rules; critical priority override applied
- 0.7-0.8: Most overdue tasks identified; mostly correct escalation levels for assumed date
- 0.5-0.6: Partial identification; some inconsistency in escalation logic
- 0.3-0.4: Minimal overdue identification
- 0.0-0.2: No meaningful escalation plan
"""

    _REMEDIATION_RUBRIC = """\
Evaluate the quality of remediation recommendations (0.0-1.0).

## Expected elements
1. Root cause analysis for each overdue task (resource constraints, complexity, dependencies)
2. Concrete remediation actions:
   - Add resources / pair programming
   - Adjust scope / reduce requirements
   - Extend deadline with stakeholder approval
   - Reduce non-essential meetings
3. Calendar context: consider upcoming meetings that may conflict
4. Priority-based urgency: critical tasks should get most urgent action

## Scoring tiers
- 0.9-1.0: All overdue tasks with cause analysis and specific remediation; calendar conflicts noted; priority ranking
- 0.7-0.8: Remediation for most tasks; some cause analysis
- 0.5-0.6: Generic remediation suggestions; partial coverage
- 0.3-0.4: Minimal remediation content
- 0.0-0.2: No meaningful remediation
"""

    # ── Helpers ───────────────────────────────────────────────────

    @staticmethod
    def _has_bounded(text: str, num: str) -> bool:
        return bool(re.search(rf'(?<!\d){re.escape(num)}(?!\d)', text))

    # ── Grading ──────────────────────────────────────────────────

    def grade(self, messages, dispatches, task, audit_data=None,
              judge=None, media_events=None, env_snapshot=None):
        scores = DimensionScores()
        scores.safety = 1.0
        final_text = self._get_final_assistant_text(messages)

        # 1. Tool gate
        tool_penalty = self._tool_gate(dispatches)

        # 2. Deterministic: time-INVARIANT facts only (55%)
        det = 0.0
        det += 0.08 * self._check_service_coverage(dispatches)   # dim1: 3 services accessed
        det += 0.15 * self._check_overdue_tasks_named(final_text) # dim2: overdue task names identified
        det += 0.10 * self._check_escalation_rules_referenced(final_text)  # dim3: L1/L2/L3 rules used
        det += 0.07 * self._check_critical_flagged(final_text)    # dim4: Data Migration Verification flagged as critical/urgent
        det += 0.05 * self._check_assignees(final_text)           # dim5: correct assignees
        det += 0.05 * self._check_task_updates(dispatches)        # dim6: todo_update_task calls
        det += 0.05 * self._check_overdue_classification(final_text) # dim7: bonus for explicit overdue/non-overdue separation (not required)

        # 3. Judge: date-aware evaluation (45%)
        judge_score = 0.0
        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            judge_score += 0.25 * judge.evaluate(
                task.prompt.text, conversation, actions, self._ESCALATION_PLAN_RUBRIC
            ).score
            judge_score += 0.20 * judge.evaluate(
                task.prompt.text, conversation, actions, self._REMEDIATION_RUBRIC
            ).score
        else:
            judge_score = self._fallback_judge(final_text)

        # 4. Combine
        completion = tool_penalty * (det + judge_score)
        scores.completion = round(min(completion, 1.0), 2)
        scores.robustness = self.compute_robustness(dispatches)
        scores.efficiency_turns = len([m for m in messages if m.message.role == "assistant"])
        return scores

    # ── Deterministic helpers (time-invariant only) ────────────────

    def _tool_gate(self, dispatches):
        svc = set()
        for d in dispatches:
            if d.response_status < 400:
                if "todo" in d.tool_name:
                    svc.add("todo")
                if "notes" in d.tool_name:
                    svc.add("notes")
                if "calendar" in d.tool_name:
                    svc.add("calendar")
        if not svc:
            return 0.2
        if len(svc) < 2:
            return 0.5
        return 1.0

    @staticmethod
    def _check_service_coverage(dispatches):
        svc = set()
        for d in dispatches:
            if d.response_status < 400:
                if "todo" in d.tool_name:
                    svc.add("todo")
                if "notes" in d.tool_name:
                    svc.add("notes")
                if "calendar" in d.tool_name:
                    svc.add("calendar")
        return len(svc) / 3.0

    @staticmethod
    def _check_overdue_tasks_named(text):
        """Check that the 3 tasks with earliest due dates are identified as overdue/at-risk.
        Time-invariant: these 3 tasks (due 3/25, 3/27, 3/28) are overdue for ANY date >= 3/28."""
        hits = 0
        overdue_kw = ["overdue", "delay", "past due", "behind schedule", "late",
                       "逾期", "过期", "超期", "到期", "截止", "延迟"]
        if ("payment module" in text.lower() or "支付模块" in text) and any(k in text.lower() for k in overdue_kw):
            hits += 1
        if ("bug fix" in text.lower() or "user feedback" in text.lower() or "Bug修复" in text or "用户反馈" in text) and any(k in text.lower() for k in overdue_kw):
            hits += 1
        if ("data migration" in text.lower() or "数据迁移" in text) and any(k in text.lower() for k in overdue_kw + ["critical", "urgent", "紧急", "关键"]):
            hits += 1
        return min(hits / 3, 1.0)

    @staticmethod
    def _check_escalation_rules_referenced(text):
        """Check that escalation rules (L1/L2/L3 or equivalent) are referenced.
        Time-invariant: doesn't check which task gets which level."""
        levels_found = 0
        if any(k in text for k in ["L1", "一级", "通知主管", "notif", "Level 1", "level 1", "notify supervisor"]):
            levels_found += 1
        if any(k in text for k in ["L2", "二级", "项目经理", "Level 2", "level 2", "project manager"]):
            levels_found += 1
        if any(k in text for k in ["L3", "三级", "部门负责人", "owner", "部门主管", "Level 3", "level 3", "department head"]):
            levels_found += 1
        return min(levels_found / 2, 1.0)  # at least 2 of 3 levels mentioned

    @staticmethod
    def _check_critical_flagged(text):
        """Check that Data Migration Verification is flagged with special urgency due to critical priority.
        Time-invariant: critical priority always triggers elevated handling."""
        if "data migration" in text.lower() or "数据迁移" in text:
            if any(k in text.lower() for k in ["critical", "urgent", "highest priority", "immediate",
                                                "关键", "紧急", "最高优先", "立即"]):
                return 1.0
            if any(k in text.lower() for k in ["priority", "important", "severe",
                                                "优先", "重要", "严重"]):
                return 0.5
        return 0.0

    @staticmethod
    def _check_assignees(text):
        """Check assignees mentioned with their tasks."""
        hits = 0
        if any(n in text for n in ["王强", "Qiang Wang", "Wang Qiang"]):
            hits += 1
        if any(n in text for n in ["李娜", "Na Li", "Li Na"]):
            hits += 1
        if any(n in text for n in ["赵敏", "Min Zhao", "Zhao Min"]):
            hits += 1
        return min(hits / 2, 1.0)

    @staticmethod
    def _check_task_updates(dispatches):
        """Check if todo_update_task was called."""
        updates = [d for d in dispatches
                   if d.tool_name == "todo_update_task" and d.response_status < 400]
        if len(updates) >= 2:
            return 1.0
        if len(updates) == 1:
            return 0.6
        return 0.0

    @staticmethod
    def _check_overdue_classification(text):
        """Bonus for explicit overdue classification.
        Since the task does NOT fix today's date, it is entirely valid for an agent
        running after 2026-04-05 to classify ALL 5 tasks as overdue.
        Therefore:
        - Full credit (1.0) if the agent mentions overdue identification at all —
          whether it distinguishes some as non-overdue or classifies all as overdue.
        - 0.0 only if the agent does not mention overdue classification at all.
        """
        has_overdue = any(k in text.lower() for k in [
            "overdue", "past due", "behind schedule", "late", "delayed",
            "逾期", "过期", "超期", "到期", "截止", "延迟",
        ])
        if has_overdue:
            return 1.0
        return 0.0

    # ── Fallback (dev-only) ──────────────────────────────────────

    @staticmethod
    def _fallback_judge(text):
        """_fallback_: keyword-based, only for --no-judge dev mode."""
        score = 0.0
        if any(k in text.lower() for k in ["升级流程", "escalat", "升级级别", "L1", "L2", "L3", "escalation"]):
            score += 0.10
        action_kws = ["补救", "计划", "plan", "增加资源", "延长", "调整范围", "行动", "减少会议",
                       "remediat", "add resource", "extend", "reduce scope", "action", "reduce meeting"]
        score += 0.12 * min(sum(1 for k in action_kws if k in text.lower()) / 2, 1.0)
        cause_kws = ["工时", "依赖", "阻塞", "blocker", "优先级", "冲突", "复杂", "资源",
                      "workload", "dependency", "block", "priority", "conflict", "complex", "resource"]
        score += 0.10 * min(sum(1 for k in cause_kws if k in text.lower()) / 2, 1.0)
        if any(k in text.lower() for k in ["周会", "部署协调", "会议安排", "weekly meeting", "deploy coordination", "meeting schedule"]):
            score += 0.06
        return min(score, 0.45)
