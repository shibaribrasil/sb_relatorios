"""Relatório Canais & Unit Economics — Shibari Brasil (camada mensal).

Regras e definição de cada indicador: ver specs/canais-unit-economics.md. Junta o valor do cliente (`tb_cliente`, margem de
contribuição acumulada), o custo de Google Ads (`tb_gads_conta_diario`) e a origem do 1º pedido (`tb_atribuicao_pedido`).
Só o Google Ads tem custo na base: CAC "por canal" só existe para o Google pago; Meta/Instagram pago ficam sem custo (manual).
"""
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from common import bigquery as bq
from common.design import (
    COLORS, METRIC_COLORS, inject_css, card, render_cards, section_title, note, plotly_layout, brl, pct,
)
from reports.clientes import carregar_dados as carregar_clientes, COORTE_DESDE
from reports.origem_campanha import carregar_campanhas, pedidos_com_campanha, NAO_IDENTIFICADA
from reports.vendas_margem import carregar_dados as carregar_vendas, _hoje_brt

INICIO_ADS = pd.Timestamp("2026-07-01")   # o custo de Ads só existe desde 15/06/2026: junho é parcial, começamos em julho
AMOSTRA_MIN = 10                           # menos pedidos que isso por campanha = amostra pequena (CAC e ROAS oscilam muito)
LTV_CAC_MIN = 3.0                          # benchmark: mínimo 3:1; saudável 4–5:1
MESES_LTV = 12
GOOGLE_PAGO = ("google", "cpc")


@st.cache_data(ttl=900)
def carregar_orcamento():
    """Orçamento diário somado das campanhas ATIVAS em cada dia (histórico da conta desde 15/06/2026). Substitui a constante antiga:
    o valor muda a cada campanha ligada, pausada ou reajustada. Dataset US, consulta separada."""
    client = bq.get_client()
    o = bq.query_df(client, f"""
        SELECT dt_data, SUM(vl_orc) AS vl_orcamento FROM (
            SELECT cd_campanha, dt_data, MAX(vl_orcamento_diario) AS vl_orc
              FROM `{bq.PROJECT}.dbt_dw_us_az.tb_gads_campanha_orcamento`
             WHERE ds_status_campanha = 'ENABLED' GROUP BY 1, 2
        ) GROUP BY 1
    """)
    o["dt_data"] = pd.to_datetime(o["dt_data"])
    o["vl_orcamento"] = pd.to_numeric(o["vl_orcamento"]).fillna(0.0)
    return o


@st.cache_data(ttl=900)
def carregar_ga4_campanhas():
    """Compras do GA4 por campanha de Google pago (fonte google / meio cpc), por mês. O Google liga a sessão à campanha do Ads do lado dele
    (vinculação GA4-Ads), sem depender do gclid da URL do pedido nem da tabela de cliques: é a 3ª fonte para comparar com os pedidos e
    com as compras que o próprio Ads reporta. Dataset US, consulta separada."""
    client = bq.get_client()
    g = bq.query_df(client, f"""
        SELECT DATE_TRUNC(s.dt_data, MONTH) AS mes, COALESCE(s.ds_canal_campanha, '(not set)') AS campanha,
               COUNT(*) AS sessoes, SUM(COALESCE(c.qt_compras, 0)) AS compras, SUM(COALESCE(c.vl_receita, 0)) AS receita
          FROM `{bq.PROJECT}.dbt_dw_us_az.tb_ga4_sessao` AS s
          LEFT JOIN (SELECT cd_sessao, COUNT(*) AS qt_compras, SUM(vl_compra) AS vl_receita
                       FROM `{bq.PROJECT}.dbt_dw_us_az.tb_ga4_compras` GROUP BY cd_sessao) AS c ON s.cd_sessao = c.cd_sessao
         WHERE LOWER(s.ds_canal_fonte) = 'google' AND LOWER(s.ds_canal_meio) = 'cpc'
         GROUP BY 1, 2
    """)
    g["mes"] = pd.to_datetime(g["mes"])
    for c in ["sessoes", "compras", "receita"]:
        g[c] = pd.to_numeric(g[c]).fillna(0.0)
    return g


