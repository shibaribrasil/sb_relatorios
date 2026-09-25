# Spec — Tráfego & Conteúdo (`reports/trafego_conteudo.py`, camada Mensal)

Pergunta: **de onde vem o tráfego do site, o que as pessoas leem e o que elas procuram?** (aquisição por origem/meio/campanha, blog, páginas, busca interna, busca no Google)

## Filtros
- **Período**: intervalo de datas livre. Abre no **mês atual até ontem** (dias fechados); no dia 1º abre no mês anterior inteiro. Mínimo 01/07/2026 (início do histórico do GA4).
- **Buscar título de página**: texto livre, sem diferenciar maiúsculas nem acentos; procura no título **e** no caminho. Filtra as tabelas de blog e de páginas (não afeta aquisição nem buscas).

## Fontes (todas região US — consulta separada da `az` de us-east4)
| Seção | Fonte |
|---|---|
| Aquisição | `dbt_dw_us_az.tb_ga4_sessao` (1 linha por sessão; `ds_canal_fonte`, `ds_canal_meio`, `ds_canal_campanha`) + `tb_ga4_compras` (compras GA4 deduplicadas por transação) |
| Blog e páginas | `dbt_dw_us_az.tb_ga4_pagina_sessao` (sessão × caminho normalizado; título mais frequente do caminho; `fl_blog`, `fl_blog_post`, `fl_pagina_entrada`) |
| Busca interna | `dbt_dw_us_az.tb_ga4_busca_interna` (evento `view_search_results`; termo normalizado) |
| Busca no Google | `dbt_dw_us_az.tb_gsc_consulta_diaria` (dia × tipo de busca × dispositivo × país × consulta), sobre a exportação em massa do Search Console (`searchconsole.searchdata_site_impression`) |

## Indicadores e regras
- **Sessões** = contagem de sessões (`cd_sessao`) com início no período. **Usuários** = `cd_usuario_pseudo` distintos (navegador/dispositivo, não pessoa).
- **Engajadas (%)** = sessões engajadas ÷ sessões (definição do GA4: >10 s, 2+ páginas ou conversão).
- **Origem / meio / campanha** = atribuição **last click da sessão** do GA4 (`session_traffic_source_last_click.cross_channel_campaign`). Visão agrupável em "Origem / meio" ou "Origem / meio / campanha".
- **Compras (GA4)** e **Receita (GA4)** = `tb_ga4_compras` ligadas à sessão. É a atribuição do GA4 (perde parte dos pedidos): a origem por pedido, mais precisa, está em Vendas & Margem. Não somar com pedidos da `tb_pedido`.
- **Pageviews** = eventos `page_view`. **Sessões na página** = sessões distintas que viram a página. **Entradas** = sessões que começaram na página. Caminho normalizado (sem domínio, query e barra final) — a mesma página com e sem "/" vira uma linha só.
- **Blog** = caminhos sob `/blog`; tabela de posts usa `/blog/posts/`. Card "sessões com blog" = sessões distintas que viram ao menos uma página do blog; "compraram na sessão" = dessas, as que tiveram compra GA4 (associação, não causalidade).
- **Busca interna**: buscas = eventos; sessões = sessões distintas que buscaram; "com compra" = sessões que buscaram e compraram.
- **Busca no Google**: cliques, impressões, **CTR = Σ cliques ÷ Σ impressões**, **posição média = Σ sum_top_position ÷ Σ impressões + 1** (fórmula oficial da exportação). Só consultas não anonimizadas aparecem por termo; o total inclui as anonimizadas.
- Razões sempre soma ÷ soma.

## Limitações
- GA4 só desde 01/07/2026. Os **últimos ~2 dias** do GA4 ainda são reprocessados pelo Google (atribuição incompleta): por isso o padrão é até ontem, e a leitura de origem dos últimos 2 dias é provisória.
- Volume baixo (~2,5 mil sessões/mês, ~190 views de blog/mês, ~70 buscas internas/mês): não tire conclusão de linha com poucas sessões; compare meses fechados.
- ~14% das sessões chegam com origem "(not set)"; Google cpc com campanha "(not set)" = cliques sem gclid (ex.: iOS GBRAID/WBRAID).
- Search Console: exportação ligada em 24/09/2026; dados **desde 23/09/2026** (sem histórico anterior), com atraso de ~2 dias. A seção considera só busca **web** (imagem fica de fora).
