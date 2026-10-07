"""Relatório Instagram — Shibari Brasil (camada semanal).

Quatro abas, uma por pergunta de decisão: Semana (como foi?), Peças (o que funcionou?), Anúncios (vale pagar?) e
Audiência e atendimento (quem é, o que perguntam, como estão os pares?). Definição de cada indicador e limites:
specs/instagram.md.

Regra de negócio: tudo vem pronto do dbt (`tb_instagram_*`, `tb_meta_ads_*`, na az, us-east4). Aqui só se filtra, soma e
apresenta; razão é sempre soma ÷ soma. As medianas por formato são calculadas sobre taxas já prontas (apresentação, sem regra nova).
Instagram e Meta Ads chegam pela Graph/Marketing API 3x por dia; pedido com origem Instagram é um PISO (a maior parte das vendas
chega sem parâmetro) e conversão do Meta não é pedido real.
"""
import html

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from common import bigquery as bq
from common import semana as sem
from common.design import COLORS, inject_css, card, render_cards, section_title, note, plotly_layout, brl, pct
from common.frescor import carregar_frescor, badge_atualizacao, detalhe_atualizacao, alerta_atraso
from common.theme import CATEGORICAL, METRIC_COLORS
from reports.vendas_margem import _hoje_brt, _delta

AZ = f"{bq.PROJECT}.dbt_dw_az"
TABELAS = ("tb_instagram_conta_dia", "tb_instagram_funil_dia", "tb_instagram_midia", "tb_instagram_anuncio",
           "tb_instagram_comentario", "tb_instagram_concorrente", "tb_instagram_publico", "tb_instagram_horario_online")
EXTRATORES = ("instagram_midia", "instagram_conta", "instagram_concorrentes", "meta_ads")

SEMANAS_TENDENCIA = 12
DIAS_SEGUIDORES_INCOMPLETOS = 3   # a Meta fecha a série de seguidores novos com 2-3 dias de atraso
FORMATO_ROTULO = {"carrossel": "Carrossel", "reel": "Reel", "imagem": "Imagem", "video_feed": "Vídeo no feed", "story": "Story"}
PERIODOS_PECAS = {"Últimos 30 dias": 30, "Últimos 90 dias": 90, "Últimos 180 dias": 180, "Tudo": None}
PERIODOS_ANUNCIOS = {"Gasto nos últimos 90 dias": 90, "Gasto nos últimos 180 dias": 180, "Todos (desde 2023)": None}
DIAS_SEMANA = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]
ORDENAR = {
    "Taxa de envio": "pct_envio", "Taxa de salvamento": "pct_salvamento", "Views": "qt_views",
    "Seguidores ganhos": "qt_seguidores_ganhos", "Data de publicação": "ts_publicacao",
}


def _n(v):
    """Inteiro com ponto de milhar (padrão brasileiro); '—' para nulo."""
    if v is None or v != v:
        return "—"
    return f"{v:,.0f}".replace(",", ".")


def _num(df, cols, zero=False):
    for c in cols:
        df[c] = pd.to_numeric(df[c], errors="coerce")
        if zero:
            df[c] = df[c].fillna(0.0)
    return df


# ── Carga ──────────────────────────────────────────────────────────────────────────────────────────────────────
@st.cache_data(ttl=900)
def carregar_conta():
    client = bq.get_client()
    df = bq.query_df(client, f"""
        SELECT c.dt_data, c.qt_views, c.qt_views_organicas, c.qt_views_anuncio, c.fg_tem_quebra, c.qt_alcance, c.qt_alcance_seguidores,
               c.qt_alcance_nao_seguidores, c.qt_visitas_perfil, c.qt_cliques_site, c.qt_interacoes, c.qt_seguidores_novos, c.vl_gasto_meta,
               f.qt_pedidos_instagram, f.qt_pedidos_instagram_organico, f.qt_pedidos_instagram_pago, f.vl_receita_instagram,
               f.vl_lucro_instagram, f.vl_lucro_instagram_apos_midia
          FROM `{AZ}.tb_instagram_conta_dia` AS c
          JOIN `{AZ}.tb_instagram_funil_dia` AS f ON f.dt_data = c.dt_data
         WHERE c.dt_data >= DATE_SUB(CURRENT_DATE('America/Sao_Paulo'), INTERVAL 400 DAY)
         ORDER BY c.dt_data
    """)
    df["dt_data"] = pd.to_datetime(df["dt_data"])
    _num(df, ["qt_views", "qt_views_organicas", "qt_views_anuncio", "qt_alcance", "qt_alcance_seguidores", "qt_alcance_nao_seguidores",
              "qt_visitas_perfil", "qt_cliques_site", "qt_interacoes", "qt_seguidores_novos", "vl_gasto_meta"])
    _num(df, ["qt_pedidos_instagram", "qt_pedidos_instagram_organico", "qt_pedidos_instagram_pago", "vl_receita_instagram",
              "vl_lucro_instagram", "vl_lucro_instagram_apos_midia"], zero=True)
    df["fg_tem_quebra"] = df["fg_tem_quebra"].fillna(False).astype(bool)
    # a API devolve os 2-3 ultimos dias de seguidores novos incompletos (chegam a 0): tratar como "sem dado", nunca como queda
    corte = pd.Timestamp(_hoje_brt()) - pd.Timedelta(days=DIAS_SEGUIDORES_INCOMPLETOS)
    df.loc[df["dt_data"] > corte, "qt_seguidores_novos"] = np.nan
    return df


