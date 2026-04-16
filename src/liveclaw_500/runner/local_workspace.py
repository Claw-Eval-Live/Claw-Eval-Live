"""Local workspace helpers for sandbox-tools runs without Docker."""

from __future__ import annotations

import base64
import glob as _glob
import mimetypes
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_WORKSPACE_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_])/workspace(?=/|\b)")
_TMP_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_])/tmp(?=/|\b)")
_TEXT_MIMES = {
    "application/json",
    "application/xml",
    "application/yaml",
    "application/x-yaml",
    "application/javascript",
}
_TEXT_EXTENSIONS = {
    ".txt", ".md", ".csv", ".json", ".yaml", ".yml", ".xml",
    ".html", ".htm", ".js", ".ts", ".py", ".sh", ".bash",
    ".cfg", ".ini", ".toml", ".log", ".sql", ".r", ".rmd",
}
_UNSHARE_SCRIPT = r"""
set -e
rootfs="$1"
workspace="$2"
tmpdir="$3"
cmd="$4"

mkdir -p "$rootfs/usr" "$rootfs/bin" "$rootfs/lib" "$rootfs/workspace" "$rootfs/tmp" "$rootfs/dev" "$rootfs/proc"
mount --rbind /usr "$rootfs/usr"
mount --rbind /bin "$rootfs/bin"
mount --rbind /lib "$rootfs/lib"
if [ -d /lib64 ]; then
  mkdir -p "$rootfs/lib64"
  mount --rbind /lib64 "$rootfs/lib64"
fi
mount --bind "$workspace" "$rootfs/workspace"
mount --bind "$tmpdir" "$rootfs/tmp"
mount --rbind /dev "$rootfs/dev"
mount --rbind /proc "$rootfs/proc"

exec chroot "$rootfs" /usr/bin/env -i HOME=/workspace TMPDIR=/tmp PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin LANG=C.UTF-8 LC_ALL=C.UTF-8 /bin/bash -lc "$cmd"
"""


def _runtime_root(workspace_root: Path | None) -> Path | None:
    if workspace_root is None:
        return None
    return workspace_root.parent


def _tmp_root(workspace_root: Path | None) -> Path | None:
    runtime_root = _runtime_root(workspace_root)
    if runtime_root is None:
        return None
    return runtime_root / "tmp"


def _rootfs_root(workspace_root: Path | None) -> Path | None:
    runtime_root = _runtime_root(workspace_root)
    if runtime_root is None:
        return None
    return runtime_root / "rootfs"


def rewrite_workspace_command(command: str, workspace_root: Path | None) -> str:
    """Rewrite shell commands so /workspace points at a local temp workspace."""
    if workspace_root is None:
        return command
    rewritten = _WORKSPACE_TOKEN_RE.sub(str(workspace_root), command)
    tmp_root = _tmp_root(workspace_root)
    if tmp_root is not None:
        rewritten = _TMP_TOKEN_RE.sub(str(tmp_root), rewritten)
    return rewritten


def resolve_workspace_path(path: str, workspace_root: Path | None) -> Path:
    """Map a sandbox path to the local temp workspace."""
    p = Path(path)
    if workspace_root is None:
        return p
    if path == "/workspace":
        return workspace_root
    if path.startswith("/workspace/"):
        rel = path.removeprefix("/workspace/").lstrip("/")
        return workspace_root / rel
    tmp_root = _tmp_root(workspace_root)
    if path == "/tmp" and tmp_root is not None:
        return tmp_root
    if path.startswith("/tmp/") and tmp_root is not None:
        rel = path.removeprefix("/tmp/").lstrip("/")
        return tmp_root / rel
    if p.is_absolute():
        raise ValueError(
            f"Local sandbox only allows /workspace and /tmp paths, got: {path}"
        )
    return workspace_root / p


