"""Tests for LLM classification adapter with custom prompts."""

import json
from unittest.mock import MagicMock, patch

from eval.classify import classify_with_prompt


SAMPLE_TRANSCRIPT = [
    {"start": 0.0, "end": 10.0, "text": "This episode brought to you by Acme"},
    {"start": 10.0, "end": 30.0, "text": "Welcome to the show"},
]

CUSTOM_PROMPT = "Find the ads.\n\nTranscript:\n"


class TestClassifyWithPrompt:
    def test_uses_custom_prompt(self):
        llm_response = json.dumps({"segments": [
            {"start": 0.0, "end": 10.0, "confidence": 0.9, "label": "Ad"},
        ]})
        mock_message = MagicMock()
        mock_message.content = [MagicMock(text=llm_response)]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_message

        mock_anthropic = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_client

        config = {"llm": {"model": "claude-haiku-4-5-20251001"}}

        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            result = classify_with_prompt(SAMPLE_TRANSCRIPT, CUSTOM_PROMPT, config)

        call_kwargs = mock_client.messages.create.call_args.kwargs

        # Prompt is in the system parameter as a cacheable text block.
        system_blocks = call_kwargs["system"]
        assert len(system_blocks) == 1
        assert system_blocks[0]["type"] == "text"
        assert system_blocks[0]["text"].startswith("Find the ads.")
        assert "You are an ad-segment detector" not in system_blocks[0]["text"]  # default prompt must not leak
        assert system_blocks[0]["cache_control"] == {"type": "ephemeral"}

        # Transcript is in the user message, not the system block.
        user_content = call_kwargs["messages"][0]["content"]
        assert "[0.0s - 10.0s]" in user_content
        assert "Find the ads." not in user_content  # prompt should be in system, not user

        assert len(result) == 1
        assert result[0].start == 0.0

    def test_returns_all_segments_regardless_of_confidence(self):
        """Eval workflow no longer filters by confidence — all model-flagged segments come through."""
        llm_response = json.dumps({"segments": [
            {"start": 0.0, "end": 10.0, "confidence": 0.3, "label": "Maybe ad"},
            {"start": 50.0, "end": 60.0, "confidence": 0.9, "label": "Definite ad"},
        ]})
        mock_message = MagicMock()
        mock_message.content = [MagicMock(text=llm_response)]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_message

        mock_anthropic = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_client

        config = {"llm": {"model": "claude-haiku-4-5-20251001"}}

        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            result = classify_with_prompt(SAMPLE_TRANSCRIPT, CUSTOM_PROMPT, config)

        assert len(result) == 2
        assert {s.start for s in result} == {0.0, 50.0}

    def test_uses_configured_model(self):
        llm_response = json.dumps({"segments": []})
        mock_message = MagicMock()
        mock_message.content = [MagicMock(text=llm_response)]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_message

        mock_anthropic = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_client

        config = {"llm": {"model": "claude-sonnet-4-20250514"}}

        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            classify_with_prompt(SAMPLE_TRANSCRIPT, CUSTOM_PROMPT, config)

        call_kwargs = mock_client.messages.create.call_args.kwargs
        assert call_kwargs["model"] == "claude-sonnet-4-20250514"
