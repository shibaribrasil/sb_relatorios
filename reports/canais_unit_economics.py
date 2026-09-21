"""Relatório Canais & Unit Economics — Shibari Brasil (camada mensal).

Regras e definição de cada indicador: ver specs/canais-unit-economics.md. Junta o valor do cliente (`tb_cliente`, margem de
contribuição acumulada), o custo de Google Ads (`tb_gads_conta_diario`) e a origem do 1º pedido (`tb_atribuicao_pedido`).
Só o Google Ads tem custo na base: CAC "por canal" só existe para o Google pago; Meta/Instagram pago ficam sem custo (manual).
"""
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from common.design import (
    COLORS, METRIC_COLORS, inject_css, card, render_cards, section_title, note, plotly_layout, brl, pct,
)
from reports.clientes import carregar_dados as carregar_clientes, COORTE_DESDE
from reports.vendas_margem import carregar_dados as carregar_vendas, _hoje_brt

INICIO_ADS = pd.Timestamp("2026-07-01")   # o custo de Ads só existe desde 15/06/2026: junho é parcial, começamos em julho
# Orçamento diário vigente da conta (Notion: "Reestruturação do Google Ads — Plano de Ação", realocação de 21/set/2026:
# Shopping R$ 22 + Pesquisa R$ 13 = R$ 35/dia). Não vem do dbt: atualizar aqui (e na spec) quando o orçamento mudar.
# A meta antiga de R$ 600/mês (Backlog B008, ~R$ 20/dia) foi superada pela decisão de 18/set.
ORCAMENTO_DIARIO = 35.0
LTV_CAC_MIN = 3.0                          # benchmark: mínimo 3:1; saudável 4–5:1
MESES_LTV = 12
GOOGLE_PAGO = ("google", "cpc")


def _mes(ts):
    return pd.Timestamp(ts).to_period("M")


def _ltv_12m(ped, hoje):
    """Margem de contribuição acumulada em até 12 meses (contando o mês da 1ª compra) por cliente das coortes já completas.
    Coortes = mês da 1ª compra desde jan/2024 com 12 meses inteiros decorridos."""
    mes_hoje = pd.Period(hoje, "M")
    elegiveis = [m for m in ped["mes_coorte"].unique() if m.to_timestamp() >= COORTE_DESDE and (m + MESES_LTV) <= mes_hoje]
    if not elegiveis:
        return None, 0
    p = ped[ped["mes_coorte"].isin(elegiveis)]
    n = p.loc[p["nr"] == 1, "cd_contato"].nunique()
    return float(p.loc[p["k"] <= MESES_LTV, "marg"].sum() / n), n


def _tabela_mensal(meses, ads, cli, vendas, hoje):
    linhas = []
    ped = vendas.groupby("cd_codigo_interno", as_index=False).agg(dt_pedido=("dt_pedido", "first"), fat=("vl_liquido_item", "sum"), mc=("vl_margem_contribuicao", "sum"))
    ped["m"] = ped["dt_pedido"].dt.to_period("M")
    cli = cli.assign(m=cli["dt_prim_pedido"].dt.to_period("M"))
    ads = ads.assign(m=ads["dt_data"].dt.to_period("M"))
    for m in meses:
        inv = float(ads.loc[ads["m"] == m, "vl_custo"].sum())
        nov = cli[cli["m"] == m]
        nov_g = nov[(nov["ds_origem_primeiro_pedido"] == GOOGLE_PAGO[0]) & (nov["ds_midia_primeiro_pedido"] == GOOGLE_PAGO[1])]
        pm = ped[ped["m"] == m]
        fat, mc = float(pm["fat"].sum()), float(pm["mc"].sum())
        linhas.append({"m": m, "inv": inv, "novos": len(nov), "novos_g": len(nov_g), "pedidos": len(pm), "fat": fat, "mc": mc})
    return pd.DataFrame(linhas)


