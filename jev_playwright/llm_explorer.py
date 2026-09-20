"""Modo llm-only para seções de perguntas (baseline): um único LLM faz tudo via tool calling.

Sem Jev. O LLM vê o snapshot, escolhe ferramenta e argumentos, decide quando chegou ao alvo,
conta os cliques e responde as perguntas (só `sim`/`não` ou inteiro; a saída é normalizada).
Orçamento: llm_explore_turns turnos por seção.
"""
from __future__ import annotations

import json
import re
import time

from .browser import Browser
from .checklist import Section
from .config import Settings
from .explorer import QuestionResult, SectionResult, format_answer, parse_answer
from .llm_client import OpenRouterChat

ALLOWED_TOOLS = ["browser_snapshot", "browser_click", "browser_hover", "browser_type", "browser_press_key",
                 "browser_navigate", "browser_navigate_back", "browser_tabs"]
ACTION_TOOLS = {"browser_click", "browser_hover", "browser_type", "browser_press_key", "browser_navigate",
                "browser_navigate_back", "browser_tabs"}

SYSTEM = (
    "Você avalia um portal público de transparência respondendo às PERGUNTAS de uma seção do checklist. "
    "O ALVO da seção é o seu título. Use as ferramentas para navegar a partir da página já aberta: "
    "browser_snapshot mostra a página; os elementos têm referências como [ref=e12], use-as no campo `target`. "
    "Após cada ação um snapshot atualizado é devolvido. Faça UMA ação por vez. Menus podem estar escondidos "
    "(botão 'Menu', hover). Um link ou card com o nome do alvo NÃO é o alvo: o alvo é a página cujo conteúdo "
    "principal trata do assunto. Explore com parcimônia e desista quando esgotar as opções plausíveis.\n"
    "TODA pergunta admite apenas dois formatos de resposta: a palavra `sim` ou `não` (perguntas de existência / "
    "característica) ou um NÚMERO INTEIRO (perguntas de 'quantos'). Nunca responda com texto livre.\n"
    "Quando tiver as respostas (ou o orçamento acabar), responda SEM chamar ferramentas, apenas com JSON:\n"
    "{\"encontrado\": true|false, \"url_alvo\": \"...\", \"cliques\": <número de cliques do caminho MAIS CURTO da "
    "página inicial até o alvo, sem contar hovers>, \"caminho\": [\"clicar em ...\", ...], "
    "\"respostas\": {\"<id>\": {\"resposta\": \"sim\" | \"não\" | <inteiro>, \"confianca\": 0..1, \"evidencia\": \"...\"}}}"
)


