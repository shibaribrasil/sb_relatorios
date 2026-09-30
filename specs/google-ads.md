# Spec — Google Ads (`reports/google_ads.py`, camada Semanal)

Análise semanal completa do Google Ads: quanto gastamos, o que o Ads reporta, o que os pedidos reais confirmam, onde perdemos leilão, como o orçamento foi consumido e quais palavras gastam. **Reescrito em 30/09/2026 sobre a camada `az`** (a versão anterior lia as `rpt_gads_*`, legado, e estava oculta do menu desde 22/09). Leia antes de mudar qualquer cálculo; se a mudança alterar uma regra, atualize este arquivo no mesmo commit.

## Fontes (todas `az`; Ads/GA4 no dataset US, pedidos em us-east4 → junção só no pandas)
| Dado | Tabela | Grão |
|---|---|---|
| Custo, impressões, cliques, compras e valor (só PURCHASE) | `dbt_dw_us_az.tb_gads_campanha_performance` | campanha × dia |
| Participação de impressões e perdas | `tb_gads_campanha_impression_share` | campanha × dia (MAX por dia: a view traz uma 2ª linha zerada em alguns dias) |
| Palavras-chave | `tb_gads_keyword_performance` (sem negativas) | palavra × correspondência × grupo × dia |
| Orçamento diário das campanhas ativas | `tb_gads_campanha_orcamento` (via `canais_unit_economics.carregar_orcamento`) | dia |
| Sessões, engajamento e compras do Google pago | `tb_ga4_sessao` + `tb_ga4_compras` (fonte google / meio cpc) | dia × campanha |
| Pedidos reais, faturamento, margem | `dbt_dw_az.tb_pedido` + `tb_atribuicao_pedido` (`carregar_dados`) | pedido |
| Ponte gclid → campanha | `tb_gads_clique_campanha` (`origem_campanha.carregar_campanhas`) | gclid |

## Regra crítica (herdada): retorno = só PURCHASE
Compras, valor de conversão, CPA e ROAS **do Ads** consideram só conversões de categoria `PURCHASE` (aplicado no dbt, `tb_gads_campanha_performance`/`tb_gads_conta_diario`). O Streamlit não recalcula isso. **Exceção conhecida:** no nível de palavra-chave a fonte não traz a categoria da conversão, então a tabela de palavras mostra "Conversões", não "Compras" (sem o filtro PURCHASE).

## Janela e semana
- Semana = segunda a domingo. Padrão: **semana em andamento, acumulada até o último dia fechado** = `min(ontem, último dia com custo do Ads)`. Hoje nunca entra: o custo do Ads chega com 1 dia de atraso e as compras creditadas aos últimos dias ainda sobem nos dias seguintes.
- Comparação: **mesmos dias** da semana anterior (`common/semana.py: janela`). Semana fechada compara com a anterior inteira.
- Histórico do Ads no BigQuery: desde 15/06/2026 (GA4 desde 01/07/2026: colunas de sessões ficam vazias antes).

## Seções
1. **Números da semana** — 3 blocos de cards. (a) *O que o Ads reporta*: investimento (por dia), cliques, CPC (Σ custo ÷ Σ cliques), CTR (Σ cliques ÷ Σ impressões), compras e CPA do Ads. (b) *O que a base confirma*: pedidos de Google pago (origem `google` / mídia `cpc` pela URL de entrada, `tb_atribuicao_pedido`), faturamento (`vl_liquido_item`: produtos líquidos + frete pago), **ROAS real** = faturamento de Google pago ÷ custo, **margem de contribuição ÷ custo** (≥ 1× = a mídia se pagou só com a margem), e "valor que o Ads reporta ÷ real" (quantas vezes o painel infla a venda). (c) *Tráfego GA4*: sessões de Google pago, % engajadas, sessões ÷ cliques do Ads (rastreio saudável 0,7–1,3) e custo por sessão. Semáforo de ROAS: verde ≥ 3×, âmbar ≥ 1,4× (equilíbrio), vermelho abaixo; **sem semáforo se a semana tem < 5 pedidos de Google pago** (amostra pequena).
2. **Semana a semana** — tabela das últimas 12 semanas e 2 gráficos (investimento por dia × ROAS real; cliques por dia × CPC). Volumes nos gráficos são **por dia** para a semana incompleta (marcada com `*`) não parecer queda.
3. **Por campanha** — custo, Δ R$ vs. período anterior, impressões, cliques, CTR, CPC, compras/CPA do Ads, sessões GA4 (casadas pelo nome da campanha), pedidos reais e ROAS real (ligados pelo `gclid`; pedidos sem `gclid`/fora do histórico de cliques ficam fora das campanhas, por isso a soma pode ficar abaixo do total do topo), participação de impressões e perdas. Abaixo, **campanha × semana** (8 semanas) com seletor de métrica (custo/dia, cliques/dia, impressões/dia, CPC, CTR, compras, CPA, participação e perdas).
4. **Leilão** — barras empilhadas por campanha: impressões ganhas, perdidas por orçamento, perdidas por classificação (lance/qualidade). Participação **ponderada pelas impressões elegíveis** (impressões ÷ participação), não média simples dos dias; dias sem participação informada saem. Resumo da Pesquisa com regra: perda por classificação > 2× a de orçamento e > 40% → "o gargalo é classificação, não verba"; perda por orçamento > 20% → "há impressão perdida por orçamento".
5. **Dia a dia** — gráfico investimento × teto (orçamento das campanhas ativas) e tabela diária (custo, % do teto, cliques, CPC, compras do Ads, sessões GA4, pedidos reais por data do pedido).
6. **Palavras-chave** — top 25 por custo na janela, filtrável por campanha; "sem conversão" = 5+ cliques e nenhuma conversão. Resumo: % do gasto em palavras sem conversão. Termos de pesquisa digitados **não** são extraídos para o BigQuery (limitação: conferir no painel).

## Limitações
- Pedidos reais por campanha dependem do `gclid`: iPhone (gbraid/wbraid) e cliques anteriores ao histórico caem em "(campanha não identificada)". **Shopping/PMax: ROAS real por campanha é piso** (pedidos com gclid válido não aparecem na tabela bruta de cliques) — não cortar orçamento por esse número (ver `canais-unit-economics.md`, investigação de 29/09).
- Poucos pedidos por semana: ROAS e CPA oscilam 20–30% com 1 pedido. Comparar tendência de várias semanas.
- Em 31/08/2026 a estrutura de campanhas mudou (Topo Funil pausada; campanhas novas); semanas anteriores têm outra base de investimento.
- Impressões da conta somadas por campanha podem diferir do total da conta (`tb_gads_conta_diario`); custo e cliques batem.
