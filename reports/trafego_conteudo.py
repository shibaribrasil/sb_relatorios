"""Relatório Tráfego & Conteúdo — Shibari Brasil (camada mensal).

Regras e definição de cada indicador: ver specs/trafego-conteudo.md. Aquisição, páginas e busca interna vêm do GA4
(`dbt_dw_us_az`, histórico desde 01/07/2026); busca no Google vem da `tb_gsc_consulta_diaria` (exportação em massa do Search Console).
Tudo na região US: consultas separadas da `az` de us-east4.
"""
import unicodedata

import pandas as pd
import streamlit as st

from common import bigquery as bq
from common.design import inject_css, card, render_cards, section_title, note, pct
from common.ga4 import INICIO_GA4
from reports.vendas_margem import _hoje_brt

GSC_TABELA = "dbt_dw_us_az.tb_gsc_consulta_diaria"


def _n(v):
    return f"{int(v):,}".replace(",", ".")


def _sem_acento(s):
    return "".join(c for c in unicodedata.normalize("NFD", str(s).lower()) if unicodedata.category(c) != "Mn")


@st.cache_data(ttl=900)
def carregar_trafego():
    client = bq.get_client()
    sess = bq.query_df(client, f"""
        SELECT s.cd_sessao, s.cd_usuario_pseudo, s.dt_data, s.fl_sessao_engajada, s.fl_conversao,
               COALESCE(s.ds_canal_fonte, '(not set)') AS ds_fonte, COALESCE(s.ds_canal_meio, '(not set)') AS ds_meio,
               COALESCE(s.ds_canal_campanha, '(not set)') AS ds_campanha,
               COALESCE(c.qt_compras, 0) AS qt_compras, COALESCE(c.vl_receita, 0) AS vl_receita
          FROM `{bq.PROJECT}.dbt_dw_us_az.tb_ga4_sessao` AS s
          LEFT JOIN (SELECT cd_sessao, COUNT(*) AS qt_compras, SUM(vl_compra) AS vl_receita
                       FROM `{bq.PROJECT}.dbt_dw_us_az.tb_ga4_compras` GROUP BY cd_sessao) AS c
                 ON s.cd_sessao = c.cd_sessao
    """)
    pag = bq.query_df(client, f"""
        SELECT cd_sessao, cd_usuario_pseudo, dt_data, ds_pagina_path, ds_pagina_titulo,
               fl_blog, fl_blog_post, fl_pagina_entrada, qt_pageviews
          FROM `{bq.PROJECT}.dbt_dw_us_az.tb_ga4_pagina_sessao`
    """)
    busca = bq.query_df(client, f"""
        SELECT cd_sessao, dt_data, ds_termo_normalizado, fl_sessao_conversao
          FROM `{bq.PROJECT}.dbt_dw_us_az.tb_ga4_busca_interna`
    """)
    for df in (sess, pag, busca):
        df["dt_data"] = pd.to_datetime(df["dt_data"])
    for c in ["qt_compras", "vl_receita"]:
        sess[c] = pd.to_numeric(sess[c]).fillna(0.0)
    pag["qt_pageviews"] = pd.to_numeric(pag["qt_pageviews"]).fillna(0)
    return {"sess": sess, "pag": pag, "busca": busca}


@st.cache_data(ttl=3600)
def carregar_gsc(ini, fim):
    """Consultas do Google no período (tb_gsc_consulta_diaria); None se a tabela ainda não existe."""
    client = bq.get_client()
    try:
        return bq.query_df(client, f"""
            SELECT ds_consulta, SUM(qt_cliques) AS qt_cliques, SUM(qt_impressoes) AS qt_impressoes,
                   SUM(qt_soma_posicao) AS qt_soma_posicao, MIN(dt_data) AS dt_min, MAX(dt_data) AS dt_max
              FROM `{bq.PROJECT}.{GSC_TABELA}`
             WHERE dt_data BETWEEN '{ini:%Y-%m-%d}' AND '{fim:%Y-%m-%d}' AND ds_tipo_busca = 'web'
             GROUP BY 1
        """)
    except Exception as e:
        if "Not found" in str(e) or "notFound" in str(e):
            return None
        raise


def _periodo_padrao(hoje):
    """Mês atual até ontem; no dia 1º, o mês anterior inteiro."""
    ontem = pd.Timestamp(hoje) - pd.Timedelta(days=1)
    return max(ontem.replace(day=1), INICIO_GA4).date(), ontem.date()