@st.cache_data(ttl=900)
def carregar_pecas():
    client = bq.get_client()
    df = bq.query_df(client, f"""
        SELECT cd_midia, ds_formato, dt_publicacao, ts_publicacao, ds_legenda, ds_permalink, qt_dias_publicada, qt_views, qt_alcance,
               qt_salvamentos, qt_envios, qt_interacoes, qt_seguidores_ganhos, qt_visitas_perfil, qt_cliques_link_bio, pct_envio,
               pct_salvamento, pct_interacao, pct_visita_perfil, qt_seguidores_por_mil_views, pct_rank_envio_formato,
               pct_rank_salvamento_formato, pct_rank_views_formato, fg_impulsionada, vl_gasto_anuncio, qt_hashtags, qt_slides,
               qt_segundos_medio_assistido, dt_ultimo_snapshot
          FROM `{AZ}.tb_instagram_midia`
         WHERE NOT fg_story
    """)
    df["dt_publicacao"] = pd.to_datetime(df["dt_publicacao"])
    _num(df, ["qt_views", "qt_alcance", "qt_salvamentos", "qt_envios", "qt_interacoes", "qt_seguidores_ganhos", "qt_visitas_perfil",
              "qt_cliques_link_bio", "pct_envio", "pct_salvamento", "pct_interacao", "pct_visita_perfil", "qt_seguidores_por_mil_views",
              "pct_rank_envio_formato", "pct_rank_salvamento_formato", "pct_rank_views_formato", "vl_gasto_anuncio"])
    df["fg_impulsionada"] = df["fg_impulsionada"].fillna(False).astype(bool)
    return df


@st.cache_data(ttl=900)
def carregar_curva(cd_midia):
    client = bq.get_client()
    df = bq.query_df(client, f"""
        SELECT dt_snapshot, qt_idade_horas, qt_views, qt_alcance, qt_salvamentos, qt_envios, qt_seguidores_ganhos
          FROM `{AZ}.tb_instagram_midia_curva` WHERE cd_midia = '{cd_midia}' ORDER BY dt_snapshot
    """)
    df["dt_snapshot"] = pd.to_datetime(df["dt_snapshot"])
    return _num(df, ["qt_views", "qt_alcance", "qt_salvamentos", "qt_envios", "qt_seguidores_ganhos", "qt_idade_horas"])


@st.cache_data(ttl=900)
def carregar_anuncios():
    client = bq.get_client()
    df = bq.query_df(client, f"""
        SELECT cd_anuncio, nm_anuncio, ds_status_efetivo, cd_midia_original, ds_permalink_midia_original, dt_publicacao_midia_original,
               vl_gasto, dt_primeiro_dia, dt_ultimo_dia, qt_dias_com_gasto, qt_visualizacao_pagina, qt_carrinho, qt_checkout,
               qt_compras_meta, vl_compras_meta, qt_pedidos_reais, vl_receita_real, vl_roas_real, vl_roas_meta
          FROM `{AZ}.tb_instagram_anuncio` WHERE vl_gasto > 0
    """)
    df["dt_ultimo_dia"] = pd.to_datetime(df["dt_ultimo_dia"])
    return _num(df, ["vl_gasto", "qt_visualizacao_pagina", "qt_carrinho", "qt_checkout", "qt_compras_meta", "vl_compras_meta",
                     "qt_pedidos_reais", "vl_receita_real", "vl_roas_real", "vl_roas_meta", "qt_dias_com_gasto"])


@st.cache_data(ttl=900)
def carregar_distribuicao():
    client = bq.get_client()
    df = bq.query_df(client, f"""
        SELECT ds_tipo, ds_chave1, ds_chave2, SUM(vl_gasto) AS vl_gasto, SUM(qt_cliques) AS qt_cliques
          FROM `{AZ}.tb_meta_ads_distribuicao_dia`
         WHERE dt_data >= DATE_SUB(CURRENT_DATE('America/Sao_Paulo'), INTERVAL 90 DAY)
         GROUP BY 1, 2, 3
    """)
    return _num(df, ["vl_gasto", "qt_cliques"])


@st.cache_data(ttl=900)
def carregar_audiencia():
    client = bq.get_client()
    publico = bq.query_df(client, f"""
        SELECT ds_quebra, ds_valor_dimensao, qt_valor, pct_do_total
          FROM `{AZ}.tb_instagram_publico`
         WHERE ds_metrica = 'follower_demographics' AND ds_quebra IN ('age', 'gender')
           AND dt_foto = (SELECT MAX(dt_foto) FROM `{AZ}.tb_instagram_publico`)
    """)
    horario = bq.query_df(client, f"SELECT nr_dia_semana, nr_hora, vl_media_seguidores_online, qt_dias_amostra FROM `{AZ}.tb_instagram_horario_online`")
    conc = bq.query_df(client, f"""
        SELECT nm_usuario, fg_propria, qt_seguidores, qt_seguidores_delta_7d, qt_seguidores_delta_30d, qt_posts_30d, fg_amostra_truncada,
               vl_media_curtidas_30d, vl_media_comentarios_30d, pct_interacao_media, pct_carrossel_30d, pct_video_30d, ts_ultimo_post
          FROM `{AZ}.tb_instagram_concorrente`
    """)
    perguntas = bq.query_df(client, f"""
        SELECT dt_comentario, ds_formato, ds_texto, nm_usuario, ds_permalink_midia
          FROM `{AZ}.tb_instagram_comentario`
         WHERE fg_sem_resposta_loja AND fg_pergunta AND dt_comentario >= DATE_SUB(CURRENT_DATE('America/Sao_Paulo'), INTERVAL 60 DAY)
         ORDER BY dt_comentario DESC LIMIT 100
    """)
    n_sem_resp = bq.query_df(client, f"""
        SELECT COUNTIF(fg_sem_resposta_loja) AS total, COUNTIF(fg_sem_resposta_loja AND fg_pergunta) AS perguntas
          FROM `{AZ}.tb_instagram_comentario`
    """)
    marc = bq.query_df(client, f"""
        SELECT dt_publicacao, nm_usuario, ds_tipo_midia, qt_curtidas, qt_comentarios, ds_permalink
          FROM `{AZ}.tb_instagram_marcacao` ORDER BY dt_publicacao DESC LIMIT 30
    """)
    for c in ("nr_dia_semana", "nr_hora", "vl_media_seguidores_online", "qt_dias_amostra"):
        horario[c] = pd.to_numeric(horario[c], errors="coerce")
    _num(conc, ["qt_seguidores", "qt_seguidores_delta_7d", "qt_seguidores_delta_30d", "qt_posts_30d", "vl_media_curtidas_30d",
                "vl_media_comentarios_30d", "pct_interacao_media", "pct_carrossel_30d", "pct_video_30d"])
    _num(publico, ["qt_valor", "pct_do_total"])
    return {"publico": publico, "horario": horario, "conc": conc, "perguntas": perguntas, "sem_resposta": n_sem_resp.iloc[0], "marcacoes": marc}


