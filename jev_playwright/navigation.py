"""Bounded graph search, semantic actions and validated route recipes (no browser refs)."""
from __future__ import annotations

import heapq
import json
import re
from dataclasses import asdict, dataclass, field
from hashlib import sha256
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

from .elements import Element, normalize


def web_url(url: str) -> bool:
    if not isinstance(url, str):
        return False
    try:
        parsed = urlparse(url)
        _ = parsed.port  # validate malformed ports even for browser candidates
        return parsed.scheme in {"http", "https"} and bool(parsed.hostname) and parsed.username is None and parsed.password is None
    except ValueError:
        return False


def document_url(url: str) -> bool:
    parsed = urlparse(url)
    extensions = (".pdf", ".csv", ".xlsx", ".xls", ".ods", ".json")
    return parsed.path.lower().endswith(extensions) or any(value.lower().endswith(extensions) for _, value in parse_qsl(parsed.query))


def document_link(element: Element) -> bool:
    """Discover format-labelled links even when an export endpoint has no file extension."""
    return bool(element.href) and (document_url(element.href) or bool(
        re.search(r"\b(pdf|csv|xlsx|xls|ods|json)\b", normalize(element.name + ' ' + element.detail))))


def search_queries(target: str) -> list[str]:
    from .explorer import search_terms
    primary = search_terms(target)
    general = re.sub(r"\b(federais|federal|estaduais|estadual|municipais|municipal|20\d{2})\b", "", primary, flags=re.I)
    general = " ".join(general.split())
    return list(dict.fromkeys(q for q in [primary, general, "emendas parlamentares", "emendas"] if q))


def shortlist(elements: list[Element], target: str, limit: int) -> list[Element]:
    """Keep generic routes and search controls, not only exact keyword matches."""
    words = normalize(target).split()
    generic = ("receita", "transfer", "convenio", "menu", "dados aberto", "transparencia")
    def score(e: Element) -> int:
        text = normalize(e.describe())
        return sum(w in text for w in words if len(w) > 3) + 2 * any(w in text for w in generic)
    ranked = sorted(elements, key=score, reverse=True)
    # Reserve 1/3 for structural diversity; all fields must be independently evaluated.
    diverse = [e for e in elements if e.kind == "type" or any(w in normalize(e.describe()) for w in generic)]
    reserved = diverse[:max(1, limit // 3)]
    result = reserved + [e for e in ranked if e not in reserved]
    return result[:limit]


@dataclass(frozen=True)
class Action:
    operation: str
    signature: str
    name: str
    href: str = ""
    query: str = ""
    expected_state: str = ""  # precondition of the originating state, never a temporary ref
    role: str = ""
    origin_url: str = ""

    @classmethod
    def from_element(cls, operation: str, element: Element, state: str, query: str = "", origin_url: str = "") -> "Action":
        return cls(operation, element.signature, element.name, element.href, query, state, element.role, origin_url)


@dataclass(order=True)
class Candidate:
    priority: float
    sequence: int
    route: tuple[Action, ...] = field(compare=False)


class Frontier:
    def __init__(self, limit: int = 100) -> None:
        self.limit = limit
        self._heap: list[Candidate] = []
        self._seen: set[tuple[Action, ...]] = set()
        self._sequence = 0

    def add(self, route: tuple[Action, ...], relevance: float) -> None:
        if route in self._seen:
            return
        self._seen.add(route)
        self._sequence += 1
        # This is a priority heuristic, NOT a calibrated probability of route success.
        heapq.heappush(self._heap, Candidate(-relevance + 0.015 * len(route), self._sequence, route))
        if len(self._heap) > self.limit:
            self._heap = heapq.nsmallest(self.limit, self._heap)
            heapq.heapify(self._heap)

    def pop(self) -> tuple[Action, ...] | None:
        return heapq.heappop(self._heap).route if self._heap else None


class RecipeStore:
    """Exact portal/ente/target scoped recipes. Cross-municipality reuse is deliberately not automatic."""
    def __init__(self, directory: str, start_url: str, target: str, ente: str, objective: str) -> None:
        identity = json.dumps([start_url, normalize(target), normalize(ente), objective])
        self.path = Path(directory) / (sha256(identity.encode()).hexdigest() + ".json")

    def load(self, max_depth: int) -> tuple[Action, ...] | None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("version") != 1 or not 0 <= len(data["route"]) <= max_depth:
                return None
            route = tuple(Action(**item) for item in data["route"])
            if any(a.operation not in {"CLICK", "HOVER", "SEARCH"} or (a.href and not web_url(a.href)) for a in route):
                return None
            return route
        except (OSError, ValueError, TypeError, KeyError):
            return None

    def save(self, route: tuple[Action, ...]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"version": 1, "route": [asdict(a) for a in route]}, ensure_ascii=False), encoding="utf-8")
        temporary.replace(self.path)

    def invalidate(self) -> None:
        self.path.unlink(missing_ok=True)


def supplier_family(url: str) -> str:
    """Known vendor domains; unknown vendors stay host-scoped instead of guessing eTLDs."""
    host = (urlparse(url).hostname or '').lower()
    for domain in ('municipioonline.com.br', 'nucleogov.com.br', 'betha.cloud', 'betha.com.br'):
        if host == domain or host.endswith('.' + domain):
            return domain
    return host


class FamilyRecipeStore:
    """Cross-ente hints contain names/roles only, never old URLs, IDs, refs or proof of success."""
    def __init__(self, directory: str, url: str, target: str, objective: str) -> None:
        self.family = supplier_family(url)
        identity = json.dumps([self.family, normalize(target), objective])
        self.path = Path(directory) / ('family-' + sha256(identity.encode()).hexdigest() + '.json')

    def save(self, route: tuple[Action, ...]) -> None:
        hints = []
        for action in route:
            if action.origin_url and supplier_family(action.origin_url) == self.family and action.role:
                hint = {"operation": action.operation, "name": action.name, "role": action.role, "query": action.query}
                if hint not in hints:
                    hints.append(hint)
        if not hints:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps({"version": 1, "hints": hints}, ensure_ascii=False), encoding='utf-8')
        temporary.replace(self.path)

    def match(self, elements: list[Element], queries: list[str]) -> list[tuple[str, Element, str, float]]:
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(data, dict) or data.get('version') != 1:
                return []
            result = []
            for hint in data['hints'][:20]:
                operation = hint['operation']
                if operation not in {'CLICK', 'HOVER', 'SEARCH'}:
                    continue
                matches = [e for e in elements if e.role == hint['role'] and normalize(e.name) == normalize(hint['name'])]
                # Ambiguous controls are not resolved by an old recipe.
                if len(matches) != 1:
                    continue
                element = matches[0]
                query = hint.get('query', '')
                if operation == 'SEARCH' and (element.kind != 'type' or query not in queries):
                    continue
                if operation != 'SEARCH' and element.kind != 'click':
                    continue
                result.append((operation, element, query if operation == 'SEARCH' else '', .95))
            return result
        except (OSError, ValueError, TypeError, KeyError):
            return []