def _tabela_aquisicao(s, com_campanha):
    chaves = ["ds_fonte", "ds_meio"] + (["ds_campanha"] if com_campanha else [])
    g = s.groupby(chaves, as_index=False).agg(
        sess=("cd_sessao", "nunique"), usu=("cd_usuario_pseudo", "nunique"), eng=("fl_sessao_engajada", "sum"),
        comp=("qt_compras", "sum"), rec=("vl_receita", "sum")).sort_values("sess", ascending=False)
    tot = g["sess"].sum()
    out = pd.DataFrame({"Origem": g["ds_fonte"], "Meio": g["ds_meio"]})
    if com_campanha:
        out["Campanha"] = g["ds_campanha"]
    out["Sessões"] = g["sess"]
    out["% das sessões"] = g["sess"] / tot if tot else 0
    out["Usuários"] = g["usu"]
    out["Engajadas"] = g["eng"] / g["sess"].where(g["sess"] > 0)
    out["Compras (GA4)"] = g["comp"]
    out["Receita (GA4)"] = g["rec"]
    return out


def _tabela_paginas(p):
    g = p.groupby(["ds_pagina_titulo", "ds_pagina_path"], as_index=False).agg(
        pv=("qt_pageviews", "sum"), sess=("cd_sessao", "nunique"), usu=("cd_usuario_pseudo", "nunique"),
        ent=("fl_pagina_entrada", "sum")).sort_values(["pv", "sess"], ascending=False)
    return pd.DataFrame({"Título": g["ds_pagina_titulo"], "Caminho": g["ds_pagina_path"], "Pageviews": g["pv"],
                         "Sessões": g["sess"], "Usuários": g["usu"], "Entradas": g["ent"]})


CFG_PAG = {
    "Título": st.column_config.TextColumn(width="large"), "Caminho": st.column_config.TextColumn(width="medium"),
    "Pageviews": st.column_config.NumberColumn(format="%d", width=90), "Sessões": st.column_config.NumberColumn(format="%d", width=80),
    "Usuários": st.column_config.NumberColumn(format="%d", width=80), "Entradas": st.column_config.NumberColumn(format="%d", width=80),
}


