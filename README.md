# jev-playwright

Executa um checklist Markdown de perguntas contra portais de transparência. O **Jev** (TypeSafe System One) decide
navegação e julgamento; o **Playwright MCP** executa no navegador; o loop de agente é Python (**política em código**).
Um LLM via OpenRouter serve de baseline (`--mode llm-only`) e de fallback estrito de contagem.

## Invariantes

- O Jev responde perguntas tipadas (`choice`, `noul`, `score`) sobre um estado JSON e devolve **probabilidades**.
  Ele nunca gera texto nem chama ferramentas: toda decisão de fluxo fica em código.
- Toda pergunta do checklist admite só **sim/não** ou **inteiro**. `parse_answer` normaliza qualquer resposta
  (dos dois modos e do gabarito); o que não normaliza vira `indeterminado`, que nunca casa com o gabarito.
- Palavras-chave do título/pergunta servem para **priorizar** elementos quando a tabela estoura, nunca para decidir.
- Limiares e orçamentos vivem em `jev_playwright/config.py` (e nas variáveis de ambiente de mesmo nome).

## Uso

```bash
conda activate jev-playmcp && pip install -r requirements.txt && cp .env.example .env   # OPENROUTER_API_KEY
python run_checklist.py checklists/checklist.md --url URL --ente "Ente/UF"    # --url/--ente repetem em pares
python run_checklist.py checklists/checklist.md --entes entes.csv --ente-filtro Aracaju
python compare.py checklists/checklist.md --entes entes.csv --gold checklists/gold.json --runs 3
```

Node ≥ 18 (o MCP roda com `npx`). Demais flags: `--help`. Relatórios em `reports/<Ente-UF>/execucao_<data>/report-<modo>.{md,json}`,
com resposta, tipo, fonte e evidência por pergunta, caminho, rastro da exploração, chamadas e custo. Código de saída 1 se
alguma seção ou ente falhou; num lote, um ente que falha não interrompe os outros.

## Checklist

```markdown
Ente: Goiânia/GO           # opcional; --ente sobrepõe
URL: https://site          # opcional; --url sobrepõe

# 1 Existência de seção de emendas parlamentares federais      ← o título é o ALVO da seção
1.1 - O portal possui seção específica de emendas parlamentares federais?
1.2 - Quantos cliques são necessários para encontrar a seção?
```

Toda pergunta se refere ao alvo da seção. O Jev classifica cada uma (1 chamada por seção):

| Tipo | Resposta | Como o modo jev responde | Alvo não encontrado |
|---|---|---|---|
| `encontrar` | sim/não | busca guiada chegou ao alvo (P ≥ `arrive_threshold`) | `não` |
| `cliques` | inteiro | profundidade do caminho vencedor, com atalho em código | `indeterminado` |
| `julgar_pagina` | sim/não | Jev `noul` em lote sobre a página-alvo, 1 chamada | `não` |
| `contar` | inteiro | nº de candidatos que o Jev aceita (ver Contagem) | `0` |

Desafio anti-robô na exploração: todas viram `indeterminado (bloqueio anti-robô)`.

Gabarito (`--gold`): `{"<host ou URL>": {"1.1": "sim", "1.2": 2}}`.

## Modo jev, por seção

1. **Classificar** as perguntas: Jev `choice`, uma cabeça por pergunta.
2. **Encontrar o alvo**: busca em profundidade guiada pelo Jev, **1 chamada por página** com as cabeças na mesma
   requisição: `chegou` (a página é uma seção dedicada ao *tema* do alvo?), `exato` (os qualificadores, como
   "federais", já estão satisfeitos?), `acao` (CLICK / HOVER / SEARCH / BACK), `alvo_clique` (ranking dos elementos
   **viáveis**), `alvo_busca`. O ranking vira a fila de backtracking: becos sem saída voltam sem nova chamada.
   Termina quando `chegou` ≥ `arrive_threshold`, ou quando esgotam alternativas, profundidade ou orçamento de páginas.
3. **Refinar dentro da seção do tema** se `exato` está baixo: até `max_refine_steps` cliques guiados por
   `alvo_clique` (aba "Federais", filtro "Federal"), cada um aceito só se `exato` sobe e o tema se mantém. Sem
   sub-alvo interno claro, links de **outro domínio no conteúdo** (banner para o sistema onde a consulta mora)
   entram numa segunda chamada; links para o mesmo host somam probabilidade.
