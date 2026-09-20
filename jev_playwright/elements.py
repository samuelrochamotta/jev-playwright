"""Converte o snapshot de acessibilidade do Playwright MCP numa tabela de elementos indexados.

Formato de cada linha do snapshot (YAML):
    - button "Login" [ref=e15] [cursor=pointer]
    - textbox "Username" [ref=e11]
    - combobox "Sort" [ref=e30]: "Name (A to Z)"
    - link "Emendas" [ref=e40] [cursor=pointer]:
        - /url: /emendas
A tabela resultante é o que o Jev recebe para escolher o alvo (por índice).
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from urllib.parse import urljoin, urldefrag

LINE_RE = re.compile(
    r"""^\s*-\s+(?P<role>[a-zA-Z]+)(?:\s+"(?P<name>(?:[^"\\]|\\.)*)")?(?P<attrs>(?:\s+\[[^\]]*\])*)\s*:?\s*(?P<rest>.*)$"""
)
REF_RE = re.compile(r"\[ref=([A-Za-z0-9_]+)\]")
URL_RE = re.compile(r"^\s*-\s+/url:\s*\"?([^\"]*)\"?\s*$")

CLICKABLE = {"button", "link", "checkbox", "radio", "menuitem", "menuitemcheckbox", "menuitemradio",
             "tab", "switch", "option", "treeitem"}
GENERIC_NAMES = {"add to cart", "remove", "buy", "comprar", "adicionar", "remover", "ver", "view", "details",
                 "detalhes", "edit", "editar", "delete", "excluir", "more", "mais", "select", "selecionar", "open", "abrir",
                 "acessar", "acesse", "saiba mais", "clique aqui", "consultar", "ver mais", "leia mais"}
TYPEABLE = {"textbox", "searchbox", "spinbutton"}
SELECTABLE = {"combobox", "listbox"}

# Botões de consentimento (cookies/LGPD) e avisos que cobrem a página: clicados por política, sem modelo.
CONSENT_NAMES = {"aceitar", "aceitar todos", "aceitar todos os cookies", "aceitar cookies", "aceito", "concordo",
                 "entendi", "entendi!", "ok", "ok, entendi", "accept", "accept all", "accept all cookies", "i agree",
                 "estou ciente", "ciente", "continuar", "prosseguir", "fechar", "close", "×", "x"}
CONSENT_CONTEXT_RE = re.compile(r"cookie|lgpd|privacidade|privacy|consentimento|consent", re.I)
STOPWORDS = {"existência", "existencia", "seção", "secao", "específica", "especifica", "portal", "possui", "para",
             "sobre", "site", "página", "pagina", "área", "area", "menu", "link", "informações", "informacoes",
             "dados", "consulta", "consultas", "transparência", "transparencia", "quantos", "cliques", "necessários"}


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in text if not unicodedata.combining(c)).lower().strip()


def keywords_from(text: str) -> list[str]:
    """Palavras significativas de um título/pergunta (para priorizar elementos, nunca para decidir)."""
    words = re.findall(r"[a-zA-ZÀ-ÿ]{4,}", text)
    out: list[str] = []
    for w in words:
        n = normalize(w)
        if n in STOPWORDS or normalize(w) in {normalize(s) for s in STOPWORDS}:
            continue
        stem = n[:6]
        if stem not in out:
            out.append(stem)
    return out


@dataclass
class Element:
    index: int
    ref: str
    role: str
    name: str
    value: str = ""
    attrs: str = ""
    options: list[str] = field(default_factory=list)
    context: str = ""
    href: str = ""

    @property
    def kind(self) -> str:
        if self.role in TYPEABLE or (self.role == "combobox" and "haspopup" not in self.attrs and not self.options):
            return "type"
        if self.role in SELECTABLE:
            return "select"
        return "click"

    @property
    def signature(self) -> str:
        """Identidade estável entre snapshots (os refs mudam a cada snapshot)."""
        return f"{self.role}|{normalize(self.name)}|{self.href}"

    def describe(self) -> str:
        s = f"{self.role} '{self.name}'" if self.name else self.role
        if self.value:
            s += f" · valor atual: '{self.value}'"
        if self.options:
            s += f" · opções: {self.options[:12]}"
        if self.context:
            s += f" · contexto: {self.context}"
        if self.href and self.href not in ("#", "") and not self.href.startswith("javascript"):
            s += f" · url: {self.href[:90]}"
        return s

    def line(self) -> str:
        return f"[{self.index}] {self.describe()}"


