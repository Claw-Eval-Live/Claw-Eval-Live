"""CTB_SHELL_05 grader -- SSL Certificate Expiry Check."""
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
        completion += 0.50 * min(len(hd) / 3, 1.0)
        completion += 0.50 * min(len(nd) / 2, 1.0)

        # 2. pay.example.com expired cert (0.25)
        if re.search(r"pay\.example\.com.*(?:expired|\u8fc7\u671f)", clean, re.IGNORECASE):
            completion += 0.10
        if re.search(r"EV.*(?:cert|certificate|\u8bc1\u4e66)|GlobalSign|payment.*(?:impact|affected|\u5f71\u54cd)|payment.*callback", clean, re.IGNORECASE):
            completion += 0.10
        if re.search(r"(?:expedit|express|urgent).*renew|\u52a0\u6025|\u7d27\u6025.*\u7eed\u671f|enterprise.*verif|\u4f01\u4e1a.*\u9a8c\u8bc1", clean, re.IGNORECASE):
            completion += 0.05

        # 3. api.example.com expiring (0.20)
        if re.search(r"api\.example\.com.*(?:3.day|03-31|3\u5929|3\u65e5)", clean, re.IGNORECASE):
            completion += 0.10
        if re.search(r"DigiCert|OV.*(?:cert|certificate|\u8bc1\u4e66)|API.*traffic", clean, re.IGNORECASE):
            completion += 0.10

        # 4. *.internal.com expiring (0.15)
        if re.search(r"internal\.com.*(?:wildcard|\u901a\u914d\u7b26|10.day|04-04|10\u5929|10\u65e5)", clean, re.IGNORECASE):
            completion += 0.10
        if re.search(r"Let.*Encrypt|certbot|auto.*renew|\u81ea\u52a8\u7eed\u671f", clean, re.IGNORECASE):
            completion += 0.05

        # 5. Priority ordering and responsible persons (0.20)
        if re.search(r"Wang|Li|Zhang|\u738b\u5de5|\u674e\u5de5|\u5f20\u8fd0\u7ef4", clean):
            completion += 0.10
        if re.search(r"priorit|\u4f18\u5148\u7ea7|urgent|\u7d27\u6025|sort|expired.*expir|\u5df2\u8fc7\u671f.*\u5373\u5c06", clean, re.IGNORECASE):
            completion += 0.10

        # 6. Report structure (0.10)
        if re.search(r"domain|\u57df\u540d|cert.*type|\u8bc1\u4e66\u7c7b\u578b|expir|\u8fc7\u671f|remaining|\u5269\u4f59", clean, re.IGNORECASE):
            completion += 0.05
        if re.search(r"renew|\u7eed\u671f|deploy|\u90e8\u7f72|process|\u6d41\u7a0b", clean, re.IGNORECASE):
            completion += 0.05

        scores.completion = round(min(completion, 1.0), 2)
        scores.robustness = self.compute_robustness(dispatches)
        scores.efficiency_turns = len([m for m in messages if m.message.role == "assistant"])
        return scores
