# Spec — Pulso do Dia (`reports/pulso_dia.py`, camada Diária)

Página operacional: responde "como está hoje, o que está parado e se o mês vai bater a meta". Sem margem por dia (≈1 pedido/dia: percentuais diários não significam nada); margem fica em Vendas & Margem.

## Fontes
`dbt_dw_az.tb_pedido` (pedidos válidos, `fg_pedido_valido`), `tb_tempo` (calendário), `tb_objetivo_faturamento` (meta), `tb_atribuicao_pedido` (origem), `dbt_dw_us_az.tb_gads_conta_diario` (custo Google Ads, região US — consulta separada). Cache de 5 min; o dbt roda de hora em hora (7h–23h BRT).

## Regras
- **Faturamento** = Σ `vl_liquido_item` (produtos líquidos de desconto + frete pago), 1 linha por pedido válido; mesma base da meta. Brinde não conta como item.
- **Hoje** está em andamento: não comparar com dia fechado.
- **A postar** = pedido válido com `ds_status_pedido = 'EM ABERTO'` (pago, aguardando postagem). **Dias úteis** = nº de dias úteis (`tb_tempo.fg_dia_util`) depois da data de pagamento (`dt_pagamento_nuvemshop`, senão `dt_pedido`) até hoje inclusive. **Atrasado** = mais de 2 dias úteis (decisão de 21/set/2026; alinhado ao B048).
- **Dias úteis do mês** = Σ `fg_dia_util` do mês; "fechados" = dias úteis com data < hoje.
- **Projeção do mês** = faturamento dos dias fechados ÷ dias úteis fechados × dias úteis do mês. Vendas de fim de semana entram no numerador. Referência de ritmo, não previsão; sem projeção no 1º dia do mês. Comparada à meta do mês (a meta ainda precisa ser revista).
- **Meta acumulada / atingimento:** `vl_meta_dia_acumulado` da data de hoje; atingimento = faturamento do mês ÷ meta acumulada.
- **Semana** = segunda a domingo. Comparação justa: semana atual até hoje × mesmos dias da semana anterior; mais a semana anterior inteira.
- **Origem** = `ds_origem_venda`/`ds_midia_venda` (URL de entrada); "(sem parametro)" agrega sem UTM/clique e sem landing_url. Últimos 7 dias.
- **Google Ads** chega com 1–2 dias de atraso: o investimento por pedido usa só os pedidos até o último dia com custo.
- Aggregations no Streamlit são só somas/filtros; o único cálculo de regra (dias úteis desde o pagamento, limite de 2) está aqui por ser de apresentação/alerta; se virar métrica oficial, mover para o dbt.

## v2 — 21/set/2026: logística e média de 7 dias
- **Média por dia (7 dias):** faturamento e pedidos dos 7 dias fechados até ontem ÷ 7.
- **Entregas em risco** (`common/logistica.py` → `tb_logistica_pedido`): cards de Em trânsito, Atrasados (`fg_atrasado_em_aberto`), Problema de entrega (`fg_problema_entrega_ativo`), Parados (em trânsito sem evento há ≥ 10 dias — limite da página), Fila do SAC (`fg_acao_sac`) e Enviados sem rastreio (situação "entregue" sem `dt_expedicao` nem entrega confirmada = rastreio ainda não carregado). Tabela dos pedidos em risco (atrasado, problema ou parado), com dias sem evento, último evento e situação no SAC.
- **Ponto cego conhecido:** o extrator de fulfillments/rastreio da Nuvemshop parou em 10/09/2026; pedidos enviados depois não têm rastreio, então problemas neles não aparecem até a carga voltar (ver B054).

## v3 — 22/set/2026: rastreio na lista de risco
- Tabela de "Entregas em risco" ganhou as colunas **Rastreio** (`cd_rastreio`) e **Link** (`ds_url_rastreio`, coluna de link "abrir"), logo depois do código do pedido.
