"""Relatório Produtos & Mix — Shibari Brasil (camada mensal).

Regras e definição de cada indicador: ver specs/produtos-mix.md. Reaproveita os dados de `vendas_margem`
(mesma base `tb_pedido`, mesma margem de contribuição antes de mídia); aqui só se filtra por período,
soma e apresenta. Brindes (ex.: Sticker) ficam fora — o custo deles está no CMV da página Vendas & Margem.
"""
import textwrap

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from common.design import (
    COLORS, METRIC_COLORS, CATEGORICAL, inject_css, card, render_cards, section_title, note, plotly_layout, brl, pct,
)
from reports.vendas_margem import carregar_dados, _hoje_brt, _delta, _variant_margem, MARGEM_OK, MARGEM_MIN

CLASSE_A = 0.80   # produtos que somam até 80% da margem de contribuição do período
CLASSE_B = 0.95   # até 95%; o resto é C
TOP_PARETO = 25
CORES_CLASSE = {"A": METRIC_COLORS["receita"], "B": METRIC_COLORS["margem_contribuicao"], "C": COLORS["text_muted"]}
PERIODOS = {"Mês atual": 0, "Últimos 3 meses": 2, "Últimos 6 meses": 5, "Últimos 12 meses": 11}


def _janela(hoje, meses_atras):
    """[início, fim] do período (meses inteiros até hoje) e da janela anterior de mesmo tamanho em dias."""
    ini = pd.Timestamp(hoje).replace(day=1) - pd.DateOffset(months=meses_atras)
    fim = pd.Timestamp(hoje)
    dias = (fim - ini).days + 1
    return ini, fim, ini - pd.Timedelta(days=dias), ini - pd.Timedelta(days=1)


def _agg_produto(d):
    g = d.groupby(["nm_produto", "ds_categoria"], as_index=False).agg(
        qtd=("qt_item", "sum"), rec=("vl_receita_liquida_produto", "sum"), custo=("vl_custo_linha", "sum"),
        marg=("vl_margem_contribuicao", "sum"), pedidos=("cd_codigo_interno", "nunique"),
    )
    g["pct_marg"] = g["marg"] / g["rec"].where(g["rec"] != 0)
    return g


def _classificar(g):
    """Curva ABC pela margem de contribuição (R$): A até 80% acumulado, B até 95%, C o restante."""
    g = g.sort_values("marg", ascending=False).reset_index(drop=True)
    total = g["marg"].clip(lower=0).sum()
    g["cum"] = g["marg"].clip(lower=0).cumsum() / total if total else 0.0
    anterior = g["cum"].shift(fill_value=0.0)  # produto entra na classe em que começa (quem cruza o limite ainda é A/B)
    g["classe"] = np.where(anterior < CLASSE_A, "A", np.where(anterior < CLASSE_B, "B", "C"))
    return g


def _curto(nome, n=26):
    return textwrap.shorten(str(nome), width=n, placeholder="…")


def _grafico_pareto(g):
    top = g.head(TOP_PARETO)
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(go.Bar(x=[_curto(n) for n in top["nm_produto"]], y=top["marg"], marker_color=[CORES_CLASSE[c] for c in top["classe"]],
                         customdata=np.stack([top["nm_produto"], top["classe"]], axis=-1), name="Margem de contribuição (R$)",
                         hovertemplate="%{customdata[0]}<br>Classe %{customdata[1]}<br>R$ %{y:,.0f}<extra></extra>"), secondary_y=False)
    fig.add_trace(go.Scatter(x=[_curto(n) for n in top["nm_produto"]], y=top["cum"], mode="lines+markers", name="% acumulado da margem",
                             line=dict(color=METRIC_COLORS["margem_pct"], width=2), hovertemplate="%{y:.0%} acumulado<extra></extra>"), secondary_y=True)
    plotly_layout(fig, height=380, showlegend=False, xaxis=dict(tickangle=-45, automargin=True, dtick=1, tickfont=dict(size=10)),
                  yaxis=dict(tickprefix="R$ ", gridcolor=COLORS["grid"]))
    fig.update_yaxes(tickformat=".0%", range=[0, 1.02], showgrid=False, secondary_y=True)
    return fig


def _grafico_dispersao(g):
    d = g[(g["rec"] > 0) & g["pct_marg"].notna()]
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=d["qtd"], y=d["pct_marg"], mode="markers", customdata=np.stack([d["nm_produto"], d["rec"], d["marg"]], axis=-1),
        marker=dict(size=np.clip(d["rec"] ** 0.5 / 3, 6, 40), color=[CORES_CLASSE[c] for c in d["classe"]], opacity=0.75, line=dict(width=1, color="white")),
        hovertemplate="%{customdata[0]}<br>%{x:.0f} un · margem %{y:.0%}<br>receita R$ %{customdata[1]:,.0f}<br>margem R$ %{customdata[2]:,.0f}<extra></extra>"))
    fig.add_hline(y=MARGEM_MIN, line=dict(color=COLORS["text_muted"], dash="dash", width=1),
                  annotation_text=f"mínimo institucional {MARGEM_MIN:.0%}", annotation_position="bottom right")
    plotly_layout(fig, height=340, showlegend=False, xaxis=dict(title="unidades vendidas", type="log", gridcolor=COLORS["grid"]),
                  yaxis=dict(title="margem de contribuição (%)", tickformat=".0%", gridcolor=COLORS["grid"]))
    return fig


