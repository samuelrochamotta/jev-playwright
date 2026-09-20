# jev-playwright — checklist de portais de transparência com Jev + Playwright MCP

Experimento: executar um checklist (Markdown) de perguntas contra portais de transparência, onde o **Jev**
(TypeSafe System One) toma as decisões de navegação e julgamento e o **Playwright MCP** executa no navegador.
Um **LLM** via OpenRouter existe só como baseline de comparação (`--mode llm-only`) e como fallback estrito de
contagem no modo jev.

O Jev **não gera texto** nem chama ferramentas: ele responde perguntas tipadas (`choice`, `noul`, `score`) sobre um
estado JSON, devolvendo **probabilidades** em ~300–500 ms. Por isso o loop de agente fica em Python (política em
código) e **toda pergunta do checklist admite só resposta sim/não ou um número inteiro** — não há texto livre.

## Setup

```bash
conda activate jev-playmcp
pip install -r requirements.txt
cp .env.example .env   # e preencha OPENROUTER_API_KEY
```

Node ≥ 18 é necessário (o servidor MCP roda com `npx -y @playwright/mcp@latest`).

## Uso

```bash
# o mesmo checklist contra vários portais (--url pode repetir)
python run_checklist.py checklists/checklist.md --url https://transparencia.pbh.gov.br/ --ente "Belo Horizonte/MG" \
    --url https://transparencia.recife.pe.gov.br/ --ente "Recife/PE"
python run_checklist.py checklists/checklist.md --url URL --mode llm-only
python run_checklist.py checklists/checklist.md --url URL --headed --only 1      # só a seção 1, navegador visível

# comparar jev × llm-only, com gabarito opcional
python compare.py checklists/checklist.md --url URL1 --url URL2 --gold checklists/gold.json [--runs 3]
```

Cada execução é gravada na pasta do ente (criada na primeira execução), numa subpasta com data e hora:

```
reports/
└── Goiânia-GO/                       # --ente "Goiânia/GO" (a barra vira hífen)
    └── execucao_20260919230000/
        ├── report-jev.md
        └── report-jev.json
```

O i-ésimo `--ente` corresponde ao i-ésimo `--url`. Sem `--ente`, vale a linha `Ente:` do checklist e, na falta
dela, o host da URL. Os relatórios trazem respostas, tipo e fonte de cada pergunta, cliques, caminho mais curto,
rastro da exploração, chamadas e custo por seção. O código de saída é 1 se alguma seção terminou com erro.

## Formato do checklist

```markdown
Ente: Goiânia/GO           # opcional; nomeia a pasta de resultados (--ente sobrepõe)
URL: https://site          # opcional; --url sobrepõe

# 1 Existência de seção de emendas parlamentares federais
1.1 - O portal possui seção específica de emendas parlamentares federais?
1.2 - Quantos cliques são necessários para encontrar a seção?
1.3 - A seção permite filtrar por ano?
1.4 - Quantos formatos de exportação a seção oferece?
```

| Elemento | Formato | Significado |
|---|---|---|
| Seção | `# N Título` | O **título descreve o alvo** (a página/seção a encontrar); todas as perguntas da seção se referem a esse alvo |
| Pergunta | `N.M - Pergunta?` | Deve admitir resposta **sim/não** ou **número inteiro** |

Os quatro tipos de pergunta (o Jev classifica cada uma, 1 chamada por seção):

| Tipo | Resposta | Exemplo | Como o modo jev responde |
|---|---|---|---|
| `encontrar` | sim/não | O portal possui seção de X? | DFS guiada pelo Jev encontrou o alvo (P ≥ `ARRIVE_THRESHOLD`) |
| `cliques` | inteiro | Quantos cliques até a seção? | Profundidade do caminho vencedor (com atalho em código) |
| `julgar_pagina` | sim/não | A seção tem filtro por ano? | Jev `noul` em lote sobre a página-alvo, 1 chamada |
| `contar` | inteiro | Quantos filtros / colunas / anos / formatos? | abre os dropdowns/menus que escondem os itens (Jev `noul` escolhe quais); Jev `noul` em lote sobre os candidatos revelados + visíveis; a resposta é o nº de "sim" |

O gabarito (`--gold`) é `{"<host>": {"1.1": "sim", "1.2": "2"}}`; `compare.py` compara as respostas como booleano
ou inteiro (nunca como texto) e `indeterminado` nunca casa.

