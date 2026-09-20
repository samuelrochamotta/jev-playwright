"""Lê um checklist em Markdown de seções de perguntas (avaliação de um portal).

    Ente: Goiânia/GO                      # opcional; nomeia a pasta de resultados (--ente sobrepõe)
    URL: https://...                      # opcional; --url sobrepõe (inclusive várias)

    # 1 Existência de seção de emendas parlamentares federais
    1.1 - O portal possui seção específica de emendas parlamentares federais?
    1.2 - Quantos cliques são necessários para encontrar a seção?

O alvo de cada seção é o seu título; todas as perguntas da seção se referem a esse alvo.
Toda pergunta deve admitir resposta sim/não ou um número inteiro.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Question:
    id: str
    text: str


@dataclass
class Section:
    number: str
    title: str
    questions: list[Question] = field(default_factory=list)


@dataclass
class Checklist:
    title: str
    url: str
    sections: list[Section]
    ente: str = ""


URL_RE = re.compile(r"^\**\s*url\s*\**\s*:\s*(.+)$", re.I)
ENTE_RE = re.compile(r"^\**\s*ente\s*\**\s*:\s*(.+)$", re.I)
SECTION_RE = re.compile(r"^#\s+(\d+)[.)]?\s+(?!\d)(.+)$")
QUESTION_RE = re.compile(r"^(?:[-*]\s*)?(\d+\.\d+(?:\.\d+)*)[.)]?\s*[-–—:.]?\s*(.+)$")


def load_checklist(path: str | Path) -> Checklist:
    text = Path(path).read_text(encoding="utf-8")
    title, url, ente = "", "", ""
    sections: list[Section] = []
    section: Section | None = None

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        mu = URL_RE.match(line.lstrip("- "))
        if mu:
            url = mu.group(1).strip()
            continue
        me = ENTE_RE.match(line.lstrip("- "))
        if me:
            ente = me.group(1).strip()
            continue
        ms = SECTION_RE.match(line)
        if ms:
            section = Section(number=ms.group(1), title=ms.group(2).strip())
            sections.append(section)
            continue
        if line.startswith("# "):
            title = line[2:].strip()
            continue
        mq = QUESTION_RE.match(line)
        if mq:
            qid, qtext = mq.group(1), mq.group(2).strip()
            num = qid.split(".")[0]
            if section is None or section.number != num:
                section = next((s for s in sections if s.number == num), None)
                if section is None:
                    section = Section(number=num, title=f"Seção {num}")
                    sections.append(section)
            section.questions.append(Question(qid, qtext))

    sections = [s for s in sections if s.questions]
    if not sections:
        raise ValueError("Checklist sem perguntas (formato: `# N Título` seguido de `N.M - Pergunta?`)")
    return Checklist(title=title or Path(path).stem, url=url, sections=sections, ente=ente)
