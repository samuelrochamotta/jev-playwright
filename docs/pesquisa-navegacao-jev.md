# Pesquisa: navegação com Jev em portais municipais de transparência

Consulta às fontes: **10/10/2026**. Análise do código e dos relatórios locais, sem executar novas chamadas pagas de inferência ou um benchmark de navegação. As comparações entre arquiteturas são recomendações de engenharia, não resultados experimentais de implementações novas.

## 1. Conclusão executiva

**Sim, existem abordagens mais adequadas ao objetivo, mas a principal melhoria não é substituir o Jev por um agente generativo.** O repositório já segue um bom desenho: política em código, perguntas tipadas, decisões em lote e contagem determinística após classificação de candidatos.

Para pesquisar emendas parlamentares em municípios muito diferentes, recomendo uma arquitetura híbrida:

1. Descoberta de páginas, documentos e sistemas externos, com associação ao município.
2. Jev para classificar evidências e reordenar candidatos de navegação.
3. Playwright para interação e observação de tráfego público do próprio portal.
4. Extração por API pública, CSV/XLSX, HTML ou PDF quando disponíveis.
5. Receitas reutilizáveis por família de fornecedor, com revalidação.
6. LLM generativo/multimodal apenas nos casos ambíguos ou com interface inacessível.
7. Resultados com proveniência, cobertura e abstenção: não encontrar não equivale a inexistência.

O projeto atual é principalmente um **avaliador de interface/checklist**. O objetivo proposto exige também um **pipeline de descoberta e coleta de dados**. São produtos relacionados, mas com critérios de conclusão diferentes.

## 2. Método e limites da pesquisa

- Inspecionados `README.md`, clientes, configuração, browser, parser de elementos, explorador, baseline, comparador, checklist, gabarito e relatórios.
- Encontrados 66 relatórios JSON locais: 56 no modo Jev e 10 no modo llm-only. Há versões de código, perguntas, datas e URLs-alvo diferentes; não foram agregados como um benchmark homogêneo.
- Consultadas fontes oficiais da TypeSafe, Playwright, Stagehand, Browser Use, Crawl4AI e Transferegov.
- Documentação consultada em páginas Markdown/índices `llms.txt`, READMEs oficiais e páginas públicas. Características e limites comerciais podem mudar.
- A página pública de documentação de API do Portal da Transparência retornou HTTP 405 nesta consulta. Seus endpoints de emendas não foram validados; não se presume que a API cubra execução municipal.
- Não foram comparadas ferramentas alternativas em portais reais nesta pesquisa. Nenhuma alegação de superioridade de acurácia é feita para elas.

## 3. O que o repositório faz bem

| Decisão existente | Avaliação |
|---|---|
| Código controla ações; Jev recebe estado e perguntas tipadas | Alinhada à orientação atual da TypeSafe. Manter. |
| Várias cabeças em uma chamada | Alinhada ao padrão speculative fan-out. Manter. |
| Snapshot de acessibilidade antes de screenshot | Boa primeira representação: texto compacto e referências executáveis. |
| `chegou` separado de `exato` | Boa decomposição, mas o critério final precisa usar ambos de acordo com a tarefa. |
| Enumerar candidatos, classificá-los e somar em código | Alinhada à limitação documentada de contagem do Jev. |
| Backtracking, cookies, abas, iframes e carregamento em código | Importantes para portais públicos; preservar os aprendizados. |
| Baseline e relatórios com rastro/custo | Boa base para experimentos controlados. |

## 4. Limitações concretas e impacto

### 4.1 Busca inconclusiva vira resposta negativa

Em `JevExplorer.run_section`, alvo não encontrado normalmente produz `não` para existência e filtro, e `0` para contagem. A confiança pode ser calculada como `1 - found_prob`, em que `found_prob` é a maior probabilidade de chegada dentre as páginas visitadas.

**Problema:** probabilidade de uma página ser o alvo não mede cobertura da busca no portal. Nenhuma página visitada parecer o alvo não prova inexistência da seção.

