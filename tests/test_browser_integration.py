"""Opt-in contract test: real Chrome/MCP against local fixtures, no paid API calls."""
import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from jev_playwright.browser import Browser
from jev_playwright.checklist import Question, Section
from jev_playwright.config import Settings
from jev_playwright.explorer import JevExplorer


class FixtureJev:
    total_calls = 0
    total_cost = 0
    def decide(self, state, questions):
        self.total_calls += 1
        answers = {}
        for key, question in questions.items():
            if question['type'] == 'choice':
                choice = 'encontrar' if key == '1.1' else next(iter(question['criteria']))
                answers[key] = {'choice': choice, 'probabilities': {choice: 1}}
            else:
                value = .99
                if key in {'chegou', 'exato', 'consulta'} and not state.get('pagina_atual', {}).get('url', '').endswith('/target'):
                    value = .1
                if key.startswith('candidate_'):
                    value = .9
                if 'fontes' in state:
                    value = .1  # network fixtures are HTML, not reusable JSON endpoints
                answers[key] = {'noul': value}
        return answers


class NoPaidChat:
    total_calls = 0
    def complete(self, *args, **kwargs):
        raise AssertionError('This test must never use a paid model')


class FixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        frame = self.path == '/frame'
        html = '''<!doctype html><title>Prefeitura Exemplo</title>
        <main><h1>Emendas federais — Prefeitura Exemplo</h1>
        <table><tr><th>Emenda</th><th>Valor pago</th></tr><tr><td>123</td><td>1.234,56</td></tr></table>
        <a href="/target" target="_blank">Consulta</a><input aria-label="Busca">
        <button onclick="this.textContent='Aberto'">Menu</button></main>'''
        if not frame and self.path != '/target':
            html += '<iframe title="Dados" src="/frame"></iframe>'
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(html.encode())

    def log_message(self, *args): pass


@unittest.skipUnless(os.environ.get('JEV_BROWSER_TESTS') == '1', 'Set JEV_BROWSER_TESTS=1 for real Chrome/MCP')
class BrowserIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_graph_collection_pipeline_with_real_browser_and_fake_decisions(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), FixtureHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                async with Browser() as browser:
                    url = f'http://127.0.0.1:{server.server_port}/'
                    settings = Settings('fake', collect=True, objective='collect', max_pages_per_find=4)
                    explorer = JevExplorer(settings, browser, FixtureJev(), NoPaidChat(), url,
                                           log=lambda *args: None, ente='Exemplo/UF', collection_dir=Path(directory))
                    result = await explorer.run_section(Section('1', 'Emendas federais', [Question('1.1', 'Existe seção?')]))
                    self.assertFalse(result.error, result.error)
                    self.assertEqual(result.status, 'encontrado', result.trace)
                    self.assertEqual(result.collection['records'], 1)
                    manifest = json.loads(Path(result.collection['manifest']).read_text())
                    self.assertEqual(manifest['records'][0]['fields']['valor_pago'], '1234.56')
                    self.assertFalse(manifest['coverage']['records_complete'])
        finally:
            server.shutdown(); server.server_close(); thread.join()

    async def test_snapshot_tables_frames_click_and_tabs_contract(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), FixtureHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            async with Browser() as browser:
                await browser.navigate(f'http://127.0.0.1:{server.server_port}/')
                await browser.wait(1)
                state = await browser.state()
                self.assertEqual(state.title, 'Prefeitura Exemplo')
                self.assertTrue(state.url.startswith('http://127.0.0.1:'))
                self.assertTrue(any(e.name == 'Consulta' for e in state.elements))
                self.assertTrue((await browser.screenshot_data()).startswith('data:image/'))
                tables = await browser.tables(10)
                self.assertGreaterEqual(len(tables), 2)
                self.assertEqual(tables[0]['rows'][1], ['123', '1.234,56'])
                state = await browser.state()
                link = next(e for e in state.elements if e.name == 'Consulta' and not e.frame)
                await browser.click(link)
                await browser.wait(.5)
                self.assertTrue(await browser.focus_newest_tab())
                self.assertTrue((await browser.state()).url.endswith('/target'))
        finally:
            server.shutdown(); server.server_close(); thread.join()
