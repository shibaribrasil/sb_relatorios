# Spec — Relatório de Vendas

Documenta as regras de negócio e a definição de cada indicador do relatório de Vendas. **Leia antes de alterar qualquer cálculo, filtro ou seção deste relatório.** Se a mudança alterar uma regra de negócio, atualize este arquivo no mesmo commit — não deixe a regra só implícita no SQL ou no app (foi exatamente essa lacuna que causou o bug do ROI no relatório de Google Ads, ver `specs/google-ads.md` e `MIGRACAO-RELATORIOS.md`, Fase 8).

## Escopo desta versão (v1, 2026-07-01)

O relatório final terá: um filtro de meses (default: mês atual, podendo selecionar um ou mais meses) e duas partes — um bloco de indicadores do **dia atual** (fixo, não afetado pelo filtro de mês) e uma seção de indicadores/gráficos/tabelas do **mês agregado** (essa sim afetada pelo filtro).

**Esta v1 cobre só o bloco "Dia Atual".** Fora de escopo, para uma próxima rodada:
- Filtro de meses (widget ainda não existe na página — não faz sentido montar um filtro que não afeta nada na tela ainda)
- Seção de indicadores/gráficos/tabelas do mês agregado

## Fonte de dados

Tabela `rpt_vendas_dia` no BigQuery, dataset **`dbt_dw_rpt`** (região `us-east4`, produzida pelo projeto `sb_dw_dbt`, modelo `models/4.rpt/Vendas/rpt_vendas_dia.sql`), grão **1 linha por dia**, sem janela de tempo (histórico completo — diferente do padrão de "últimos 30 dias" do relatório de Google Ads, porque aqui o volume é pequeno — 1 linha/dia — e o filtro de meses de uma fase futura precisa poder olhar qualquer mês passado).

`rpt_vendas_dia` lê de `{{ ref('tb_pedido_agg_dia') }}` (camada `az`, projeto `sb_dw_dbt`), que por sua vez:
- Vem da origem **Bling** (`stg_pedido`/`stg_item_pedido` → `tb_pedido` → `tb_pedido_agg_dia`) e **Google Drive** (`stg_meta_faturamento` → `tb_objetivo_faturamento`) para a meta mensal.
- **Já exclui pedidos com `ds_status_pedido = 'CANCELADO'`** (filtro aplicado em `tb_pedido_agg_dia.sql`, herdado por todos os indicadores deste relatório — não é uma regra nova, é a mesma usada em `tb_atingimento_faturamento_dinamico`, tabela que já alimenta relatórios de Financeiro).
- **Já traz `vl_meta_dia` pronto**, calculado em `tb_objetivo_faturamento.sql` como `vl_objetivo_total (meta do mês) ÷ quantidade de dias do mês` (via `safe_divide` — protegido contra divisão por zero) — exatamente a regra pedida para o indicador "Meta do Dia".

**Não usamos `tb_atingimento_faturamento_dinamico`** como fonte, apesar do nome sugerir mais adequação: essa tabela é incremental (`merge`) e alimenta outros relatórios de Financeiro; seu `SELECT` final descarta lucro, margem e quantidade de pedidos (ficam só em CTEs intermediárias). Criar uma fonte própria (`rpt_vendas_dia`, lendo direto de `tb_pedido_agg_dia`) evita qualquer risco de regressão nesses outros relatórios.

## Bloco "Dia Atual"

**Regra de negócio: usa sempre a data de hoje, fuso BRT (`UTC-3`, sem horário de verão desde 2019 — mesmo padrão de `BRT`/`_data_referencia_brt()` em `reports/google_ads.py`), independentemente de qualquer filtro de mês.** Mesmo que o usuário selecione um mês fechado ou uma combinação de meses na seção de mês agregado (fase futura), este bloco não muda — é sempre o dia corrente.

Se a linha do dia de hoje ainda não existir em `rpt_vendas_dia` (ex.: cron do `sb_dw_dbt` ainda não rodou hoje), os cards mostram "sem dados ainda hoje" em vez de zero — mesmo padrão usado no card "Custo — Mês Atual" do relatório de Google Ads (zero seria enganoso, sugerindo faturamento zerado em vez de dado ausente).

| Indicador | Fórmula | Coluna em `rpt_vendas_dia` |
|---|---|---|
| Meta do Dia | meta do mês ÷ dias do mês | `vl_meta_dia` |
| Faturamento Bruto | soma de `vl_total_pedido` dos pedidos do dia | `vl_faturamento_bruto` |
| Atingimento da Meta | `vl_faturamento_bruto ÷ vl_meta_dia` | calculado no app |
| Pedidos | contagem de pedidos distintos no dia | `qt_pedidos` |
| Margem de Lucro | `vl_lucro_bruto ÷ vl_faturamento_liquido` (ver regra abaixo) | calculado no app |
| Lucro | `vl_lucro_bruto` | `vl_lucro_bruto` |

Sem cor/benchmark nos cards nesta v1 (nem no indicador de Atingimento da Meta, que naturalmente convida a um semáforo verde/amarelo/vermelho) — não há limiar definido pelo usuário e o projeto tem histórico de bug por regra de negócio assumida sem confirmação. Fica neutro até alguém validar os limiares certos.

## Regra de negócio: margem de lucro % (decisão de v1, a confirmar)

`vl_lucro_bruto` (calculado em `rpt_vendas_dia.sql`) = faturamento líquido − custo de mercadoria vendida, onde faturamento líquido = faturamento bruto − frete rateado − taxa aproximada de **5%** sobre o faturamento bruto (mesma fórmula de taxa aproximada já usada em `tb_atingimento_faturamento_dinamico.sql`, projeto `sb_dw_dbt` — não é uma regra nova criada aqui).

**Margem de lucro % = `vl_lucro_bruto ÷ vl_faturamento_liquido`** (não ÷ faturamento bruto). Escolhida por ser a mesma base sobre a qual o lucro já é calculado. **Esta é uma decisão tomada nesta v1 sem confirmação explícita do usuário — se o time financeiro definir outra convenção (ex.: margem sobre faturamento bruto), é uma troca de 1 linha em `reports/vendas.py`, não uma mudança de schema.**

## Limitações conhecidas

- A taxa de **5%** é uma aproximação (não é o custo real de gateway/marketplace por pedido) — herdada de `tb_atingimento_faturamento_dinamico`, não recalculada aqui.
- `vl_objetivo_total`/`vl_meta_dia` dependem de `stg_meta_faturamento` (planilha do Drive) estar preenchida para o mês em questão — meses sem meta cadastrada terão essas colunas nulas, e "Atingimento da Meta" fica indefinido (não dividir por nulo/zero).
- `rpt_vendas_dia` não filtra por loja/canal de venda — é a conta consolidada. Quebra por canal fica fora de escopo desta v1.
