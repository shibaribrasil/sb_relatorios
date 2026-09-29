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
- **Orçamento de referência (v2, 29/09/2026):** não é mais uma constante. É a **soma, dia a dia, do orçamento diário das campanhas ativas** naquele dia (`tb_gads_campanha_orcamento`, `MAX(vl_orcamento_diario)` por campanha e dia, só `ENABLED`; histórico desde 15/06/2026), somada nos dias dos meses escolhidos. Sobe quando uma campanha é ligada e cai quando é pausada; é um teto, não meta. A constante antiga (R$ 35/dia) estava desatualizada (o real hoje é R$ 49/dia: Shopping 22 + Pesquisa 16 + Bondage e Spanking 8 + Marca 3) e o histórico de jul/ago era R$ 38/dia, não R$ 52 como a nota dizia. O gasto de campanhas pausadas no meio do dia não entra no teto.
- **Por canal:** clientes novos por origem/mídia do 1º pedido, % que já recompraram, valor médio; investimento e CAC só para o Google pago.
- Mês corrente é parcial; o custo de Ads chega com 1–2 dias de atraso.

## v2 — 29/set/2026: CAC e retorno por campanha do Google Ads
Tabela nova entre "Mês a mês" e "De onde vêm os clientes novos", uma linha por campanha (+ Total), nos meses escolhidos:
- **Custo, cliques e compras (Ads)** por campanha: `tb_gads_campanha_performance` (via `origem_campanha.carregar_campanhas`).
- **Pedidos, clientes novos, faturamento, margem de contribuição:** pedidos de origem google/cpc (`tb_atribuicao_pedido`), ligados à campanha pelo `gclid` → `tb_gads_clique_campanha` (`origem_campanha.pedidos_com_campanha`; junta no pandas, regiões diferentes). Cliente novo = pedido com `fg_cliente_recorrente` falso (1º pedido do cliente).
- **CAC** = custo da campanha ÷ clientes novos atribuídos a ela; em branco se não há custo ligado à linha ou nenhum cliente novo. **ROAS real** = faturamento dos pedidos atribuídos ÷ custo (o valor de conversão do Ads não é usado: inflado). **Margem ÷ custo** ≥ 1 = a campanha pagou o custo só com a margem de contribuição (antes de mídia).
- Pedidos Google pago sem campanha (sem `gclid`, ex.: iPhone) ficam em "(campanha não identificada)", sem custo; campanhas com custo e sem pedido aparecem com CAC em branco. **Total** = soma de tudo (bate com o "CAC do Google pago" do topo).
- **Amostra pequena** = menos de 10 pedidos na linha (`AMOSTRA_MIN`): CAC e ROAS oscilam demais; ler como hipótese. Último clique: Remarketing e Marca fecham venda de quem já conhecia a loja, e Shopping/Pesquisa/topo de funil ficam sem o crédito de apresentar a loja.
- Sem dbt novo. Referência (29/09, jul–set): 44 pedidos, 34 com campanha (77%); CAC total R$ 79,6; Pesquisa Compra Direta R$ 86 (2,6× ROAS real), Shopping R$ 229 (0,7×, 5 pedidos), Remarketing R$ 15 (17,9×), Topo Funil R$ 682 de custo e 0 pedido atribuído.
