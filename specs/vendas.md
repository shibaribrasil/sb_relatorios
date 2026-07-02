# Spec — Relatório de Vendas

Documenta as regras de negócio e a definição de cada indicador do relatório de Vendas. **Leia antes de alterar qualquer cálculo, filtro ou seção deste relatório.** Se a mudança alterar uma regra de negócio, atualize este arquivo no mesmo commit — não deixe a regra só implícita no SQL ou no app (foi exatamente essa lacuna que causou o bug do ROI no relatório de Google Ads, ver `specs/google-ads.md` e `MIGRACAO-RELATORIOS.md`, Fase 8).

## Escopo

O relatório tem: um filtro de meses (default: mês atual, podendo selecionar um ou mais meses) e duas partes — um bloco de indicadores do **dia atual** (fixo, não afetado pelo filtro de mês — v1, 2026-07-01) e uma seção de indicadores/gráficos/tabelas do **mês agregado** (afetada pelo filtro — v2, 2026-07-01).

## Fonte de dados

Duas tabelas no BigQuery, dataset **`dbt_dw_rpt`** (região `us-east4`, produzidas pelo projeto `sb_dw_dbt`, pasta `models/4.rpt/Vendas/`), sem janela de tempo (histórico completo — diferente do padrão de "últimos 30 dias" do relatório de Google Ads, porque aqui o volume é pequeno e o filtro de meses precisa poder olhar qualquer mês passado):

- **`rpt_vendas_dia`** — grão 1 linha por dia. Fonte do bloco "Dia Atual", dos 3 blocos de cards do "Mês" e do gráfico "Vendas por Dia" (todos deriváveis de somas/valores diários, ver seção "Bloco Mês" abaixo).
- **`rpt_vendas_pedidos`** — grão 1 linha por item de pedido. Fonte da tabela "Pedidos do Mês" e de toda a seção "Produtos" (tabela por produto, gráficos de categoria/subcategoria). Além das colunas de valor, traz `nm_produto_base` (nome do produto **sem** variação de cor/tamanho — vem de `tb_produto` via `tb_pedido.nm_produto`, diferente de `nm_produto_completo`/`nm_produto`, que inclui a variação), `ds_categoria` e `ds_subcategoria` (também de `tb_produto`, via `tb_pedido`).
- **`rpt_vendas_recorrencia_dia`** — grão 1 linha por dia, fonte da seção "Clientes — Retorno" (v6, 2026-07-01). Diferente das outras duas, **não é filtrada pelo seletor de mês** — é uma janela móvel própria de 60 dias, recalculada para cada dia do histórico. Ver seção própria abaixo.

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

## Regra de negócio: margem de lucro % e taxa (v5, 2026-07-01 — taxa real unificada)

`vl_lucro_bruto` (calculado em `rpt_vendas_dia.sql`) = faturamento líquido − custo de mercadoria vendida, onde faturamento líquido = faturamento bruto − frete rateado − **taxa real por forma de pagamento** (`vl_taxa_pedido_rateio`, soma direto de `tb_pedido_agg_dia` — já vinha propagada até essa tabela, só não estava sendo usada aqui).

**Histórico:** até 2026-07-01 este cálculo usava uma **aproximação fixa de 5%** do faturamento bruto (mesma fórmula de `tb_atingimento_faturamento_dinamico.sql`), enquanto `rpt_vendas_pedidos` (tabela de pedidos, seção "Produtos") já usava a taxa real desde a v2 — uma inconsistência conhecida e documentada nesta spec. **Pedido explícito do usuário:** "se a gente já tem o dado da taxa real, precisamos usá-lo" — trocada a aproximação pela taxa real em `rpt_vendas_dia.sql` (coluna renomeada de `vl_taxa_aproximada` para `vl_taxa`), unificando com `rpt_vendas_pedidos`. Todo o relatório usa a mesma base de taxa agora — a inconsistência intencional descrita nas versões anteriores desta spec não existe mais.

