"""Public, bounded, read-only collection. No session credentials or CAPTCHA bypass.

DNS is resolved and pinned to a public IP for each connection; redirects must remain
on an explicitly authorized host. Raw artifacts are immutable by content hash.
"""
from __future__ import annotations

import asyncio
import csv
import io
import ipaddress
import json
import re
import socket
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from urllib.parse import parse_qsl, urljoin, urlparse

import httpx

from .elements import normalize
from .navigation import web_url

SENSITIVE = re.compile(r"token|secret|password|senha|authorization|session|cookie|api.?key|access.?key|credential|signature", re.I)
ALLOWED_MIME = {"application/json", "text/csv", "text/plain", "text/html", "application/pdf",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "application/vnd.ms-excel", "application/octet-stream"}
ALIASES = {
    "identificador": {"id emenda", "numero emenda", "numero da emenda", "codigo emenda", "emenda"},
    "autor": {"autor", "parlamentar", "autor da emenda"},
    "ano": {"ano", "exercicio"}, "objeto": {"objeto", "descricao"},
    "municipio": {"municipio", "cidade"}, "orgao": {"orgao", "unidade gestora"},
    "beneficiario": {"beneficiario", "favorecido"}, "esfera": {"esfera", "origem da emenda"},
    "data": {"data", "data pagamento", "data de pagamento"},
    "valor_previsto": {"valor previsto", "valor aprovado"},
    "valor_transferido": {"valor transferido", "valor recebido"},
    "valor_empenhado": {"valor empenhado"}, "valor_liquidado": {"valor liquidado"},
    "valor_pago": {"valor pago"},
}


def public_source(url: str, hosts: set[str]) -> bool:
    if not web_url(url):
        return False
    p = urlparse(url)
    try:
        if p.port not in {None, 80, 443}:
            return False
    except ValueError:
        return False
    return (p.hostname or "").lower() in hosts and not any(SENSITIVE.search(k) for k, _ in parse_qsl(p.query))


async def resolve_public(host: str, port: int) -> str:
    entries = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    addresses = list(dict.fromkeys(item[4][0] for item in entries))
    if not addresses or any(not ipaddress.ip_address(a).is_global for a in addresses):
        raise ValueError("Destino privado/local ou DNS misto não autorizado")
    return addresses[0]


async def download_public(url: str, hosts: set[str], max_bytes: int) -> tuple[bytes, str, str]:
    current = url
    async with httpx.AsyncClient(timeout=30, trust_env=False, follow_redirects=False) as client:
        for _ in range(4):
            if not public_source(current, hosts):
                raise ValueError("URL/redirect fora do escopo público autorizado")
            parsed = urlparse(current)
            host = parsed.hostname.lower()
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            ip = await resolve_public(host, port)
            pinned = httpx.URL(current).copy_with(host=ip)
            headers = {"Host": host, "User-Agent": "jev-playwright-public-research/1.0"}
            async with client.stream("GET", pinned, headers=headers, extensions={"sni_hostname": host}) as response:
                if response.is_redirect:
                    current = urljoin(current, response.headers.get("location", ""))
                    continue
                response.raise_for_status()
                mime = response.headers.get("content-type", "").split(";", 1)[0].lower()
                if mime and mime not in ALLOWED_MIME and not mime.endswith('+json'):
                    raise ValueError(f"MIME não suportado: {mime}")
                if int(response.headers.get("content-length", 0)) > max_bytes:
                    raise ValueError("Arquivo excede limite de bytes")
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > max_bytes:
                        raise ValueError("Arquivo excede limite de bytes")
                    chunks.append(chunk)
                return b"".join(chunks), mime, current
    raise ValueError("Limite de redirects excedido")


def money(value) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    text = re.sub(r"[R$\s\u00a0]", "", str(value)).strip()
    if not re.fullmatch(r"-?[\d.,]+", text):
        return None
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    elif isinstance(value, str) and re.fullmatch(r"-?\d{1,3}(?:\.\d{3})+", text):
        text = text.replace(".", "")
    try:
        number = Decimal(text)
        return str(number) if number.is_finite() else None
    except InvalidOperation:
        return None


def date_value(value) -> str | None:
    if isinstance(value, datetime):
        return value.date().isoformat()
    for pattern in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(str(value), pattern).date().isoformat()
        except ValueError:
            pass
    return None


