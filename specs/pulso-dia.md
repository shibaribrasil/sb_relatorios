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