def _grafico_investimento(t):
    x = [m.strftime("%m/%Y") for m in t["m"]]
    fig = go.Figure()
    fig.add_bar(x=x, y=t["inv"], name="Investimento Google Ads", marker_color=METRIC_COLORS["receita"], hovertemplate="%{x}: R$ %{y:,.0f}<extra></extra>")
    # referência mensal = orçamento diário × dias do mês (mês corrente: só dias decorridos, para comparar com o gasto até agora)
    ref = [ORCAMENTO_DIARIO * (min(_hoje_brt().day, m.days_in_month) if m == pd.Period(_hoje_brt(), "M") else m.days_in_month) for m in t["m"]]
    fig.add_trace(go.Scatter(x=x, y=ref, name="Orçamento diário × dias", mode="lines+markers", line=dict(color=METRIC_COLORS["meta"], dash="dash", width=2),
                             hovertemplate="%{x}: R$ %{y:,.0f} de orçamento<extra></extra>"))
    plotly_layout(fig, height=280, xaxis=dict(type="category"), yaxis=dict(tickprefix="R$ ", gridcolor=COLORS["grid"]))
    return fig


def _grafico_mer(t):
    x = [m.strftime("%m/%Y") for m in t["m"]]
    mer = t["fat"] / t["inv"].where(t["inv"] > 0)
    be = t["fat"] / t["mc"].where(t["mc"] > 0)
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=x, y=mer, name="MER (faturamento ÷ investimento)", mode="lines+markers", line=dict(color=METRIC_COLORS["receita"], width=3),
                             hovertemplate="%{x}: %{y:.1f}×<extra></extra>"))
    fig.add_trace(go.Scatter(x=x, y=be, name="Break-even (1 ÷ margem de contribuição)", mode="lines", line=dict(color=METRIC_COLORS["meta"], dash="dash", width=2),
                             hovertemplate="%{x}: %{y:.2f}×<extra></extra>"))
    plotly_layout(fig, height=280, xaxis=dict(type="category"), yaxis=dict(ticksuffix="×", gridcolor=COLORS["grid"]))
    return fig


