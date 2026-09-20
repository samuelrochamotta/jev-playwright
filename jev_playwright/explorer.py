"""Modo Jev para seções de perguntas (avaliação de um portal de transparência).

Toda pergunta admite só resposta sim/não ou um número inteiro. Por seção:
  1. Jev (choice, 1 chamada, uma cabeça por pergunta): classifica cada pergunta em
     encontrar (sim/não) / cliques (número) / julgar_pagina (sim/não) / contar (número).
  2. Busca do alvo (título da seção) por DFS guiada pelo Jev — 1 chamada por página visitada,
     com as cabeças na MESMA requisição:
        chegou (noul)      → a página atual é uma seção dedicada ao tema do alvo?
        exato (noul)       → os qualificadores do alvo (federais, ano...) já estão satisfeitos?
        acao (choice)      → CLICK / HOVER / SEARCH / BACK
        alvo_clique        → ranking de links (as probabilidades viram a fila de backtracking)
        alvo_busca         → campo de busca, se houver
     Política em código: não repete (página, ação, alvo); poda links já visitados;
     becos sem saída voltam pela fila sem nova chamada ao modelo.
  3. Atalho em código: se alguma página do caminho já tinha link direto para a URL final,
     o número de cliques é encurtado (sem modelo).
  4. Jev (noul em lote, 1 chamada): perguntas do tipo julgar_pagina sobre a página-alvo.
  5. Perguntas do tipo contar, em duas etapas:
     a) Jev (noul em lote, 1 chamada): quais elementos "abridores" da página-alvo (dropdown customizado,
        menu, acordeão, aba) escondem os itens pedidos? Os aceitos são abertos um a um (clique), o que
        apareceu de novo no snapshot vira candidato "revelado", e o abridor é fechado (Escape / reclique /
        recarga da página).
     b) Jev (noul em lote sobre os candidatos revelados + visíveis): cada candidato é julgado "é um dos
        itens pedidos?" e a resposta é o número de "sim". O LLM só entra como fallback se a página não
        tiver candidatos, e mesmo assim devolve um inteiro.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from urllib.parse import urlparse

from .browser import Browser, PageState
from .checklist import Section
from .config import Settings
from .elements import (Element, STOPWORDS, candidate_pairs, count_candidates, keywords_from, normalize,
                       opener_candidates)
from .jev_client import JevClient
from .llm_client import OpenRouterChat

QUESTION_KINDS = {
    "encontrar": "Pergunta sim/não se o portal POSSUI / OFERECE / DISPONIBILIZA uma seção, página, área, menu ou "
                 "funcionalidade específica. A resposta é a existência do alvo da seção.",
    "cliques": "Pergunta QUANTOS CLIQUES, passos ou níveis são necessários para chegar ao alvo (número).",
    "julgar_pagina": "Pergunta sim/não sobre uma característica do CONTEÚDO da página-alvo, uma vez encontrada "
                     "(ex.: tem filtro por ano? permite exportar? mostra o autor da emenda?).",
    "contar": "Pergunta QUANTOS itens de um tipo existem na página-alvo (número): quantos filtros, campos, "
              "colunas, opções, anos disponíveis, formatos de exportação, links etc.",
}
BOOL_KINDS = {"encontrar", "julgar_pagina"}
INT_KINDS = {"cliques", "contar"}

YES = {"sim", "s", "yes", "y", "true", "verdadeiro", "possui", "tem"}
NO = {"nao", "n", "no", "false", "falso", "nenhum", "nenhuma"}
INT_RE = re.compile(r"-?\d+")


def parse_answer(value) -> bool | int | None:
    """Converte uma resposta (de qualquer modo ou do gabarito) em bool, int ou None (indeterminado)."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return int(value)
    text = normalize(str(value or "")).strip()
    if not text:
        return None
    head = re.split(r"[\s.,;:!()]+", text)[0]
    if head in YES:
        return True
    if head in NO:
        return False
    m = INT_RE.match(text)
    return int(m.group(0)) if m else None


def format_answer(value: bool | int | None) -> str:
    if value is None:
        return "indeterminado"
    if isinstance(value, bool):
        return "sim" if value else "não"
    return str(value)

ACTIONS = {
    "CLICK": "Clicar no link/botão/aba/menu de `elementos` com maior chance de levar ao alvo "
             "(ou de abrir o menu que contém o caminho até ele).",
    "HOVER": "Passar o mouse sobre um item de menu para revelar submenus escondidos.",
    "SEARCH": "Digitar o nome do alvo no campo de busca do site (há um campo de busca em `elementos`).",
    "BACK": "Beco sem saída: nada nesta página leva ao alvo; voltar e tentar outra alternativa.",
}
LOCAL_ACTIONS = {"HOVER"}


@dataclass
class PathStep:
    kind: str                      # click | search | hover | scroll
    element: str
    url_after: str = ""
    new_tab: bool = False

    def label(self) -> str:
        verb = {"click": "clicar em", "search": "buscar por", "hover": "passar o mouse em", "scroll": "rolar"}[self.kind]
        s = f"{verb} {self.element}"
        if self.new_tab:
            s += " (abre nova aba)"
        return s


