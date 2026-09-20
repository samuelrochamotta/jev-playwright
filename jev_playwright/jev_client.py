"""Cliente do Jev (TypeSafe System One) via OpenRouter.

O Jev não é um modelo de chat: ele recebe um `state` (JSON) e um conjunto de
`questions` tipadas (choice / score / noul) e devolve escolhas com probabilidades.
Endpoint no OpenRouter: POST /api/alpha/decisions
"""
from __future__ import annotations

from typing import Any

import httpx


class JevClient:
    def __init__(self, api_key: str, model: str, base_url: str = "https://openrouter.ai") -> None:
        self._model = model
        self._http = httpx.Client(
            base_url=base_url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/jev-playwright-checklist",
                "X-Title": "jev-playwright-checklist",
            },
            timeout=60.0,
        )
        self.total_cost = 0.0
        self.total_calls = 0

    def decide(self, state: Any, questions: dict[str, dict]) -> dict[str, Any]:
        """Envia state + questions e devolve o dicionário `answers`."""
        payload = {"model": self._model, "state": state, "questions": questions}
        resp = self._http.post("/api/alpha/decisions", json=payload)
        if resp.status_code >= 400:
            raise RuntimeError(f"Jev HTTP {resp.status_code}: {resp.text[:500]}")
        data = resp.json()
        usage = data.get("usage") or {}
        self.total_cost += float(usage.get("cost") or 0.0)
        self.total_calls += 1
        return data.get("answers", {})

    # ---- helpers para montar perguntas -------------------------------------------------

    @staticmethod
    def choice(instructions: str, criteria: dict[str, str]) -> dict:
        return {"type": "choice", "instructions": instructions, "criteria": criteria}

    @staticmethod
    def noul(instructions: str, criteria: dict[str, str] | None = None) -> dict:
        q: dict[str, Any] = {"type": "noul", "instructions": instructions}
        if criteria:
            q["criteria"] = criteria
        return q

    @staticmethod
    def score(instructions: str, levels: list[str]) -> dict:
        return {"type": "score", "instructions": instructions, "criteria": levels}
