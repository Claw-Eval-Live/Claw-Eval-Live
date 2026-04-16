"""LLM-as-judge for subjective communication quality scoring."""

from __future__ import annotations

import json
import random
import re
import time
from typing import Any

from openai import OpenAI
from pydantic import BaseModel


class JudgeResult(BaseModel):
    score: float  # 0.0-1.0
    reasoning: str


_SYSTEM_PROMPT = """\
You are an evaluation judge for an AI assistant.
You will be given a task prompt, a conversation, a summary of actions taken, and a rubric.
Follow the rubric to score the assistant's response on a 0.0-1.0 scale.
Respond with JSON only: {"score": <float>, "reasoning": "<brief explanation>"}
"""


class LLMJudge:
    """Judge communication quality using an LLM via OpenAI-compatible API."""

    def __init__(
        self,
        model_id: str = "google/gemini-2.5-flash",
        api_key: str | None = None,
        base_url: str = "https://openrouter.ai/api/v1",
        *,
        default_headers: dict[str, str] | None = None,
        default_query: dict[str, Any] | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> None:
        self.client = OpenAI(
            api_key=api_key or "dummy",
            base_url=base_url,
            default_headers=default_headers or None,
            default_query=default_query or None,
        )
        self.model_id = model_id
        self.extra_body = dict(extra_body or {})

    def evaluate(
        self,
        task_prompt: str,
        conversation: str,
        actions_summary: str,
        rubric: str,
    ) -> JudgeResult:
        """Evaluate communication quality and return a JudgeResult."""
        user_msg = (
            f"## Task Prompt\n{task_prompt}\n\n"
            f"## Conversation\n{conversation}\n\n"
            f"## Actions Taken\n{actions_summary}\n\n"
            f"## Rubric\n{rubric}"
        )
        max_retries = 5
        last_exc: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                resp = self.client.chat.completions.create(
                    model=self.model_id,
                    messages=[
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user", "content": user_msg},
                    ],
                    temperature=0.0,
                    max_tokens=512,
                    extra_body=self.extra_body or None,
                )
                raw = resp.choices[0].message.content or "{}"
                # Strip markdown code fences if present
                raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
                raw = re.sub(r"\s*```$", "", raw.strip())
                parsed = json.loads(raw)

                # Detect empty or missing score — treat as judge failure
                if "score" not in parsed:
                    raise ValueError(f"Judge returned no 'score' field: {raw[:200]}")

                return JudgeResult(
                    score=max(0.0, min(1.0, float(parsed["score"]))),
                    reasoning=str(parsed.get("reasoning", "")),
                )
            except Exception as exc:
                last_exc = exc
                status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
                retryable = status in (429, 403, 500, 502, 503, 529) or "timeout" in str(exc).lower()
                if not retryable or attempt == max_retries:
                    raise RuntimeError(f"Judge evaluation failed: {exc}") from exc
                delay = min(2 ** (attempt + 1), 64) + random.uniform(0, 1)
                print(f"[judge-retry] ({status or type(exc).__name__}), "
                      f"attempt {attempt + 1}/{max_retries}, waiting {delay:.1f}s ...")
                time.sleep(delay)
