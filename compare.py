"""Roda o mesmo checklist nos dois modos (jev × llm-only) e compara lado a lado.

    python compare.py checklists/checklist.md --url URL1 --ente "Ente 1/UF" --url URL2 --ente "Ente 2/UF" --gold checklists/gold.json [--only 1] [--runs 3]

O gabarito (--gold) é um JSON {"<host ou URL>": {"1.1": "sim", "1.2": "2"}} ou, para um só site, {"1.1": ...}.
As respostas são comparadas como booleano (sim/não) ou inteiro. Com gabarito, imprime a acurácia de cada modo;
sem ele, imprime a concordância entre os modos.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from urllib.parse import urlparse

from jev_playwright.checklist import load_checklist
from jev_playwright.config import load_settings
from jev_playwright.explorer import parse_answer
from run_checklist import pair_urls_entes, run

ROWS = [("alvos_encontrados", "Alvos encontrados"), ("tempo_total_s", "Tempo total (s)"), ("paginas", "Páginas visitadas"),
        ("chamadas_llm", "Chamadas ao LLM"), ("tokens_llm", "Tokens do LLM"), ("tempo_llm_s", "Tempo no LLM (s)"),
        ("custo_llm_usd", "Custo do LLM (US$)"), ("chamadas_jev", "Chamadas ao Jev"), ("custo_jev_usd", "Custo do Jev (US$)")]


def load_gold(path: str | None, url: str) -> dict[str, str]:
    if not path:
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    host = urlparse(url).netloc
    for key in (url, host, url.rstrip("/")):
        if key in data:
            return {k: str(v) for k, v in data[key].items()}
    if all(not isinstance(v, dict) for v in data.values()):
        return {k: str(v) for k, v in data.items()}
    return {}


def same(a, b) -> bool:
    """Igualdade tipada: ambos booleanos iguais ou ambos inteiros iguais. Indeterminado nunca casa."""
    pa, pb = parse_answer(a), parse_answer(b)
    if pa is None or pb is None or isinstance(pa, bool) != isinstance(pb, bool):
        return False
    return pa == pb


def _fmt(key: str, v: float) -> str:
    return f"{v:.5f}" if "usd" in key else (f"{v:.1f}" if key.startswith("tempo") else f"{v:.0f}")


async def main() -> None:
    ap = argparse.ArgumentParser(description="Compara os modos jev e llm-only no mesmo checklist, com gabarito opcional.")
    ap.add_argument("checklist")
    ap.add_argument("--url", action="append")
    ap.add_argument("--ente", action="append", help='nome do ente por --url, ex.: "Goiânia/GO"')
    ap.add_argument("--only", help="números de seções, ex.: 1,3")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--gold")
    args = ap.parse_args()
    settings = load_settings()
    checklist = load_checklist(args.checklist)
    pairs = pair_urls_entes(args.url or [checklist.url], args.ente)
    urls = [u for u, _ in pairs]
    grand: dict[str, dict] = {"jev": {}, "llm-only": {}}
    hits: dict[str, list[int]] = {"jev": [], "llm-only": []}
    agree: list[int] = []

    for url, ente in pairs:
        totals: dict[str, dict] = {}
        answers: dict[str, dict] = {}
        for mode in ("jev", "llm-only"):
            acc: dict = {}
            for _ in range(args.runs):
                s = await run(args.checklist, mode, settings, args.only, "reports", url, ente)
                for k, v in s.items():
                    if isinstance(v, (int, float)):
                        acc[k] = acc.get(k, 0) + v
            totals[mode] = {k: v / args.runs for k, v in acc.items()}
            answers[mode] = s["respostas"]
            for k, v in totals[mode].items():
                grand[mode][k] = grand[mode].get(k, 0) + v

        gold = load_gold(args.gold, url)
        print(f"\n{'='*78}\nCOMPARAÇÃO — {url}  (média de {args.runs} execução(ões); LLM = {settings.llm_model})\n")
        print(f"{'Métrica':<28}{'jev':>16}{'llm-only':>18}")
        print("-" * 62)
        for key, label in ROWS:
            a, b = totals["jev"].get(key, 0), totals["llm-only"].get(key, 0)
            print(f"{label:<28}{_fmt(key, a):>16}{_fmt(key, b):>18}")
        cj = totals["jev"].get("custo_llm_usd", 0) + totals["jev"].get("custo_jev_usd", 0)
        cl = totals["llm-only"].get("custo_llm_usd", 0)
        print("-" * 62)
        print(f"{'Custo total (US$)':<28}{cj:>16.5f}{cl:>18.5f}")

        print(f"\n{'ID':<6}{'jev':<28}{'llm-only':<28}{'gabarito':<16}")
        print("-" * 78)
        for qid in answers["jev"]:
            a = answers["jev"][qid]["resposta"]
            b = answers["llm-only"].get(qid, {}).get("resposta", "-")
            g = gold.get(qid, "")
            agree.append(int(same(a, b)))
            marks = ""
            if g:
                hits["jev"].append(int(same(a, g)))
                hits["llm-only"].append(int(same(b, g)))
                marks = f"{'✓' if same(a, g) else '✗'} jev  {'✓' if same(b, g) else '✗'} llm"
            print(f"{qid:<6}{str(a)[:26]:<28}{str(b)[:26]:<28}{g[:14]:<16}{marks}")

    if len(urls) > 1:
        print(f"\n{'='*78}\nTOTAL em {len(urls)} sites\n")
        print(f"{'Métrica':<28}{'jev':>16}{'llm-only':>18}")
        print("-" * 62)
        for key, label in ROWS:
            a, b = grand["jev"].get(key, 0), grand["llm-only"].get(key, 0)
            print(f"{label:<28}{_fmt(key, a):>16}{_fmt(key, b):>18}")
    if agree:
        print(f"\nConcordância jev × llm-only: {sum(agree)}/{len(agree)} perguntas")
    if hits["jev"]:
        print(f"Acurácia vs gabarito: jev {sum(hits['jev'])}/{len(hits['jev'])}   "
              f"llm-only {sum(hits['llm-only'])}/{len(hits['llm-only'])}")


if __name__ == "__main__":
    asyncio.run(main())