# ── Agregação (soma e razão soma ÷ soma) ───────────────────────────────────────────────────────────────────────
def _soma(s):
    """Soma ignorando nulos; None quando TODOS são nulos (dado inexistente no período, não zero)."""
    return None if s.isna().all() else float(s.sum())


def _somas(df):
    seg, nao = _soma(df["qt_alcance_seguidores"]), _soma(df["qt_alcance_nao_seguidores"])
    views, org = _soma(df["qt_views"]), _soma(df["qt_views_organicas"])
    alcance, visitas = _soma(df["qt_alcance"]), _soma(df["qt_visitas_perfil"])
    return {
        "views": views, "organicas": org, "anuncio": _soma(df["qt_views_anuncio"]), "alcance": alcance,
        "pct_nao": (nao / (seg + nao)) if seg is not None and nao is not None and (seg + nao) > 0 else None,
        "visitas": visitas, "pct_visita": (visitas / alcance) if alcance else None, "cliques": _soma(df["qt_cliques_site"]),
        "seg_novos": _soma(df["qt_seguidores_novos"]), "pedidos": float(df["qt_pedidos_instagram"].sum()),
        "pedidos_org": float(df["qt_pedidos_instagram_organico"].sum()), "pedidos_pago": float(df["qt_pedidos_instagram_pago"].sum()),
        "receita": float(df["vl_receita_instagram"].sum()), "lucro": float(df["vl_lucro_instagram"].sum()),
        "gasto": _soma(df["vl_gasto_meta"]),
    }


def _c(p, ant, chave, rot, tipo="rel", fmt=None):
    return _delta(p.get(chave), ant.get(chave), rot, tipo, fmt)


