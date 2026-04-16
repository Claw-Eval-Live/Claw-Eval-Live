"""CTB_SHELL_24 grader v2 — storage tiering review.

Upgrade: verify agent actions (dispatches + audit) AND output accuracy.

Ground truth (from fixtures):
  - TK-1001: Hot SSD 91% (10TB), 2.8TB expired data, auto-tier-migrate failed (NFS stale)
  - TK-1002: Warm HDD latency 180ms (base<=20ms), IOPS 300 (base 1200), RAID-6 disk-03 degraded + disk-12 pending=128
  - TK-1003: Cold storage cost 28500 (budget 21000, +35%), 42TB duplicate, 28TB expired, lifecycle JSON error
  - TK-1004: Migration bandwidth 800Mbps (should limit 300), DB replication lag 50ms/15s
  - note_1001: Storage tiering architecture
  - note_1002: March storage change log
"""
from __future__ import annotations

import re
from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.trace import DimensionScores


REQUIRED_TICKETS = ["TK-1001", "TK-1002", "TK-1003", "TK-1004"]
REQUIRED_NOTES = ["note_1001", "note_1002"]

STORAGE_ISSUES = {
    "hot_ssd": {
        "anchor": ["热数据", "hot data", "hot tier"],
        "anchor_alt": ["SSD", "storage-hot", "hot.*tier"],
        "detail_keywords": ["91%", "9.1.*TB", "2.8.*TB"],
        "cause_keywords": ["NFS.*挂载.*失败", "NFS.*mount.*fail", "NFS.*mount.*error", "mount.*stale", "auto-tier-migrate",
                           "迁移.*失败", "migration.*fail", "migration.*error", "过期.*未迁移", "expired.*not.*migrat", "expired.*data.*remain"],
    },
    "warm_hdd": {
        "anchor": ["温数据", "warm data", "warm tier"],
        "anchor_alt": ["HDD", "storage-warm", "warm.*tier", "RAID"],
        "detail_keywords": ["180.*ms", "延迟.*180", "latency.*180", "delay.*180", "IOPS.*300"],
        "cause_keywords": ["disk-03.*degraded", "degraded", "RAID-6", "disk-12", "pending.*sector.*128", "SMART"],
    },
    "cold_cost": {
        "anchor": ["冷数据", "cold data", "cold tier"],
        "anchor_alt": ["对象存储", "object storage", "MinIO", "cold.*tier"],
        "detail_keywords": ["28500", "超预算.*35%", "over.*budget.*35%", "exceed.*budget.*35%", "成本.*超", "cost.*over", "cost.*exceed", "cost.*overrun"],
        "cause_keywords": ["42.*TB.*重复", "42.*TB.*duplicat", "28.*TB.*过期", "28.*TB.*expir",
                           "JSON.*语法.*错误", "JSON.*syntax.*error", "JSON.*parse.*error", "lifecycle.*policy",
                           "策略.*未生效", "policy.*not.*effect", "policy.*fail", "policy.*broken", "policy.*inactive"],
    },
    "migration_bw": {
        "anchor": ["迁移", "migration"],
        "anchor_alt": ["带宽.*争用", "bandwidth.*contention", "bandwidth"],
        "detail_keywords": ["800.*Mbps", "带宽", "bandwidth", "throughput"],
        "cause_keywords": ["50.*ms", "主从.*lag.*15", "replication.*lag.*15", "replica.*lag.*15", "复制.*延迟", "replication.*delay", "replication.*latency",
                           "300.*Mbps.*限速", "300.*Mbps.*limit", "300.*Mbps.*throttle", "凌晨", "off.peak", "off-peak", "QoS", "时间窗口", "time.*window", "maintenance.*window"],
    },
}