def normalized_fields(raw: dict) -> dict:
    fields = {key: None for key in ALIASES}
    for key, value in raw.items():
        label = " ".join(normalize(str(key)).replace("_", " ").split())
        for field_name, aliases in ALIASES.items():
            if label in aliases:
                # Do not infer financial stage from a generic "valor" column.
                if field_name == "data":
                    fields[field_name] = date_value(value)
                else:
                    fields[field_name] = money(value) if field_name.startswith("valor_") else (str(value) if value is not None else None)
    return fields


@dataclass
class Parsed:
    rows: list[dict] = field(default_factory=list)
    passages: list[dict] = field(default_factory=list)
    truncated: bool = False
    warnings: list[str] = field(default_factory=list)


def decode_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            pass
    return data.decode("utf-8", errors="replace")


def table_rows(rows: list[list], limit: int, location: str) -> Parsed:
    result = Parsed()
    if not rows:
        return result
    # Duplicate column labels are retained instead of silently overwritten by dict().
    labels = []
    for i, value in enumerate(rows[0]):
        label = str(value or f"column_{i + 1}")
        if label in labels:
            label = f"{label} [{i + 1}]"
        labels.append(label)
    for number, row in enumerate(rows[1:limit + 1], 2):
        if any(value not in ("", None) for value in row):
            raw = {labels[i] if i < len(labels) else f"column_{i + 1}": value for i, value in enumerate(row)}
            result.rows.append({"raw": raw, "location": f"{location}:row:{number}"})
    result.truncated = len(rows) > limit + 1
    return result


def parse_document(data: bytes, mime: str, url: str, limit: int, max_bytes: int) -> Parsed:
    parsed_url = urlparse(url)
    suffix = parsed_url.path.lower()
    for _, value in parse_qsl(parsed_url.query):
        if value.lower().endswith(('.pdf', '.csv', '.xlsx', '.json')):
            suffix = value.lower()
            break
    json_body = data.lstrip(b'\xef\xbb\xbf \r\n\t')[:1] in {b'{', b'['}
    if mime == "application/json" or mime.endswith('+json') or suffix.endswith(".json") or json_body:
        payload = json.loads(decode_text(data), parse_float=Decimal)
        # Conservative: collect arrays of objects, do not guess pagination endpoints or financial fields.
        arrays = []
        def walk(value, path="$", depth=0):
            if depth > 8:
                return
            if isinstance(value, list) and all(isinstance(item, dict) for item in value):
                arrays.append((path, value))
            elif isinstance(value, dict):
                for key, child in value.items():
                    walk(child, f"{path}.{key}", depth + 1)
        walk(payload)
        result = Parsed()
        for path, values in arrays:
            for i, row in enumerate(values):
                if len(result.rows) >= limit:
                    result.truncated = True
                    break
                result.rows.append({"raw": row, "location": f"{path}[{i}]"})
        result.warnings.append("Completude da paginação JSON não verificada")
        return result
    if mime == "application/pdf" or data.startswith(b"%PDF"):
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        result = Parsed()
        for number, page in enumerate(reader.pages[:50], 1):
            text = page.extract_text() or ""
            if text.strip():
                result.passages.append({"page": number, "text": text[:20000]})
            else:
                result.warnings.append(f"Página {number} sem texto: OCR/revisão necessária")
        result.truncated = len(reader.pages) > 50
        # Text is evidence, not an invented table of financial records.
        result.warnings.append("PDF extraído como evidência textual; registros estruturados não inferidos")
        return result
    if suffix.endswith(".xlsx") or "spreadsheetml" in mime:
        from openpyxl import load_workbook
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if sum(item.file_size for item in archive.infolist()) > max_bytes * 10:
                raise ValueError("Planilha excede limite descompactado")
        book = load_workbook(io.BytesIO(data), read_only=True, data_only=True, keep_links=False)
        result = Parsed()
        try:
            for sheet in book:
                rows = []
                for row in sheet.iter_rows(max_col=min(100, sheet.max_column or 100), values_only=True):
                    rows.append(list(row))
                    if len(rows) >= limit + 2:
                        break
                parsed = table_rows(rows, max(0, limit - len(result.rows)), sheet.title)
                result.rows.extend(parsed.rows)
                result.truncated |= parsed.truncated
        finally:
            book.close()
        return result
    if suffix.endswith(".csv") or mime == "text/csv":
        text = decode_text(data)
        try:
            dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        rows = []
        for row in csv.reader(io.StringIO(text), dialect):
            rows.append(row)
            if len(rows) >= limit + 2:
                break
        return table_rows(rows, limit, "csv")
    raise ValueError("Formato não suportado pelo extrator (XLS/ODS/HTML estático exigem outro parser)")


