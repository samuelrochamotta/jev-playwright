"""Lê uma lista de entes em CSV para rodar o mesmo checklist em lote.

    ente,uf,url
    Aracaju,SE,https://transparencia.aracaju.se.gov.br/prefeitura/
    Salvador,BA,https://www.transparencia.salvador.ba.gov.br/#/

A coluna `uf` é opcional; `ente` e `uf` viram o nome `Ente/UF`, que nomeia a pasta de resultados.
Linhas sem URL são ignoradas.
"""
from __future__ import annotations

import csv
from pathlib import Path

ALIASES = {"ente": "ente", "municipio": "ente", "município": "ente", "nome": "ente",
           "uf": "uf", "estado": "uf", "url": "url", "site": "url", "portal": "url"}


def _norm(name: str) -> str:
    return ALIASES.get((name or "").strip().lower().lstrip("﻿"), "")


def load_entes(path: str | Path) -> list[tuple[str, str]]:
    """Devolve pares (url, "Ente/UF") na ordem do arquivo."""
    text = Path(path).read_text(encoding="utf-8-sig")
    reader = csv.DictReader(text.splitlines())
    cols = {_norm(c): c for c in (reader.fieldnames or []) if _norm(c)}
    if "url" not in cols or "ente" not in cols:
        raise SystemExit(f"{path}: o CSV precisa das colunas 'ente' e 'url' (cabeçalho: {reader.fieldnames})")

    pairs: list[tuple[str, str]] = []
    for row in reader:
        url = (row.get(cols["url"]) or "").strip()
        ente = (row.get(cols["ente"]) or "").strip()
        uf = (row.get(cols["uf"]) or "").strip() if "uf" in cols else ""
        if not url or url.startswith("#"):
            continue
        pairs.append((url, f"{ente}/{uf}" if ente and uf else ente))
    if not pairs:
        raise SystemExit(f"{path}: nenhuma linha com URL")
    return pairs


def filter_entes(pairs: list[tuple[str, str]], only: str | None) -> list[tuple[str, str]]:
    """Mantém só os entes cujo nome contém um dos termos de `only` (separados por vírgula)."""
    if not only:
        return pairs
    termos = [t.strip().lower() for t in only.split(",") if t.strip()]
    kept = [(u, e) for u, e in pairs if any(t in (e or u).lower() for t in termos)]
    if not kept:
        raise SystemExit(f"--ente-filtro {only!r} não casou com nenhum ente da lista")
    return kept
