# Spec — Relatório de Vendas

Documenta as regras de negócio e a definição de cada indicador do relatório de Vendas. **Leia antes de alterar qualquer cálculo, filtro ou seção deste relatório.** Se a mudança alterar uma regra de negócio, atualize este arquivo no mesmo commit — não deixe a regra só implícita no SQL ou no app (foi exatamente essa lacuna que causou o bug do ROI no relatório de Google Ads, ver `specs/google-ads.md` e `MIGRACAO-RELATORIOS.md`, Fase 8).

## Escopo

O relatório tem: um filtro de meses (default: mês atual, podendo selecionar um ou mais meses) e duas partes — um bloco de indicadores do **dia atual** (fixo, não afetado pelo filtro de mês — v1, 2026-07-01) e uma seção de indicadores/gráficos/tabelas do **mês agregado** (afetada pelo filtro — v2, 2026-07-01).

## Fonte de dados

Duas tabelas no BigQuery, dataset **`dbt_dw_rpt`** (região `us-east4`, produzidas pelo projeto `sb_dw_dbt`, pasta `models/4.rpt/Vendas/`), sem janela de tempo (histórico completo — diferente do padrão de "últimos 30 dias" do relatório de Google Ads, porque aqui o volume é pequeno e o filtro de meses precisa poder olhar qualquer mês passado):

- **`rpt_vendas_dia`** — grão 1 linha por dia. Fonte do bloco "Dia Atual", dos 3 blocos de cards do "Mês" e do gráfico "Vendas por Dia" (todos deriváveis de somas/valores diários, ver seção "Bloco Mês" abaixo).
- **`rpt_vendas_pedidos`** — grão 1 linha por item de pedido. Fonte da tabela "Pedidos do Mês" e de toda a seção "Produtos" (tabela por produto, gráficos de categoria/subcategoria). Além das colunas de valor, traz `nm_produto_base` (nome do produto **sem** variação de cor/tamanho — vem de `tb_produto` via `tb_pedido.nm_produto`, diferente de `nm_produto_completo`/`nm_produto`, que inclui a variação), `ds_categoria` e `ds_subcategoria` (também de `tb_produto`, via `tb_pedido`).

Ambas leem, direta ou indiretamente, de `tb_pedido`/`tb_pedido_agg_dia` (camada `az`, projeto `sb_dw_dbt`), que vêm da origem **Bling** (`stg_pedido`/`stg_item_pedido` → `tb_pedido` → `tb_pedido_agg_dia`) e **Google Drive** (`stg_meta_faturamento` → `tb_objetivo_faturamento`) para a meta mensal. `tb_pedido_agg_dia` já traz `vl_meta_dia` pronto, calculado em `tb_objetivo_faturamento.sql` como `vl_objetivo_total (meta do mês) ÷ quantidade de dias do mês` (via `safe_divide`).

**Não usamos `tb_atingimento_faturamento_dinamico`** como fonte, apesar do nome sugerir mais adequação: essa tabela é incremental (`merge`) e alimenta outros relatórios de Financeiro; seu `SELECT` final descarta lucro, margem e quantidade de pedidos (ficam só em CTEs intermediárias). Criar uma fonte própria evita qualquer risco de regressão nesses outros relatórios.

### Regra de negócio: exclusão de `CANCELADO`

**Todo indicador deste relatório exclui pedidos com `ds_status_pedido = 'CANCELADO'`, exceto a Taxa de Cancelamento** (que por definição precisa do total, cancelados + não cancelados).

- Em `rpt_vendas_dia`: a exclusão vem herdada de `tb_pedido_agg_dia.sql` (filtro na base, antes de qualquer agregação) para todas as colunas **exceto** `qt_pedidos_cancelados`, que é calculada à parte, direto de `tb_pedido` filtrando `ds_status_pedido = 'CANCELADO'` (numa CTE própria, `cte_cancelados`, com `left join` por dia) — é a única coluna desta tabela que **inclui** cancelados, e existe só para alimentar a Taxa de Cancelamento.
- Em `rpt_vendas_pedidos`: filtro explícito `where ds_status_pedido <> 'CANCELADO'` na própria query (não há indicador de cancelamento nesta tabela).

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

