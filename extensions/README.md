# Extensões locais do Pi

## Medidor de contexto

`context-meter.ts` adiciona um gráfico colorido de uso do contexto acima do editor do Pi. A linha exibe somente o percentual, a distribuição estimada e a relação entre tokens usados e a capacidade. O detalhamento textual e a prévia das respostas ficam desativados. A extensão também oculta a linha nativa de tokens/custo do rodapé e mantém o diretório de trabalho à esquerda, exibindo o modelo selecionado e o effort à direita.

Para instalar na configuração global do Pi:

```bash
ln -s "$PWD/extensions/context-meter.ts" "$HOME/.pi/agent/extensions/context-meter.ts"
```

Se esse destino já existir, não o sobrescreva: revise/remova a versão existente primeiro. Reinicie o Pi para carregar a extensão. Também é possível carregá-la somente numa sessão com `pi -e /caminho/para/context-meter.ts`.

## Status neutros das ferramentas

`collapsed-tool-traces.ts` personaliza a apresentação das chamadas de ferramenta. A área permanece com fundo neutro e sem indicação colorida durante a execução; ao concluir, aparece `✓ concluído` em verde e, em caso de erro, `✗ falha` em vermelho. Os detalhes continuam expansíveis, e a extensão não altera execução nem resultados das ferramentas.

Para instalar ou atualizar a extensão global do Pi (faça isso somente se o arquivo existente for a versão que deseja substituir):

```bash
ln -sfn "$PWD/extensions/collapsed-tool-traces.ts" \
  "$HOME/.pi/agent/extensions/collapsed-tool-traces.ts"
```

Reinicie o Pi para carregar a extensão. Também é possível testá-la em uma sessão com `pi -e "$PWD/extensions/collapsed-tool-traces.ts"`.

