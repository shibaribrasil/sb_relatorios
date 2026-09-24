"""Relatório Site & Funil — Shibari Brasil (camada mensal).

Regras e definição de cada indicador: ver specs/site-funil.md. Sessões e funil vêm do GA4 (histórico desde 01/07/2026);
pedidos e faturamento vêm da `tb_pedido` (pedidos válidos). Conversão e RPV usam os pedidos reais, não as conversões do GA4.
"""
from datetime import timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from common.design import (
    COLORS, METRIC_COLORS, inject_css, card, render_cards, section_title, note, plotly_layout, brl, pct,
)
from common.ga4 import carregar_ga4, INICIO_GA4
from reports.vendas_margem import carregar_dados, _hoje_brt, _delta

PERIODOS = {"Últimos 7 dias": 7, "Últimos 30 dias": 30, "Últimos 60 dias": 60, "Desde 01/07 (início do GA4)": None}
ETAPAS = [("view_item", "Viu um produto"), ("add_to_cart", "Colocou no carrinho"), ("begin_checkout", "Iniciou o checkout"), ("purchase", "Comprou")]
# Referências de conversão: NuvemCommerce 2026 (Nuvemshop, nossa plataforma) — lojas menores 0,75%, maiores 1,17%.
CR_PISO = 0.0075
CR_META = 0.0117


def _janela(hoje, dias):
    """Período de dias fechados (até ontem) e o anterior de mesmo tamanho."""
    fim = pd.Timestamp(hoje) - pd.Timedelta(days=1)
    ini = INICIO_GA4 if dias is None else max(fim - pd.Timedelta(days=dias - 1), INICIO_GA4)
    n = (fim - ini).days + 1
    return ini, fim, ini - pd.Timedelta(days=n), ini - pd.Timedelta(days=1)


def _totais(canal, funil, ped, ini, fim):
    c = canal[(canal["dt_data"] >= ini) & (canal["dt_data"] <= fim)]
    f = funil[(funil["dt_data"] >= ini) & (funil["dt_data"] <= fim)].groupby("ds_etapa_funil")["qt_ocorrencias"].sum()
    p = ped[(ped["dt_pedido"] >= ini) & (ped["dt_pedido"] <= fim)]
    sess = float(c["qt_sessoes"].sum())
    eng = float(c["qt_sessoes_engajadas"].sum())
    pedidos = len(p)  # `ped` já é 1 linha por pedido
    fat = float(p["vl_liquido_item"].sum())
    ev = {k: float(f.get(k, 0.0)) for k, _ in ETAPAS}
    return {
        "sess": sess, "pedidos": pedidos, "fat": fat, "eng": (eng / sess) if sess else None,
        "cr": (pedidos / sess) if sess else None, "rpv": (fat / sess) if sess else None, "ev": ev,
        "carrinho": (ev["add_to_cart"] / ev["view_item"]) if ev["view_item"] else None,
        "checkout": (ev["purchase"] / ev["begin_checkout"]) if ev["begin_checkout"] else None,
        "abandono": (1 - ev["purchase"] / ev["add_to_cart"]) if ev["add_to_cart"] else None,
    }


def _grafico_funil(ev):
    nomes = [n for _, n in ETAPAS]
    vals = [ev[k] for k, _ in ETAPAS]
    base = vals[0] or 1
    fig = go.Figure(go.Bar(
        y=nomes, x=vals, orientation="h", marker_color=[METRIC_COLORS["receita"]] * 3 + [METRIC_COLORS["margem_contribuicao"]],
        text=[f"{int(v):,}".replace(",", ".") + f"  ({v / base:.1%})".replace(".", ",") for v in vals], textposition="outside", cliponaxis=False,
        hovertemplate="%{y}: %{x:,.0f}<extra></extra>"))
    plotly_layout(fig, height=260, xaxis=dict(range=[0, base * 1.35], showgrid=False, showticklabels=False), yaxis=dict(autorange="reversed"))
    return fig


