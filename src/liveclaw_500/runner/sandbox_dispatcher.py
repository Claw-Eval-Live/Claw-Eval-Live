"""Sandbox-aware tool dispatcher.

Routes ``sandbox_*`` tool calls either:
  - Over HTTP to a remote sandbox container (when *sandbox_url* is provided), OR
  - Locally via subprocess/filesystem (fallback for backward compatibility).

All other tool calls are delegated to the standard HTTP ToolDispatcher.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from ..models.content import TextBlock, ToolResultBlock, ToolUseBlock
from ..models.trace import ToolDispatch
from .dispatcher import ToolDispatcher
from .local_workspace import (
    display_workspace_path,
    read_path_payload,
    resolve_workspace_path,
    run_local_shell_command,
)


class SandboxToolDispatcher:
    """Routes sandbox tools to container HTTP or local fallback; others via HTTP."""

    def __init__(
        self,
        http_dispatcher: ToolDispatcher,
        *,
        sandbox_url: str | None = None,
        workspace_root: Path | None = None,
    ) -> None:
        self._http = http_dispatcher
        self._sandbox_url = sandbox_url
        self._workspace_root = workspace_root
        self._client = None  # lazy-init httpx client for remote mode

    # ---- public interface (same signature as ToolDispatcher) ---------------

    def dispatch(
        self, tool_use: ToolUseBlock, trace_id: str
    ) -> tuple[ToolResultBlock, ToolDispatch]:
        if tool_use.name.startswith("sandbox_"):
            return self._dispatch_sandbox(tool_use, trace_id)
        return self._http.dispatch(tool_use, trace_id)

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
        self._http.close()

    # ---- sandbox routing -------------------------------------------------

    def _dispatch_sandbox(
        self, tool_use: ToolUseBlock, trace_id: str
    ) -> tuple[ToolResultBlock, ToolDispatch]:
        if self._sandbox_url:
            return self._dispatch_remote(tool_use, trace_id)
        return self._dispatch_local(tool_use, trace_id)

    # ---- remote mode: HTTP to container ----------------------------------

    _PATH_MAP = {
        "sandbox_shell_exec": "/exec",
        "sandbox_file_read": "/read",
        "sandbox_file_write": "/write",
        "sandbox_browser_screenshot": "/screenshot",
    }

    def _get_client(self):
        if self._client is None:
            import httpx
            # Sandbox HTTP is exposed on localhost from a local Docker port mapping.
            self._client = httpx.Client(timeout=60.0, trust_env=False)
        return self._client

    def _dispatch_remote(
        self, tool_use: ToolUseBlock, trace_id: str
    ) -> tuple[ToolResultBlock, ToolDispatch]:
        path = self._PATH_MAP.get(tool_use.name)
        if not path:
            return self._error_result(
                tool_use, trace_id,
                f"Unknown sandbox tool: {tool_use.name}",
                status=404,
            )

        endpoint_url = f"{self._sandbox_url}{path}"
        t0 = time.monotonic()
        try:
            client = self._get_client()
            resp = client.post(endpoint_url, json=tool_use.input)
            latency_ms = (time.monotonic() - t0) * 1000
            body = resp.json()
            is_error = resp.status_code >= 400
        except Exception as exc:
            latency_ms = (time.monotonic() - t0) * 1000
            return self._error_result(
                tool_use, trace_id, str(exc),
                status=500, latency_ms=latency_ms,
                endpoint_url=endpoint_url,
            )

        result = ToolResultBlock(
            tool_use_id=tool_use.id,
            content=[TextBlock(text=json.dumps(body, ensure_ascii=False))],
            is_error=is_error,
        )
        dispatch_event = ToolDispatch(
            trace_id=trace_id,
            tool_use_id=tool_use.id,
            tool_name=tool_use.name,
            endpoint_url=endpoint_url,
            request_body=tool_use.input,
            response_status=resp.status_code,
            response_body=body,
            latency_ms=latency_ms,
        )
        return result, dispatch_event

    # ---- local mode: subprocess/filesystem (backward compat) -------------

    _LOCAL_HANDLERS: dict[str, str] = {
        "sandbox_shell_exec": "_handle_shell_exec",
        "sandbox_file_read": "_handle_file_read",
        "sandbox_file_write": "_handle_file_write",
        "sandbox_browser_screenshot": "_handle_browser_screenshot",
    }

    def _dispatch_local(
        self, tool_use: ToolUseBlock, trace_id: str
    ) -> tuple[ToolResultBlock, ToolDispatch]:
        handler_name = self._LOCAL_HANDLERS.get(tool_use.name)
        if handler_name is None:
            return self._error_result(
                tool_use, trace_id,
                f"Unknown sandbox tool: {tool_use.name}",
                status=404,
            )

        handler = getattr(self, handler_name)
        t0 = time.monotonic()
        try:
            body = handler(tool_use.input)
            latency_ms = (time.monotonic() - t0) * 1000
            content_text = json.dumps(body, ensure_ascii=False) if isinstance(body, dict) else str(body)
            result = ToolResultBlock(
                tool_use_id=tool_use.id,
                content=[TextBlock(text=content_text)],
                is_error=False,
            )
            dispatch_event = ToolDispatch(
                trace_id=trace_id,
                tool_use_id=tool_use.id,
                tool_name=tool_use.name,
                endpoint_url=f"local://sandbox/{tool_use.name.removeprefix('sandbox_')}",
                request_body=tool_use.input,
                response_status=200,
                response_body=body,
                latency_ms=latency_ms,
            )
        except Exception as exc:
            latency_ms = (time.monotonic() - t0) * 1000
            return self._error_result(
                tool_use, trace_id, str(exc),
                status=500, latency_ms=latency_ms,
            )

        return result, dispatch_event

    # ---- local handlers (unchanged from original) ------------------------

    def _handle_shell_exec(self, inp: dict) -> dict:
        timeout = inp.get("timeout_seconds", 30)
        try:
            proc = run_local_shell_command(
                inp["command"],
                workspace_root=self._workspace_root,
                timeout=timeout,
            )
            return {
                "exit_code": proc.returncode,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
            }
        except subprocess.TimeoutExpired:
            return {
                "exit_code": -1,
                "stdout": "",
                "stderr": f"Command timed out after {timeout}s",
            }

    def _handle_file_read(self, inp: dict) -> dict:
        path = resolve_workspace_path(inp["path"], self._workspace_root)
        return read_path_payload(path, self._workspace_root)

    def _handle_file_write(self, inp: dict) -> dict:
        path = resolve_workspace_path(inp["path"], self._workspace_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(inp["content"], encoding="utf-8")
        return {
            "written": display_workspace_path(path, self._workspace_root),
            "bytes": len(inp["content"]),
        }

    @staticmethod
    def _handle_browser_screenshot(inp: dict) -> dict:
        url = inp["url"]
        try:
            from playwright.sync_api import sync_playwright  # type: ignore[import-untyped]
        except ImportError:
            return {
                "error": "playwright is not installed. "
                "Install with: pip install playwright && playwright install chromium",
                "url": url,
            }

        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1280, "height": 720})
                page.goto(url, wait_until="networkidle", timeout=30_000)
                title = page.title()
                text = page.inner_text("body")[:2000]
                browser.close()
            return {"url": url, "title": title, "body_text": text}
        except Exception as exc:
            return {"error": str(exc), "url": url}

    # ---- helpers ---------------------------------------------------------

    @staticmethod
    def _error_result(
        tool_use: ToolUseBlock,
        trace_id: str,
        error_msg: str,
        *,
        status: int = 500,
        latency_ms: float = 0.0,
        endpoint_url: str | None = None,
    ) -> tuple[ToolResultBlock, ToolDispatch]:
        result = ToolResultBlock(
            tool_use_id=tool_use.id,
            content=[TextBlock(text=f"Error: {error_msg}")],
            is_error=True,
        )
        dispatch_event = ToolDispatch(
            trace_id=trace_id,
            tool_use_id=tool_use.id,
            tool_name=tool_use.name,
            endpoint_url=endpoint_url or f"local://sandbox/{tool_use.name.removeprefix('sandbox_')}",
            request_body=tool_use.input,
            response_status=status,
            response_body={"error": error_msg},
            latency_ms=latency_ms,
        )
        return result, dispatch_event