# ── Aba 1 · Semana ─────────────────────────────────────────────────────────────────────────────────────────────
def _aba_semana(conta):
    fechado = min(pd.Timestamp(_hoje_brt()) - pd.Timedelta(days=1), conta["dt_data"].max())
    opcoes = sem.semanas(conta["dt_data"].min(), fechado)
    seg = st.selectbox("Semana", opcoes, format_func=lambda s: sem.rotulo(s, fechado) + (" (em andamento)" if sem.janela(s, fechado)["parcial"] else ""), key="ig_semana")
    j = sem.janela(seg, fechado)
    atual = sem.entre(conta, "dt_data", j["ini"], j["fim"])
    anterior = sem.entre(conta, "dt_data", j["ant_ini"], j["ant_fim"])
    p, a = _somas(atual), _somas(anterior)
    rot = f"{sem.rotulo(j['ant_ini'], j['ant_fim'])}"
    rot_curto = "semana anterior" + (f" ({j['n']} dias)" if j["parcial"] else "")

    def d(chave, **kw):
        return _c(p, a, chave, rot_curto, **kw)

    dv, cv = d("views", fmt=_n)
    do, co = d("organicas", fmt=_n)
    dn, cn = d("pct_nao", tipo="pp", fmt=pct)
    dvi, cvi = d("visitas", fmt=_n)
    dc, cc = d("cliques", fmt=_n)
    dp, cp = d("pedidos", fmt=_n)
    dg, _ = d("gasto", fmt=lambda v: brl(v, 0))
    dsn, csn = d("seg_novos", fmt=_n)
    org_share = (p["organicas"] / p["views"]) if p["views"] and p["organicas"] is not None else None
    render_cards([
        card("Views", _n(p["views"]), sub=f"{j['n']} dias · {j['ini']:%d/%m}–{j['fim']:%d/%m}", delta=dv, delta_color=cv),
        card("Views orgânicas", _n(p["organicas"]), sub=f"{pct(org_share)} das views" if org_share is not None else "sem quebra no período",
             ref=f"anúncio: {_n(p['anuncio'])}", delta=do, delta_color=co),
        card("Alcance de não seguidores", pct(p["pct_nao"]), sub="soma dos dias (não é alcance único)", delta=dn, delta_color=cn),
        card("Visitas ao perfil", _n(p["visitas"]), sub=f"{pct(p['pct_visita'])} do alcance somado" if p["pct_visita"] is not None else "", delta=dvi, delta_color=cvi),
        card("Toques no link da bio", _n(p["cliques"]), sub="cliques no site (API)", delta=dc, delta_color=cc),
        card("Seguidores novos", _n(p["seg_novos"]), sub="últimos dias ainda incompletos na API" if p["seg_novos"] is None else "ganhos na semana (API fecha com 3 dias de atraso)", delta=dsn, delta_color=csn),
        card("Pedidos do Instagram", _n(p["pedidos"]), sub=f"{brl(p['receita'], 0)} · {int(p['pedidos_pago'])} de anúncio", ref="piso: só pedidos com UTM", delta=dp, delta_color=cp),
        card("Gasto no Meta", brl(p["gasto"], 0) if p["gasto"] is not None else "—", sub=f"margem do Instagram após Meta: {brl(p['lucro'] - (p['gasto'] or 0), 0)}",
             ref="margem antes de mídia: " + brl(p["lucro"], 0), delta=dg, delta_color=COLORS["text_muted"]),
    ])

    section_title("Tendência · últimas 12 semanas")
    sem_ini = sem.segunda(fechado) - pd.Timedelta(days=7 * (SEMANAS_TENDENCIA - 1))
    t = conta[conta["dt_data"] >= sem_ini].copy()
    t["semana"] = t["dt_data"].apply(sem.segunda)
    g = t.groupby("semana").agg(
        views=("qt_views", "sum"), organicas=("qt_views_organicas", "sum"), anuncio=("qt_views_anuncio", "sum"),
        tem_quebra=("fg_tem_quebra", "all"), visitas=("qt_visitas_perfil", "sum"), pedidos=("qt_pedidos_instagram", "sum"),
        pedidos_pago=("qt_pedidos_instagram_pago", "sum"), gasto=("vl_gasto_meta", "sum")).reset_index()
    g["rot"] = g["semana"].apply(lambda s: s.strftime("%d/%m"))
    c1, c2 = st.columns(2)
    with c1:
        fig = go.Figure()
        q = g[g["tem_quebra"]]
        fig.add_bar(x=q["rot"], y=q["organicas"], name="Orgânicas", marker_color=COLORS["primary"])
        fig.add_bar(x=q["rot"], y=q["anuncio"], name="De anúncio", marker_color=COLORS["accent"])
        sq = g[~g["tem_quebra"]]
        if not sq.empty:
            fig.add_bar(x=sq["rot"], y=sq["views"], name="Sem quebra", marker_color=COLORS["text_muted"])
        plotly_layout(fig, legend=dict(orientation="h", y=-0.22, x=0), margin=dict(l=10, r=10, t=40, b=10), barmode="stack", height=320, title=dict(text="Views por semana", font=dict(size=14)), xaxis=dict(type="category"))
        st.plotly_chart(fig, width="stretch")
    with c2:
        fig = go.Figure()
        fig.add_bar(x=g["rot"], y=g["visitas"], name="Visitas ao perfil", marker_color=METRIC_COLORS["pedidos"])
        fig.add_scatter(x=g["rot"], y=g["pedidos"], name="Pedidos do Instagram", mode="lines+markers", yaxis="y2", line=dict(color=COLORS["accent"], width=2))
        plotly_layout(fig, legend=dict(orientation="h", y=-0.22, x=0), margin=dict(l=10, r=10, t=40, b=10), height=320, title=dict(text="Visitas ao perfil e pedidos", font=dict(size=14)), xaxis=dict(type="category"),
                      yaxis2=dict(overlaying="y", side="right", showgrid=False, rangemode="tozero"))
        st.plotly_chart(fig, width="stretch")

    section_title("Dia a dia da semana selecionada")
    dia = atual.copy()
    dia["rot"] = dia["dt_data"].dt.strftime("%d/%m") + " " + dia["dt_data"].dt.dayofweek.map(dict(enumerate(DIAS_SEMANA)))
    fig = go.Figure()
    fig.add_bar(x=dia["rot"], y=dia["qt_views_organicas"].fillna(dia["qt_views"]), name="Views orgânicas", marker_color=COLORS["primary"])
    fig.add_bar(x=dia["rot"], y=dia["qt_views_anuncio"].fillna(0), name="Views de anúncio", marker_color=COLORS["accent"])
    plotly_layout(fig, legend=dict(orientation="h", y=-0.22, x=0), margin=dict(l=10, r=10, t=40, b=10), barmode="stack", height=290, xaxis=dict(type="category"))
    st.plotly_chart(fig, width="stretch")
    note(f"<strong>Como ler:</strong> a semana vai de segunda a domingo, cortada no último dia fechado e comparada aos <em>mesmos dias</em> da anterior ({rot}). "
         "Views de anúncio já estão fora das <em>orgânicas</em>. <strong>Pedidos do Instagram é um piso</strong>: só entram pedidos com origem Instagram na UTM; "
         "a maior parte das vendas chega sem parâmetro. Com ~1 pedido por dia a semana oscila: leia 4+ semanas e não conclua causa (views altos e pedido no mesmo dia "
         "não provam que um causou o outro). O dia da métrica segue o fuso da Meta e os 1–2 últimos dias podem estar incompletos.")


