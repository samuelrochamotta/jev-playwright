"""Cliente do Jev (TypeSafe System One) via OpenRouter.

O Jev não é um modelo de chat: ele recebe um `state` (JSON) e um conjunto de
`questions` tipadas (choice / score / noul) e devolve escolhas com probabilidades.
Endpoint no OpenRouter: POST /api/alpha/decisions
"""
from __future__ import annotations

import math
import time
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
        self.model_version: str | None = None

    def decide(self, state: Any, questions: dict[str, dict]) -> dict[str, Any]:
        """Envia state + questions e devolve o dicionário `answers`."""
        payload = {"model": self._model, "state": state, "questions": questions}
        for attempt in range(3):
            try:
                resp = self._http.post("/api/alpha/decisions", json=payload)
            except (httpx.TimeoutException, httpx.TransportError):
                if attempt == 2:
                    raise
                time.sleep(0.5 * 2 ** attempt)
                continue
            if resp.status_code in {429, 502, 503, 504} and attempt < 2:
                try:
                    delay = min(10.0, max(0.0, float(resp.headers.get("retry-after", 0.5 * 2 ** attempt))))
                except ValueError:
                    delay = 0.5 * 2 ** attempt
                time.sleep(delay)
                continue
            break
        if resp.status_code >= 400:
            raise RuntimeError(f"Jev HTTP {resp.status_code}")
        data = resp.json()
        usage = data.get("usage") or {}
        self.total_cost += float(usage.get("cost") or 0.0)
        self.total_calls += 1
        self.model_version = data.get("model") or self.model_version
        answers = data.get("answers")
        if not isinstance(answers, dict) or any(key not in answers for key in questions):
            raise RuntimeError("Jev devolveu respostas incompletas")
        for key, question in questions.items():
            answer = answers[key]
            if not isinstance(answer, dict):
                raise RuntimeError(f"Resposta Jev inválida: {key}")
            if question["type"] == "noul":
                value = answer.get("noul")
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
                    raise RuntimeError(f"Probabilidade Jev inválida: {key}")
            elif question["type"] == "choice" and answer.get("choice") not in question["criteria"]:
                raise RuntimeError(f"Escolha Jev inválida: {key}")
        return answers

    def close(self) -> None:
        self._http.close()

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
