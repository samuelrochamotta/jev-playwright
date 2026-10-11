# Implementação das prioridades de navegação

## Entregas

| Prioridade | Implementação |
|---|---|
| 1. Resultado inconclusivo não é ausência | Status/motivo/cobertura explícitos; perguntas dependentes ficam indeterminadas, confiança indisponível. Nenhuma declaração de ausência exaustiva. |
| 2. Recuperação e falta de progresso | Estado semântico sem refs/foco/relógios; consultas alternativas; bloqueio de consulta repetida; reinspeção após timeout; limites de erros, tentativas, profundidade e páginas. |
| 3. Qualificadores e cobertura | Chegada exige tema e qualificadores; ente informado exige identificação. Coleta exige consulta/arquivo. Julgamentos/contagens ambíguos e enumerações acima do limite se abstêm. |
| 4. Terceiros e documentos | Entrada por links oficiais desde a descoberta, incluindo menus/raízes; município externo verificado antes da expansão. Arquivos são fontes separadas, não controles HTML. |
| 5. Ranking e busca | Shortlist diverso, Noul por candidato em lote, fila best-first com rotas reproduzíveis. DFS preservado como baseline. |
| 6. Coleta e receitas | GET público observado, CSV/XLSX/JSON/HTML/PDF textual; validação de fontes e de cada linha/trecho; evidência/hash/localização; receitas por portal/ente e dicas semânticas por fornecedor sem URLs antigas. |
| 7. Escalada generativa/visual | Flags explícitas, orçamento de propostas, índices/operações/buscas permitidos. Screenshot opcional; nenhuma URL arbitrária, coordenada, código gerado ou declaração de sucesso. Jev verifica destinos. |

## Outras correções

- IDs físicos de frame MCP mudam após navegação, inclusive no principal: identidade lógica por ancestralidade em vez de prefixo físico; comportamento coberto com browser real.
- Playwright MCP fixado em `0.0.83`, com contrato de snapshot/evaluate/frame/aba/screenshot verificado.
- Jev tem retry limitado, Retry-After e validação de respostas incompletas/probabilidades inválidas. Chamadas na busca/contagem/julgamento padrão não bloqueiam o event loop.
- Gabarito de Goiânia aceito com host/caminho e variantes `www`/barra final.
- Acurácia/concordância do comparador usa todas as repetições, não apenas a última.
- Resultados numéricos fracionários/não finitos não são truncados para inteiros.
- Diretórios de execução não sobrescrevem execuções feitas no mesmo segundo; clientes fecham mesmo em falha.
- Dinheiro usa Decimal, JSON preserva precisão decimal, datas reconhecidas são normalizadas em código. Dados brutos são preservados.

## Segurança e limites

Downloads não herdam autenticação do browser. O host deve ser autorizado, o DNS exclusivamente público, a conexão fixada no IP validado com TLS/SNI e o redirect restrito ao escopo. Parâmetros de credenciais, destinos privados e arquivos acima do limite são rejeitados. As tabelas/arquivos preservam artefatos de evidência por SHA-256. Arquivos malformados são isolados por fonte.

O browser não é uma sandbox de navegação contra conteúdo malicioso; a proteção de IP descrita é do downloader. Execute em ambiente isolado, use apenas portais autorizados e respeite limites/termos. O sistema não contorna desafios anti-robô.

Ainda não há paginação universal, comprovação de todos os órgãos, OCR, parser XLS/ODS, execução de POST/exportação sem URL ou computer-use por coordenadas. Esses casos ficam parciais/pendentes. Dicas de fornecedor são conhecimento semântico de rotas observadas, não adaptadores completos garantidos para todo portal de uma marca.

## Validação realizada

- **57 testes aprovados** com `JEV_BROWSER_TESTS=1`, incluindo dois testes com Google Chrome e MCP reais contra fixtures locais.
- Teste end-to-end de navegação/consulta/coleta com decisões simuladas, arquivo `collection.json`, campos monetários e proveniência.
- Testes de IP fixado/Host/SNI, redirects, limites, DNS privado/misto, arquivos inválidos, cache/rotas, abstenção, frames e avaliação de todas as repetições.
- Smoke test de GET público real em `https://example.com/`, confirmando conexão com IP fixado e TLS/SNI, sem inferência paga.
- Compilação Python, `git diff --check` e lint das regras F/I aprovados.
- Não foram feitas chamadas pagas ao Jev/LLM, nem benchmark em portais municipais reais. O endpoint Jev existente não foi migrado sem teste autenticado de contrato.

Comandos:

```bash
pip install -r requirements.txt
python -m unittest discover -s tests -v
JEV_BROWSER_TESTS=1 python -m unittest discover -s tests -v
python run_checklist.py checklists/checklist.md --entes entes.csv --collect --recipes --fallback
```

Para modelos multimodais: configure `LLM_MODEL` apropriado e use `--vision-fallback` em vez de `--fallback`. A próxima etapa de validação é o benchmark estratificado proposto em `pesquisa-navegacao-jev.md`, com revisão humana dos destinos e da cobertura dos registros.
