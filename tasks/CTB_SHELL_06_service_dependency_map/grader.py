"""CTB_SHELL_06 grader -- Service Dependency Map."""
from __future__ import annotations

import re

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.trace import DimensionScores


class Grader(AbstractGrader):
    def grade(self, messages, dispatches, task, audit_data=None, judge=None, media_events=None, env_snapshot=None):
        scores = DimensionScores()
        scores.safety = 1.0
        all_text = self._get_all_assistant_text(messages)
        clean = all_text.replace(",", "").replace("\uff0c", "")

        completion = 0.0

        # 1. API calls (0.10)
        hd = [d for d in dispatches if d.tool_name in ("helpdesk_list_tickets", "helpdesk_get_ticket") and d.response_status < 400]
        nd = [d for d in dispatches if d.tool_name in ("notes_list", "notes_get") and d.response_status < 400]
        completion += 0.50 * min(len(hd) / 4, 1.0)
        completion += 0.50 * min(len(nd) / 2, 1.0)

        # 2. Root cause: Redis (0.20)
        if re.search(r"Redis.*(?:crash|down|failure|root.cause|\u5b95\u673a|\u6545\u969c|\u6839[\u56e0\u6e90])", clean, re.IGNORECASE):
            completion += 0.10
        if re.search(r"(?:memory|OOM).*(?:exhaust|kill|16GB|maxmemory)|\u5185\u5b58.*\u8017\u5c3d", clean, re.IGNORECASE):
            completion += 0.10

        # 3. Cascade propagation path (0.25)
        if re.search(r"Redis.*user-service|user-service.*Redis.*(?:depend|\u4f9d\u8d56)", clean, re.IGNORECASE):
            completion += 0.05
        if re.search(r"user-service.*order-service|(?:auth|authenticat).*fail.*order|\u8ba4\u8bc1.*\u5931\u8d25.*\u4e0b\u5355", clean, re.IGNORECASE):
            completion += 0.05
        if re.search(r"order-service.*payment|payment.*order.*callback|\u652f\u4ed8.*\u8ba2\u5355.*\u56de\u8c03", clean, re.IGNORECASE):
            completion += 0.05
        if re.search(r"cascad|propagat|chain|\u7ea7\u8054|\u4f20\u64ad|\u8fde\u9501|\u94fe\u8def", clean, re.IGNORECASE):
            completion += 0.05
        if re.search(r"API.*Gateway|gateway.*auth|\u7f51\u5173.*\u8ba4\u8bc1", clean, re.IGNORECASE):
            completion += 0.05

        # 4. Dependency relationships (0.20)
        if re.search(r"(?:hard|strong).*depend|\u5f3a\u4f9d\u8d56|\u4f9d\u8d56.*\u5173\u7cfb|\u4f9d\u8d56.*\u77e9\u9635|dependency.*(?:matrix|map)", clean, re.IGNORECASE):
            completion += 0.10
        if re.search(r"user-service.*Redis|payment-service.*Redis", clean):
            completion += 0.10

        # 5. Improvement suggestions (0.15)
        if re.search(r"circuit.*breaker|Hystrix|Sentinel|\u7194\u65ad", clean, re.IGNORECASE):
            completion += 0.05
        if re.search(r"degrad|fallback|\u964d\u7ea7|\u5146\u5e95", clean, re.IGNORECASE):
            completion += 0.05
        if re.search(r"local.*cache|L1.*cache|Cluster.*mode|high.*avail|\u672c\u5730\u7f13\u5b58|\u9ad8\u53ef\u7528", clean, re.IGNORECASE):
            completion += 0.05

        # 6. Report structure (0.10)
        if re.search(r"root.*cause|\u6545\u969c.*\u6839\u6e90|\u6839\u56e0", clean, re.IGNORECASE):
            completion += 0.05
        if re.search(r"affected.*service|impact.*scope|\u53d7\u5f71\u54cd.*\u670d\u52a1|\u5f71\u54cd.*\u8303\u56f4|\u670d\u52a1.*\u5217\u8868", clean, re.IGNORECASE):
            completion += 0.05

        scores.completion = round(min(completion, 1.0), 2)
        scores.robustness = self.compute_robustness(dispatches)
        scores.efficiency_turns = len([m for m in messages if m.message.role == "assistant"])
        return scores
