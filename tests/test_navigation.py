from __future__ import annotations

import tempfile
import unittest
from unittest.mock import AsyncMock, Mock

from jev_playwright.browser import PageState
from jev_playwright.checklist import Question, Section
from jev_playwright.config import Settings
from jev_playwright.elements import Element, focused_content, parse_snapshot
from jev_playwright.explorer import JevExplorer, SectionResult, parse_answer
from jev_playwright.llm_explorer import LlmExplorer
from jev_playwright.navigation import (
    Action,
    FamilyRecipeStore,
    Frontier,
    RecipeStore,
    search_queries,
    shortlist,
)

ROOT = "https://transparencia.exemplo.gov.br/"


def page(url=ROOT, title="Prefeitura Exemplo", snapshot="- heading \"Prefeitura Exemplo\"", elements=None):
    return PageState(url, title, snapshot, elements or [], set())


def signals(theme=0.1, exact=0.1, municipality=0.95, consultation=0.1, candidates=None):
    answers = {"chegou": {"noul": theme}, "exato": {"noul": exact}, "municipio": {"noul": municipality},
               "consulta": {"noul": consultation}, "acao": {"choice": "CLICK", "probabilities": {"CLICK": 1}}}
    answers.update({f"candidate_{key}": {"noul": probability} for key, probability in (candidates or {}).items()})
    return answers


class BrowserFake:
    def __init__(self, initial, routes=None):
        self.initial, self.current = initial, initial
        self.routes = routes or {}
        self.keywords, self.main_first = [], False
        self.actions = []

    async def close_extra_tabs(self): pass
    async def navigate(self, url): self.current = self.initial
    async def state(self): return self.current
    async def wait(self, seconds=0): pass
    async def dismiss_consent(self, p): return False
    async def focus_newest_tab(self): return False

    async def click(self, element):
        self.actions.append(("CLICK", element.name))
        destination = self.routes.get(element.name)
        if isinstance(destination, Exception):
            raise destination
        if destination:
            self.current = destination

    async def hover(self, element): await self.click(element)
    async def type(self, element, text, submit=False):
        self.actions.append(("SEARCH", text))
        destination = self.routes.get(text)
        if destination:
            self.current = destination


class JevFake:
    total_calls = 0
    total_cost = 0

    def __init__(self, decisions=None): self.decisions = decisions or {}
    def decide(self, state, questions):
        self.total_calls += 1
        if "perguntas" in state and "pagina_atual" not in state:
            kinds = {"1.1": "encontrar", "1.2": "cliques", "1.3": "julgar_pagina", "1.4": "contar"}
            return {key: {"choice": kinds[key]} for key in questions}
        return self.decisions.get(state.get("pagina_atual", {}).get("url"), signals())


class ChatFake:
    total_calls = 0
    def complete(self, *args, **kwargs):
        self.total_calls += 1
        return {"content": '{"operation":"CLICK","index":1}'}


