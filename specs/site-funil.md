# Spec — Site & Funil (`reports/site_funil.py`, camada Semanal)

Pergunta: **o tráfego está virando venda e onde o funil vaza?** (KPIs 5.1 CR/RPV, 5.4 funil, 5.3 performance por canal)

## Fontes
`dbt_dw_us_az.tb_ga4_canal_diario` e `tb_ga4_funil` (GA4; região US, consulta separada; histórico só desde 01/07/2026) e pedidos válidos de `tb_pedido` (via `vendas_margem.carregar_dados`). Dias fechados (até ontem); comparação com o período anterior de mesmo tamanho se ele estiver dentro do histórico do GA4.

## Regras
- **Taxa de conversão** = pedidos válidos ÷ sessões (não usa as conversões do GA4). Referência: ~2,5% geral; piso de loja nova ~1,4%.
- **RPV** = faturamento ÷ sessões (faturamento = Σ `vl_liquido_item`, mesma base da meta).
- **Funil** = contagem de **eventos** GA4 (`view_item` → `add_to_cart` → `begin_checkout` → `purchase`); taxas entre etapas são de eventos, não de sessões distintas (acompanhar tendência, não valor exato). **Carrinho ÷ visualização** (ref. 7,2–7,5%), **checkout concluído** = purchase ÷ begin_checkout, **abandono de carrinho** = 1 − purchase ÷ add_to_cart (ref. ~70%).
- **Por semana:** sessões e conversão só de semanas fechadas (seg–dom).
- **Por canal:** canal padrão do GA4; engajamento, compras/receita **do GA4** (a atribuição do GA4 perde pedidos; a origem por pedido, mais precisa, está em Vendas & Margem). E-mail ainda sem sessões relevantes.
- **Limite:** sem histórico do GA4 antes de 01/07/2026.
