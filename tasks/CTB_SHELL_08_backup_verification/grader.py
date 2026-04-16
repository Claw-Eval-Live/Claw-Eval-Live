"""CTB_SHELL_08 grader -- Backup Verification.

Ground truth source: task.yaml reference_solution + fixtures.

v2.2: Claw-Eval hybrid mode (backup verification report).
- Deterministic 35%: tool gate, ticket/note coverage, backup issue identification
- Judge 65%: diagnostic accuracy (35%), remediation quality (30%)
- Fallback: English-first keyword scoring when judge unavailable

Ground truth (3 tickets + 2 notes):
  TK-801 MySQL backup: mysqldump Error 28 -- disk full, /data/backup only 5GB free,
         backup ~120GB, old backups consuming 400GB, RPO 24h, last success 2026-03-23
  TK-802 File backup: rsync timeout (code 30) on /data/uploads, 15GB missing of 80GB,
         network issue during backup window
  TK-803 Remote backup: rclone sync timeout, 500GB data vs 100Mbps link (~50% utilization),
         only ~135GB transferred in 6h window, 72h lag, RPO 48h breached
  note_801: backup strategy document (mysqldump, rsync, rclone config)
  note_802: backup recovery process (MySQL/file/remote recovery procedures)
"""

from __future__ import annotations

import re
from typing import Any

from liveclaw_500.graders.base import AbstractGrader
from liveclaw_500.models.task import TaskDefinition
from liveclaw_500.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage

EXPECTED_TICKETS = {"TK-801", "TK-802", "TK-803"}
EXPECTED_NOTES = {"note_801", "note_802"}