Respostas quando o alvo não é encontrado: `encontrar` → `não`; `cliques` → `indeterminado`; `julgar_pagina` → `não`;
`contar` → `0`. Se a exploração esbarrou num desafio anti-robô, todas viram `indeterminado (bloqueio anti-robô)`.

## Modo jev × modo llm-only

| Etapa | jev | llm-only |
|---|---|---|
| Tipo de cada pergunta | Jev `choice`, **1 chamada por seção** (uma cabeça por pergunta): encontrar / cliques / julgar_pagina / contar | o LLM interpreta livremente |
| Encontrar a seção | DFS guiada pelo Jev: **1 chamada por página** com 5 cabeças na mesma requisição — `chegou` (noul: seção dedicada ao *tema* do alvo), `exato` (noul: qualificadores como "federais" já satisfeitos?), `acao` (choice), `alvo_clique` (ranking só dos links ainda viáveis), `alvo_busca` | LLM com tool calling lê o snapshot inteiro a cada turno |
| Refinamento na seção | se `chegou` alto e `exato` baixo, até 2 cliques extras dentro da seção (aba "Federais", filtro "Federal") guiados por `alvo_clique`; cada passo só é aceito se `exato` sobe mantendo o tema | o LLM decide |
| Backtracking | as probabilidades do ranking viram a fila de alternativas; becos sem saída voltam **sem chamada ao modelo** | o LLM decide |
| Poda | em código: links já visitados, já clicados, fora do site, documentos (PDF/XLS), páginas de captcha | – |
| Nº de cliques | profundidade do caminho vencedor; atalho em código se uma página anterior já linkava direto para a URL final (cliques dentro da página final, como abas, continuam contando) | o LLM conta |
| Perguntas sim/não sobre a página-alvo | Jev `noul` **em lote, 1 chamada** | LLM |
| Contagem (`contar`) | 1) Jev `noul` (1 chamada) sobre os "abridores" da página-alvo (combobox customizado, botão, aba, acordeão, link sem URL própria): "abrir isto revela os itens pedidos?"; os aceitos (P ≥ 0,5, até `MAX_OPENERS`) são clicados, o que surge de novo no snapshot vira candidato "revelado ao abrir X", e o abridor é fechado (Escape → reclique → recarga); 2) candidatos revelados + visíveis (filtros, campos, colunas, opções, abas, links, títulos), deduplicados; Jev `noul` por candidato em lotes de 25 ("é um dos itens pedidos?"); resposta = nº de "sim" | LLM |
| Cookies / nova aba / desafio anti-robô | política em código, nos dois modos | idem |

**Fallback de LLM em `contar`.** Só quando a página-alvo não tem nenhum candidato enumerável (snapshot sem elementos
nomeados — por exemplo, conteúdo inteiro dentro de um iframe/painel embutido) o modo jev chama o LLM uma vez com a
instrução de responder **somente um inteiro**; a saída é parseada e vira `0` se não houver número. A coluna
"Fonte" do relatório mostra `llm` nesse caso. Fora disso o modo jev não gasta tokens de LLM.

No modo llm-only o prompt exige `sim`/`não` ou inteiro e a saída é normalizada (`parse_answer`): qualquer outra
coisa vira `indeterminado`.

### Resultado de referência (checklist de emendas parlamentares, LLM `z-ai/glm-5.3-flash`)

| Portal | Gabarito | jev | llm-only |
|---|---|---|---|
| transparencia.pbh.gov.br | sim / 2 cliques | sim / 2 — **19 s**, 4 Jev, 0 LLM, US$ 0,0013 | sim / 2 — 54 s, 4 LLM, 37 mil tokens, US$ 0,0044 |
| transparencia.fortaleza.ce.gov.br | sim / 1 clique | sim / 1 — **8,5 s**, 3 Jev, 0 LLM, US$ 0,0006 | sim / 1 — 60 s, 5 LLM, 61 mil tokens, US$ 0,0044 |
| transparencia.recife.pe.gov.br | sim / 3 cliques (menu → 2024 → filtro "Federal") | sim / 3 — 22 s, 5 Jev, 0 LLM | – |
| www.goiania.go.gov.br/transparencia | sim / 2 cliques (filtro "Emendas Parlamentares" → "Painel Emendas Parlamentares 2026") | sim / 2 — **39 s**, 4 Jev, 0 LLM, US$ 0,0008 | sim / 3 — 482 s, 17 LLM, 393 mil tokens, US$ 0,0213 |

