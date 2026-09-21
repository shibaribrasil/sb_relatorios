"""Relatório Vendas & Margem — Shibari Brasil (camada mensal).

Regras de negócio e definição de cada indicador: ver specs/vendas-margem.md.
Não altere cálculo/filtro sem antes ler (e, se preciso, atualizar) esse spec.

Toda regra vive em `dbt_dw_az.tb_pedido` (projeto sb_dw_dbt). Aqui só se filtra,
soma e apresenta: margem em % é sempre soma(margem) ÷ soma(receita líquida de
produtos), nunca média de percentuais.

A única margem exibida é a MARGEM DE CONTRIBUIÇÃO (antes de mídia) =
receita líquida de produtos − CMV + resultado de frete − taxas de pagamento
− reembolsos − embalagem − imposto. Toda tela diz "margem de contribuição".
"""
import os
import textwrap
from datetime import datetime, timedelta, timezone

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from common import bigquery as bq
from common.design import (
    COLORS, METRIC_COLORS, CATEGORICAL, inject_css, card, render_cards,
    section_title, note, plotly_layout, kpi_delta_color, brl, pct,
)

# Dev: apontar para uma cópia de validação (ex.: SB_DATASET_PEDIDO=dbt_dw_scratch
# SB_TABELA_PEDIDO=tb_pedido_fase2). Em produção usa a tabela oficial.
DATASET = os.environ.get("SB_DATASET_PEDIDO", "dbt_dw_az")
TABELA = os.environ.get("SB_TABELA_PEDIDO", "tb_pedido")

INICIO_HISTORICO = "2025-08-01"  # taxa real e frete real da Nuvemshop só são confiáveis desde ago/2025
BRT = timezone(timedelta(hours=-3))  # sem horário de verão desde 2019

# Semáforo da margem de contribuição (antes de mídia) — ver specs/vendas-margem.md
MARGEM_OK = 0.50     # >= 50%: verde
MARGEM_MIN = 0.40    # >= 40% (mínimo institucional): âmbar; abaixo: vermelho

COLUNAS = """
    cd_codigo_interno, cd_pedido, cd_pedido_nuvemshop, cd_contato, nm_contato, dt_pedido, dt_reembolso,
    fg_cliente_recorrente, dt_proxima_compra_cliente, ds_status_pedido, nm_produto,
    ds_categoria, ds_meio_pagamento_nuvemshop, qt_item, fg_brinde, ds_incompletude,
    vl_receita_bruta_produto, vl_desconto_venda_rateio, vl_receita_liquida_produto, vl_liquido_item,
    vl_frete_pago_rateio, vl_frete_real_rateio, vl_resultado_frete, vl_custo_linha,
    vl_taxa_pedido_rateio, vl_reembolso_rateio, vl_embalagem_rateio, vl_imposto_rateio,
    vl_margem_contribuicao, ts_load
"""

SOMAVEIS = [
    "qt_item", "vl_receita_bruta_produto", "vl_desconto_venda_rateio", "vl_receita_liquida_produto",
    "vl_liquido_item", "vl_frete_pago_rateio", "vl_frete_real_rateio", "vl_resultado_frete",
    "vl_custo_linha", "vl_taxa_pedido_rateio", "vl_reembolso_rateio", "vl_embalagem_rateio",
    "vl_imposto_rateio", "vl_margem_contribuicao",
]

TIPO_CANCELAMENTO = {
    "pending": "sem pagamento",
    "voided": "anulados",
    "refunded": "estornados",
    "paid": "cancelados após pago",
}


@st.cache_data(ttl=900)
def carregar_dados():
    client = bq.get_client()
    base = f"`{bq.PROJECT}.{DATASET}.{TABELA}`"
    vendas = bq.query_df(client, f"""
        SELECT {COLUNAS} FROM {base}
         WHERE fg_pedido_valido AND dt_pedido >= DATE '{INICIO_HISTORICO}'
    """)
    cancel = bq.query_df(client, f"""
        SELECT cd_codigo_interno, ANY_VALUE(dt_pedido) AS dt_pedido, ANY_VALUE(ds_status_pagamento) AS ds_status_pagamento
          FROM {base}
         WHERE ds_status_pedido = 'CANCELADO' AND NOT fg_pedido_valido AND dt_pedido >= DATE '{INICIO_HISTORICO}'
         GROUP BY cd_codigo_interno
    """)
    origem = bq.query_df(client, f"""
        SELECT p.cd_codigo_interno,
               COALESCE(a.ds_origem_venda, '(sem parametro)') AS origem,
               COALESCE(a.ds_midia_venda, '(sem parametro)') AS midia
          FROM (SELECT DISTINCT cd_codigo_interno, cd_pedido FROM {base}
                 WHERE fg_pedido_valido AND dt_pedido >= DATE '{INICIO_HISTORICO}') AS p
     LEFT JOIN (SELECT cd_pedido, ANY_VALUE(ds_origem_venda) AS ds_origem_venda, ANY_VALUE(ds_midia_venda) AS ds_midia_venda
                  FROM `{bq.PROJECT}.dbt_dw_az.tb_atribuicao_pedido` GROUP BY cd_pedido) AS a USING (cd_pedido)
    """)
    metas = bq.query_df(client, f"""
        SELECT dt_prim_dia_mes, dt_data, vl_meta_dia_acumulado, vl_objetivo_total
          FROM `{bq.PROJECT}.dbt_dw_az.tb_objetivo_faturamento`
         WHERE dt_prim_dia_mes >= DATE '{INICIO_HISTORICO}'
    """)
    ads = bq.query_df(client, f"""
        SELECT dt_data, SUM(vl_custo) AS vl_custo, SUM(qt_cliques) AS qt_cliques
          FROM `{bq.PROJECT}.dbt_dw_us_az.tb_gads_conta_diario`
         WHERE dt_data >= DATE '{INICIO_HISTORICO}' GROUP BY dt_data
    """)
    # série de clientes: histórico completo do DW (nov/2023 em diante), 1 linha por pedido válido, só contagens
    hist = bq.query_df(client, f"""
        SELECT cd_codigo_interno, ANY_VALUE(dt_pedido) AS dt_pedido, ANY_VALUE(cd_contato) AS cd_contato,
               ANY_VALUE(fg_cliente_recorrente) AS fg_cliente_recorrente,
               ANY_VALUE(dt_proxima_compra_cliente) AS dt_proxima_compra_cliente
          FROM {base} WHERE fg_pedido_valido GROUP BY cd_codigo_interno
    """)
    hist["dt_pedido"] = pd.to_datetime(hist["dt_pedido"])
    hist["mes"] = hist["dt_pedido"].dt.to_period("M").dt.to_timestamp()
    hist["fg_cliente_recorrente"] = hist["fg_cliente_recorrente"].fillna(False).astype(bool)
    hist["dt_proxima_compra_cliente"] = pd.to_datetime(hist["dt_proxima_compra_cliente"])
    vendas = vendas.merge(origem, on="cd_codigo_interno", how="left")
    vendas[["origem", "midia"]] = vendas[["origem", "midia"]].fillna("(sem parametro)")
    vendas["dt_pedido"] = pd.to_datetime(vendas["dt_pedido"])
    vendas["mes"] = vendas["dt_pedido"].dt.to_period("M").dt.to_timestamp()
    vendas["ds_categoria"] = vendas["ds_categoria"].fillna("Sem categoria")
    for c in SOMAVEIS:
        vendas[c] = pd.to_numeric(vendas[c]).fillna(0.0)
    # reembolso no mês em que aconteceu (data aproximada: última atualização do pedido na Nuvemshop).
    # A margem "pré-reembolso" é a da linha sem o reembolso do pedido; o reembolso volta pela data dele.
    vendas["dt_reembolso"] = pd.to_datetime(vendas["dt_reembolso"]).fillna(vendas["dt_pedido"])
    vendas["vl_margem_pre_reembolso"] = vendas["vl_margem_contribuicao"] + vendas["vl_reembolso_rateio"]
    refs = vendas.loc[vendas["vl_reembolso_rateio"] != 0, ["dt_reembolso", "vl_reembolso_rateio"]].copy()
    refs["mes"] = refs["dt_reembolso"].dt.to_period("M").dt.to_timestamp()
    vendas["fg_cliente_recorrente"] = vendas["fg_cliente_recorrente"].fillna(False).astype(bool)
    vendas["dt_proxima_compra_cliente"] = pd.to_datetime(vendas["dt_proxima_compra_cliente"])
    cancel["dt_pedido"] = pd.to_datetime(cancel["dt_pedido"])
    cancel["mes"] = cancel["dt_pedido"].dt.to_period("M").dt.to_timestamp()
    metas["dt_data"] = pd.to_datetime(metas["dt_data"])
    metas["mes"] = pd.to_datetime(metas["dt_prim_dia_mes"])
    ads["dt_data"] = pd.to_datetime(ads["dt_data"])
    ads["mes"] = ads["dt_data"].dt.to_period("M").dt.to_timestamp()
    ads["vl_custo"] = pd.to_numeric(ads["vl_custo"]).fillna(0.0)
    return {"vendas": vendas, "cancel": cancel, "metas": metas, "ads": ads, "refs": refs, "hist": hist}


