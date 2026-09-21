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
# Subárvores de navegação do site (cabeçalho, menus, rodapé): não são conteúdo da página-alvo.
NAV_SUBTREE_ROLES = {"banner", "navigation", "contentinfo"}
# Placeholders de painéis ainda carregando (Power BI, Looker...): o snapshot foi tirado cedo demais.
LOADING_RE = re.compile(r"carregando|loading|aguarde|please wait", re.I)


def iter_snapshot(text: str):
    """Gera (índice, linha, dentro_de_navegação) para cada linha não vazia do snapshot, seguindo a
    indentação: tudo abaixo de um nó banner/navigation/contentinfo é navegação. Conteúdo de iframes
    (refs `f2e10`) vem aninhado como qualquer outro nó e é tratado como conteúdo normal."""
    nav_indent: int | None = None
    for i, raw in enumerate(text.splitlines()):
        if not raw.strip():
            continue
        indent = len(raw) - len(raw.lstrip())
        if nav_indent is not None and indent <= nav_indent:
            nav_indent = None
        m = LINE_RE.match(raw)
        if nav_indent is None and m and m.group("role") in NAV_SUBTREE_ROLES:
            nav_indent = indent
        yield i, raw, nav_indent is not None


def _child_text(lines: list[str], i: int, indent: int, max_lines: int = 8) -> str:
    """Texto do primeiro filho de texto de um nó (`- generic: Exportar xlsx`, `- text: ...`, `- img "..."`).
    É o que distingue elementos cujo nome ARIA é genérico ou repetido."""
    for child in lines[i + 1:i + 1 + max_lines]:
        if not child.strip():
            continue
        if len(child) - len(child.lstrip()) <= indent:
            break
        if URL_RE.match(child):
            continue
        cm = LINE_RE.match(child)
        if not cm:
            continue
        txt = (cm.group("rest") or "").strip().strip('"') or (cm.group("name") or "").strip()
        if txt and len(txt) <= 60 and not LOADING_RE.search(txt):
            return txt
    return ""


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
    detail: str = ""      # texto do filho, usado quando o nome ARIA é genérico ou repetido na página
    in_nav: bool = False  # dentro de banner/navigation/contentinfo

    @property
    def kind(self) -> str:
        if self.role in TYPEABLE or (self.role == "combobox" and "haspopup" not in self.attrs and not self.options):
            return "type"
        if self.role in SELECTABLE:
            return "select"
        return "click"

    @property
    def signature(self) -> str:
        """Identidade estável entre snapshots (os refs mudam a cada snapshot). O `detail` entra quando
        o nome sozinho não distingue o elemento (dois links "Clique para exportar" → xlsx e csv)."""
        s = f"{self.role}|{normalize(self.name)}|{self.href}"
        return f"{s}|{normalize(self.detail)}" if self.detail else s

    def describe(self) -> str:
        # texto visível na frente: o nome ARIA de botões de painel costuma ser boilerplate ("Clique para exportar")
        if self.detail:
            s = f"{self.role} '{self.detail}' · nome acessível: '{self.name}'"
        else:
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
    nav_of = {i: in_nav for i, _raw, in_nav in iter_snapshot(text)}
    # nomes que se repetem na página (mesmo papel + nome): precisam do texto do filho para se distinguir
    counts: dict[str, int] = {}
    for raw in lines:
        m = LINE_RE.match(raw)
        if m and m.group("name") and REF_RE.search(m.group("attrs") or ""):
            k = f"{m.group('role')}|{normalize(m.group('name'))}"
            counts[k] = counts.get(k, 0) + 1
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
        indent = len(raw) - len(raw.lstrip())
        if not name and role not in CLICKABLE | TYPEABLE | SELECTABLE and value and len(value) <= 80:
            # `generic [cursor=pointer]: Texto` — filtro/aba/card clicável sem papel ARIA: o texto é o nome
            name, value = value, ""
        if not name and role in CLICKABLE:
            # link/botão sem nome ARIA cujo filho é uma imagem ou texto (`- img "emendas banner 2026"`):
            # o texto do filho é o nome, senão o Jev vê só "link · url: ..."
            name = _child_text(lines, i, indent)
        context = " | ".join(recent_text) if name.lower() in GENERIC_NAMES or not name else ""
        el = Element(index=len(elements) + 1, ref=ref.group(1), role=role, name=name, value=value,
                     attrs=attrs, context=context, in_nav=nav_of.get(i, False))
        if name and (normalize(name) in GENERIC_NAMES or counts.get(f"{role}|{normalize(name)}", 0) > 1):
            detail = _child_text(lines, i, indent)
            if detail and normalize(detail) != normalize(name):
                el.detail = detail
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


