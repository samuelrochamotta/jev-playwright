"""Executa um checklist Markdown de seções de perguntas num ou mais portais.

    python run_checklist.py checklists/checklist.md --url https://transparencia.pbh.gov.br/ --ente "Belo Horizonte/MG"
    python run_checklist.py checklists/checklist.md --url URL1 --ente "Ente 1/UF" --url URL2 --ente "Ente 2/UF" --mode llm-only
    python run_checklist.py checklists/checklist.md --entes entes.csv            # lista de entes (ente,uf,url)
    python run_checklist.py checklists/checklist.md --entes entes.csv --ente-filtro Salvador
    python run_checklist.py checklists/checklist.md --url URL --headed --only 1

Cada execução é gravada em `<out>/<Nome do ente-UF>/execucao_<AAAAMMDDhhmmss>/report-<modo>.{md,json}`.
O i-ésimo --ente corresponde ao i-ésimo --url; sem --ente vale a linha `Ente:` do checklist ou, na falta, o host da URL.
"""
from __future__ import annotations

import argparse
import asyncio
import time
from dataclasses import replace

from jev_playwright.browser import MCP_VERSION, Browser
from jev_playwright.checklist import load_checklist
from jev_playwright.config import Settings, load_settings
from jev_playwright.entes import filter_entes, load_entes
from jev_playwright.explorer import JevExplorer, SectionResult, parse_answer
from jev_playwright.jev_client import JevClient
from jev_playwright.llm_client import OpenRouterChat
from jev_playwright.llm_explorer import LlmExplorer
from jev_playwright.report import execution_dir, write_reports


async def run(checklist_path: str, mode: str, settings: Settings, only: str | None = None, out: str = "reports",
              url: str | None = None, ente: str | None = None) -> dict:
    if settings.collect and mode != "jev":
        raise ValueError("Coleta verificada requer --mode jev; llm-only é apenas baseline")
    checklist = load_checklist(checklist_path)
    url = url or checklist.url
    ente = ente or checklist.ente
    if not url:
        raise SystemExit("O checklist não tem linha 'URL:'; informe --url https://...")
    if settings.collect and not ente:
        raise ValueError("Informe --ente Nome/UF para validar a identidade na coleta")
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

    try:
        t0 = time.perf_counter()
        output = execution_dir(out, ente, url)
        results: list[SectionResult] = []
        async with Browser(headless=settings.headless, max_elements=settings.max_elements) as browser:
            await browser.navigate(url)
            explorer = (JevExplorer(settings, browser, jev, chat, url, ente=ente or "", collection_dir=output / "artifacts") if mode == "jev"
                        else LlmExplorer(settings, browser, chat, url))
            for section in sections:
                results.append(await explorer.run_section(section))

        summary = {
            "modo": mode, "ente": ente or "", "site": url,
            "secoes": len(results), "alvos_encontrados": sum(r.found for r in results),
            "perguntas": sum(len(r.questions) for r in results),
            "erros": sum(1 for r in results if r.error),
            "inconclusivas": sum(1 for r in results if not r.found or r.collection.get("status") == "inconclusive"
                                  or any(parse_answer(q.answer) is None for q in r.questions)),
            "status_secoes": {r.number: r.status for r in results},
            "estrategia": settings.search_strategy,
            "objetivo": settings.objective,
            "modelo_jev": settings.jev_model,
            "modelo_jev_efetivo": jev.model_version,
            "playwright_mcp_version": MCP_VERSION,
            "politica_version": "graph-v1",
            "modelo_llm": settings.llm_model,
            "registros_coletados": sum(r.collection.get("records", 0) for r in results),
            "tempo_total_s": round(time.perf_counter() - t0, 1),
            "chamadas_jev": jev.total_calls, "custo_jev_usd": round(jev.total_cost, 6),
            "chamadas_llm": chat.total_calls, "custo_llm_usd": round(chat.total_cost, 5),
            "tokens_llm": chat.total_tokens, "tempo_llm_s": round(chat.total_ms / 1000, 1),
            "paginas": sum(r.pages_visited for r in results),
            "respostas": {q.id: {"resposta": q.answer, "confianca": q.confidence, "tipo": q.kind}
                          for r in results for q in r.questions},
        }
        json_path, md_path = write_reports(checklist.title, url, results, out, summary, mode, ente, output=output)
        shown = {k: v for k, v in summary.items() if k != "respostas"}
        print(f"\nRespostas ({mode}): " + "  ".join(f"{k}={v['resposta']!r}" for k, v in summary["respostas"].items()))
        print(shown)
        print(f"Relatórios: {md_path}  {json_path}")
        return summary
    finally:
        jev.close()
        chat.close()