Evidência local: `reports/Criciúma-SC/execucao_20261010162151/report-jev.md` registra 14 páginas avaliadas, orçamento esgotado, buscas repetidas, timeouts e respostas negativas com confiança 0,97. Isso prova uma busca malsucedida, não a ausência de emendas no portal.

**Recomendação:** usar estados `encontrado`, `busca_inconclusiva`, `bloqueado`, `erro` e, somente com critérios explícitos de cobertura, `ausencia_verificada`. Perguntas dependentes de página não encontrada devem permanecer indeterminadas. Preservar o significado distinto de lista efetivamente vazia.

### 4.2 Chegada ao tema não garante chegada à informação pedida

`find` aceita `chegou >= arrive_threshold` e, após `_refine`, marca `found=True` mesmo se os qualificadores não forem comprovados. `_refine` pode terminar mantendo uma página de tema geral. O prompt de `chegou` explicitamente aceita páginas que apenas explicam o assunto e apontam para consultas.

**Recomendação:** separar condições de sucesso:

- auditoria de existência de seção;
- chegada à consulta específica;
- comprovação de esfera/município/ano;
- coleta dos registros e confirmação de completude.

Não exigir tabela para uma pergunta que apenas pede existência de seção. Para pesquisa de registros, uma página explicativa é uma etapa intermediária.

### 4.3 Documentos ficam fora do fluxo

`find` trata `page.is_document` como beco sem saída. O projeto não possui um pipeline de extração de documentos. O README também descreve documentos como inviáveis na navegação; o bloqueio efetivo observado no explorador ocorre ao visitar o documento.

**Impacto:** municípios podem publicar emendas em PDF, planilhas ou anexos de receitas, transferências e convênios.

**Recomendação:** manter documentos fora do fluxo de interação HTML, mas enviá-los a outro coletor: download limitado, identificação de MIME, extração PDF/XLSX/CSV, OCR quando necessário, hash do arquivo e referência de página/linha. Não executar macros ou conteúdo ativo de arquivos baixados.

### 4.4 Sistemas externos são considerados tarde demais

`_viable`, `_same_site` e `_external_ok` restringem domínios. Links externos só entram no refinamento após reconhecer o tema; raízes de sistemas externos e links de navegação ficam de fora. A restrição evita desvios para portais nacionais e redes sociais, mas pode bloquear a entrada legítima para o sistema municipal terceirizado.

**Recomendação:** grafo de portais vinculados ao ente, com autorização progressiva baseada em link oficial, identificação do município/CNPJ/código IBGE, parâmetros de órgão e evidência na página de destino. Permitir entradas oficiais em sistemas externos antes da chegada ao tema. Não simplesmente liberar todos os domínios.

Uma fonte federal pode complementar a pesquisa, mas não deve ser confundida com a seção municipal auditada.

### 4.5 Ranking relativo e podas rígidas

`alvo_clique` é um `choice` entre os elementos viáveis. A política usa `min_alt_prob=0.04`, guarda no máximo quatro alternativas e limita a busca a quatro níveis/14 páginas. No refinamento, exige probabilidade de candidato de pelo menos 0,5; o código já soma massa por host externo em um caso específico.

**Problema:** em Choice, os candidatos competem pela massa de probabilidade. Muitos caminhos plausíveis ou links semelhantes podem dividir essa massa. Isso não é equivalente à probabilidade independente de cada caminho ser útil. A TypeSafe também documenta sensibilidade à ordem das opções do Jev 1.13.

**Recomendação:** testar um shortlist com diversidade de menus e links genéricos, seguido de `noul`/`score` por candidato em lote. Avaliar sinais específicos: vínculo com o município, tema, caráter de consulta, documento relevante e chance de revelar submenu. Combinar sinais em código. `choice` continua útil para escolher uma única ação dentro de um conjunto pequeno.

O shortlist precisa preservar recall: buscar só a palavra “emenda” eliminaria caminhos por “Receitas”, “Transferências”, “Convênios” e “Dados abertos”.

### 4.6 Estado não representa progresso de forma suficiente