**Margem de lucro % = `vl_lucro_bruto ÷ vl_faturamento_liquido`** (não ÷ faturamento bruto). Escolhida por ser a mesma base sobre a qual o lucro já é calculado.

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
| Faturamento Líquido | Faturamento Bruto − Frete − Taxa (real) | `vl_faturamento_liquido` |
| Custo Total dos Produtos | soma de `vl_custo_pedido` | `vl_custo_mercadoria` |
| Lucro | Faturamento Líquido − Custo Total dos Produtos | `vl_lucro_bruto` |
| Margem de Lucro % | Lucro ÷ Faturamento Líquido | calculado no app |

**Decisão de negócio (v2, sem confirmação explícita do usuário — fácil de reverter):** "Faturamento Bruto" continua definido como no bloco "Dia Atual" (soma de `vl_total_pedido`, que já vem líquido de desconto — ver `tb_pedido.sql`, projeto `sb_dw_dbt`). **"Descontos Totais" é informativo, não é subtraído de novo do Faturamento Bruto** (o desconto já está embutido nele) — mostrar os dois lado a lado poderia sugerir uma cascata literal bruto − frete − desconto = líquido, mas essa conta **não fecha** com os números aqui, de propósito, porque líquido desconta taxa, não desconto. Se isso causar confusão no uso real, a alternativa é redefinir Faturamento Bruto como pré-desconto (`vl_total_item`) — mudaria também o card já validado do bloco "Dia Atual".

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
| Taxa | soma de `vl_taxa` do pedido (resumida) / fração do item (detalhada) | **taxa real** por forma de pagamento (alíquota + taxa fixa do meio de pagamento) — mesma base usada em `rpt_vendas_dia` desde 2026-07-01 (v5) |
| Lucro | soma de `vl_lucro` do pedido (resumida) / valor do item (detalhada) — fórmula: `vl_total_pedido − vl_frete − vl_taxa − vl_custo_produto` | |
| Margem de Lucro % | Lucro ÷ (Total do Pedido − Frete − Taxa) — **não** é Lucro ÷ Total do Pedido | recalculada no nível do pedido na view resumida, não é a média das margens dos itens |

**Bug corrigido em 2026-07-01** (achado pelo usuário comparando o Lucro desta tabela com o Lucro do bloco "Hoje" — ficou muito evidente porque o mês tinha só 1 pedido): `vl_lucro` em `rpt_vendas_pedidos.sql` esquecia de descontar o frete (`vl_frete_rateio`), calculando só `Total do Pedido − Taxa − Custo`. Como `vl_total_pedido` já inclui o frete cobrado do cliente (ver Fonte de Dados), isso inflava o lucro em exatamente o valor do frete — R$ 24,52 de diferença no pedido de teste (151,40 em vez de 126,88). A fórmula correta espelha a mesma lógica de `rpt_vendas_dia` (que já descontava frete corretamente): `Total do Pedido − Frete − Taxa − Custo`. `pct_margem_lucro` também foi corrigida (denominador passa a ser líquido de frete e taxa, não o Total do Pedido bruto), e o cálculo equivalente em Python (`_tabela_pedidos_resumo()`, que recalcula a margem localmente pois agrega várias linhas) foi ajustado do mesmo jeito.

**Unificação de taxa (v5, 2026-07-01):** até então, esta tabela já usava a taxa real e os blocos de cards usavam a aproximação de 5% — uma inconsistência intencional documentada aqui. Resolvida a pedido do usuário: `rpt_vendas_dia` passou a usar a mesma taxa real (ver "Regra de negócio: margem de lucro % e taxa" acima). Depois da correção do bug de frete (parágrafo acima) **e** desta unificação, o Lucro de um pedido nesta tabela agora bate exatamente com o Lucro do bloco "Hoje"/"Mês" quando o período é o mesmo (validado com o pedido de teste: R$ 126,88 nos dois lugares).

## Seção "Produtos" (v4, 2026-07-01)

Fonte: `rpt_vendas_pedidos`, filtrada pelos meses selecionados no filtro de topo (a mesma seleção usada pela tabela "Pedidos do Mês").

### Tabela "Venda por Produto"