def _grafico_tendencia(canal, ped, hoje):
    seg_hoje = pd.Timestamp(hoje) - pd.Timedelta(days=pd.Timestamp(hoje).weekday())
    c = canal[canal["dt_data"] >= INICIO_GA4].copy()
    c["semana"] = c["dt_data"] - pd.to_timedelta(c["dt_data"].dt.weekday, unit="D")
    s = c.groupby("semana")["qt_sessoes"].sum()
    p = ped.assign(semana=ped["dt_pedido"] - pd.to_timedelta(ped["dt_pedido"].dt.weekday, unit="D")).groupby("semana")["cd_codigo_interno"].nunique()
    df = pd.DataFrame({"sess": s}).join(p.rename("ped")).fillna(0)
    df = df[(df.index < seg_hoje) & (df.index >= INICIO_GA4 - pd.Timedelta(days=6))]  # só semanas fechadas
    df["cr"] = df["ped"] / df["sess"].where(df["sess"] > 0)
    x = [d.strftime("%d/%m") for d in df.index]
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_bar(x=x, y=df["sess"], name="Sessões", marker_color=METRIC_COLORS["receita"], hovertemplate="semana de %{x}<br>%{y:,.0f} sessões<extra></extra>", secondary_y=False)
    fig.add_trace(go.Scatter(x=x, y=df["cr"], name="Conversão (pedidos ÷ sessões)", mode="lines+markers", line=dict(color=METRIC_COLORS["margem_pct"], width=2),
                             hovertemplate="semana de %{x}<br>%{y:.2%}<extra></extra>"), secondary_y=True)
    plotly_layout(fig, height=300, hovermode="x unified", xaxis=dict(type="category", gridcolor=COLORS["grid"]))
    fig.update_yaxes(title_text="sessões", gridcolor=COLORS["grid"], secondary_y=False)
    fig.update_yaxes(tickformat=".1%", rangemode="tozero", showgrid=False, secondary_y=True)
    return fig


def _tabela_canais(canal, ini, fim):
    c = canal[(canal["dt_data"] >= ini) & (canal["dt_data"] <= fim)]
    g = c.groupby("ds_canal", as_index=False).agg(sess=("qt_sessoes", "sum"), eng=("qt_sessoes_engajadas", "sum"),
                                                   conv=("qt_conversoes", "sum"), rec=("vl_receita", "sum")).sort_values("sess", ascending=False)
    tot = g["sess"].sum()
    return pd.DataFrame({
        "Canal": g["ds_canal"], "Sessões": g["sess"], "% das sessões": g["sess"] / tot if tot else 0,
        "Engajadas": g["eng"] / g["sess"].where(g["sess"] > 0), "Compras (GA4)": g["conv"],
        "Conversão (GA4)": g["conv"] / g["sess"].where(g["sess"] > 0), "Receita (GA4)": g["rec"], "RPV (GA4)": g["rec"] / g["sess"].where(g["sess"] > 0),
    })