def parse_snapshot(text: str, max_elements: int = 600, base_url: str = "") -> list[Element]:
    """Extrai apenas os elementos interativos (com ref) do snapshot, na ordem do documento."""
    elements: list[Element] = []
    lines = text.splitlines()
    recent_text: list[str] = []
    for i, raw in enumerate(lines):
        m = LINE_RE.match(raw)
        if not m:
            continue
        attrs = m.group("attrs") or ""
        ref = REF_RE.search(attrs)
        rest_text = (m.group("rest") or "").strip().strip('"')
        role = m.group("role")
        if rest_text and role not in CLICKABLE | TYPEABLE | SELECTABLE | {"option"}:
            recent_text.append(rest_text[:80])
            recent_text = recent_text[-3:]
        if role == "heading" and m.group("name"):
            recent_text.append(m.group("name")[:80])
            recent_text = recent_text[-3:]
        if not ref:
            continue
        interactive = role in CLICKABLE | TYPEABLE | SELECTABLE or "cursor=pointer" in attrs
        if not interactive or role == "generic" and "cursor=pointer" not in attrs:
            continue
        rest = (m.group("rest") or "").strip()
        value = rest.strip('"') if rest and not rest.endswith(":") else ""
        name = (m.group("name") or "").replace('\\"', '"')
        if not name and role not in CLICKABLE | TYPEABLE | SELECTABLE and value and len(value) <= 80:
            # `generic [cursor=pointer]: Texto` — filtro/aba/card clicável sem papel ARIA: o texto é o nome
            name, value = value, ""
        context = " | ".join(recent_text) if name.lower() in GENERIC_NAMES or not name else ""
        el = Element(index=len(elements) + 1, ref=ref.group(1), role=role, name=name, value=value,
                     attrs=attrs, context=context)
        indent = len(raw) - len(raw.lstrip())
        for child in lines[i + 1:i + 3]:
            if not child.strip():
                continue
            mu = URL_RE.match(child)
            if mu and len(child) - len(child.lstrip()) > indent:
                href = mu.group(1).strip()
                if href and href != "#" and not href.startswith("javascript"):
                    el.href = urldefrag(urljoin(base_url, href) if base_url else href)[0]
            break
        if not name and not el.href and role not in CLICKABLE | TYPEABLE | SELECTABLE:
            continue  # wrapper genérico clicável sem nome: ruído (o link nomeado dentro dele já entra)
        if role in SELECTABLE:
            for child in lines[i + 1:]:
                cindent = len(child) - len(child.lstrip())
                if child.strip() and cindent <= indent:
                    break
                cm = LINE_RE.match(child)
                if cm and cm.group("role") == "option" and cm.group("name"):
                    el.options.append(cm.group("name"))
        elements.append(el)
        if len(elements) >= max_elements:
            break
    return elements


def prioritize(elements: list[Element], keywords: list[str], max_elements: int) -> list[Element]:
    """Deduplica (mesmo nome+url) e, se a tabela estoura o limite, mantém primeiro os elementos
    cujo nome/contexto contém palavras-chave do alvo. Reindexa a partir de 1."""
    seen: set[str] = set()
    unique: list[Element] = []
    for e in elements:
        sig = e.signature if e.name or e.href else f"{e.role}|{e.ref}"
        if sig in seen:
            continue
        seen.add(sig)
        unique.append(e)
    if len(unique) > max_elements and keywords:
        def hit(e: Element) -> bool:
            blob = normalize(e.name + " " + e.context + " " + e.href)
            return any(k in blob for k in keywords)
        unique = [e for e in unique if hit(e)] + [e for e in unique if not hit(e)]
    unique = unique[:max_elements]
    for i, e in enumerate(unique, 1):
        e.index = i
    return unique


def consent_button(elements: list[Element], snapshot: str) -> Element | None:
    """Botão de aceite de cookies / aviso, se a página parece ter um. Política em código."""
    has_dialog = bool(re.search(r"^\s*-\s+(dialog|alertdialog)\b", snapshot, re.M))
    if not (has_dialog or CONSENT_CONTEXT_RE.search(snapshot)):
        return None
    for e in elements:
        if e.role in ("button", "link") and normalize(e.name) in {normalize(n) for n in CONSENT_NAMES}:
            if normalize(e.name) in {"fechar", "close", "x", "×", "continuar", "prosseguir"} and not has_dialog:
                continue
            return e
    return None


def page_text(text: str, limit: int = 6000) -> str:
    """Texto legível do snapshot (para o planejador e a verificação), sem os refs."""
    out = []
    for raw in text.splitlines():
        if URL_RE.match(raw):
            continue
        s = REF_RE.sub("", raw)
        s = re.sub(r"\s*\[cursor=pointer\]", "", s)
        s = re.sub(r"\s*\[(active|level=\d+|aria-hidden|selected|checked|expanded|disabled)\]", "", s)
        s = re.sub(r"^(\s*-\s+)generic\s*:\s*", r"\1", s)
        s = re.sub(r"^(\s*-\s+)(generic|text|paragraph|listitem|list|main|navigation|region|contentinfo|banner|article|complementary|group|table|row|cell|rowgroup|columnheader)\s*:?\s*$", "", s)
        if s.strip() and s.strip() not in ("-", "- :"):
            out.append(s.rstrip())
    joined = "\n".join(out)
    return joined[:limit] + ("\n…(truncado)" if len(joined) > limit else "")


NAV_LINE_RE = re.compile(r"""^\s*-\s+(link|button|img|menuitem|tab|listitem|list|navigation|banner|contentinfo|menubar|menu|option|combobox|searchbox|textbox|checkbox|radio|switch)(\s+"[^"]*")?\s*(\[[^\]]*\]\s*)*:?\s*$""")


