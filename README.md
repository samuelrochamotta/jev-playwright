# jev-playwright

Pesquisa e avalia portais municipais de transparência com **Jev como decisor tipado** e **Playwright MCP como executor**. O fluxo é controlado em Python; o modelo não executa código nem declara sucesso livremente.

O modo padrão agora usa **busca best-first**, re-ranking independente de candidatos, restauração de rotas e comprovação de qualificadores. `--strategy dfs` preserva o explorador anterior como baseline, com a nova semântica de abstenção.

## Instalação

Python ≥ 3.11, Node ≥ 18 e Google Chrome instalado:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Preencha OPENROUTER_API_KEY
```

O servidor MCP está fixado em `@playwright/mcp@0.0.83`, validado por teste de contrato com Chrome real. O cliente Jev continua usando a integração existente `/api/alpha/decisions`; a migração de endpoint/SDK não foi feita sem teste autenticado de compatibilidade.

## Uso

### Avaliar interface/checklist

```bash
python run_checklist.py checklists/checklist.md \
  --url https://transparencia.aracaju.se.gov.br/prefeitura/ --ente "Aracaju/SE"

python run_checklist.py checklists/checklist.md --entes entes.csv
```

### Pesquisar e coletar dados públicos

```bash
python run_checklist.py checklists/checklist.md \
  --url https://transparencia.aracaju.se.gov.br/prefeitura/ --ente "Aracaju/SE" \
  --collect --recipes --max-pages 24 --max-depth 6
```

`--collect` exige identificação do ente e modo `jev`. Além de tema e qualificadores, a página precisa identificar o município e disponibilizar uma consulta ou arquivo. Documentos relevantes descobertos podem ser coletados mesmo quando não há página HTML-alvo; o resultado de descoberta continua inconclusivo nesse caso.

### Fallback e baseline

```bash
# Opcional: LLM propõe uma ação quando os caminhos são insuficientes/incertos.
python run_checklist.py checklists/checklist.md --entes entes.csv --fallback

# Opcional: configure LLM_MODEL com suporte a imagens antes de usar visão.
python run_checklist.py checklists/checklist.md --entes entes.csv --vision-fallback

# DFS anterior, com resultados inconclusivos tratados corretamente.
python run_checklist.py checklists/checklist.md --entes entes.csv --strategy dfs

# Baseline generativo, separado da coleta verificada.
python run_checklist.py checklists/checklist.md --entes entes.csv --mode llm-only

python compare.py checklists/checklist.md --entes entes.csv \
  --gold checklists/gold.json --runs 3
```

Outras flags: `--headed`, `--only 1,3`, `--ente-filtro`, `--out`; veja `--help`.

## Resultados: não encontrado ≠ inexistente

| Status | Significado |
|---|---|
| `encontrado` | Página-alvo verificada pelo Jev conforme o objetivo |
| `busca_inconclusiva` | Alternativas/orçamento esgotados ou qualificadores não comprovados |
| `bloqueado` | Desafio anti-robô impediu a pesquisa |
| `erro` | Falha de execução/contrato |
| `encontrado_declarado` | Apenas no baseline llm-only: declaração do LLM, não prova de sucesso |

Uma busca inconclusiva retorna **indeterminado** em todas as perguntas dependentes do alvo, com confiança indisponível; nunca `não`/`0` por inferência de ausência. O sistema não emite `ausencia_verificada`: ainda não há mecanismo de cobertura exaustiva que sustente essa conclusão.

A probabilidade de chegada de uma página não mede completude do portal. O relatório separa status, motivo de parada, `P(exato)`, município, cobertura e semântica da confiança. Contagens sem candidatos enumeráveis também permanecem indeterminadas.

Código de saída 1 se houver erro ou pesquisa/coleta inconclusiva. Um ente que falha não interrompe o lote. Coleta parcial com registros/evidências não é declarada completa.

## Como a navegação funciona

1. Jev classifica as perguntas (`choice`) em encontrar, cliques, julgar página ou contar.
2. O código monta um shortlist com diversidade: mantém menus genéricos de receitas, transferências, convênios e campos de busca, não apenas correspondências literais de “emendas”.
3. Jev avalia os candidatos **independentemente** (`noul` em lote), junto com tema, qualificadores, município e disponibilidade de consulta. O score da fila é uma heurística de prioridade, não probabilidade conjunta calibrada.
4. Uma fila best-first mantém rotas alternativas. Estados ignoram refs/foco/relógios; ações incluem estado, alvo semântico e argumentos. Prefixos físicos de frame do MCP não são identidade estável.
5. Ao voltar a uma alternativa, a aplicação reproduz a rota a partir da origem oficial, incluindo hovers e buscas. Cada ação exige precondição e uma referência nova; se o estado mudou, a rota é descartada.
6. A mesma consulta no mesmo campo/URL não é expandida repetidamente. Há variantes da busca, orçamento de páginas/tentativas/profundidade e limite de erros de ação. Timeout é seguido de reinspeção: pode ter navegado.
7. Links a sistemas terceiros podem entrar desde a descoberta, inclusive em menus/raízes de fornecedor. Destinos externos precisam identificar o ente antes da expansão/sucesso. Redes sociais e portais nacionais/judiciais alheios não são a seção municipal.
8. Documentos são encaminhados para coleta, não tratados como controles HTML. Clique não é necessário para arquivos reconhecidos pela extensão.
9. No alvo, perguntas booleanas e contagens continuam tipadas; abridores/dropdowns/listas virtualizadas são explorados em código. Caminho encontrado não é garantia de mínimo global de cliques.

Sem `--ente`, a auditoria usa o portal fornecido como origem, mas não pode comprovar a identidade de um município em um sistema terceiro. Use `--ente Nome/UF` em pesquisas reais.

## Coleta, proveniência e segurança

`--collect` observa tabelas renderizadas, inclusive frames acessíveis pelo MCP, links de documentos e URLs de requisições **GET** da própria consulta. Seleção de fontes e validação do conteúdo/linhas/trechos são feitas pelo Jev; o cálculo e a normalização são determinísticos.

Formatos implementados:

- CSV, com detecção de delimitador e texto UTF-8/CP1252;
- XLSX em modo somente leitura, sem macros/links executáveis;
- JSON: arrays de objetos, com localização no documento;
- tabelas HTML renderizadas;
- PDF textual, como trechos com número da página — não como registros financeiros inventados.

Saídas em:

```text
reports/<Ente-UF>/execucao_<data>/
  report-jev.json
  report-jev.md
  artifacts/section-1/
    collection.json
    <sha256>.bin     # arquivos originais
    <sha256>.json    # tabelas HTML observadas
