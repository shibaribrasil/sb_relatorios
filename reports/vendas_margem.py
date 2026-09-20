"""Relatório Vendas & Margem — Shibari Brasil (camada mensal).

Regras de negócio e definição de cada indicador: ver specs/vendas-margem.md.
Não altere cálculo/filtro sem antes ler (e, se preciso, atualizar) esse spec.

Toda regra vive em `dbt_dw_az.tb_pedido` (projeto sb_dw_dbt). Aqui só se filtra,
soma e apresenta: margem em % é sempre soma(margem) ÷ soma(receita líquida de
produtos), nunca média de percentuais.
"""
import os
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
# SB_TABELA_PEDIDO=tb_pedido_fase1). Em produção usa a tabela oficial.
DATASET = os.environ.get("SB_DATASET_PEDIDO", "dbt_dw_az")
TABELA = os.environ.get("SB_TABELA_PEDIDO", "tb_pedido")

INICIO_HISTORICO = "2025-08-01"  # taxa real e frete real da Nuvemshop só são confiáveis desde ago/2025
BRT = timezone(timedelta(hours=-3))  # sem horário de verão desde 2019

# Semáforo da margem de contribuição (antes de mídia) — ver specs/vendas-margem.md
MARGEM_OK = 0.50     # >= 50%: verde
MARGEM_MIN = 0.40    # >= 40% (mínimo institucional): âmbar; abaixo: vermelho

COLUNAS = """
    cd_codigo_interno, cd_pedido, dt_pedido, ds_status_pedido, nm_produto, ds_categoria,
    ds_meio_pagamento_nuvemshop, qt_item, vl_total_item, vl_desconto_rateio,
    vl_receita_liquida_produto, vl_liquido_item, vl_frete_pago_rateio, vl_frete_real_rateio,
    vl_resultado_frete, vl_custo_linha, vl_taxa_pedido_rateio, vl_reembolso_rateio,
    vl_embalagem_rateio, vl_imposto_rateio, vl_margem_contribuicao, fg_margem_incompleta, ts_load
"""

SOMAVEIS = [
    "qt_item", "vl_liquido_item", "vl_receita_liquida_produto", "vl_frete_pago_rateio",
    "vl_frete_real_rateio", "vl_resultado_frete", "vl_custo_linha", "vl_taxa_pedido_rateio",
    "vl_reembolso_rateio", "vl_embalagem_rateio", "vl_imposto_rateio", "vl_margem_contribuicao",
]


@st.cache_data(ttl=900)
def carregar_dados():
    client = bq.get_client()
    sql = f"""
        SELECT {COLUNAS}
          FROM `{bq.PROJECT}.{DATASET}.{TABELA}`
         WHERE fg_pedido_valido
           AND dt_pedido >= DATE '{INICIO_HISTORICO}'
    """
    df = bq.query_df(client, sql)
    df["dt_pedido"] = pd.to_datetime(df["dt_pedido"])
    df["mes"] = df["dt_pedido"].dt.to_period("M").dt.to_timestamp()
    df["ds_categoria"] = df["ds_categoria"].fillna("Sem categoria")
    for c in SOMAVEIS:
        df[c] = pd.to_numeric(df[c]).fillna(0.0)
    return df


def _hoje_brt():
    return datetime.now(BRT).date()


def _fmt_mes(ts):
    return pd.Timestamp(ts).strftime("%m/%Y")


def _somas(df):
    s = {c: float(df[c].sum()) for c in SOMAVEIS}
    s["pedidos"] = int(df["cd_codigo_interno"].nunique())
    s["linhas"] = len(df)
    s["linhas_incompletas"] = int(df["fg_margem_incompleta"].fillna(False).astype(bool).sum())
    return s


def _razoes(s):
    rec = s["vl_receita_liquida_produto"]
    return {
        "margem_pct": (s["vl_margem_contribuicao"] / rec) if rec else None,
        "ticket": (s["vl_liquido_item"] / s["pedidos"]) if s["pedidos"] else None,
    }


def _periodo_anterior(df, meses_sel, hoje):
    """Só existe comparação quando UM mês está selecionado. Mês corrente (parcial)
    compara com o mesmo intervalo de dias do mês anterior; mês fechado, com o mês
    anterior inteiro. Devolve (df_anterior, rótulo) ou (None, None)."""
    if len(meses_sel) != 1:
        return None, None
    mes = pd.Timestamp(meses_sel[0])
    ant = (mes - pd.offsets.MonthBegin(1))
    d = df[df["mes"] == ant]
    if d.empty:
        return None, None
    if mes.date() == hoje.replace(day=1):
        d = d[d["dt_pedido"].dt.day <= hoje.day]
        return d, f"mesmo período de {_fmt_mes(ant)}"
    return d, f"{_fmt_mes(ant)}"