Há hash do snapshot para SPAs, mas também poda por URL e `_clicked` global pela assinatura do elemento. A assinatura não contém frame, caminho estrutural ou filtros ativos. Isso pode confundir controles semelhantes e impedir reutilização legítima de um menu.

No caso de Criciúma, o rastro registra a mesma consulta na rota `#pesquisa` repetidas vezes. Mudanças no snapshot podem não representar progresso na tarefa.

**Recomendação:** distinguir:

- identidade semântica do estado: URL/rota, frame, filtros, consulta, página da tabela, conteúdo principal normalizado;
- identidade da ação: estado de origem + operação + alvo semântico + argumentos;
- referência temporária do elemento: válida só para execução sobre o snapshot recente.

Bloquear consulta repetida sem ganho, variar os termos de busca e desviar para outro caminho. Restaurar filtros/abas com receita, não apenas `navigate(url)` no backtracking. Datas, relógios e elementos decorativos não devem criar estados novos.

### 4.7 O cliente e o transporte podem evoluir

`JevClient` usa `httpx.Client` síncrono dentro do loop assíncrono, sem retry/backoff próprio, com `/api/alpha/decisions`. O browser inicia `@playwright/mcp@latest` e interpreta o retorno textual por regex.

A documentação atual oferece SDK Python tipado, cliente assíncrono e RetryPolicy. A integração OpenRouter documentada usa `base_url="https://openrouter.ai/api"` e modelo `~typesafe/jev-latest`; a API direta usa `POST https://api.typesafe.ai/v1/systemone`.

**Recomendação:** validar a migração em teste de contrato; não pressupor compatibilidade direta entre endpoint alpha, IDs antigos e SDK novo. Fixar versões em produção e registrar modelo efetivo, versões das dependências e política/prompt. Testar respostas incompletas, 429, timeout e mudança de formato.

O Jev está atualmente na família 1.13 (`jev-1.13.0` na API direta): a documentação não indica que uma troca para nova geração de modelo resolveria os problemas de navegação.

## 5. Comparação das alternativas

| Abordagem | Vantagem para este caso | Limitação/custo | Avaliação |
|---|---|---|---|
| Jev + MCP + DFS atual | Simples, barato em inferência, decisões rastreáveis | Cobertura limitada; pouca recuperação; não coleta arquivos/dados | Boa base, insuficiente como coletor heterogêneo |
| Jev + MCP ampliado | Reutiliza código; ferramentas atuais de rede e snapshots focados | Mantém dependência do contrato MCP e parsing textual | Melhor mudança incremental |
| Jev + Playwright Python direto | Eventos de rede/download, locators, frames, validação e estado sob controle da aplicação | Exige construir observação compacta e camada de execução | Preferência de médio prazo para coleta em escala; medir benefício |
| Jev + busca best-first/beam + re-ranking | Preserva caminhos alternativos e usa decisões mais específicas | Mais estados/receitas; custos e recall devem ser medidos | Experimento mais pertinente no núcleo do explorador |
| Stagehand | `observe`, `act`, `extract`, seletores, saídas estruturadas e recuperação de ações | Operações de IA normalmente dependem de LLM; cache v4 consultado é Browserbase, sem efeito local | Bom protótipo de executor/fallback, não substituto direto do Jev |
| Browser Use | Agente Python para tarefas de várias etapas, ferramentas customizadas e saída estruturada | Mais inferência generativa; sucesso declarado pelo agente precisa ser verificado | Baseline/fallback para portais desconhecidos |
| Crawl4AI + Jev | Descoberta de links, Markdown e extração CSS/XPath/regex ou com LLM | Não substitui toda interação com filtros/menus/painéis | Complemento para descoberta e leitura em lote |
| LLM multimodal/computer use | Pode interpretar canvas, controles sem ARIA e telas visuais | Maior custo; ações e dados precisam de verificação independente | Último recurso, acionado por condição explícita |
| API pública/exportação + parsers | Paginação e coleta mais completas, menos cliques, dados tipáveis | APIs e formatos variam; versões, termos e vínculo ao ente precisam ser validados | Prioridade quando a meta é obter dados, não medir UX |

