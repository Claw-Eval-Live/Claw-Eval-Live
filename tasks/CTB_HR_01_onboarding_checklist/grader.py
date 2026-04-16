"""CTB_HR_01 grader -- new employee onboarding checklist.

Ground truth source: task.yaml reference_solution + fixtures.

v2.2: Claw-Eval mode (HR analysis report).
- Deterministic 35%: tool gate, employee coverage, department-specific items
- Judge 65%: checklist completeness, training plan, report quality
- Fallback: keyword-based scoring when judge unavailable

Ground truth:
  Zhang Wei (EMP-101): Engineering, Apr 1, GitHub/AWS, mentor Li Ming
  Liu Yang (EMP-102): Marketing, Apr 2, CRM/marketing budget
  Chen Jing (EMP-103): Finance, Apr 3, SAP/bank USB key
  Common: access badge, corporate email, company culture training, IT security training
"""

from __future__ import annotations

import re
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


_EMPLOYEE_DATA = [
    {
        "names": ["Zhang Wei", "Wei Zhang"],
        "dept": ["Engineering", "Tech"],
        "specific": ["GitHub", "AWS", "Li Ming"],
    },
    {
        "names": ["Liu Yang", "Yang Liu"],
        "dept": ["Marketing"],
        "specific": ["CRM", "marketing budget"],
    },
    {
        "names": ["Chen Jing", "Jing Chen"],
        "dept": ["Finance"],
        "specific": ["SAP", "USB key", "USB token", "bank"],
    },
]


class Grader(AbstractGrader):
    """Grade a new employee onboarding checklist report."""

    _CHECKLIST_RUBRIC = """\
Evaluate the completeness of per-employee onboarding checklists (0.0-1.0).

## Ground Truth
- Zhang Wei (EMP-101): Senior Developer, Engineering, Apr 1. Needs: laptop+monitor, access badge, corporate email, mentor Li Ming, GitHub Enterprise, AWS permissions, code repo access. Training: company culture (AM), IT security (PM), department orientation.
- Liu Yang (EMP-102): Marketing Manager, Marketing, Apr 2. Needs: laptop, access badge, corporate email, CRM access, marketing budget system access.
- Chen Jing (EMP-103): Financial Analyst, Finance, Apr 3. Needs: laptop+calculator, access badge, corporate email, SAP access, bank USB key.

## Scoring tiers
- 0.9-1.0: All 3 employees with complete checklists including department-specific items
- 0.7-0.8: All 3 employees covered; most specific items present
- 0.5-0.6: 2-3 employees; partial specific items
- 0.3-0.4: 1-2 employees with minimal detail
- 0.0-0.2: No meaningful checklists
"""

    _TRAINING_RUBRIC = """\
Evaluate the training plan and common onboarding items (0.0-1.0).

## Expected common items for ALL new employees
- IT setup: laptop, email account, system access
- HR paperwork: contracts, tax forms, benefits enrollment
- Access badge/card
- Corporate email setup
- Company culture training (Apr 1 AM)
- IT security training (Apr 1 PM)
- Department orientation (Apr 2)
- Buddy/mentor assignment (Zhang Wei -> Li Ming)

## Scoring tiers
- 0.9-1.0: Comprehensive training schedule; all common items; mentor assignment noted
- 0.7-0.8: Good training plan; most common items
- 0.5-0.6: Some training items; partial common items
- 0.3-0.4: Minimal training mention
- 0.0-0.2: No training plan
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

        # 1. Tool gate
        tool_penalty = self._tool_gate(dispatches)

        # 2. Deterministic checks (35%)
        det_score = 0.0
        det_score += 0.35 * self._score_employee_coverage(all_text, lower)
        det_score += 0.35 * self._score_dept_specific_items(all_text, lower)
        det_score += 0.30 * self._score_common_items(all_text, lower)

        # 3. Judge scoring (65%)
        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            checklist_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._CHECKLIST_RUBRIC
            ).score
            training_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._TRAINING_RUBRIC
            ).score
        else:
            checklist_score = self._fallback_checklist(all_text, lower)
            training_score = self._fallback_training(all_text, lower)

        # 4. Combine
        completion = tool_penalty * (
            0.35 * det_score
            + 0.35 * checklist_score
            + 0.30 * training_score
        )

        scores.completion = min(round(completion, 2), 1.0)
        scores.robustness = self.compute_robustness(dispatches)
        scores.efficiency_turns = len(
            [m for m in messages if m.message.role == "assistant"]
        )
        return scores

    # ── Deterministic helpers ──────────────────────────────────────

    def _tool_gate(self, dispatches: list[ToolDispatch]) -> float:
        gmail_calls = [d for d in dispatches
                       if d.tool_name in ("gmail_list_messages", "gmail_get_message")
                       and d.response_status < 400]
        crm_calls = [d for d in dispatches
                     if d.tool_name in ("crm_list_customers", "crm_get_customer")
                     and d.response_status < 400]
        if not gmail_calls and not crm_calls:
            return 0.2
        if not gmail_calls or not crm_calls:
            return 0.5
        return 1.0

    def _score_employee_coverage(self, all_text: str, lower: str) -> float:
        found = 0
        for emp in _EMPLOYEE_DATA:
            if any(n.lower() in lower for n in emp["names"]):
                found += 1
        return found / len(_EMPLOYEE_DATA)

    def _score_dept_specific_items(self, all_text: str, lower: str) -> float:
        total_specific = 0
        found_specific = 0
        for emp in _EMPLOYEE_DATA:
            if not any(n.lower() in lower for n in emp["names"]):
                continue
            for item in emp["specific"]:
                total_specific += 1
                if item.lower() in lower:
                    found_specific += 1
        if total_specific == 0:
            return 0.0
        return min(found_specific / max(total_specific * 0.6, 1), 1.0)

    def _score_common_items(self, all_text: str, lower: str) -> float:
        common = ["badge", "access card", "corporate email", "company email",
                   "company culture", "culture training", "IT security",
                   "security training", "mentor", "buddy"]
        found = sum(1 for item in common if item.lower() in lower)
        return min(found / 4, 1.0)

    # ── Fallback scorers ───────────────────────────────────────────

    def _fallback_checklist(self, all_text: str, lower: str) -> float:
        """_fallback_: dev-only."""
        score = 0.0
        for emp in _EMPLOYEE_DATA:
            if any(n.lower() in lower for n in emp["names"]):
                score += 0.15
                if any(d.lower() in lower for d in emp["dept"]):
                    score += 0.05
                items_found = sum(1 for it in emp["specific"] if it.lower() in lower)
                score += 0.10 * min(items_found / 2, 1.0)
        return min(score, 1.0)

    def _fallback_training(self, all_text: str, lower: str) -> float:
        """_fallback_: dev-only."""
        score = 0.0
        if "Li Ming" in all_text and any(kw in lower for kw in ["mentor", "buddy"]):
            score += 0.25
        training_kw = ["company culture", "IT security", "orientation", "training"]
        found = sum(1 for kw in training_kw if kw.lower() in lower)
        score += 0.25 * min(found / 2, 1.0)
        common = ["badge", "access card", "email", "laptop"]
        found_c = sum(1 for c in common if c.lower() in lower)
        score += 0.25 * min(found_c / 2, 1.0)
        if len(all_text.strip()) >= 300:
            score += 0.25
        return min(score, 1.0)
