"""LLM classification adapter that accepts custom prompts."""

from __future__ import annotations

import json
from typing import Any, TYPE_CHECKING

from podcast_etl.detectors import AdSegment

if TYPE_CHECKING:
    from anthropic import Anthropic


### Copied from podcast_etl.detectors.transcription to avoid coupling the eval
### harness to private functions in production code. Keep in sync if the
### production versions change shape.
def _format_transcript(segments: list[dict[str, Any]]) -> str:
    lines = []
    for seg in segments:
        start = seg.get("start", 0.0)
        end = seg.get("end", 0.0)
        text = seg.get("text", "").strip()
        lines.append(f"[{start:.1f}s - {end:.1f}s] {text}")
    return "\n".join(lines)


def _parse_llm_response(response_text: str) -> list[AdSegment]:
    text = response_text.strip()
    if text.startswith("```"):
        text = "\n".join(text.split("\n")[1:])
        text = text.rsplit("```", 1)[0]
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"LLM returned invalid JSON: {response_text!r}") from exc
    return [
        AdSegment(
            start=float(seg["start"]),
            end=float(seg["end"]),
            confidence=float(seg.get("confidence", 0.8)),
            detector="transcription",
            label=seg.get("label", ""),
        )
        for seg in data.get("segments", [])
    ]


def classify_with_prompt(
    transcript: list[dict[str, Any]],
    prompt_text: str,
    config: dict[str, Any],
    client: Anthropic | None = None,
) -> list[AdSegment]:
    """Classify transcript segments using a custom prompt.

    The prompt is sent in the `system` parameter with ephemeral cache_control
    so it can be reused across episodes for the same eval config without
    re-paying the prompt input cost. The per-episode transcript goes in the
    user message.

    Pass `client` to reuse a single `anthropic.Anthropic` instance across
    calls (avoids reconstructing connection pools on every episode).
    """
    llm_config = config.get("llm", {})
    model = llm_config.get("model", "claude-haiku-4-5-20251001")

    if client is None:
        import anthropic
        client = anthropic.Anthropic(api_key=llm_config.get("api_key") or None)

    formatted_transcript = _format_transcript(transcript)

    message = client.messages.create(
        model=model,
        max_tokens=4096,
        system=[{
            "type": "text",
            "text": prompt_text,
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[{"role": "user", "content": formatted_transcript}],
    )

    if not message.content or not hasattr(message.content[0], "text"):
        raise ValueError(f"Unexpected Anthropic response: {message.content!r}")

    return _parse_llm_response(message.content[0].text)