@dataclass
class Frame:
    """Página de onde se clicou: guarda as alternativas ranqueadas pelo Jev para o backtracking."""
    url: str
    links: dict[str, str]                       # href → nome (para o atalho)
    alternatives: list[tuple[str, str, float]]  # (assinatura, descrição, probabilidade)
    tried: set[str]
    prep: list[tuple[str, str]]                 # ações locais (hover) a repetir ao voltar: (assinatura, descrição)
    step: PathStep


@dataclass
class QuestionResult:
    id: str
    text: str
    kind: str
    answer: str
    confidence: float
    evidence: str = ""
    source: str = "jev"


@dataclass
class SectionResult:
    number: str
    title: str
    target: str
    found: bool = False
    found_prob: float = 0.0
    clicks: int | None = None
    path: list[str] = field(default_factory=list)
    pages_visited: int = 0
    blocked: int = 0          # páginas de desafio anti-robô encontradas
    final_url: str = ""
    questions: list[QuestionResult] = field(default_factory=list)
    jev_calls: int = 0
    llm_calls: int = 0
    elapsed_s: float = 0.0
    error: str = ""
    trace: list[str] = field(default_factory=list)

    @property
    def mode_label(self) -> str:
        return "seção"


def search_terms(text: str) -> str:
    words = re.findall(r"[a-zA-ZÀ-ÿ]{3,}", text)
    return " ".join(w for w in words if normalize(w) not in STOPWORDS)


