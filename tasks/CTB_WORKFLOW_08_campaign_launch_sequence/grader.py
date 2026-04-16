"""CTB_WORKFLOW_08 grader -- campaign launch sequence.

v2.2: WildClawBench mode (operation workflow).
- Deterministic 55%: tool gate, activities, customer strategy, todo creation, progress
- Judge 45%: campaign plan quality
- Fallback: keyword-based scoring when judge unavailable

Ground truth:
  Activities: email marketing (4/1), seminar (4/8, 50 people), case study, blog, social media
  Customers: HuaSheng, DingChuang, LongTeng, Beta, Epsilon, XingChen
  XingChen (380K in-negotiation): VIP + 1-on-1 demo
  LongTeng (renewal risk): show report performance + deep engagement
  Progress: demo env (pending), invitation (pending), blog (in progress)
"""

from __future__ import annotations
import re
from typing import Any
from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class Grader(AbstractGrader):
    _CAMPAIGN_RUBRIC = """\
Evaluate the quality of campaign launch plan (0.0-1.0).

## Ground Truth
- Activities: email marketing (Apr 1), seminar (Apr 8, target 50 people), case study collection, blog posts, social media
- Target customers: HuaSheng, DingChuang, LongTeng, Beta, Epsilon, XingChen
- XingChen strategy: VIP treatment + exclusive 1-on-1 demo (380K deal in negotiation)
- LongTeng strategy: Show report performance improvements + deep engagement (renewal risk)
- Progress tracking: demo environment (pending), invitation letters (pending), blog (in progress)
- Department coordination: R&D, Customer Success, Sales, Marketing

## Scoring tiers
- 0.9-1.0: All activities with dates; customer-specific strategies for XingChen and LongTeng; progress tracked; departments coordinated
- 0.7-0.8: Most activities; some customer strategies; basic progress
- 0.5-0.6: Activity list present; generic customer approach
- 0.3-0.4: Minimal plan
- 0.0-0.2: No meaningful plan
"""

    def grade(self, messages, dispatches, task, audit_data=None, judge=None, media_events=None, env_snapshot=None):
        scores = DimensionScores(safety=1.0)
        all_text = self._get_all_assistant_text(messages)
        lower = all_text.lower()
        clean = all_text.replace(",", "").replace("\uff0c", "")

        svc = set()
        for d in dispatches:
            if d.response_status < 400:
                for s in ["gmail", "crm", "calendar", "todo"]:
                    if s in d.tool_name: svc.add(s)
        tool_penalty = 1.0 if len(svc) >= 3 else (0.5 if len(svc) >= 2 else 0.2)

        det_score = 0.0

        # Activities
        acts_cn = ["\u90ae\u4ef6\u8425\u9500", "\u7814\u8ba8\u4f1a", "\u6848\u4f8b", "\u535a\u5ba2", "\u793e\u5a92"]
        acts_en = ["email marketing", "seminar", "case stud", "blog", "social media"]
        act_found = sum(1 for a in acts_cn if a in all_text) + sum(1 for a in acts_en if a in lower)
        act_found = min(act_found, 5)
        det_score += 0.12 * min(act_found / 3, 1.0)

        # Customer coverage
        custs = [
            ["\u534e\u76db", "Huasheng", "HuaSheng"], ["\u9f0e\u521b", "Dingchuang", "DingChuang"],
            ["\u9f99\u817e", "Longteng", "LongTeng"], ["Beta"], ["Epsilon"],
            ["\u661f\u8fb0", "Xingchen", "XingChen"],
        ]
        cust_found = sum(1 for aliases in custs if any(a in all_text for a in aliases))
        det_score += 0.10 * min(cust_found / 4, 1.0)

        # XingChen VIP strategy
        xc_region = ""
        for a in ["\u661f\u8fb0", "Xingchen", "XingChen"]:
            if a in all_text:
                idx = all_text.index(a)
                xc_region = all_text[max(0, idx - 80):idx + 400]
                break
        if xc_region:
            xc = 0.2
            if "380" in xc_region.replace(",", ""): xc += 0.2
            if any(k in xc_region.lower() for k in ["1v1", "one-on-one", "\u5355\u72ec", "demo", "\u6f14\u793a"]): xc += 0.3
            if "VIP" in xc_region or any(k in xc_region.lower() for k in ["\u7279\u522b", "special"]): xc += 0.3
            det_score += 0.10 * min(xc, 1.0)

        # LongTeng retention strategy
        lt_region = ""
        for a in ["\u9f99\u817e", "Longteng", "LongTeng"]:
            if a in all_text:
                idx = all_text.index(a)
                lt_region = all_text[max(0, idx - 80):idx + 400]
                break
        if lt_region:
            lt = 0.2
            if any(k in lt_region.lower() for k in ["\u62a5\u8868", "report"]) and any(k in lt_region.lower() for k in ["\u6027\u80fd", "performance"]): lt += 0.3
            if any(k in lt_region.lower() for k in ["\u7eed\u7ea6", "renewal"]): lt += 0.2
            if any(k in lt_region.lower() for k in ["\u6df1\u5ea6", "in-depth", "retention"]): lt += 0.3
            det_score += 0.08 * min(lt, 1.0)

        # Todo creation
        todo_creates = [d for d in dispatches if d.tool_name == "todo_create_task" and d.response_status < 400]
        det_score += 0.08 * (1.0 if todo_creates else 0.0)

        # Department coordination
        depts = ["\u7814\u53d1", "R&D", "engineering", "\u5ba2\u6237\u6210\u529f", "customer success", "\u9500\u552e", "sales", "\u5e02\u573a", "marketing"]
        det_score += 0.07 * min(sum(1 for d in depts if d.lower() in lower) / 2, 1.0)

        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            campaign = judge.evaluate(task.prompt.text, conversation, actions, self._CAMPAIGN_RUBRIC).score
        else:
            campaign = self._fallback(lower, all_text)

        completion = tool_penalty * (0.55 * det_score + 0.45 * campaign)
        scores.completion = min(round(completion, 2), 1.0)
        scores.robustness = self.compute_robustness(dispatches)
        scores.efficiency_turns = len([m for m in messages if m.message.role == "assistant"])
        return scores

    def _fallback(self, lower, text):
        """_fallback_: dev-only keyword scoring for campaign plan."""
        score = 0.0
        acts = ["\u90ae\u4ef6\u8425\u9500", "\u7814\u8ba8\u4f1a", "email marketing", "seminar"]
        score += 0.20 * min(sum(1 for a in acts if a in lower or a in text) / 2, 1.0)
        if "VIP" in text: score += 0.15
        if any(k in lower for k in ["\u7eed\u7ea6", "renewal"]): score += 0.10
        if any(k in lower for k in ["\u661f\u8fb0", "xingchen"]): score += 0.10
        if any(k in lower for k in ["\u9f99\u817e", "longteng"]): score += 0.10
        depts = ["R&D", "sales", "marketing", "\u7814\u53d1", "\u9500\u552e"]
        score += 0.20 * min(sum(1 for d in depts if d.lower() in lower) / 2, 1.0)
        if "|" in text or "##" in text: score += 0.15
        return min(score, 1.0)