1 linha por `nm_produto_base` (`_tabela_venda_produto()`), somando os itens de todas as suas variações — **regra de negócio: agrupamento pelo nome do produto sem variação de cor/tamanho**, pedida explicitamente pelo usuário ("use o nome do produto sem a variação, pra agrupar todas as vendas de suas variações numa linha só"). `nm_produto_base` vem de `tb_produto` (via `tb_pedido.nm_produto`) — é o nome cadastrado no catálogo, diferente de `nm_produto_completo` (que inclui sufixos como "Cor:Amarela" ou "Tamanho:Pequeno", adicionados pelo Bling na venda). **Atenção:** dimensões que fazem parte do nome cadastrado do produto (ex.: "Corda de Algodão Tratada - 6mm X 8m" vs. "- 6mm X 10m") continuam como produtos diferentes — não é uma variação de cor/tamanho no sentido do Bling, é um produto distinto no catálogo.

Colunas: Produto, Quantidade Vendida (soma de `qt_item`), Custo do Produto (soma de `vl_custo_produto`), Faturamento Total (soma de `vl_faturamento_total`), Lucro (soma de `vl_lucro`), Margem de Lucro % (Lucro ÷ Faturamento Total, recalculada no nível do produto — mesma lógica de "não é a média das margens dos itens" da tabela de pedidos). Ordenada por Faturamento Total decrescente.

### Gráficos "Faturamento por Categoria" e "Faturamento por Subcategoria"

**Pizza (v6, 2026-07-01, trocado de barra horizontal a pedido do usuário)** — `_grafico_pizza_participacao()`, lado a lado via `st.columns(2)`. Rótulo (nome + %) fora da fatia, com linha conectora (comportamento nativo do Plotly com `textposition="outside"`, sem configuração extra). Sem legenda — cada fatia já tem nome e % escritos ao lado, uma legenda só repetiria a mesma informação.

Valor: `vl_faturamento_total` somado por `ds_categoria`, depois por `ds_subcategoria`. **Cor por identidade, não por ranking do período**: a cor de cada categoria é fixa, baseada no ranking de faturamento de **todo o histórico** (`_mapa_cores_categoria()`), não do(s) mês(es) selecionado(s) no filtro — assim, trocar o filtro de mês nunca repinta a mesma categoria com uma cor diferente (a categoria "Shibari" é sempre a mesma cor, esteja ela em 1º ou 5º lugar no mês em tela). Só as **7 categorias de maior faturamento no histórico completo** ganham cor própria (paleta categórica de 8 tons em `common/design.py`, `CATEGORICAL_PALETTE`); o resto soma em **"Outros"** (cinza `MUTED`) — com 14 subcategorias reais, uma pizza com 14 cores ficaria ilegível e sem segurança de contraste para daltonismo; 7 + Outros é o limite recomendado para gráfico de pizza/paleta categórica.

**Regra de negócio: "faturamento sem frete" = `vl_faturamento_total`** (pedido explicitamente pelo usuário). Não é preciso subtrair frete de nada — `vl_faturamento_total` (= `vl_total_item` em `tb_pedido`) já é o valor bruto do item, calculado **antes** de frete e desconto entrarem na conta (ver Tabela "Pedidos do Mês" acima). É diferente de "Faturamento Bruto" dos blocos de cards (que é `vl_total_pedido`, já com frete/desconto aplicados) — os dois "faturamento" deste relatório não são a mesma base, ver "Limitações conhecidas".

### Mapa "Pedidos por Estado" — removido (v8, 2026-07-02)

Existiu brevemente na v7 (choropleth por UF, GeoJSON do IBGE) e foi **removido a pedido do usuário** ("não deu certo") — apresentou algum problema em produção (Streamlit Cloud) que não foi possível reproduzir localmente (ambiente de desenvolvimento local preso em Python 3.8, incompatível com a versão de `pandas` fixada em `requirements.txt`). Removido por completo: função `_mapa_pedidos_estado()`/`_carregar_geojson_uf()` e o mapa `CODAREA_PARA_UF` em `reports/vendas.py`, o arquivo `common/geo/brasil_uf.geojson`, e a coluna `ds_uf` (só existia pra alimentar o mapa) em `rpt_vendas_pedidos.sql` — a coluna `cd_contato` (adicionada na mesma leva, mas usada pela tabela "Clientes que Mais Gastaram") foi mantida. Se o mapa for retomado no futuro, considerar investigar a causa raiz antes de reimplementar — não foi identificada nesta rodada.