def _hoje_brt():
    return datetime.now(BRT).date()


def _fmt_mes(ts):
    return pd.Timestamp(ts).strftime("%m/%Y")


def _somas(df, refs=None):
    """Somas do período. Com `refs`, o reembolso vem pelo mês em que aconteceu (não pelo do pedido)
    e a margem de contribuição do período é a pré-reembolso das vendas − esses reembolsos."""
    s = {c: float(df[c].sum()) for c in SOMAVEIS}
    if refs is not None:
        s["vl_reembolso_rateio"] = float(refs["vl_reembolso_rateio"].sum())
        s["vl_margem_contribuicao"] = float(df["vl_margem_pre_reembolso"].sum()) - s["vl_reembolso_rateio"]
    s["itens"] = float(df.loc[~df["fg_brinde"], "qt_item"].sum())  # brinde não conta como item vendido
    s["custo_brinde"] = float(df.loc[df["fg_brinde"], "vl_custo_linha"].sum())
    s["pedidos"] = int(df["cd_codigo_interno"].nunique())
    s["linhas"] = len(df)
    s["linhas_incompletas"] = int(df["ds_incompletude"].notna().sum())
    return s


def _razoes(s):
    rec = s["vl_receita_liquida_produto"]
    return {
        "margem_pct": (s["vl_margem_contribuicao"] / rec) if rec else None,
        "cmv_pct": (s["vl_custo_linha"] / rec) if rec else None,
        "ticket": (s["vl_liquido_item"] / s["pedidos"]) if s["pedidos"] else None,
    }


def _periodo_anterior(df, refs, meses_sel, hoje):
    """Só existe comparação quando UM mês está selecionado. Mês corrente (parcial)
    compara com o mesmo intervalo de dias do mês anterior; mês fechado, com o mês
    anterior inteiro. Devolve (df_anterior, reembolsos_anterior, rótulo) ou (None, None, None)."""
    if len(meses_sel) != 1:
        return None, None, None
    mes = pd.Timestamp(meses_sel[0])
    ant = mes - pd.offsets.MonthBegin(1)
    d = df[df["mes"] == ant]
    rf = refs[refs["mes"] == ant]
    if d.empty:
        return None, None, None
    if mes.date() == hoje.replace(day=1):
        d = d[d["dt_pedido"].dt.day <= hoje.day]
        rf = rf[rf["dt_reembolso"].dt.day <= hoje.day]
        return d, rf, f"01–{hoje.day:02d}/{ant.month:02d}"
    return d, rf, _fmt_mes(ant)


def _delta(cur, prev, rotulo, tipo="rel", fmt=None):
    """Variação com sinal explícito + valor anterior + cor. tipo 'rel' = %, 'pp' = pontos percentuais."""
    if cur is None or prev is None or (prev == 0 and tipo == "rel"):
        return "", ""
    if tipo == "pp":
        d = (cur - prev) * 100
        txt = f"{'+' if d >= 0 else '−'}{abs(d):.1f}".replace(".", ",") + " p.p."
    else:
        d = (cur - prev) / abs(prev)
        txt = f"{'+' if d >= 0 else '−'}{abs(d) * 100:.1f}".replace(".", ",") + "%"
    anterior = f" ({fmt(prev)})" if fmt else ""
    return f"{txt} vs. {rotulo}{anterior}", kpi_delta_color(d)


def _variant_margem(m):
    if m is None:
        return "neutral"
    if m >= MARGEM_OK:
        return "ok"
    return "warn" if m >= MARGEM_MIN else "bad"


def _cancelamentos(cancel, meses_sel, n_validos):
    c = cancel[cancel["mes"].isin(meses_sel)]
    total = len(c)
    partes = [f"{(c['ds_status_pagamento'] == k).sum()} {rot}" for k, rot in TIPO_CANCELAMENTO.items() if (c["ds_status_pagamento"] == k).sum()]
    base = n_validos + total
    return {"qtd": total, "pct": (total / base) if base else None, "detalhe": " · ".join(partes)}


def _meta(metas, meses_sel, hoje, faturamento):
    """Meta de faturamento dos meses selecionados. Mês corrente: meta acumulada até hoje;
    mês passado: meta cheia; mês futuro: fora."""
    total, acumulada, sem_meta = 0.0, 0.0, []
    for m in meses_sel:
        m = pd.Timestamp(m)
        mm = metas[metas["mes"] == m]
        if mm.empty:
            sem_meta.append(_fmt_mes(m))
            continue
        obj = float(mm["vl_objetivo_total"].iloc[0])
        total += obj
        if m.date() == hoje.replace(day=1):
            linha = mm[mm["dt_data"].dt.date == hoje]
            acumulada += float(linha["vl_meta_dia_acumulado"].iloc[0]) if not linha.empty else obj
        elif m.date() < hoje.replace(day=1):
            acumulada += obj
    return {
        "total": total, "acumulada": acumulada, "sem_meta": sem_meta,
        "atingimento": (faturamento / acumulada) if acumulada else None,
        "falta": max(total - faturamento, 0.0),
    }


