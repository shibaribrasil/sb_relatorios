# Spec — Relatório Google Ads

Documenta as regras de negócio e a definição de cada indicador do relatório de Google Ads. **Leia antes de alterar qualquer cálculo, filtro ou seção deste relatório.** Se a mudança alterar uma regra de negócio, atualize este arquivo no mesmo commit — não deixe a regra só implícita no código (foi exatamente essa lacuna que causou o bug do ROI, ver "Regra de negócio crítica" abaixo).

## Fonte de dados

Todas as tabelas vêm do BigQuery, dataset `dbt_dw_us_rpt` (produzidas pelo projeto `sb_dw_dbt`, camada `4.rpt`), com janela fixa dos **últimos 30 dias fechados** (`dt_data >= CURRENT_DATE() - 30` e `dt_data < CURRENT_DATE()`):

| Tabela `rpt` | Usada em |
|---|---|
| `rpt_gads_resumo_conta` | Seção 1 — Saúde Financeira |
| `rpt_gads_performance_campanhas` | Seções 1 e 2 |
| `rpt_gads_tendencia_diaria` | Seção 3 (gráfico de tendência) |
| `rpt_gads_conversoes_tipo` | Seção 3 (conversões por tipo) |
| `rpt_gads_orcamento` | Seção 4 |
| `rpt_gads_keywords_top` | Seção 5 |
| `rpt_gads_impression_share` | Seção 6 |
| `rpt_gads_anuncios` | Seção 7 |

## Regra de negócio crítica: retorno = só PURCHASE

**Receita, ROI, ROAS e CPA consideram exclusivamente conversões de categoria `PURCHASE`** (`segments_conversion_action_category = 'PURCHASE'` na fonte Google Ads). O Google Ads soma automaticamente TODAS as ações de conversão configuradas na conta em `metrics_conversions`/`metrics_conversions_value` — inclui Page View, Add to Cart, Begin Checkout junto com Purchase. Usar esse total bruto infla o retorno (chegou a ~16x em 2026-07-01, antes da correção).

Essa regra é aplicada na camada `3.az` do dbt (`tb_gads_conta_diario.sql` e `tb_gads_campanha_performance.sql`, projeto `sb_dw_dbt`), **não** no Streamlit — o app só lê `vl_conversoes_total`/`qt_conversoes_total`/`vl_roas`/`vl_cpa` das tabelas `rpt` já corrigidas. Se algum dia o Streamlit passar a calcular esses indicadores localmente em vez de ler pronto do BigQuery, essa mesma regra precisa ser reaplicada aqui.

**Limitação conhecida:** no nível de keyword (`rpt_gads_keywords_top`, Seção 5), o CPA **ainda usa o total bruto de conversões** (não filtrado por PURCHASE), porque a fonte do Google Ads (`KeywordBasicStats`) não tem quebra por categoria de conversão nesse grão — só existe em `CampaignConversionStats` (grão campanha/dia). Não é um bug pendente de código; é um limite de dado disponível. Se isso for revisitado, exigiria uma fonte adicional (conversão por keyword) que hoje não é ingerida.

## Seção 1 — Saúde Financeira

Fonte: `rpt_gads_resumo_conta` (1 linha, totais do período) + `rpt_gads_performance_campanhas` (gráficos por campanha).

| Indicador | Fórmula | Benchmark / regra |
|---|---|---|
| Custo Total | `vl_custo_total` | — |
| Receita Gerada | `vl_conversoes_total` (só PURCHASE, ver acima) | — |
| ROI | `(receita − custo) ÷ custo` | meta: > 100% |
| ROAS Médio | `vl_roas` = receita ÷ custo | meta 3–5× · mínimo 2× · abaixo de 2× = prejuízo considerando margem |
| CPA Médio | `vl_cpa` = custo ÷ compras | sem meta fixa — varia por produto |
| Impressões / Cliques / CPC | direto da tabela | — |
| CTR | `pct_ctr` | benchmark 2–6%+ |

Gráfico "ROAS por Campanha": cor por faixa (`roas_variant`: ok ≥3×, warn 2–3×, bad <2×), linha de referência em 2×. Barra horizontal (nome de campanha abreviado via `nome_curto()` — nome completo aparece no hover) para não quebrar rótulo.
Gráfico "Investido vs. Receita por Campanha": receita menor que investido na mesma campanha = prejuízo no período. Também horizontal; ordem de leitura de cima pra baixo é sempre Investido, depois Receita (cuidado: no Plotly, em barra horizontal agrupada, o primeiro trace adicionado desenha embaixo — por isso o código adiciona "Receita" antes de "Investido").

ROI **não inclui custo do produto** — considera só gasto de mídia. Importante não confundir com margem real do negócio.

