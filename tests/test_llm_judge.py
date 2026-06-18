import pytest

from liveclaw_500.graders.llm_judge import _parse_judge_json


def test_parse_judge_json_plain_object():
    assert _parse_judge_json('{"score": 0.75, "reasoning": "ok"}') == {
        "score": 0.75,
        "reasoning": "ok",
    }


def test_parse_judge_json_code_fence():
    assert _parse_judge_json('```json\n{"score": 1, "reasoning": "great"}\n```') == {
        "score": 1,
        "reasoning": "great",
    }


def test_parse_judge_json_embedded_after_thinking_text():
    raw = (
        "Thinking through the rubric...\n"
        'The schema is `{"score": <float>, "reasoning": "brief"}`.\n'
        'Final answer: {"score": 0.5, "reasoning": "Recovered from text."}'
    )
    assert _parse_judge_json(raw) == {
        "score": 0.5,
        "reasoning": "Recovered from text.",
    }


def test_parse_judge_json_requires_score():
    with pytest.raises(ValueError, match="parseable JSON object"):
        _parse_judge_json('{"reasoning": "missing score"}')