def _delta(cur, prev, rotulo, tipo="rel"):
    """Texto de variação com sinal explícito + cor. tipo 'rel' = %, 'pp' = pontos percentuais."""
    if cur is None or prev is None or prev == 0 and tipo == "rel":
        return "", ""
    if tipo == "pp":
        d = (cur - prev) * 100
        txt = f"{'+' if d >= 0 else '−'}{abs(d):.1f}".replace(".", ",") + " p.p."
    else:
        d = (cur - prev) / abs(prev)
        txt = f"{'+' if d >= 0 else '−'}{abs(d) * 100:.1f}".replace(".", ",") + "%"
    return f"{txt} vs. {rotulo}", kpi_delta_color(d)


def _variant_margem(m):
    if m is None:
        return "neutral"
    if m >= MARGEM_OK:
        return "ok"
    return "warn" if m >= MARGEM_MIN else "bad"


def _grafico_cascata(s):
    passos = [
        ("Receita líquida de produtos", s["vl_receita_liquida_produto"], "absolute"),
        ("+ Frete pago pelo cliente", s["vl_frete_pago_rateio"], "relative"),
        ("− Frete real (etiqueta)", -s["vl_frete_real_rateio"], "relative"),
        ("− Custo dos produtos", -s["vl_custo_linha"], "relative"),
        ("− Taxa de pagamento", -s["vl_taxa_pedido_rateio"], "relative"),
        ("− Reembolsos", -s["vl_reembolso_rateio"], "relative"),
        ("− Embalagem", -s["vl_embalagem_rateio"], "relative"),
        ("− Imposto", -s["vl_imposto_rateio"], "relative"),
    ]
    # passos zerados só poluem a cascata (ex.: imposto 0% sem CNPJ); ficam de fora
    passos = [p for i, p in enumerate(passos) if i == 0 or abs(p[1]) >= 0.005]
    nomes = [p[0] for p in passos] + ["= Margem de contribuição"]
    valores = [p[1] for p in passos] + [s["vl_margem_contribuicao"]]
    medidas = [p[2] for p in passos] + ["total"]
    fig = go.Figure(go.Waterfall(
        x=nomes, y=valores, measure=medidas,
        text=[brl(v, 0) for v in valores], textposition="outside", cliponaxis=False,
        connector=dict(line=dict(color=COLORS["border"], width=1)),
        increasing=dict(marker=dict(color=CATEGORICAL[7])),
        decreasing=dict(marker=dict(color=COLORS["text_muted"])),
        totals=dict(marker=dict(color=METRIC_COLORS["receita"])),
        hovertemplate="%{x}<br>%{text}<extra></extra>",
    ))
    plotly_layout(fig, height=360, showlegend=False,
                  yaxis=dict(tickprefix="R$ ", gridcolor=COLORS["grid"]),
                  xaxis=dict(tickangle=-20, automargin=True))
    return fig


def _grafico_evolucao(df):
    m = df.groupby("mes").agg(rec=("vl_receita_liquida_produto", "sum"), marg=("vl_margem_contribuicao", "sum")).reset_index()
    m["pct"] = m["marg"] / m["rec"]
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_bar(x=m["mes"], y=m["rec"], name="Receita líquida de produtos", marker_color=METRIC_COLORS["receita"],
                hovertemplate="%{x|%m/%Y}<br>R$ %{y:,.0f}<extra></extra>", secondary_y=False)
    fig.add_bar(x=m["mes"], y=m["marg"], name="Margem de contribuição", marker_color=METRIC_COLORS["margem_contribuicao"],
                hovertemplate="%{x|%m/%Y}<br>R$ %{y:,.0f}<extra></extra>", secondary_y=False)
    fig.add_trace(go.Scatter(x=m["mes"], y=m["pct"], name="Margem %", mode="lines+markers",
                             line=dict(color=METRIC_COLORS["margem_pct"], width=2),
                             hovertemplate="%{x|%m/%Y}<br>%{y:.1%}<extra></extra>"), secondary_y=True)
    plotly_layout(fig, height=320, barmode="group", hovermode="x unified",
                  xaxis=dict(tickformat="%m/%Y", dtick="M1", gridcolor=COLORS["grid"]))
    fig.update_yaxes(tickprefix="R$ ", gridcolor=COLORS["grid"], secondary_y=False)
    fig.update_yaxes(tickformat=".0%", range=[0, 1], showgrid=False, secondary_y=True)
    return fig


