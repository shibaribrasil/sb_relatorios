"""Relatório Vendas da Semana — Shibari Brasil (camada semanal).

Regras e definição de cada indicador: ver specs/vendas-semana.md. Reaproveita os dados e as regras de
`vendas_margem` (mesma base `tb_pedido`, mesma margem de contribuição antes de mídia); aqui só se filtra
por semana (segunda a domingo), soma e apresenta.
"""
from datetime import timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from common.design import (
    COLORS, METRIC_COLORS, inject_css, card, render_cards, section_title, note, plotly_layout, brl, pct,
)
from reports.vendas_margem import (
    carregar_dados, _hoje_brt, _somas, _razoes, _delta, _variant_margem, _tabela_origem, _tabela_produtos,
    MARGEM_OK, MARGEM_MIN,
)

DIAS_SEMANA = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]
SEMANAS_TENDENCIA = 12


def _segunda(serie):
    return serie.dt.normalize() - pd.to_timedelta(serie.dt.weekday, unit="D")


def _rotulo(seg):
    seg = pd.Timestamp(seg)
    dom = seg + pd.Timedelta(days=6)
    iso = seg.isocalendar()
    return f"S{int(iso.week):02d}/{int(iso.year)} · {seg.strftime('%d/%m')}–{dom.strftime('%d/%m')}"


def _ads_semana(ads, seg):
    seg = pd.Timestamp(seg)
    a = ads[(ads["dt_data"] >= seg) & (ads["dt_data"] <= seg + pd.Timedelta(days=6))]
    return float(a["vl_custo"].sum())


def _grafico_tendencia(df, seg_sel, hoje_seg):
    """Últimas semanas até a selecionada: receita líquida e margem de contribuição (R$) + margem (%)."""
    g = df.groupby("semana").agg(rec=("vl_receita_liquida_produto", "sum"), marg=("vl_margem_contribuicao", "sum")).reset_index()
    g = g[(g["semana"] <= seg_sel)].sort_values("semana").tail(SEMANAS_TENDENCIA)
    g["pct"] = g["marg"] / g["rec"]
    x = [f"{pd.Timestamp(s).strftime('%d/%m')}" for s in g["semana"]]
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_bar(x=x, y=g["rec"], name="Receita líquida de produtos", marker_color=METRIC_COLORS["receita"],
                hovertemplate="semana de %{x}<br>R$ %{y:,.0f}<extra></extra>", secondary_y=False)
    fig.add_bar(x=x, y=g["marg"], name="Margem de contribuição (R$)", marker_color=METRIC_COLORS["margem_contribuicao"],
                hovertemplate="semana de %{x}<br>R$ %{y:,.0f}<extra></extra>", secondary_y=False)
    fig.add_trace(go.Scatter(x=x, y=g["pct"], name="Margem de contribuição (%)", mode="lines+markers",
                             line=dict(color=METRIC_COLORS["margem_pct"], width=2),
                             hovertemplate="semana de %{x}<br>%{y:.1%}<extra></extra>"), secondary_y=True)
    plotly_layout(fig, height=320, barmode="group", hovermode="x unified",
                  xaxis=dict(type="category", dtick=1, gridcolor=COLORS["grid"]))
    fig.update_yaxes(tickprefix="R$ ", gridcolor=COLORS["grid"], secondary_y=False)
    fig.update_yaxes(tickformat=".0%", range=[0, 1], showgrid=False, secondary_y=True)
    return fig


def _grafico_dias(sel, ant, seg, ate):
    fig = go.Figure()
    for base, ini, nome, cor in [(ant, seg - pd.Timedelta(days=7), "Semana anterior", COLORS["text_muted"]),
                                 (sel, seg, "Semana selecionada", METRIC_COLORS["receita"])]:
        por_dia = base.groupby(base["dt_pedido"].dt.normalize())["vl_liquido_item"].sum()
        ys = [None if (ini + pd.Timedelta(days=i)) > ate else float(por_dia.get(ini + pd.Timedelta(days=i), 0.0)) for i in range(7)]
        fig.add_bar(x=DIAS_SEMANA, y=ys, name=nome, marker_color=cor, hovertemplate="%{x}<br>R$ %{y:,.0f}<extra>" + nome + "</extra>")
    plotly_layout(fig, height=300, barmode="group", xaxis=dict(tickmode="array", tickvals=DIAS_SEMANA),
                  yaxis=dict(tickprefix="R$ ", gridcolor=COLORS["grid"]))
    return fig