class Grader(AbstractGrader):
    """Grade backup verification report."""

    @staticmethod
    def _has_bounded(text: str, num: str) -> bool:
        return bool(re.search(rf'(?<!\d){re.escape(num)}(?!\d)', text))

    # ── Judge rubrics ──────────────────────────────────────────────

    _DIAGNOSTIC_RUBRIC = """\
Evaluate the accuracy of backup failure diagnosis across all three backup tasks (0.0-1.0).

## Ground Truth
1. MySQL backup (TK-801): mysqldump failed with Error 28 -- No space left on device.
   /data/backup has only 5GB free; backup needs ~120GB. Old backups consuming 400GB
   were never cleaned. RPO is 24 hours. Last successful backup was 2026-03-23.
   Root cause: disk space exhaustion due to missing cleanup of old backups.

2. File backup (TK-802): rsync failed with timeout error (code 30) while syncing
   /data/uploads directory. Only 65GB of 80GB transferred (~15GB missing).
   Network issues during the 3:00-4:00 AM backup window caused the interruption.
   Root cause: rsync timeout / network instability during backup window.

3. Remote/offsite backup (TK-803): rclone sync timed out. 500GB of data needs to
   transfer over a 100Mbps link with only ~50% utilization (~50Mbps effective).
   6-hour window only transfers ~135GB. Backup is 72 hours behind, breaching
   the 48-hour RPO target.
   Root cause: insufficient bandwidth for the data volume.

## Scoring tiers
- 0.9-1.0: All 3 backup failures correctly diagnosed with root causes, specific error details (Error 28, code 30, bandwidth numbers), and RPO/data-loss risk assessed
- 0.7-0.8: All 3 failures identified with mostly correct root causes; some specifics
- 0.5-0.6: 2-3 failures identified; partial root cause analysis
- 0.3-0.4: 1-2 failures with minimal analysis
- 0.0-0.2: No meaningful diagnostic analysis
"""

    _REMEDIATION_RUBRIC = """\
Evaluate the quality of remediation recommendations for the backup issues (0.0-1.0).

## Expected Recommendations
1. MySQL: Clean up old backups / implement retention policy; increase /data/backup space;
   run immediate manual backup after cleanup; fix the cleanup cron script
2. File backup: Fix rsync timeout / increase timeout setting; investigate network issues
   during backup window; fix rsync exclusion rules to cover /data/uploads
3. Remote backup: Upgrade bandwidth (e.g., to 1Gbps); optimize rclone transfer settings;
   consider incremental/differential backup to reduce transfer volume;
   enable AOF or secondary backup mechanism

## Scoring tiers
- 0.9-1.0: Concrete, actionable remediation for all 3 issues; addresses both immediate fix and prevention; mentions specific actions (cleanup script, bandwidth upgrade, timeout config)
- 0.7-0.8: Remediation for all 3 issues; mostly actionable
- 0.5-0.6: Remediation for 2+ issues; some actionable items
- 0.3-0.4: Generic suggestions; 1-2 issues addressed
- 0.0-0.2: No meaningful recommendations
"""

    # ── Main grading ──────────────────────────────────────────────

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
        clean = all_text.replace(",", "").replace("\uff0c", "")

        # 1. Tool gate
        tool_penalty = self._tool_gate(dispatches)

        # 2. Deterministic checks (35%)
        det_score = 0.0
        det_score += 0.30 * self._score_data_retrieval(dispatches)
        det_score += 0.30 * self._score_ticket_coverage(lower)
        det_score += 0.40 * self._score_backup_issues(lower, clean)

        # 3. Judge scoring (65%)
        if judge:
            conversation = self.format_conversation(messages)
            actions = self.summarize_actions(audit_data)
            diag_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._DIAGNOSTIC_RUBRIC
            ).score
            remed_score = judge.evaluate(
                task.prompt.text, conversation, actions, self._REMEDIATION_RUBRIC
            ).score
        else:
            diag_score = self._fallback_diagnostic(lower, clean)
            remed_score = self._fallback_remediation(lower)

        # 4. Combine
        completion = tool_penalty * (
            0.35 * det_score
            + 0.35 * diag_score
            + 0.30 * remed_score
        )

        scores.completion = min(round(completion, 2), 1.0)
        scores.robustness = self.compute_robustness(dispatches)
        scores.efficiency_turns = len(
            [m for m in messages if m.message.role == "assistant"]
        )
        return scores

    # ── Deterministic helpers ──────────────────────────────────────

    def _tool_gate(self, dispatches: list[ToolDispatch]) -> float:
        """Require both helpdesk and notes API usage."""
        hd = any(
            d.tool_name in ("helpdesk_list_tickets", "helpdesk_get_ticket")
            and d.response_status < 400
            for d in dispatches
        )
        nd = any(
            d.tool_name in ("notes_list_documents", "notes_get_document",
                            "notes_list", "notes_get")
            and d.response_status < 400
            for d in dispatches
        )
        if not hd and not nd:
            return 0.2
        if not hd or not nd:
            return 0.5
        return 1.0

    def _score_data_retrieval(self, dispatches: list[ToolDispatch]) -> float:
        """Score breadth of data retrieval -- ticket IDs and note IDs fetched."""
        read_ticket_ids: set[str] = set()
        read_note_ids: set[str] = set()
        for d in dispatches:
            if d.response_status >= 400:
                continue
            body = d.request_body if isinstance(d.request_body, dict) else {}
            if d.tool_name == "helpdesk_get_ticket":
                tid = body.get("ticket_id", "")
                if tid:
                    read_ticket_ids.add(tid)
            elif d.tool_name in ("notes_get_document", "notes_get"):
                nid = body.get("note_id", "")
                if nid:
                    read_note_ids.add(nid)

        ticket_cov = len(read_ticket_ids & EXPECTED_TICKETS) / len(EXPECTED_TICKETS)
        note_cov = len(read_note_ids & EXPECTED_NOTES) / len(EXPECTED_NOTES)
        return 0.60 * ticket_cov + 0.40 * note_cov

    def _score_ticket_coverage(self, lower: str) -> float:
        """Check whether all 3 ticket IDs are mentioned in the output."""
        found = 0
        for tid in EXPECTED_TICKETS:
            if tid.lower() in lower:
                found += 1
        return found / len(EXPECTED_TICKETS)

    def _score_backup_issues(self, lower: str, clean: str) -> float:
        """Deterministic check for key data points from each backup issue."""
        score = 0.0

        # MySQL backup issue (weight: 1/3)
        mysql_signals = [
            any(kw in lower for kw in ["mysql", "mysqldump"]),
            any(kw in lower for kw in [
                "no space left", "disk full", "disk space",
                "error 28", "space exhausted",
                # Chinese fallback
                "\u7a7a\u95f4\u4e0d\u8db3", "\u78c1\u76d8\u6ee1",
            ]),
            any([
                self._has_bounded(clean, "120") and any(
                    kw in lower for kw in ["gb", "backup"]
                ),
                self._has_bounded(clean, "5") and any(
                    kw in lower for kw in ["gb", "free", "remaining", "left"]
                ),
                self._has_bounded(clean, "400") and any(
                    kw in lower for kw in ["gb", "old", "retain"]
                ),
            ]),
        ]
        score += (sum(1 for s in mysql_signals if s) / 3) / 3

        # File backup issue (weight: 1/3)
        file_signals = [
            any(kw in lower for kw in ["rsync", "file backup"]),
            any(kw in lower for kw in [
                "timeout", "code 30", "timed out", "interrupted",
                # Chinese fallback
                "\u8d85\u65f6", "\u4e2d\u65ad",
            ]),
            any([
                "/data/uploads" in lower,
                self._has_bounded(clean, "15") and any(
                    kw in lower for kw in ["gb", "missing", "incomplete", "gap"]
                ),
            ]),
        ]
        score += (sum(1 for s in file_signals if s) / 3) / 3

        # Remote/offsite backup issue (weight: 1/3)
        remote_signals = [
            any(kw in lower for kw in [
                "rclone", "remote backup", "offsite",
                "off-site", "disaster recovery",
                # Chinese fallback
                "\u5f02\u5730", "\u707e\u5907",
            ]),
            any(kw in lower for kw in [
                "bandwidth", "insufficient", "100mbps",
                "throughput", "transfer rate",
                # Chinese fallback
                "\u5e26\u5bbd\u4e0d\u8db3",
            ]),
            any([
                self._has_bounded(clean, "500") and any(
                    kw in lower for kw in ["gb", "data"]
                ),
                self._has_bounded(clean, "72") and any(
                    kw in lower for kw in ["hour", "behind", "lag", "h"]
                ),
                re.search(r"48\s*(?:hour|h)", lower) and any(
                    kw in lower for kw in ["rpo", "breach", "exceed", "target"]
                ),
            ]),
        ]
        score += (sum(1 for s in remote_signals if s) / 3) / 3

        return min(score, 1.0)

    # ── Fallback scorers ───────────────────────────────────────────

    def _fallback_diagnostic(self, lower: str, clean: str) -> float:
        """Keyword-based fallback when judge is unavailable."""
        score = 0.0

        # MySQL diagnosis
        if any(kw in lower for kw in ["mysql", "mysqldump"]):
            if any(kw in lower for kw in [
                "no space", "disk full", "disk space", "error 28",
                "\u7a7a\u95f4\u4e0d\u8db3",
            ]):
                score += 0.15
            if any(kw in lower for kw in [
                "old backup", "cleanup", "retention", "not cleaned",
                "\u65e7\u5907\u4efd", "\u672a\u6e05\u7406",
            ]):
                score += 0.10

        # File backup diagnosis
        if any(kw in lower for kw in ["rsync", "file backup"]):
            if any(kw in lower for kw in [
                "timeout", "code 30", "interrupt", "timed out",
                "\u8d85\u65f6", "\u4e2d\u65ad",
            ]):
                score += 0.15
            if "/data/uploads" in lower or any(kw in lower for kw in [
                "incomplete", "missing", "gap",
                "\u4e0d\u5b8c\u6574",
            ]):
                score += 0.10

        # Remote backup diagnosis
        if any(kw in lower for kw in [
            "rclone", "remote", "offsite", "off-site",
            "\u5f02\u5730", "\u707e\u5907",
        ]):
            if any(kw in lower for kw in [
                "bandwidth", "100mbps", "insufficient", "throughput",
                "\u5e26\u5bbd\u4e0d\u8db3",
            ]):
                score += 0.15
            if any([
                self._has_bounded(clean, "72") and any(
                    kw in lower for kw in ["hour", "lag", "behind"]
                ),
                re.search(r"48\s*(?:hour|h)", lower) and "rpo" in lower,
            ]):
                score += 0.10

        # Bonus for RPO / data-loss awareness across issues
        if re.search(r"\brpo\b", lower):
            score += 0.05

        return min(score, 1.0)

    def _fallback_remediation(self, lower: str) -> float:
        """Keyword-based fallback for remediation quality."""
        score = 0.0

        # MySQL remediation: cleanup old backups / expand storage
        if any(kw in lower for kw in [
            "clean up", "cleanup", "delete old", "retention policy",
            "purge", "remove expired",
            # Chinese fallback
            "\u6e05\u7406", "\u5220\u9664\u8fc7\u671f",
        ]):
            score += 0.20

        # File backup remediation: fix rsync / network
        if any(kw in lower for kw in [
            "increase timeout", "fix rsync", "network stability",
            "fix exclusion", "rsync config",
            "adjust timeout",
            # Chinese fallback
            "\u4fee\u590d\u7f51\u7edc", "\u6392\u67e5\u4e22\u5305",
        ]):
            score += 0.20

        # Remote backup remediation: upgrade bandwidth
        if any(kw in lower for kw in [
            "upgrade bandwidth", "1gbps", "increase bandwidth",
            "expand", "dedicated line",
            # Chinese fallback
            "\u5347\u7ea7\u5e26\u5bbd", "\u6269\u5bb9\u4e13\u7ebf",
        ]):
            score += 0.20

        # General: immediate backup / manual backup
        if any(kw in lower for kw in [
            "immediate backup", "manual backup", "run backup now",
            "supplemental backup", "ad-hoc backup",
            # Chinese fallback
            "\u7acb\u5373\u5907\u4efd", "\u624b\u52a8\u5907\u4efd",
        ]):
            score += 0.15

        # General: actionable language
        if any(kw in lower for kw in [
            "recommend", "action", "remediat", "resolution",
            "mitigation", "next step",
        ]):
            score += 0.10

        return min(score, 1.0)
