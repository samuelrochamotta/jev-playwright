import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    openrouter_api_key: str
    jev_model: str = "typesafe/jev-1.13"
    llm_model: str = "z-ai/glm-5.3-flash"
    headless: bool = True
    openrouter_base: str = "https://openrouter.ai"
    max_elements: int = 160          # elementos interativos enviados ao Jev por página
    # --- exploração (busca do alvo da seção) ---
    arrive_threshold: float = 0.7    # P(chegou ao alvo) mínima para considerar a seção encontrada
    max_depth: int = 4               # cliques máximos a partir da página inicial
    max_pages_per_find: int = 14     # orçamento de páginas avaliadas por seção
    max_local_actions: int = 3       # hover/busca na mesma página antes de desistir dela
    min_alt_prob: float = 0.04       # alternativas abaixo disso não entram na fila de backtracking
    max_alternatives: int = 4        # alternativas guardadas por página para backtracking
    max_refine_steps: int = 2        # cliques extras dentro da seção do tema para satisfazer qualificadores
    refine_min_prob: float = 0.5     # P mínima do sub-alvo (aba/filtro) para tentar o refinamento
    refine_min_gain: float = 0.05    # ganho mínimo em P(exato) para aceitar um passo de refinamento
    refine_retry_drop: float = 0.3   # em página externa, queda de P(tema) maior que isso reavalia após esperar
    page_text_chars: int = 3500      # trecho de texto da página enviado ao Jev
    # --- perguntas `contar` ---
    max_count_candidates: int = 80   # candidatos da página-alvo enviados ao Jev para contagem
    count_batch: int = 25            # candidatos julgados (noul) por chamada ao Jev
    count_text_chars: int = 8000     # trecho de texto da página-alvo enviado ao Jev nas contagens
    collapse_options_over: int = 20  # listbox com mais opções que isso vira um candidato só (salvo palavra-chave)
    max_openers_shown: int = 12      # elementos "abridores" (dropdown/menu/acordeão) mostrados ao Jev
    max_openers: int = 4             # abridores efetivamente abertos por pergunta `contar`
    max_reveal_depth: int = 2        # níveis de abertura (menu → submenu) por abridor
    max_sub_openers: int = 2         # sub-abridores abertos dentro de cada abridor
    max_scroll_rounds: int = 4       # rolagens dentro de uma lista virtualizada para revelar mais itens
    wait_loading_rounds: int = 4     # esperas extras enquanto o snapshot tem placeholders de carga
    # --- baseline llm-only ---
    llm_explore_turns: int = 25      # turnos do LLM por seção
    search_strategy: str = "best-first"  # dfs remains available as baseline
    candidate_limit: int = 24
    frontier_limit: int = 100
    max_action_errors: int = 3
    require_exact: bool = True
    uncertainty_margin: float = 0.1  # abstain on binary/count candidate probabilities near 0.5
    objective: str = "audit"        # audit | collect
    collect: bool = False
    max_sources: int = 8
    max_download_bytes: int = 10_000_000
    max_records: int = 1000
    recipe_dir: str = ".jev-recipes"
    use_recipes: bool = False
    fallback: bool = False          # bounded, validated generative proposal
    fallback_attempts: int = 1
    fallback_vision: bool = False   # opt-in screenshot as context; executable actions remain bounded

    def __post_init__(self) -> None:
        if self.search_strategy not in {"best-first", "dfs"} or self.objective not in {"audit", "collect"}:
            raise ValueError("Estratégia/objetivo inválido")
        for name in ("max_elements", "max_pages_per_find", "candidate_limit", "frontier_limit", "max_action_errors",
                     "max_sources", "max_download_bytes", "max_records", "fallback_attempts", "max_alternatives", "count_batch"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} deve ser positivo")
        if self.max_depth < 0:
            raise ValueError("max_depth não pode ser negativo")
        if not 0 <= self.uncertainty_margin < 0.5:
            raise ValueError("uncertainty_margin deve estar entre 0 e 0.5 (exclusivo)")
        if not 0 <= self.arrive_threshold <= 1:
            raise ValueError("arrive_threshold deve estar entre 0 e 1")
        if self.fallback_vision and not self.fallback:
            raise ValueError("Visão exige fallback habilitado")
        if self.collect and self.objective != "collect":
            raise ValueError("Coleta exige objective=collect")


def load_settings() -> Settings:
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        raise SystemExit("OPENROUTER_API_KEY não definida. Copie .env.example para .env e preencha a chave.")
    return Settings(
        openrouter_api_key=key,
        jev_model=os.environ.get("JEV_MODEL", "typesafe/jev-1.13"),
        llm_model=os.environ.get("LLM_MODEL", "z-ai/glm-5.3-flash"),
        headless=os.environ.get("HEADLESS", "true").lower() != "false",
        max_elements=_env_int("MAX_ELEMENTS", 160),
        arrive_threshold=_env_float("ARRIVE_THRESHOLD", 0.7),
        max_depth=_env_int("MAX_DEPTH", 4),
        max_pages_per_find=_env_int("MAX_PAGES_PER_FIND", 14),
        refine_min_gain=_env_float("REFINE_MIN_GAIN", 0.05),
        refine_retry_drop=_env_float("REFINE_RETRY_DROP", 0.3),
        max_count_candidates=_env_int("MAX_COUNT_CANDIDATES", 80),
        count_text_chars=_env_int("COUNT_TEXT_CHARS", 8000),
        collapse_options_over=_env_int("COLLAPSE_OPTIONS_OVER", 20),
        max_openers_shown=_env_int("MAX_OPENERS_SHOWN", 12),
        max_openers=_env_int("MAX_OPENERS", 4),
        max_reveal_depth=_env_int("MAX_REVEAL_DEPTH", 2),
        max_sub_openers=_env_int("MAX_SUB_OPENERS", 2),
        max_scroll_rounds=_env_int("MAX_SCROLL_ROUNDS", 4),
        wait_loading_rounds=_env_int("WAIT_LOADING_ROUNDS", 4),
        llm_explore_turns=_env_int("LLM_EXPLORE_TURNS", 25),
        search_strategy=os.environ.get("SEARCH_STRATEGY", "best-first"),
        candidate_limit=_env_int("CANDIDATE_LIMIT", 24),
        frontier_limit=_env_int("FRONTIER_LIMIT", 100),
        max_action_errors=_env_int("MAX_ACTION_ERRORS", 3),
        max_sources=_env_int("MAX_SOURCES", 8),
        max_download_bytes=_env_int("MAX_DOWNLOAD_BYTES", 10_000_000),
        max_records=_env_int("MAX_RECORDS", 1000),
        recipe_dir=os.environ.get("RECIPE_DIR", ".jev-recipes"),
    )