## Seção "Clientes — Retorno" (v6, 2026-07-01)

**Não é filtrada pelo seletor de mês** — usa todo o histórico de `rpt_vendas_recorrencia_dia`, porque é uma janela móvel própria (60 dias), um conceito diferente do "mês selecionado" do resto da página. Deixado explícito no título da seção pra não confundir.

### Histórico da decisão (por que não é média nem contagem acumulada)

Pedido original do usuário: um indicador de "retorno do cliente" — quantidade média de compras por cliente, em todo o período de dados, recalculado todo dia. Duas ideias foram descartadas em conversa antes de chegar na definição final, porque ambas tinham problemas reais:

1. **Média de compras por cliente, acumulada desde o início dos dados.** Problema: é diluída por aquisição — todo cliente novo entra na conta com 1 compra, então crescer a base de clientes empurra a média pra baixo (ou trava ela) mesmo que a fidelidade dos clientes antigos esteja melhorando de verdade. Mistura "crescimento de base" com "retenção" numa métrica só, que são efeitos opostos.
2. **Contagem de clientes com mais de 1 compra, acumulada.** Problema: é um contador que só sobe (ratchet) — uma vez que um cliente cruza a linha de 2 compras, ele fica contado ali pra sempre, mesmo que nunca volte a comprar. Não captura o cliente que já tinha 2 compras e comprou de novo (ele já tinha cruzado a linha há tempo), nem reflete quando alguém para de comprar.

**Definição final: Clientes Recorrentes, numa janela móvel de 60 dias.** Um cliente conta como recorrente no dia D se comprou no período (D-59 a D) **e** já tinha comprado antes desse período começar (antes de D-60). Não é diluído por cliente novo (ele entra em "Clientes Novos", não em "Recorrentes") e não tem ratchet (se o cliente para de comprar, ele sai da contagem quando a janela avança 60 dias além da última compra dele). É o equivalente ao conceito de mercado **"Returning Customers"**, numa janela móvel em vez de acumulada — a mesma ideia usada por padrão em relatórios de e-commerce (Shopify, GA, Klaviyo etc.) pra separar cliente novo de cliente recorrente.

**60 dias foi escolhido pelo usuário** ("pro meu negócio 60 dias seria o ideal") — não é um benchmark de mercado, é o ciclo de recompra que faz sentido pro negócio da Shibari Brasil.

### Fonte de dados e cálculo

`rpt_vendas_recorrencia_dia` (passthrough de `models/3.az/Cliente/tb_cliente_recorrencia_dia.sql`, projeto `sb_dw_dbt`), grão 1 linha por dia, **com backfill retroativo** desde que há dado de pedido (~nov/2023) — decisão do usuário, pra já nascer com histórico longo em vez de esperar meses pra ver tendência. Materializada como `table` normal (não incremental) — é função pura do histórico de pedidos, não tem "congelamento do passado" como `tb_atingimento_faturamento_dinamico`.

Reaproveita `tb_cliente.sql` (já existente, projeto `sb_dw_dbt`) — que já calcula `dt_prim_pedido` (data da primeira compra de cada cliente, já excluindo `CANCELADO`) por `cd_contato`. `tb_cliente_recorrencia_dia.sql` não recalcula "primeira compra por cliente" do zero: pra cada dia D, junta `stg_tempo` com os pedidos por intervalo (`dt_pedido` dentro de `(D-60, D]`, um join por range, não por igualdade de data) e cruza com `dt_prim_pedido` de `tb_cliente` pra decidir se cada cliente comprador daquele dia já existia antes da janela.

