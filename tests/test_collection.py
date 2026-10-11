import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from jev_playwright.collection import (
    Collector,
    download_public,
    money,
    normalized_fields,
    parse_document,
    public_source,
    resolve_public,
    table_rows,
)


class ParserTests(unittest.TestCase):
    def test_csv_portuguese_decimal_and_provenance_location(self):
        parsed = parse_document('Número da emenda;Autor;Valor pago\n123;Maria;1.234,56\n'.encode(), 'text/csv', 'https://example.org/file.csv', 10, 100000)
        self.assertEqual(parsed.rows[0]['location'], 'csv:row:2')
        fields = normalized_fields(parsed.rows[0]['raw'])
        self.assertEqual(fields['valor_pago'], '1234.56')
        self.assertEqual(fields['identificador'], '123')
        self.assertIsNone(fields['valor_empenhado'])

    def test_money_no_floats_or_guessed_stage(self):
        self.assertEqual(money('R$ 1.234,50'), '1234.50')
        self.assertIsNone(money('não informado'))
        self.assertIsNone(money('NaN'))
        self.assertIsNone(normalized_fields({'valor': 1234})['valor_pago'])

    def test_json_arrays_keep_path_and_warn_pagination(self):
        parsed = parse_document(b'{"data":[{"emenda":"7"}],"total":100}', 'application/json', 'https://example.org/api', 5, 100000)
        self.assertEqual(parsed.rows[0]['location'], '$.data[0]')
        self.assertTrue(parsed.warnings)

    def test_json_record_limit(self):
        parsed = parse_document(json.dumps([{'id': i} for i in range(4)]).encode(), 'application/json', 'https://example.org/api', 2, 100000)
        self.assertEqual(len(parsed.rows), 2)
        self.assertTrue(parsed.truncated)

    def test_duplicate_headers_preserved(self):
        parsed = table_rows([['Valor', 'Valor'], [1, 2]], 10, 'sheet')
        self.assertEqual(parsed.rows[0]['raw'], {'Valor': 1, 'Valor [2]': 2})

    def test_csv_record_limit(self):
        parsed = parse_document(b'Autor,Emenda\na,1\nb,2\nc,3', 'text/csv', 'https://example.org/e.csv', 2, 100000)
        self.assertEqual(len(parsed.rows), 2)
        self.assertTrue(parsed.truncated)

    def test_xlsx_read_only(self):
        from openpyxl import Workbook
        book = Workbook()
        book.active.append(['Emenda', 'Valor pago']); book.active.append(['12', '5,00'])
        out = io.BytesIO(); book.save(out)
        parsed = parse_document(out.getvalue(), 'application/octet-stream', 'https://example.org/e.xlsx', 10, 1000000)
        self.assertEqual(parsed.rows[0]['raw']['Emenda'], '12')
        self.assertIn('row:2', parsed.rows[0]['location'])

    def test_scanned_pdf_signals_need_for_ocr(self):
        from pypdf import PdfWriter
        writer = PdfWriter(); writer.add_blank_page(width=100, height=100)
        out = io.BytesIO(); writer.write(out)
        parsed = parse_document(out.getvalue(), 'application/pdf', 'https://example.org/e.pdf', 10, 100000)
        self.assertEqual(parsed.rows, [])
        self.assertTrue(any('OCR' in warning for warning in parsed.warnings))

    def test_public_source_no_credentials_tokens_private_scope_or_ports(self):
        hosts = {'example.org'}
        self.assertTrue(public_source('https://example.org/a.csv', hosts))
        for url in ['file:///etc/passwd', 'https://user:password@example.org/a', 'https://example.org/a?token=secret',
                    'https://example.org:8080/a', 'https://other.org/a', 'https://example.org:invalid/a']:
            self.assertFalse(public_source(url, hosts))


class CollectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_download_pins_ip_and_preserves_tls_hostname(self):
        requests = []
        def handler(request):
            requests.append(request)
            return httpx.Response(200, headers={'content-type': 'text/csv'}, content=b'Emenda,Autor\n1,Ana')
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with patch('jev_playwright.collection.httpx.AsyncClient', return_value=client), \
             patch('jev_playwright.collection.resolve_public', AsyncMock(return_value='93.184.216.34')):
            data, mime, final = await download_public('https://example.org/a.csv', {'example.org'}, 1000)
        self.assertEqual(requests[0].url.host, '93.184.216.34')
        self.assertEqual(requests[0].headers['host'], 'example.org')
        self.assertEqual(requests[0].extensions['sni_hostname'], 'example.org')
        self.assertNotIn('authorization', requests[0].headers)
        self.assertEqual(final, 'https://example.org/a.csv')

    async def test_download_redirect_cannot_expand_host_authority(self):
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(302, headers={'location':'https://attacker.org/file.csv'})))
        with patch('jev_playwright.collection.httpx.AsyncClient', return_value=client), \
             patch('jev_playwright.collection.resolve_public', AsyncMock(return_value='93.184.216.34')):
            with self.assertRaisesRegex(ValueError, 'escopo'):
                await download_public('https://example.org/a.csv', {'example.org'}, 1000)

    async def test_download_byte_limit(self):
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, headers={'content-type':'text/csv'}, content=b'12345')))
        with patch('jev_playwright.collection.httpx.AsyncClient', return_value=client), \
             patch('jev_playwright.collection.resolve_public', AsyncMock(return_value='93.184.216.34')):
            with self.assertRaisesRegex(ValueError, 'limite'):
                await download_public('https://example.org/a.csv', {'example.org'}, 3)

    async def test_dns_local_and_mixed_rejected(self):
        for addresses in [['127.0.0.1'], ['93.184.216.34', '192.168.1.1'], ['::1']]:
            records = [(0, 0, 0, '', (address, 443)) for address in addresses]
            with patch('asyncio.base_events.BaseEventLoop.getaddrinfo', new=AsyncMock(return_value=records)):
                with self.assertRaises(ValueError): await resolve_public('example.org', 443)

    async def test_dns_public_accepted(self):
        with patch('asyncio.base_events.BaseEventLoop.getaddrinfo', new=AsyncMock(return_value=[(0, 0, 0, '', ('93.184.216.34', 443))])):
            self.assertEqual(await resolve_public('example.org', 443), '93.184.216.34')

    async def test_collect_raw_artifact_hash_manifest_and_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            collector = Collector(Path(directory), 2, 100000, 10)
            source = {'url': 'https://example.org/e.csv', 'kind': 'document', 'origin': 'https://example.org/'}
            downloader = AsyncMock(return_value=(b'Emenda,Autor\n12,Ana', 'text/csv', source['url']))
            with patch('jev_playwright.collection.download_public', downloader):
                summary = await collector.collect([source], [], source['origin'], {'example.org'}, AsyncMock(return_value=True))
            manifest = json.loads((Path(directory) / 'collection.json').read_text())
            self.assertEqual(summary['records'], 1)
            self.assertEqual(manifest['records'][0]['fields']['autor'], 'Ana')
            provenance = manifest['records'][0]['provenance'][0]
            self.assertEqual(provenance['location'], 'csv:row:2')
            self.assertTrue((Path(directory) / provenance['artifact']).exists())
            self.assertFalse(summary['coverage']['records_complete'])

    async def test_irrelevant_content_not_published_as_records(self):
        with tempfile.TemporaryDirectory() as directory:
            source = {'url': 'https://example.org/e.csv', 'kind': 'document'}
            with patch('jev_playwright.collection.download_public', AsyncMock(return_value=(b'Emenda,Autor\n12,Ana', 'text/csv', source['url']))):
                summary = await Collector(Path(directory), 2, 10000, 10).collect([source], [], source['url'], {'example.org'}, AsyncMock(return_value=False))
            self.assertEqual(summary['records'], 0)
            self.assertEqual(summary['sources'][0]['status'], 'pending_review')

    async def test_bad_file_isolated_and_next_source_collected(self):
        with tempfile.TemporaryDirectory() as directory:
            sources = [{'url': 'https://example.org/a.pdf', 'kind': 'document'}, {'url': 'https://example.org/b.csv', 'kind': 'document'}]
            values = [(b'%PDF-broken', 'application/pdf', sources[0]['url']), (b'Emenda,Autor\n1,Ana', 'text/csv', sources[1]['url'])]
            with patch('jev_playwright.collection.download_public', AsyncMock(side_effect=values)):
                summary = await Collector(Path(directory), 2, 100000, 10).collect(sources, [], sources[0]['url'], {'example.org'})
            self.assertEqual(summary['sources'][0]['status'], 'pending')
            self.assertEqual(summary['records'], 1)

    async def test_identical_rows_without_identifier_not_collapsed(self):
        with tempfile.TemporaryDirectory() as directory:
            tables = [{'index': 0, 'rows': [['Autor', 'Valor pago'], ['Ana', 100], ['Ana', 100]]}]
            summary = await Collector(Path(directory), 2, 100000, 10).collect([], tables, 'https://example.org/', {'example.org'})
            self.assertEqual(summary['records'], 2)

    async def test_same_identified_record_merges_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            tables = [{'index': index, 'rows': [['Emenda', 'Autor'], ['1', 'Ana']]} for index in range(2)]
            summary = await Collector(Path(directory), 2, 100000, 10).collect([], tables, 'https://example.org/', {'example.org'})
            manifest = json.loads((Path(directory) / 'collection.json').read_text())
            self.assertEqual(summary['records'], 1)
            self.assertEqual(len(manifest['records'][0]['provenance']), 2)
