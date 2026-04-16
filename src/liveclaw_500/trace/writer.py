"""Append-only JSONL trace writer."""

from __future__ import annotations

import json
from pathlib import Path
from typing import IO

from ..models.trace import (
    AuditSnapshot,
    DimensionScores,
    MediaLoad,
    TraceEnd,
    TraceEvent,
    TraceMessage,
    TraceStart,
    ToolDispatch,
)


class TraceWriter:
    """Writes trace events as JSONL (one JSON object per line)."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh: IO[str] | None = None

    def _ensure_open(self) -> IO[str]:
        if self._fh is None or self._fh.closed:
            self._fh = open(self.path, "a")
        return self._fh

    def write_event(self, event: TraceStart | TraceMessage | ToolDispatch | AuditSnapshot | MediaLoad | TraceEnd) -> None:
        fh = self._ensure_open()
        fh.write(event.model_dump_json() + "\n")
        fh.flush()

    def close(self) -> None:
        if self._fh and not self._fh.closed:
            self._fh.close()

    def __enter__(self) -> TraceWriter:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def patch_trace_end_scores(
    trace_path: str | Path,
    scores: DimensionScores,
    task_score: float,
    passed: bool,
) -> None:
    """Rewrite the trace_end line in a JSONL trace file with grading results.

    This is called after grading completes so that the trace file contains
    the final scores rather than the default zeros written during the agent
    loop.  All other lines are preserved unchanged.
    """
    path = Path(trace_path)
    lines = path.read_text().splitlines(keepends=True)
    patched = False
    for i, line in enumerate(lines):
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("type") == "trace_end":
            obj["scores"] = scores.model_dump()
            obj["task_score"] = task_score
            obj["passed"] = passed
            lines[i] = json.dumps(obj, ensure_ascii=False) + "\n"
            patched = True
            break
    if patched:
        path.write_text("".join(lines))
