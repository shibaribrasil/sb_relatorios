# Spec — Vendas da Semana (`reports/vendas_semana.py`, camada Semanal)

Fechamento semanal de vendas: quanto vendemos, com que margem, vindo de onde, de quem e o que vendeu. Reaproveita `carregar_dados()` e as regras de `vendas_margem` (mesma `tb_pedido`, mesma margem de contribuição antes de mídia; ver `specs/vendas-margem.md`). Nenhuma regra nova de negócio: só filtro por semana e soma.

## Regras
- **Semana** = segunda a domingo (ISO), pela data do pedido. Padrão: última semana fechada; a semana em andamento é selecionável e mostra **só dias fechados** (até ontem), comparada aos **mesmos dias** da semana anterior.
- **Comparação:** semana anterior (inteira, se a selecionada está fechada). Deltas relativos em R$/pedidos/ticket e em p.p. na margem %. Razões sempre Σnumerador ÷ Σdenominador.
- **Margem de contribuição:** antes de mídia, com o reembolso no pedido de origem (visão por pedido). Semáforo: verde ≥ 50%, âmbar ≥ 40%.
- **Google Ads:** soma do custo dos dias da semana (`tb_gads_conta_diario`); "margem após mídia" = margem de contribuição − custo, com o mesmo semáforo. Só aparece se houver custo na semana.
- **Tendência:** últimas 12 semanas até a selecionada (receita líquida, margem R$, margem %). Volume pequeno (~10 pedidos/semana): ler tendência, não semana isolada.
- **Clientes:** `fg_cliente_recorrente` (dbt) → pedidos de novos × recorrentes e margem % de cada grupo.
- **Origem/Produtos:** mesmas tabelas da página mensal, filtradas na semana; brindes fora dos produtos (custo no CMV).
