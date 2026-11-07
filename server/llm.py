"""Thin LLM client. Supports the Anthropic API or any OpenAI-compatible endpoint
(LiteLLM proxy, OpenRouter, local servers). Every caller has a deterministic fallback,
so the demo runs with no key and never breaks on stage if the API is slow or down."""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from . import config

log = logging.getLogger("envoy.llm")

# Reasoning-capable models spend output tokens on hidden thinking before any
# visible text, so small ceilings return empty content. Keep a floor for headroom.
MIN_TOKENS = 1024


def parse_json(text: str) -> Any:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    start = min([i for i in (text.find("{"), text.find("[")) if i >= 0], default=-1)
    if start < 0:
        raise ValueError("no JSON found")
    return json.loads(text[start:])


class LLM:
    def __init__(self) -> None:
        self.provider = config.PROVIDER if config.MODE == "live" else "none"
        self.enabled = self.provider in ("anthropic", "openai")
        self.client = None
        if self.provider == "anthropic":
            from anthropic import AsyncAnthropic
            self.client = AsyncAnthropic(api_key=config.API_KEY)
        elif self.provider == "openai":
            import httpx
            self.client = httpx.AsyncClient(
                base_url=config.OPENAI_BASE_URL, timeout=60,
                headers={"Authorization": f"Bearer {config.OPENAI_API_KEY}"})

    @property
    def mode(self) -> str:
        return "live" if self.enabled else "mock"

    async def json(self, system: str, user: str, *, fast: bool = False, max_tokens: int = 700) -> Any | None:
        """Return parsed JSON, or None (caller falls back) on any failure."""
        if not self.enabled:
            return None
        model = config.MODEL_FAST if fast else config.MODEL_SMART
        system = system + "\nRespond with JSON only. No prose, no code fences."
        tokens = max(max_tokens, MIN_TOKENS)
        for attempt in range(2):
            try:
                if self.provider == "anthropic":
                    resp = await self.client.messages.create(
                        model=model, max_tokens=tokens, system=system,
                        messages=[{"role": "user", "content": user}])
                    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
                else:  # openai-compatible chat completions
                    r = await self.client.post("/chat/completions", json={
                        "model": model, "max_tokens": tokens, "reasoning_effort": "low",
                        "messages": [{"role": "system", "content": system},
                                     {"role": "user", "content": user}]})
                    r.raise_for_status()
                    text = r.json()["choices"][0]["message"]["content"] or ""
                return parse_json(text)
            except Exception as exc:  # network, rate limit, bad JSON
                log.warning("LLM call failed (attempt %s, %s): %s", attempt + 1, model, exc)
        return None


llm = LLM()