**Regra de cor (v3, 2026-07-01, pedida explicitamente pelo usuário):** o card "Atingimento da Meta" é verde (`variant="ok"`) se ≥ 100%, vermelho (`variant="bad"`) se < 100% — sem faixa intermediária de alerta. Mesma regra vale para o card "Atingimento da Meta" do bloco "Mês" (ver abaixo). Implementada em `_atingimento_variant()` em `reports/vendas.py`. Os demais cards deste bloco continuam sem cor — não há limiar definido pelo usuário para eles.

## Regra de negócio: margem de lucro % (decisão de v1, a confirmar)

`vl_lucro_bruto` (calculado em `rpt_vendas_dia.sql`) = faturamento líquido − custo de mercadoria vendida, onde faturamento líquido = faturamento bruto − frete rateado − taxa aproximada de **5%** sobre o faturamento bruto (mesma fórmula de taxa aproximada já usada em `tb_atingimento_faturamento_dinamico.sql`, projeto `sb_dw_dbt` — não é uma regra nova criada aqui).

**Margem de lucro % = `vl_lucro_bruto ÷ vl_faturamento_liquido`** (não ÷ faturamento bruto). Escolhida por ser a mesma base sobre a qual o lucro já é calculado. **Esta é uma decisão tomada nesta v1 sem confirmação explícita do usuário — se o time financeiro definir outra convenção (ex.: margem sobre faturamento bruto), é uma troca de 1 linha em `reports/vendas.py`, não uma mudança de schema.**

## Filtro de meses

`st.multiselect` no topo da seção "Mês" (não afeta o bloco "Dia Atual", que fica antes e é sempre hoje). Opções = valores distintos de `dt_prim_dia_mes` presentes em `rpt_vendas_dia`, formatados "MM/AAAA", ordenados do mais recente pro mais antigo. Default = mês atual.

Todos os agregados do bloco "Mês" são somas simples de `rpt_vendas_dia` filtradas pelos meses selecionados — **sem caso especial para "mês atual" vs. "mês passado"**: dias futuros do mês atual já têm faturamento zero (não existem pedidos ainda) e dias de meses passados já são todos ≤ hoje, então os dois filtros abaixo bastam:

```python
meta_acumulada    = soma(vl_meta_dia)         onde dt_prim_dia_mes in meses_selecionados E dt_data <= hoje
demais_agregados  = soma(coluna)              onde dt_prim_dia_mes in meses_selecionados
```

`qt_pedidos`, `qt_pedidos_cancelados`, `qt_item`, `vl_faturamento_bruto`, `vl_frete`, `vl_desconto`, `vl_custo_mercadoria`, `vl_lucro_bruto` podem ser somados por dia sem risco de dupla-contagem — cada `cd_pedido` pertence a exatamente 1 `dt_pedido`. `vl_objetivo_total` **não** pode ser somado direto (é o mesmo valor repetido em cada dia do mês) — precisa de 1 valor por mês antes de somar entre meses selecionados (`groupby(dt_prim_dia_mes).first()`).

## Bloco "Atingimento da Meta"

| Indicador | Fórmula |
|---|---|
| Meta Total do Mês | soma de `vl_objetivo_total` (1 valor por mês selecionado) |
| Meta Acumulada até Hoje | soma de `vl_meta_dia` onde `dt_data <= hoje` |
| Faturamento Bruto | soma de `vl_faturamento_bruto` |
| Atingimento da Meta | Faturamento Bruto ÷ Meta Acumulada até Hoje |

Se mais de um mês estiver selecionado, os valores somam entre os meses (visão consolidada), não uma média — ex.: atingimento é sempre `soma(faturamento) ÷ soma(meta acumulada)`, nunca a média dos % de cada mês isoladamente.

Mesma regra de cor do bloco "Dia Atual": verde se ≥ 100%, vermelho se < 100% (`_atingimento_variant()`).

## Gráfico "Vendas por Dia" (v4, 2026-07-01)

Barra por dia (`vl_faturamento_bruto`) + linha tracejada da meta do dia (`vl_meta_dia`), para os meses selecionados, ordenado por `dt_data`. Objetivo explícito pedido pelo usuário: visualizar em quais dias o faturamento ficou acima ou abaixo da meta.