def render():
    inject_css()
    with st.spinner("Carregando dados do BigQuery..."):
        try:
            dc = carregar_clientes()
            dv = carregar_vendas()
        except Exception as e:
            st.error(f"Erro ao carregar dados do BigQuery: {e}")
            return
    cli, ped_cli, vendas, ads = dc["cli"], dc["ped"], dv["vendas"], dv["ads"]
    hoje = _hoje_brt()
    st.html(f"""
    <div class="report-header">
      <div>
        <div class="report-brand">shibari brasil · camada mensal</div>
        <div class="report-title">Canais <span>&amp;</span> Unit Economics</div>
        <div class="report-meta">Custo de Google Ads (única mídia paga com custo na base) · valor do cliente = margem de contribuição acumulada · CAC = investimento ÷ clientes novos</div>
      </div>
      <div class="report-badge">Hoje: <strong>{hoje.strftime("%d/%m/%Y")}</strong></div>
    </div>
    """)

    mes_atual = pd.Period(hoje, "M")
    meses = [m for m in pd.period_range(INICIO_ADS.to_period("M"), mes_atual, freq="M")]
    sel = st.multiselect("Meses", options=meses, default=meses[-3:], format_func=lambda m: m.strftime("%m/%Y"))
    if not sel:
        st.info("Selecione ao menos um mês.")
        return
    tab = _tabela_mensal(meses, ads, cli, vendas, hoje)
    p = tab[tab["m"].isin(sel)]
    inv, novos, novos_g = float(p["inv"].sum()), int(p["novos"].sum()), int(p["novos_g"].sum())
    fat, mc, pedidos = float(p["fat"].sum()), float(p["mc"].sum()), int(p["pedidos"].sum())
    cac = inv / novos if novos else None
    cac_g = inv / novos_g if novos_g else None
    ltv12, n_ltv = _ltv_12m(ped_cli, hoje)
    ltv_cac = (ltv12 / cac) if ltv12 and cac else None
    # payback: quanto do CAC o 1º pedido dos clientes captados no período já devolve (margem de contribuição do 1º pedido)
    prim = ped_cli[(ped_cli["nr"] == 1) & (ped_cli["mes"].isin(sel))]
    marg_1 = float(prim["marg"].mean()) if len(prim) else None
    cobre = (marg_1 / cac) if marg_1 and cac else None
    mer = fat / inv if inv else None
    be = fat / mc if mc else None
    n_meses = len(sel)
    tem_parcial = mes_atual in sel
    orcado = sum(ORCAMENTO_DIARIO * (min(hoje.day, m.days_in_month) if m == mes_atual else m.days_in_month) for m in sel)  # orçamento diário atual × dias do período

    section_title("Economia de aquisição: " + ", ".join(m.strftime("%m/%Y") for m in sorted(sel)))
    render_cards([
        card("Investimento em mídia (Google Ads)", brl(inv), f"{pct(inv / orcado, 0)} do orçamento atual ({brl(orcado)} = R$ {ORCAMENTO_DIARIO:.0f}/dia × dias)",
             variant=("bad" if inv > orcado * 1.1 else "ok")),
        card("Clientes novos", f"{novos}", f"{novos_g} vindos do Google pago (origem do 1º pedido)"),
        card("CAC (todos os clientes novos)", brl(cac) if cac else "—", "investimento ÷ clientes novos", ref="referência: R$ 50–175 (mediana do setor)"),
        card("CAC do Google pago", brl(cac_g) if cac_g else "—", "investimento ÷ clientes novos atribuídos ao Google (cpc)"),
        card("Valor do cliente em 12 meses", brl(ltv12) if ltv12 else "—", f"margem de contribuição por cliente · {n_ltv} clientes de coortes com 12 meses completos"),
        card("LTV : CAC", f"{ltv_cac:.1f} : 1".replace(".", ",") if ltv_cac else "—", f"valor em 12 meses ÷ CAC · mínimo saudável {LTV_CAC_MIN:.0f} : 1",
             variant=("ok" if ltv_cac and ltv_cac >= LTV_CAC_MIN else "warn" if ltv_cac and ltv_cac >= 1 else "bad" if ltv_cac else "neutral"),
             ref="acima de 6 : 1 pode significar que estamos investindo de menos"),
        card("Payback", pct(cobre, 0) if cobre else "—", f"do CAC já volta no 1º pedido ({brl(marg_1)} de margem de contribuição)" if marg_1 else "",
             variant=("ok" if cobre and cobre >= 1 else "warn" if cobre else "neutral")),
        card("MER", f"{mer:.1f}×".replace(".", ",") if mer else "—", f"faturamento total ÷ investimento · break-even {be:.2f}×".replace(".", ",") if be else "faturamento total ÷ investimento",
             variant=("ok" if mer and be and mer >= be else "bad" if mer and be else "neutral")),
    ])
    note("<strong>CAC</strong> = investimento em Google Ads ÷ clientes novos (1º pedido válido no período). Só o Google Ads tem custo na base: Meta/Instagram pago existem nos pedidos, "
         "mas sem custo — o CAC de todos os novos clientes fica <strong>subestimado</strong>. <strong>Valor em 12 meses</strong> = margem de contribuição acumulada em até 12 meses "
         "por cliente de coortes antigas (com 12 meses completos, desde 2024; margem de pedidos anteriores a ago/2025 é aproximada) — por isso não há LTV de coortes recentes. "
         "<strong>LTV:CAC</strong> mistura coortes antigas com o CAC atual: use como ordem de grandeza. <strong>Payback</strong> = margem de contribuição do 1º pedido ÷ CAC (≥ 100% = o 1º pedido já paga a aquisição). "
         "<strong>MER</strong> = faturamento de <em>todas</em> as origens ÷ investimento; o break-even é faturamento ÷ margem de contribuição (abaixo disso, a mídia consome mais do que a venda deixa)."
         + (" O mês corrente está parcial (e o custo de Ads chega com 1–2 dias de atraso)." if tem_parcial else ""))

    # ═══ MÊS A MÊS ═══
    section_title("Mês a mês")
    t = tab.copy()
    t["cac"] = t["inv"] / t["novos"].where(t["novos"] > 0)
    t["cac_g"] = t["inv"] / t["novos_g"].where(t["novos_g"] > 0)
    t["mer"] = t["fat"] / t["inv"].where(t["inv"] > 0)
    st.dataframe(pd.DataFrame({
        "Mês": t["m"].map(lambda m: m.strftime("%m/%Y") + (" (parcial)" if m == mes_atual else "")), "Investimento": t["inv"], "Clientes novos": t["novos"],
        "Novos via Google": t["novos_g"], "CAC": t["cac"], "CAC Google": t["cac_g"], "Faturamento": t["fat"], "MER": t["mer"],
    }), hide_index=True, use_container_width=True,
        column_config={"Mês": st.column_config.TextColumn(width=110), "Investimento": st.column_config.NumberColumn(format="R$ %.0f", width=110),
                       "Clientes novos": st.column_config.NumberColumn(width=110), "Novos via Google": st.column_config.NumberColumn(width=120),
                       "CAC": st.column_config.NumberColumn(format="R$ %.0f", width=80), "CAC Google": st.column_config.NumberColumn(format="R$ %.0f", width=100),
                       "Faturamento": st.column_config.NumberColumn(format="R$ %.0f", width=110), "MER": st.column_config.NumberColumn(format="%.1f×", width=80)})
    col1, col2 = st.columns(2)
    with col1:
        st.html('<div class="c-label" style="margin:0 0 10px">Investimento × orçamento atual</div>')
        with st.container(border=True):
            st.plotly_chart(_grafico_investimento(t), use_container_width=True)
    with col2:
        st.html('<div class="c-label" style="margin:0 0 10px">MER × break-even</div>')
        with st.container(border=True):
            st.plotly_chart(_grafico_mer(t), use_container_width=True)
    note(f"Orçamento de referência = R$ {ORCAMENTO_DIARIO:.0f}/dia (Shopping R$ 22 + Pesquisa R$ 13, realocação de 21/set/2026, do plano de reestruturação no Notion) × dias do mês; "
         "antes de 18/set o orçamento era menor e antes da auditoria (ago) era ~R$ 52/dia, então meses anteriores comparam com o orçamento de hoje, não com o da época. "
         "A meta antiga de R$ 600/mês (Backlog B008) foi superada. O histórico de Ads começa em 15/06/2026, por isso a tabela parte de julho.")

    # ═══ POR CANAL ═══
    section_title("De onde vêm os clientes novos")
    nov = cli[cli["dt_prim_pedido"].dt.to_period("M").isin(sel)]
    g = nov.groupby(["ds_origem_primeiro_pedido", "ds_midia_primeiro_pedido"], as_index=False).agg(
        clientes=("cd_contato", "size"), rec=("fg_recorrente", "sum"), marg=("vl_margem_contribuicao", "sum")).sort_values("clientes", ascending=False)
    g["custo"] = [inv if (o, m) == GOOGLE_PAGO else None for o, m in zip(g["ds_origem_primeiro_pedido"], g["ds_midia_primeiro_pedido"])]
    g["cac"] = g["custo"] / g["clientes"]
    st.dataframe(pd.DataFrame({
        "Origem do 1º pedido": g["ds_origem_primeiro_pedido"], "Mídia": g["ds_midia_primeiro_pedido"], "Clientes novos": g["clientes"],
        "Já recompraram": g["rec"], "Valor médio hoje (R$)": g["marg"] / g["clientes"], "Investimento medido": g["custo"], "CAC": g["cac"],
    }), hide_index=True, use_container_width=True,
        column_config={"Clientes novos": st.column_config.NumberColumn(width=110), "Já recompraram": st.column_config.NumberColumn(width=120),
                       "Valor médio hoje (R$)": st.column_config.NumberColumn(format="R$ %.2f", width=160),
                       "Investimento medido": st.column_config.NumberColumn(format="R$ %.0f", width=140), "CAC": st.column_config.NumberColumn(format="R$ %.0f", width=80)})
    note("Origem detectada pela URL de entrada do 1º pedido. \"Investimento medido\" só existe para o <strong>Google pago (cpc)</strong>: as demais origens (orgânico, Shopping gratuito, "
         "direto, Instagram, e-mail) aparecem sem custo — Meta/Instagram pago não tem gasto na base (Melhorias Manuais, item 6). O Google inclui todo o gasto da conta, inclusive "
         "campanhas que trazem clientes que a URL não identifica como cpc, então o CAC do Google pago é um <strong>teto</strong>.")