def _grafico_categorias(sel, top_n=8):
    g = sel.groupby("ds_categoria").agg(rec=("vl_receita_liquida_produto", "sum"), marg=("vl_margem_contribuicao", "sum")).reset_index()
    g = g.sort_values("marg", ascending=False)
    if len(g) > top_n:
        resto = g.iloc[top_n:]
        g = pd.concat([g.iloc[:top_n], pd.DataFrame([{"ds_categoria": "Outras", "rec": resto["rec"].sum(), "marg": resto["marg"].sum()}])])
    g["pct"] = g["marg"] / g["rec"]
    g = g.iloc[::-1]  # maior no topo
    fig = go.Figure(go.Bar(
        y=g["ds_categoria"], x=g["marg"], orientation="h", marker_color=METRIC_COLORS["margem_contribuicao"],
        text=[f"{brl(m, 0)} · {pct(p, 0)}" for m, p in zip(g["marg"], g["pct"])], textposition="outside", cliponaxis=False,
        hovertemplate="%{y}<br>Margem R$ %{x:,.0f}<extra></extra>",
    ))
    plotly_layout(fig, height=max(240, 34 * len(g) + 60), showlegend=False,
                  xaxis=dict(tickprefix="R$ ", gridcolor=COLORS["grid"], range=[0, max(float(g["marg"].max()), 1.0) * 1.35]),
                  yaxis=dict(automargin=True))
    return fig


def _tabela_pedidos(sel):
    g = sel.groupby(["cd_pedido", "dt_pedido", "ds_status_pedido", "ds_meio_pagamento_nuvemshop"], dropna=False, as_index=False).agg(
        itens=("qt_item", "sum"),
        rec=("vl_receita_liquida_produto", "sum"),
        frete_pago=("vl_frete_pago_rateio", "sum"),
        frete_real=("vl_frete_real_rateio", "sum"),
        custo=("vl_custo_linha", "sum"),
        taxa=("vl_taxa_pedido_rateio", "sum"),
        marg=("vl_margem_contribuicao", "sum"),
        incompleta=("fg_margem_incompleta", "max"),
    ).sort_values("dt_pedido", ascending=False)
    g["pct"] = g["marg"] / g["rec"]
    return pd.DataFrame({
        "Pedido": g["cd_pedido"], "Data": g["dt_pedido"].dt.date, "Status": g["ds_status_pedido"].str.title(),
        "Pagamento": g["ds_meio_pagamento_nuvemshop"].fillna("—"), "Itens": g["itens"].astype(int),
        "Receita líq. produtos": g["rec"], "Frete pago": g["frete_pago"], "Frete real": g["frete_real"],
        "Custo": g["custo"], "Taxa": g["taxa"], "Margem": g["marg"], "Margem %": g["pct"],
        "Dado incompleto": g["incompleta"].fillna(False).astype(bool).map({True: "sim", False: ""}),
    })


def _tabela_produtos(sel):
    g = sel.groupby("nm_produto", as_index=False).agg(
        qtd=("qt_item", "sum"), rec=("vl_receita_liquida_produto", "sum"),
        custo=("vl_custo_linha", "sum"), marg=("vl_margem_contribuicao", "sum"),
    ).sort_values("marg", ascending=False)
    g["pct"] = g["marg"] / g["rec"]
    return pd.DataFrame({
        "Produto": g["nm_produto"], "Qtd": g["qtd"].astype(int), "Receita líq. produtos": g["rec"],
        "Custo": g["custo"], "Margem": g["marg"], "Margem %": g["pct"],
    })


_CFG_MOEDA = st.column_config.NumberColumn(format="R$ %.2f")
_CFG_PCT = st.column_config.NumberColumn(format="percent")