Subtítulo de cada card mostra um fato/número concreto, não a fórmula (a fórmula fica no `ref`, junto com a meta): Custo Total → nº de campanhas e dias do período; Receita Gerada → nº de compras confirmadas; ROI → retorno líquido em R$; ROAS → quantas campanhas tiveram alguma compra; CPA → variação (mín–máx) de CPA entre campanhas.

## Seção 2 — Performance por Campanha

Fonte: `rpt_gads_performance_campanhas`. Tabela com uma linha por campanha: Investido, Impressões, Cliques, CTR, CPC, Conv. Rate (`conversões ÷ cliques`, calculado no app), Conversões (compras), CPA, Receita, ROAS (colorido pela mesma regra da Seção 1).

Benchmarks exibidos: CTR Search >2% aceitável / >6% bom · Conv. Rate e-commerce >1% mínimo / >3% bom · ROAS mínimo 2× / meta 3–5×.

## Seção 3 — Tendência Temporal e Conversões

- **Gasto diário e Cliques** (`rpt_gads_tendencia_diaria`): gasto e cliques dos últimos 30 dias, eixo duplo. Leitura: gasto subindo com cliques estáveis → CPC subindo (leilão mais competitivo); cliques caindo com gasto constante → possível perda de impression share.
- **Conversões por Tipo** (`rpt_gads_conversoes_tipo`): quebra por `ds_categoria_conversao` (Purchase, Page View, Add to Cart, Begin Checkout, etc.) — **é aqui que aparece a conversão bruta, de propósito**, para dar visibilidade de todo o funil. Conversões que não são categoria Purchase não são receita real e não devem ser somadas a ROAS/receita (reforça a regra crítica acima).
  - **Classificação de tipo** (`tipo_conversao()` em `reports/google_ads.py`, específica deste relatório): `PURCHASE` → **Primária**; `ADD_TO_CART`/`BEGIN_CHECKOUT` → **Secundária**; qualquer outra categoria (ex. `PAGE_VIEW`) → **Micro**. É uma regra fixa por categoria técnica, sem julgamento de IA.
  - Essa tabela é materializada (não é view) e atualizada pelo cron do GitHub Actions — pode estar algumas horas defasada em relação a tabelas que você acabou de rodar manualmente via `dbt run`. Se os números daqui parecerem inconsistentes com o resumo da conta, confira quando cada tabela rodou pela última vez antes de assumir bug.

## Seção 4 — Orçamento

Fonte: `rpt_gads_orcamento`. Compara `vl_orcamento_diario` (budget configurado) com `vl_gasto_medio_diario` (gasto médio real) por campanha.

Regra de status de utilização (`pct_utilizacao_media`, calculada no app via `util_variant`):
- **≥ 100%** → campanha limitada por orçamento — perdendo impressões/cliques que poderiam converter
- **70–99%** → normal
- **< 70%** → subutilizado — pode ter espaço para aumentar lance ou indicar audiência pequena

Não há cálculo de "orçamento recomendado" ou projeção — isso ficou fora de escopo do design (é julgamento/IA, ver Fase 7 do `MIGRACAO-RELATORIOS.md`).

## Seção 5 — Top Keywords por Gasto

Fonte: `rpt_gads_keywords_top`. Quality Score colorido por faixa (`qs_variant`): ok ≥9, warn 7–8, bad <7. Benchmarks: meta ≥7, aceitável 5–6, crítico <5, perfeito QS 10. Keywords com QS 10 ganham "★" no nome e a linha inteira fica com fundo verde claro.

CPA aqui usa conversão bruta (ver limitação conhecida acima) — não é diretamente comparável ao CPA das Seções 1/2, que é purchase-only.

## Seção 6 — Impression Share por Campanha

Fonte: `rpt_gads_impression_share`. Barras empilhadas: IS conquistado, perda por budget, perda por ranking.

Regra de leitura: **perda por budget** se resolve aumentando orçamento; **perda por ranking** se resolve melhorando Quality Score ou lance — são diagnósticos opostos, não confundir um pelo outro.

## Seção 7 — Anúncios Ativos

Fonte: `rpt_gads_anuncios`. Lista de criativos ativos com Ad Strength e status de aprovação — sem cálculo, só apresentação. Ad Strength "Ruim"/"Regular" costuma perder posição no leilão; mais variações de título/descrição geralmente elevam para "Boa"/"Excelente".

## Fora de escopo (documentado, não implementado)

- **Diagnóstico Executivo / Oportunidades** (insights gerados por IA) — depende da Fase 7 (Claude API) do `MIGRACAO-RELATORIOS.md`.
- **Landing Page por intenção de busca** — exigiria classificar keyword por intenção (curadoria manual ou IA); não é extraível direto das tabelas `rpt` atuais.
- **Orçamento recomendado + projeção 30 dias** — era cálculo manual/IA no relatório de referência (HTML do time de marketing), não uma regra fixa.