```

Cada registro preserva dados brutos, campos normalizados, probabilidade de relevância e proveniência (URL, hash, arquivo e linha/localização). Valores monetários usam `Decimal`, serializados como strings; datas reconhecidas são normalizadas em código para ISO; campos ausentes são `null`. Valor previsto/transferido/empenhado/liquidado/pago não são intercambiáveis. Linhas idênticas sem identificador de emenda não são automaticamente colapsadas.

Downloads usam apenas GET público, sem copiar cookies, cabeçalhos de autenticação ou corpos de requisição. URLs com parâmetros de credenciais são rejeitadas. Hosts precisam estar no escopo autorizado; DNS privado/local/misto é rejeitado, a conexão é fixada no IP público validado e redirects não podem expandir o escopo. Há limites de bytes, fontes, registros e descompactação de planilhas. Não há bypass de CAPTCHA; fontes que exigem autorização ficam pendentes.

**Cobertura sempre explícita e conservadora:** paginação, todos os órgãos e completude do conjunto de registros não são comprovados automaticamente. APIs paginadas não são enumeradas inventando parâmetros. OCR, XLS/ODS, exportações sem URL, POST autenticado e painéis exclusivamente visuais exigem adaptação/revisão. Arquivos inválidos não interrompem a coleta de outras fontes.

## Receitas e fallback

`--recipes` guarda rotas deste portal/ente/alvo em `.jev-recipes/`, com revalidação de precondições e do destino. Receitas inválidas são descartadas e a descoberta continua.

Também guarda **dicas semânticas por família de fornecedor** (Municipio Online, NúcleoGov, Betha; host específico para fornecedores desconhecidos). As dicas contêm nomes/papéis, nunca URLs/IDs de outro município. Só são usadas após identificação do ente atual, precisam corresponder a um único controle atual e não dispensam comprovação do destino. Dados financeiros nunca são usados como cache de rota.

`--fallback` permite, por padrão, uma proposta generativa por seção. O LLM só escolhe operação/índice entre controles atuais e uma das buscas permitidas. Não fornece URLs arbitrárias, não executa JavaScript e não julga sucesso. O Jev verifica o estado resultante. `--vision-fallback` acrescenta um screenshot ao contexto da proposta, apenas nessa escalada; requer um modelo multimodal configurado em `LLM_MODEL`. Não executa cliques por coordenadas nem computer-use irrestrito: telas sem controles acessíveis continuam exigindo adaptação/revisão.

## Configuração e comparação

Orçamentos em `jev_playwright/config.py`/`.env.example`: `MAX_PAGES_PER_FIND`, `MAX_DEPTH`, `CANDIDATE_LIMIT`, `FRONTIER_LIMIT`, `MAX_ACTION_ERRORS`, `MAX_SOURCES`, `MAX_DOWNLOAD_BYTES`, `MAX_RECORDS` e limites de contagem/carregamento.

`compare.py` calcula acurácia/concordância em **todas** as execuções de `--runs`; a tabela de respostas identifica que mostra a última execução. O lookup de gabarito aceita host/caminho com ou sem `www`/barra final. Os relatórios registram modelo configurado/efetivo (quando devolvido pela API), versão MCP e versão da política.

## Testes

Sem browser ou chamadas pagas:

```bash
python -m unittest discover -s tests -v
```

Contrato e pipeline com Chrome/MCP reais contra um portal local, usando decisões simuladas e sem chamadas pagas:

```bash
JEV_BROWSER_TESTS=1 python -m unittest discover -s tests -p test_browser_integration.py -v
```

A suíte cobre abstenção, qualificadores, identidade externa, repetição, recuperação, receitas, parsers, limites, segurança de download, proveniência, respostas incompletas/retry e comparação entre execuções.

A implementação foi validada em fixtures e testes locais; **não constitui benchmark de generalização em portais municipais reais**. A pesquisa e o plano desse benchmark estão em [docs/pesquisa-navegacao-jev.md](docs/pesquisa-navegacao-jev.md).

## Estrutura

- `graph_explorer.py` / `navigation.py`: best-first, ações, receitas e fallback;
- `explorer.py`: perguntas tipadas, DFS baseline, julgamento e contagem;
- `collection_orchestrator.py` / `collection.py`: seleção, validação, coleta e proveniência;
- `browser.py` / `elements.py`: MCP e observação semântica;
- `jev_client.py` / `llm_client.py`: clientes de inferência;
- `checklist.py` / `entes.py` / `report.py`: entradas e relatórios;
- `run_checklist.py` / `compare.py`: execução e comparação.