| Indicador | Fórmula | Coluna em `rpt_vendas_recorrencia_dia` |
|---|---|---|
| Clientes Recorrentes | clientes com compra nos últimos 60 dias E primeira compra antes disso | `qt_clientes_recorrentes` |
| Taxa de Recorrência | Clientes Recorrentes ÷ Clientes Compradores (60 dias) | `pct_recorrencia` |
| Clientes Novos (60 dias) | Clientes Compradores − Clientes Recorrentes | `qt_clientes_novos` |

**Regra de negócio: exclusão de `CANCELADO`** também vale aqui — herdada de `tb_pedido` (filtro na CTE de pedidos) e de `tb_cliente.dt_prim_pedido` (que já exclui `CANCELADO` na própria definição).

**Semântica de borda dos 60 dias, validada com dado real:** o dia exatamente 60 dias após a primeira compra de um cliente **ainda não** conta como recorrente — só a partir do dia seguinte (`dt_prim_pedido < D-60`, comparação estrita). Testado com um cliente real (primeira compra 2026-02-11): em D=2026-04-12 (exatamente 60 dias depois) ele ainda não é recorrente; em D=2026-04-13 já é, se tiver compra na janela.

### Tabela "Clientes que Mais Gastaram" (v7, 2026-07-01)

Top 10 clientes por `vl_total_pedido` somado, nos últimos 60 dias — **mesma janela móvel desta seção, não segue o filtro de mês** (o usuário confirmou explicitamente que não faria sentido misturar as duas janelas). Fonte: `rpt_vendas_pedidos`, agrupado por `cd_contato` (chave de cliente — agrupar só por nome poderia confundir dois clientes homônimos) via `_tabela_top_clientes_60dias()`.

| Coluna | Fórmula |
|---|---|
| Cliente | `nm_cliente` |
| Qtd Pedidos | contagem de `cd_pedido` distintos no período |
| Faturamento Total | soma de `vl_total_pedido` no período — **"quanto o cliente efetivamente pagou"**, mesma base de "Faturamento Bruto"/"Total do Pedido" do resto do relatório (não é `vl_faturamento_total`, que é o bruto antes de frete/desconto) |

## Limitações conhecidas

- ~~A taxa de 5% usada nos blocos de cards é uma aproximação~~ — resolvido em 2026-07-01 (v5): todo o relatório usa a taxa real por forma de pagamento (`vl_taxa_pedido_rateio`). Limitação residual: essa taxa real vem de `stg_forma_pagamento` (alíquota + taxa fixa cadastradas por forma de pagamento) — se a taxa negociada com o meio de pagamento mudar e o cadastro não for atualizado, o valor fica desatualizado; não é mais uma aproximação estrutural, mas depende de cadastro correto.
- `vl_objetivo_total`/`vl_meta_dia` dependem de `stg_meta_faturamento` (planilha do Drive) estar preenchida para o mês em questão — meses sem meta cadastrada terão essas colunas nulas, e "Atingimento da Meta" fica indefinido (não dividir por nulo/zero).
- Nem `rpt_vendas_dia` nem `rpt_vendas_pedidos` filtram por loja/canal de venda — é a conta consolidada. Quebra por canal fica fora de escopo.
- "Faturamento Bruto" nos blocos de cards já vem líquido de desconto (ver decisão acima) — não é o bruto "de livro contábil" antes de qualquer dedução. "Faturamento Total"/"sem frete" na tabela de pedidos e na seção "Produtos" (tabela por produto, gráficos de categoria/subcategoria), por outro lado, é o bruto de verdade (antes de frete/desconto) — os dois "bruto" do relatório não significam a mesma coisa; ler as tabelas com atenção antes de comparar entre seções.
- Categoria/subcategoria vêm de `tb_produto` (cadastro do Bling) — produto sem categoria cadastrada ou com cadastro `[Interno]` (ex.: material de embalagem/suprimento interno, não produto de venda) aparece nos gráficos igual a qualquer outra categoria; não há filtro para excluir categorias internas nesta v4.
- Os primeiros ~60 dias de `rpt_vendas_recorrencia_dia` (a partir de ~nov/2023) sempre mostram `qt_clientes_recorrentes = 0` — é esperado, não é bug: ainda não havia 60 dias de histórico anterior pra ninguém poder ser considerado "cliente antes da janela".
