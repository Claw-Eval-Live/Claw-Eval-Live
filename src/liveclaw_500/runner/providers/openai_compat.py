"""OpenAI-compatible provider (GPT-4o, vLLM, MiMo, etc.)."""

from __future__ import annotations

import json
import os
import random
import re
import time
from copy import deepcopy
from uuid import uuid4
from typing import Any

from openai import OpenAI

from ...models.content import AudioBlock, ImageBlock, TextBlock, ToolUseBlock, VideoBlock
from ...models.message import Message
from ...models.tool import ToolSpec
from ...models.trace import TokenUsage


def _normalize_tool_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Normalize JSON Schema for stricter OpenAI-compatible gateways.

    Some providers reject object schemas when ``required`` is omitted,
    even though standard JSON Schema allows that. Backfill ``required: []``
    recursively so zero-arg tools remain valid.
    """
    normalized = deepcopy(schema)

    def _walk(node: Any) -> Any:
        if isinstance(node, dict):
            if node.get("type") == "object":
                node.setdefault("properties", {})
                if node.get("required") is None:
                    node["required"] = []

            for key, value in list(node.items()):
                if key == "properties" and isinstance(value, dict):
                    node[key] = {name: _walk(child) for name, child in value.items()}
                elif key == "items":
                    node[key] = _walk(value)
                elif key in {"anyOf", "oneOf", "allOf"} and isinstance(value, list):
                    node[key] = [_walk(child) for child in value]
                elif isinstance(value, dict):
                    node[key] = _walk(value)
                elif isinstance(value, list):
                    node[key] = [_walk(child) for child in value]
        elif isinstance(node, list):
            return [_walk(child) for child in node]
        return node

    return _walk(normalized)


def _tool_spec_to_openai(spec: ToolSpec) -> dict[str, Any]:
    """Convert our ToolSpec to OpenAI function calling format."""
    return {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": spec.description,
            "parameters": _normalize_tool_schema(spec.input_schema),
        },
    }


def _audio_format_from_mime(mime_type: str) -> str:
    """Map mime type to OpenAI input_audio format."""
    mime = mime_type.lower()
    if mime in {"audio/wav", "audio/x-wav", "audio/wave"}:
        return "wav"
    if mime in {"audio/mp3", "audio/mpeg"}:
        return "mp3"
    return "wav"


_TOOL_CALL_BLOCK_RE = re.compile(
    r"<tool_call>\s*(.*?)\s*</tool_call>",
    flags=re.IGNORECASE | re.DOTALL,
)
_FUNCTION_RE = re.compile(
    r"<function\s*=\s*([a-zA-Z0-9_:-]+)\s*>",
    flags=re.IGNORECASE,
)
_PARAM_RE = re.compile(
    r"<parameter\s*=\s*([a-zA-Z0-9_:-]+)\s*>(.*?)</parameter>",
    flags=re.IGNORECASE | re.DOTALL,
)


def _coerce_param_value(raw: str) -> Any:
    value = raw.strip()
    if not value:
        return ""

    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered == "null":
        return None

    if re.fullmatch(r"-?\d+", value):
        try:
            return int(value)
        except Exception:
            return value
    if re.fullmatch(r"-?\d+\.\d+", value):
        try:
            return float(value)
        except Exception:
            return value

    if (value.startswith("{") and value.endswith("}")) or (
        value.startswith("[") and value.endswith("]")
    ):
        try:
            return json.loads(value)
        except Exception:
            return value

    return value


def _extract_text_tool_calls(text: str) -> tuple[str, list[ToolUseBlock]]:
    """Parse pseudo tool-call markup from text as fallback.

    This supports common non-native formats like:
    <tool_call>
      <function=todo_list_tasks>
      <parameter=status>all</parameter>
    </tool_call>
    """
    tool_uses: list[ToolUseBlock] = []
    if "<tool_call" not in text.lower():
        return text, tool_uses

    matches = list(_TOOL_CALL_BLOCK_RE.finditer(text))
    if not matches:
        return text, tool_uses

    for m in matches:
        block = m.group(1)
        fn = _FUNCTION_RE.search(block)
        if not fn:
            continue

        tool_name = fn.group(1).strip()
        parsed_input: dict[str, Any] = {}
        for p in _PARAM_RE.finditer(block):
            key = p.group(1).strip()
            raw_val = p.group(2)
            parsed_input[key] = _coerce_param_value(raw_val)

        tool_uses.append(
            ToolUseBlock(
                id=f"fallback_{uuid4().hex[:12]}",
                name=tool_name,
                input=parsed_input,
            )
        )

    if not tool_uses:
        return text, tool_uses

    cleaned_text = _TOOL_CALL_BLOCK_RE.sub("", text).strip()
    return cleaned_text, tool_uses


def _blocks_to_openai_content(msg: Message) -> str | list[dict[str, Any]]:
    """Convert text/image/audio/video blocks into OpenAI content parts.

    Returns plain string for text-only content to preserve compatibility.
    """
    text_parts = [b.text for b in msg.content if b.type == "text"]
    has_non_text = any(b.type in {"image", "audio", "video"} for b in msg.content)
    if not has_non_text:
        return "\n".join(text_parts) if text_parts else ""

    parts: list[dict[str, Any]] = []
    for block in msg.content:
        if block.type == "text":
            parts.append({"type": "text", "text": block.text})
        elif block.type == "image":
            block = block if isinstance(block, ImageBlock) else ImageBlock.model_validate(block)
            parts.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:{block.mime_type};base64,{block.data}",
                },
            })
        elif block.type == "audio":
            block = block if isinstance(block, AudioBlock) else AudioBlock.model_validate(block)
            parts.append({
                "type": "input_audio",
                "input_audio": {
                    "data": block.data,
                    "format": _audio_format_from_mime(block.mime_type),
                },
            })
        elif block.type == "video":
            block = block if isinstance(block, VideoBlock) else VideoBlock.model_validate(block)
            # Most OpenAI-compatible chat endpoints do not universally support
            # native video parts yet; convert to explicit text marker.
            parts.append({
                "type": "text",
                "text": (
                    f"[video attached: {block.source_path or 'inline'} "
                    f"({block.mime_type}, base64_bytes={len(block.data) * 3 // 4})]"
                ),
            })
    return parts


def _message_to_openai(msg: Message) -> dict[str, Any] | list[dict[str, Any]]:
    """Convert our Message to OpenAI chat format.

    Returns a single dict for simple messages, or a list of dicts
    when tool_result blocks need to be sent as separate tool messages.
    """
    # Tool result messages need special handling
    tool_results = [b for b in msg.content if b.type == "tool_result"]
    if tool_results:
        results = []
        for tr in tool_results:
            content_text = "\n".join(t.text for t in tr.content) if tr.content else ""
            results.append({
                "role": "tool",
                "tool_call_id": tr.tool_use_id,
                "content": content_text,
            })
        return results

    # Assistant messages with tool_use blocks
    tool_uses = [b for b in msg.content if b.type == "tool_use"]
    if tool_uses:
        return {
            "role": "assistant",
            "content": _blocks_to_openai_content(msg),
            "tool_calls": [
                {
                    "id": tu.id,
                    "type": "function",
                    "function": {
                        "name": tu.name,
                        "arguments": json.dumps(tu.input),
                    },
                    **({"extra_content": tu.extra_content} if getattr(tu, "extra_content", None) else {}),
                }
                for tu in tool_uses
            ],
        }

    # Simple text message
    return {
        "role": msg.role,
        "content": _blocks_to_openai_content(msg),
    }


class OpenAICompatProvider:
    """Calls any OpenAI-compatible chat completions endpoint."""

    def __init__(
        self,
        model_id: str = "gpt-4o",
        api_key: str | None = None,
        base_url: str | None = None,
        *,
        default_headers: dict[str, str] | None = None,
        default_query: dict[str, Any] | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> None:
        self.model_id = model_id
        self._extra_body = dict(extra_body or {})
        resolved_key = api_key or os.environ.get("OPENAI_API_KEY") or "unused"
        timeout_s = float(os.environ.get("CLAW_EVAL_MODEL_TIMEOUT_S", "180"))
        self.client = OpenAI(
            api_key=resolved_key,
            base_url=base_url,
            default_headers=default_headers or None,
            default_query=default_query or None,
            timeout=timeout_s,
        )

    def chat(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
    ) -> tuple[Message, TokenUsage]:
        """Send messages to the model and return parsed response."""
        has_multimodal_input = any(
            b.type in {"image", "audio", "video"}
            for m in messages
            for b in m.content
        )

        # Build OpenAI messages list
        oai_messages: list[dict[str, Any]] = []
        for msg in messages:
            converted = _message_to_openai(msg)
            if isinstance(converted, list):
                oai_messages.extend(converted)
            else:
                oai_messages.append(converted)

        kwargs: dict[str, Any] = {
            "model": self.model_id,
            "messages": oai_messages,
        }
        request_extra_body = dict(self._extra_body)
        kwargs["temperature"] = request_extra_body.pop("temperature", 0.0)
        if tools:
            kwargs["tools"] = [_tool_spec_to_openai(t) for t in tools]
        if request_extra_body:
            kwargs["extra_body"] = request_extra_body

        max_retries = 5
        last_exc: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                response = self.client.chat.completions.create(**kwargs)
                # Guard against empty choices (provider anomaly)
                if not response.choices:
                    raise RuntimeError("Model returned empty choices (choices=None or [])")
                break
            except Exception as exc:
                last_exc = exc
                # Check if retryable (rate-limit or server error)
                status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
                retryable = (
                    status in (429, 500, 502, 503, 529)
                    or "timeout" in str(exc).lower()
                    or "empty choices" in str(exc).lower()
                )
                if not retryable or attempt == max_retries:
                    if has_multimodal_input:
                        raise RuntimeError(
                            "Model endpoint rejected multimodal input. "
                            "Check provider support for image/audio/video message parts, "
                            "or set media.strict_mode=false to allow skips."
                        ) from exc
                    raise
                # Exponential backoff with jitter: 2, 4, 8, 16, 32 seconds base
                delay = min(2 ** (attempt + 1), 64) + random.uniform(0, 1)
                print(f"[retry] API error ({status or type(exc).__name__}), "
                      f"attempt {attempt + 1}/{max_retries}, waiting {delay:.1f}s ...")
                time.sleep(delay)
        choice = response.choices[0]

        # Parse response into our content blocks
        content_blocks = []
        if choice.message.content:
            if isinstance(choice.message.content, str):
                content_blocks.append(TextBlock(text=choice.message.content))
            elif isinstance(choice.message.content, list):
                text_chunks = []
                for part in choice.message.content:
                    if isinstance(part, dict):
                        part_type = part.get("type")
                        if part_type == "text":
                            text_val = part.get("text")
                            if isinstance(text_val, str):
                                text_chunks.append(text_val)
                        continue

                    part_type = getattr(part, "type", None)
                    if part_type == "text":
                        text_val = getattr(part, "text", None)
                        if isinstance(text_val, str):
                            text_chunks.append(text_val)
                if text_chunks:
                    content_blocks.append(TextBlock(text="\n".join(text_chunks)))

        parsed_tool_uses: list[ToolUseBlock] = []
        if choice.message.tool_calls:
            for tc in choice.message.tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    args = {}
                tool_use_id = tc.id or f"call_{uuid4().hex[:12]}"
                # Capture extra_content (e.g. Gemini thought_signature)
                extra = None
                if hasattr(tc, "extra_content") and tc.extra_content:
                    extra = tc.extra_content
                parsed_tool_uses.append(ToolUseBlock(
                    id=tool_use_id,
                    name=tc.function.name,
                    input=args,
                    extra_content=extra,
                ))
            content_blocks.extend(parsed_tool_uses)
        else:
            # Fallback for providers that return pseudo tool markup in text
            # instead of native tool_calls payloads.
            text_blocks = [b for b in content_blocks if b.type == "text"]
            if text_blocks:
                merged_text = "\n".join(tb.text for tb in text_blocks)
                cleaned_text, fallback_tools = _extract_text_tool_calls(merged_text)
                if fallback_tools:
                    content_blocks = []
                    if cleaned_text:
                        content_blocks.append(TextBlock(text=cleaned_text))
                    content_blocks.extend(fallback_tools)

        usage = TokenUsage()
        if response.usage:
            prompt_tokens = response.usage.prompt_tokens or 0
            completion_tokens = response.usage.completion_tokens or 0
            usage = TokenUsage(
                input_tokens=prompt_tokens,
                output_tokens=completion_tokens,
            )

        return Message(role="assistant", content=content_blocks), usage