def display_workspace_path(path: Path, workspace_root: Path | None) -> str:
    """Convert a local temp-workspace path back to the canonical /workspace form."""
    if workspace_root is None:
        return str(path)
    try:
        rel = path.resolve().relative_to(workspace_root.resolve())
    except ValueError:
        tmp_root = _tmp_root(workspace_root)
        if tmp_root is not None:
            try:
                rel = path.resolve().relative_to(tmp_root.resolve())
                rel_str = rel.as_posix()
                return "/tmp" if rel_str in {"", "."} else f"/tmp/{rel_str}"
            except ValueError:
                return str(path)
        return str(path)
    rel_str = rel.as_posix()
    return "/workspace" if rel_str in {"", "."} else f"/workspace/{rel_str}"


def read_path_payload(path: Path, workspace_root: Path | None = None) -> dict[str, Any]:
    """Return a payload shaped like sandbox/server.py /read responses."""
    if not path.exists():
        return {"error": f"File not found: {display_workspace_path(path, workspace_root)}"}

    mime, _ = mimetypes.guess_type(str(path))
    ext = path.suffix.lower()
    is_text = (
        mime in _TEXT_MIMES
        or (mime is not None and mime.startswith("text/"))
        or (mime is None and ext in _TEXT_EXTENSIONS)
    )
    if is_text:
        return {
            "content": path.read_text(encoding="utf-8", errors="replace"),
            "mime_type": mime or "text/plain",
            "encoding": "utf-8",
        }

    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return {
        "content": data,
        "mime_type": mime or "application/octet-stream",
        "encoding": "base64",
        "size_bytes": path.stat().st_size,
    }


@dataclass
class LocalWorkspaceHandle:
    """Reference to a temp workspace used by local sandbox-tools runs."""

    root_dir: Path
    workspace_root: Path
    tmp_root: Path
    rootfs_root: Path
    run_id: str


class LocalWorkspaceRunner:
    """Manage a temp /workspace-like directory for sandbox-tools runs."""

    def __init__(
        self,
        *,
        base_dir: str | Path | None = None,
        keep: bool | None = None,
    ) -> None:
        self._base_dir = Path(base_dir) if base_dir else None
        if self._base_dir is not None:
            self._base_dir.mkdir(parents=True, exist_ok=True)
        self._keep = keep if keep is not None else os.environ.get("CLAW_EVAL_KEEP_LOCAL_WORKSPACE") == "1"

    def start_workspace(self, *, run_id: str) -> LocalWorkspaceHandle:
        """Create a temp root that behaves like a local /workspace."""
        root_dir = Path(
            tempfile.mkdtemp(
                prefix=f"claw-eval-{run_id}-",
                dir=str(self._base_dir) if self._base_dir is not None else None,
            )
        )
        workspace_root = root_dir / "workspace"
        tmp_root = root_dir / "tmp"
        rootfs_root = root_dir / "rootfs"
        workspace_root.mkdir(parents=True, exist_ok=True)
        tmp_root.mkdir(parents=True, exist_ok=True)
        rootfs_root.mkdir(parents=True, exist_ok=True)
        print(f"[local-workspace] {run_id} -> {workspace_root}")
        return LocalWorkspaceHandle(
            root_dir=root_dir,
            workspace_root=workspace_root,
            tmp_root=tmp_root,
            rootfs_root=rootfs_root,
            run_id=run_id,
        )

    def stop_workspace(self, handle: LocalWorkspaceHandle) -> None:
        """Remove the temp workspace unless explicitly kept for debugging."""
        if self._keep:
            print(f"[local-workspace] keeping {handle.workspace_root}")
            return
        shutil.rmtree(handle.root_dir, ignore_errors=True)

    @staticmethod
    def _resolve_task_root(task, task_dir: str | None) -> Path:
        if task_dir:
            return Path(task_dir)
        if getattr(task, "task_file", None):
            return Path(task.task_file).parent
        return Path.cwd()

    @staticmethod
    def _project_root_from(root: Path) -> Path:
        project_root = root.resolve().parent
        probe = root.resolve()
        while probe.parent != probe:
            if (probe / "tasks").is_dir():
                return probe
            probe = probe.parent
        return project_root

    @staticmethod
    def _copy_path(src: Path, dst: Path) -> None:
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dst)

    def _inject_file_list(
        self,
        handle: LocalWorkspaceHandle,
        file_list: list[str],
        root: Path,
        *,
        label: str,
    ) -> int:
        if not file_list:
            return 0

        project_root = self._project_root_from(root)
        injected = 0

        for rel_path in file_list:
            src = root / rel_path
            if not src.exists():
                alt = project_root / rel_path
                if alt.exists():
                    src = alt
                else:
                    print(f"[local-workspace] {label}: skipping {rel_path} (not found)")
                    continue

            dst = handle.workspace_root / rel_path
            self._copy_path(src, dst)
            injected += 1

        if injected:
            print(f"[local-workspace] {label}: {injected}/{len(file_list)} files into {handle.workspace_root}")
        return injected

    def inject_files(self, handle: LocalWorkspaceHandle, task, *, task_dir: str | None = None) -> int:
        file_list: list[str] = list(task.sandbox_files) if task.sandbox_files else []
        if not file_list:
            file_list = list(getattr(task.environment, "fixtures", []))
        if not file_list:
            return 0

        root = self._resolve_task_root(task, task_dir)
        return self._inject_file_list(handle, file_list, root, label="inject")

    def inject_grader_files(self, handle: LocalWorkspaceHandle, task, *, task_dir: str | None = None) -> int:
        file_list: list[str] = list(task.sandbox_grader_files) if getattr(task, "sandbox_grader_files", None) else []
        if not file_list:
            return 0

        root = self._resolve_task_root(task, task_dir)
        return self._inject_file_list(handle, file_list, root, label="grader-inject")