def _grafico_cascata(s, r, custo_ads=0.0):
    passos = [
        ("Receita bruta de produtos", s["vl_receita_bruta_produto"], "absolute"),
        ("− Descontos", -s["vl_desconto_venda_rateio"], "relative"),
        ("= Receita líquida de produtos", None, "total"),
        ("− CMV (custo dos produtos)", -s["vl_custo_linha"], "relative"),
        ("+ Frete pago pelo cliente", s["vl_frete_pago_rateio"], "relative"),
        ("− Frete real (etiqueta)", -s["vl_frete_real_rateio"], "relative"),
        ("− Taxas de pagamento", -s["vl_taxa_pedido_rateio"], "relative"),
        ("− Reembolsos", -s["vl_reembolso_rateio"], "relative"),
        ("− Embalagem (estimada)", -s["vl_embalagem_rateio"], "relative"),
        ("− Imposto", -s["vl_imposto_rateio"], "relative"),
        ("= Margem de contribuição", None, "total"),
        ("− Mídia paga (Google Ads)", -custo_ads, "relative"),
        ("= Margem após mídia", None, "total"),
    ]
    # passos zerados só poluem a cascata (ex.: imposto 0% sem CNPJ, meses sem reembolso)
    passos = [p for p in passos if p[2] == "total" or abs(p[1]) >= 0.005]
    if custo_ads <= 0:  # sem mídia no período: a cascata termina na margem de contribuição
        passos = [p for p in passos if p[0] != "= Margem após mídia"]
    subtotais = {
        "= Receita líquida de produtos": s["vl_receita_liquida_produto"],
        "= Margem de contribuição": s["vl_margem_contribuicao"],
        "= Margem após mídia": s["vl_margem_contribuicao"] - custo_ads,
    }
    nomes = [p[0] for p in passos]
    valores = [subtotais[p[0]] if p[1] is None else p[1] for p in passos]
    fig = go.Figure(go.Waterfall(
        x=nomes, y=valores, measure=[p[2] for p in passos],
        text=[brl(v, 0) for v in valores], textposition="outside", cliponaxis=False,
        connector=dict(line=dict(color=COLORS["border"], width=1)),
        increasing=dict(marker=dict(color=CATEGORICAL[7])),
        decreasing=dict(marker=dict(color=COLORS["text_muted"])),
        totals=dict(marker=dict(color=METRIC_COLORS["receita"])),
        hovertemplate="%{x}<br>%{text}<extra></extra>",
    ))
    plotly_layout(fig, height=440, showlegend=False,
                  yaxis=dict(tickprefix="R$ ", gridcolor=COLORS["grid"]),
                  xaxis=dict(tickangle=-35, automargin=True, dtick=1, tickfont=dict(size=11)))
    return fig


def _grafico_meta_acumulada(df, metas, mes, hoje):
    """Faturamento acumulado dia a dia do mês vs. meta acumulada (linha tracejada, cor de meta)."""
    mes = pd.Timestamp(mes)
    mm = metas[metas["mes"] == mes].sort_values("dt_data")
    if mm.empty:
        return None
    dia = df[df["mes"] == mes].groupby(df["dt_pedido"].dt.date)["vl_liquido_item"].sum()
    datas = [d.date() for d in mm["dt_data"]]
    acum, tot = [], 0.0
    for d in datas:
        tot += float(dia.get(d, 0.0))
        acum.append(tot if d <= hoje else None)
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=datas, y=mm["vl_meta_dia_acumulado"], name="Meta acumulada", mode="lines",
                             line=dict(color=METRIC_COLORS["meta"], width=2, dash="dash"),
                             hovertemplate="%{x|%d/%m}<br>Meta R$ %{y:,.0f}<extra></extra>"))
    fig.add_trace(go.Scatter(x=datas, y=acum, name="Faturamento acumulado", mode="lines",
                             line=dict(color=METRIC_COLORS["receita"], width=3),
                             hovertemplate="%{x|%d/%m}<br>Faturado R$ %{y:,.0f}<extra></extra>"))
    plotly_layout(fig, height=320, hovermode="x unified",
                  xaxis=dict(tickformat="%d/%m", gridcolor=COLORS["grid"]),
                  yaxis=dict(tickprefix="R$ ", gridcolor=COLORS["grid"]))
    return fig


def _grafico_meta_mensal(df, metas):
    fat = df.groupby("mes")["vl_liquido_item"].sum().reset_index()
    obj = metas.groupby("mes")["vl_objetivo_total"].max().reset_index()
    m = fat.merge(obj, on="mes", how="left")
    fig = go.Figure()
    fig.add_bar(x=m["mes"], y=m["vl_liquido_item"], name="Faturamento", marker_color=METRIC_COLORS["receita"],
                hovertemplate="%{x|%m/%Y}<br>Faturado R$ %{y:,.0f}<extra></extra>")
    fig.add_trace(go.Scatter(x=m["mes"], y=m["vl_objetivo_total"], name="Meta do mês", mode="markers",
                             marker=dict(color=METRIC_COLORS["meta"], size=11, symbol="diamond"),
                             hovertemplate="%{x|%m/%Y}<br>Meta R$ %{y:,.0f}<extra></extra>"))
    plotly_layout(fig, height=320, hovermode="x unified",
                  xaxis=dict(tickformat="%m/%Y", dtick="M1", gridcolor=COLORS["grid"]),
                  yaxis=dict(tickprefix="R$ ", gridcolor=COLORS["grid"]))
    return fig


def _grafico_evolucao(df, refs):
    m = df.groupby("mes").agg(rec=("vl_receita_liquida_produto", "sum"), marg_pre=("vl_margem_pre_reembolso", "sum")).reset_index()
    m["marg"] = m["marg_pre"] - m["mes"].map(refs.groupby("mes")["vl_reembolso_rateio"].sum()).fillna(0.0)
    m["pct"] = m["marg"] / m["rec"]
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_bar(x=m["mes"], y=m["rec"], name="Receita líquida de produtos", marker_color=METRIC_COLORS["receita"],
                hovertemplate="%{x|%m/%Y}<br>R$ %{y:,.0f}<extra></extra>", secondary_y=False)
    fig.add_bar(x=m["mes"], y=m["marg"], name="Margem de contribuição (R$)", marker_color=METRIC_COLORS["margem_contribuicao"],
                hovertemplate="%{x|%m/%Y}<br>R$ %{y:,.0f}<extra></extra>", secondary_y=False)
    fig.add_trace(go.Scatter(x=m["mes"], y=m["pct"], name="Margem de contribuição (%)", mode="lines+markers",
                             line=dict(color=METRIC_COLORS["margem_pct"], width=2),
                             hovertemplate="%{x|%m/%Y}<br>%{y:.1%}<extra></extra>"), secondary_y=True)
    plotly_layout(fig, height=320, barmode="group", hovermode="x unified",
                  xaxis=dict(tickformat="%m/%Y", dtick="M1", gridcolor=COLORS["grid"]))
    fig.update_yaxes(tickprefix="R$ ", gridcolor=COLORS["grid"], secondary_y=False)
    fig.update_yaxes(tickformat=".0%", range=[0, 1], showgrid=False, secondary_y=True)
    return fig


