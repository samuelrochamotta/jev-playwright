"""Cliente do Playwright MCP (servidor oficial @playwright/mcp via stdio)."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .elements import (
    LOADING_RE,
    Element,
    consent_button,
    content_text,
    page_text,
    parse_snapshot,
    prioritize,
)

SNAP_RE = re.compile(r"```yaml\n(.*?)```", re.S)
URL_RE = re.compile(r"- Page URL: (.*)")
TITLE_RE = re.compile(r"- Page Title: (.*)")
TAB_RE = re.compile(r"^-\s*(\d+):\s*(\(current\))?\s*\[(.*?)\]\((.*?)\)\s*$", re.M)
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/128.0.0.0 Safari/537.36")
CHALLENGE_RE = re.compile(r"captcha|visitante leg[ií]timo|digite o c[oó]digo que aparece|verify you are (a )?human|"
                          r"n[aã]o sou um rob[oô]|access denied|acesso negado|request blocked|attention required", re.I)
ACTIVE_RE = re.compile(r"\s*\[active\]")  # marcador de foco: muda a cada clique sem mudar a página
DOC_EXT = (".pdf", ".xls", ".xlsx", ".csv", ".ods", ".json", ".doc", ".docx", ".zip")
MCP_VERSION = "0.0.83"


@dataclass
class PageState:
    url: str
    title: str
    snapshot: str
    elements: list[Element]
    hrefs: set[str] = field(default_factory=set)

    def summary(self, text_limit: int = 6000) -> str:
        return f"URL: {self.url}\nTítulo: {self.title}\n\nConteúdo:\n{page_text(self.snapshot, text_limit)}"

    def content(self, text_limit: int = 4000) -> str:
        """Só o conteúdo principal (sem navegação)."""
        return content_text(self.snapshot, text_limit)

    @property
    def hash(self) -> str:
        """Identidade do estado da página. Ignora `[active]` (foco): um clique que só move o foco, como num
        botão-envoltório ou num item de menu que nada faz, não é uma página nova."""
        return hashlib.sha1((self.url + "\n" + ACTIVE_RE.sub("", self.snapshot)).encode()).hexdigest()[:12]

    @property
    def semantic_hash(self) -> str:
        """State identity without ephemeral refs/focus; includes route, filters and frame context."""
        snap = re.sub(r"\[ref=[A-Za-z0-9_]+\]", "", ACTIVE_RE.sub("", self.snapshot))
        snap = re.sub(r"\b\d{1,2}:\d{2}:\d{2}\b", "<clock>", snap)
        snap = re.sub(r"[ \t]+", " ", snap)
        return hashlib.sha256((self.url + "\n" + snap).encode()).hexdigest()[:20]

    @property
    def is_challenge(self) -> bool:
        """Página de desafio anti-robô (captcha/WAF): a exploração não pode prosseguir por aqui."""
        explicit = re.search(r"captcha|verify you are (a )?human|visitante leg[ií]timo|digite o c[oó]digo que aparece|n[aã]o sou um rob[oô]", self.snapshot, re.I)
        return bool(explicit) or (bool(CHALLENGE_RE.search(self.snapshot)) and len(self.elements) < 12)

    @property
    def loading(self) -> int:
        """Placeholders de carga no snapshot ("Os visuais estão carregando..."): painéis embutidos ainda
        montando. Zero quando a página está pronta."""
        return sum(1 for line in self.snapshot.splitlines() if LOADING_RE.search(line) and len(line.strip()) < 80)

    @property
    def is_document(self) -> bool:
        return self.url.lower().split("?")[0].endswith(DOC_EXT) or (not self.snapshot.strip() and bool(self.url))

    def element_table(self) -> str:
        return "\n".join(e.line() for e in self.elements) or "(nenhum elemento interativo)"

    def find(self, signature: str) -> Element | None:
        return next((e for e in self.elements if e.signature == signature), None)


class Browser:
    def __init__(self, headless: bool = True, max_elements: int = 120) -> None:
        # --isolated: perfil novo a cada execução. Sem isso o MCP reutiliza um perfil persistente
        # e estado antigo (cookies/localStorage) pode fazer o site ignorar cliques.
        args = ["-y", f"@playwright/mcp@{MCP_VERSION}", "--browser", "chrome", "--isolated",
                "--user-agent", UA, "--ignore-https-errors", "--timeout-action", "15000"]
        if headless:
            args.append("--headless")
        self._params = StdioServerParameters(command="npx", args=args)
        self._max_elements = max_elements
        self._session: ClientSession | None = None
        self.keywords: list[str] = []          # priorização de elementos quando a tabela estoura
        self.main_first = False                # página-alvo encontrada: navegação do site por último na tabela
        self._consent_seen: set[str] = set()

    async def __aenter__(self) -> "Browser":
        self._stdio = stdio_client(self._params)
        read, write = await self._stdio.__aenter__()
        self._session_cm = ClientSession(read, write)
        self._session = await self._session_cm.__aenter__()
        await self._session.initialize()
        return self

    async def __aexit__(self, *exc) -> None:
        try:
            await self._session_cm.__aexit__(*exc)
        finally:
            await self._stdio.__aexit__(*exc)

    async def call(self, tool: str, **args) -> str:
        assert self._session
        res = await self._session.call_tool(tool, args)
        text = "\n".join(c.text for c in res.content if getattr(c, "text", None))
        if getattr(res, "is_error", None) or getattr(res, "isError", False):
            raise RuntimeError(f"{tool} falhou: {text[:400]}")
        return text

    async def navigate(self, url: str) -> None:
        last: Exception | None = None
        for attempt in range(3):
            try:
                await self.call("browser_navigate", url=url)
                last = None
                break
            except RuntimeError as exc:
                last = exc
                if "Timeout" in str(exc) or "timeout" in str(exc):
                    break  # a página pode ter carregado parcialmente; seguimos com o snapshot
                await self.wait(1.5 * (attempt + 1))
        if last is not None and "imeout" not in str(last):
            raise last
        await self.wait(0.5)

    async def state(self) -> PageState:
        text = await self.call("browser_snapshot")
        m = SNAP_RE.search(text)
        snap = m.group(1) if m else text
        url = (URL_RE.search(text) or [None, ""])[1].strip()
        title = (TITLE_RE.search(text) or [None, ""])[1].strip()
        all_elements = parse_snapshot(snap, 800, base_url=url)
        hrefs = {e.href for e in all_elements if e.href}
        elements = prioritize(all_elements, self.keywords, self._max_elements, self.main_first)
        return PageState(url=url, title=title, snapshot=snap, elements=elements, hrefs=hrefs)

    async def dismiss_consent(self, page: PageState) -> bool:
        """Fecha banner de cookies/aviso, no máximo uma vez por página (hash)."""
        if page.url in self._consent_seen:
            return False
        btn = consent_button(page.elements, page.snapshot)
        if not btn:
            return False
        self._consent_seen.add(page.url)
        try:
            await self.click(btn)
            await self.wait(0.5)
            return True
        except RuntimeError:
            return False

    async def click(self, el: Element) -> None:
        await self.call("browser_click", element=el.describe()[:120], target=el.ref)

    async def hover(self, el: Element) -> None:
        await self.call("browser_hover", element=el.describe()[:120], target=el.ref)

    async def type(self, el: Element, text: str, submit: bool = False) -> None:
        await self.call("browser_type", element=el.describe()[:120], target=el.ref, text=text, submit=submit)

    async def select(self, el: Element, value: str) -> None:
        await self.call("browser_select_option", element=el.describe()[:120], target=el.ref, values=[value])

    async def scroll(self, down: bool = True) -> None:
        await self.call("browser_press_key", key="PageDown" if down else "PageUp")

    async def press(self, key: str) -> None:
        await self.call("browser_press_key", key=key)

    async def scroll_into_view(self, el: Element) -> None:
        """Rola o contêiner até o elemento (listas virtualizadas só renderizam o que está visível)."""
        await self.call("browser_evaluate", function="(el) => el.scrollIntoView({block: 'end'})",
                        element=el.describe()[:120], target=el.ref)

    async def back(self) -> None:
        await self.call("browser_navigate_back")

    async def wait(self, seconds: float = 1.5) -> None:
        await self.call("browser_wait_for", time=seconds)

    async def screenshot(self, filename: str) -> None:
        await self.call("browser_take_screenshot", filename=filename)

    async def screenshot_data(self) -> str:
        """Opt-in multimodal context. Never interpreted as proof of successful collection."""
        assert self._session
        response = await self._session.call_tool("browser_take_screenshot", {"type": "png"})
        for content in response.content:
            if getattr(content, "type", None) == "image":
                mime = getattr(content, "mime_type", None) or getattr(content, "mimeType", "image/png")
                if len(content.data) > 8_000_000:
                    raise RuntimeError("Screenshot excede limite do fallback visual")
                return f"data:{mime};base64,{content.data}"
        raise RuntimeError("MCP não retornou imagem para fallback visual")

    async def evaluate_json(self, function: str, element: Element | None = None):
        """Read-only observations. Tool text is transport, not a JSON API contract."""
        import json
        args = {"element": element.describe()[:120], "target": element.ref} if element else {}
        text = await self.call("browser_evaluate", function=function, **args)
        # MCP returns a Result section followed by code/snapshot metadata.
        result = text.split("### Result", 1)[-1].split("###", 1)[0].strip()
        result = re.sub(r"^```(?:json)?\s*|\s*```$", "", result).strip()
        try:
            value = json.loads(result)
            return json.loads(value) if isinstance(value, str) else value
        except (ValueError, TypeError):
            raise RuntimeError("browser_evaluate não retornou JSON válido") from None

    async def tables(self, limit: int = 1000) -> list[dict]:
        page = await self.state()
        frames: dict[str, Element | None] = {"main": None}
        for element in page.elements:
            if 'iframe' in element.scope:
                logical_frame = '/'.join(part for part in element.scope.split('/') if part.startswith('iframe'))
                frames.setdefault(logical_frame, element)
        result = []
        body = ("Array.from(doc.querySelectorAll('table')).slice(0,20).map((t,index) => ({"
                "index, rows:Array.from(t.rows).slice(0," + str(limit + 2) + ").map(r => "
                "Array.from(r.cells).slice(0,100).map(c => c.innerText.trim().slice(0,2000))),"
                "truncated:t.rows.length>" + str(limit + 1) + "}))")
        for frame, element in frames.items():
            function = ("(el) => { const doc=el.ownerDocument; return " if element else
                        "() => { const doc=document; return ") + body + "; }"
            try:
                tables = await self.evaluate_json(function, element)
                for table in tables:
                    table["index"] = f"{frame}:{table['index']}"
                result.extend(tables)
            except RuntimeError:
                result.append({"index": frame, "rows": [], "truncated": True, "warning": "Frame não observado"})
        return result

    async def network_sources(self) -> list[str]:
        """Observe GET URLs only; never export headers, credentials or request bodies."""
        try:
            text = await self.call("browser_network_requests")
        except RuntimeError:
            return []
        return list(dict.fromkeys(re.findall(r"\bGET\s+(https?://[^\s\]<>]+)", text)))

    # ---- abas ---------------------------------------------------------------------------

    async def tabs(self) -> list[tuple[int, bool, str]]:
        try:
            text = await self.call("browser_tabs", action="list")
        except RuntimeError:
            return []
        return [(int(i), bool(cur), url) for i, cur, _t, url in TAB_RE.findall(text)]

    async def focus_newest_tab(self) -> bool:
        """Se um clique abriu nova aba, passa a trabalhar nela. Devolve True se trocou."""
        tabs = await self.tabs()
        if len(tabs) <= 1:
            return False
        newest = tabs[-1]
        if newest[1]:
            return True
        await self.call("browser_tabs", action="select", index=newest[0])
        await self.wait(0.5)
        return True

    async def close_new_tabs(self, before: list[tuple[int, bool, str]]) -> None:
        """Fecha só as abas abertas depois de `before` e volta para a aba que estava ativa (a de trabalho
        pode não ser a primeira: a página-alvo pode ter sido aberta numa nova aba)."""
        tabs = await self.tabs()
        if len(tabs) <= len(before):
            return
        for i, _cur, _url in reversed(tabs[len(before):]):
            try:
                await self.call("browser_tabs", action="close", index=i)
            except RuntimeError:
                pass
        current = next((i for i, cur, _u in before if cur), None)
        if current is not None:
            try:
                await self.call("browser_tabs", action="select", index=current)
            except RuntimeError:
                pass

    async def close_extra_tabs(self) -> None:
        tabs = await self.tabs()
        for i, _cur, _url in reversed(tabs[1:]):
            try:
                await self.call("browser_tabs", action="close", index=i)
            except RuntimeError:
                pass
        if len(tabs) > 1:
            try:
                await self.call("browser_tabs", action="select", index=0)
            except RuntimeError:
                pass