# ── Aba 2 · Peças ──────────────────────────────────────────────────────────────────────────────────────────────
def _aba_pecas(pecas):
    f1, f2, f3, f4 = st.columns([1.2, 1.6, 1.2, 1])
    periodo = f1.selectbox("Publicadas", list(PERIODOS_PECAS), index=1, key="ig_per")
    formatos = f2.multiselect("Formato", sorted(pecas["ds_formato"].unique()), default=sorted(pecas["ds_formato"].unique()),
                              format_func=lambda x: FORMATO_ROTULO.get(x, x), key="ig_fmt")
    visao = f3.radio("Visão", ["Todas", "Só orgânicas", "Só impulsionadas"], key="ig_visao")
    minimo = f4.selectbox("Alcance mínimo", [0, 500, 1000, 2000], key="ig_min")
    d = pecas[pecas["ds_formato"].isin(formatos)]
    if PERIODOS_PECAS[periodo]:
        d = d[d["dt_publicacao"] >= pd.Timestamp(_hoje_brt()) - pd.Timedelta(days=PERIODOS_PECAS[periodo])]
    if visao == "Só orgânicas":
        d = d[~d["fg_impulsionada"]]
    elif visao == "Só impulsionadas":
        d = d[d["fg_impulsionada"]]
    d = d[d["qt_alcance"].fillna(0) >= minimo]
    if d.empty:
        st.info("Nenhuma peça com esses filtros.")
        return

    seg_mil = (d["qt_seguidores_ganhos"].sum() * 1000 / d["qt_views"].sum()) if d["qt_views"].sum() else None
    render_cards([
        card("Peças", _n(len(d)), sub=f"{int(d['fg_impulsionada'].sum())} impulsionadas"),
        card("Views (mediana)", _n(d["qt_views"].median()), sub=f"alcance mediano {_n(d['qt_alcance'].median())}"),
        card("Taxa de envio (mediana)", pct(d["pct_envio"].median(), 2), sub="envios por DM ÷ alcance", ref="o sinal mais forte de alcance novo"),
        card("Taxa de salvamento (mediana)", pct(d["pct_salvamento"].median(), 2), sub="salvamentos ÷ alcance"),
        card("Seguidores por mil views", f"{seg_mil:.2f}".replace(".", ",") if seg_mil is not None else "—", sub="soma ÷ soma das peças filtradas"),
    ])

    section_title("Mix por formato")
    mix = d.groupby("ds_formato").agg(
        pecas=("cd_midia", "count"), views=("qt_views", "median"), alcance=("qt_alcance", "median"), envio=("pct_envio", "median"),
        salvamento=("pct_salvamento", "median"), seg=("qt_seguidores_ganhos", "sum"), vw=("qt_views", "sum"),
        imp=("fg_impulsionada", "sum"), gasto=("vl_gasto_anuncio", "sum")).reset_index()
    mix["seg_mil"] = np.where(mix["vw"] > 0, mix["seg"] * 1000 / mix["vw"], np.nan)
    mix["Formato"] = mix["ds_formato"].map(lambda x: FORMATO_ROTULO.get(x, x))
    st.dataframe(mix.sort_values("pecas", ascending=False)[["Formato", "pecas", "views", "alcance", "envio", "salvamento", "seg_mil", "imp", "gasto"]],
                 hide_index=True, width="stretch", column_config={
                     "pecas": st.column_config.NumberColumn("Peças", format="%d"), "views": st.column_config.NumberColumn("Views (mediana)", format="%d"),
                     "alcance": st.column_config.NumberColumn("Alcance (mediano)", format="%d"),
                     "envio": st.column_config.NumberColumn("Envio (mediana)", format="percent"),
                     "salvamento": st.column_config.NumberColumn("Salvamento (mediana)", format="percent"),
                     "seg_mil": st.column_config.NumberColumn("Seguidores / mil views", format="%.2f"),
                     "imp": st.column_config.NumberColumn("Impulsionadas", format="%d"),
                     "gasto": st.column_config.NumberColumn("Gasto em anúncio", format="R$ %.0f")})
    note("Compare uma peça com a mediana do <strong>seu formato</strong>, não com o recorde. Peça impulsionada tem views e alcance <strong>com o pago</strong>: "
         "use o filtro <em>Só orgânicas</em> para ver o que o conteúdo faz sozinho.")

    section_title("Peças")
    ordem = st.selectbox("Ordenar por", list(ORDENAR), key="ig_ordem")
    tab = d.sort_values(ORDENAR[ordem], ascending=False, na_position="last").copy()
    tab["Formato"] = tab["ds_formato"].map(lambda x: FORMATO_ROTULO.get(x, x))
    tab["Legenda"] = tab["ds_legenda"].fillna("").str.slice(0, 70)
    st.dataframe(tab[["dt_publicacao", "Formato", "Legenda", "qt_views", "qt_alcance", "pct_envio", "pct_salvamento", "pct_interacao",
                      "qt_seguidores_ganhos", "pct_rank_envio_formato", "fg_impulsionada", "vl_gasto_anuncio", "ds_permalink"]],
                 hide_index=True, width="stretch", height=420, column_config={
                     "dt_publicacao": st.column_config.DateColumn("Data", format="DD/MM/YY"),
                     "qt_views": st.column_config.NumberColumn("Views", format="%d"), "qt_alcance": st.column_config.NumberColumn("Alcance", format="%d"),
                     "pct_envio": st.column_config.NumberColumn("Envio", format="percent"), "pct_salvamento": st.column_config.NumberColumn("Salvam.", format="percent"),
                     "pct_interacao": st.column_config.NumberColumn("Interação", format="percent"),
                     "qt_seguidores_ganhos": st.column_config.NumberColumn("Seg. ganhos", format="%d"),
                     "pct_rank_envio_formato": st.column_config.ProgressColumn("Percentil de envio no formato", min_value=0, max_value=1, format="percent"),
                     "fg_impulsionada": st.column_config.CheckboxColumn("Impulsionada"),
                     "vl_gasto_anuncio": st.column_config.NumberColumn("Gasto", format="R$ %.0f"),
                     "ds_permalink": st.column_config.LinkColumn("Link", display_text="abrir")})

    section_title("Alcance × taxa de envio")
    fig = go.Figure()
    for i, (fmt, g) in enumerate(d[d["qt_alcance"] > 0].groupby("ds_formato")):
        fig.add_scatter(x=g["qt_alcance"], y=g["pct_envio"], mode="markers", name=FORMATO_ROTULO.get(fmt, fmt),
                        marker=dict(size=9, color=CATEGORICAL[i % len(CATEGORICAL)], opacity=0.8, symbol=np.where(g["fg_impulsionada"], "diamond", "circle").tolist()),
                        text=g["ds_legenda"].fillna("").str.slice(0, 60), hovertemplate="%{text}<br>alcance %{x:,.0f} · envio %{y:.2%}<extra></extra>")
    plotly_layout(fig, height=320, xaxis=dict(title="Alcance (contas)"), yaxis=dict(title="Taxa de envio", tickformat=".1%"))
    st.plotly_chart(fig, width="stretch")
    note("Losango = peça impulsionada (alcance inclui o pago). O que importa é estar <strong>no alto</strong> (muito envio por alcance), independente de estar à direita.")

    section_title("Uma peça em detalhe")
    recentes = d.sort_values("ts_publicacao", ascending=False).head(80)
    rotulos = {r.cd_midia: f"{r.dt_publicacao:%d/%m/%y} · {FORMATO_ROTULO.get(r.ds_formato, r.ds_formato)} · {(r.ds_legenda or '')[:55]}" for r in recentes.itertuples()}
    escolha = st.selectbox("Peça", list(rotulos), format_func=lambda k: rotulos[k], key="ig_peca")
    r = recentes[recentes["cd_midia"] == escolha].iloc[0]
    render_cards([
        card("Views", _n(r["qt_views"]), sub=f"alcance {_n(r['qt_alcance'])}"),
        card("Taxa de envio", pct(r["pct_envio"], 2), sub=f"percentil {pct(r['pct_rank_envio_formato'], 0)} no formato"),
        card("Taxa de salvamento", pct(r["pct_salvamento"], 2), sub=f"percentil {pct(r['pct_rank_salvamento_formato'], 0)} no formato"),
        card("Seguidores ganhos", _n(r["qt_seguidores_ganhos"]), sub=f"{_n(r['qt_visitas_perfil'])} visitas ao perfil"),
        card("Cliques no link da bio", _n(r["qt_cliques_link_bio"]), sub="a partir desta peça"),
    ])
    if r["fg_impulsionada"]:
        note(f"Esta peça foi <strong>impulsionada</strong> (gasto de anúncio {brl(r['vl_gasto_anuncio'], 0)}): views e alcance incluem o pago.", variant="warn")
    curva = carregar_curva(escolha)
    if len(curva) >= 2:
        fig = go.Figure()
        fig.add_scatter(x=curva["dt_snapshot"], y=curva["qt_views"], mode="lines+markers", name="Views", line=dict(color=COLORS["primary"]))
        fig.add_scatter(x=curva["dt_snapshot"], y=curva["qt_alcance"], mode="lines+markers", name="Alcance", line=dict(color=COLORS["accent"]))
        plotly_layout(fig, height=260)
        st.plotly_chart(fig, width="stretch")
    else:
        st.caption("Ainda há só 1 snapshot desta peça: a curva de crescimento se forma a partir de agora (1 por dia nas peças com até 30 dias).")
    st.link_button("Abrir no Instagram", r["ds_permalink"])