def _grafico_categorias(sel, top_n=7):
    g = sel.groupby("ds_categoria").agg(rec=("vl_receita_liquida_produto", "sum"), marg=("vl_margem_contribuicao", "sum")).reset_index()
    g = g.sort_values("marg", ascending=False)
    if len(g) > top_n:
        resto = g.iloc[top_n:]
        g = pd.concat([g.iloc[:top_n], pd.DataFrame([{"ds_categoria": "Outras", "rec": resto["rec"].sum(), "marg": resto["marg"].sum()}])])
    g["pct"] = g["marg"] / g["rec"]
    g = g.iloc[::-1]  # maior no topo
    rotulos = ["<br>".join(textwrap.wrap(c, 15)) or c for c in g["ds_categoria"]]  # quebra o nome: gráfico estreito
    fig = go.Figure(go.Bar(
        y=rotulos, x=g["marg"], orientation="h", marker_color=METRIC_COLORS["margem_contribuicao"],
        text=[pct(p, 0) for p in g["pct"]], textposition="outside", cliponaxis=False,
        customdata=g["pct"], hovertemplate="%{y}<br>Margem de contribuição R$ %{x:,.0f} (%{customdata:.0%})<extra></extra>",
    ))
    plotly_layout(fig, height=max(260, 36 * len(g) + 60), showlegend=False, font=dict(size=11),
                  xaxis=dict(showticklabels=False, showgrid=False, range=[0, max(float(g["marg"].max()), 1.0) * 1.3]),
                  yaxis=dict(automargin=True))
    return fig


RECOMPRA_DIAS = 90
CLIENTES_DESDE = pd.Timestamp("2024-01-01")  # histórico do DW começa em nov/2023: antes disso "novo" é superestimado


def _pedidos_cliente(df):
    """Uma linha por pedido válido, com o que interessa para a análise de recompra."""
    return df.groupby("cd_codigo_interno").agg(
        mes=("mes", "first"), dt=("dt_pedido", "first"), contato=("cd_contato", "first"),
        recorrente=("fg_cliente_recorrente", "first"), proxima=("dt_proxima_compra_cliente", "first"),
        rec=("vl_receita_liquida_produto", "sum"), marg=("vl_margem_contribuicao", "sum"),
    ).reset_index()


def _serie_recorrencia(ped):
    g = ped[ped["mes"] >= CLIENTES_DESDE].groupby("mes").agg(n=("recorrente", "size"), recor=("recorrente", "sum")).reset_index()
    g["novos"] = g["n"] - g["recor"]
    g["pct"] = g["recor"] / g["n"]
    g["pct_6m"] = g["recor"].rolling(6).sum() / g["n"].rolling(6).sum()
    return g


def _serie_coorte(ped, hoje):
    """Coorte = clientes cuja 1ª compra válida foi no mês; recompra = 2ª compra em até 90 dias.
    Só entra cliente com 90 dias completos desde a 1ª compra (senão a taxa sairia artificialmente baixa)."""
    novos = ped[~ped["recorrente"]].copy()
    novos = novos[novos["dt"] + pd.Timedelta(days=RECOMPRA_DIAS) <= pd.Timestamp(hoje)]
    novos["voltou"] = novos["proxima"].notna() & ((novos["proxima"] - novos["dt"]).dt.days <= RECOMPRA_DIAS)
    c = novos[novos["mes"] >= CLIENTES_DESDE].groupby("mes").agg(n=("voltou", "size"), voltaram=("voltou", "sum")).reset_index()
    c["pct"] = c["voltaram"] / c["n"]
    c["pct_6m"] = c["voltaram"].rolling(6).sum() / c["n"].rolling(6).sum()
    return c


def _grafico_recorrencia(g):
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_bar(x=g["mes"], y=g["novos"], name="Pedidos de clientes novos", marker_color=METRIC_COLORS["receita"],
                hovertemplate="%{x|%m/%Y}<br>%{y} pedidos novos<extra></extra>", secondary_y=False)
    fig.add_bar(x=g["mes"], y=g["recor"], name="Pedidos de clientes recorrentes", marker_color=METRIC_COLORS["margem_contribuicao"],
                hovertemplate="%{x|%m/%Y}<br>%{y} pedidos recorrentes<extra></extra>", secondary_y=False)
    fig.add_trace(go.Scatter(x=g["mes"], y=g["pct"], name="% recorrentes (mês)", mode="markers",
                             marker=dict(color=METRIC_COLORS["margem_pct"], size=6),
                             hovertemplate="%{x|%m/%Y}<br>%{y:.1%}<extra></extra>"), secondary_y=True)
    fig.add_trace(go.Scatter(x=g["mes"], y=g["pct_6m"], name="% recorrentes (média 6 meses)", mode="lines",
                             line=dict(color=METRIC_COLORS["margem_pct"], width=3),
                             hovertemplate="%{x|%m/%Y}<br>%{y:.1%} (6 meses)<extra></extra>"), secondary_y=True)
    plotly_layout(fig, height=320, barmode="stack", hovermode="x unified",
                  xaxis=dict(tickformat="%m/%y", gridcolor=COLORS["grid"]))
    fig.update_yaxes(title_text="pedidos", gridcolor=COLORS["grid"], secondary_y=False)
    fig.update_yaxes(tickformat=".0%", rangemode="tozero", showgrid=False, secondary_y=True)
    return fig


def _grafico_coorte(c):
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=c["mes"], y=c["pct"], name="Recompra em 90 dias (coorte do mês)", mode="markers",
                             marker=dict(color=METRIC_COLORS["margem_pct"], size=7), customdata=c[["voltaram", "n"]].to_numpy(),
                             hovertemplate="Coorte %{x|%m/%Y}<br>%{y:.1%} (%{customdata[0]} de %{customdata[1]} clientes)<extra></extra>"))
    fig.add_trace(go.Scatter(x=c["mes"], y=c["pct_6m"], name="Média de 6 coortes", mode="lines",
                             line=dict(color=METRIC_COLORS["margem_contribuicao"], width=3),
                             hovertemplate="Coorte %{x|%m/%Y}<br>%{y:.1%} (6 coortes)<extra></extra>"))
    plotly_layout(fig, height=320, hovermode="x unified", xaxis=dict(tickformat="%m/%y", gridcolor=COLORS["grid"]))
    fig.update_yaxes(tickformat=".0%", rangemode="tozero", gridcolor=COLORS["grid"])
    return fig


