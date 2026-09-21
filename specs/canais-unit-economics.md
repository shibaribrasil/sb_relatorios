# Spec — Canais & Unit Economics (`reports/canais_unit_economics.py`, camada Mensal)

Pergunta: **cada real de mídia se paga? Quanto custa um cliente novo e quanto ele vale?** (KPIs 5.2 CAC/LTV:CAC/payback, 5.3 MER/CAC por canal/teto)

## Fontes
`tb_cliente` (valor do cliente, origem do 1º pedido, `dt_prim_pedido`), `tb_pedido` (pedidos válidos, margem), custo diário de `dbt_dw_us_az.tb_gads_conta_diario` (região US, desde 15/06/2026 → a tabela parte de julho). **Só o Google Ads tem custo**: Meta/Instagram pago sem gasto (Melhorias Manuais, item 6).

## Regras
- **CAC** = investimento em Google Ads no período ÷ clientes novos (1º pedido válido no período). **CAC do Google pago** = mesmo investimento ÷ clientes novos com origem do 1º pedido google/cpc (teto: o gasto inclui campanhas que a URL não identifica como cpc).
- **Valor em 12 meses** = Σ margem de contribuição em até 12 meses (mês da 1ª compra + 12) por cliente das coortes de 2024 em diante já com 12 meses inteiros. Margem de pedidos anteriores a ago/2025 é aproximada.
- **LTV : CAC** = valor em 12 meses ÷ CAC. Mistura coortes antigas com CAC atual: ordem de grandeza. Mínimo 3:1; saudável 4–5:1; acima de 6:1 pode ser subinvestimento.
- **Payback** = margem de contribuição média do 1º pedido dos clientes captados no período ÷ CAC (≥ 100% = o 1º pedido já paga a aquisição).
- **MER** = faturamento total (todas as origens) ÷ investimento; **break-even** = faturamento ÷ margem de contribuição do período (MER abaixo disso = a mídia consome mais do que a venda deixa).
- **Teto de investimento:** R$ 600/mês (definido após a auditoria de ago/2026), constante da página.
- **Por canal:** clientes novos por origem/mídia do 1º pedido, % que já recompraram, valor médio; investimento e CAC só para o Google pago.
- Mês corrente é parcial; o custo de Ads chega com 1–2 dias de atraso.