def render():
    inject_css()

    with st.spinner("Carregando dados do BigQuery..."):
        try:
            df = carregar_dados()
        except Exception as e:
            st.error(f"Erro ao carregar dados do BigQuery: {e}")
            return

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
    s = _somas(sel)
    r = _razoes(s)
    ant, rot = _periodo_anterior(df, meses_sel, hoje)
    sa = _somas(ant) if ant is not None else None
    ra = _razoes(sa) if sa else None

    def d(chave, tipo="rel", fonte="s"):
        if sa is None:
            return "", ""
        cur, prev = (s[chave], sa[chave]) if fonte == "s" else (r[chave], ra[chave])
        return _delta(cur, prev, rot, tipo)

    # ═══ INDICADORES ═══
    section_title("Indicadores do período")
    t_fat, c_fat = d("vl_liquido_item")
    t_rec, c_rec = d("vl_receita_liquida_produto")
    t_mg, c_mg = d("vl_margem_contribuicao")
    t_pct, c_pct = d("margem_pct", "pp", "r")
    t_ped, c_ped = d("pedidos")
    t_tk, c_tk = d("ticket", "rel", "r")
    render_cards([
        card("Faturamento", brl(s["vl_liquido_item"]), "produtos líquidos + frete pago", delta=t_fat, delta_color=c_fat),
        card("Receita líq. de produtos", brl(s["vl_receita_liquida_produto"]), "produtos − descontos, sem frete", delta=t_rec, delta_color=c_rec),
        card("Margem de contribuição", brl(s["vl_margem_contribuicao"]), "antes de mídia", delta=t_mg, delta_color=c_mg),
        card("Margem %", pct(r["margem_pct"]), "margem ÷ receita líq. de produtos",
             variant=_variant_margem(r["margem_pct"]), delta=t_pct, delta_color=c_pct,
             ref=f"verde ≥ {MARGEM_OK:.0%} · âmbar ≥ {MARGEM_MIN:.0%}"),
        card("Pedidos", f"{s['pedidos']}", f"{int(s['qt_item'])} itens", delta=t_ped, delta_color=c_ped),
        card("Ticket médio", brl(r["ticket"]), "faturamento ÷ pedidos", delta=t_tk, delta_color=c_tk),
        card("Resultado de frete", brl(s["vl_resultado_frete"]), "frete pago − frete real (negativo = subsídio)"),
    ])
    if rot:
        note(f"Variações comparam com {rot}. Só aparecem quando um único mês está selecionado.")
    if s["linhas"] and s["linhas_incompletas"]:
        cob = 1 - s["linhas_incompletas"] / s["linhas"]
        note(f"<strong>Cobertura dos dados: {pct(cob)}.</strong> {s['linhas_incompletas']} de {s['linhas']} linhas do período têm custo, taxa "
             "ou dado da Nuvemshop ausente — a margem dessas linhas está superestimada.", variant="warn")

    # ═══ CASCATA ═══
    section_title("Da receita à margem de contribuição")
    with st.container(border=True):
        st.plotly_chart(_grafico_cascata(s), use_container_width=True)
    note("Cada barra é a soma do período. Passos com valor zero (ex.: imposto, enquanto a loja não tem CNPJ) não aparecem. "
         "Margem <strong>antes de mídia</strong>: o gasto com Google Ads não é atribuível por pedido — ver specs/vendas-margem.md.")

    # ═══ EVOLUÇÃO ═══
    section_title("Evolução mensal (desde ago/2025, não segue o filtro)")
    with st.container(border=True):
        st.plotly_chart(_grafico_evolucao(df), use_container_width=True)
    note("Barras: receita líquida de produtos e margem de contribuição (R$). Linha: margem % (eixo direito). "
         "Antes de ago/2025 a taxa e o frete reais da Nuvemshop não estão disponíveis, por isso o histórico começa aqui. "
         "Com ~1 pedido por dia, variações de um mês para outro de poucos pontos percentuais não são sinal.")

    # ═══ PRODUTOS ═══
    section_title("Produtos e categorias")
    col_a, col_b = st.columns([1, 1])
    with col_a:
        st.html('<div class="c-label" style="margin:0 0 10px">Margem por categoria</div>')
        with st.container(border=True):
            st.plotly_chart(_grafico_categorias(sel), use_container_width=True)
    with col_b:
        st.html('<div class="c-label" style="margin:0 0 10px">Margem por produto</div>')
        st.dataframe(_tabela_produtos(sel), hide_index=True, use_container_width=True, height=320,
                     column_config={"Receita líq. produtos": _CFG_MOEDA, "Custo": _CFG_MOEDA, "Margem": _CFG_MOEDA, "Margem %": _CFG_PCT})
    note("Agrupado por produto (sem separar variação). A categoria vem do cadastro do Bling.")

    # ═══ PEDIDOS ═══
    section_title("Pedidos do período")
    st.dataframe(_tabela_pedidos(sel), hide_index=True, use_container_width=True,
                 column_config={"Receita líq. produtos": _CFG_MOEDA, "Frete pago": _CFG_MOEDA, "Frete real": _CFG_MOEDA,
                                "Custo": _CFG_MOEDA, "Taxa": _CFG_MOEDA, "Margem": _CFG_MOEDA, "Margem %": _CFG_PCT})
    note("Uma linha por pedido, sem dados do cliente. Margem % do pedido = margem ÷ receita líquida de produtos do próprio pedido. "
         "\"Dado incompleto\" = custo, taxa ou dado da Nuvemshop ausente em alguma linha do pedido.")
