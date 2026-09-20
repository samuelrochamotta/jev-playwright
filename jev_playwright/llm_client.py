"""Cliente mínimo de chat/completions do OpenRouter (usado pelo baseline llm-only e, como fallback
estrito de contagem, pelo modo jev)."""
from __future__ import annotations

import time
from typing import Any

import httpx


class OpenRouterChat:
    """Cliente de chat/completions com contagem de chamadas, tokens, tempo e custo (usage.include)."""

    def __init__(self, api_key: str, model: str, base_url: str = "https://openrouter.ai") -> None:
        self.model = model
        self._http = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            timeout=180.0,
        )
        self.total_calls = 0
        self.total_cost = 0.0
        self.total_tokens = 0
        self.total_ms = 0

    def complete(self, messages: list[dict], tools: list[dict] | None = None, temperature: float = 0) -> dict:
        body: dict[str, Any] = {"model": self.model, "messages": messages, "temperature": temperature,
                                "usage": {"include": True}}
        if tools:
            body["tools"] = tools
        t0 = time.perf_counter()
        resp = self._http.post("/api/v1/chat/completions", json=body)
        self.total_ms += int((time.perf_counter() - t0) * 1000)
        if resp.status_code >= 400:
            raise RuntimeError(f"LLM HTTP {resp.status_code}: {resp.text[:500]}")
        data = resp.json()
        usage = data.get("usage") or {}
        self.total_calls += 1
        self.total_cost += float(usage.get("cost") or 0.0)
        self.total_tokens += int(usage.get("total_tokens") or 0)
        return data["choices"][0]["message"]

    def stats(self) -> dict:
        return {"chamadas": self.total_calls, "custo_usd": round(self.total_cost, 5),
                "tokens": self.total_tokens, "tempo_ms": self.total_ms}
