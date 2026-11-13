# Spec — Pulso do Dia (`reports/pulso_dia.py`, camada Diária)

Relatório de tela, para acompanhar o dia: responde "como está hoje, o que está parado, como está a semana e se o mês vai bater a meta". Sem margem por dia (≈1 pedido/dia: percentuais diários não significam nada); a margem aparece só no Perfil dos pedidos (mês corrente) e em Vendas & Margem.

## Fontes
`dbt_dw_az.tb_pedido` (pedidos válidos, `fg_pedido_valido`), `tb_tempo` (calendário), `tb_objetivo_faturamento` (meta), `tb_atribuicao_pedido` (origem), `dbt_dw_us_az.tb_gads_conta_diario` (custo Google Ads, região US — consulta separada). Cache de 5 min; o dbt roda de hora em hora (7h–23h BRT).

## Regras
- **Faturamento** = Σ `vl_liquido_item` (produtos líquidos de desconto + frete pago), 1 linha por pedido válido; mesma base da meta. Brinde não conta como item.
- **Hoje** está em andamento: não comparar com dia fechado.
- **A postar** = pedido válido com `ds_status_pedido = 'EM ABERTO'` (pago, aguardando postagem). **Dias úteis** = nº de dias úteis (`tb_tempo.fg_dia_util`) depois da data de pagamento (`dt_pagamento_nuvemshop`, senão `dt_pedido`) até hoje inclusive. **Atrasado** = mais de 2 dias úteis (decisão de 21/set/2026; alinhado ao B048).
- **Dias do mês** = dias corridos do mês; "fechados" = dias com data < hoje (decisão do Hugo, 07/10/2026: ritmo e projeção em dias corridos, igual a Vendas & Margem). Dias úteis seguem só na regra de "a postar".
- **Projeção do mês** = média de vendas por dia (faturamento dos dias fechados ÷ dias corridos fechados) × dias do mês. Referência de ritmo, não previsão; sem projeção no 1º dia do mês. Comparada à meta do mês (a meta ainda precisa ser revista).
- **Meta acumulada / atingimento:** `vl_meta_dia_acumulado` da data de hoje; atingimento = faturamento do mês ÷ meta acumulada.
- **Semana** = segunda a domingo. Comparação justa: semana atual até hoje × mesmos dias da semana anterior; mais a semana anterior inteira.
- **Origem** = `ds_origem_venda`/`ds_midia_venda` (URL de entrada; pedido sem parâmetro com clique do Google Ads recuperado pelo GA4 vira google/cpc, desde 19/10/2026); "(sem parametro)" agrega sem UTM/clique e sem landing_url. Últimos 7 dias.
- **Google Ads** chega com 1–2 dias de atraso: o investimento por pedido usa só os pedidos até o último dia com custo.
- Aggregations no Streamlit são só somas/filtros; o único cálculo de regra (dias úteis desde o pagamento, limite de 2) está aqui por ser de apresentação/alerta; se virar métrica oficial, mover para o dbt.

## v2 — 21/set/2026: logística e média de 7 dias
- **Média por dia (7 dias):** faturamento e pedidos dos 7 dias fechados até ontem ÷ 7.
- **Entregas em risco** (`common/logistica.py` → `tb_logistica_pedido`): cards de Em trânsito, Atrasados (`fg_atrasado_em_aberto`), Problema de entrega (`fg_problema_entrega_ativo`), Parados (em trânsito sem evento há ≥ 10 dias — limite da página), Fila do SAC (`fg_acao_sac`) e Enviados sem rastreio (situação "entregue" sem `dt_expedicao` nem entrega confirmada = rastreio ainda não carregado). Tabela dos pedidos em risco (atrasado, problema ou parado), com dias sem evento, último evento e situação no SAC.
- **Ponto cego conhecido:** o extrator de fulfillments/rastreio da Nuvemshop parou em 10/09/2026; pedidos enviados depois não têm rastreio, então problemas neles não aparecem até a carga voltar (ver B054).

## v3 — 22/set/2026: rastreio na lista de risco
- Tabela de "Entregas em risco" ganhou as colunas **Rastreio** (`cd_rastreio`) e **Link** (`ds_url_rastreio`, coluna de link "abrir"), logo depois do código do pedido.

## v4 — 23/set/2026: nota sobre a página SAC
- A marcação de "já contatei o cliente" das entregas em risco passou a existir de fato na nova página **SAC — Tarefas do Dia** (`reports/sac.py`, camada Diária), com checkbox persistido em `raw_control.sac_tarefas` (fora do dbt — ver `specs/sac.md`). O Pulso do Dia continua mostrando a lista de risco só para leitura/visão geral; quem quiser marcar o contato feito usa a página SAC.