def render():
    inject_css()
    with st.spinner("Carregando dados..."):
        try:
            d = carregar_trafego()
        except Exception as e:
            st.error(f"Erro ao carregar dados: {e}")
            return
    hoje = _hoje_brt()
    st.html(f"""
    <div class="report-header">
      <div>
        <div class="report-brand">shibari brasil · camada mensal</div>
        <div class="report-title">Tráfego <span>&amp;</span> Conteúdo</div>
        <div class="report-meta">GA4 (desde 01/07/2026) · Search Console (exportação em massa) · abre no mês atual até ontem</div>
      </div>
      <div class="report-badge">Hoje: <strong>{hoje.strftime("%d/%m/%Y")}</strong></div>
    </div>
    """)

    c1, c2 = st.columns([1, 2])
    with c1:
        rng = st.date_input("Período", value=_periodo_padrao(hoje), min_value=INICIO_GA4.date(), max_value=pd.Timestamp(hoje).date(),
                            format="DD/MM/YYYY")
    with c2:
        busca_titulo = st.text_input("Buscar título de página", placeholder="ex.: corda, karada, kit iniciante…")
    if not isinstance(rng, (tuple, list)) or len(rng) != 2:
        st.info("Escolha a data inicial e a final.")
        return
    ini, fim = pd.Timestamp(rng[0]), pd.Timestamp(rng[1])
    no = lambda df: df[(df["dt_data"] >= ini) & (df["dt_data"] <= fim)]
    s, p, b = no(d["sess"]), no(d["pag"]), no(d["busca"])
    if busca_titulo.strip():
        alvo = _sem_acento(busca_titulo.strip())
        chave = (p["ds_pagina_titulo"].map(_sem_acento) + " " + p["ds_pagina_path"].map(_sem_acento))
        p_filtro = p[chave.str.contains(alvo, regex=False)]
    else:
        p_filtro = p

    # ═══ RESUMO ═══
    section_title(f"Resumo: {ini.strftime('%d/%m/%Y')} a {fim.strftime('%d/%m/%Y')}")
    n_sess = s["cd_sessao"].nunique()
    render_cards([
        card("Sessões", _n(n_sess), f"{pct(s['fl_sessao_engajada'].sum() / n_sess if n_sess else None, 0)} engajadas"),
        card("Usuários", _n(s["cd_usuario_pseudo"].nunique()), "navegadores distintos (GA4)"),
        card("Pageviews", _n(p["qt_pageviews"].sum()), f"{p['qt_pageviews'].sum() / n_sess:.1f} por sessão".replace(".", ",") if n_sess else ""),
        card("Buscas na loja", _n(len(b)), f"{_n(b['cd_sessao'].nunique())} sessões buscaram"),
    ])
    if fim >= pd.Timestamp(hoje) - pd.Timedelta(days=2):
        note("O período inclui os <strong>últimos 2 dias</strong>: o GA4 ainda reprocessa a origem das sessões recentes, então a atribuição desses dias é provisória.", "warn")

    # ═══ AQUISIÇÃO ═══
    section_title("Aquisição: sessões por origem / meio / campanha")
    visao = st.radio("Agrupar por", ["Origem / meio / campanha", "Origem / meio"], horizontal=True, label_visibility="collapsed")
    tab = _tabela_aquisicao(s, visao.startswith("Origem / meio / c"))
    st.dataframe(tab, hide_index=True, use_container_width=True, height=min(38 + 35 * len(tab), 460),
                 column_config={"Origem": st.column_config.TextColumn(width="small"), "Meio": st.column_config.TextColumn(width="small"),
                                "Campanha": st.column_config.TextColumn(width="medium"),
                                "Sessões": st.column_config.NumberColumn(format="%d", width=80),
                                "% das sessões": st.column_config.NumberColumn(format="percent", width=100),
                                "Usuários": st.column_config.NumberColumn(format="%d", width=80),
                                "Engajadas": st.column_config.NumberColumn(format="percent", width=90),
                                "Compras (GA4)": st.column_config.NumberColumn(format="%d", width=110),
                                "Receita (GA4)": st.column_config.NumberColumn(format="R$ %.0f", width=110)})
    note("Origem, meio e campanha pela atribuição <strong>last click da sessão</strong> do GA4. Compras e receita são as do GA4, que perde parte dos pedidos: "
         "a origem por pedido, mais precisa, está em Vendas & Margem — não some as duas. Google cpc com campanha <em>(not set)</em> = clique sem gclid (ex.: iPhone). "
         "Com volume baixo, linhas com poucas sessões oscilam muito: compare meses fechados.")

    # ═══ BLOG ═══
    section_title("Blog")
    blog = p[p["fl_blog"]]
    sess_blog = blog["cd_sessao"].unique()
    conv_blog = s[s["cd_sessao"].isin(sess_blog) & s["fl_conversao"]]["cd_sessao"].nunique()
    render_cards([
        card("Sessões com blog", _n(len(sess_blog)), f"{pct(len(sess_blog) / n_sess if n_sess else None, 1)} das sessões do site"),
        card("Entraram pelo blog", _n(blog[blog["fl_pagina_entrada"]]["cd_sessao"].nunique()), "sessões que começaram no blog"),
        card("Pageviews do blog", _n(blog["qt_pageviews"].sum()), f"{_n(blog['cd_usuario_pseudo'].nunique())} usuários"),
        card("Compraram na sessão", _n(conv_blog), "sessões com blog e compra (GA4)"),
    ])
    posts = _tabela_paginas(p_filtro[p_filtro["fl_blog_post"]])
    if posts.empty:
        st.caption("Nenhum post do blog no período" + (" com esse título." if busca_titulo.strip() else "."))
    else:
        st.dataframe(posts, hide_index=True, use_container_width=True, column_config=CFG_PAG, height=min(38 + 35 * len(posts), 420))
    note("Blog = páginas sob /blog (a tabela lista só os posts, /blog/posts/). <strong>Entradas</strong> = sessões que começaram no post: mede o post como porta de entrada "
         "(busca orgânica, Instagram). \"Compraram na sessão\" é associação, não prova de que o post vendeu. A busca por título filtra esta tabela.")

    # ═══ PÁGINAS ═══
    section_title("Páginas por título" + (f" — filtro: “{busca_titulo.strip()}”" if busca_titulo.strip() else ""))
    pags = _tabela_paginas(p_filtro)
    if pags.empty:
        st.caption("Nenhuma página encontrada com esse título no período.")
    else:
        st.dataframe(pags, hide_index=True, use_container_width=True, column_config=CFG_PAG, height=min(38 + 35 * len(pags), 460))
    note("Caminho normalizado: a mesma página com e sem \"/\" no final, ou com parâmetros na URL, vira uma linha só. O título é o mais frequente da página em todo o histórico "
         "(quando o GA4 nunca recebeu título, aparece o caminho). A busca ignora maiúsculas e acentos e procura no título e no caminho.")

    # ═══ BUSCA INTERNA ═══
    section_title("O que procuram na loja (busca interna)")
    if b.empty:
        st.caption("Nenhuma busca na loja no período.")
    else:
        g = (b.assign(sess_conv=b["cd_sessao"].where(b["fl_sessao_conversao"].astype(bool)))
              .groupby("ds_termo_normalizado", as_index=False)
              .agg(buscas=("cd_sessao", "size"), sess=("cd_sessao", "nunique"), conv=("sess_conv", "nunique"))
              .sort_values(["buscas", "sess"], ascending=False))
        st.dataframe(pd.DataFrame({"Termo": g["ds_termo_normalizado"], "Buscas": g["buscas"], "Sessões": g["sess"], "Sessões com compra": g["conv"]}),
                     hide_index=True, use_container_width=True, height=min(38 + 35 * len(g), 420),
                     column_config={"Termo": st.column_config.TextColumn(width="large"), "Buscas": st.column_config.NumberColumn(format="%d", width=80),
                                    "Sessões": st.column_config.NumberColumn(format="%d", width=80),
                                    "Sessões com compra": st.column_config.NumberColumn(format="%d", width=140)})
    note("Termos em minúsculas, sem espaços sobrando (acentos mantidos). Termo muito buscado e sem compra = produto que falta, nome diferente do que o cliente usa "
         "ou resultado de busca ruim — vale conferir a busca na loja com o termo.")

    # ═══ BUSCA NO GOOGLE ═══
    section_title("O que buscam no Google (Search Console)")
    try:
        gsc = carregar_gsc(ini.date(), fim.date())
    except Exception as e:
        st.error(f"Erro ao carregar o Search Console: {e}")
        gsc = pd.DataFrame()
    if gsc is None:
        note("<strong>Aguardando dados.</strong> A exportação em massa do Search Console para o BigQuery foi pedida em 24/09/2026; o primeiro lote chega em até 48 h "
             "e o histórico começa no dia em que ela foi ligada.", "warn")
    elif gsc.empty:
        st.caption("Sem dados do Search Console no período (a exportação só tem dados a partir do dia em que foi ligada, com ~2–3 dias de atraso).")
    else:
        for c in ["qt_cliques", "qt_impressoes", "qt_soma_posicao"]:
            gsc[c] = pd.to_numeric(gsc[c]).fillna(0.0)
        cl, im = gsc["qt_cliques"].sum(), gsc["qt_impressoes"].sum()
        render_cards([
            card("Cliques do Google", _n(cl), "busca orgânica (web)"),
            card("Impressões", _n(im), f"dados de {pd.to_datetime(gsc['dt_min']).min():%d/%m} a {pd.to_datetime(gsc['dt_max']).max():%d/%m}"),
            card("CTR", pct(cl / im if im else None, 1), "cliques ÷ impressões"),
            card("Posição média", f"{gsc['qt_soma_posicao'].sum() / im + 1:.1f}".replace(".", ",") if im else "—", "ponderada por impressão"),
        ])
        g = gsc.sort_values(["qt_cliques", "qt_impressoes"], ascending=False)
        st.dataframe(pd.DataFrame({
            "Consulta": g["ds_consulta"], "Cliques": g["qt_cliques"], "Impressões": g["qt_impressoes"],
            "CTR": g["qt_cliques"] / g["qt_impressoes"].where(g["qt_impressoes"] > 0),
            "Posição média": g["qt_soma_posicao"] / g["qt_impressoes"].where(g["qt_impressoes"] > 0) + 1}),
            hide_index=True, use_container_width=True, height=460,
            column_config={"Consulta": st.column_config.TextColumn(width="large"), "Cliques": st.column_config.NumberColumn(format="%d", width=80),
                           "Impressões": st.column_config.NumberColumn(format="%d", width=100),
                           "CTR": st.column_config.NumberColumn(format="percent", width=80),
                           "Posição média": st.column_config.NumberColumn(format="%.1f", width=110)})
    note("Busca orgânica no Google (não inclui anúncios). CTR e posição são soma ÷ soma. Consultas raras vêm anonimizadas pelo Google e aparecem agrupadas. "
         "Consulta com muitas impressões e CTR baixo = título/descrição da página a melhorar; posição entre 5 e 15 = oportunidade de conteúdo para subir à 1ª página.")