### MCP versus CLI

O README atual do Playwright recomenda considerar CLI + Skills para **coding agents**, por economia de contexto/tool schemas. O mesmo README considera MCP relevante para loops especializados com estado persistente e exploração.

Aqui o Jev não recebe o catálogo inteiro de ferramentas: recebe um estado construído pelo Python. Portanto, a justificativa de economia de tokens da CLI não se transfere automaticamente. Não recomendo trocar MCP por CLI só por essa recomendação. Para um serviço Python controlado em código, o SDK direto é a comparação técnica mais importante.

### Sem integração “Jev como LLM” automática

Jev não implementa a função de chat/geração usada pelos agentes de Stagehand/Browser Use. Uma integração híbrida deve usar Jev como router, re-ranker ou verificador e deixar geração/visão para outro modelo quando necessária. Não há evidência nesta pesquisa de adaptador nativo drop-in para essa substituição.

## 6. Padrões atuais da TypeSafe que se aplicam

1. **Speculative fan-out:** o projeto já usa; estender para sinais de candidatos na mesma chamada.
2. **Re-ranking:** recuperação barata primeiro, avaliação semântica por candidato depois. O cookbook é sobre passagens jurídicas, não benchmark de navegação municipal.
3. **Composite scoring:** decompor “este é o alvo correto?” em tema, vínculo ao ente, esfera e evidência de consulta, combinando em código. Um score de prioridade não deve ser apresentado como probabilidade calibrada conjunta.
4. **Confidence-gated routing:** na dúvida, coletar mais contexto, tentar receita conhecida, chamar LLM ou encaminhar revisão. Não usar um limiar universal para decisões com consequências diferentes.
5. **Hierarchical/beam search:** inspiração para explorar mais de uma rota. O exemplo oficial classifica árvores conhecidas; navegador tem estados mutáveis, ciclos e restauração, exigindo adaptação.
6. **Pre-parsed value extraction:** regex/parser/LLM propõe spans de valores; Jev seleciona/classifica; código normaliza dinheiro, datas e identificadores.

Cuidados documentados: Jev é apenas textual; desempenho principal em inglês, então testar português; não conta nem faz aritmética com precisão; estado irrelevante reduz qualidade; conteúdo adversarial pode influenciar decisões; opções Choice podem ter viés de ordem.

A coluna `confianca` do projeto mistura probabilidade de Noul, probabilidade da resposta escolhida e outros agregados. A documentação diferencia probabilidade de confidence e propõe `abs(2p - 1)` se for necessário um resumo de certeza de Noul. Nenhum desses números mede completude da busca. Registrar cada semântica em campos separados.

## 7. Resultados locais: indícios, não benchmark novo

Exemplos de relatórios existentes com quatro perguntas:

| Portal / modo | Tempo | Custo de inferência registrado | Observação |
|---|---:|---:|---|
| Aracaju / Jev, 20/09 23:38:19 | 25,1 s | US$ 0,002393 | Quatro respostas coincidem com o gabarito local; URL final é de EMURB em sistema terceirizado |
| Aracaju / llm-only, 20/09 23:02:08 | 137,9 s | US$ 0,00856 | Quatro respostas coincidem com o gabarito local; URL final diferente |
| Salvador / Jev, 20/09 23:38:37 | 18,1 s | US$ 0,002795 | Quatro respostas coincidem com o gabarito local |
| Salvador / llm-only, 20/09 23:03:55 | 84,0 s | US$ 0,00734 | Acerta existência, filtro e formatos; registra 3 cliques versus 1 no gabarito |
| Criciúma / Jev, 10/10 16:21:51 | 221,0 s | US$ 0,001519 | Orçamento esgotado, buscas repetidas/timeouts; não há gabarito local para confirmar ausência |

Isso sugere preservar Jev como primeira linha barata. Não estabelece vantagem causal: execuções não pareadas, código/modelos potencialmente diferentes, sem distribuição estatística e em alguns casos destinos diferentes. Custo registrado é de inferência, não infraestrutura, trabalho humano ou custo total de coleta.