class NavigationTests(unittest.TestCase):
    def test_semantic_state_ignores_refs_focus_and_clock(self):
        a = page(snapshot='- textbox "Ano" [ref=e1]: "2024"\n- text: 12:00:01')
        b = page(snapshot='- textbox "Ano" [ref=e95] [active]: "2024"\n- text: 12:00:59')
        self.assertEqual(a.semantic_hash, b.semantic_hash)
        self.assertNotEqual(a.semantic_hash, page(snapshot='- textbox "Ano": "2025"').semantic_hash)

    def test_signature_distinguishes_frames_and_scopes(self):
        elements = parse_snapshot('- region "A":\n  - button "Consultar" [ref=e1]\n- region "B":\n  - button "Consultar" [ref=f1e2]')
        self.assertEqual(len(elements), 2)
        self.assertNotEqual(elements[0].signature, elements[1].signature)

    def test_frontier_best_first_bounded_deduplicated(self):
        f = Frontier(2)
        a = Action("CLICK", "a", "a")
        b = Action("CLICK", "b", "b")
        c = Action("CLICK", "c", "c")
        f.add((a,), .2); f.add((b,), .9); f.add((c,), .7); f.add((b,), .9)
        self.assertEqual(f.pop(), (b,)); self.assertEqual(f.pop(), (c,)); self.assertIsNone(f.pop())

    def test_shortlist_retains_generic_menu(self):
        items = [Element(i, f"e{i}", "link", "Emendas " + str(i)) for i in range(10)]
        menu = Element(11, "e11", "button", "Receitas e Transferências")
        self.assertIn(menu, shortlist(items + [menu], "Emendas parlamentares", 6))

    def test_query_variation_and_deduplication(self):
        queries = search_queries("Existência de seção de emendas parlamentares federais")
        self.assertIn("emendas parlamentares", queries)
        self.assertEqual(len(queries), len(set(queries)))

    def test_recipes_scope_validation_and_invalidation(self):
        with tempfile.TemporaryDirectory() as directory:
            store = RecipeStore(directory, ROOT, "Emendas", "Exemplo/UF", "audit")
            route = (Action("CLICK", "signature", "menu", ROOT + "emendas", expected_state="hash"),)
            store.save(route)
            self.assertEqual(store.load(2), route)
            self.assertIsNone(store.load(0))
            self.assertIsNone(RecipeStore(directory, ROOT, "Emendas", "Outra/UF", "audit").load(2))
            store.path.write_text('{"version":1,"route":[{"operation":"EVAL","signature":"x","name":"x"}]}')
            self.assertIsNone(store.load(2))
            store.invalidate(); self.assertFalse(store.path.exists())

    def test_supplier_recipe_uses_current_links_never_cached_municipality_urls(self):
        with tempfile.TemporaryDirectory() as directory:
            a = Element(1, 'f1e2', 'link', 'Emendas federais', href='https://a.nucleogov.com.br/ente/1/emendas')
            route = (Action.from_element('CLICK', a, 'state', origin_url='https://a.nucleogov.com.br/ente/1/'),)
            store = FamilyRecipeStore(directory, a.href, 'Emendas', 'collect')
            store.save(route)
            b = Element(2, 'f9e99', 'link', 'Emendas federais', href='https://b.nucleogov.com.br/ente/2/emendas')
            other = FamilyRecipeStore(directory, b.href, 'Emendas', 'collect')
            self.assertEqual(other.match([b], [])[0][1].href, b.href)
            self.assertNotIn('/ente/1', store.path.read_text())
            self.assertEqual(other.match([b, b], []), [])

    def test_main_frame_prefix_changes_do_not_change_signature(self):
        a = parse_snapshot('- main:\n  - link "Consulta" [ref=e2]:\n    - /url: /consulta', base_url=ROOT)[0]
        b = parse_snapshot('- main:\n  - link "Consulta" [ref=f3e99]:\n    - /url: /consulta', base_url=ROOT)[0]
        self.assertEqual(a.signature, b.signature)

    def test_retrieval_keeps_relevant_tail_before_truncation(self):
        snapshot = '\n'.join(['- paragraph: informações gerais sem relação ' + str(i) for i in range(100)])
        snapshot += '\n- heading "Emendas parlamentares federais"\n- paragraph: Prefeitura Exemplo disponibiliza a consulta'
        text = focused_content(snapshot, ['emenda', 'parlam', 'feder'], 500)
        self.assertIn('Emendas parlamentares federais', text)
        self.assertLessEqual(len(text), 500)

    def test_fractional_or_nonfinite_answers_not_coerced_to_integers(self):
        for value in [1.5, float('nan'), float('inf'), '2.5', '2 cliques']:
            self.assertIsNone(parse_answer(value))
        self.assertEqual(parse_answer(2.0), 2)
        self.assertEqual(parse_answer('0'), 0)

    def test_invalid_settings_fail_early(self):
        for kwargs in ({"max_pages_per_find": 0}, {"search_strategy": "unknown"}, {"collect": True}, {"max_depth": -1}):
            with self.assertRaises(ValueError): Settings("test", **kwargs)


