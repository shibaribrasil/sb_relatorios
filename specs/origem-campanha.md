# Spec — Drill-down origem → campanha (`reports/origem_campanha.py`)

Abaixo de cada tabela "Origem das vendas" (Pulso do Dia — últimos 7 dias, Vendas da Semana, Vendas & Margem), um bloco expansível por origem/mídia mostra as **campanhas** e suas conversões (pedidos, receita, margem).

## De onde vem a campanha
| Origem/mídia | Campanha | Fonte |
|---|---|---|
| google · cpc | nome da campanha do Google Ads | `ds_gclid` (URL de entrada, `tb_atribuicao_pedido`) → `tb_gads_clique_campanha` (dataset US; junta no pandas) |
| demais | `utm_campaign` da URL de entrada | `tb_atribuicao_pedido.ds_utm_campaign` (só ~10% dos pedidos trazem UTM) |

Google pago traz ainda, por campanha e no período: **custo, cliques e compras reportadas pelo Ads** (`tb_gads_campanha_performance`) e **margem ÷ custo** (onde há margem). Campanhas com custo e sem pedido atribuído aparecem (custo sem venda identificada).

## Limitações (aparecem na nota da tela)
- Cliques do Ads só desde 15/06/2026 e guardados ~90 dias pelo Google.
- Pedido Google sem `gclid` (iOS manda `gbraid`/`wbraid`) fica em "(campanha não identificada)" — desde 15/06, 39 de 47 pedidos com gclid casaram (83%).
- "Compras (Ads)" ≠ "Pedidos": o Ads conta conversões que a nossa base pode não ter ligado ao gclid.
- Instagram pago traz o ID numérico da campanha do Meta; não há dados do Meta Ads na base para dar nome.

## dbt (PR sb_dw_dbt #9)
`tb_atribuicao_pedido.ds_gclid`, `stg_gads_clique`, `tb_gads_clique_campanha` (1 linha por gclid; testes unique/not_null), fonte `ads_ClickStats`. **O Streamlit depende desse PR estar em produção** (senão a coluna `ds_gclid` some na próxima rodada horária do dbt).

## Fora deste escopo
"Origem do 1º pedido" (Canais & Unit Economics e Clientes & Coorte) ainda não abre por campanha.
