"""Executa um checklist Markdown de seções de perguntas num ou mais portais.

    python run_checklist.py checklists/checklist.md --url https://transparencia.pbh.gov.br/ --ente "Belo Horizonte/MG"
    python run_checklist.py checklists/checklist.md --url URL1 --ente "Ente 1/UF" --url URL2 --ente "Ente 2/UF" --mode llm-only
    python run_checklist.py checklists/checklist.md --url URL --headed --only 1

Cada execução é gravada em `<out>/<Nome do ente-UF>/execucao_<AAAAMMDDhhmmss>/report-<modo>.{md,json}`.
O i-ésimo --ente corresponde ao i-ésimo --url; sem --ente vale a linha `Ente:` do checklist ou, na falta, o host da URL.
"""
from __future__ import annotations

import argparse
import asyncio
import time

from jev_playwright.browser import Browser
from jev_playwright.checklist import load_checklist
from jev_playwright.config import Settings, load_settings
from jev_playwright.explorer import JevExplorer, SectionResult
from jev_playwright.jev_client import JevClient
from jev_playwright.llm_client import OpenRouterChat
from jev_playwright.llm_explorer import LlmExplorer
from jev_playwright.report import write_reports


async def run(checklist_path: str, mode: str, settings: Settings, only: str | None = None, out: str = "reports",
              url: str | None = None, ente: str | None = None) -> dict:
    checklist = load_checklist(checklist_path)
    url = url or checklist.url
    ente = ente or checklist.ente
    if not url:
        raise SystemExit("O checklist não tem linha 'URL:'; informe --url https://...")
    sections = checklist.sections
    if only:
        keep = {x.strip() for x in only.split(",")}
        sections = [s for s in sections if s.number in keep]

    print(f"\n{'#'*60}\nModo: {mode}   Checklist: {checklist.title}  "
          f"({len(sections)} seções / {sum(len(s.questions) for s in sections)} perguntas)\n"
          f"Ente: {ente or '-'}   Site: {url}")
    jev = JevClient(settings.openrouter_api_key, settings.jev_model, settings.openrouter_base)
    chat = OpenRouterChat(settings.openrouter_api_key, settings.llm_model, settings.openrouter_base)
    print(f"Jev: {settings.jev_model if mode == 'jev' else '-'}   LLM: {settings.llm_model}\n")

    t0 = time.perf_counter()
    results: list[SectionResult] = []
    async with Browser(headless=settings.headless, max_elements=settings.max_elements) as browser:
        await browser.navigate(url)
        explorer = (JevExplorer(settings, browser, jev, chat, url) if mode == "jev"
                    else LlmExplorer(settings, browser, chat, url))
        for section in sections:
            results.append(await explorer.run_section(section))

    summary = {
        "modo": mode, "ente": ente or "", "site": url,
        "secoes": len(results), "alvos_encontrados": sum(r.found for r in results),
        "perguntas": sum(len(r.questions) for r in results),
        "erros": sum(1 for r in results if r.error),
        "tempo_total_s": round(time.perf_counter() - t0, 1),
        "chamadas_jev": jev.total_calls, "custo_jev_usd": round(jev.total_cost, 6),
        "chamadas_llm": chat.total_calls, "custo_llm_usd": round(chat.total_cost, 5),
        "tokens_llm": chat.total_tokens, "tempo_llm_s": round(chat.total_ms / 1000, 1),
        "paginas": sum(r.pages_visited for r in results),
        "respostas": {q.id: {"resposta": q.answer, "confianca": q.confidence, "tipo": q.kind}
                      for r in results for q in r.questions},
    }
    json_path, md_path = write_reports(checklist.title, url, results, out, summary, mode, ente)
    shown = {k: v for k, v in summary.items() if k != "respostas"}
    print(f"\nRespostas ({mode}): " + "  ".join(f"{k}={v['resposta']!r}" for k, v in summary["respostas"].items()))
    print(shown)
    print(f"Relatórios: {md_path}  {json_path}")
    return summary


def pair_urls_entes(urls: list[str] | None, entes: list[str] | None) -> list[tuple[str | None, str | None]]:
    """Casa o i-ésimo --ente com o i-ésimo --url; entes a mais são erro, a menos ficam None."""
    urls, entes = urls or [None], entes or []
    if len(entes) > len(urls):
        raise SystemExit(f"--ente informado {len(entes)} vez(es) para {len(urls)} --url")
    return [(u, entes[i] if i < len(entes) else None) for i, u in enumerate(urls)]


def main() -> int:
    ap = argparse.ArgumentParser(description="Executa um checklist de perguntas (sim/não ou número) contra portais.")
    ap.add_argument("checklist")
    ap.add_argument("--mode", choices=["jev", "llm-only"], default="jev")
    ap.add_argument("--url", action="append", help="site(s) a avaliar; sobrepõe a linha URL: do checklist")
    ap.add_argument("--ente", action="append", help='nome do ente por --url, ex.: "Goiânia/GO"; nomeia a pasta de resultados')
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--only", help="números de seções, ex.: 1,3")
    ap.add_argument("--out", default="reports")
    args = ap.parse_args()
    settings = load_settings()
    if args.headed:
        settings = Settings(**{**settings.__dict__, "headless": False})
    ok = True
    for url, ente in pair_urls_entes(args.url, args.ente):
        summary = asyncio.run(run(args.checklist, args.mode, settings, args.only, args.out, url, ente))
        ok = ok and summary["erros"] == 0
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
