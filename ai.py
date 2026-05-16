"""AI cleaning backends. Selected via config [ai] backend.

Each backend implements AICleaner.clean(text, system_prompt) -> str.
Construction may raise (e.g. missing API key); the caller is expected to
catch that and fall back to NoOpCleaner with a visible error.
"""
import asyncio
import os


class AICleaner:
    """Interface for an AI text cleaner."""

    def clean(self, text: str, system_prompt: str) -> str:
        raise NotImplementedError

    @property
    def name(self) -> str:
        return type(self).__name__


class NoOpCleaner(AICleaner):
    """No cleaning - returns the raw transcription unchanged."""

    def clean(self, text: str, system_prompt: str) -> str:
        return text


class ClaudeSDKCleaner(AICleaner):
    """Routes through the local Claude Code subscription via claude-agent-sdk.

    Free (uses your Claude subscription) but slower (~8s subprocess startup).
    """

    def clean(self, text: str, system_prompt: str) -> str:
        return asyncio.run(self._clean_async(text, system_prompt))

    async def _clean_async(self, text: str, system_prompt: str) -> str:
        from claude_agent_sdk import (
            query, ClaudeAgentOptions, AssistantMessage, TextBlock,
        )
        options = ClaudeAgentOptions(
            system_prompt=system_prompt, max_turns=1, allowed_tools=[],
        )
        parts: list[str] = []
        async for msg in query(prompt=text, options=options):
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        parts.append(block.text)
        return "".join(parts).strip()


class AnthropicAPICleaner(AICleaner):
    """Anthropic API. Fast, pay-as-you-go. Needs an API key env var."""

    def __init__(self, api_key_env: str, model: str):
        import anthropic
        key = os.environ.get(api_key_env, "").strip()
        if not key:
            raise RuntimeError(f"环境变量 {api_key_env} 未设置")
        self._client = anthropic.Anthropic(api_key=key)
        self._model = model

    def clean(self, text: str, system_prompt: str) -> str:
        resp = self._client.messages.create(
            model=self._model,
            max_tokens=2000,
            system=system_prompt,
            messages=[{"role": "user", "content": text}],
        )
        return "".join(
            b.text for b in resp.content if getattr(b, "type", "") == "text"
        ).strip()


class OpenAICompatCleaner(AICleaner):
    """Any OpenAI-compatible endpoint (DeepSeek, Qwen, ...). Fast, cheap."""

    def __init__(self, api_key_env: str, base_url: str, model: str):
        from openai import OpenAI
        key = os.environ.get(api_key_env, "").strip()
        if not key:
            raise RuntimeError(f"环境变量 {api_key_env} 未设置")
        self._client = OpenAI(api_key=key, base_url=base_url)
        self._model = model

    def clean(self, text: str, system_prompt: str) -> str:
        resp = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text},
            ],
        )
        return (resp.choices[0].message.content or "").strip()


def make_cleaner(ai_config: dict) -> AICleaner:
    """Build the cleaner described by config [ai]. May raise on bad config."""
    backend = ai_config.get("backend", "claude-sdk")
    if backend == "none":
        return NoOpCleaner()
    if backend == "claude-sdk":
        return ClaudeSDKCleaner()
    if backend == "anthropic-api":
        a = ai_config["anthropic"]
        return AnthropicAPICleaner(a["api_key_env"], a["model"])
    if backend == "openai-compatible":
        o = ai_config["openai"]
        return OpenAICompatCleaner(o["api_key_env"], o["base_url"], o["model"])
    raise ValueError(f"未知 AI 后端: {backend}")