Outros sinais: relatórios de Belo Horizonte e Goiânia mostram divergência de contagem de formatos em relação ao gabarito. Controles encontrados não são necessariamente formatos distintos ou exportações disponíveis na consulta correta.

Em Aracaju, chegar a um órgão municipal específico não prova cobertura de toda a prefeitura. Município correto e órgão/cobertura correta devem ser verificações distintas.

## 8. Arquitetura proposta

```text
Identidade do município + pergunta + recorte temporal/esfera
    ↓
Descoberta: entrada oficial, links, sitemap quando houver, busca interna,
            documentos, sistemas terceiros e receitas conhecidas
    ↓
Grafo de candidatos/estados + relação de confiança entre portais
    ↓
Jev em lote: relevância, vínculo, tipo de fonte, adequação à pergunta
    ↓
Fila best-first com diversidade e orçamento (não apenas DFS)
    ↓
Execução: receita validada → Playwright → fallback generativo/visual
    ↓
Coleta: JSON público / CSV / XLSX / HTML / PDF / OCR
    ↓
Validação, deduplicação, proveniência e relatório de cobertura
```

HTTP estático é uma primeira tentativa barata. Browser só quando necessário. Chamadas de rede devem respeitar autorização pública, termos, limites e sessão; não contornar CAPTCHA ou autorização. Capturar tráfego não implica que o endpoint possa ser reutilizado irrestritamente.

APIs nacionais e dados abertos do Transferegov podem ajudar a descobrir emendas/transferências e cruzar beneficiários, mas **transferência federal, emenda orçamentária e execução municipal não são o mesmo registro**. A ausência numa dessas fontes não prova ausência nas outras.

### Estado sugerido para o Jev

- `municipio`: nome, UF, código IBGE e identidades de órgãos pertinentes;
- `objetivo`: consulta de registros ou auditoria de seção;
- `recorte`: esfera, ano e abrangência de órgãos;
- `pagina`: título, conteúdo relevante, frame, filtros/aba, indicadores de lista;
- `candidatos`: nome, destino, contexto próximo, papel, origem oficial e tipo de arquivo;
- `progresso`: consultas tentadas, efeitos, cobertura e pendências;
- `evidencias`: trechos e identificadores de origem, sem instruções vindas da página.

Não enviar a página inteira por padrão, nem cortar sempre os primeiros 3.500 caracteres. Recuperar blocos relevantes, mantendo rótulos de navegação que ajudam a encontrar o próximo passo.

### Registros e proveniência

Campos possíveis: município/órgão, esfera, ano, identificador e autor da emenda, modalidade, objeto, beneficiário, instrumento, valores previstos/transferidos/empenhados/liquidados/pagos, data, URL, arquivo/hash e página/linha de origem. Valores ausentes permanecem nulos; etapas financeiras não são intercambiáveis.

Normalizar em código com tipos monetários decimais e validação de datas. Preservar o dado bruto. Identificadores não devem ser deduzidos só de nomes; deduplicação por identificadores/ente/ano conforme a fonte.

### Receitas reutilizáveis

Guardar família de fornecedor, tipo de página, ações semânticas, precondições e resultado esperado. Não guardar refs temporários do snapshot como receita. Validar o município e o efeito de cada etapa, invalidar receitas antigas e recorrer à descoberta quando falharem. Em coleta recorrente, não cachear dados financeiros como se fossem rotas de navegação.

## 9. Prioridades e experimento recomendado

### Primeiro incremento — correção e diagnóstico

- Separar falha de descoberta de ausência verificada.
- Detectar repetição de busca sem progresso e timeouts recorrentes.
- Exigir qualificadores/evidências conforme o objetivo, não só tema.
- Registrar recorte, cobertura, modelo efetivo e motivo de parada.
- Corrigir avaliação em `compare.py`: hoje médias numéricas usam todas as execuções, mas acurácia/concordância usa somente `s['respostas']` da última execução de cada modo.
- Corrigir lookup do gabarito: a chave de Goiânia `goiania.go.gov.br/transparencia/` não coincide com URL completa nem host testados por `load_gold`.