def _orcamento_meses(orc, meses):
    """Soma do orçamento diário nos dias dos meses escolhidos (só dias que o Ads já tem: o mês corrente vai até o último dia carregado)."""
    return float(orc.loc[orc["dt_data"].dt.to_period("M").isin(meses), "vl_orcamento"].sum())


def _campanhas_google(vendas, sel, ponte, perf, ga4):
    """Uma linha por campanha do Google Ads: custo (Ads) x pedidos e clientes novos atribuídos pelo gclid x faturamento e margem dos pedidos.
    Sem regra de negócio nova: origem/mídia/gclid vêm da tb_atribuicao_pedido; custo, cliques e compras vêm do Ads por campanha."""
    v = vendas[vendas["mes"].dt.to_period("M").isin(sel)]
    ped = v.groupby("cd_codigo_interno", as_index=False).agg(
        origem=("origem", "first"), midia=("midia", "first"), ds_gclid=("ds_gclid", "first"), ds_utm_campaign=("ds_utm_campaign", "first"),
        valor=("vl_liquido_item", "sum"), marg=("vl_margem_contribuicao", "sum"), recorrente=("fg_cliente_recorrente", "first"))
    ped = ped[(ped["origem"] == GOOGLE_PAGO[0]) & (ped["midia"] == GOOGLE_PAGO[1])]
    p = pedidos_com_campanha(ped, ponte)
    p["novo"] = ~p["recorrente"].fillna(False).astype(bool)
    ag = p.groupby(["cd_campanha", "campanha"], as_index=False, dropna=False).agg(
        pedidos=("campanha", "size"), novos=("novo", "sum"), valor=("valor", "sum"), marg=("marg", "sum"))
    pf = perf[perf["dt_data"].dt.to_period("M").isin(sel)]
    custo = pf.groupby("cd_campanha", as_index=False).agg(nm=("nm_campanha", "last"), custo=("vl_custo", "sum"), cliques=("qt_cliques", "sum"), compras_ads=("qt_conversoes", "sum"))
    g = ag.merge(custo, on="cd_campanha", how="outer")
    g["campanha"] = g["campanha"].fillna(g["nm"])
    # GA4: compras por campanha, casadas pelo NOME (o GA4 usa o nome da campanha do Ads); "(not set)" = sem campanha identificada
    gg = ga4[ga4["mes"].dt.to_period("M").isin(sel)].copy()
    gg["campanha"] = gg["campanha"].replace({"(not set)": NAO_IDENTIFICADA})
    gg = gg.groupby("campanha", as_index=False).agg(compras_ga4=("compras", "sum"))
    g = g.merge(gg, on="campanha", how="outer")
    for c in ["pedidos", "novos", "valor", "marg", "custo", "cliques", "compras_ads", "compras_ga4"]:
        g[c] = pd.to_numeric(g[c]).fillna(0)
    g = g[(g["pedidos"] > 0) | (g["custo"] > 0) | (g["compras_ga4"] > 0)].sort_values("custo", ascending=False)
    ident = int(g.loc[g["campanha"] != NAO_IDENTIFICADA, "pedidos"].sum())
    return g, ident, int(g["pedidos"].sum())


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