class Collector:
    def __init__(self, directory: Path, max_sources: int, max_bytes: int, max_records: int) -> None:
        self.directory = directory
        self.max_sources, self.max_bytes, self.max_records = max_sources, max_bytes, max_records

    async def collect(self, sources: list[dict], tables: list[dict], page_url: str, hosts: set[str], verifier=None) -> dict:
        self.directory.mkdir(parents=True, exist_ok=True)
        records, passages, collected = [], [], []
        seen = set()
        def append_rows(parsed: Parsed, provenance: dict):
            for row in parsed.rows:
                fields = normalized_fields(row["raw"])
                # Without a stable emenda identifier, identical amounts/names may be distinct records.
                identity = row["raw"] if fields["identificador"] else [provenance.get("url"), row["location"], row["raw"]]
                fingerprint = sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
                evidence = provenance | {"location": row["location"]}
                if fingerprint in seen:
                    existing = next(r for r in records if r["fingerprint"] == fingerprint)
                    if evidence not in existing["provenance"]:
                        existing["provenance"].append(evidence)
                    continue
                if len(records) >= self.max_records:
                    break
                seen.add(fingerprint)
                records.append({"fingerprint": fingerprint, "raw": row["raw"],
                                "fields": fields, "provenance": [evidence],
                                "relevance_probability": row.get("relevance_probability")})
        for table in tables:
            if table.get("warning"):
                collected.append({"url": page_url, "table": table["index"], "status": "pending", "reason": table["warning"]})
                continue
            parsed = table_rows(table.get("rows", []), self.max_records, f"html:table:{table['index']}")
            parsed.truncated |= table.get("truncated", False)
            if verifier and not await verifier(parsed, {"url": page_url, "kind": "rendered_html"}):
                collected.append({"url": page_url, "table": table["index"], "status": "pending_review"})
                continue
            raw_table = json.dumps(table, ensure_ascii=False, default=str).encode()
            digest = sha256(raw_table).hexdigest()
            artifact = self.directory / f"{digest}.json"
            artifact.write_bytes(raw_table)
            append_rows(parsed, {"url": page_url, "kind": "rendered_html", "sha256": digest, "artifact": artifact.name})
            collected.append({"url": page_url, "kind": "rendered_html", "table": table["index"],
                              "status": "collected", "truncated": parsed.truncated, "sha256": digest, "artifact": artifact.name})
        for source in sources[:self.max_sources]:
            item = dict(source)
            try:
                data, mime, final = await download_public(source["url"], hosts, self.max_bytes)
                digest = sha256(data).hexdigest()
                artifact = self.directory / f"{digest}.bin"
                artifact.write_bytes(data)
                parsed = await asyncio.to_thread(parse_document, data, mime, final, self.max_records, self.max_bytes)
                artifact_name = artifact.name
                item.update({"status": "collected", "final_url": final, "mime": mime, "sha256": digest,
                             "artifact": artifact_name, "truncated": parsed.truncated, "warnings": parsed.warnings})
                if verifier and not await verifier(parsed, item):
                    item["status"] = "pending_review"
                    collected.append(item)
                    continue
                append_rows(parsed, {"url": final, "sha256": digest, "artifact": artifact_name, "kind": source["kind"]})
                passages.extend({**passage, "url": final, "sha256": digest, "artifact": artifact_name} for passage in parsed.passages)
            except Exception as exc:  # isolate malformed files and parser-specific exceptions per source
                # Never log response bodies or request headers, which may contain credentials.
                item.update({"status": "pending", "error": type(exc).__name__, "reason": str(exc)[:160]})
            collected.append(item)
        manifest = {"records": records, "passages": passages, "sources": collected,
                    "coverage": {"records_complete": False, "pagination_verified": False, "organs_verified": False,
                                 "record_limit_reached": len(records) >= self.max_records,
                                 "source_limit_reached": len(sources) > self.max_sources},
                    "status": "partial" if records or passages else "inconclusive"}
        (self.directory / "collection.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return {"status": manifest["status"], "records": len(records), "passages": len(passages),
                "sources": collected, "coverage": manifest["coverage"], "manifest": str(self.directory / "collection.json")}
