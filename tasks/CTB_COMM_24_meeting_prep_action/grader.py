"""CTB_COMM_24 grader -- meeting prep action items.

v2.2: analysis mode (workflow/communication, no write operations).
- Deterministic 35%: tool gate, key people+status pairs, data points (12000, 78%)
- Judge 65%: product review accuracy, strategy meeting accuracy, status tracking
- Fallback: keyword-based scoring when judge unavailable

Ground truth:
  Product Review Meeting (3/30): User growth report - Li Ming - completed,
                    Competitive analysis - Zhao Li - unconfirmed,
                    Tech architecture v2 - Wang Tao - in progress (due 3/29),
                    New users 12,000, retention rate 78%
  Strategy Planning Meeting (4/1): OKR - all VPs,
                   Overseas market - Liu Yang - submitted for review,
                   AI roadmap - Tech VP - unconfirmed,
                   Southeast Asia (Thailand/Vietnam/Indonesia)
"""

from __future__ import annotations

import re

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.trace import DimensionScores


class Grader(AbstractGrader):

    # -- Judge rubrics --

    _REVIEW_MEETING_RUBRIC = """\
Evaluate accuracy of the product review meeting (3/30) prep items (0.0-1.0).

## Ground Truth
Meeting: Product Review Meeting on 3/30, Room A301, attendees: Zhang Wei, Li Ming, Zhao Li, Wang Tao

### Prep items and status
1. Q1 User Growth Data Report -- Li Ming -- completed (uploaded to shared drive)
   - Key data: Q1 new users 12,000, retention rate 78%
2. Competitive Analysis Document -- Zhao Li -- unconfirmed (no reply email, status unknown)
3. Tech Architecture Proposal v2 -- Wang Tao -- in progress (expected completion 3/29)

## Scoring tiers
- 0.9-1.0: All 3 items with correct person and status; includes user/retention data
- 0.7-0.8: All 3 items identified with mostly correct status
- 0.5-0.6: 2 items correct; partial data
- 0.3-0.4: 1 item correct or major status errors
- 0.0-0.2: No meaningful review meeting prep info
"""

    _STRATEGY_MEETING_RUBRIC = """\
Evaluate accuracy of the strategy meeting (4/1) prep items (0.0-1.0).

## Ground Truth
Meeting: Strategy Planning Meeting on 4/1, Board Room, attendees: CEO Chen, Liu Yang, all VPs

### Prep items and status
1. Department OKR completion status -- all VPs -- required to prepare
2. Overseas Market Expansion Plan -- Liu Yang -- submitted to CEO Chen for review
   - Focus: Southeast Asia (Thailand, Vietnam, Indonesia)
3. AI Product Roadmap -- Tech VP -- unconfirmed (mentioned in email but no response)

## Scoring tiers
- 0.9-1.0: All 3 items with correct responsible person and status; SE Asia countries mentioned
- 0.7-0.8: All items identified; mostly correct status
- 0.5-0.6: 2 items correct
- 0.3-0.4: 1 item or vague coverage
- 0.0-0.2: No meaningful strategy meeting prep info
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
        clean = final_text.replace(",", "").replace(",", "")

        # 1. Tool gate: both calendar and gmail accessed?
        tool_penalty = self._tool_gate(dispatches)

        # 2. Deterministic dimensions (35%)
        det = 0.0
        det += 0.05 * self._check_api_coverage(dispatches)     # dim1: API coverage
        det += 0.10 * self._check_people_status(final_text)     # dim2: person+status pairs
        det += 0.08 * self._check_data_points(clean, final_text)  # dim3: 12000 users, 78%
        det += 0.07 * self._check_meeting_dates(final_text)     # dim4: meeting dates
        det += 0.05 * self._check_status_labels(final_text)     # dim5: status labels coverage

        # 3. Judge dimensions (65%)
        judge_score = 0.0
        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            judge_score += 0.35 * judge.evaluate(
                task.prompt.text, conversation, actions, self._REVIEW_MEETING_RUBRIC
            ).score
            judge_score += 0.30 * judge.evaluate(
                task.prompt.text, conversation, actions, self._STRATEGY_MEETING_RUBRIC
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
        cal = any(d.tool_name in ("calendar_list_events", "calendar_get_event")
                  and d.response_status < 400 for d in dispatches)
        mail = any(d.tool_name in ("gmail_list_messages", "gmail_get_message")
                   and d.response_status < 400 for d in dispatches)
        if not cal and not mail:
            return 0.2
        if not cal or not mail:
            return 0.5
        return 1.0

    def _check_api_coverage(self, dispatches):
        svc = set()
        for d in dispatches:
            if d.response_status < 400:
                if d.tool_name in ("calendar_list_events", "calendar_get_event"):
                    svc.add("calendar")
                if d.tool_name in ("gmail_list_messages", "gmail_get_message"):
                    svc.add("gmail")
        return len(svc) / 2.0

    @staticmethod
    def _check_people_status(text):
        """Check key person+status pairs."""
        hits = 0
        if ("Li Ming" in text or "李明" in text) and ("completed" in text.lower() or "done" in text.lower() or "finished" in text.lower() or "完成" in text):
            hits += 1
        if ("Zhao Li" in text or "赵丽" in text) and ("unconfirmed" in text.lower() or "no reply" in text.lower() or "unknown" in text.lower() or "no response" in text.lower() or "未确认" in text or "未回复" in text or "不确定" in text):
            hits += 1
        if ("Wang Tao" in text or "王涛" in text) and ("in progress" in text.lower() or "in-progress" in text.lower() or "ongoing" in text.lower() or "修改中" in text or "进行中" in text or "3/29" in text or "3月29" in text):
            hits += 1
        if ("Liu Yang" in text or "刘洋" in text) and ("submitted" in text.lower() or "review" in text.lower() or "sent for review" in text.lower() or "已提交" in text or "审阅" in text or "已发送" in text):
            hits += 1
        return min(hits / 3, 1.0)

    def _check_data_points(self, clean, text):
        """Check verifiable data: 12000 users, 78% retention."""
        score = 0.0
        if self._has_bounded(clean, "12000") or "1.2万" in text:
            score += 0.50
        if "78%" in text or self._has_bounded(clean, "78"):
            score += 0.50
        return score

    @staticmethod
    def _check_meeting_dates(text):
        """Check meeting dates mentioned."""
        lower = text.lower()
        score = 0.0
        if "3/30" in text or "3月30" in text or "Product Review" in text or "产品评审" in text or "product review" in lower:
            score += 0.50
        if "4/1" in text or "4月1" in text or "Strategy" in text or "战略规划" in text or "strategy" in lower:
            score += 0.50
        return score

    @staticmethod
    def _check_status_labels(text):
        """Check variety of status labels."""
        statuses = ["completed", "in progress", "unconfirmed", "submitted", "已完成", "进行中", "未确认", "已提交"]
        found = sum(1 for s in statuses if s in text.lower())
        return min(found / 3, 1.0)

    # -- Fallback (dev-only) --

    @staticmethod
    def _fallback_judge(clean, text):
        """_fallback_: keyword-based, only for --no-judge dev mode."""
        score = 0.0

        # Review meeting items
        review_hits = 0
        if ("user growth" in text.lower() or "用户增长" in text or "growth data" in text.lower() or "growth report" in text.lower()) and ("Li Ming" in text or "李明" in text):
            review_hits += 1
        if ("competitive" in text.lower() or "竞品分析" in text or "competitor" in text.lower()) and ("Zhao Li" in text or "赵丽" in text):
            review_hits += 1
        if ("architecture" in text.lower() or "技术架构" in text or "tech architecture" in text.lower()) and ("Wang Tao" in text or "王涛" in text):
            review_hits += 1
        score += 0.20 * min(review_hits / 2, 1.0)

        # Strategy meeting items
        strat_hits = 0
        if "OKR" in text and ("VP" in text):
            strat_hits += 1
        if ("overseas" in text.lower() or "海外市场" in text or "expansion" in text.lower() or "international" in text.lower()) and ("Liu Yang" in text or "刘洋" in text):
            strat_hits += 1
        if "AI" in text and ("roadmap" in text.lower() or "路线图" in text or "产品" in text or "product" in text.lower()):
            strat_hits += 1
        score += 0.18 * min(strat_hits / 2, 1.0)

        # Southeast Asia detail
        sea_kw = ["Southeast Asia", "Thailand", "Vietnam", "Indonesia", "东南亚", "泰国", "越南", "印尼"]
        sea_found = sum(1 for kw in sea_kw if kw in text)
        score += 0.10 * min(sea_found / 2, 1.0)

        # Structure
        if len(text.strip()) >= 300:
            score += 0.05

        return min(score, 0.65)
