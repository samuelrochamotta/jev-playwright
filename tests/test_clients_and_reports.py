import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

import compare
from jev_playwright.browser import Browser
from jev_playwright.config import Settings
from jev_playwright.explorer import QuestionResult, SectionResult
from jev_playwright.jev_client import JevClient
from jev_playwright.report import write_reports


class ClientTests(unittest.TestCase):
    def client(self, handler):
        client = JevClient('fake-key', 'fake-model')
        client._http.close()
        client._http = httpx.Client(base_url='https://example.org', transport=httpx.MockTransport(handler))
        self.addCleanup(client.close)
        return client

    def test_missing_answer_raises_instead_of_false(self):
        client = self.client(lambda req: httpx.Response(200, json={'answers': {}}))
        with self.assertRaisesRegex(RuntimeError, 'incompletas'):
            client.decide({}, {'q': JevClient.noul('Test')})

    def test_invalid_probability_raises(self):
        for value in [None, True, -0.1, 1.1, '0.9']:
            client = self.client(lambda req, value=value: httpx.Response(200, json={'answers': {'q': {'noul': value}}}))
            with self.assertRaises(RuntimeError): client.decide({}, {'q': JevClient.noul('Test')})

    def test_rate_limit_retry_and_effective_model(self):
        calls = []
        def handler(request):
            calls.append(request)
            if len(calls) == 1:
                return httpx.Response(429, headers={'retry-after': '0'})
            return httpx.Response(200, json={'model': 'versioned-model', 'answers': {'q': {'noul': .9}}, 'usage': {'cost': .01}})
        client = self.client(handler)
        with patch('jev_playwright.jev_client.time.sleep'):
            self.assertEqual(client.decide({}, {'q': JevClient.noul('Test')})['q']['noul'], .9)
        self.assertEqual(len(calls), 2)
        self.assertEqual(client.model_version, 'versioned-model')
        self.assertEqual(client.total_cost, .01)

    def test_http_error_does_not_expose_body(self):
        client = self.client(lambda req: httpx.Response(401, text='secret credential'))
        with self.assertRaisesRegex(RuntimeError, '^Jev HTTP 401$'):
            client.decide({}, {'q': JevClient.noul('Test')})

    def test_gold_host_path_and_www(self):
        gold = compare.load_gold('checklists/gold.json', 'https://www.goiania.go.gov.br/transparencia/')
        self.assertEqual(gold['1.2'], '2')

    def test_report_preserves_inconclusive_and_provenance_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            result = SectionResult('1', 'Emendas', 'Emendas', stop_reason='orcamento_paginas_esgotado')
            result.questions.append(QuestionResult('1.1', 'Existe?', 'encontrar', 'indeterminado', 0, confidence_kind='unavailable'))
            json_path, md_path = write_reports('Checklist', 'https://example.org/', [result], directory, {})
            data = json.loads(json_path.read_text())
            self.assertEqual(data['results'][0]['status'], 'busca_inconclusiva')
            self.assertFalse(data['results'][0]['coverage']['search_complete'])
            self.assertNotIn('❌ não', md_path.read_text())


class AsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_browser_json_transport_parsing(self):
        browser = Browser()
        browser.call = AsyncMock(return_value='### Result\n[{"rows": []}]\n### Ran Playwright code\n...')
        self.assertEqual(await browser.evaluate_json('() => []'), [{'rows': []}])
        browser.call = AsyncMock(return_value='### Result\n```json\n{"ok":true}\n```\n### Snapshot\n...')
        self.assertEqual(await browser.evaluate_json('() => ({})'), {'ok': True})

    async def test_network_observation_only_get(self):
        browser = Browser()
        browser.call = AsyncMock(return_value='[1] GET https://example.org/api => [200]\n[2] POST https://example.org/login => [200]')
        self.assertEqual(await browser.network_sources(), ['https://example.org/api'])

    async def test_compare_accuracy_uses_every_run(self):
        with tempfile.TemporaryDirectory() as directory:
            checklist = Path(directory) / 'checklist.md'
            checklist.write_text('URL: https://example.org/\n# 1 Emendas\n1.1 - Existe?')
            gold = Path(directory) / 'gold.json'
            gold.write_text('{"example.org":{"1.1":"sim"}}')
            results = [
                {'respostas': {'1.1': {'resposta': answer}}, 'erros': 0}
                for answer in ['sim', 'indeterminado', 'sim', 'sim']]
            stdout = io.StringIO()
            with patch('sys.argv', ['compare.py', str(checklist), '--gold', str(gold), '--runs', '2']), \
                 patch('compare.load_settings', return_value=Settings('fake')), \
                 patch('compare.run', new=AsyncMock(side_effect=results)), contextlib.redirect_stdout(stdout):
                await compare.main()
            self.assertIn('jev 1/2', stdout.getvalue())
            self.assertIn('llm-only 2/2', stdout.getvalue())
            self.assertIn('Concordância jev × llm-only: 1/2', stdout.getvalue())