class LlmExplorer:
    mode = "llm-only"

    def __init__(self, settings: Settings, browser: Browser, chat: OpenRouterChat, start_url: str, log=print) -> None:
        self.s, self.browser, self.chat, self.start_url, self.log = settings, browser, chat, start_url, log
        self._tools: list[dict] | None = None

    async def _tools_schema(self) -> list[dict]:
        if self._tools is None:
            listed = await self.browser._session.list_tools()
            self._tools = []
            for t in listed.tools:
                if t.name in ALLOWED_TOOLS:
                    schema = t.inputSchema if hasattr(t, "inputSchema") else t.input_schema
                    self._tools.append({"type": "function", "function": {
                        "name": t.name, "description": (t.description or "")[:400], "parameters": schema}})
        return self._tools

    @staticmethod
    def _json(text: str) -> dict:
        text = re.sub(r"```(?:json)?", "", text or "")
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            return {}
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            # tenta o primeiro objeto balanceado
            depth, start = 0, None
            for i, ch in enumerate(text):
                if ch == "{":
                    if depth == 0:
                        start = i
                    depth += 1
                elif ch == "}" and depth:
                    depth -= 1
                    if depth == 0 and start is not None:
                        try:
                            return json.loads(text[start:i + 1])
                        except json.JSONDecodeError:
                            start = None
            return {}

    async def run_section(self, section: Section) -> SectionResult:
        result = SectionResult(section.number, section.title, section.title)
        self.log(f"\n━━━ Seção {section.number}: {section.title}")
        t0 = time.perf_counter()
        llm0 = self.chat.total_calls
        tools = await self._tools_schema()
        try:
            await self.browser.close_extra_tabs()
            await self.browser.navigate(self.start_url)
            page = await self.browser.state()
            if await self.browser.dismiss_consent(page):
                page = await self.browser.state()
            qs = "\n".join(f"- {q.id}: {q.text}" for q in section.questions)
            messages = [{"role": "system", "content": SYSTEM},
                        {"role": "user", "content": f"ALVO DA SEÇÃO: {section.title}\nPERGUNTAS:\n{qs}\n\n"
                                                    f"PÁGINA INICIAL ({self.start_url}):\n{page.summary(7000)}"}]
            data: dict = {}
            for turn in range(self.s.llm_explore_turns + 1):
                final = turn == self.s.llm_explore_turns
                if final:
                    messages.append({"role": "user", "content": "Orçamento esgotado. Responda agora SÓ com o JSON final."})
                t1 = time.perf_counter()
                msg = self.chat.complete(messages, tools=None if final else tools)
                ms = int((time.perf_counter() - t1) * 1000)
                messages.append({k: v for k, v in msg.items() if k in ("role", "content", "tool_calls")} | {"role": "assistant"})
                calls = msg.get("tool_calls") or []
                if not calls:
                    content = msg.get("content", "") or ""
                    data = self._json(content)
                    result.trace.append(f"resposta final: {content[:1500]}")
                    self.log(f"    LLM: resposta final ({ms} ms)")
                    if not data.get("respostas") and not final:
                        # sem JSON válido: uma chance de corrigir o formato
                        messages.append({"role": "user", "content": "Responda SOMENTE com o JSON final no formato pedido."})
                        continue
                    break
                for call in calls:
                    name = call["function"]["name"]
                    try:
                        args = json.loads(call["function"]["arguments"] or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    self.log(f"    LLM: {name} {json.dumps(args, ensure_ascii=False)[:110]} ({ms} ms)")
                    result.trace.append(f"{name} {json.dumps(args, ensure_ascii=False)[:160]}")
                    try:
                        out = await self.browser.call(name, **args)
                        if name in ACTION_TOOLS:
                            await self.browser.wait(0.5)
                            await self.browser.focus_newest_tab()
                            out = out.split("### Snapshot")[0][:1200] + "\n\n" + await self.browser.call("browser_snapshot")
                    except Exception as exc:  # noqa: BLE001
                        out = f"ERRO: {exc}"
                    if name in ("browser_click", "browser_navigate", "browser_type"):
                        result.pages_visited += 1
                    messages.append({"role": "tool", "tool_call_id": call["id"], "content": out[:14000]})
            result.found = bool(data.get("encontrado"))
            result.final_url = str(data.get("url_alvo", "") or "")
            result.path = [str(p) for p in (data.get("caminho") or [])]
            clicks = parse_answer(data.get("cliques"))
            result.clicks = clicks if result.found and isinstance(clicks, int) and not isinstance(clicks, bool) else None
            answers = data.get("respostas") or {}
            for q in section.questions:
                a = answers.get(q.id) or {}
                raw = a.get("resposta")
                answer = format_answer(parse_answer(raw))  # só sim / não / inteiro / indeterminado
                result.questions.append(QuestionResult(
                    q.id, q.text, "llm", answer, float(a.get("confianca", 0.0) or 0.0),
                    str(a.get("evidencia", ""))[:300], source="llm"))
                extra = f"  (bruto: {str(raw)[:40]!r})" if answer == "indeterminado" and raw not in (None, "") else ""
                self.log(f"    {q.id} → {answer}  (conf {result.questions[-1].confidence:.2f}){extra}")
            result.found_prob = max((r.confidence for r in result.questions), default=0.0)
        except Exception as exc:  # noqa: BLE001
            result.error = str(exc)
            self.log(f"    ERRO: {exc}")
            for q in section.questions:
                if not any(r.id == q.id for r in result.questions):
                    result.questions.append(QuestionResult(q.id, q.text, "llm", "erro", 0.0, str(exc)[:200], "llm"))
        result.llm_calls = self.chat.total_calls - llm0
        result.elapsed_s = round(time.perf_counter() - t0, 1)
        self.log(f"    seção {section.number}: llm={result.llm_calls} {result.elapsed_s}s")
        return result