def _secao_clientes(df, hist, meses_sel, hoje):
    section_title("Clientes novos e recorrentes")
    ped = _pedidos_cliente(df)
    p = ped[ped["mes"].isin(meses_sel)]
    n = len(p)
    if n == 0:
        return
    rec_p, nov_p = p[p["recorrente"]], p[~p["recorrente"]]
    receita = float(p["rec"].sum())

    def mpct(x):
        r = float(x["rec"].sum())
        return (float(x["marg"].sum()) / r) if r else None

    render_cards([
        card("Clientes no período", f"{p['contato'].nunique()}", "clientes distintos com pedido válido"),
        card("Pedidos de recorrentes", f"{len(rec_p)} · {pct(len(rec_p) / n)}", f"de {n} pedidos · recorrente = já comprou antes"),
        card("Receita líq. de recorrentes", brl(float(rec_p["rec"].sum())),
             f"{pct(float(rec_p['rec'].sum()) / receita) if receita else '—'} da receita líq. de produtos"),
        card("Margem contrib. (%) — novos", pct(mpct(nov_p)), f"{len(nov_p)} pedidos de 1ª compra", variant=_variant_margem(mpct(nov_p))),
        card("Margem contrib. (%) — recorrentes", pct(mpct(rec_p)), f"{len(rec_p)} pedidos de recompra", variant=_variant_margem(mpct(rec_p))),
    ])
    hp = hist.rename(columns={"dt_pedido": "dt", "cd_contato": "contato", "fg_cliente_recorrente": "recorrente", "dt_proxima_compra_cliente": "proxima"})
    g, c = _serie_recorrencia(hp), _serie_coorte(hp, hoje)
    col1, col2 = st.columns(2)
    with col1:
        st.html('<div class="c-label" style="margin:0 0 10px">Pedidos de novos × recorrentes por mês (não segue o filtro)</div>')
        with st.container(border=True):
            st.plotly_chart(_grafico_recorrencia(g), use_container_width=True)
    with col2:
        st.html(f'<div class="c-label" style="margin:0 0 10px">Clientes novos que recompraram em até {RECOMPRA_DIAS} dias, por mês da 1ª compra</div>')
        with st.container(border=True):
            st.plotly_chart(_grafico_coorte(c), use_container_width=True)
    veredito = ""
    if len(c) >= 12:
        atual, anterior = c.tail(6), c.iloc[-12:-6]
        ra = atual["voltaram"].sum() / atual["n"].sum()
        rb = anterior["voltaram"].sum() / anterior["n"].sum()
        veredito = (f" Últimas 6 coortes com 90 dias completos: <strong>{pct(ra)}</strong> recompraram, "
                    f"contra <strong>{pct(rb)}</strong> nas 6 anteriores.")
    note("<strong>Recorrente</strong> = pedido de cliente (contato do Bling) que já tinha uma compra válida anterior. A recompra por coorte só conta clientes com "
         f"{RECOMPRA_DIAS} dias completos desde a 1ª compra, por isso as coortes mais recentes não aparecem." + veredito +
         " O volume é pequeno (dezenas de clientes novos por mês): olhe a <strong>linha de média de 6 meses</strong>, não os pontos isolados. "
         f"Séries a partir de {CLIENTES_DESDE.strftime('%m/%Y')}; o histórico do DW começa em nov/2023, então clientes antigos no início da série aparecem como novos. "
         "A margem dos cards é a do pedido (reembolso no mês do pedido).")


def _juntar_incompletude(serie):
    tipos = set()
    for v in serie.dropna():
        tipos.update(t.strip() for t in v.split("+"))
    return " + ".join(sorted(tipos))


def _detalhe_pedido(sel, cd, linha):
    """Drill: produtos (linhas) de um pedido, com a margem de contribuição de cada um."""
    itens = sel[sel["cd_codigo_interno"] == cd].sort_values("vl_receita_liquida_produto", ascending=False)
    section_title(f"Produtos do pedido {linha['Pedido']} — {linha['Cliente']}")
    det = pd.DataFrame({
        "Produto": itens["nm_produto"].fillna("—") + itens["fg_brinde"].map({True: " (brinde)", False: ""}),
        "Qtd": itens["qt_item"],
        "Receita líq.": itens["vl_receita_liquida_produto"],
        "CMV": itens["vl_custo_linha"],
        "Taxa": itens["vl_taxa_pedido_rateio"],
        "Resultado frete": itens["vl_resultado_frete"],
        "Reembolso": itens["vl_reembolso_rateio"],
        "Embalagem": itens["vl_embalagem_rateio"],
        "Contrib. R$": itens["vl_margem_contribuicao"],
        "Contrib. %": (itens["vl_margem_contribuicao"] / itens["vl_receita_liquida_produto"]).where(itens["vl_receita_liquida_produto"] != 0),
        "Dado faltante": itens["ds_incompletude"].fillna("—"),
    })
    st.dataframe(det, hide_index=True, use_container_width=True,
                 column_config={"Produto": st.column_config.TextColumn(width=180),
                                "Qtd": st.column_config.NumberColumn(width=40),
                                "Receita líq.": st.column_config.NumberColumn(format="R$ %.2f", width=80),
                                "CMV": st.column_config.NumberColumn(format="R$ %.2f", width=68),
                                "Taxa": st.column_config.NumberColumn(format="R$ %.2f", width=60),
                                "Resultado frete": st.column_config.NumberColumn(format="R$ %.2f", width=90),
                                "Reembolso": st.column_config.NumberColumn(format="R$ %.2f", width=72),
                                "Embalagem": st.column_config.NumberColumn(format="R$ %.2f", width=72),
                                "Contrib. R$": st.column_config.NumberColumn(format="R$ %.2f", width=80),
                                "Contrib. %": st.column_config.NumberColumn(format="percent", width=68),
                                "Dado faltante": st.column_config.TextColumn(width=80)})
    note("Frete, taxa, reembolso e embalagem do pedido são rateados entre os produtos (pelo valor de cada um); brindes ficam fora do rateio, mas o custo deles aparece. "
         "A soma das linhas fecha com a linha do pedido na tabela acima.")


