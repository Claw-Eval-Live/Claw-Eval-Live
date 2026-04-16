"""CTB_DATA_20 grader -- project cost vs plan comparison.

v2.2: Claw-Eval mode (data analysis).
- Deterministic 35%: tool gate, total actual 550K, total budget 530K, overrun 3.8%, stage counts
- Judge 65%: stage-by-stage comparison accuracy, progress tracking, budget risk assessment
- Fallback: keyword-based scoring when judge unavailable

Ground truth:
  Smart Campus project:
  Total actual: 550K (150+80+200+35+60+25), Total budget: 530K (50+80+120+180+30+50+20)
  Over: 20K (+3.8%)
  Stages: Front-end 120->150(+30), Back-end 180->200(+20), Testing 30->35(+5),
          Deployment 50->60(+10), Documentation 20->25(+5), UI Design 80->80(0)
  Progress: 4/7 completed (Requirements+UI+Front-end+Requirements Analysis?), 2 in_progress, 1 pending
"""

from __future__ import annotations

import re

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.trace import DimensionScores


class Grader(AbstractGrader):

    # -- Judge rubrics --

    _STAGE_COMPARISON_RUBRIC = """\
Evaluate the accuracy of stage-by-stage cost comparison (0.0-1.0).

## Ground Truth -- Budget vs Actual by stage
| Stage | Budget (K) | Actual (K) | Variance |
|-------|-----------|-----------|----------|
| Requirements Analysis | 50 | (no transaction) | -- |
| UI Design | 80 | 80 | 0 (on budget) |
| Front-end Development | 120 | 150 | +30K (overspent) |
| Back-end Development | 180 | 200 | +20K (overspent) |
| Testing | 30 | 35 | +5K |
| Deployment | 50 | 60 | +10K |
| Documentation | 20 | 25 | +5K |

Key insight: Front-end (+30K) and back-end (+20K) are the biggest overruns.

## Scoring tiers
- 0.9-1.0: All stages with correct budget/actual/variance; biggest overruns identified
- 0.7-0.8: Most stages correct; key overruns identified
- 0.5-0.6: 3-4 stages with partial data; some variance calculations
- 0.3-0.4: Minimal stage comparison
- 0.0-0.2: No meaningful stage-by-stage data
"""

    _PROGRESS_RUBRIC = """\
Evaluate the accuracy of project task progress tracking (0.0-1.0).

## Ground Truth -- Progress
- Completed (4): Requirements Analysis, UI Design, Front-end Development, (one more from tasks)
- In progress (2): Back-end Development, Testing, Documentation
- Pending (1): Deployment/Go-live

## Scoring tiers
- 0.9-1.0: Correct task status breakdown for all stages; completion counts match
- 0.7-0.8: Most stages with correct status; minor misclassifications
- 0.5-0.6: Partial status coverage; some stages missing
- 0.3-0.4: Minimal progress tracking
- 0.0-0.2: No meaningful progress data
"""

    _RISK_RUBRIC = """\
Evaluate the quality of budget risk assessment and recommendations (0.0-1.0).

## Ground Truth -- Risk Assessment
- Total overrun: 20K / +3.8% over budget
- Project is within acceptable range but trending over budget
- Front-end and back-end outsourcing are the primary cost drivers
- Remaining items (deployment, documentation) still in progress -- risk of further overrun
- Need to monitor closely to prevent budget blow-out

## Scoring tiers
- 0.9-1.0: Identifies 3.8% overrun; clear risk items with specific recommendations
- 0.7-0.8: Overrun calculated; some risk discussion and suggestions
- 0.5-0.6: Overrun approximately noted; basic risk mention
- 0.3-0.4: Minimal risk assessment
- 0.0-0.2: No meaningful risk analysis
"""

    # -- Helpers --

    @staticmethod
    def _has_bounded(text: str, num: str) -> bool:
        return bool(re.search(rf'(?<!\d){re.escape(num)}(?!\d)', text))

    # -- Grading --

    def grade(self, messages, dispatches, task, audit_data=None,
              judge=None, media_events=None, env_snapshot=None):
        scores = DimensionScores()
        scores.safety = 1.0
        final_text = self._get_final_assistant_text(messages)
        clean = final_text.replace(",", "").replace(",", "").replace("$", "").replace("USD", "")

        # 1. Tool gate
        tool_penalty = self._tool_gate(dispatches)

        # 2. Deterministic dimensions (35%)
        det = 0.0
        det += 0.05 * self._check_api_calls(dispatches)        # dim1: both APIs called
        det += 0.10 * self._check_totals(clean, final_text)     # dim2: 550K actual, 530K budget
        det += 0.08 * self._check_overrun_pct(clean, final_text)  # dim3: 3.8% / 20K overrun
        det += 0.07 * self._check_key_stages(clean, final_text)   # dim4: front+back overrun
        det += 0.05 * self._check_progress_statuses(final_text)   # dim5: status labels

        # 3. Judge dimensions (65%)
        judge_score = 0.0
        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            judge_score += 0.30 * judge.evaluate(
                task.prompt.text, conversation, actions, self._STAGE_COMPARISON_RUBRIC
            ).score
            judge_score += 0.20 * judge.evaluate(
                task.prompt.text, conversation, actions, self._PROGRESS_RUBRIC
            ).score
            judge_score += 0.15 * judge.evaluate(
                task.prompt.text, conversation, actions, self._RISK_RUBRIC
            ).score
        else:
            judge_score = self._fallback_judge(clean, final_text)

        # 4. Combine
        completion = tool_penalty * (det + judge_score)

        scores.completion = round(min(completion, 1.0), 2)
        scores.robustness = self.compute_robustness(dispatches)
        scores.efficiency_turns = len([m for m in messages if m.message.role == "assistant"])
        return scores

    # -- Deterministic helpers --

    def _tool_gate(self, dispatches):
        fin = any(d.tool_name == "finance_list_transactions" and d.response_status < 400
                  for d in dispatches)
        todo = any(d.tool_name == "todo_list_tasks" and d.response_status < 400
                   for d in dispatches)
        if not fin and not todo:
            return 0.2
        if not fin or not todo:
            return 0.5
        return 1.0

    def _check_api_calls(self, dispatches):
        svc = set()
        for d in dispatches:
            if d.response_status < 400:
                if d.tool_name == "finance_list_transactions":
                    svc.add("finance")
                if d.tool_name == "todo_list_tasks":
                    svc.add("todo")
        return len(svc) / 2.0

    def _check_totals(self, clean, text):
        """Total actual 550K, budget 530K."""
        score = 0.0
        if self._has_bounded(clean, "550000") or re.search(r"550K|550k|55万|550\.0|550,000", text):
            score += 0.50
        if self._has_bounded(clean, "530000") or re.search(r"530K|530k|53万|530\.0|530,000", text):
            score += 0.50
        return score

    def _check_overrun_pct(self, clean, text):
        """Overrun 20K / +3.8%."""
        score = 0.0
        if re.search(r"3\.8%|3\.77|3\.78", clean):
            score += 0.60
        if any(kw in text.lower() for kw in ["overrun", "over budget", "variance", "超支", "超预算", "偏差"]) and self._has_bounded(clean, "20"):
            score += 0.40
        return min(score, 1.0)

    def _check_key_stages(self, clean, text):
        """Front-end 120->150 (+30), back-end 180->200 (+20)."""
        text_lower = text.lower()
        score = 0.0
        if ("front" in text_lower or "前端" in text) and self._has_bounded(clean, "150") and self._has_bounded(clean, "120"):
            score += 0.50
        if ("back" in text_lower or "后端" in text) and self._has_bounded(clean, "200") and self._has_bounded(clean, "180"):
            score += 0.50
        return score

    @staticmethod
    def _check_progress_statuses(text):
        """Check progress status labels present."""
        statuses = ["completed", "in progress", "in_progress", "pending", "已完成", "进行中", "待开始", "未开始"]
        found = sum(1 for s in statuses if s in text.lower())
        return min(found / 2, 1.0)

    # -- Fallback (dev-only) --

    @classmethod
    def _fallback_judge(cls, clean, text):
        """_fallback_: keyword-based, only for --no-judge dev mode."""
        score = 0.0

        # Stage detail coverage
        stages = ["front-end", "back-end", "testing", "deployment", "documentation", "UI",
                  "前端", "后端", "测试", "部署", "文档", "UI设计"]
        found = sum(1 for s in stages if s in text.lower() or s in text)
        score += 0.15 * min(found / 4, 1.0)

        # Overrun mentions
        if any(kw in text.lower() for kw in ["overrun", "over budget", "overspent", "超支", "超预算"]):
            score += 0.10
        if any(kw in text.lower() for kw in ["risk", "风险"]):
            score += 0.08

        # Specific amounts for other stages
        detail_hits = 0
        if any(kw in text.lower() for kw in ["test", "测试"]) and ("35" in clean or cls._has_bounded(clean, "35000")):
            detail_hits += 1
        if any(kw in text.lower() for kw in ["deploy", "部署"]) and "60" in clean:
            detail_hits += 1
        if any(kw in text.lower() for kw in ["doc", "文档"]) and ("25" in clean or cls._has_bounded(clean, "25000")):
            detail_hits += 1
        score += 0.12 * min(detail_hits / 2, 1.0)

        # Progress tracking
        if any(kw in text.lower() for kw in ["completed", "完成"]) and any(kw in text.lower() for kw in ["in progress", "in_progress", "进行中"]):
            score += 0.10

        # Table format
        if "|" in text and "---" in text:
            score += 0.05

        return min(score, 0.65)