def prioritize(elements: list[Element], keywords: list[str], max_elements: int,
               main_first: bool = False) -> list[Element]:
    """Deduplica (mesmo nome+url) e, se a tabela estoura o limite, mantém primeiro os elementos
    cujo nome/contexto contém palavras-chave do alvo. Com `main_first` (página-alvo já encontrada), os
    elementos de navegação do site ficam por último, para o conteúdo (inclusive iframes) não ser cortado.
    Reindexa a partir de 1."""
    seen: set[str] = set()
    unique: list[Element] = []
    for e in elements:
        sig = e.signature if e.name or e.href else f"{e.role}|{e.ref}"
        if sig in seen:
            continue
        seen.add(sig)
        unique.append(e)
    if len(unique) > max_elements:
        def hit(e: Element) -> bool:
            blob = normalize(e.name + " " + e.detail + " " + e.context + " " + e.href)
            return any(k in blob for k in keywords)
        def rank(e: Element) -> tuple[int, int]:
            return (0 if keywords and hit(e) else 1, 1 if main_first and e.in_nav else 0)
        unique = sorted(unique, key=rank)  # estável: mantém a ordem do documento dentro de cada grupo
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
    títulos, parágrafos, textos, tabelas. É o que o Jev lê para julgar se a página É o alvo.
    Cabeçalho/menus/rodapé do site (banner, navigation, contentinfo) ficam de fora, exceto títulos;
    placeholders de carga ("Os visuais estão carregando...") também."""
    out = []
    for _i, raw, in_nav in iter_snapshot(text):
        if URL_RE.match(raw) or NAV_LINE_RE.match(raw):
            continue
        if in_nav and not re.match(r"^\s*-\s+heading\b", raw):
            continue
        if LOADING_RE.search(raw) and len(raw.strip()) < 80:
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


def _options_below(lines: list[str], i: int, indent: int) -> list[str]:
    """Nomes das `option` aninhadas sob a linha i (listbox/combobox)."""
    out: list[str] = []
    for child in lines[i + 1:]:
        if child.strip() and len(child) - len(child.lstrip()) <= indent:
            break
        cm = LINE_RE.match(child)
        if cm and cm.group("role") == "option" and cm.group("name"):
            out.append(cm.group("name"))
    return out


def candidate_pairs(snapshot: str, keywords: list[str] | None = None, collapse_over: int = 0,
                    skip_nav: bool = True) -> list[tuple[str, str]]:
    """(chave, descrição) de cada elemento nomeado do snapshot que pode ser item de uma contagem
    (interativo ou estrutural: cabeçalho de coluna, opção, aba, título...), deduplicado por (papel, nome).

    - Navegação do site (banner/menus/rodapé) fica de fora com `skip_nav`.
    - Nomes genéricos ou repetidos ganham o texto do filho ("Clique para exportar" → "Exportar xlsx" /
      "Exportar csv"), que também entra na chave, para não colapsarem num só.
    - Um listbox com mais de `collapse_over` opções (slicer de parlamentares num painel) vira UM candidato
      "listbox 'X' · N opções" e as opções são puladas — a não ser que alguma palavra-chave da pergunta case
      com o nome do listbox ou com uma opção, caso em que as opções são exatamente o que se conta."""
    lines = snapshot.splitlines()
    nav_flags = {i: in_nav for i, _raw, in_nav in iter_snapshot(snapshot)}
    counts: dict[str, int] = {}
    for raw in lines:
        m = LINE_RE.match(raw)
        if m and m.group("name"):
            k = f"{m.group('role')}|{normalize(m.group('name'))}"
            counts[k] = counts.get(k, 0) + 1
    kws = keywords or []
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    skip_indent: int | None = None
    for i, raw in enumerate(lines):
        m = LINE_RE.match(raw)
        if not m:
            continue
        indent = len(raw) - len(raw.lstrip())
        if skip_indent is not None:
            if indent > skip_indent:
                continue
            skip_indent = None
        if skip_nav and nav_flags.get(i, False):
            continue
        role, attrs = m.group("role"), m.group("attrs") or ""
        name = (m.group("name") or "").replace('\\"', '"').strip()
        rest = (m.group("rest") or "").strip().strip('"')
        if not name and role == "generic" and "cursor=pointer" in attrs and rest and len(rest) <= 80:
            name = rest
        if role not in COUNTABLE_ROLES and not (role == "generic" and "cursor=pointer" in attrs):
            continue
        if not name or len(name) > 120 or LOADING_RE.search(name) and len(name) < 60:
            continue
        detail = ""
        if normalize(name) in GENERIC_NAMES or counts.get(f"{role}|{normalize(name)}", 0) > 1:
            detail = _child_text(lines, i, indent)
            if normalize(detail) == normalize(name):
                detail = ""
        key = f"{role}|{normalize(name)}" + (f"|{normalize(detail)}" if detail else "")
        if key in seen:
            continue
        seen.add(key)
        desc = f"{role} '{detail}' · nome acessível: '{name}'" if detail else f"{role} '{name}'"
        if role in SELECTABLE and collapse_over:
            options = _options_below(lines, i, indent)
            if len(options) > collapse_over:
                blob = normalize(name + " " + " ".join(options))
                if not any(k in blob for k in kws):
                    out.append((key, f"{desc} · {len(options)} opções (colapsadas)"))
                    skip_indent = indent
                    continue
        if rest and rest != name and role in SELECTABLE | TYPEABLE and len(rest) <= 60:
            desc += f" · valor atual: '{rest}'"
        out.append((key, desc))
    return out


def count_candidates(snapshot: str, keywords: list[str], max_candidates: int = 80,
                     revealed: list[str] | None = None, collapse_over: int = 0) -> list[str]:
    """Candidatos para uma pergunta `contar`: os itens revelados ao abrir dropdowns/menus (se houver) vêm
    primeiro, seguidos dos elementos visíveis do snapshot (sem a navegação do site). Se estourar o
    limite, mantém primeiro os que casam com palavras-chave da pergunta."""
    out = list(revealed or []) + [d for _k, d in candidate_pairs(snapshot, keywords, collapse_over)]
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
        if e.in_nav:
            continue  # menus do site não escondem itens da página-alvo
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
        blob = normalize(e.name + " " + e.detail + " " + e.context)
        return any(k in blob for k in keywords)
    out = [e for e in out if hit(e)] + [e for e in out if not hit(e)]
    return out[:max_openers]