def pair_urls_entes(urls: list[str] | None, entes: list[str] | None) -> list[tuple[str | None, str | None]]:
    """Casa o i-ésimo --ente com o i-ésimo --url; entes a mais são erro, a menos ficam None."""
    urls, entes = urls or [None], entes or []
    if len(entes) > len(urls):
        raise SystemExit(f"--ente informado {len(entes)} vez(es) para {len(urls)} --url")
    return [(u, entes[i] if i < len(entes) else None) for i, u in enumerate(urls)]


def collect_targets(urls: list[str] | None, entes: list[str] | None, entes_csv: str | None = None,
                    ente_filtro: str | None = None) -> list[tuple[str | None, str | None]]:
    """Alvos a avaliar: os do CSV (--entes) mais os pares --url/--ente da linha de comando."""
    pairs: list[tuple[str | None, str | None]] = []
    if entes_csv:
        pairs += filter_entes(load_entes(entes_csv), ente_filtro)
    elif ente_filtro:
        raise SystemExit("--ente-filtro só faz sentido junto de --entes")
    if urls or entes or not pairs:
        pairs += pair_urls_entes(urls, entes)
    return pairs


def main() -> int:
    ap = argparse.ArgumentParser(description="Executa um checklist de perguntas (sim/não ou número) contra portais.")
    ap.add_argument("checklist")
    ap.add_argument("--mode", choices=["jev", "llm-only"], default="jev")
    ap.add_argument("--url", action="append", help="site(s) a avaliar; sobrepõe a linha URL: do checklist")
    ap.add_argument("--ente", action="append", help='nome do ente por --url, ex.: "Goiânia/GO"; nomeia a pasta de resultados')
    ap.add_argument("--entes", help="CSV com colunas ente,uf,url (ex.: entes.csv); roda o checklist em cada linha")
    ap.add_argument("--ente-filtro", help="roda só os entes do --entes cujo nome contém um destes termos, ex.: Salvador,Aracaju")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--only", help="números de seções, ex.: 1,3")
    ap.add_argument("--out", default="reports")
    ap.add_argument("--strategy", choices=["best-first", "dfs"], help="best-first (padrão) ou baseline DFS")
    ap.add_argument("--collect", action="store_true", help="coletar fontes públicas, registros e evidências com proveniência")
    ap.add_argument("--recipes", action="store_true", help="reutilizar rotas validadas deste portal/ente/alvo")
    ap.add_argument("--fallback", action="store_true", help="LLM propõe uma ação quando Jev não identifica caminho claro")
    ap.add_argument("--vision-fallback", action="store_true", help="fallback multimodal opcional; requer LLM_MODEL com visão")
    ap.add_argument("--max-pages", type=int)
    ap.add_argument("--max-depth", type=int)
    args = ap.parse_args()
    settings = load_settings()
    settings = replace(settings, headless=False if args.headed else settings.headless,
                       search_strategy=args.strategy or settings.search_strategy,
                       collect=args.collect, objective="collect" if args.collect else "audit",
                       use_recipes=args.recipes, fallback=args.fallback or args.vision_fallback,
                       fallback_vision=args.vision_fallback,
                       max_pages_per_find=args.max_pages if args.max_pages is not None else settings.max_pages_per_find,
                       max_depth=args.max_depth if args.max_depth is not None else settings.max_depth)
    targets = collect_targets(args.url, args.ente, args.entes, args.ente_filtro)
    ok, falhas = True, []
    for i, (url, ente) in enumerate(targets, 1):
        if len(targets) > 1:
            print(f"\n>>> [{i}/{len(targets)}] {ente or url}")
        try:
            summary = asyncio.run(run(args.checklist, args.mode, settings, args.only, args.out, url, ente))
        except Exception as exc:  # um ente que falha não interrompe o lote
            if len(targets) == 1:
                raise
            print(f"!!! {ente or url}: {type(exc).__name__}: {exc}")
            ok = False
            falhas.append(ente or url)
            continue
        ok = ok and summary["erros"] == 0 and summary["inconclusivas"] == 0
    if falhas:
        print(f"\nEntes com falha: {', '.join(falhas)}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