# ── Aba 3 · Anúncios ───────────────────────────────────────────────────────────────────────────────────────────
def _aba_anuncios(anuncios, dist):
    periodo = st.selectbox("Anúncios com", list(PERIODOS_ANUNCIOS), index=1, key="ig_per_ad")
    d = anuncios
    if PERIODOS_ANUNCIOS[periodo]:
        d = d[d["dt_ultimo_dia"] >= pd.Timestamp(_hoje_brt()) - pd.Timedelta(days=PERIODOS_ANUNCIOS[periodo])]
    if d.empty:
        st.info("Nenhum anúncio com gasto no período.")
        return
    gasto, receita, pedidos = d["vl_gasto"].sum(), d["vl_receita_real"].sum(), d["qt_pedidos_reais"].sum()
    roas_real = receita / gasto if gasto else None
    roas_meta = d["vl_compras_meta"].sum() / gasto if gasto else None
    ligado = d.loc[d["cd_midia_original"].notna(), "vl_gasto"].sum() / gasto if gasto else None
    x = lambda v: f"{v:.2f}".replace(".", ",") + "×" if v is not None else "—"
    render_cards([
        card("Gasto no Meta", brl(gasto, 0), sub=f"{len(d)} anúncios"),
        card("Pedidos reais identificados", _n(pedidos), sub=f"{brl(receita, 0)} de receita", ref="piso: só pedidos com a UTM do anúncio"),
        card("ROAS real (piso)", x(roas_real), sub="receita real ÷ gasto", variant="ok" if roas_real and roas_real >= 1.4 else "warn"),
        card("ROAS segundo o Meta", x(roas_meta), sub="compras atribuídas pelo Meta ÷ gasto", ref="janela 7 dias clique / 1 dia view; não é pedido real"),
        card("Gasto ligado a uma peça", pct(ligado, 0), sub="o resto são anúncios sem a peça original identificada"),
    ])
    note("A diferença entre o <strong>ROAS real</strong> e o do Meta é o dado mais importante desta aba: o Meta atribui compras que a UTM do pedido não captura. "
         "O real é um <strong>piso</strong> (pedidos sem parâmetro não entram), então a verdade está entre os dois. Ponto de equilíbrio de referência: ROAS ≈ 1,4 (margem bruta ~72%). "
         "Decisão de verba é do Hugo; amostra pequena (poucos pedidos por anúncio) não é tendência.", variant="warn")

    section_title("Anúncios por gasto")
    t = d.sort_values("vl_gasto", ascending=False).head(40).copy()
    t["Anúncio"] = t["nm_anuncio"].fillna("(anúncio excluído)").str.slice(0, 55)
    st.dataframe(t[["Anúncio", "dt_publicacao_midia_original", "vl_gasto", "qt_dias_com_gasto", "qt_visualizacao_pagina", "qt_carrinho", "qt_pedidos_reais",
                    "vl_receita_real", "vl_roas_real", "vl_roas_meta", "ds_status_efetivo", "ds_permalink_midia_original"]],
                 hide_index=True, width="stretch", height=420, column_config={
                     "dt_publicacao_midia_original": st.column_config.DateColumn("Peça publicada", format="DD/MM/YY"),
                     "vl_gasto": st.column_config.NumberColumn("Gasto", format="R$ %.0f"), "qt_dias_com_gasto": st.column_config.NumberColumn("Dias", format="%d"),
                     "qt_visualizacao_pagina": st.column_config.NumberColumn("Visualizações de página", format="%d"),
                     "qt_carrinho": st.column_config.NumberColumn("Carrinhos (Meta)", format="%d"),
                     "qt_pedidos_reais": st.column_config.NumberColumn("Pedidos reais", format="%d"),
                     "vl_receita_real": st.column_config.NumberColumn("Receita real", format="R$ %.0f"),
                     "vl_roas_real": st.column_config.NumberColumn("ROAS real", format="%.2f"), "vl_roas_meta": st.column_config.NumberColumn("ROAS Meta", format="%.2f"),
                     "ds_status_efetivo": "Status", "ds_permalink_midia_original": st.column_config.LinkColumn("Peça original", display_text="abrir")})

    section_title("Para onde o dinheiro foi · últimos 90 dias")
    c1, c2 = st.columns(2)
    pos = dist[dist["ds_tipo"] == "posicao"].copy()
    pub = dist[dist["ds_tipo"] == "publico"].copy()
    with c1:
        if not pos.empty:
            pos["rot"] = pos["ds_chave1"].fillna("") + " · " + pos["ds_chave2"].fillna("")
            pos = pos.groupby("rot", as_index=False)["vl_gasto"].sum().sort_values("vl_gasto").tail(10)
            fig = go.Figure(go.Bar(x=pos["vl_gasto"], y=pos["rot"], orientation="h", marker_color=COLORS["primary"], text=[brl(v, 0) for v in pos["vl_gasto"]], textposition="outside"))
            plotly_layout(fig, height=320, title=dict(text="Por plataforma e posição", font=dict(size=14)), xaxis=dict(visible=False))
            st.plotly_chart(fig, width="stretch")
    with c2:
        if not pub.empty:
            fig = go.Figure()
            for i, (gen, g) in enumerate(pub.groupby("ds_chave2")):
                g = g.groupby("ds_chave1", as_index=False)["vl_gasto"].sum()
                fig.add_bar(x=g["ds_chave1"], y=g["vl_gasto"], name={"male": "Homens", "female": "Mulheres", "unknown": "Não informado"}.get(gen, gen), marker_color=CATEGORICAL[i % len(CATEGORICAL)])
            plotly_layout(fig, legend=dict(orientation="h", y=-0.22, x=0), margin=dict(l=10, r=10, t=40, b=10), barmode="stack", height=340, title=dict(text="Por faixa etária e gênero", font=dict(size=14)), xaxis=dict(type="category"))
            st.plotly_chart(fig, width="stretch")
    note("As duas visões são do <em>mesmo</em> gasto (não somam entre si). Gasto desde 23/10/2023 (limite de 37 meses da API); o acumulado da conta está no dbt.")