def _grafico_categoria_mensal(d):
    m = d.groupby(["mes", "ds_categoria"], as_index=False)["vl_receita_liquida_produto"].sum()
    tot = m.groupby("mes")["vl_receita_liquida_produto"].transform("sum")
    m["share"] = m["vl_receita_liquida_produto"] / tot
    ordem = d.groupby("ds_categoria")["vl_receita_liquida_produto"].sum().sort_values(ascending=False).index
    fig = go.Figure()
    for i, cat in enumerate(ordem):
        s = m[m["ds_categoria"] == cat]
        fig.add_bar(x=s["mes"], y=s["share"], name=cat, marker_color=CATEGORICAL[i % len(CATEGORICAL)],
                    customdata=s["vl_receita_liquida_produto"], hovertemplate=cat + "<br>%{x|%m/%Y}: %{y:.0%} (R$ %{customdata:,.0f})<extra></extra>")
    plotly_layout(fig, height=320, barmode="stack", hovermode="closest", xaxis=dict(tickformat="%m/%y", dtick="M1", gridcolor=COLORS["grid"]),
                  yaxis=dict(tickformat=".0%", range=[0, 1], gridcolor=COLORS["grid"]), legend=dict(orientation="h", y=-0.25))
    return fig


def render():
    inject_css()
    with st.spinner("Carregando dados do BigQuery..."):
        try:
            dados = carregar_dados()
        except Exception as e:
            st.error(f"Erro ao carregar dados do BigQuery: {e}")
            return
    df = dados["vendas"]
    df = df[~df["fg_brinde"]]
    hoje = _hoje_brt()
    if df.empty:
        st.info("Sem pedidos válidos no período de histórico.")
        return
    st.html(f"""
    <div class="report-header">
      <div>
        <div class="report-brand">shibari brasil · camada mensal</div>
        <div class="report-title">Produtos <span>&amp;</span> Mix</div>
        <div class="report-meta">Pedidos válidos, sem brindes · margem de contribuição antes de mídia · histórico desde ago/2025 · fonte: tb_pedido</div>
      </div>
      <div class="report-badge">Hoje: <strong>{hoje.strftime("%d/%m/%Y")}</strong></div>
    </div>
    """)

    nome = st.selectbox("Período", options=list(PERIODOS), index=1)
    ini, fim, p_ini, p_fim = _janela(hoje, PERIODOS[nome])
    cur = df[(df["dt_pedido"] >= ini) & (df["dt_pedido"] <= fim)]
    ant = df[(df["dt_pedido"] >= p_ini) & (df["dt_pedido"] <= p_fim)]
    if cur.empty:
        st.info("Sem vendas no período escolhido.")
        return
    g = _classificar(_agg_produto(cur))
    ga = _agg_produto(ant) if not ant.empty else pd.DataFrame(columns=g.columns)
    rot = f"{p_ini.strftime('%d/%m')}–{p_fim.strftime('%d/%m/%y')}"
    tem_ant = not ant.empty and p_ini >= df["dt_pedido"].min()

    rec, marg = float(g["rec"].sum()), float(g["marg"].sum())
    rec_a, marg_a = float(ga["rec"].sum()) if tem_ant else None, float(ga["marg"].sum()) if tem_ant else None
    pm, pm_a = (marg / rec if rec else None), ((marg_a / rec_a) if tem_ant and rec_a else None)
    n_a = int((g["classe"] == "A").sum())
    top5 = float(g.head(5)["marg"].sum() / marg) if marg else None
    t_rec, c_rec = _delta(rec, rec_a, rot, "rel", brl) if tem_ant else ("", "")
    t_pm, c_pm = _delta(pm, pm_a, rot, "pp") if tem_ant else ("", "")
    section_title(f"{nome}: {ini.strftime('%d/%m/%Y')} a {fim.strftime('%d/%m/%Y')}")
    render_cards([
        card("Produtos vendidos", f"{len(g)}", f"{int(g['qtd'].sum())} unidades · {len(g['ds_categoria'].unique())} categorias"),
        card("Receita líquida de produtos", brl(rec), "bruta − descontos, sem frete", delta=t_rec, delta_color=c_rec),
        card("Margem de contribuição (%)", pct(pm), "margem de contribuição ÷ receita líq. de produtos",
             variant=_variant_margem(pm), delta=t_pm, delta_color=c_pm, ref=f"verde ≥ {MARGEM_OK:.0%} · âmbar ≥ {MARGEM_MIN:.0%}"),
        card("Produtos classe A", f"{n_a} de {len(g)}", f"somam {CLASSE_A:.0%} da margem de contribuição"),
        card("Top 5 produtos", pct(top5), "da margem de contribuição do período"),
    ])
    note("Comparação com o período anterior de mesmo tamanho em dias (" + rot + ")." if tem_ant else "Sem período anterior completo para comparar.")

    # ═══ PARETO ═══
    section_title("Curva ABC — o que sustenta a margem")
    with st.container(border=True):
        st.plotly_chart(_grafico_pareto(g), use_container_width=True)
    note(f"Produtos ordenados pela <strong>margem de contribuição (R$)</strong>; mostra os {TOP_PARETO} maiores. Classe <strong>A</strong> = os que, somados, chegam a "
         f"{CLASSE_A:.0%} da margem; <strong>B</strong> = até {CLASSE_B:.0%}; <strong>C</strong> = o restante (cauda longa). A linha é a % acumulada. "
         "Produto de classe A sem estoque é venda perdida: cruzar com o estoque virá com a página de Estoque refeita.")

    # ═══ DISPERSÃO ═══
    section_title("Volume × margem — quem vende muito com margem baixa")
    with st.container(border=True):
        st.plotly_chart(_grafico_dispersao(g), use_container_width=True)
    baixos = g[(g["rec"] > 0) & (g["pct_marg"] < MARGEM_MIN)].sort_values("rec", ascending=False)
    nomes_baixos = ", ".join(f"{_curto(r.nm_produto, 34)} ({pct(r.pct_marg, 0)})" for r in baixos.head(5).itertuples()) or "nenhum"
    note(f"Cada bolha é um produto (tamanho = receita; cor = classe ABC; eixo horizontal em escala logarítmica). Abaixo da linha tracejada a margem de contribuição está "
         f"abaixo do mínimo institucional de {MARGEM_MIN:.0%}. Produtos abaixo do mínimo no período: <strong>{nomes_baixos}</strong>.")

    # ═══ MIX POR CATEGORIA ═══
    section_title("Mix por categoria")
    col1, col2 = st.columns([3, 2])
    with col1:
        with st.container(border=True):
            st.plotly_chart(_grafico_categoria_mensal(cur), use_container_width=True)
    with col2:
        c = cur.groupby("ds_categoria", as_index=False).agg(rec=("vl_receita_liquida_produto", "sum"), marg=("vl_margem_contribuicao", "sum"), qtd=("qt_item", "sum"))
        c["pct_rec"] = c["rec"] / c["rec"].sum()
        c["pct_marg"] = c["marg"] / c["rec"]
        c = c.sort_values("rec", ascending=False)
        st.dataframe(pd.DataFrame({"Categoria": c["ds_categoria"], "Receita líq.": c["rec"], "% da receita": c["pct_rec"], "Margem contrib. (%)": c["pct_marg"]}),
                     hide_index=True, use_container_width=True,
                     column_config={"Categoria": st.column_config.TextColumn(width=150), "Receita líq.": st.column_config.NumberColumn(format="R$ %.0f", width=90),
                                    "% da receita": st.column_config.NumberColumn(format="percent", width=80),
                                    "Margem contrib. (%)": st.column_config.NumberColumn(format="percent", width=100)})
    note("Barras: participação de cada categoria na receita líquida de produtos, mês a mês dentro do período. Categoria vem do cadastro do Bling.")

    # ═══ TABELA ═══
    section_title("Todos os produtos do período")
    t = g.copy()
    if tem_ant:
        t = t.merge(ga[["nm_produto", "rec"]].rename(columns={"rec": "rec_ant"}), on="nm_produto", how="left")
        t["var"] = (t["rec"] - t["rec_ant"]) / t["rec_ant"].where(t["rec_ant"] > 0)
    else:
        t["var"] = np.nan
    tab = pd.DataFrame({
        "Produto": t["nm_produto"], "Categoria": t["ds_categoria"], "Classe": t["classe"], "Qtd": t["qtd"].astype(int),
        "Receita líq.": t["rec"], "% da receita": t["rec"] / rec, "Margem contrib. (R$)": t["marg"], "Margem contrib. (%)": t["pct_marg"],
        "Δ receita vs anterior": t["var"],
    })
    st.dataframe(tab, hide_index=True, use_container_width=True,
                 column_config={"Produto": st.column_config.TextColumn(width=230), "Categoria": st.column_config.TextColumn(width=150),
                                "Classe": st.column_config.TextColumn(width=55), "Qtd": st.column_config.NumberColumn(width=50),
                                "Receita líq.": st.column_config.NumberColumn(format="R$ %.2f", width=90),
                                "% da receita": st.column_config.NumberColumn(format="percent", width=80),
                                "Margem contrib. (R$)": st.column_config.NumberColumn(format="R$ %.2f", width=110),
                                "Margem contrib. (%)": st.column_config.NumberColumn(format="percent", width=105),
                                "Δ receita vs anterior": st.column_config.NumberColumn(format="percent", width=105)})
    note("Δ receita = variação da receita líquida do produto contra o período anterior de mesmo tamanho; vazio = produto sem venda no período anterior (novo ou voltando). "
         "Margem de contribuição do produto = receita líq. − CMV − a parte do produto nas taxas, no frete (pago − real), nos reembolsos e na embalagem (estimada). "
         "Brindes ficam de fora. Com poucos pedidos, um produto isolado oscila muito: olhe períodos longos.")