def _tabela_pedidos(sel):
    sel = sel.copy()
    sel["codigo"] = sel["cd_pedido_nuvemshop"].where(sel["cd_pedido_nuvemshop"].notna(), "Bling " + sel["cd_pedido"].astype(str))
    g = sel.groupby(["cd_codigo_interno", "codigo", "nm_contato", "dt_pedido", "ds_status_pedido", "ds_meio_pagamento_nuvemshop"], dropna=False, as_index=False).agg(
        itens=("qt_item", "sum"), rec=("vl_receita_liquida_produto", "sum"), custo=("vl_custo_linha", "sum"),
        taxa=("vl_taxa_pedido_rateio", "sum"), frete_pago=("vl_frete_pago_rateio", "sum"), frete_real=("vl_frete_real_rateio", "sum"),
        marg=("vl_margem_contribuicao", "sum"), incompletude=("ds_incompletude", _juntar_incompletude),
    ).sort_values(["dt_pedido", "codigo"], ascending=[False, False])
    g["pct"] = g["marg"] / g["rec"]
    ids = g["cd_codigo_interno"].tolist()  # mesma ordem das linhas exibidas (para o drill)
    return ids, pd.DataFrame({
        "Pedido": g["codigo"].astype(str), "Cliente": g["nm_contato"].fillna("—"), "Data": g["dt_pedido"].dt.date, "Status": g["ds_status_pedido"].str.title(),
        "Pagamento": g["ds_meio_pagamento_nuvemshop"].fillna("—"),
        "Receita líq.": g["rec"], "CMV": g["custo"], "Taxa": g["taxa"],
        "Resultado frete": g["frete_pago"] - g["frete_real"],
        "Contrib. R$": g["marg"], "Contrib. %": g["pct"],
        "Dado faltante": g["incompletude"].replace("", "—"),
    })


def _tabela_origem(sel):
    g = sel.groupby(["origem", "midia"], as_index=False).agg(
        pedidos=("cd_codigo_interno", "nunique"), rec=("vl_receita_liquida_produto", "sum"), marg=("vl_margem_contribuicao", "sum"),
    ).sort_values(["pedidos", "rec"], ascending=False)
    g["pct_ped"] = g["pedidos"] / g["pedidos"].sum()
    g["pct"] = g["marg"] / g["rec"]
    return pd.DataFrame({
        "Origem": g["origem"], "Mídia": g["midia"], "Pedidos": g["pedidos"], "% dos pedidos": g["pct_ped"],
        "Receita líq.": g["rec"], "Margem de contrib. (R$)": g["marg"], "Margem de contrib. (%)": g["pct"],
    })


def _tabela_produtos(sel):
    g = sel.groupby("nm_produto", as_index=False).agg(
        qtd=("qt_item", "sum"), rec=("vl_receita_liquida_produto", "sum"),
        custo=("vl_custo_linha", "sum"), marg=("vl_margem_contribuicao", "sum"),
    ).sort_values("marg", ascending=False)
    g["pct"] = g["marg"] / g["rec"]
    return pd.DataFrame({
        "Produto": g["nm_produto"], "Qtd": g["qtd"].astype(int), "Receita líq.": g["rec"], "CMV": g["custo"],
        "Margem de contrib. (R$)": g["marg"], "Margem de contrib. (%)": g["pct"],
    })


_CFG_MOEDA = st.column_config.NumberColumn(format="R$ %.2f")
_CFG_PCT = st.column_config.NumberColumn(format="percent")


