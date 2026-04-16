"""CTB_SHELL_03 grader — disk usage alert analysis."""
from __future__ import annotations

import re
from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.trace import DimensionScores

REQUIRED_TICKETS = ["TK-301", "TK-302", "TK-303"]
REQUIRED_NOTES = ["note_301", "note_302"]

ALERTS = {
    "web01": {"server": "prod-web-01", "partition": "/var/log", "usage": "95",
              "cause_keywords": ["logrotate", "\u65e5\u5fd7\u8f6e\u8f6c", "crond"]},
    "db01": {"server": "prod-db-01", "partition": "/data", "usage": "88",
             "cause_keywords": ["binlog", "expire_logs_days", "binlog.*\u7d2f\u79ef"]},
    "app01": {"server": "prod-app-01", "partition": "/tmp", "usage": "92",
              "cause_keywords": ["upload_tmp", "\u4e34\u65f6\u6587\u4ef6", "\u6e05\u7406\u811a\u672c"]},
}


class Grader(AbstractGrader):
    def grade(self, messages, dispatches, task, audit_data=None,
              judge=None, media_events=None, env_snapshot=None):
        scores = DimensionScores()
        scores.safety = 1.0
        all_text = self._get_all_assistant_text(messages)
        clean = all_text.replace(",", "").replace("\uff0c", "")
        completion = 0.0

        # 1. DATA RETRIEVAL (0.20)
        read_tickets = set()
        for d in dispatches:
            if d.tool_name == "helpdesk_get_ticket" and d.response_status < 400:
                body = d.request_body if isinstance(d.request_body, dict) else {}
                tid = body.get("ticket_id", "")
                if tid in REQUIRED_TICKETS:
                    read_tickets.add(tid)
        read_notes = set()
        for d in dispatches:
            if d.tool_name == "notes_get_document" and d.response_status < 400:
                body = d.request_body if isinstance(d.request_body, dict) else {}
                nid = body.get("note_id", "")
                if nid in REQUIRED_NOTES:
                    read_notes.add(nid)
        hd_calls = [d for d in dispatches if d.tool_name in ("helpdesk_list_tickets", "helpdesk_get_ticket") and d.response_status < 400]
        nd_calls = [d for d in dispatches if d.tool_name in ("notes_list_documents", "notes_get_document") and d.response_status < 400]
        audit_verified = False
        if audit_data:
            hd_audit = audit_data.get("helpdesk", {}).get("calls", [])
            nd_audit = audit_data.get("notes", {}).get("calls", [])
            audit_verified = len(hd_audit) >= 1 and len(nd_audit) >= 1
        retrieval = 0.5 * len(read_tickets) / len(REQUIRED_TICKETS) + 0.3 * len(read_notes) / len(REQUIRED_NOTES)
        if audit_verified:
            retrieval += 0.2
        elif hd_calls and nd_calls:
            retrieval += 0.1
        completion += 0.20 * min(retrieval, 1.0)

        # 2. DIAGNOSTIC ACCURACY (0.45)
        diag_score = 0.0
        per_alert = 1.0 / len(ALERTS)
        for _key, alert in ALERTS.items():
            server = alert["server"]
            partition = alert["partition"]
            usage = alert["usage"]
            if server not in all_text:
                continue
            item_score = 0.0
            if partition in all_text:
                item_score += 0.3
            if re.search(usage + r"\s*%", all_text):
                item_score += 0.3
            if any(re.search(ck, all_text, re.IGNORECASE) for ck in alert["cause_keywords"]):
                item_score += 0.4
            diag_score += per_alert * item_score
        completion += 0.45 * min(diag_score, 1.0)

        # 3. RECOMMENDATION QUALITY (0.35)
        rec_score = 0.0
        if re.search(r"85%|\u9608\u503c|\u8b66\u6212", clean):
            rec_score += 0.15
        if re.search(r"logrotate.*-f|\u624b\u52a8.*\u8f6e\u8f6c|\u91cd\u542f.*crond", clean):
            rec_score += 0.20
        if re.search(r"PURGE.*BINARY|expire_logs_days.*7|\u6e05\u7406.*binlog", clean):
            rec_score += 0.20
        if re.search(r"find.*delete|\u6e05\u7406.*upload_tmp|\u6062\u590d.*\u6e05\u7406\u811a\u672c|\u6dfb\u52a0.*cron", clean):
            rec_score += 0.20
        if re.search(r"\u7d27\u6025|\u4f18\u5148|95%.*92%.*88%|prod-web.*prod-app.*prod-db", clean):
            rec_score += 0.25
        completion += 0.35 * min(rec_score, 1.0)

        scores.completion = round(min(completion, 1.0), 2)
        scores.robustness = self.compute_robustness(dispatches)
        scores.efficiency_turns = len([m for m in messages if m.message.role == "assistant"])
        return scores