class JevExplorer:
    mode = "jev"

    def __init__(self, settings: Settings, browser: Browser, jev: JevClient, chat: OpenRouterChat,
                 start_url: str, log=print) -> None:
        self.s, self.browser, self.jev, self.chat, self.start_url, self.log = settings, browser, jev, chat, start_url, log
        self._host = urlparse(start_url).netloc.lower()
        labels = self._host.split(".")
        # transparencia.prefeitura.sp.gov.br → aceita *.prefeitura.sp.gov.br; nunca sai para outro domínio
        self._site_suffix = "." + ".".join(labels[1:]) if len(labels) >= 4 else "." + self._host
        self._clicked: set[str] = set()
        self._reference = ""

    # ---- Jev: perguntas tipadas ----------------------------------------------------------

    def classify(self, section: Section) -> dict[str, str]:
        state = {"secao": section.title, "alvo_da_secao": section.title,
                 "perguntas": {q.id: q.text for q in section.questions}}
        questions = {q.id: JevClient.choice(
            f"Classifique a pergunta `perguntas.{q.id}` em relação ao `alvo_da_secao`.", QUESTION_KINDS)
            for q in section.questions}
        answers = self.jev.decide(state, questions)
        return {q.id: answers.get(q.id, {}).get("choice", "julgar_pagina") for q in section.questions}

    def _viable(self, e: Element, page: PageState, visited: set[str]) -> bool:
        """Elemento que ainda pode ser clicado: não repete a página atual, links já visitados, já clicados
        nem sites externos. Só estes entram na tabela do Jev, para a probabilidade ir a opções úteis."""
        if e.kind != "click":
            return True
        if e.signature in self._clicked:
            return False
        if e.href and (e.href in visited or e.href.rstrip("/") == page.url.rstrip("/")):
            return False
        return not e.href or self._same_site(e.href)

    def _decide(self, target: str, page: PageState, stack: list[Frame], discarded: list[str],
                visited: set[str] | None = None) -> dict:
        shown = [e for e in page.elements if self._viable(e, page, visited or set())]
        clickable = {str(e.index): e.describe() for e in shown if e.kind == "click"}
        typeable = {str(e.index): e.describe() for e in shown if e.kind == "type"}
        state = {
            "alvo": target,
            "pergunta_de_referencia": self._reference,
            "contexto": "Estamos navegando num portal público procurando a página/seção descrita em `alvo`. "
                        "Um link ou card com o nome do alvo NÃO é o alvo: o alvo é a página cujo conteúdo principal "
                        "(título, listagem, filtros, dados) trata do assunto.",
            "pagina_atual": {"url": page.url, "titulo": page.title, "profundidade_em_cliques": len(stack),
                             "conteudo": page.content(self.s.page_text_chars)},
            "caminho_percorrido": [f.step.label() for f in stack],
            "paginas_ja_descartadas": discarded[-8:],
            "elementos": [e.line() for e in shown],
        }
        questions = {
            "chegou": JevClient.noul(
                "Avalie SOMENTE `pagina_atual.conteudo` e `pagina_atual.titulo`. Esta página é uma seção DEDICADA ao "
                "tema principal do `alvo`? Julgue pelo TEMA (ex.: 'emendas parlamentares'), não pelos qualificadores "
                "(federais/estaduais, ano, órgão): uma página, painel ou dashboard dedicado ao tema conta como a seção, "
                "mesmo que o título não repita todos os termos do alvo, que os dados venham de um painel embutido ou "
                "que só explique o tema e aponte para consultas externas.",
                {"true": "O título principal e o conteúdo (explicação, listagem, painel, consulta, filtros ou links para "
                         "consultar) são sobre o tema do alvo.",
                 "false": "É a página inicial, uma página de outro tema, resultados de busca/listagem de categorias, ou "
                          "uma página que apenas contém um link/card para o alvo entre outros."}),
            "exato": JevClient.noul(
                "Além do tema, esta página já corresponde EXATAMENTE ao `alvo` e à `pergunta_de_referencia`, incluindo "
                "os qualificadores (esfera federal/estadual, ano, órgão)? Se o alvo pede 'federais' e a página cobre o "
                "tema em geral com uma aba/filtro/link 'Federais' ainda por selecionar, responda false.",
                {"true": "A página já é a parte específica pedida (qualificadores satisfeitos, ou o alvo não tem qualificador).",
                 "false": "A página trata do tema em geral; a parte específica exige mais um clique (aba, filtro, link) "
                          "ou não existe nela."}),
            "acao": JevClient.choice(
                "Se esta página NÃO é o alvo, qual ação de navegação tem mais chance de levar ao alvo? "
                "Prefira CLICK quando houver em `elementos` um link ou menu plausível (mesmo com nome genérico como "
                "'Transferências', 'Receitas', 'Convênios', 'Menu'). Use BACK só quando nada aqui é promissor.", ACTIONS),
        }
        if clickable:
            questions["alvo_clique"] = JevClient.choice(
                "Se a ação for CLICK ou HOVER, qual índice de `elementos` tem maior chance de levar ao `alvo` "
                "(diretamente ou abrindo o menu que o contém)? Ignore links já presentes em `caminho_percorrido`.",
                clickable)
        if typeable:
            questions["alvo_busca"] = JevClient.choice(
                "Se a ação for SEARCH, qual índice de `elementos` é o campo de busca do site?", typeable)
        return self.jev.decide(state, questions)

    @staticmethod
    def _rank(answer: dict | None) -> list[tuple[str, float]]:
        if not answer:
            return []
        probs = answer.get("probabilities") or {answer.get("choice"): 1.0}
        return sorted(probs.items(), key=lambda kv: -kv[1])

    # ---- política em código -------------------------------------------------------------

    def _policy(self, ans: dict, page: PageState, tried: set, visited: set[str], local_actions: int, depth: int
                ) -> tuple[str, Element | None, float, list[tuple[str, str, float]]]:
        """Escolhe (ação, elemento) respeitando o que já foi tentado nesta página e os links já visitados.
        Devolve também as alternativas de clique ranqueadas (fila de backtracking)."""
        by_index = {str(e.index): e for e in page.elements}
        conf = float(ans.get("acao", {}).get("confidence", 0) or 0)

        def ok_click(e: Element) -> bool:
            if (page.hash, "CLICK", e.signature) in tried or e.signature in self._clicked:
                return False
            if e.href and (e.href in visited or e.href.rstrip("/") == page.url.rstrip("/")):
                return False
            if e.href and not self._same_site(e.href):
                return False
            return True

        ranked_click = [(by_index[k], p) for k, p in self._rank(ans.get("alvo_clique")) if k in by_index]
        alternatives = [(e.signature, e.describe()[:100], p) for e, p in ranked_click
                        if ok_click(e) and p >= self.s.min_alt_prob][: self.s.max_alternatives + 1]

        for op, _p in self._rank(ans.get("acao")):
            if op == "BACK":
                if depth == 0 and alternatives:
                    continue  # na página inicial não há para onde voltar: tenta a melhor alternativa
                return "BACK", None, conf, alternatives
            if op in LOCAL_ACTIONS and local_actions >= self.s.max_local_actions:
                continue
            if op in ("CLICK", "HOVER"):
                for e, _ in ranked_click:
                    if op == "HOVER" and (page.hash, "HOVER", e.signature) in tried:
                        continue
                    if op == "CLICK" and not ok_click(e):
                        continue
                    return op, e, conf, alternatives
            if op == "SEARCH":
                for k, _ in self._rank(ans.get("alvo_busca")):
                    e = by_index.get(k)
                    if e is not None and (page.hash, "SEARCH", e.signature) not in tried:
                        return op, e, conf, alternatives
        return "BACK", None, conf, alternatives

    # ---- navegação ----------------------------------------------------------------------

    async def _ready(self) -> PageState:
        """Snapshot pronto para decisão: espera a página carregar e estabilizar, fecha banner de consentimento."""
        page = await self.browser.state()
        for _ in range(4):
            if page.is_document:
                break
            loaded = len(page.elements) >= 3 and bool(page.title)
            await self.browser.wait(1.2 if loaded else 2.0)
            again = await self.browser.state()
            stable = again.hash == page.hash
            page = again
            if loaded and stable:
                break
        if await self.browser.dismiss_consent(page):
            page = await self.browser.state()
        return page

    def _same_site(self, href: str) -> bool:
        if not href:
            return True
        host = urlparse(href).netloc.lower()
        if not host:
            return True
        return host == self._host or host.endswith(self._site_suffix)

    async def _do(self, op: str, el: Element | None, query: str) -> tuple[PageState, bool]:
        """Executa a ação; devolve a página resultante e se abriu nova aba."""
        if op == "CLICK" and el is not None:
            await self.browser.click(el)
        elif op == "HOVER" and el is not None:
            await self.browser.hover(el)
        elif op == "SEARCH" and el is not None:
            await self.browser.type(el, query, submit=True)
        await self.browser.wait(0.8)
        new_tab = op in ("CLICK", "SEARCH") and await self.browser.focus_newest_tab()
        return await self._ready(), new_tab

    async def _backtrack(self, stack: list[Frame], visited: set[str], evaluated: set[str], trace: list[str]) -> PageState | None:
        """Volta pela fila de alternativas do Jev (sem nova chamada ao modelo)."""
        while stack:
            fr = stack[-1]
            nxt = next((a for a in fr.alternatives if a[0] not in fr.tried), None)
            if nxt is None:
                stack.pop()
                continue
            fr.tried.add(nxt[0])
            await self.browser.close_extra_tabs()
            await self.browser.navigate(fr.url)
            page = await self._ready()
            for sig, _desc in fr.prep:
                el = page.find(sig)
                if el is not None:
                    await self.browser.hover(el)
                    await self.browser.wait(0.5)
                    page = await self.browser.state()
            el = page.find(nxt[0])
            if el is None:
                trace.append(f"↩ alternativa '{nxt[1]}' não encontrada ao voltar")
                continue
            if el.href and el.href in visited:
                continue
            trace.append(f"↩ voltando a {fr.url} e tentando alternativa: {nxt[1]} (p={nxt[2]:.2f})")
            self._clicked.add(el.signature)
            try:
                new_page, new_tab = await self._do("CLICK", el, "")
            except RuntimeError as exc:
                trace.append(f"· clique deu erro ({str(exc)[:60]}); verificando a página")
                new_page, new_tab = await self._ready(), False
            if new_page.hash == page.hash or new_page.hash in evaluated:
                continue
            fr.step = PathStep("click", f"'{el.name or el.role}'", new_page.url, new_tab)
            return new_page
        return None

    async def find(self, target: str, keywords: list[str], result: SectionResult) -> PageState | None:
        """DFS guiada pelo Jev. Devolve a página-alvo (ou None) e preenche result."""
        trace = result.trace
        self.browser.keywords = keywords
        query = search_terms(target)
        await self.browser.close_extra_tabs()
        await self.browser.navigate(self.start_url)
        stack: list[Frame] = []
        visited: set[str] = set()      # URLs já avaliadas (poda de links)
        evaluated: set[str] = set()    # hashes de página já avaliados (abas/SPA mudam conteúdo sem mudar URL)
        self._clicked = set()
        discarded: list[str] = []
        tried: set = set()
        local_actions = 0
        prep: list[tuple[str, str]] = []
        best = 0.0
        page: PageState | None = await self._ready()

        while page is not None and result.pages_visited < self.s.max_pages_per_find:
            dead_end = False
            if page.is_challenge:
                result.blocked += 1
                trace.append(f"⛔ desafio anti-robô (captcha/WAF) em {page.url}")
                self.log(f"    ⛔ desafio anti-robô em {page.url}")
                visited.add(page.url)
                dead_end = True
            elif page.is_document:
                trace.append(f"✗ {page.url} é um documento/arquivo, não uma página")
                visited.add(page.url)
                dead_end = True
            else:
                result.pages_visited += 1
                t0 = time.perf_counter()
                ans = self._decide(target, page, stack, discarded, visited)
                ms = int((time.perf_counter() - t0) * 1000)
                prob = float(ans.get("chegou", {}).get("noul", 0.0) or 0.0)
                best = max(best, prob)
                op, el, conf, alternatives = self._policy(ans, page, tried, visited, local_actions, len(stack))
                self.log(f"    Jev [{len(stack)}] {page.title[:50]!r} P(alvo)={prob:.2f} → {op}"
                         f"{(' ' + el.describe()[:70]) if el else ''} (conf {conf:.2f}, {ms} ms)")
                if prob >= self.s.arrive_threshold:
                    page, prob = await self._refine(target, page, ans, stack, visited, discarded, trace, result)
                    result.found, result.found_prob, result.final_url = True, prob, page.url
                    result.path = [f.step.label() for f in stack]
                    result.clicks = sum(1 for f in stack if f.step.kind in ("click", "search"))
                    trace.append(f"✓ alvo encontrado em {page.url} (P={prob:.2f}) após {result.clicks} clique(s)")
                    self._shortcut(stack, page.url, result)
                    return page
                visited.add(page.url)
                evaluated.add(page.hash)
                discarded.append(f"{page.title[:60]} ({page.url})")

                if op == "BACK" or len(stack) >= self.s.max_depth and op in ("CLICK", "SEARCH"):
                    if len(stack) >= self.s.max_depth and op != "BACK":
                        trace.append(f"✗ profundidade máxima ({self.s.max_depth}) em {page.url}")
                    dead_end = True
                elif op in LOCAL_ACTIONS:
                    tried.add((page.hash, op, el.signature if el else "-"))
                    local_actions += 1
                    if op == "HOVER" and el is not None:
                        prep.append((el.signature, el.describe()[:100]))
                    trace.append(f"· {op.lower()} {el.describe()[:80] if el else ''}")
                    page, _ = await self._do(op, el, query)
                    result.pages_visited -= 1  # ação local: não conta como página nova
                    continue
                else:  # CLICK / SEARCH
                    tried.add((page.hash, op, el.signature))
                    if op == "CLICK":
                        self._clicked.add(el.signature)
                    before = page
                    try:
                        new_page, new_tab = await self._do(op, el, query)
                    except RuntimeError as exc:
                        # timeout no clique não significa que nada aconteceu: a navegação pode ter ocorrido
                        trace.append(f"· {op.lower()} {el.describe()[:60]} deu erro ({str(exc)[:60]}); verificando a página")
                        new_page, new_tab = await self._ready(), False
                    if new_page.hash == before.hash:
                        trace.append(f"· {op.lower()} {el.describe()[:80]} não mudou a página")
                        page = new_page
                        continue
                    kind = "search" if op == "SEARCH" else "click"
                    label = f"'{query}'" if op == "SEARCH" else f"'{el.name or el.role}'"
                    step = PathStep(kind, label, new_page.url, new_tab)
                    stack.append(Frame(before.url, {}, [a for a in alternatives if a[0] != el.signature],
                                       {el.signature}, prep, step))
                    stack[-1].links = self._links(before)
                    trace.append(f"→ [{len(stack)}] {step.label()} → {new_page.url}")
                    local_actions, prep = 0, []
                    if new_page.hash in evaluated:
                        trace.append("✗ página já avaliada")
                        dead_end = True
                    else:
                        page = new_page
                        continue
            if dead_end:
                page = await self._backtrack(stack, visited, evaluated, trace)
                local_actions, prep = 0, []
                if page is not None:
                    tried = {t for t in tried if t[0] != page.hash}

        result.found, result.found_prob = False, best
        if result.pages_visited >= self.s.max_pages_per_find:
            trace.append(f"✗ orçamento de {self.s.max_pages_per_find} páginas esgotado (melhor P={best:.2f})")
        else:
            trace.append(f"✗ alternativas esgotadas (melhor P={best:.2f})")
        return None

    async def _refine(self, target: str, page: PageState, ans: dict, stack: list[Frame], visited: set[str],
                      discarded: list[str], trace: list[str], result: SectionResult) -> tuple[PageState, float]:
        """Já na seção do tema. Se o Jev diz que faltam qualificadores do alvo (ex.: aba 'Federais' numa página
        de emendas em geral), dá até `max_refine_steps` cliques DENTRO da seção, guiado pela cabeça `alvo_clique`,
        e só aceita cada passo se `exato` subir mantendo o tema. Caso contrário, fica na página do tema."""
        prob = float(ans.get("chegou", {}).get("noul", 0.0) or 0.0)
        for _ in range(self.s.max_refine_steps):
            exact = float(ans.get("exato", {}).get("noul", 0.0) or 0.0)
            if exact >= self.s.arrive_threshold:
                break
            by_index = {str(e.index): e for e in page.elements}
            ranked = [(by_index[k], p) for k, p in self._rank(ans.get("alvo_clique")) if k in by_index]
            cand = next(((e, p) for e, p in ranked if self._viable(e, page, visited)), None)
            if cand is None or cand[1] < self.s.refine_min_prob:
                trace.append(f"· seção do tema encontrada (exato={exact:.2f}); sem sub-alvo claro para refinar")
                break
            el, p = cand
            label = f"'{el.name or el.role}'"
            self._clicked.add(el.signature)
            before = page
            try:
                new_page, new_tab = await self._do("CLICK", el, "")
            except RuntimeError:
                new_page, new_tab = await self._ready(), False
            if new_page.hash == before.hash:
                trace.append(f"· refinamento: clicar em {label} não mudou a página")
                break
            result.pages_visited += 1
            ans2 = self._decide(target, new_page, stack, discarded, visited)
            prob2 = float(ans2.get("chegou", {}).get("noul", 0.0) or 0.0)
            exact2 = float(ans2.get("exato", {}).get("noul", 0.0) or 0.0)
            self.log(f"    Jev [{len(stack)}+] refinamento {label}: P(tema)={prob2:.2f} P(exato)={exact:.2f}→{exact2:.2f}")
            if prob2 >= self.s.arrive_threshold and exact2 > exact:
                stack.append(Frame(before.url, self._links(before), [], {el.signature}, [],
                                   PathStep("click", label, new_page.url, new_tab)))
                trace.append(f"→ [{len(stack)}] {stack[-1].step.label()} → {new_page.url} "
                             f"(refinamento: exato {exact:.2f}→{exact2:.2f})")
                page, ans, prob = new_page, ans2, prob2
                visited.add(before.url)
                continue
            trace.append(f"· refinamento com {label} não melhorou (tema={prob2:.2f}, exato={exact2:.2f}); "
                         f"mantendo {before.url}")
            await self.browser.close_extra_tabs()
            await self.browser.navigate(before.url)
            page = await self._ready()
            break
        return page, prob

    @staticmethod
    def _links(page: PageState) -> dict[str, str]:
        return {e.href: (e.name or e.role) for e in page.elements if e.href}

    def _shortcut(self, stack: list[Frame], final_url: str, result: SectionResult) -> None:
        """Se uma página anterior do caminho já linkava direto para a URL final, encurta o caminho (sem modelo).
        Cliques dentro da página final (abas, filtros) continuam contando."""
        target = final_url.rstrip("/")
        first = next((j for j, f in enumerate(stack) if f.step.url_after.rstrip("/") == target), None)
        if first is None:
            return
        tail = stack[first + 1:]  # passos dados já dentro da página final (ex.: aba 'Federais')
        for i, fr in enumerate(stack[:first + 1]):
            name = next((n for h, n in fr.links.items() if h.rstrip("/") == target), None)
            if name is not None and i < first:
                result.path = ([f.step.label() for f in stack[:i]] + [f"clicar em '{name}' (link direto)"]
                               + [f.step.label() for f in tail])
                result.clicks = i + 1 + sum(1 for f in tail if f.step.kind in ("click", "search"))
                result.trace.append(f"✓ atalho: a página {fr.url} já tinha link direto '{name}' → {result.clicks} clique(s)")
                return

    # ---- respostas ----------------------------------------------------------------------

    def _judge(self, questions: list, target: str, page: PageState) -> dict[str, float]:
        """Perguntas sim/não sobre a página-alvo: Jev `noul` em lote, uma cabeça por pergunta, 1 chamada.
        As perguntas são sobre o que ESTA página oferece: controles reais (filtros, campos, botões de exportação)
        são julgados por `pagina.elementos`; informação exibida, por `pagina.conteudo`. Texto que só descreve ou
        aponta para um recurso de outro site não vale como evidência."""
        state = {"alvo": target,
                 "pagina": {"url": page.url, "titulo": page.title, "conteudo": page.content(8000),
                            "elementos": [e.line() for e in page.elements]},
                 "perguntas": {q.id: q.text for q in questions}}
        qs = {q.id: JevClient.noul(
            f"A resposta para `perguntas.{q.id}` é SIM para ESTA página (`pagina.url`)? A pergunta é sobre o que a "
            f"própria página oferece ao usuário. Se ela pergunta por um controle (filtro, seletor, campo, botão, "
            f"aba, exportação, download), a evidência principal é `pagina.elementos`: precisa existir um elemento "
            f"interativo correspondente NESTA página. Se ela pergunta por uma informação exibida (autor, valor, "
            f"órgão, data...), a evidência é `pagina.conteudo`. Texto em `pagina.conteudo` que apenas descreve, "
            f"explica ou orienta a usar um recurso de OUTRO site/portal (ou de um link externo) NÃO conta como "
            f"a página oferecer esse recurso.",
            {"true": "A própria página tem o controle interativo pedido em `pagina.elementos`, ou exibe a informação "
                     "pedida em `pagina.conteudo` (mesmo com palavras diferentes).",
             "false": "A página não tem esse controle nem exibe essa informação; ou só menciona/descreve o recurso "
                      "como existente em outro site; ou mostra o contrário."}) for q in questions}
        answers = self.jev.decide(state, qs)
        return {q.id: float(answers.get(q.id, {}).get("noul", 0.0) or 0.0) for q in questions}

    async def _reveal(self, question: str, target: str, page: PageState, keywords: list[str]
                      ) -> tuple[list[str], list[str]]:
        """Dropdowns customizados, menus, acordeões e afins escondem itens que não estão no snapshot.
        O Jev julga (noul, 1 chamada) quais abridores da página-alvo escondem os itens pedidos; cada um
        aceito é aberto, o que surgiu de novo no snapshot vira candidato revelado, e o abridor é fechado.
        Devolve (candidatos revelados, notas para a evidência)."""
        openers = opener_candidates(page.elements, page.url, keywords, self.s.max_openers_shown)
        if not openers:
            return [], []
        state = {"alvo": target, "pergunta": question,
                 "pagina": {"url": page.url, "titulo": page.title, "conteudo": page.content(self.s.page_text_chars)},
                 "abridores": {str(i): e.describe() for i, e in enumerate(openers, 1)}}
        qs = {str(i): JevClient.noul(
            f"`pergunta` pede para CONTAR itens de um certo tipo. Os itens a contar podem estar ESCONDIDOS atrás de "
            f"`abridores.{i}` (dropdown/seletor customizado, lista suspensa, menu, acordeão, painel de filtros) e só "
            f"aparecerem depois de abri-lo? Responda true SOMENTE se abrir esse elemento deve revelar exatamente os "
            f"itens pedidos (ex.: pergunta 'quantos anos' e o abridor é o seletor de ano). Responda false se o "
            f"abridor É um dos itens contados (ex.: pergunta 'quantos filtros' e o abridor é um filtro), se ele "
            f"navega para outra página, ou se não tem relação com a pergunta.",
            {"true": "Abrir este elemento revela a lista dos itens que a pergunta pede para contar.",
             "false": "Ele é um dos itens, ou leva a outra página, ou não tem relação com o que se conta."})
            for i in range(1, len(openers) + 1)}
        answers = self.jev.decide(state, qs)
        ranked = sorted(((openers[int(k) - 1], float(v.get("noul", 0.0) or 0.0)) for k, v in answers.items()
                        if k.isdigit() and int(k) <= len(openers)), key=lambda ep: -ep[1])
        chosen = [(e, p) for e, p in ranked if p >= 0.5][: self.s.max_openers]
        if not chosen:
            return [], []
        base_keys = {k for k, _d in candidate_pairs(page.snapshot)}
        revealed: list[str] = []
        seen: set[str] = set()
        notes: list[str] = []
        for el, p in chosen:
            fresh = await self.browser.state()
            live = fresh.find(el.signature)
            label = f"{el.role} '{el.name}'"
            if live is None:
                notes.append(f"abridor {label} não encontrado")
                continue
            tabs_before = await self.browser.tabs()
            try:
                await self.browser.click(live)
            except RuntimeError as exc:
                notes.append(f"abridor {label}: erro ao clicar ({str(exc)[:40]})")
                continue
            await self.browser.wait(0.6)
            await self.browser.close_new_tabs(tabs_before)  # abridor que abriu nova aba: descarta só a aba nova
            opened = await self.browser.state()
            if opened.url.rstrip("/") != page.url.rstrip("/"):
                notes.append(f"abridor {label} navegou para outra página; ignorado")
                await self.browser.navigate(page.url)
                await self._ready()
                continue
            new = [(k, d) for k, d in candidate_pairs(opened.snapshot) if k not in base_keys and k not in seen]
            for k, d in new:
                seen.add(k)
                revealed.append(f"{d} · revelado ao abrir {label}")
            self.log(f"    contar: abrir {label} (P={p:.2f}) revelou {len(new)} item(ns)")
            notes.append(f"abrir {label} revelou {len(new)} item(ns)")
            await self._close_opener(page, live, base_keys)
        return revealed, notes

    async def _close_opener(self, page: PageState, opener: Element, base_keys: set[str]) -> None:
        """Fecha o que foi aberto: Escape; se ainda houver itens extras, reclica o abridor; por fim recarrega."""
        try:
            await self.browser.press("Escape")
            await self.browser.wait(0.3)
        except RuntimeError:
            pass
        after = await self.browser.state()
        if not {k for k, _d in candidate_pairs(after.snapshot)} - base_keys:
            return
        again = after.find(opener.signature)
        if again is not None:
            try:
                await self.browser.click(again)
                await self.browser.wait(0.3)
            except RuntimeError:
                pass
            after = await self.browser.state()
            if not {k for k, _d in candidate_pairs(after.snapshot)} - base_keys:
                return
        await self.browser.navigate(page.url)
        await self._ready()

    async def _count(self, question: str, target: str, page: PageState) -> tuple[int, float, str, str]:
        """Pergunta `contar`: abre dropdowns/menus que escondem os itens (ver `_reveal`) e depois Jev `noul`
        em lote sobre os candidatos (revelados + visíveis), um por cabeça; a resposta é o número de "sim".
        Devolve (n, confiança, evidência, fonte)."""
        keywords = keywords_from(question)
        revealed, notes = await self._reveal(question, target, page, keywords)
        cands = count_candidates(page.snapshot, keywords, self.s.max_count_candidates, revealed)
        if not cands:
            n, ev = self._count_llm(question, target, page)
            return n, 0.0, ev, "llm"
        state = {"alvo": target, "pergunta": question,
                 "pagina": {"url": page.url, "titulo": page.title, "conteudo": page.content(self.s.page_text_chars)},
                 "candidatos": {str(i): c for i, c in enumerate(cands, 1)}}
        probs: dict[str, float] = {}
        for start in range(0, len(cands), self.s.count_batch):
            keys = [str(i) for i in range(start + 1, min(start + self.s.count_batch, len(cands)) + 1)]
            qs = {k: JevClient.noul(
                f"`pergunta` pede para CONTAR itens de um certo tipo na página-alvo. O `candidatos.{k}` é UM desses "
                f"itens (um dos que devem entrar na contagem)? Candidatos marcados 'revelado ao abrir X' estavam "
                f"escondidos num dropdown/menu e só apareceram ao abri-lo. Considere o papel do elemento e o "
                f"contexto em `pagina.conteudo`; não conte elementos de navegação geral do site, o próprio abridor, "
                f"nem itens de outro tipo.",
                {"true": "O candidato é exatamente um dos itens que a pergunta pede para contar.",
                 "false": "O candidato é outra coisa (navegação, rodapé, abridor, item de outro tipo, texto solto)."})
                for k in keys}
            answers = self.jev.decide(state, qs)
            for k in keys:
                probs[k] = float(answers.get(k, {}).get("noul", 0.0) or 0.0)
        counted = [k for k, p in probs.items() if p >= 0.5]
        conf = round(sum(max(p, 1 - p) for p in probs.values()) / len(probs), 2) if probs else 0.0
        evidence = "; ".join(cands[int(k) - 1] for k in counted)[:300] or f"{len(cands)} candidatos, nenhum aceito"
        if notes:
            evidence = f"[{'; '.join(notes)}] " + evidence
        return len(counted), conf, evidence[:400], "jev"

    def _count_llm(self, question: str, target: str, page: PageState) -> tuple[int, str]:
        """Fallback estrito (só quando a página não tem candidatos enumeráveis): o LLM devolve um inteiro."""
        system = ("Você conta itens numa página web (árvore de acessibilidade). Responda SOMENTE com um número "
                  "inteiro (0 se não houver), sem texto.")
        user = f"ALVO: {target}\nPERGUNTA: {question}\n\nPÁGINA:\n{page.summary(9000)}"
        text = self.chat.complete([{"role": "system", "content": system}, {"role": "user", "content": user}])["content"]
        m = INT_RE.search(text or "")
        return (int(m.group(0)) if m else 0), f"LLM (sem candidatos enumeráveis): {(text or '').strip()[:80]}"

    async def run_section(self, section: Section) -> SectionResult:
        target = section.title
        result = SectionResult(section.number, section.title, target)
        self.log(f"\n━━━ Seção {section.number}: {section.title}")
        t0 = time.perf_counter()
        jev0, llm0 = self.jev.total_calls, self.chat.total_calls
        try:
            kinds = self.classify(section)
            self._reference = next((q.text for q in section.questions if kinds[q.id] == "encontrar"),
                                   section.questions[0].text)
            for q in section.questions:
                self.log(f"    {q.id} [{kinds[q.id]}] {q.text}")
            keywords = keywords_from(section.title)
            page = await self.find(target, keywords, result)
            status = f"encontrado em {result.clicks} clique(s)" if result.found else "não encontrado"
            self.log(f"    alvo: {status}  (P={result.found_prob:.2f}, {result.pages_visited} páginas avaliadas)")

            judge_qs = [q for q in section.questions if kinds[q.id] == "julgar_pagina"]
            judged = self._judge(judge_qs, target, page) if (judge_qs and page is not None) else {}
            for q in section.questions:
                kind = kinds[q.id]
                not_found = "indeterminado (bloqueio anti-robô)" if result.blocked else "não"
                not_found_conf = 0.0 if result.blocked else round(1 - result.found_prob, 2)
                if kind == "encontrar":
                    if result.found:
                        qr = QuestionResult(q.id, q.text, kind, "sim", result.found_prob, result.final_url)
                    else:
                        qr = QuestionResult(q.id, q.text, kind, not_found, not_found_conf,
                                            f"{result.pages_visited} páginas avaliadas sem encontrar o alvo"
                                            + (f"; {result.blocked} bloqueada(s) por captcha" if result.blocked else ""))
                elif kind == "cliques":
                    if result.found:
                        qr = QuestionResult(q.id, q.text, kind, str(result.clicks), result.found_prob, " → ".join(result.path))
                    else:
                        qr = QuestionResult(q.id, q.text, kind, "indeterminado" if not result.blocked else not_found,
                                            not_found_conf, "alvo não encontrado")
                elif kind == "julgar_pagina":
                    if page is None:
                        qr = QuestionResult(q.id, q.text, kind, not_found, not_found_conf, "alvo não encontrado")
                    else:
                        p = judged.get(q.id, 0.0)
                        qr = QuestionResult(q.id, q.text, kind, "sim" if p >= 0.5 else "não", round(max(p, 1 - p), 2), page.url)
                else:  # contar → Jev noul em lote sobre candidatos (LLM só como fallback)
                    if page is None:
                        qr = QuestionResult(q.id, q.text, kind, "0" if not result.blocked else not_found, not_found_conf,
                                            "alvo não encontrado")
                    else:
                        n, conf, ev, src = await self._count(q.text, target, page)
                        qr = QuestionResult(q.id, q.text, kind, str(n), conf, ev, source=src)
                result.questions.append(qr)
                self.log(f"    {q.id} → {qr.answer}  (conf {qr.confidence:.2f})")
        except Exception as exc:  # noqa: BLE001
            result.error = str(exc)
            self.log(f"    ERRO: {exc}")
            for q in section.questions:
                if not any(r.id == q.id for r in result.questions):
                    result.questions.append(QuestionResult(q.id, q.text, "?", "erro", 0.0, str(exc)[:200]))
        result.jev_calls = self.jev.total_calls - jev0
        result.llm_calls = self.chat.total_calls - llm0
        result.elapsed_s = round(time.perf_counter() - t0, 1)
        self.log(f"    seção {section.number}: jev={result.jev_calls} llm={result.llm_calls} {result.elapsed_s}s")
        return result