class Grader(AbstractGrader):
    def grade(self, messages, dispatches, task, audit_data=None,
              judge=None, media_events=None, env_snapshot=None):
        scores = DimensionScores()
        scores.safety = 1.0
        all_text = self._get_all_assistant_text(messages)
        clean = all_text.replace(",", "").replace("，", "")
        completion = 0.0

        # ────────────────────────────────────────────────────────
        # 1. DATA RETRIEVAL (0.15)
        # ────────────────────────────────────────────────────────
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

        hd_calls = [d for d in dispatches
                    if d.tool_name in ("helpdesk_list_tickets", "helpdesk_get_ticket")
                    and d.response_status < 400]
        nd_calls = [d for d in dispatches
                    if d.tool_name in ("notes_list_documents", "notes_get_document")
                    and d.response_status < 400]

        audit_verified = False
        if audit_data:
            hd_audit = audit_data.get("helpdesk", {}).get("calls", [])
            nd_audit = audit_data.get("notes", {}).get("calls", [])
            audit_verified = len(hd_audit) >= 1 and len(nd_audit) >= 1

        retrieval = 0.5 * len(read_tickets) / len(REQUIRED_TICKETS) + \
                    0.3 * len(read_notes) / len(REQUIRED_NOTES)
        if audit_verified:
            retrieval += 0.2
        elif hd_calls and nd_calls:
            retrieval += 0.1

        completion += 0.15 * min(retrieval, 1.0)

        # ────────────────────────────────────────────────────────
        # 2. DIAGNOSTIC ACCURACY (0.50)
        # ────────────────────────────────────────────────────────
        diag_score = 0.0
        per_issue = 1.0 / len(STORAGE_ISSUES)

        for key, issue in STORAGE_ISSUES.items():
            # Check anchor (may be string or list)
            anchor_val = issue["anchor"]
            anchor_list = anchor_val if isinstance(anchor_val, list) else [anchor_val]
            anchor_found = any(re.search(re.escape(a), clean, re.IGNORECASE) for a in anchor_list)
            if not anchor_found:
                alt_val = issue["anchor_alt"]
                alt_list = alt_val if isinstance(alt_val, list) else [alt_val]
                for alt in alt_list:
                    if re.search(alt, clean, re.IGNORECASE):
                        anchor_found = True
                        break
            if not anchor_found:
                continue

            # Detail numbers
            detail_found = any(re.search(dk, clean, re.IGNORECASE) for dk in issue["detail_keywords"])

            # Root cause
            cause_found = any(re.search(ck, clean, re.IGNORECASE) for ck in issue["cause_keywords"])

            item_score = 0.0
            if anchor_found:
                item_score += 0.2
            if detail_found:
                item_score += 0.4
            if cause_found:
                item_score += 0.4

            diag_score += per_issue * item_score

        completion += 0.50 * min(diag_score, 1.0)

        # ────────────────────────────────────────────────────────
        # 3. RECOMMENDATION QUALITY (0.15)
        # ────────────────────────────────────────────────────────
        rec_score = 0.0

        fix_keywords = [
            r"更换.*磁盘|更换.*disk|replace.*disk|swap.*disk|备份.*数据|backup.*data",
            r"修复.*NFS|恢复.*迁移|重启.*nfsd|fix.*NFS|repair.*NFS|restore.*migrat|restart.*nfsd|remount.*NFS",
            r"修复.*JSON|清理.*冗余|删除.*重复|清理.*过期|fix.*JSON|repair.*JSON|clean.*redundan|remove.*duplicat|purge.*expir|delete.*expir",
            r"限速.*300|凌晨.*执行|QoS|时间窗口|throttle.*300|off.peak|off-peak|maintenance.*window|time.*window|rate.*limit",
        ]
        hits = sum(1 for k in fix_keywords if re.search(k, clean, re.IGNORECASE))
        rec_score = min(hits / 3, 1.0)

        # Risk ordering
        if re.search(r"风险.*等级|紧急|排序|优先|risk.*level|risk.*rating|urgent|critical|ranking|priority|severity|triage", clean, re.IGNORECASE):
            rec_score = min(rec_score + 0.20, 1.0)

        completion += 0.15 * min(rec_score, 1.0)

        # ── Storage metrics coverage (0.20) ──
        stor_kw = ["存储层", "storage tier", "tiering", "使用率", "utilization", "usage", "延迟", "latency",
                   "成本", "cost", "容量", "capacity", "IOPS", "throughput"]
        stor_found = sum(1 for kw in stor_kw if kw.lower() in clean.lower())
        completion += 0.20 * min(stor_found / 3, 1.0)

        scores.completion = round(min(completion, 1.0), 2)
        scores.robustness = self.compute_robustness(dispatches)
        scores.efficiency_turns = len(
            [m for m in messages if m.message.role == "assistant"]
        )
        return scores

    @staticmethod
    def _near_context(text: str, anchor: str, patterns: list[str], distance: int = 200) -> bool:
        anchor_positions = [m.start() for m in re.finditer(re.escape(anchor), text, re.IGNORECASE)]
        for pat in patterns:
            kw_positions = [m.start() for m in re.finditer(pat, text, re.IGNORECASE)]
            for ap in anchor_positions:
                for kp in kw_positions:
                    if abs(ap - kp) < distance:
                        return True
        return False