**Regra de cor da barra** (`_grafico_venda_diaria()` em `reports/vendas.py`): verde se `vl_faturamento_bruto >= vl_meta_dia` naquele dia, vermelho se abaixo. Cinza se o mês não tem meta cadastrada (`vl_meta_dia` nulo) — evita colorir de vermelho um dia que na verdade não tem meta pra comparar.

## Bloco "Detalhamento do Lucro"

| Indicador | Fórmula | Coluna em `rpt_vendas_dia` |
|---|---|---|
| Frete | soma de `vl_frete_rateio` | `vl_frete` |
| Descontos Totais | soma de `vl_desconto_rateio` | `vl_desconto` |
| Faturamento Líquido | Faturamento Bruto − Frete − taxa aproximada 5% | `vl_faturamento_liquido` |
| Custo Total dos Produtos | soma de `vl_custo_pedido` | `vl_custo_mercadoria` |
| Lucro | Faturamento Líquido − Custo Total dos Produtos | `vl_lucro_bruto` |
| Margem de Lucro % | Lucro ÷ Faturamento Líquido | calculado no app |

**Decisão de negócio (v2, sem confirmação explícita do usuário — fácil de reverter):** "Faturamento Bruto" continua definido como no bloco "Dia Atual" (soma de `vl_total_pedido`, que já vem líquido de desconto — ver `tb_pedido.sql`, projeto `sb_dw_dbt`). **"Descontos Totais" é informativo, não é subtraído de novo do Faturamento Bruto** (o desconto já está embutido nele) — mostrar os dois lado a lado poderia sugerir uma cascata literal bruto − frete − desconto = líquido, mas essa conta **não fecha** com os números aqui, de propósito, porque líquido usa a aproximação de 5% de taxa, não o desconto. Se isso causar confusão no uso real, a alternativa é redefinir Faturamento Bruto como pré-desconto (`vl_total_item`) — mudaria também o card já validado do bloco "Dia Atual".

## Bloco "Indicadores de Pedidos"

| Indicador | Fórmula |
|---|---|
| Qtd de Pedidos | soma de `qt_pedidos` |
| Qtd de Produtos Vendidos | soma de `qt_item` |
| Ticket Médio | Faturamento Bruto ÷ Qtd de Pedidos |
| Preço Médio dos Produtos Vendidos | Faturamento Bruto ÷ Qtd de Produtos Vendidos |
| Qtd Média de Produtos por Pedido | Qtd de Produtos Vendidos ÷ Qtd de Pedidos |
| Taxa de Cancelamento | Qtd de Pedidos Cancelados ÷ (Qtd de Pedidos + Qtd de Pedidos Cancelados) |

Taxa de Cancelamento é o único indicador de todo o relatório que **usa** pedidos cancelados (no numerador e no denominador) — todos os outros os excluem. Fonte: `qt_pedidos_cancelados` em `rpt_vendas_dia`.

## Tabela "Pedidos do Mês"

Fonte: `rpt_vendas_pedidos` (grão **1 linha por item de pedido**). Filtrada pelos meses selecionados no filtro de topo.

**View padrão (v3, 2026-07-01, pedida explicitamente pelo usuário): resumida por pedido/cliente** — 1 linha por `cd_pedido` (não por item), agrupando/somando os itens desse pedido (`_tabela_pedidos_resumo()` em `reports/vendas.py`). A coluna "Produto" não aparece nesta view — é "um nível a mais", só exibido quando o usuário marca o checkbox **"Mostrar produtos"**, que troca a tabela toda para a view detalhada (1 linha por item, `_tabela_pedidos_detalhe()`), idêntica à v2. Não existe drill-down por linha individual (o Streamlit não tem grid nativo com expandir/recolher) — a troca é global, para toda a tabela.

Ordenação em ambas as views: `dt_pedido` decrescente (mais recente primeiro).