### Segundo incremento — descoberta

- Reranking em lote por candidato versus Choice atual.
- Fila best-first/beam, orçamento adaptativo e reserva para menus genéricos.
- Vínculos oficiais a sistemas externos e recepção de documentos.
- Restaurar estado semântico em backtracking.

### Terceiro incremento — coleta e escala

- Captura/validação de endpoints públicos e downloads.
- Extratores tipados e proveniência por registro.
- Receitas por fornecedor, revalidação e fallback pontual.
- Comparar MCP ampliado versus Playwright Python direto.

### Benchmark

Começar com 20–30 portais estratificados por fornecedor/tipo: HTML simples, SPA, sistema terceiro, painel embutido, documentos e bloqueios. Reservar fornecedores/municípios não usados nos ajustes para medir generalização. Rodar ao menos três repetições por configuração, pareadas por tarefa e versão, alternando a ordem dos modos e respeitando limites dos sites.

Comparar:

A. Jev + MCP atual, com correção da semântica de resultado;
B. Jev + re-ranking + grafo/best-first;
C. B + documentos/rede/receitas;
D. B ou C + fallback Stagehand/Browser Use/multimodal.

Métricas: sucesso verificado de descoberta e coleta; falsos negativos; falsa declaração de ausência; precisão/recall de registros/campos; completude de paginação/órgãos; custo por tarefa e por tarefa correta; tempo mediano/P95; abstenções; necessidade humana e motivo de falha. Manter métricas de experiência de navegação separadas: caminho encontrado não é garantia de menor número possível de cliques. Menor caminho exige busca apropriada no grafo, não DFS com um atalho local.

## 10. Fontes consultadas

1. TypeSafe — Models: https://docs.typesafe.ai/models
2. TypeSafe — How to build with System One: https://docs.typesafe.ai/concepts/how-to-build-with-system-one
3. TypeSafe — Jev 1.13 jaggedness (revisada em 02/10/2026): https://docs.typesafe.ai/model-jaggedness/jev-1.13
4. TypeSafe — Re-ranking: https://docs.typesafe.ai/cookbooks/rerank_typesafe
5. TypeSafe — Hierarchical classification: https://docs.typesafe.ai/cookbooks/hierarchical_classification
6. TypeSafe — Confidence: https://docs.typesafe.ai/confidence
7. TypeSafe — Python SDK usage e integração OpenRouter: https://docs.typesafe.ai/sdk/python/usage
8. TypeSafe — Composite scoring: https://docs.typesafe.ai/patterns/composite-scoring
9. TypeSafe — Speculative fan-out: https://docs.typesafe.ai/patterns/fan-out
10. TypeSafe — Pre-parsed value extraction: https://docs.typesafe.ai/cookbooks/pre_parsed_value_extraction_cookbook
11. Playwright MCP — README e ferramentas atuais: https://github.com/microsoft/playwright-mcp
12. Playwright Python — Network: https://playwright.dev/python/docs/network
13. Playwright CLI — README: https://github.com/microsoft/playwright-cli
14. Stagehand — SDK/README atual: https://github.com/browserbase/stagehand
15. Stagehand v4 — Observe: https://docs.stagehand.dev/v4/basics/observe
16. Stagehand v4 — Caching: https://docs.stagehand.dev/v4/best-practices/caching
17. Browser Use — projeto e ferramentas: https://github.com/browser-use/browser-use
18. Browser Use — índice oficial e advertências sobre sucesso declarado: https://docs.browser-use.com/llms.txt
19. Crawl4AI — projeto e capacidades: https://github.com/unclecode/crawl4ai
20. Transferegov — Dados abertos: https://www.gov.br/transferegov/pt-br/ferramentas-gestao/dados-abertos

Fontes de fornecedores sustentam a descrição de capacidades, não uma superioridade comprovada nos portais municipais. As recomendações precisam ser verificadas pelo benchmark acima.