Acurácia contra o gabarito: jev 6/6, llm-only 5/6 (errou os cliques em Goiânia). Tempo total nos três portais
comparados: jev 66 s, llm-only 596 s.
O ganho do Jev é de **velocidade e custo com precisão igual**, e vem de três coisas: uma requisição por página
(quatro decisões de uma vez), backtracking pelas probabilidades sem novas chamadas, e zero tokens de LLM.

Limitações observadas nos testes:
- `portaldatransparencia.gov.br` passou a responder HTTP 405 (WAF) após algumas execuções em sequência.
- `transparencia.prefeitura.sp.gov.br` mostra um CAPTCHA da Prodam no primeiro clique; o agente detecta a página
  de desafio e responde `indeterminado (bloqueio anti-robô)` em vez de `não`.
- A busca é em profundidade limitada (`MAX_DEPTH`, `MAX_PAGES_PER_FIND`); o número de cliques é o do melhor caminho
  encontrado, não um mínimo garantido.
- Conteúdo dentro de iframes não aparece no snapshot de acessibilidade — afeta `julgar_pagina` e `contar` em
  painéis embutidos (Power BI etc.).
- `contar` abre até `MAX_OPENERS` dropdowns/menus customizados por pergunta (um clique cada) para revelar itens
  escondidos; listas que exigem mais de um nível de abertura, rolagem interna ou digitação (autocomplete) continuam
  fora. Em Recife, "quantos anos" só responde 4 (2023–2026) porque o seletor "Ano da Emenda" é aberto antes de contar.

## Estrutura

- `jev_playwright/jev_client.py` — cliente do endpoint de decisões (`choice` / `noul` / `score`)
- `jev_playwright/llm_client.py` — cliente chat/completions do OpenRouter (baseline e fallback de contagem)
- `jev_playwright/checklist.py` — parser de seções e perguntas
- `jev_playwright/explorer.py` — **modo jev**: classificação, DFS guiada, refinamento, atalho, julgamento e
  contagem em lote; `parse_answer`/`format_answer` (normalização sim/não/inteiro)
- `jev_playwright/llm_explorer.py` — baseline llm-only
- `jev_playwright/browser.py` / `elements.py` — Playwright MCP, tabela de elementos (href, deduplicação,
  priorização por palavras-chave, conteúdo principal sem navegação), candidatos de contagem, cookies, abas, captcha
- `jev_playwright/report.py` — relatórios
- `run_checklist.py` / `compare.py` — linha de comando

## Lições da depuração

- **`--isolated` é obrigatório.** O Playwright MCP reutiliza um perfil persistente entre execuções; estado antigo
  fazia o site ignorar cliques reais. Perfil novo resolve.
- **User-agent normal e `--timeout-action 15000`.** Portais públicos resetam a conexão do "HeadlessChrome" e
  demoram mais de 5 s para navegar após um clique; um clique com timeout **não** significa que nada aconteceu — o
  agente re-inspeciona a página antes de decidir.
- **Refs com prefixo de frame.** Após navegação os refs vêm como `f1e24`, não `e24`; a regex precisa aceitar ambos.
- **Esperar o snapshot estabilizar.** Dois snapshots iguais e título não vazio antes de perguntar ao Jev; senão
  ele julga uma página em branco.
- **Rolar não muda o snapshot.** A árvore de acessibilidade já contém a página inteira.
- **Abas/SPA mudam conteúdo sem mudar URL.** Poda de "já avaliada" por hash do snapshot, não por URL.
- **O que o Jev lê importa mais que o limiar.** Enviar só o conteúdo principal (sem linhas de menu) e perguntar
  "esta página é uma seção *dedicada* ao assunto?" subiu P(alvo) de 0,40 para 0,92 na página certa, mantendo
  < 0,07 nas erradas.
- **Caso Goiânia (falso "não").** Três causas, três correções: (1) o filtro "Emendas Parlamentares" era um
  `generic [cursor=pointer]: Texto` sem nome ARIA e o parser o descartava — agora o texto vira o nome do elemento;
  (2) a tabela enviada ao Jev incluía links inviáveis (a própria página, já visitados, externos), que absorviam
  a probabilidade e esvaziavam a fila de alternativas — agora só entram elementos clicáveis viáveis;
  (3) o painel "Emendas Parlamentares 2026" (Power BI) ficava em P=0,60 porque o alvo dizia "federais" — a
  pergunta `chegou` passou a julgar pelo *tema* (P=0,94) e uma cabeça separada `exato` cuida dos qualificadores,
  com refinamento por cliques dentro da seção (é o que leva à aba "Federais" em BH e ao filtro "Federal" em Recife).
- **Política em código** para não repetir (página, ação, alvo) já executados.