| Coluna exibida | Origem | Observação |
|---|---|---|
| Código do Pedido | `cd_pedido` | na view resumida, é a chave de agrupamento (1 linha por valor); na detalhada, repete entre linhas do mesmo pedido |
| Cliente | `nm_cliente` | |
| Produto | `nm_produto` | **só na view detalhada** — ausente na resumida |
| Quantidade | soma de `qt_item` do pedido (resumida) / `qt_item` do item (detalhada) | |
| Custo do Produto | soma de `vl_custo_produto` do pedido (resumida) / valor do item (detalhada) | **custo total**, não custo unitário |
| Faturamento Total | soma de `vl_faturamento_total` do pedido (resumida) / valor do item (detalhada) | bruto, antes de frete/desconto (`vl_total_item` em `tb_pedido`) |
| Frete | soma de `vl_frete` do pedido (resumida) / fração do item (detalhada) | |
| Desconto | soma de `vl_desconto` do pedido (resumida) / fração do item (detalhada) | |
| Total do Pedido | soma de `vl_total_pedido` dos itens do pedido (resumida) / fração do item (detalhada) | na resumida, é o total real do pedido; na detalhada, é só a fração daquele item — a soma das linhas de um mesmo pedido é que dá o total real |
| Taxa | soma de `vl_taxa` do pedido (resumida) / fração do item (detalhada) | **taxa real** por forma de pagamento (alíquota + taxa fixa do meio de pagamento), não a aproximação de 5% usada nos blocos de cards |
| Lucro | soma de `vl_lucro` do pedido (resumida) / valor do item (detalhada) — fórmula: `vl_total_pedido − vl_frete − vl_taxa − vl_custo_produto` | |
| Margem de Lucro % | Lucro ÷ (Total do Pedido − Frete − Taxa) — **não** é Lucro ÷ Total do Pedido | recalculada no nível do pedido na view resumida, não é a média das margens dos itens |

**Bug corrigido em 2026-07-01** (achado pelo usuário comparando o Lucro desta tabela com o Lucro do bloco "Hoje" — ficou muito evidente porque o mês tinha só 1 pedido): `vl_lucro` em `rpt_vendas_pedidos.sql` esquecia de descontar o frete (`vl_frete_rateio`), calculando só `Total do Pedido − Taxa − Custo`. Como `vl_total_pedido` já inclui o frete cobrado do cliente (ver Fonte de Dados), isso inflava o lucro em exatamente o valor do frete — R$ 24,52 de diferença no pedido de teste (151,40 em vez de 126,88). A fórmula correta espelha a mesma lógica de `rpt_vendas_dia` (que já descontava frete corretamente): `Total do Pedido − Frete − Taxa − Custo`. `pct_margem_lucro` também foi corrigida (denominador passa a ser líquido de frete e taxa, não o Total do Pedido bruto), e o cálculo equivalente em Python (`_tabela_pedidos_resumo()`, que recalcula a margem localmente pois agrega várias linhas) foi ajustado do mesmo jeito.

**Decisão de negócio (v2, sem confirmação explícita do usuário):** esta tabela usa a **taxa real por forma de pagamento** (`vl_taxa_pedido_rateio` em `tb_pedido`, já calculada a partir de `stg_forma_pagamento`), enquanto os blocos de cards (Dia Atual e Detalhamento do Lucro) continuam usando a **aproximação fixa de 5%**. É uma inconsistência intencional: a taxa real só existe nativamente no grão de `tb_pedido`/`rpt_vendas_pedidos`; trocar os blocos agregados para a taxa real também exigiria somar `vl_taxa_pedido_rateio` em vez de `vl_total_pedido * 0.05` em `rpt_vendas_dia`, o que mudaria o lucro/margem já validado do bloco "Dia Atual" em produção. Se o time decidir que vale a pena unificar, é uma troca localizada em `rpt_vendas_dia.sql` (troca `vl_taxa_aproximada` por uma soma real, com re-run do dbt). **Por causa dessa inconsistência intencional, o Lucro desta tabela nunca vai bater 100% com o Lucro dos cards agregados — só a parte referente ao frete estava incorreta (já corrigida); a diferença de taxa é esperada e documentada.**

## Seção "Produtos" (v4, 2026-07-01)

Fonte: `rpt_vendas_pedidos`, filtrada pelos meses selecionados no filtro de topo (a mesma seleção usada pela tabela "Pedidos do Mês").

### Tabela "Venda por Produto"

