"""Core agent execution loop: Think -> Act -> Observe -> Repeat."""

from __future__ import annotations

import json
import time
from pathlib import Path
from uuid import uuid4

from ..config import MediaConfig, ModelConfig, PromptConfig
from ..models.content import ContentBlock, TextBlock
from ..models.message import Message
from ..models.task import TaskDefinition
from ..models.trace import (
    AuditSnapshot,
    DimensionScores,
    MediaLoad,
    TokenUsage,
    TraceEnd,
    TraceMessage,
    TraceStart,
)
from ..trace.writer import TraceWriter
from .dispatcher import ToolDispatcher
from .media_loader import collect_media_references, load_media_from_ref, model_supports_modality, to_content_block
from .providers.openai_compat import OpenAICompatProvider
from .system_prompt import build_system_prompt


def _log(msg: str) -> None:
    """Print a log line and flush immediately (important for container logs)."""
    print(msg, flush=True)


def _brief(d: dict, max_len: int = 80) -> str:
    """Compact one-line summary of a dict for logging."""
    s = json.dumps(d, ensure_ascii=False)
    return s if len(s) <= max_len else s[:max_len] + "..."


def _build_initial_user_content(
    task: TaskDefinition,
    *,
    trace_id: str,
    writer: TraceWriter,
    model_cfg: ModelConfig | None,
    media_cfg: MediaConfig | None,
) -> list[ContentBlock]:
    content: list[ContentBlock] = [TextBlock(text=task.prompt.text)]
    if media_cfg is not None and not media_cfg.enabled:
        return content

    cfg = media_cfg or MediaConfig()
    model = model_cfg or ModelConfig()
    refs = collect_media_references(task.prompt.text, task.prompt.attachments)
    if not refs:
        return content

    workspace_root = Path.cwd()
    task_dir = Path(task.task_file).parent if task.task_file else None
    for idx, ref in enumerate(refs):
        ref_modality = "image"
        if ref.mime_type:
            if ref.mime_type.startswith("audio/"):
                ref_modality = "audio"
            elif ref.mime_type.startswith("video/"):
                ref_modality = "video"
            elif ref.mime_type.startswith("text/") or ref.mime_type in {"application/json", "application/xml"}:
                ref_modality = "document"
        if idx >= cfg.max_files:
            writer.write_event(MediaLoad(
                trace_id=trace_id,
                modality=ref_modality,  # type: ignore[arg-type]
                source_path=ref.raw_path,
                mime_type=ref.mime_type or "",
                size_bytes=0,
                sha256="",
                status="skipped",
                note=f"exceeds max_files={cfg.max_files}",
            ))
            continue
        try:
            loaded = load_media_from_ref(
                ref,
                workspace_root=workspace_root,
                task_dir=task_dir,
                max_bytes=cfg.max_bytes_per_file,
                image_max_dimension=cfg.image_max_dimension,
            )
            if not model_supports_modality(model.input_modalities, loaded.modality):
                writer.write_event(MediaLoad(
                    trace_id=trace_id,
                    modality=loaded.modality,  # type: ignore[arg-type]
                    source_path=loaded.source_path,
                    mime_type=loaded.mime_type,
                    size_bytes=loaded.size_bytes,
                    sha256=loaded.sha256,
                    status="skipped",
                    note=f"model does not support modality: {loaded.modality}",
                ))
                if cfg.strict_mode:
                    raise ValueError(f"Model {model.model_id} does not support {loaded.modality} input")
                continue
            content.append(to_content_block(loaded))
            writer.write_event(MediaLoad(
                trace_id=trace_id,
                modality=loaded.modality,  # type: ignore[arg-type]
                source_path=loaded.source_path,
                mime_type=loaded.mime_type,
                size_bytes=loaded.size_bytes,
                sha256=loaded.sha256,
                status="loaded",
                note=ref.source,
            ))
        except Exception as exc:
            writer.write_event(MediaLoad(
                trace_id=trace_id,
                modality=ref_modality,  # type: ignore[arg-type]
                source_path=ref.raw_path,
                mime_type=ref.mime_type or "",
                size_bytes=0,
                sha256="",
                status="error",
                note=str(exc),
            ))
            if cfg.strict_mode:
                raise
    return content