# ── Aba 4 · Audiência e atendimento ────────────────────────────────────────────────────────────────────────────
def _aba_audiencia(aud):
    pub, hor, conc = aud["publico"], aud["horario"], aud["conc"]
    section_title("Quem segue a conta")
    c1, c2 = st.columns([3, 2])
    idade = pub[pub["ds_quebra"] == "age"].sort_values("ds_valor_dimensao")
    gen = pub[pub["ds_quebra"] == "gender"]
    with c1:
        if not idade.empty:
            fig = go.Figure(go.Bar(x=idade["ds_valor_dimensao"], y=idade["pct_do_total"], marker_color=COLORS["primary"],
                                   text=[pct(v, 0) for v in idade["pct_do_total"]], textposition="outside"))
            plotly_layout(fig, height=280, title=dict(text="Faixa etária dos seguidores", font=dict(size=14)), yaxis=dict(visible=False), xaxis=dict(type="category"))
            st.plotly_chart(fig, width="stretch")
    with c2:
        if not gen.empty:
            nomes = {"F": "Mulheres", "M": "Homens", "U": "Não informado"}
            fig = go.Figure(go.Pie(labels=[nomes.get(x, x) for x in gen["ds_valor_dimensao"]], values=gen["qt_valor"], hole=0.55, marker=dict(colors=CATEGORICAL)))
            plotly_layout(fig, height=280, title=dict(text="Gênero", font=dict(size=14)))
            st.plotly_chart(fig, width="stretch")
    note("Demografia da própria Meta, só de quem informou idade e gênero. Foto do dia: não há histórico de variação ainda.")

    section_title("Quando os seguidores estão online")
    if not hor.empty:
        hor = hor.dropna(subset=["nr_dia_semana", "nr_hora"]).astype({"nr_dia_semana": int, "nr_hora": int})
        piv = hor.pivot_table(index="nr_dia_semana", columns="nr_hora", values="vl_media_seguidores_online", aggfunc="mean")
        piv = piv.reindex(index=[2, 3, 4, 5, 6, 7, 1], columns=range(24))
        amostra = int(hor["qt_dias_amostra"].max())
        fig = go.Figure(go.Heatmap(z=piv.values, x=[f"{h}h" for h in range(24)], y=DIAS_SEMANA, colorscale=[[0, "#EAF1FB"], [1, "#1B3558"]],
                                   hovertemplate="%{y} %{x}: %{z:,.0f} seguidores online<extra></extra>", showscale=False))
        plotly_layout(fig, height=260)
        st.plotly_chart(fig, width="stretch")
        note(f"Média de seguidores online por hora. <strong>Amostra: {amostra} dia(s)</strong> — o histórico começou em 04/10/2026, então só uma linha do mapa tem dado por enquanto; "
             "o desenho completo se forma ao longo das próximas semanas. Horário como a Meta devolve (fuso a verificar).", variant="warn" if amostra < 14 else "")

    section_title("Concorrentes e referências")
    if not conc.empty:
        c = conc.sort_values("qt_seguidores", ascending=False).copy()
        c["Perfil"] = np.where(c["fg_propria"], "★ @" + c["nm_usuario"] + " (nós)", "@" + c["nm_usuario"])
        c["Posts 30d"] = c["qt_posts_30d"].fillna(0).astype(int).astype(str) + np.where(c["fg_amostra_truncada"].fillna(False), "+", "")
        st.dataframe(c[["Perfil", "qt_seguidores", "qt_seguidores_delta_7d", "qt_seguidores_delta_30d", "Posts 30d", "vl_media_curtidas_30d",
                        "vl_media_comentarios_30d", "pct_interacao_media", "pct_carrossel_30d", "pct_video_30d"]],
                     hide_index=True, width="stretch", column_config={
                         "qt_seguidores": st.column_config.NumberColumn("Seguidores", format="%d"),
                         "qt_seguidores_delta_7d": st.column_config.NumberColumn("Δ 7 dias", format="%+d"),
                         "qt_seguidores_delta_30d": st.column_config.NumberColumn("Δ 30 dias", format="%+d"),
                         "vl_media_curtidas_30d": st.column_config.NumberColumn("Curtidas (média)", format="%.0f"),
                         "vl_media_comentarios_30d": st.column_config.NumberColumn("Comentários (média)", format="%.1f"),
                         "pct_interacao_media": st.column_config.NumberColumn("Interação média", format="percent"),
                         "pct_carrossel_30d": st.column_config.NumberColumn("% carrossel", format="percent"),
                         "pct_video_30d": st.column_config.NumberColumn("% vídeo", format="percent")})
        note("Só dado <strong>público</strong> de contas profissionais, sobre as ~25 últimas peças de cada perfil (<strong>+</strong> = a amostra pode ter cortado posts dos 30 dias). "
             "Não há alcance, salvamento nem visualização do concorrente. Variação de seguidores começa a aparecer com o histórico (desde 07/10/2026). "
             "Use para ver <em>padrão</em> (formato, frequência, ângulo), nunca para copiar peça: originalidade é regra de recomendação do Instagram.")

    section_title("Perguntas sem resposta da loja")
    sr = aud["sem_resposta"]
    render_cards([card("Comentários de terceiros sem resposta", _n(sr["total"]), sub="histórico todo, 1º nível", variant="warn" if sr["total"] else "ok"),
                  card("Dessas, perguntas", _n(sr["perguntas"]), sub="texto com \"?\" (heurística)")])
    perg = aud["perguntas"]
    if perg.empty:
        st.success("Nenhuma pergunta sem resposta nos últimos 60 dias.")
    else:
        perg = perg.copy()
        perg["Comentário"] = perg["ds_texto"].fillna("").str.slice(0, 110)
        perg["Formato"] = perg["ds_formato"].map(lambda x: FORMATO_ROTULO.get(x, x))
        st.dataframe(perg[["dt_comentario", "Formato", "Comentário", "nm_usuario", "ds_permalink_midia"]], hide_index=True, width="stretch", height=300,
                     column_config={"dt_comentario": st.column_config.DateColumn("Data", format="DD/MM/YY"), "nm_usuario": "Usuário",
                                    "ds_permalink_midia": st.column_config.LinkColumn("Post", display_text="abrir")})
    note("Lista só para triagem: <strong>a resposta é sempre humana</strong> (Robson/Hugo), pelo Instagram. Comentário e usuário são dado pessoal de terceiros: uso interno. "
         "\"Pergunta\" é uma heurística simples (texto com ?), pode haver comentário relevante sem ela e emoji solto contado como pendência.")

    section_title("Posts de terceiros que marcaram a conta")
    marc = aud["marcacoes"]
    if not marc.empty:
        st.dataframe(marc, hide_index=True, width="stretch", height=260, column_config={
            "dt_publicacao": st.column_config.DateColumn("Data", format="DD/MM/YY"), "nm_usuario": "Perfil", "ds_tipo_midia": "Tipo",
            "qt_curtidas": st.column_config.NumberColumn("Curtidas", format="%d"), "qt_comentarios": st.column_config.NumberColumn("Comentários", format="%d"),
            "ds_permalink": st.column_config.LinkColumn("Post", display_text="abrir")})
        note("Candidatos a depoimento ou parceria. <strong>Reaproveitar peça de terceiro exige permissão do autor</strong> (e repost sem valor agregado é penalizado nas recomendações).")