## v5 — 24/set/2026: Entregas em risco sai do Pulso
- Seção **Entregas em risco** removida do Pulso do Dia (decisão do Hugo): o acompanhamento de entregas vive só na página **SAC** (`reports/sac.py`). O Pulso deixou de carregar `tb_logistica_pedido`.
- Perfil dos pedidos (7 dias): tabela por tipo de pedido ganhou margem de contribuição R$ e %; card de frete grátis mostra "N de M pedidos" (ver `specs/perfil-pedidos.md`).

## v6 — 03/nov/2026: Pulso vira o relatório de tela
- Conceito (pedido do Hugo): é a página que fica aberta durante o dia. Seções, nesta ordem: **Hoje e ontem**, **Pedidos a postar**, **Semana**, **Vendas e meta do período** e **Perfil dos pedidos**. Saíram **Ritmo do mês** (média/projeção/gráfico de meta acumulada passam a vir só de "Vendas e meta do período") e **Origem e mídia** (Google Ads, origem, drill por campanha — seguem em Vendas & Margem e Origem & Campanha). Por enquanto a página tem só isso.
- **Vendas e meta do período** é a mesma seção de Vendas & Margem (`vendas_margem.secao_vendas_meta`, regras em `specs/vendas-margem.md`), fixa no **mês corrente**, com comparação contra o mesmo trecho do mês anterior. Lê `vendas_margem.carregar_dados` (cache de 15 min), não mais a base própria do Pulso; as regras de Dias do mês, Projeção, Meta acumulada e Atingimento acima valem como descritas lá.
- **Perfil dos pedidos** é a seção completa de `specs/perfil-pedidos.md` (cards, os dois gráficos de barras — itens e faixa de ticket — e tabela por tipo, com margem de contribuição R$ e %), agora do **mês corrente** em vez dos últimos 7 dias fechados. Com poucos pedidos nos primeiros dias do mês os percentuais oscilam.
- As regras de Origem e Google Ads acima não se aplicam mais a esta página.

## v7 — 04/nov/2026: Canal de venda e Origem das vendas
- Duas seções novas no fim da página, depois de Perfil dos pedidos, sem textos explicativos (pedido do Hugo: tela limpa), ambas do **mês corrente**.
- **Canal de venda** (bloco enxuto): gráfico de barras do faturamento por canal + tabela com Pedidos, Faturamento e Margem de contribuição (R$ e %). **Canal** = `tb_pedido.ds_tipo_loja` (tipo da loja no cadastro de canais do Bling: hoje `Nuvemshop` e `MercadoLivre`; `nm_loja` traz o nome da loja, ex. "Loja Mercado Livre - 01"); pedido sem loja vira "(sem canal)". Faturamento = Σ `vl_liquido_item`; margem de contribuição = Σ `vl_margem_contribuicao` (reembolso no mês do pedido, como em Origem das vendas); % = margem ÷ receita líquida de produtos. Primeira venda no Mercado Livre: 31/10/2026 (1 pedido); com volume tão baixo, os percentuais do canal não são leitura confiável.
- **Origem das vendas** é a mesma seção de Vendas & Margem (`vendas_margem.secao_origem`, regras em `specs/vendas-margem.md`): tabela por origem/mídia e drill por campanha, sem as notas (`com_nota=False`, também em `origem_campanha.drill_campanhas`). Vendas & Margem e as demais páginas seguem com as notas.
- `ds_tipo_loja` passou a ser lida em `vendas_margem.carregar_dados`.