def run_task(
    task: TaskDefinition,
    provider: OpenAICompatProvider,
    trace_dir: str | Path = "traces",
    *,
    sandbox_tools: bool = False,
    sandbox_url: str | None = None,
    workspace_root: Path | None = None,
    prompt_cfg: PromptConfig | None = None,
    model_cfg: ModelConfig | None = None,
    media_cfg: MediaConfig | None = None,
) -> Path:
    """Execute one trial of a task and write JSONL trace.

    Args:
        sandbox_tools: When True, sandbox tools (shell/file/browser) are
            appended to task tools and dispatched via
            :class:`SandboxToolDispatcher`.
        sandbox_url: When provided, sandbox tool calls are routed over
            HTTP to a container sandbox server at this URL (e.g.
            ``http://localhost:18080``).  When *None*, sandbox tools
            execute locally via subprocess (backward compatibility).
        workspace_root: Local temp workspace root used when sandbox tools
            run without Docker. Relative file ops resolve under this path.

    Returns the path to the trace file.
    """
    trace_id = str(uuid4())
    trace_path = Path(trace_dir) / f"{task.task_id}_{trace_id[:8]}.jsonl"

    endpoint_map = task.get_endpoint_map()
    http_dispatcher = ToolDispatcher(endpoint_map)

    sandbox_tool_list = None
    if sandbox_tools:
        from .sandbox_dispatcher import SandboxToolDispatcher
        from .sandbox_tools import SANDBOX_TOOLS

        # Deduplicate: skip sandbox tools already defined in task.yaml
        existing_names = {t.name for t in task.tools}
        sandbox_tool_list = [t for t in SANDBOX_TOOLS if t.name not in existing_names]
        task_tools = list(task.tools) + sandbox_tool_list
        dispatcher = SandboxToolDispatcher(
            http_dispatcher,
            sandbox_url=sandbox_url,
            workspace_root=workspace_root,
        )
    else:
        task_tools = task.tools
        dispatcher = http_dispatcher

    total_usage = TokenUsage()
    turn_count = 0
    wall_start = time.monotonic()
    model_time_s = 0.0
    tool_time_s = 0.0

    _log(f"[start] task={task.task_id} model={provider.model_id} trace={trace_path.name}")
    _log(f"[config] max_turns={task.environment.max_turns} timeout={task.environment.timeout_seconds}s sandbox_tools={sandbox_tools}")

    with TraceWriter(trace_path) as writer:
        # Write trace start
        writer.write_event(TraceStart(
            trace_id=trace_id,
            task_id=task.task_id,
            model=provider.model_id,
        ))

        # Build initial messages
        system_prompt = build_system_prompt(task, prompt_cfg, extra_tools=sandbox_tool_list)
        user_content = _build_initial_user_content(
            task,
            trace_id=trace_id,
            writer=writer,
            model_cfg=model_cfg,
            media_cfg=media_cfg,
        )
        messages: list[Message] = [
            Message(role="system", content=[TextBlock(text=system_prompt)]),
            Message(role="user", content=user_content),
        ]

        # Log user message
        writer.write_event(TraceMessage(
            trace_id=trace_id,
            message=messages[-1],
        ))

        # Agent loop
        while turn_count < task.environment.max_turns:
            # Check timeout
            elapsed = time.monotonic() - wall_start
            if elapsed > task.environment.timeout_seconds:
                _log(f"[timeout] {elapsed:.1f}s exceeded limit {task.environment.timeout_seconds}s")
                break

            # Call model
            _log(f"[turn {turn_count + 1}/{task.environment.max_turns}] calling model ...")
            model_t0 = time.monotonic()
            response, usage = provider.chat(messages, tools=task_tools)
            model_time_s += time.monotonic() - model_t0
            total_usage.input_tokens += usage.input_tokens
            total_usage.output_tokens += usage.output_tokens
            turn_count += 1

            # Log assistant message
            writer.write_event(TraceMessage(
                trace_id=trace_id,
                message=response,
                usage=usage,
            ))

            messages.append(response)

            # Summarize what the model returned
            text_blocks = [b for b in response.content if b.type == "text"]
            tool_uses = [b for b in response.content if b.type == "tool_use"]
            text_preview = text_blocks[0].text[:120].replace("\n", " ") if text_blocks else ""
            _log(f"[turn {turn_count}] assistant: {len(text_blocks)} text, {len(tool_uses)} tool_use | tokens: +{usage.input_tokens}in +{usage.output_tokens}out")
            if text_preview:
                _log(f"  text: {text_preview}{'...' if len(text_blocks[0].text) > 120 else ''}")

            if not tool_uses:
                _log(f"[done] no tool calls — agent finished at turn {turn_count}")
                break

            # Dispatch each tool call
            result_blocks = []
            for tu in tool_uses:
                _log(f"  -> tool: {tu.name}({_brief(tu.input)})")
                result, dispatch_event = dispatcher.dispatch(tu, trace_id)
                writer.write_event(dispatch_event)
                result_blocks.append(result)
                tool_time_s += dispatch_event.latency_ms / 1000.0
                status_tag = "OK" if not result.is_error else "ERR"
                _log(f"  <- {tu.name}: {status_tag} ({dispatch_event.latency_ms:.0f}ms)")

            # Add tool results as a user message (OpenAI convention)
            tool_msg = Message(role="user", content=result_blocks)
            messages.append(tool_msg)

            writer.write_event(TraceMessage(
                trace_id=trace_id,
                message=tool_msg,
            ))

        # Fetch audit snapshots from mock services (best-effort)
        import httpx as _httpx

        for svc in task.services:
            if svc.reset_endpoint:
                audit_url = svc.reset_endpoint.rsplit("/reset", 1)[0] + "/audit"
                try:
                    resp = _httpx.get(audit_url, timeout=5, trust_env=False)
                    writer.write_event(AuditSnapshot(
                        trace_id=trace_id,
                        service_name=svc.name,
                        audit_url=audit_url,
                        audit_data=resp.json(),
                    ))
                except Exception:
                    pass  # audit fetch is best-effort

        # Write trace end
        wall_time = time.monotonic() - wall_start
        input_tok = total_usage.input_tokens
        output_tok = total_usage.output_tokens
        total_tok = total_usage.input_tokens + total_usage.output_tokens
        other_time_s = max(0.0, wall_time - model_time_s - tool_time_s)
        writer.write_event(TraceEnd(
            trace_id=trace_id,
            total_turns=turn_count,
            model_input_tokens=input_tok,
            model_output_tokens=output_tok,
            input_tokens=input_tok,
            output_tokens=output_tok,
            total_tokens=total_tok,
            model_time_s=round(model_time_s, 2),
            tool_time_s=round(tool_time_s, 2),
            other_time_s=round(other_time_s, 2),
            wall_time_s=round(wall_time, 2),
        ))

    _log(
        f"[end] turns={turn_count} tokens={total_tok} "
        f"({input_tok}in/{output_tok}out) "
        f"time=model {model_time_s:.1f}s tool {tool_time_s:.1f}s wall {wall_time:.1f}s"
    )

    dispatcher.close()
    return trace_path