# ── Página ─────────────────────────────────────────────────────────────────────────────────────────────────────
def render():
    inject_css()
    try:
        fr = carregar_frescor(TABELAS, EXTRATORES)
    except Exception:
        fr = None
    selo = badge_atualizacao(fr) if fr else ""
    st.html(f"""
    <div class="report-header"><div>
      <div class="report-brand">shibari brasil · camada semanal</div>
      <div class="report-title">Insta<span>gram</span></div>
      <div class="report-meta">Audiência, peças, anúncios e atendimento do @shibari.brasil · dados da Graph API e da Marketing API, 3x por dia</div>
    </div>{selo}</div>""")
    if fr:
        alerta_atraso(fr)
    with st.spinner("Carregando dados do BigQuery..."):
        try:
            conta, pecas, anuncios = carregar_conta(), carregar_pecas(), carregar_anuncios()
        except Exception as e:
            st.error(f"Erro ao carregar dados do BigQuery: {e}")
            return
    if conta.empty:
        st.info("Sem dados do Instagram ainda.")
        return
    t_semana, t_pecas, t_anuncios, t_aud = st.tabs(["Semana", "Peças", "Anúncios", "Audiência e atendimento"])
    with t_semana:
        _aba_semana(conta)
    with t_pecas:
        _aba_pecas(pecas)
    with t_anuncios:
        _aba_anuncios(anuncios, carregar_distribuicao())
    with t_aud:
        try:
            _aba_audiencia(carregar_audiencia())
        except Exception as e:
            st.error(f"Erro ao carregar audiência: {e}")
    if fr:
        detalhe_atualizacao(fr)