def render():
    inject_css()
    with st.spinner("Carregando dados..."):
        try:
            ga = carregar_ga4()
            ped = carregar_dados()["vendas"]
        except Exception as e:
            st.error(f"Erro ao carregar dados: {e}")
            return
    canal, funil = ga["canal"], ga["funil"]
    hoje = _hoje_brt()
    ped = ped.groupby("cd_codigo_interno", as_index=False).agg(dt_pedido=("dt_pedido", "first"), vl_liquido_item=("vl_liquido_item", "sum"))
    st.html(f"""
    <div class="report-header">
      <div>
        <div class="report-brand">shibari brasil · camada mensal</div>
        <div class="report-title">Site <span>&amp;</span> Funil</div>
        <div class="report-meta">Sessões e funil do GA4 (desde 01/07/2026) · pedidos válidos da tb_pedido · dias fechados (até ontem)</div>
      </div>
      <div class="report-badge">Hoje: <strong>{hoje.strftime("%d/%m/%Y")}</strong></div>
    </div>
    """)
    nome = st.selectbox("Período", options=list(PERIODOS), index=1)
    ini, fim, p_ini, p_fim = _janela(hoje, PERIODOS[nome])
    t = _totais(canal, funil, ped, ini, fim)
    tem_ant = p_ini >= INICIO_GA4
    ta = _totais(canal, funil, ped, p_ini, p_fim) if tem_ant else None
    rot = f"{p_ini.strftime('%d/%m')}–{p_fim.strftime('%d/%m')}"

    def d(chave, tipo="rel", fmt=None):
        if not tem_ant or ta[chave] is None or t[chave] is None:
            return "", ""
        return _delta(t[chave], ta[chave], rot, tipo, fmt if tipo == "rel" else None)

    section_title(f"{nome}: {ini.strftime('%d/%m/%Y')} a {fim.strftime('%d/%m/%Y')}")
    t1, c1 = d("sess", fmt=lambda v: f"{int(v):,}".replace(",", "."))
    t2, c2 = d("cr", "pp")
    t3, c3 = d("rpv", fmt=brl)
    render_cards([
        card("Sessões", f"{int(t['sess']):,}".replace(",", "."), f"{pct(t['eng'], 0)} engajadas", delta=t1, delta_color=c1),
        card("Taxa de conversão", pct(t["cr"], 2), f"{t['pedidos']} pedidos ÷ sessões", delta=t2, delta_color=c2,
             variant=("ok" if t["cr"] and t["cr"] >= CR_META else "warn" if t["cr"] and t["cr"] >= CR_PISO else "bad"),
             ref="ref. Nuvemshop (NuvemCommerce 2026): lojas menores 0,75% · maiores 1,17%"),
        card("Receita por visitante (RPV)", brl(t["rpv"]), "faturamento ÷ sessões", delta=t3, delta_color=c3),
        card("Carrinho ÷ visualização", pct(t["carrinho"], 1), "add_to_cart ÷ view_item (eventos)"),
        card("Checkout concluído", pct(t["checkout"], 1), "compras ÷ inícios de checkout (eventos)"),
        card("Abandono de carrinho", pct(t["abandono"], 0), "1 − compras ÷ add_to_cart (eventos)", ref="ref. Nuvemshop (NuvemCommerce 2025): ~40% em lojas em expansão · definição a confirmar"),
    ])
    note("Conversão e RPV usam os <strong>pedidos válidos</strong> da tb_pedido (Nuvemshop/Bling) ÷ sessões do GA4. Taxas do funil usam <strong>contagem de eventos</strong> do GA4 "
         "(não sessões distintas), então servem para acompanhar tendência, não como valor exato. Sem tráfego = sem conversão: bots e visitas sem intenção derrubam a taxa.")

    # ═══ FUNIL ═══
    section_title("Funil do período")
    with st.container(border=True):
        st.plotly_chart(_grafico_funil(t["ev"]), use_container_width=True)
    ev = t["ev"]
    passos = []
    for (k1, n1), (k2, n2) in zip(ETAPAS[:-1], ETAPAS[1:]):
        if ev[k1]:
            passos.append(f"{n1.lower()} → {n2.lower()}: <strong>{pct(ev[k2] / ev[k1], 1)}</strong>")
    note("Conversão entre etapas: " + " · ".join(passos) + ". A etapa com a maior queda relativa mostra onde o funil vaza: "
         "produto → carrinho baixo aponta para a página de produto (preço, fotos, frete); carrinho → checkout ou checkout → compra baixos apontam para frete e meios de pagamento.")

    # ═══ TENDÊNCIA ═══
    section_title("Sessões e conversão por semana")
    with st.container(border=True):
        st.plotly_chart(_grafico_tendencia(canal, ped, hoje), use_container_width=True)
    note("Só semanas fechadas (segunda a domingo) desde o início do GA4. Com poucos pedidos por semana a conversão oscila: olhe a tendência de várias semanas.")

    # ═══ CANAIS ═══
    section_title("Performance por canal (GA4)")
    tab = _tabela_canais(canal, ini, fim)
    st.dataframe(tab, hide_index=True, use_container_width=True,
                 column_config={"Canal": st.column_config.TextColumn(width=150), "Sessões": st.column_config.NumberColumn(format="%.0f", width=80),
                                "% das sessões": st.column_config.NumberColumn(format="percent", width=100),
                                "Engajadas": st.column_config.NumberColumn(format="percent", width=90),
                                "Compras (GA4)": st.column_config.NumberColumn(format="%.0f", width=110),
                                "Conversão (GA4)": st.column_config.NumberColumn(format="percent", width=120),
                                "Receita (GA4)": st.column_config.NumberColumn(format="R$ %.0f", width=110),
                                "RPV (GA4)": st.column_config.NumberColumn(format="R$ %.2f", width=100)})
    note("Canais do agrupamento padrão do GA4. Compras, conversão e receita desta tabela são as do <strong>GA4</strong> (atribuição do próprio GA4, que perde parte dos pedidos: "
         "a origem por pedido, mais precisa, está em Vendas & Margem). Serve para comparar canais entre si: <strong>engajamento</strong> baixo sinaliza descasamento entre anúncio e página; "
         "canal pago com muitas sessões e nenhuma compra é candidato a corte. E-mail quase sem sessões = canal ainda não ativado.")