def render():
    inject_css()

    with st.spinner("Carregando dados do BigQuery..."):
        try:
            dados = carregar_dados()
        except Exception as e:
            st.error(f"Erro ao carregar dados do BigQuery: {e}")
            return

    df, cancel, metas, ads, refs, hist = dados["vendas"], dados["cancel"], dados["metas"], dados["ads"], dados["refs"], dados["hist"]
    hoje = _hoje_brt()
    if df.empty:
        st.info("Sem pedidos válidos no período de histórico.")
        return

    atualizado = pd.to_datetime(df["ts_load"]).max()
    st.html(f"""
    <div class="report-header">
      <div>
        <div class="report-brand">shibari brasil · camada mensal</div>
        <div class="report-title">Vendas <span>&amp;</span> Margem de Contribuição</div>
        <div class="report-meta">Pedidos válidos (pagos, não cancelados) · margem antes de mídia · fonte: {DATASET}.{TABELA}</div>
      </div>
      <div class="report-badge">
        Hoje: <strong>{hoje.strftime("%d/%m/%Y")}</strong><br>
        Dados atualizados em: <strong>{atualizado.strftime("%d/%m %H:%M") if pd.notna(atualizado) else "—"}</strong>
      </div>
    </div>
    """)

    meses = sorted(df["mes"].unique(), reverse=True)
    mes_atual = pd.Timestamp(hoje.replace(day=1))
    default = [mes_atual] if mes_atual in meses else meses[:1]
    meses_sel = st.multiselect("Meses", options=meses, default=default, format_func=_fmt_mes)
    if not meses_sel:
        st.info("Selecione ao menos um mês para ver os indicadores.")
        return

    sel = df[df["mes"].isin(meses_sel)]
    refs_sel = refs[refs["mes"].isin(meses_sel)]
    s = _somas(sel, refs_sel)
    r = _razoes(s)
    ant, refs_ant, rot = _periodo_anterior(df, refs, meses_sel, hoje)
    sa = _somas(ant, refs_ant) if ant is not None else None
    ra = _razoes(sa) if sa else None
    canc = _cancelamentos(cancel, meses_sel, s["pedidos"])
    meta = _meta(metas, meses_sel, hoje, s["vl_liquido_item"])

    def d(chave, tipo="rel", fonte="s", fmt=brl):
        if sa is None:
            return "", ""
        cur, prev = (s[chave], sa[chave]) if fonte == "s" else (r[chave], ra[chave])
        return _delta(cur, prev, rot, tipo, fmt if tipo == "rel" else None)

    rec = s["vl_receita_liquida_produto"]

    def sobre_rec(v):
        return f"{pct(v / rec)} da receita líq." if rec else ""

    # ═══ VENDAS E META ═══
    section_title("Vendas e meta do período")
    t_fat, c_fat = d("vl_liquido_item")
    t_ped, c_ped = d("pedidos", fmt=lambda v: f"{int(v)}")
    t_tk, c_tk = d("ticket", "rel", "r")
    render_cards([
        card("Faturamento", brl(s["vl_liquido_item"]), "produtos já com desconto + frete pago pelo cliente", delta=t_fat, delta_color=c_fat),
        card("Pedidos", f"{s['pedidos']}", f"{int(s['itens'])} itens vendidos (sem brindes)", delta=t_ped, delta_color=c_ped),
        card("Ticket médio", brl(r["ticket"]), "faturamento ÷ pedidos", delta=t_tk, delta_color=c_tk),
        card("Cancelamentos", f"{canc['qtd']} · {pct(canc['pct'])}", canc["detalhe"] or "nenhum no período",
             ref="cancelados ÷ (válidos + cancelados)"),
        card("Meta de faturamento", brl(meta["total"]) if meta["total"] else "—", "meta do(s) mês(es) selecionado(s)"),
        card("Atingimento da meta", pct(meta["atingimento"], 0) if meta["atingimento"] is not None else "—",
             "faturamento ÷ meta acumulada até hoje",
             variant=("ok" if meta["atingimento"] and meta["atingimento"] >= 1 else "bad" if meta["atingimento"] is not None else "neutral"),
             ref=f"meta acumulada: {brl(meta['acumulada'])}"),
        card("Falta para a meta", brl(meta["falta"]) if meta["total"] else "—", "meta do mês − faturamento"),
    ])
    if rot:
        note(f"Variações comparam com {rot}, com o valor daquele período entre parênteses. Só aparecem com um único mês selecionado.")
    if meta["sem_meta"]:
        note(f"Sem meta cadastrada para: {', '.join(meta['sem_meta'])}. A meta de faturamento ainda precisa ser revista — trate o atingimento como referência.", variant="warn")

    # ═══ DA RECEITA À MARGEM ═══
    section_title("Da receita à margem")
    t_rec, c_rec = d("vl_receita_liquida_produto")
    t_mc, c_mc = d("vl_margem_contribuicao")
    t_mcp, c_mcp = d("margem_pct", "pp", "r")
    render_cards([
        card("Receita bruta de produtos", brl(s["vl_receita_bruta_produto"]), "preço × quantidade, antes dos descontos · sem frete e sem brindes"),
        card("(−) Descontos", brl(s["vl_desconto_venda_rateio"]), sobre_rec(s["vl_desconto_venda_rateio"]) + " · cupom, PIX e promoção"),
        card("Receita líquida de produtos", brl(rec), "bruta − descontos · frete fica de fora (já debitado do faturamento)", delta=t_rec, delta_color=c_rec),
        card("(−) CMV", brl(s["vl_custo_linha"]), f"{sobre_rec(s['vl_custo_linha'])} · inclui {brl(s['custo_brinde'])} de brindes"),
        card("(−) Taxas de pagamento", brl(s["vl_taxa_pedido_rateio"]), sobre_rec(s["vl_taxa_pedido_rateio"])),
        card("Resultado de frete", brl(s["vl_resultado_frete"]), f"pago {brl(s['vl_frete_pago_rateio'])} − real {brl(s['vl_frete_real_rateio'])}"),
        card("(−) Reembolsos", brl(s["vl_reembolso_rateio"]), sobre_rec(s["vl_reembolso_rateio"])),
        card("(−) Embalagem (estimada)", brl(s["vl_embalagem_rateio"]), "R$ 2,50 por pedido — estimativa, não medida"),
        card("Margem de contribuição (R$)", brl(s["vl_margem_contribuicao"]), "antes de mídia", delta=t_mc, delta_color=c_mc),
        card("Margem de contribuição (%)", pct(r["margem_pct"]), "margem de contribuição ÷ receita líq. de produtos",
             variant=_variant_margem(r["margem_pct"]), delta=t_mcp, delta_color=c_mcp,
             ref=f"verde ≥ {MARGEM_OK:.0%} · âmbar ≥ {MARGEM_MIN:.0%}"),
    ])
    if s["linhas"] and s["linhas_incompletas"]:
        tipos = _juntar_incompletude(sel["ds_incompletude"])
        cob = 1 - s["linhas_incompletas"] / s["linhas"]
        note(f"<strong>Cobertura dos dados: {pct(cob)}.</strong> {s['linhas_incompletas']} de {s['linhas']} linhas do período têm dado faltante "
             f"({tipos}) — a margem dessas linhas está superestimada.", variant="warn")

    # ═══ DEPOIS DA MÍDIA ═══
    section_title("Depois da mídia (Google Ads)")
    ads_sel = ads[ads["mes"].isin(meses_sel)]
    custo_ads = float(ads_sel["vl_custo"].sum())
    if custo_ads > 0:
        margem_pos = s["vl_margem_contribuicao"] - custo_ads
        g = sel[(sel["origem"] == "google") & (sel["midia"] == "cpc")]
        rec_g, marg_g = float(g["vl_receita_liquida_produto"].sum()), float(g["vl_margem_contribuicao"].sum())
        ped_g = int(g["cd_codigo_interno"].nunique())
        x = lambda v: f"{v:.1f}×".replace(".", ",")
        render_cards([
            card("Investimento Google Ads", brl(custo_ads), f"{pct(custo_ads / rec) if rec else '—'} da receita líq. · {int(ads_sel['qt_cliques'].sum())} cliques"),
            card("Margem após mídia (R$)", brl(margem_pos), "margem de contribuição − investimento Google Ads",
                 variant="ok" if margem_pos > 0 else "bad"),
            card("Margem após mídia (%)", pct(margem_pos / rec) if rec else "—", "margem após mídia ÷ receita líq. de produtos",
                 variant=_variant_margem(margem_pos / rec if rec else None), ref=f"verde ≥ {MARGEM_OK:.0%} · âmbar ≥ {MARGEM_MIN:.0%}"),
            card("ROAS atribuído ao Google", x(rec_g / custo_ads), f"receita líq. de {ped_g} pedidos Google (cpc) ÷ investimento",
                 ref="piso: só clique identificável na URL"),
            card("Retorno sobre a margem", x(marg_g / custo_ads), "margem de contribuição dos pedidos Google ÷ investimento",
                 variant="ok" if marg_g / custo_ads >= 1 else "bad", ref="abaixo de 1× o anúncio perde dinheiro"),
            card("Investimento por pedido", brl(custo_ads / s["pedidos"]) if s["pedidos"] else "—", "investimento ÷ pedidos de todas as origens"),
        ])
        note("Inclui <strong>só o Google Ads</strong>. <strong>ROAS atribuído</strong> e <strong>retorno sobre a margem</strong> usam apenas os pedidos que a "
             "classificação de origem (tb_atribuicao_pedido) liga ao Google pago (cpc), detectado pela URL de entrada — é um <strong>piso</strong>, pois vendas "
             "influenciadas pelo anúncio sem clique identificável ficam de fora. O <strong>retorno sobre a margem</strong> é o que diz se o anúncio se paga "
             f"(o ROAS de receita ignora CMV, frete e taxas). Contexto: o Google pago responde por {pct(rec_g / rec) if rec else '—'} da receita líquida do período. "
             "O histórico do Ads começa em 15/06/2026.")
    else:
        note("Sem custo de Google Ads no período (o histórico do Ads começa em 15/06/2026).")

    # ═══ CASCATA ═══
    section_title("Da receita bruta à margem de contribuição e à margem após mídia")
    with st.container(border=True):
        st.plotly_chart(_grafico_cascata(s, r, custo_ads), use_container_width=True)
    note("<strong>De onde vem cada barra:</strong> receita, descontos e frete pago — pedido do Bling conferido com a Nuvemshop · "
         "CMV — custo do produto vigente na data do pedido (histórico de compras do Bling) · "
         "frete real — custo da etiqueta (Nuvem Envio, via Nuvemshop) · taxas de pagamento — tarifa cobrada por transação (Nuvem Pago/Mercado Pago, via Nuvemshop) · "
         "reembolsos — valor estornado na transação (Nuvemshop), lançado no <strong>mês em que aconteceu</strong> (data aproximada: última atualização do pedido; pedidos totalmente reembolsados saem das vendas e aparecem em Cancelamentos como \"estornados\") · "
         "<strong>embalagem — estimativa fixa de R$ 2,50 por pedido (parâmetro, não é medido)</strong> · imposto — 0% enquanto a loja não tem CNPJ. "
         "mídia paga — investimento no Google Ads no período (única mídia paga na base). "
         "Passos zerados não aparecem. A <strong>margem de contribuição</strong> é <strong>antes de mídia</strong>; a última barra, <strong>margem após mídia</strong>, já desconta o Google Ads. Ver specs/vendas-margem.md.")

    # ═══ META ═══
    section_title("Atingimento da meta")
    col_m1, col_m2 = st.columns(2)
    with col_m1:
        ultimo = max(meses_sel)
        st.html(f'<div class="c-label" style="margin:0 0 10px">Faturamento acumulado × meta acumulada — {_fmt_mes(ultimo)}</div>')
        fig_meta = _grafico_meta_acumulada(df, metas, ultimo, hoje)
        with st.container(border=True):
            if fig_meta is not None:
                st.plotly_chart(fig_meta, use_container_width=True)
            else:
                st.info("Sem meta cadastrada para este mês.")
    with col_m2:
        st.html('<div class="c-label" style="margin:0 0 10px">Faturamento × meta por mês (histórico completo)</div>')
        with st.container(border=True):
            st.plotly_chart(_grafico_meta_mensal(df, metas), use_container_width=True)
    note("Faturamento = produtos líquidos + frete pago (mesma base da meta). A meta de faturamento foi definida no início do ano e não foi revista: "
         "use o atingimento como referência de ritmo, não como veredito.")

    # ═══ ORIGEM ═══
    section_title("Origem das vendas")
    st.dataframe(_tabela_origem(sel), hide_index=True, use_container_width=True,
                 column_config={"Pedidos": st.column_config.NumberColumn(width=80),
                                "% dos pedidos": st.column_config.NumberColumn(format="percent", width=110),
                                "Receita líq.": st.column_config.NumberColumn(format="R$ %.2f", width=110),
                                "Margem de contrib. (R$)": st.column_config.NumberColumn(format="R$ %.2f", width=150),
                                "Margem de contrib. (%)": st.column_config.NumberColumn(format="percent", width=150)})
    note("Origem detectada pela <strong>URL de entrada</strong> do pedido (UTM e clique de anúncio), classificada no dbt (tb_atribuicao_pedido). "
         "\"(sem parametro)\" = sem UTM nem clique de anúncio identificável, ou sem sessão rastreável (direto, orgânico, link sem marcação). "
         "Os parâmetros UTM crus (<code>ds_utm_*</code>) cobrem só ~7% dos pedidos, por isso o Google Ads "
         "aparece pela detecção de clique. Nomes unificados no dbt: \"ig\", \"igshopping\" e \"instagram\" viram <strong>instagram</strong>.")

    # ═══ CLIENTES ═══
    _secao_clientes(df, hist, meses_sel, hoje)

    # ═══ EVOLUÇÃO ═══
    section_title("Evolução mensal (desde ago/2025, não segue o filtro)")
    with st.container(border=True):
        st.plotly_chart(_grafico_evolucao(df, refs), use_container_width=True)
    note("Barras: receita líquida de produtos e margem de contribuição (R$). Linha: margem de contribuição (%, eixo direito). "
         "Antes de ago/2025 a taxa e o frete reais da Nuvemshop não estão disponíveis. "
         "Com ~1 pedido por dia, variações de poucos pontos percentuais entre meses não são sinal.")

    # ═══ PRODUTOS ═══
    section_title("Produtos e categorias")
    prod = sel[~sel["fg_brinde"]]
    col_a, col_b = st.columns([1, 3.6])
    with col_a:
        st.html('<div class="c-label" style="margin:0 0 10px">Margem de contribuição por categoria</div>')
        with st.container(border=True):
            st.plotly_chart(_grafico_categorias(prod), use_container_width=True)
    with col_b:
        st.html('<div class="c-label" style="margin:0 0 10px">Margem de contribuição por produto</div>')
        st.dataframe(_tabela_produtos(prod), hide_index=True, use_container_width=True, height=380,
                     column_config={"Produto": st.column_config.TextColumn(width=190),
                                    "Qtd": st.column_config.NumberColumn(width=42),
                                    "Receita líq.": st.column_config.NumberColumn(format="R$ %.2f", width=88),
                                    "CMV": st.column_config.NumberColumn(format="R$ %.2f", width=78),
                                    "Margem de contrib. (R$)": st.column_config.NumberColumn(format="R$ %.2f", width=140),
                                    "Margem de contrib. (%)": st.column_config.NumberColumn(format="percent", width=135)})
    note("<strong>Margem de contribuição</strong> do produto = receita líq. − CMV − a parte do produto nas taxas de pagamento, no frete (pago − real), "
         "nos reembolsos e na embalagem; % sobre a receita líq. Brindes (ex.: Sticker) ficam fora desta seção, mas o custo deles entra no CMV do período. "
         "Categoria vem do cadastro do Bling.")

    # ═══ PEDIDOS ═══
    section_title("Pedidos do período")
    ids_pedidos, tab_pedidos = _tabela_pedidos(sel)
    evento = st.dataframe(tab_pedidos, hide_index=True, use_container_width=True,
                 on_select="rerun", selection_mode="single-row", key="tab_pedidos",
                 column_config={"Pedido": st.column_config.TextColumn(width=58),
                                "Cliente": st.column_config.TextColumn(width=104),
                                "Data": st.column_config.DateColumn(width=78),
                                "Status": st.column_config.TextColumn(width=70),
                                "Pagamento": st.column_config.TextColumn(width=62),
                                "Receita líq.": st.column_config.NumberColumn(format="R$ %.2f", width=80),
                                "CMV": st.column_config.NumberColumn(format="R$ %.2f", width=62),
                                "Taxa": st.column_config.NumberColumn(format="R$ %.2f", width=56),
                                "Resultado frete": st.column_config.NumberColumn(format="R$ %.2f", width=86),
                                "Contrib. R$": st.column_config.NumberColumn(format="R$ %.2f", width=90),
                                "Contrib. %": st.column_config.NumberColumn(format="percent", width=80),
                                "Dado faltante": st.column_config.TextColumn(width=70)})
    note("Pedido = <strong>código da Nuvemshop</strong> (o mesmo do painel da loja); \"Bling nnnn\" aparece só quando o pedido não tem correspondente na Nuvemshop. "
         "Uma linha por pedido. Contrib. R$ / % = margem de contribuição (R$ e % da receita líquida de produtos do próprio pedido); Resultado frete = frete pago − frete real. "
         "\"Dado faltante\" diz o que falta: sem custo, sem taxa de pagamento ou sem dados da Nuvemshop.")

    linhas_sel = evento.selection.rows if evento is not None else []
    if linhas_sel:
        _detalhe_pedido(sel, ids_pedidos[linhas_sel[0]], tab_pedidos.iloc[linhas_sel[0]])
    else:
        st.caption("Clique no início de uma linha da tabela para ver os produtos daquele pedido.")