def content_text(text: str, limit: int = 4000) -> str:
    """Conteúdo principal da página: descarta linhas que são só link/botão/menu (navegação) e mantém
    títulos, parágrafos, textos, tabelas. É o que o Jev lê para julgar se a página É o alvo."""
    out = []
    for raw in text.splitlines():
        if URL_RE.match(raw) or NAV_LINE_RE.match(raw):
            continue
        s = REF_RE.sub("", raw)
        s = re.sub(r"\s*\[(cursor=pointer|active|level=\d+|aria-hidden|selected|checked|expanded|disabled)\]", "", s)
        s = re.sub(r"^\s*-\s+(generic|text|paragraph|main|region|article|group|complementary|section|document|status|figure)\s*:\s*", "- ", s)
        s = re.sub(r"^\s*-\s+(generic|paragraph|main|region|article|group|complementary|section|document|status|list|listitem|table|row|rowgroup|cell|columnheader|figure|separator)\s*:?\s*$", "", s)
        s = s.strip()
        if re.sub(r"[-:\"\s\u00a0\u200b\ufeff\u2800\u3000]+", "", s):
            out.append(s)
    joined = "\n".join(out)
    return joined[:limit] + ("\n…(truncado)" if len(joined) > limit else "")


# Papéis do snapshot que podem ser "itens" de uma contagem (filtros, colunas, opções, abas, links, títulos...).
COUNTABLE_ROLES = CLICKABLE | TYPEABLE | SELECTABLE | {"columnheader", "heading", "cell", "img", "row"}


def candidate_pairs(snapshot: str) -> list[tuple[str, str]]:
    """(chave, descrição) de cada elemento nomeado do snapshot que pode ser item de uma contagem
    (interativo ou estrutural: cabeçalho de coluna, opção, aba, título...), deduplicado por (papel, nome)."""
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for raw in snapshot.splitlines():
        m = LINE_RE.match(raw)
        if not m:
            continue
        role, attrs = m.group("role"), m.group("attrs") or ""
        name = (m.group("name") or "").replace('\\"', '"').strip()
        rest = (m.group("rest") or "").strip().strip('"')
        if not name and role == "generic" and "cursor=pointer" in attrs and rest and len(rest) <= 80:
            name = rest
        if role not in COUNTABLE_ROLES and not (role == "generic" and "cursor=pointer" in attrs):
            continue
        if not name or len(name) > 120:
            continue
        key = f"{role}|{normalize(name)}"
        if key in seen:
            continue
        seen.add(key)
        desc = f"{role} '{name}'"
        if rest and rest != name and role in SELECTABLE | TYPEABLE and len(rest) <= 60:
            desc += f" · valor atual: '{rest}'"
        out.append((key, desc))
    return out


def count_candidates(snapshot: str, keywords: list[str], max_candidates: int = 80,
                     revealed: list[str] | None = None) -> list[str]:
    """Candidatos para uma pergunta `contar`: os itens revelados ao abrir dropdowns/menus (se houver) vêm
    primeiro, seguidos dos elementos visíveis do snapshot. Se estourar o limite, mantém primeiro os que
    casam com palavras-chave da pergunta."""
    out = list(revealed or []) + [d for _k, d in candidate_pairs(snapshot)]
    if len(out) > max_candidates and keywords:
        def hit(desc: str) -> bool:
            return any(k in normalize(desc) for k in keywords)
        out = [c for c in out if hit(c)] + [c for c in out if not hit(c)]
    return out[:max_candidates]


# Papéis que costumam abrir listas escondidas (dropdown customizado, menu, acordeão, painel de filtros).
OPENER_ROLES = {"combobox", "button", "listbox", "menuitem", "tab", "treeitem", "switch"}
OPENER_HINT_RE = re.compile(r"expanded|haspopup|collapsed", re.I)


def opener_candidates(elements: list[Element], page_url: str, keywords: list[str], max_openers: int = 12
                      ) -> list[Element]:
    """Elementos da página-alvo que podem esconder itens de uma contagem: comboboxes customizados (sem
    `option` no snapshot), botões, abas, menus e wrappers clicáveis que não navegam para outra página.
    Links com URL própria ficam de fora (abrir = sair da página). Prioriza por palavras-chave."""
    base = page_url.rstrip("/")
    out: list[Element] = []
    for e in elements:
        if e.role in SELECTABLE and e.options:
            continue  # <select> nativo: as opções já estão no snapshot
        if e.href and e.href.rstrip("/") != base:
            continue
        if e.role in TYPEABLE and e.role != "combobox":
            continue
        if not (e.role in OPENER_ROLES or e.role == "generic" and "cursor=pointer" in e.attrs
                or OPENER_HINT_RE.search(e.attrs) or e.role == "link"):
            continue
        if not e.name or normalize(e.name) in {normalize(n) for n in CONSENT_NAMES}:
            continue
        out.append(e)

    def hit(e: Element) -> bool:
        blob = normalize(e.name + " " + e.context)
        return any(k in blob for k in keywords)
    out = [e for e in out if hit(e)] + [e for e in out if not hit(e)]
    return out[:max_openers]