1 linha por `nm_produto_base` (`_tabela_venda_produto()`), somando os itens de todas as suas variações — **regra de negócio: agrupamento pelo nome do produto sem variação de cor/tamanho**, pedida explicitamente pelo usuário ("use o nome do produto sem a variação, pra agrupar todas as vendas de suas variações numa linha só"). `nm_produto_base` vem de `tb_produto` (via `tb_pedido.nm_produto`) — é o nome cadastrado no catálogo, diferente de `nm_produto_completo` (que inclui sufixos como "Cor:Amarela" ou "Tamanho:Pequeno", adicionados pelo Bling na venda). **Atenção:** dimensões que fazem parte do nome cadastrado do produto (ex.: "Corda de Algodão Tratada - 6mm X 8m" vs. "- 6mm X 10m") continuam como produtos diferentes — não é uma variação de cor/tamanho no sentido do Bling, é um produto distinto no catálogo.

Colunas: Produto, Quantidade Vendida (soma de `qt_item`), Custo do Produto (soma de `vl_custo_produto`), Faturamento Total (soma de `vl_faturamento_total`), Lucro (soma de `vl_lucro`), Margem de Lucro % (Lucro ÷ Faturamento Total, recalculada no nível do produto — mesma lógica de "não é a média das margens dos itens" da tabela de pedidos). Ordenada por Faturamento Total decrescente.

### Gráficos "Faturamento por Categoria" e "Faturamento por Subcategoria"

Barra horizontal (`_grafico_participacao()`) de `vl_faturamento_total` somado por `ds_categoria`, depois por `ds_subcategoria` (mesma função, coluna de agrupamento diferente), ordenada do maior pro menor, com % de participação no rótulo de cada barra (`valor da categoria ÷ soma de todas as categorias do período`). Capado nas 15 categorias/subcategorias de maior faturamento (`top_n=15`) para não estourar o gráfico em caso de muitas categorias — hoje a conta tem só 7 categorias e 14 subcategorias, então o cap não afeta nada na prática.

**Regra de negócio: "faturamento sem frete" = `vl_faturamento_total`** (pedido explicitamente pelo usuário). Não é preciso subtrair frete de nada — `vl_faturamento_total` (= `vl_total_item` em `tb_pedido`) já é o valor bruto do item, calculado **antes** de frete e desconto entrarem na conta (ver Tabela "Pedidos do Mês" acima). É diferente de "Faturamento Bruto" dos blocos de cards (que é `vl_total_pedido`, já com frete/desconto aplicados) — os dois "faturamento" deste relatório não são a mesma base, ver "Limitações conhecidas".

## Limitações conhecidas

- A taxa de **5%** usada nos blocos de cards é uma aproximação (não é o custo real de gateway/marketplace por pedido) — herdada de `tb_atingimento_faturamento_dinamico`, não recalculada aqui. A tabela de pedidos usa a taxa real (ver acima) — os dois números não são diretamente comparáveis linha a linha.
- `vl_objetivo_total`/`vl_meta_dia` dependem de `stg_meta_faturamento` (planilha do Drive) estar preenchida para o mês em questão — meses sem meta cadastrada terão essas colunas nulas, e "Atingimento da Meta" fica indefinido (não dividir por nulo/zero).
- Nem `rpt_vendas_dia` nem `rpt_vendas_pedidos` filtram por loja/canal de venda — é a conta consolidada. Quebra por canal fica fora de escopo.
- "Faturamento Bruto" nos blocos de cards já vem líquido de desconto (ver decisão acima) — não é o bruto "de livro contábil" antes de qualquer dedução. "Faturamento Total"/"sem frete" na tabela de pedidos e na seção "Produtos" (tabela por produto, gráficos de categoria/subcategoria), por outro lado, é o bruto de verdade (antes de frete/desconto) — os dois "bruto" do relatório não significam a mesma coisa; ler as tabelas com atenção antes de comparar entre seções.
- Categoria/subcategoria vêm de `tb_produto` (cadastro do Bling) — produto sem categoria cadastrada ou com cadastro `[Interno]` (ex.: material de embalagem/suprimento interno, não produto de venda) aparece nos gráficos igual a qualquer outra categoria; não há filtro para excluir categorias internas nesta v4.
