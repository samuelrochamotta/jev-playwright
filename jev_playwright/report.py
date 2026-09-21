from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from .explorer import SectionResult


def host_slug(url: str) -> str:
    host = urlparse(url).netloc or url
    return re.sub(r"[^a-z0-9]+", "-", host.lower()).strip("-")[:40]


def ente_dirname(ente: str | None, url: str) -> str:
    """Nome da pasta do ente: `Goiânia/GO` vira `Goiânia-GO`; sem ente, usa o host da URL."""
    name = (ente or "").strip()
    if not name:
        return host_slug(url)
    name = re.sub(r"\s*/\s*", "-", name)               # UF após o nome
    name = re.sub(r"[\\:*?\"<>|\x00-\x1f]+", "", name)  # caracteres inválidos em nomes de pasta
    return re.sub(r"\s+", " ", name).strip(" .")[:80] or host_slug(url)


def _dir_key(name: str) -> str:
    """Chave de comparação de pastas: sem acentos, sem diferença de caixa/espaços (`Goiânia-GO` == `Goiania-GO`)."""
    decomposed = unicodedata.normalize("NFD", name)
    plain = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", plain).strip().casefold()


def resolve_ente_dir(out_dir: str | Path, ente: str | None, url: str) -> Path:
    """Pasta do ente em `out_dir`: reaproveita a já existente (mesmo nome, ignorando acentos e caixa) ou devolve a nova."""
    out_dir = Path(out_dir)
    name = ente_dirname(ente, url)
    if out_dir.is_dir():
        key = _dir_key(name)
        for existing in sorted(out_dir.iterdir()):
            if existing.is_dir() and _dir_key(existing.name) == key:
                return existing
    return out_dir / name


def execution_dir(out_dir: str | Path, ente: str | None, url: str, when: datetime | None = None) -> Path:
    """`<out_dir>/<ente>/execucao_<AAAAMMDDhhmmss>` — cria a pasta do ente só se ainda não existir uma equivalente."""
    when = when or datetime.now()
    path = resolve_ente_dir(out_dir, ente, url) / f"execucao_{when:%Y%m%d%H%M%S}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_reports(title: str, url: str, sections: list[SectionResult], out_dir: str | Path, extra: dict,
                  mode: str = "jev", ente: str | None = None) -> tuple[Path, Path]:
    now = datetime.now()
    out = execution_dir(out_dir, ente, url, now)
    base = f"report-{mode}"
    json_path, md_path = out / f"{base}.json", out / f"{base}.md"

    json_path.write_text(json.dumps({"title": title, "ente": ente or "", "url": url, "executado_em": now.isoformat(timespec="seconds"),
                                     "results": [asdict(s) for s in sections], **extra},
                                    ensure_ascii=False, indent=2), encoding="utf-8")

    n_q = sum(len(s.questions) for s in sections)
    lines = [f"# Relatório ({mode}): {title}", ""]
    if ente:
        lines.append(f"- Ente: {ente}")
    lines += [f"- Site: {url}", f"- Data: {now:%Y-%m-%d %H:%M}",
             f"- Perguntas respondidas: **{n_q}** em {len(sections)} seção(ões); "
             f"alvos encontrados: {sum(s.found for s in sections)}/{len(sections)}", ""]
    for k, v in extra.items():
        if k != "respostas":
            lines.append(f"- {k}: {v}")

    lines += ["", "## Perguntas", "", "| ID | Pergunta | Resposta | Conf. | Tipo | Fonte | Evidência |",
              "|---|---|---|---|---|---|---|"]
    for s in sections:
        for q in s.questions:
            ev = q.evidence.replace("|", "/").replace("\n", " ")[:120]
            lines.append(f"| {q.id} | {q.text} | **{q.answer}** | {q.confidence:.2f} | {q.kind} | {q.source} | {ev} |")
    lines += ["", "## Seções", "", "| # | Seção | Alvo encontrado | P | Cliques | Páginas | Jev | LLM | Tempo (s) | URL final |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for s in sections:
        found = "✅ sim" if s.found else "❌ não"
        clicks = "-" if s.clicks is None else str(s.clicks)
        lines.append(f"| {s.number} | {s.title} | {found} | {s.found_prob:.2f} | {clicks} | {s.pages_visited} | "
                     f"{s.jev_calls} | {s.llm_calls} | {s.elapsed_s} | {s.final_url} |")
    for s in sections:
        lines += ["", f"### Seção {s.number}. {s.title}", ""]
        if s.path:
            lines += ["Caminho mais curto encontrado:", ""] + [f"{i}. {p}" for i, p in enumerate(s.path, 1)] + [""]
        if s.trace:
            lines += ["Rastro da exploração:", ""] + [f"- {t}" for t in s.trace]
        if s.error:
            lines += ["", f"**Erro:** {s.error}"]
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path
