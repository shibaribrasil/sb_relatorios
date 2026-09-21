# Spec — Clientes & Coorte (`reports/clientes.py`, camada Mensal)

Pergunta: **os clientes voltam a comprar, quando, quem vale mais e de que canal vêm os que ficam?**

## Fontes
`dbt_dw_az.tb_cliente` (1 linha por contato do tipo Cliente; compras = pedidos válidos; `vl_margem_contribuicao` = valor do cliente/LTV a data; `fg_recorrente`, `qt_dias_entre_compras`, `qt_dias_desde_ultima_compra`, origem/mídia do 1º pedido) e `dbt_dw_az.tb_pedido` (pedidos válidos, por pedido: margem, receita, faturamento). Cache de 30 min. Ver `sb_dw_dbt/models/3.az/Cliente/tb_cliente.yml`.

## Regras
- **Cliente com compra** = contato com ≥ 1 pedido válido (`fg_pedido_valido`). **Recorrente** = ≥ 2 pedidos válidos. **Base ativa** = comprou nos últimos 90 dias.
- **Valor do cliente** = Σ margem de contribuição das linhas dos pedidos válidos dele (antes de mídia; embalagem estimada; reembolso no pedido). Antes de ago/2025 taxa e frete reais não existem → valor de clientes antigos é aproximado.
- **Histórico** do DW desde nov/2023: quem comprou antes aparece com a 1ª compra a partir dessa data. Coortes e recompra a partir de 01/2024; mapa de coorte mostra as últimas 24.
- **Curva de recompra:** % dos clientes com 2ª compra em até N dias (30/60/90/180/365) da 1ª, só entre quem já teve N dias completos.
- **Mapa de coorte:** linha = mês da 1ª compra; coluna k = nº de clientes da coorte que compraram k meses depois ÷ tamanho da coorte (cor); células de meses futuros vazias. Volume pequeno → ler padrão, não célula.
- **LTV por coorte:** coorte trimestral; margem acumulada por cliente até k meses depois, só k completo para toda a coorte.
- **Origem do 1º pedido:** `tb_atribuicao_pedido` do 1º pedido válido; "(sem parametro)" agrega direto/orgânico/sem marcação.
- **Reativar:** recorrente sem comprar há ≥ 180 dias (limite da página), ordenado por valor, 25 maiores.
- Razões sempre Σnumerador ÷ Σdenominador; nenhuma média de percentuais.

## v2 — 21/set/2026: concentração geográfica
- Seção "Onde estão os clientes": UF do cadastro do cliente (Bling), 10 maiores por margem de contribuição acumulada (clientes, % clientes, margem, % da margem, recorrentes) + cards da maior praça e das três maiores (aviso se > 80%).