def render():
    inject_css()
    with st.spinner("Carregando dados do BigQuery..."):
        try:
            dados = carregar_dados()
        except Exception as e:
            st.error(f"Erro ao carregar dados do BigQuery: {e}")
            return
    df, ads = dados["vendas"].copy(), dados["ads"]
    if df.empty:
        st.info("Sem pedidos válidos no período de histórico.")
        return
    hoje = _hoje_brt()
    hoje_ts = pd.Timestamp(hoje)
    hoje_seg = hoje_ts - pd.Timedelta(days=hoje_ts.weekday())
    df["semana"] = _segunda(df["dt_pedido"])
    atualizado = pd.to_datetime(df["ts_load"]).max()
    st.html(f"""
    <div class="report-header">
      <div>
        <div class="report-brand">shibari brasil · camada semanal</div>
        <div class="report-title">Vendas <span>da</span> Semana</div>
        <div class="report-meta">Semana de segunda a domingo · pedidos válidos · margem de contribuição antes de mídia · fonte: tb_pedido</div>
      </div>
      <div class="report-badge">
        Hoje: <strong>{hoje.strftime("%d/%m/%Y")}</strong><br>
        Dados atualizados em: <strong>{atualizado.strftime("%d/%m %H:%M") if pd.notna(atualizado) else "—"}</strong>
      </div>
    </div>
    """)

    semanas = sorted(set(df["semana"]) | {hoje_seg}, reverse=True)
    fechadas = [s for s in semanas if s < hoje_seg]
    default = 0 if not fechadas else semanas.index(fechadas[0])
    seg = pd.Timestamp(st.selectbox("Semana", options=semanas, index=default, format_func=lambda s: _rotulo(s) + (" (em andamento)" if s == hoje_seg else "")))
    parcial = seg == hoje_seg
    ate = min(seg + pd.Timedelta(days=6), hoje_ts - pd.Timedelta(days=1)) if parcial else seg + pd.Timedelta(days=6)  # só dias fechados
    ant_ini = seg - pd.Timedelta(days=7)

    sel_full = df[df["semana"] == seg]
    sel = sel_full[sel_full["dt_pedido"] <= ate]
    ant_full = df[df["semana"] == ant_ini]
    ant = ant_full[ant_full["dt_pedido"] <= (ate - pd.Timedelta(days=7))]  # mesmo trecho da semana anterior
    if parcial and ate < seg:
        st.info("A semana em andamento ainda não tem dia fechado. Escolha a semana anterior.")
        return
    s, sa = _somas(sel), _somas(ant)
    r, ra = _razoes(s), _razoes(sa)
    rot_ant = f"semana anterior{' (mesmos dias)' if parcial else ''}"

    def d(chave, tipo="rel", fonte="s", fmt=brl):
        cur, prev = (s[chave], sa[chave]) if fonte == "s" else (r[chave], ra[chave])
        return _delta(cur, prev, rot_ant, tipo, fmt if tipo == "rel" else None)

    # ═══ NÚMEROS DA SEMANA ═══
    section_title(f"Semana {_rotulo(seg)}" + (" — até ontem" if parcial else ""))
    t_f, c_f = d("vl_liquido_item")
    t_p, c_p = d("pedidos", fmt=lambda v: f"{int(v)}")
    t_t, c_t = d("ticket", "rel", "r")
    t_m, c_m = d("vl_margem_contribuicao")
    t_mp, c_mp = d("margem_pct", "pp", "r")
    render_cards([
        card("Faturamento", brl(s["vl_liquido_item"]), "produtos líquidos + frete pago", delta=t_f, delta_color=c_f),
        card("Pedidos", f"{s['pedidos']}", f"{int(s['itens'])} itens (sem brindes)", delta=t_p, delta_color=c_p),
        card("Ticket médio", brl(r["ticket"]), "faturamento ÷ pedidos", delta=t_t, delta_color=c_t),
        card("Receita líquida de produtos", brl(s["vl_receita_liquida_produto"]), "bruta − descontos, sem frete"),
        card("Margem de contribuição (R$)", brl(s["vl_margem_contribuicao"]), "antes de mídia", delta=t_m, delta_color=c_m),
        card("Margem de contribuição (%)", pct(r["margem_pct"]), "margem de contribuição ÷ receita líq. de produtos",
             variant=_variant_margem(r["margem_pct"]), delta=t_mp, delta_color=c_mp, ref=f"verde ≥ {MARGEM_OK:.0%} · âmbar ≥ {MARGEM_MIN:.0%}"),
    ])
    custo_ads = _ads_semana(ads, seg)
    if custo_ads > 0:
        pos = s["vl_margem_contribuicao"] - custo_ads
        rec = s["vl_receita_liquida_produto"]
        render_cards([
            card("Investimento Google Ads", brl(custo_ads), "na semana" + (" (dias com custo disponível)" if parcial else "")),
            card("Margem após mídia (R$)", brl(pos), "margem de contribuição − Google Ads", variant="ok" if pos > 0 else "bad"),
            card("Margem após mídia (%)", pct(pos / rec) if rec else "—", "margem após mídia ÷ receita líq. de produtos",
                 variant=_variant_margem(pos / rec if rec else None), ref=f"verde ≥ {MARGEM_OK:.0%} · âmbar ≥ {MARGEM_MIN:.0%}"),
        ])
    note("Com ~10 pedidos por semana, a margem em % oscila com o mix de produtos e frete: compare tendências de várias semanas (gráfico abaixo), "
         "não uma semana isolada. Embalagem é estimada (R$ 2,50 por pedido). Reembolsos entram no pedido de origem. "
         + ("Semana em andamento: só dias fechados, comparados com os mesmos dias da semana anterior." if parcial else "Comparação com a semana anterior inteira."))

    # ═══ TENDÊNCIA ═══
    section_title(f"Últimas {SEMANAS_TENDENCIA} semanas")
    with st.container(border=True):
        st.plotly_chart(_grafico_tendencia(df, seg, hoje_seg), use_container_width=True)
    note("Eixo: segunda-feira de cada semana. A semana em andamento, se aparecer, está incompleta.")

    # ═══ DIA A DIA ═══
    section_title("Faturamento por dia da semana")
    with st.container(border=True):
        st.plotly_chart(_grafico_dias(sel_full, ant_full, seg, ate), use_container_width=True)

    # ═══ CLIENTES ═══
    section_title("Clientes novos e recorrentes na semana")
    ped = sel.groupby("cd_codigo_interno").agg(rec=("fg_cliente_recorrente", "first"), receita=("vl_receita_liquida_produto", "sum"), marg=("vl_margem_contribuicao", "sum"))
    n = len(ped)
    if n:
        rr, nn = ped[ped["rec"]], ped[~ped["rec"]]
        mp = lambda x: (x["marg"].sum() / x["receita"].sum()) if len(x) and x["receita"].sum() else None
        render_cards([
            card("Pedidos de clientes novos", f"{len(nn)}", f"{pct(len(nn) / n)} dos pedidos"),
            card("Pedidos de recorrentes", f"{len(rr)}", f"{pct(len(rr) / n)} dos pedidos · já haviam comprado antes"),
            card("Margem contrib. (%) — novos", pct(mp(nn)), "", variant=_variant_margem(mp(nn))),
            card("Margem contrib. (%) — recorrentes", pct(mp(rr)), "", variant=_variant_margem(mp(rr))),
        ])

    # ═══ ORIGEM ═══
    section_title("Origem das vendas na semana")
    st.dataframe(_tabela_origem(sel), hide_index=True, use_container_width=True,
                 column_config={"Pedidos": st.column_config.NumberColumn(width=80),
                                "% dos pedidos": st.column_config.NumberColumn(format="percent", width=110),
                                "Receita líq.": st.column_config.NumberColumn(format="R$ %.2f", width=110),
                                "Margem de contrib. (R$)": st.column_config.NumberColumn(format="R$ %.2f", width=150),
                                "Margem de contrib. (%)": st.column_config.NumberColumn(format="percent", width=150)})

    # ═══ PRODUTOS ═══
    section_title("Produtos da semana")
    prod = sel[~sel["fg_brinde"]]
    st.dataframe(_tabela_produtos(prod), hide_index=True, use_container_width=True,
                 column_config={"Produto": st.column_config.TextColumn(width=300),
                                "Qtd": st.column_config.NumberColumn(width=50),
                                "Receita líq.": st.column_config.NumberColumn(format="R$ %.2f", width=110),
                                "CMV": st.column_config.NumberColumn(format="R$ %.2f", width=100),
                                "Margem de contrib. (R$)": st.column_config.NumberColumn(format="R$ %.2f", width=150),
                                "Margem de contrib. (%)": st.column_config.NumberColumn(format="percent", width=150)})
    note("Origem detectada pela URL de entrada (<code>tb_atribuicao_pedido</code>). Brindes ficam fora da tabela de produtos, mas o custo deles entra no CMV.")