## v8 — 10/nov/2026: canal WhatsApp (pós-venda) em Canal de venda
- Pedido do Hugo: emular o canal **WhatsApp** na seção Canal de venda para mostrar o que veio do pós-venda, **debitado do total da Nuvemshop** (o mesmo pedido nunca aparece nos dois canais; a soma dos canais continua igual ao faturamento do mês).
- **Regra:** pedido de `ds_tipo_loja = 'Nuvemshop'` com `tb_pedido_acao.fg_sac_marketing = true` vira **WhatsApp**; o resto segue no tipo da loja. `fg_sac_marketing` (regra no dbt, ver `specs/vendas-acao-promocao.md`) é verdadeiro quando o cupom é de uma ação mapeada do SAC/pós-venda em `stg_acao_cupom` (`SEGUNDACHANCE…` recontato, `RETORNOPERDIDO/MATURACAO/DORMENTE…` crédito de retorno, `CASHBACKPOSCOMPRA…` cashback, `EXPLORAR20` cartão impresso/Área VIP) **ou** quando o pedido recuperou carrinho/pedido cancelado depois de contato do SAC (mesmo com o cupom automático REC/RCA da Nuvemshop). Vale o cupom usado, não o meio pelo qual o cliente chegou.
- **Não entram:** `PRIMEIRODATE` (cupom permanente de entrada), recuperação automática sem contato do SAC, promoção automática, desconto manual e pedido manual de rascunho (`DRAFT-ORDER-…`) — não são ação do pós-venda. Cupom novo de pós-venda passa a contar quando entra em `stg_acao_cupom` com `fg_sac_marketing = true`.
- Implementação: `vendas_margem.carregar_dados` lê `fg_posvenda` (= `fg_sac_marketing`) e cria `ds_canal` via `vendas_margem.canal_venda`; `ds_tipo_loja` não muda (a Consulta de Pedidos continua mostrando a loja real do pedido). Só a Nuvemshop gera cupom do pós-venda, então pedido de outro canal nunca é reclassificado. Faturamento e margem seguem as bases de v7.
- Limitação: o rótulo "WhatsApp" é emulado. Vendas feitas à mão pelo WhatsApp **sem** cupom do pós-venda (pedido manual, `order_origin` "Whatsapp") continuam como Nuvemshop. Volume baixo: out/2026 teve 9 pedidos e nov/2026 (até o dia 10) 1, então a margem % do canal oscila.
- **Colunas novas (10/nov/2026):** *Cresc. vs. <período>* e *Part. na margem total*. **Crescimento** = faturamento do canal no mês corrente ÷ faturamento do mesmo canal no **mesmo trecho do mês anterior** (`calcular_periodo`: dias 01–hoje do mês anterior) − 1; sinal explícito, **verde** quando cresce e **vermelho** quando cai (cores `success`/`danger` do tema). Canal que não vendeu no período anterior mostra "—" (sem base de comparação; o Mercado Livre, por exemplo, só vendeu a partir de 31/10/2026). O pós-venda do mês anterior já vem reclassificado como WhatsApp pelo mesmo critério, então a comparação é entre canais iguais. Sem comparação (mês anterior sem dados) a coluna some. **Participação na margem** = margem de contribuição (R$) do canal ÷ margem de contribuição total do mês; a coluna soma 100%.

## v9 — 10/nov/2026: Canal de venda sem gráfico
- O gráfico de barras do faturamento por canal saiu (ficava espremido ao lado da tabela, pedido do Hugo); o bloco é só a tabela, na largura da página. **Crescimento vs. <período>** passou a ser a **última coluna**, depois de *Participação na margem total*. Regras de v7 e v8 não mudam.

## v10 — 12/nov/2026: anotação "whatsapp" também entra no canal WhatsApp
- Pedido do Hugo: pedido da Nuvemshop que vier com **anotação de WhatsApp** entra no canal WhatsApp e é debitado da Nuvemshop, além do pós-venda de v8.
- **Regra:** `ds_tipo_loja = 'Nuvemshop'` e a **anotação da loja** (`owner_note` da Nuvemshop, `stg_nuvemshop_pedido.ds_obs_loja`) contém "whatsapp" (qualquer caixa). Canal = WhatsApp quando **pós-venda (v8) OU anotação**; um pedido nos dois critérios conta uma vez. O pedido liga à Nuvemshop pelo número do pedido na loja (`cd_pedido_loja`).
- **Não entram:** a observação do cliente (`note`: o cliente escreve o próprio número de WhatsApp para a entrega, não é canal) e o campo "origem do pedido" (`order_origin`, `ds_origem_pedido`), mesmo escrito "Whatsapp" (ver abaixo). Variantes como "zap" e "wpp" também não entram (nenhuma ocorrência desde ago/2025).
- **Limite conhecido:** a anotação é texto livre e "whatsapp" também aparece em notas de suporte (ex.: "Cliente solicitou o ajuste do endereço. Feito! - Whatsapp"), que não são necessariamente venda fechada pelo WhatsApp; a regra segue o que o Hugo pediu (a palavra na anotação). Desde ago/2025 são 5 pedidos com a palavra na anotação (de 926). Mover a regra para o dbt (coluna em `tb_pedido`) é o caminho se ela crescer.
- Implementação: `vendas_margem.carregar_dados` lê `fg_anotacao_whatsapp` e passa a `canal_venda(ds_tipo_loja, fg_posvenda, fg_anotacao_whatsapp)`; `ds_tipo_loja` não muda.