4. **Atalho**: se uma página anterior do caminho já linkava direto para a URL final, o nº de cliques encurta; cliques
   dentro da página final (abas) continuam contando.
5. **Julgar** as perguntas sim/não: Jev `noul` em lote, 1 chamada. Controles (filtro, exportação) são julgados pelos
   elementos da página; informação exibida, pelo conteúdo. Texto que descreve um recurso de outro site é evidência de nada.
6. **Contar**, em duas etapas. (a) Jev escolhe (1 chamada) quais **abridores** da página-alvo (combobox customizado,
   botão, aba, acordeão) escondem os itens pedidos; cada aceito é aberto, o que surge no snapshot vira candidato
   "revelado ao abrir X" (com ArrowDown/digitação para autocomplete, rolagem em lista virtualizada, sub-abridores
   até `max_reveal_depth`), e o abridor é fechado. (b) Jev `noul` por candidato revelado + visível, em lotes: a
   resposta é o nº de "sim". Sem nenhum candidato enumerável, o LLM responde um inteiro (fonte `llm` no relatório).

### Política em código

- **Viável** para clicar: não é a página atual, não foi visitado nem clicado, não é documento (PDF/XLS) nem página
  de captcha, e é do próprio site. Só elementos viáveis entram na tabela do Jev: inviáveis absorvem probabilidade e
  esvaziam a fila de alternativas.
- Links de outro domínio só entram no refinamento (passo 3), e mesmo assim só os do **conteúdo**: ficam fora
  cabeçalho/menus/rodapé, raiz de outro site, portais de outros entes/poderes (`gov.br`, `jus.br`, `mp.br`,
  `leg.br`) e redes sociais. Sem essa poda, o rodapé "Transparência Nacional" vence quando o alvo diz "federais".
- O Jev lê só o **conteúdo principal** (sem linhas de menu, sem cabeçalho/rodapé, sem placeholders "carregando").
  Isso pesa mais que qualquer limiar: P(alvo) na página certa foi de 0,40 a 0,92 com o mesmo modelo.
- Página "já avaliada" é identificada pelo **hash do snapshot**, porque abas e SPAs mudam conteúdo sem mudar URL.
- Cookies, nova aba e desafio anti-robô são tratados em código, nos dois modos.

### Playwright MCP: o que sempre vale

- `--isolated`: perfil novo por execução. O perfil persistente guarda estado que faz o site ignorar cliques.
- User-agent de navegador comum e `--timeout-action 15000`: portais públicos resetam "HeadlessChrome" e demoram
  mais de 5 s após um clique. Clique com timeout pode ter navegado: re-inspecione a página antes de decidir.
- Refs vêm com prefixo de frame (`f1e24`) após navegação e dentro de iframes; a regex aceita ambos.
- Decida sobre um snapshot **estável**: dois snapshots iguais e título não vazio. Painéis embutidos (Power BI)
  montam depois: espere enquanto os placeholders de carga diminuem.
- A árvore de acessibilidade já contém a página inteira; rolar só importa em listas virtualizadas.
- Nome de um elemento: nome ARIA; senão, o texto ou `alt` do filho (`generic [cursor=pointer]: Texto`,
  `link > img "banner"`). Nomes genéricos ou repetidos ("Clique para exportar") ganham o texto do filho para se
  distinguir ("Exportar xlsx" / "Exportar csv").

## Estrutura

- `jev_playwright/explorer.py` — modo jev (os seis passos acima) e `parse_answer`/`format_answer`
- `jev_playwright/llm_explorer.py` — baseline llm-only
- `jev_playwright/browser.py` / `elements.py` — Playwright MCP; tabela de elementos, conteúdo principal, candidatos
  de contagem, abridores, cookies, abas, captcha
- `jev_playwright/jev_client.py` / `llm_client.py` — clientes do Jev e do OpenRouter
- `jev_playwright/checklist.py` / `entes.py` / `report.py` — checklist, CSV de entes, relatórios
- `run_checklist.py` / `compare.py` — linha de comando
- `checklists/gold.json` — gabarito dos portais já verificados à mão; `reports/` — execuções

Limites conhecidos: `portaldatransparencia.gov.br` bloqueia (HTTP 405) após execuções seguidas;
`transparencia.prefeitura.sp.gov.br` mostra CAPTCHA no primeiro clique.