def _grafico_investimento(t, orc):
    x = [m.strftime("%m/%Y") for m in t["m"]]
    fig = go.Figure()
    fig.add_bar(x=x, y=t["inv"], name="Investimento Google Ads", marker_color=METRIC_COLORS["receita"], hovertemplate="%{x}: R$ %{y:,.0f}<extra></extra>")
    # referência mensal = soma dos orçamentos diários das campanhas ativas em cada dia (mês corrente: só até o último dia carregado)
    ref = [_orcamento_meses(orc, [m]) for m in t["m"]]
    fig.add_trace(go.Scatter(x=x, y=ref, name="Orçamento das campanhas ativas", mode="lines+markers", line=dict(color=METRIC_COLORS["meta"], dash="dash", width=2),
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
            orc = carregar_orcamento()
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
    orcado = _orcamento_meses(orc, sel)  # orçamento diário real das campanhas ativas, dia a dia (não uma constante)

    section_title("Economia de aquisição: " + ", ".join(m.strftime("%m/%Y") for m in sorted(sel)))
    render_cards([
        card("Investimento em mídia (Google Ads)", brl(inv), f"{pct(inv / orcado, 0) if orcado else '—'} do orçamento das campanhas ativas ({brl(orcado)} no período)",
             variant=("bad" if orcado and inv > orcado * 1.1 else "ok")),
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
         "<strong>MER</strong> = faturamento de <em>todas</em> as origens ÷ investimento; o break-even é faturamento ÷ margem de contribuição (abaixo disso, a mídia consome mais do que a venda deixa). "
         "O custo do Google Ads vem da transferência nativa do Google pro BigQuery (atualiza 1× por dia) — pode ficar um pouco diferente do painel do Google Ads em "
         "tempo real, principalmente nos últimos dias (correção de cliques inválidos que o Google credita de volta depois que o dia fecha)."
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
        st.html('<div class="c-label" style="margin:0 0 10px">Investimento × orçamento das campanhas ativas</div>')
        with st.container(border=True):
            st.plotly_chart(_grafico_investimento(t, orc), use_container_width=True)
    with col2:
        st.html('<div class="c-label" style="margin:0 0 10px">MER × break-even</div>')
        with st.container(border=True):
            st.plotly_chart(_grafico_mer(t), use_container_width=True)
    note("<strong>Orçamento</strong> = soma, dia a dia, do orçamento diário das campanhas <em>ativas</em> naquele dia (histórico da conta no BigQuery), e não um valor fixo: "
         "sobe quando uma campanha é ligada e cai quando é pausada. É um teto, não meta de gasto: o Google pode gastar menos, e o gasto de campanhas pausadas no meio do dia "
         "não entra no teto. O histórico de Ads começa em 15/06/2026, por isso a tabela parte de julho.")

    # ═══ POR CAMPANHA DO GOOGLE ═══
    section_title("CAC e retorno por campanha do Google Ads")
    try:
        dcamp = carregar_campanhas()
    except Exception as e:
        dcamp = None
        st.warning(f"Campanhas indisponíveis: {e}")
    if dcamp is not None:
        try:
            ga4 = carregar_ga4_campanhas()
        except Exception as e:
            ga4 = pd.DataFrame({"mes": pd.to_datetime([]), "campanha": [], "sessoes": [], "compras": [], "receita": []})
            st.warning(f"Compras do GA4 indisponíveis: {e}")
        g, ident, total_ped = _campanhas_google(vendas, sel, dcamp["ponte"], dcamp["perf"], ga4)
        if g.empty:
            st.info("Sem custo nem pedidos de Google pago nos meses escolhidos.")
        else:
            g["cac"] = g["custo"] / g["novos"].where((g["novos"] > 0) & (g["custo"] > 0))  # sem custo ligado à linha (ex.: campanha não identificada), CAC fica em branco
            g["roas"] = g["valor"] / g["custo"].where(g["custo"] > 0)
            g["mc_custo"] = g["marg"] / g["custo"].where(g["custo"] > 0)
            g["amostra"] = ["pequena" if n < AMOSTRA_MIN else "ok" for n in g["pedidos"]]
            tot = {"campanha": "Total", "custo": g["custo"].sum(), "cliques": g["cliques"].sum(), "pedidos": g["pedidos"].sum(), "novos": g["novos"].sum(),
                   "valor": g["valor"].sum(), "marg": g["marg"].sum(), "compras_ads": g["compras_ads"].sum(), "compras_ga4": g["compras_ga4"].sum()}
            tot["cac"] = tot["custo"] / tot["novos"] if tot["novos"] else None
            tot["roas"] = tot["valor"] / tot["custo"] if tot["custo"] else None
            tot["mc_custo"] = tot["marg"] / tot["custo"] if tot["custo"] else None
            tot["amostra"] = "pequena" if tot["pedidos"] < AMOSTRA_MIN else "ok"
            g = pd.concat([g, pd.DataFrame([tot])], ignore_index=True)
            st.dataframe(pd.DataFrame({
                "Campanha": g["campanha"], "Custo (Ads)": g["custo"], "Cliques": g["cliques"], "Pedidos": g["pedidos"], "Clientes novos": g["novos"], "CAC": g["cac"],
                "Faturamento atribuído": g["valor"], "ROAS real": g["roas"], "Margem de contrib.": g["marg"], "Margem ÷ custo": g["mc_custo"],
                "Compras (Ads)": g["compras_ads"], "Compras (GA4)": g["compras_ga4"], "Amostra": g["amostra"],
            }), hide_index=True, use_container_width=True,
                column_config={"Campanha": st.column_config.TextColumn(width="large"), "Custo (Ads)": st.column_config.NumberColumn(format="R$ %.0f", width=100),
                               "Cliques": st.column_config.NumberColumn(format="%d", width=70), "Pedidos": st.column_config.NumberColumn(format="%d", width=70),
                               "Clientes novos": st.column_config.NumberColumn(format="%d", width=100), "CAC": st.column_config.NumberColumn(format="R$ %.0f", width=80),
                               "Faturamento atribuído": st.column_config.NumberColumn(format="R$ %.0f", width=140), "ROAS real": st.column_config.NumberColumn(format="%.1f×", width=90),
                               "Margem de contrib.": st.column_config.NumberColumn(format="R$ %.0f", width=130), "Margem ÷ custo": st.column_config.NumberColumn(format="%.1f×", width=110),
                               "Compras (Ads)": st.column_config.NumberColumn(format="%.0f", width=110),
                               "Compras (GA4)": st.column_config.NumberColumn(format="%.0f", width=115), "Amostra": st.column_config.TextColumn(width=80)})
            note(f"<strong>Como ler:</strong> cada pedido de Google pago é ligado à campanha pelo <code>gclid</code> da URL de entrada (cruzado com os cliques do Ads). Neste período, "
                 f"<strong>{ident} de {total_ped} pedidos</strong> ({pct(ident / total_ped if total_ped else None, 0)}) têm campanha identificada; o resto fica em “{NAO_IDENTIFICADA}” "
                 "(clique sem <code>gclid</code>, como no iPhone, ou fora do histórico de cliques). <strong>CAC</strong> = custo da campanha ÷ clientes novos atribuídos a ela (1º pedido do cliente); "
                 "custo sem cliente identificado não some: fica na linha da campanha, com CAC em branco. <strong>ROAS real</strong> = faturamento dos pedidos atribuídos ÷ custo (o valor de "
                 "conversão que o Ads reporta é maior e não é usado). <strong>Margem ÷ custo</strong> ≥ 1 = a campanha pagou o próprio custo só com a margem de contribuição. "
                 f"<strong>Amostra pequena</strong> = menos de {AMOSTRA_MIN} pedidos: CAC e ROAS de poucos pedidos oscilam demais, leia como hipótese, não como conclusão. "
                 "O <strong>Remarketing</strong> (e a Marca) fecha a venda de quem já conhecia a loja: CAC baixo ali não quer dizer que a campanha sozinha traz gente nova, e parte do mérito é do "
                 "Shopping, da Pesquisa e do topo de funil, que apresentaram a loja antes (o último clique leva o crédito). Por isso leia o Total e compare campanhas de mesma função. "
                 "<strong>Três fontes de compras por campanha, com critérios diferentes:</strong> <em>Pedidos</em> = pedidos reais da nossa base ligados pelo <code>gclid</code> da URL de entrada (piso: "
                 "pedido com <code>gclid</code> que não aparece na tabela de cliques do Ads cai em “não identificada”); <em>Compras (Ads)</em> = o que o Google Ads credita à campanha (modelo dele, inclui "
                 "conversões que a nossa base não ligou); <em>Compras (GA4)</em> = compras de sessões que o GA4 liga à campanha pela vinculação com o Ads (também é último clique não direto, então "
                 "conta compras de quem voltou depois). Quando as três divergem muito, como no Shopping, o CAC e o ROAS da linha são um piso, não um veredito. "
                 "Os últimos ~2 dias do GA4 ainda são reprocessados pelo Google.")

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
    note("Origem detectada pela URL de entrada do 1º pedido. \"Investimento medido\" só existe para o <strong>Google pago (cpc)</strong>: as demais origens (Google orgânico, "
         "direto, Instagram, e-mail) aparecem sem custo — Meta/Instagram pago não tem gasto na base (Melhorias Manuais, item 6). O Google inclui todo o gasto da conta, inclusive "
         "campanhas que trazem clientes que a URL não identifica como cpc, então o CAC do Google pago é um <strong>teto</strong>.")