def run_local_shell_command(
    command: str,
    *,
    workspace_root: Path | None,
    timeout: int = 30,
) -> subprocess.CompletedProcess[str]:
    """Run a shell command in a rootless local sandbox when available."""
    if workspace_root is None or shutil.which("unshare") is None:
        rewritten = rewrite_workspace_command(command, workspace_root)
        return subprocess.run(
            rewritten,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=workspace_root,
        )

    rootfs_root = _rootfs_root(workspace_root)
    tmp_root = _tmp_root(workspace_root)
    assert rootfs_root is not None
    assert tmp_root is not None
    rootfs_root.mkdir(parents=True, exist_ok=True)
    tmp_root.mkdir(parents=True, exist_ok=True)

    return subprocess.run(
        [
            "unshare",
            "--user",
            "--map-root-user",
            "--mount",
            "--fork",
            "bash",
            "-lc",
            _UNSHARE_SCRIPT,
            "bash",
            str(rootfs_root),
            str(workspace_root),
            str(tmp_root),
            command,
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=workspace_root,
    )


def collect_local_env_snapshot(handle: LocalWorkspaceHandle, task) -> dict[str, Any]:
    """Collect env_snapshot data by reading files and running commands locally."""
    snapshot: dict[str, Any] = {}

    for pattern in getattr(task, "env_snapshot_files", []):
        try:
            mapped_pattern = str(resolve_workspace_path(pattern, handle.workspace_root))
            if "*" in pattern or "?" in pattern:
                for match in sorted(_glob.glob(mapped_pattern, recursive=True)):
                    resolved = Path(match)
                    key = f"file:{display_workspace_path(resolved, handle.workspace_root)}"
                    snapshot[key] = read_path_payload(resolved, handle.workspace_root)
            else:
                resolved = resolve_workspace_path(pattern, handle.workspace_root)
                snapshot[f"file:{pattern}"] = read_path_payload(resolved, handle.workspace_root)
        except Exception as exc:
            snapshot[f"file:{pattern}"] = {"error": str(exc)}
            print(f"[WARNING] local env_snapshot file failed: {pattern}: {exc}")

    for cmd in getattr(task, "env_snapshot_commands", []):
        try:
            proc = run_local_shell_command(
                cmd,
                workspace_root=handle.workspace_root,
                timeout=10,
            )
            snapshot[f"cmd:{cmd}"] = {
                "exit_code": proc.returncode,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
            }
        except subprocess.TimeoutExpired:
            snapshot[f"cmd:{cmd}"] = {
                "exit_code": -1,
                "stdout": "",
                "stderr": "Timed out after 10s",
            }
        except Exception as exc:
            snapshot[f"cmd:{cmd}"] = {"error": str(exc)}
            print(f"[WARNING] local env_snapshot command failed: {cmd}: {exc}")

    return snapshot