class GraphTests(unittest.IsolatedAsyncioTestCase):
    def explorer(self, browser, decisions=None, **kwargs):
        settings = Settings("test", **kwargs)
        return JevExplorer(settings, browser, JevFake(decisions), ChatFake(), ROOT, log=lambda *args: None, ente="Exemplo/UF")

    async def test_inconclusive_never_returns_negative_or_zero(self):
        browser = BrowserFake(page())
        explorer = self.explorer(browser)
        section = Section("1", "Emendas federais", [Question(f"1.{i}", "Pergunta") for i in range(1, 5)])
        result = await explorer.run_section(section)
        self.assertEqual(result.status, "busca_inconclusiva")
        self.assertEqual([q.answer for q in result.questions], ["indeterminado"] * 4)
        self.assertTrue(all(q.confidence == 0 and q.confidence_kind == "unavailable" for q in result.questions))

    async def test_theme_without_qualifiers_is_not_success(self):
        explorer = self.explorer(BrowserFake(page()), {ROOT: signals(.99, .2)})
        result = SectionResult("1", "Emendas federais", "Emendas federais")
        self.assertIsNone(await explorer.find(result.target, [], result))
        self.assertFalse(result.found)

    async def test_best_first_replays_and_reaches_verified_external_root(self):
        external = "https://fornecedor.example/"
        link = Element(1, "e1", "link", "Portal de emendas", href=external, in_nav=True)
        initial = page(elements=[link])
        destination = page(external, snapshot='- heading "Emendas federais Prefeitura Exemplo"')
        browser = BrowserFake(initial, {link.name: destination})
        explorer = self.explorer(browser, {ROOT: signals(candidates={1: .95}), external: signals(.95, .95)})
        result = SectionResult("1", "Emendas federais", "Emendas federais")
        self.assertIsNotNone(await explorer.find(result.target, [], result))
        self.assertEqual(result.final_url, external)
        self.assertEqual(result.clicks, 1)
        self.assertEqual(result.status, "encontrado")

    async def test_wrong_municipality_external_not_expanded(self):
        external = "https://fornecedor.example/"
        link = Element(1, "e1", "link", "Consulta", href=external)
        browser = BrowserFake(page(elements=[link]), {link.name: page(external)})
        explorer = self.explorer(browser, {ROOT: signals(candidates={1: .9}), external: signals(.99, .99, .1)})
        result = SectionResult("1", "Emendas", "Emendas")
        self.assertIsNone(await explorer.find(result.target, [], result))
        self.assertFalse(result.found)

    async def test_baseline_stops_on_captcha_without_inference(self):
        browser = BrowserFake(page(snapshot='- text: CAPTCHA', elements=[Element(i, f'e{i}', 'button', str(i)) for i in range(20)]))
        chat = ChatFake()
        explorer = LlmExplorer(Settings('fake'), browser, chat, ROOT, log=lambda *args: None)
        explorer._tools_schema = AsyncMock(return_value=[])
        result = await explorer.run_section(Section('1', 'Emendas', [Question('1.1', 'Existe?')]))
        self.assertEqual(result.status, 'bloqueado')
        self.assertEqual(result.questions[0].answer, 'indeterminado')
        self.assertEqual(chat.total_calls, 0)

    async def test_collect_requires_real_consultation(self):
        explorer = self.explorer(BrowserFake(page()), {ROOT: signals(.99, .99, .99, .1)}, collect=True, objective="collect")
        result = SectionResult("1", "Emendas", "Emendas")
        self.assertIsNone(await explorer.find(result.target, [], result))

    async def test_documents_discovered_without_browser_navigation(self):
        document = Element(1, "e1", "link", "Emendas PDF", href=ROOT + "emendas.pdf")
        browser = BrowserFake(page(elements=[document]))
        explorer = self.explorer(browser, {ROOT: signals(candidates={1: .99})})
        result = SectionResult("1", "Emendas", "Emendas")
        await explorer.find(result.target, [], result)
        self.assertEqual(result.sources[0]["url"], document.href)
        self.assertEqual(browser.actions, [])

    async def test_action_errors_bounded(self):
        link = Element(1, "e1", "link", "Menu", href=ROOT + "menu")
        browser = BrowserFake(page(elements=[link]), {link.name: RuntimeError("Timeout")})
        explorer = self.explorer(browser, {ROOT: signals(candidates={1: .99})}, max_action_errors=1)
        result = SectionResult("1", "Emendas", "Emendas")
        await explorer.find(result.target, [], result)
        self.assertEqual(result.action_errors, 1)
        self.assertEqual(result.stop_reason, "limite_erros_de_acao")

    async def test_timeout_that_navigated_still_verifies_destination(self):
        target = ROOT + 'emendas'
        link = Element(1, 'e1', 'link', 'Emendas', href=target)
        browser = BrowserFake(page(elements=[link]))
        async def delayed_click(element):
            browser.current = page(target, snapshot='- heading "Emendas federais"')
            raise RuntimeError('Timeout after navigation')
        browser.click = delayed_click
        explorer = self.explorer(browser, {ROOT: signals(candidates={1: .99}), target: signals(.99, .99)}, max_action_errors=1)
        result = SectionResult('1', 'Emendas', 'Emendas')
        self.assertIsNotNone(await explorer.find(result.target, [], result))
        self.assertTrue(result.found)
        self.assertEqual(result.action_errors, 1)

    async def test_challenge_stops_and_does_not_fallback(self):
        browser = BrowserFake(page(snapshot="- text: CAPTCHA verify you are human"))
        explorer = self.explorer(browser, fallback=True)
        result = SectionResult("1", "Emendas", "Emendas")
        await explorer.find(result.target, [], result)
        self.assertEqual(result.status, "bloqueado")
        self.assertEqual(explorer.chat.total_calls, 0)

    async def test_search_query_not_repeated_after_incidental_change(self):
        field = Element(1, "e1", "searchbox", "Busca")
        initial = page(elements=[field])
        browser = BrowserFake(initial, {query: page(ROOT, snapshot='- text: resultado ' + query, elements=[field])
                                        for query in search_queries("Emendas federais")})
        explorer = self.explorer(browser, {ROOT: signals()}, max_pages_per_find=8)
        original = explorer._decide
        def decide(*args, **kwargs):
            return original(*args, **kwargs) | {"alvo_busca": {"probabilities": {"1": 1}, "choice": "1"}}
        explorer._decide = decide
        result = SectionResult("1", "Emendas federais", "Emendas federais")
        await explorer.find(result.target, [], result)
        # Replay may repeat ancestors; no winning route nests the same query on this URL.
        queries = [value for operation, value in browser.actions if operation == 'SEARCH']
        self.assertEqual(len(queries), len(set(queries)))
        self.assertLessEqual(result.pages_visited, 8)
        self.assertLessEqual(len(browser.actions), 10)

    async def test_stale_recipe_falls_back_to_discovery(self):
        with tempfile.TemporaryDirectory() as directory:
            store = RecipeStore(directory, ROOT, "Emendas", "Exemplo/UF", "audit")
            store.save((Action("CLICK", "missing", "old", expected_state="stale"),))
            explorer = self.explorer(BrowserFake(page()), {ROOT: signals(.99, .99)}, use_recipes=True, recipe_dir=directory)
            result = SectionResult("1", "Emendas", "Emendas")
            self.assertIsNotNone(await explorer.find(result.target, [], result))
            self.assertTrue(any("invalidada" in line for line in result.trace))

    async def test_fallback_only_proposes_allowlisted_action(self):
        link = Element(1, "e1", "link", "Menu", href=ROOT + "menu")
        explorer = self.explorer(BrowserFake(page(elements=[link])), fallback=True)
        result = SectionResult("1", "Emendas", "Emendas")
        proposal = await explorer._propose_fallback(result.target, explorer.browser.current, result)
        self.assertEqual(proposal[0][0], "CLICK")
        self.assertFalse(result.found)
        self.assertEqual(await explorer._propose_fallback(result.target, explorer.browser.current, result), [])

    async def test_visual_fallback_is_opt_in_and_does_not_declare_success(self):
        link = Element(1, 'e1', 'link', 'Menu', href=ROOT + 'menu')
        browser = BrowserFake(page(elements=[link]))
        browser.screenshot_data = AsyncMock(return_value='data:image/png;base64,AAAA')
        explorer = self.explorer(browser, fallback=True, fallback_vision=True)
        explorer.chat.complete = Mock(return_value={'content':'{"operation":"CLICK","index":1}'})
        result = SectionResult('1', 'Emendas', 'Emendas')
        proposal = await explorer._propose_fallback(result.target, browser.current, result)
        self.assertTrue(proposal)
        browser.screenshot_data.assert_awaited_once()
        messages = explorer.chat.complete.call_args.args[0]
        self.assertEqual(messages[1]['content'][1]['type'], 'image_url')
        self.assertFalse(result.found)

    async def test_count_candidate_budget_abstains(self):
        explorer = self.explorer(BrowserFake(page()), max_count_candidates=1)
        explorer._reveal = AsyncMock(return_value=([], []))
        number, *_ = await explorer._count('Quantos?', 'Emendas', page(snapshot='- heading "A"\n- heading "B"'))
        self.assertIsNone(number)

    async def test_missing_count_candidates_abstains_without_llm(self):
        explorer = self.explorer(BrowserFake(page()))
        explorer._reveal = AsyncMock(return_value=([], []))
        number, confidence, evidence, source = await explorer._count("Quantos formatos?", "Emendas", page(snapshot='- paragraph: Sem controles enumeráveis'))
        self.assertIsNone(number)
        self.assertEqual(source, "unavailable")
        self.assertEqual(explorer.chat.total_calls, 0)
