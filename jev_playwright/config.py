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
    )
