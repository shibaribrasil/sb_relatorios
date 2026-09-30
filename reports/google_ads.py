"""Relatório Google Ads — Shibari Brasil (camada semanal).

Regras e definição de cada indicador: ver specs/google-ads.md. Reescrito em 30/09/2026 sobre a camada `az` (antes lia as `rpt_gads_*`, legado):
custo, impressões, cliques, compras, leilão (participação de impressões), orçamento e palavras-chave vêm do Google Ads
(`dbt_dw_us_az.tb_gads_*`); o retorno REAL vem dos pedidos (`tb_pedido` + `tb_atribuicao_pedido`, us-east4) e o tráfego do GA4. Regiões
diferentes (US × us-east4): junta no pandas. Nenhuma regra de negócio nova: só filtro por semana, soma e razão (Σ ÷ Σ).

A semana em andamento mostra o ACUMULADO até o último dia fechado (o custo do Ads chega com 1 dia de atraso) e é comparada aos mesmos dias
da semana anterior.
"""
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from common import bigquery as bq
from common import semana as sm
from common.design import (
    COLORS, METRIC_COLORS, inject_css, card, render_cards, section_title, note, plotly_layout, kpi_delta_color, brl, pct, nome_curto,
)
from reports.canais_unit_economics import carregar_orcamento
from reports.origem_campanha import carregar_campanhas, pedidos_com_campanha, NAO_IDENTIFICADA
from reports.vendas_margem import carregar_dados as carregar_vendas, _hoje_brt

INICIO_ADS = pd.Timestamp("2026-06-15")   # histórico do Ads no BigQuery
SEMANAS_TENDENCIA = 12
SEMANAS_PIVOT = 8
AMOSTRA_MIN = 5                           # menos compras que isso na semana = amostra pequena (CPA e ROAS oscilam)
TIPO_CANAL = {"SEARCH": "Pesquisa", "SHOPPING": "Shopping", "PERFORMANCE_MAX": "PMax", "DISPLAY": "Display", "VIDEO": "Vídeo"}


# ═══ CARGA ═════════════════════════════════════════════════════════════════════════════════════════════════════════════════════

@st.cache_data(ttl=900)
def carregar_ads():
    """Custo/impressões/cliques/compras por campanha e dia, participação de impressões e palavras-chave. Dataset US, consulta separada."""
    c = bq.get_client()
    perf = bq.query_df(c, f"""
        SELECT cd_campanha, nm_campanha, ds_status_campanha, ds_tipo_canal, dt_data, vl_custo, qt_impressoes, qt_cliques, qt_conversoes, vl_conversoes
          FROM `{bq.PROJECT}.dbt_dw_us_az.tb_gads_campanha_performance` WHERE dt_data >= DATE '{INICIO_ADS.date()}'
    """)
    # a view de participação de impressões traz uma 2ª linha zerada por dia em alguns dias (segmento): MAX por campanha/dia descarta a linha vazia
    lei = bq.query_df(c, f"""
        SELECT cd_campanha, dt_data, MAX(pct_impression_share) AS is_, MAX(pct_perda_budget) AS perda_orc, MAX(pct_perda_ranking) AS perda_rank
          FROM `{bq.PROJECT}.dbt_dw_us_az.tb_gads_campanha_impression_share` WHERE dt_data >= DATE '{INICIO_ADS.date()}' GROUP BY 1, 2
    """)
    kw = bq.query_df(c, f"""
        SELECT nm_campanha, nm_grupo_anuncio, ds_keyword, ds_correspondencia, dt_data, vl_custo, qt_impressoes, qt_cliques, qt_conversoes
          FROM `{bq.PROJECT}.dbt_dw_us_az.tb_gads_keyword_performance`
         WHERE dt_data >= DATE '{INICIO_ADS.date()}' AND fl_keyword_negativa IS NOT TRUE
    """)
    for d in (perf, lei, kw):
        d["dt_data"] = pd.to_datetime(d["dt_data"])
    for col in ["vl_custo", "qt_impressoes", "qt_cliques", "qt_conversoes", "vl_conversoes"]:
        perf[col] = pd.to_numeric(perf[col]).fillna(0.0)
    for col in ["vl_custo", "qt_impressoes", "qt_cliques", "qt_conversoes"]:
        kw[col] = pd.to_numeric(kw[col]).fillna(0.0)
    for col in ["is_", "perda_orc", "perda_rank"]:
        lei[col] = pd.to_numeric(lei[col])
    perf["nm_campanha"] = perf["nm_campanha"].fillna(perf["cd_campanha"].astype(str))
    perf["campanha"] = perf["nm_campanha"]
    perf["tipo"] = perf["ds_tipo_canal"].map(TIPO_CANAL).fillna(perf["ds_tipo_canal"])
    return {"perf": perf, "lei": lei, "kw": kw}


@st.cache_data(ttl=900)
def carregar_ga4_cpc():
    """Sessões, sessões engajadas e compras do GA4 vindas de Google pago (fonte google / meio cpc), por dia e campanha. Dataset US."""
    c = bq.get_client()
    g = bq.query_df(c, f"""
        SELECT s.dt_data, COALESCE(s.ds_canal_campanha, '(not set)') AS campanha, COUNT(*) AS sessoes, COUNTIF(s.fl_sessao_engajada) AS engajadas,
               SUM(COALESCE(p.qt, 0)) AS compras, SUM(COALESCE(p.vl, 0)) AS receita
          FROM `{bq.PROJECT}.dbt_dw_us_az.tb_ga4_sessao` AS s
          LEFT JOIN (SELECT cd_sessao, COUNT(*) AS qt, SUM(vl_compra) AS vl FROM `{bq.PROJECT}.dbt_dw_us_az.tb_ga4_compras` GROUP BY cd_sessao) AS p USING (cd_sessao)
         WHERE LOWER(s.ds_canal_fonte) = 'google' AND LOWER(s.ds_canal_meio) = 'cpc'
         GROUP BY 1, 2
    """)
    g["dt_data"] = pd.to_datetime(g["dt_data"])
    for col in ["sessoes", "engajadas", "compras", "receita"]:
        g[col] = pd.to_numeric(g[col]).fillna(0.0)
    return g


# ═══ CÁLCULO (só soma e razão) ═════════════════════════════════════════════════════════════════════════════════════════════════

def variacao(cur, prev, rot, tipo="rel", fmt=None, menor_melhor=False, neutro=False):
    """Texto de variação com sinal + valor anterior + cor. `menor_melhor` inverte a cor (CPC, CPA); `neutro` = sem juízo (ex.: investimento)."""
    if cur is None or prev is None or cur != cur or prev != prev or (tipo == "rel" and prev == 0):
        return "", ""
    d = (cur - prev) / abs(prev) if tipo == "rel" else (cur - prev)
    txt = (f"{'+' if d >= 0 else '−'}{abs(d) * 100:.1f}".replace(".", ",") + "%") if tipo == "rel" else (f"{'+' if d >= 0 else '−'}{abs(d) * 100:.1f}".replace(".", ",") + " p.p.")
    cor = COLORS["text_muted"] if neutro else kpi_delta_color(d, higher_is_better=not menor_melhor)
    return f"{txt} vs. {rot}" + (f" ({fmt(prev)})" if fmt else ""), cor


def _div(a, b):
    return (a / b) if b else None


def somas_ads(perf, ini, fim):
    """Totais do Ads na janela: custo, impressões, cliques, compras e valor de conversão (o que o Ads reporta) + razões Σ÷Σ."""
    x = sm.entre(perf, "dt_data", ini, fim)
    s = {k: float(x[c].sum()) for k, c in [("custo", "vl_custo"), ("impr", "qt_impressoes"), ("cliques", "qt_cliques"), ("compras", "qt_conversoes"), ("valor", "vl_conversoes")]}
    s.update({"ctr": _div(s["cliques"], s["impr"]), "cpc": _div(s["custo"], s["cliques"]), "cpa": _div(s["custo"], s["compras"]), "roas_ads": _div(s["valor"], s["custo"])})
    return s


def google_pago(vendas, ini, fim):
    """Pedidos REAIS de Google pago (origem google / mídia cpc, pela URL de entrada) na janela: pedidos, faturamento, margem de contribuição, clientes novos."""
    x = sm.entre(vendas, "dt_pedido", ini, fim)
    g = x[(x["origem"] == "google") & (x["midia"] == "cpc")]
    ped = g.groupby("cd_codigo_interno").agg(rec=("fg_cliente_recorrente", "first"))
    return {"pedidos": int(len(ped)), "fat": float(g["vl_liquido_item"].sum()), "mc": float(g["vl_margem_contribuicao"].sum()), "novos": int((~ped["rec"].astype(bool)).sum())}


def ga4_cpc(ga4, ini, fim):
    x = sm.entre(ga4, "dt_data", ini, fim)
    s = {"sessoes": float(x["sessoes"].sum()), "engajadas": float(x["engajadas"].sum()), "compras": float(x["compras"].sum()), "receita": float(x["receita"].sum())}
    s["pct_eng"] = _div(s["engajadas"], s["sessoes"])
    return s


def _leilao(perf, lei, ini, fim, por_campanha=True):
    """Participação de impressões ponderada pelas impressões ELEGÍVEIS (impressões ÷ participação), não média simples dos dias.
    Perdas por orçamento e por classificação (ranking) ponderadas do mesmo jeito. Dias sem participação informada ficam de fora."""
    x = sm.entre(perf, "dt_data", ini, fim)[["cd_campanha", "dt_data", "qt_impressoes"]].merge(lei, on=["cd_campanha", "dt_data"], how="inner")
    x = x[(x["is_"] > 0) & (x["qt_impressoes"] > 0)]
    if x.empty:
        return pd.DataFrame(columns=["cd_campanha", "is_", "perda_orc", "perda_rank"]) if por_campanha else None
    x["elig"] = x["qt_impressoes"] / x["is_"]
    x["po"], x["pr"] = x["perda_orc"].fillna(0) * x["elig"], x["perda_rank"].fillna(0) * x["elig"]
    g = x.groupby("cd_campanha" if por_campanha else lambda _: 0).agg(impr=("qt_impressoes", "sum"), elig=("elig", "sum"), po=("po", "sum"), pr=("pr", "sum"))
    out = pd.DataFrame({"is_": g["impr"] / g["elig"], "perda_orc": g["po"] / g["elig"], "perda_rank": g["pr"] / g["elig"]})
    return out.reset_index().rename(columns={"index": "cd_campanha"}) if por_campanha else out.iloc[0].to_dict()


def leilao_conta(perf, lei, ini, fim, tipos=("Pesquisa",)):
    """Participação/perdas só das campanhas de Pesquisa (o leilão de busca é o que se controla por lance e orçamento)."""
    p = perf[perf["tipo"].isin(tipos)]
    return _leilao(p, lei, ini, fim, por_campanha=False)


def tabela_semanas(perf, lei, vendas, ga4, fechado, seg_ini, n_sem):
    """Uma linha por semana (mais antigas primeiro), a última cortada no último dia fechado."""
    linhas = []
    for seg in reversed(sm.semanas(seg_ini, fechado)[:n_sem]):
        j = sm.janela(seg, fechado)
        a, r, g = somas_ads(perf, j["ini"], j["fim"]), google_pago(vendas, j["ini"], j["fim"]), ga4_cpc(ga4, j["ini"], j["fim"])
        linhas.append({"seg": seg, "n": j["n"], "parcial": j["parcial"], "fim": j["fim"], **{f"a_{k}": v for k, v in a.items()},
                       "pedidos": r["pedidos"], "fat": r["fat"], "mc": r["mc"], "novos": r["novos"],
                       "roas": _div(r["fat"], a["custo"]), "mc_custo": _div(r["mc"], a["custo"]), "sess": g["sessoes"], "pct_eng": g["pct_eng"]})
    return pd.DataFrame(linhas)


def _rot_x(t):
    return [f"{s.strftime('%d/%m')}" + ("*" if p else "") for s, p in zip(t["seg"], t["parcial"])]


def _grafico_custo_roas(t):
    x = _rot_x(t)
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_bar(x=x, y=t["a_custo"] / t["n"], name="Investimento por dia", marker_color=METRIC_COLORS["receita"], hovertemplate="semana de %{x}<br>R$ %{y:,.0f} por dia<extra></extra>", secondary_y=False)
    fig.add_trace(go.Scatter(x=x, y=t["roas"], name="ROAS real (faturamento de Google pago ÷ custo)", mode="lines+markers", line=dict(color=METRIC_COLORS["margem_pct"], width=2),
                             hovertemplate="semana de %{x}<br>%{y:.1f}×<extra></extra>"), secondary_y=True)
    plotly_layout(fig, height=300, hovermode="x unified", xaxis=dict(type="category", dtick=1, gridcolor=COLORS["grid"]))
    fig.update_yaxes(tickprefix="R$ ", gridcolor=COLORS["grid"], secondary_y=False)
    fig.update_yaxes(ticksuffix="×", showgrid=False, rangemode="tozero", secondary_y=True)
    return fig


def _grafico_cliques_cpc(t):
    x = _rot_x(t)
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_bar(x=x, y=t["a_cliques"] / t["n"], name="Cliques por dia", marker_color=METRIC_COLORS["pedidos"], hovertemplate="semana de %{x}<br>%{y:.0f} cliques por dia<extra></extra>", secondary_y=False)
    fig.add_trace(go.Scatter(x=x, y=t["a_cpc"], name="CPC médio", mode="lines+markers", line=dict(color=METRIC_COLORS["meta"], width=2),
                             hovertemplate="semana de %{x}<br>R$ %{y:.2f}<extra></extra>"), secondary_y=True)
    plotly_layout(fig, height=300, hovermode="x unified", xaxis=dict(type="category", dtick=1, gridcolor=COLORS["grid"]))
    fig.update_yaxes(gridcolor=COLORS["grid"], secondary_y=False)
    fig.update_yaxes(tickprefix="R$ ", showgrid=False, rangemode="tozero", secondary_y=True)
    return fig


def _campanhas_janela(perf, lei, vendas, ponte, ga4, ini, fim, ant_ini, ant_fim):
    """Uma linha por campanha na janela: Ads (custo, impressões, cliques, compras), leilão, sessões GA4 e pedidos reais ligados pelo gclid."""
    x = sm.entre(perf, "dt_data", ini, fim)
    g = x.groupby("cd_campanha").agg(campanha=("campanha", "last"), tipo=("tipo", "last"), status=("ds_status_campanha", "last"), custo=("vl_custo", "sum"), impr=("qt_impressoes", "sum"),
                                       cliques=("qt_cliques", "sum"), compras=("qt_conversoes", "sum"), valor=("vl_conversoes", "sum")).reset_index()
    ant = sm.entre(perf, "dt_data", ant_ini, ant_fim).groupby("cd_campanha").agg(custo_ant=("vl_custo", "sum"), cliques_ant=("qt_cliques", "sum")).reset_index()
    g = g.merge(ant, on="cd_campanha", how="left").merge(_leilao(perf, lei, ini, fim), on="cd_campanha", how="left")
    # pedidos reais: gclid da URL de entrada -> campanha (tabela de cliques do Ads)
    v = sm.entre(vendas, "dt_pedido", ini, fim)
    ped = v.groupby("cd_codigo_interno", as_index=False).agg(origem=("origem", "first"), midia=("midia", "first"), ds_gclid=("ds_gclid", "first"), ds_utm_campaign=("ds_utm_campaign", "first"),
                                                            valor=("vl_liquido_item", "sum"), marg=("vl_margem_contribuicao", "sum"))
    ped = ped[(ped["origem"] == "google") & (ped["midia"] == "cpc")]
    pc = pedidos_com_campanha(ped, ponte).groupby("cd_campanha", dropna=False).agg(pedidos=("campanha", "size"), fat=("valor", "sum"), marg=("marg", "sum")).reset_index()
    nao_ident = pc[pc["cd_campanha"].isna()]
    g = g.merge(pc[pc["cd_campanha"].notna()], on="cd_campanha", how="left")
    gs = sm.entre(ga4, "dt_data", ini, fim).groupby("campanha", as_index=False).agg(sess=("sessoes", "sum"), eng=("engajadas", "sum"))
    g = g.merge(gs, on="campanha", how="left")
    for col in ["pedidos", "fat", "marg", "sess", "eng", "custo_ant", "cliques_ant"]:
        g[col] = pd.to_numeric(g[col]).fillna(0.0)
    g["ctr"] = g["cliques"] / g["impr"].where(g["impr"] > 0)
    g["cpc"] = g["custo"] / g["cliques"].where(g["cliques"] > 0)
    g["cpa"] = g["custo"] / g["compras"].where(g["compras"] > 0)
    g["roas"] = g["fat"] / g["custo"].where(g["custo"] > 0)
    return g[(g["custo"] > 0) | (g["impr"] > 0)].sort_values("custo", ascending=False), (int(nao_ident["pedidos"].sum()) if not nao_ident.empty else 0)


def _pivot(perf, lei, fechado, metrica, seg_ini):
    """Campanha × semana (últimas semanas, mais antigas primeiro). Volumes por dia da janela: a semana em andamento fica comparável às fechadas."""
    cols, dados = [], {}
    for seg in reversed(sm.semanas(seg_ini, fechado)[:SEMANAS_PIVOT]):
        j = sm.janela(seg, fechado)
        x = sm.entre(perf, "dt_data", j["ini"], j["fim"])
        g = x.groupby("campanha").agg(custo=("vl_custo", "sum"), impr=("qt_impressoes", "sum"), cliques=("qt_cliques", "sum"), compras=("qt_conversoes", "sum"))
        lg = _leilao(x.assign(), lei, j["ini"], j["fim"])
        nm = perf.drop_duplicates("cd_campanha").set_index("cd_campanha")["campanha"]
        lg = lg.assign(campanha=lg["cd_campanha"].map(nm)).set_index("campanha") if not lg.empty else pd.DataFrame(columns=["is_", "perda_orc", "perda_rank"])
        n = j["n"]
        m = {"Investimento por dia": g["custo"] / n, "Cliques por dia": g["cliques"] / n, "Impressões por dia": g["impr"] / n,
             "CPC": g["custo"] / g["cliques"].where(g["cliques"] > 0), "CTR": g["cliques"] / g["impr"].where(g["impr"] > 0),
             "Compras (Ads) na semana": g["compras"], "CPA (Ads)": g["custo"] / g["compras"].where(g["compras"] > 0),
             "Participação de impressões": lg["is_"] if "is_" in lg else None, "Perda por classificação": lg["perda_rank"] if "perda_rank" in lg else None,
             "Perda por orçamento": lg["perda_orc"] if "perda_orc" in lg else None}[metrica]
        rot = seg.strftime("%d/%m") + ("*" if j["parcial"] else "")
        cols.append(rot)
        dados[rot] = m
    df = pd.DataFrame(dados).reindex(columns=cols)
    return df


FORMATOS = {"Investimento por dia": "R$ %.2f", "Cliques por dia": "%.1f", "Impressões por dia": "%.0f", "CPC": "R$ %.2f", "CTR": "percent", "Compras (Ads) na semana": "%.1f",
            "CPA (Ads)": "R$ %.2f", "Participação de impressões": "percent", "Perda por classificação": "percent", "Perda por orçamento": "percent"}


def _grafico_dias(perf, orc, ga4, vendas, j):
    dias = pd.date_range(j["ini"], j["ini"] + pd.Timedelta(days=6))
    a = sm.entre(perf, "dt_data", j["ini"], j["ini"] + pd.Timedelta(days=6)).groupby("dt_data").agg(custo=("vl_custo", "sum"), cliques=("qt_cliques", "sum"), compras=("qt_conversoes", "sum"))
    o = orc.set_index("dt_data")["vl_orcamento"]
    ate = j["fim"]
    x = [f"{sm.DIAS[d.weekday()]} {d.strftime('%d/%m')}" for d in dias]
    fig = go.Figure()
    fig.add_bar(x=x, y=[float(a["custo"].get(d, 0.0)) if d <= ate else None for d in dias], name="Investimento", marker_color=METRIC_COLORS["receita"], hovertemplate="%{x}<br>R$ %{y:,.2f}<extra></extra>")
    fig.add_trace(go.Scatter(x=x, y=[float(o.get(d)) if (d in o.index and d <= ate) else None for d in dias], name="Orçamento das campanhas ativas (teto)", mode="lines+markers",
                             line=dict(color=METRIC_COLORS["meta"], dash="dash", width=2), hovertemplate="%{x}<br>teto R$ %{y:,.2f}<extra></extra>"))
    plotly_layout(fig, height=280, yaxis=dict(tickprefix="R$ ", gridcolor=COLORS["grid"]), xaxis=dict(type="category"))
    return fig, a, o, dias


def _grafico_leilao(g):
    d = g[g["is_"].notna()].sort_values("custo", ascending=True)
    y = [nome_curto(n, 34) for n in d["campanha"]]
    fig = go.Figure()
    fig.add_bar(y=y, x=d["is_"], name="Impressões ganhas", orientation="h", marker_color=METRIC_COLORS["receita"], hovertemplate="%{y}<br>ganhamos %{x:.0%}<extra></extra>")
    fig.add_bar(y=y, x=d["perda_orc"], name="Perdidas por orçamento", orientation="h", marker_color=METRIC_COLORS["meta"], hovertemplate="%{y}<br>perdidas por orçamento %{x:.0%}<extra></extra>")
    fig.add_bar(y=y, x=d["perda_rank"], name="Perdidas por classificação (lance/qualidade)", orientation="h", marker_color=METRIC_COLORS["devolucoes"], hovertemplate="%{y}<br>perdidas por classificação %{x:.0%}<extra></extra>")
    plotly_layout(fig, height=max(200, 60 * len(d) + 80), barmode="stack", xaxis=dict(tickformat=".0%", range=[0, 1.001], gridcolor=COLORS["grid"]))
    return fig


def _palavras(kw, ini, fim, campanha):
    x = sm.entre(kw, "dt_data", ini, fim)
    if campanha != "Todas":
        x = x[x["nm_campanha"] == campanha]
    g = x.groupby(["nm_campanha", "ds_keyword", "ds_correspondencia"], as_index=False).agg(custo=("vl_custo", "sum"), impr=("qt_impressoes", "sum"), cliques=("qt_cliques", "sum"), compras=("qt_conversoes", "sum"))
    g = g[(g["custo"] > 0) | (g["cliques"] > 0)]
    g["ctr"] = g["cliques"] / g["impr"].where(g["impr"] > 0)
    g["cpc"] = g["custo"] / g["cliques"].where(g["cliques"] > 0)
    g["cpa"] = g["custo"] / g["compras"].where(g["compras"] > 0)
    return g.sort_values("custo", ascending=False)


# ═══ TELA ══════════════════════════════════════════════════════════════════════════════════════════════════════════════════════

def render():
    inject_css()
    with st.spinner("Carregando dados do BigQuery..."):
        try:
            ads = carregar_ads()
            ga4 = carregar_ga4_cpc()
            orc = carregar_orcamento()
            dv = carregar_vendas()
            dcamp = carregar_campanhas()
        except Exception as e:
            st.error(f"Erro ao carregar dados do BigQuery: {e}")
            return
    perf, lei, kw, vendas = ads["perf"], ads["lei"], ads["kw"], dv["vendas"]
    hoje = pd.Timestamp(_hoje_brt())
    ultimo_ads = perf["dt_data"].max()
    fechado = min(hoje - pd.Timedelta(days=1), ultimo_ads)  # o custo do Ads chega com 1 dia de atraso: hoje nunca entra
    st.html(f"""
    <div class="report-header">
      <div>
        <div class="report-brand">shibari brasil · camada semanal</div>
        <div class="report-title">Google <span>Ads</span></div>
        <div class="report-meta">Semana de segunda a domingo · custo e cliques do Google Ads · retorno real dos pedidos · fontes: tb_gads_*, tb_pedido, GA4</div>
      </div>
      <div class="report-badge">
        Hoje: <strong>{hoje.strftime("%d/%m/%Y")}</strong><br>
        Ads carregado até: <strong>{ultimo_ads.strftime("%d/%m")}</strong>
      </div>
    </div>
    """)

    semanas = sm.semanas(INICIO_ADS, fechado)
    atual = semanas[0]
    seg = pd.Timestamp(st.selectbox("Semana", options=semanas, index=0, format_func=lambda s: sm.rotulo(s, fechado if s == atual else None) + (" (em andamento, acumulado)" if s == atual and sm.janela(s, fechado)["parcial"] else "")))
    j = sm.janela(seg, fechado)
    rot_ant = f"semana anterior{' (mesmos dias)' if j['parcial'] else ''}"
    a, aa = somas_ads(perf, j["ini"], j["fim"]), somas_ads(perf, j["ant_ini"], j["ant_fim"])
    r, ra = google_pago(vendas, j["ini"], j["fim"]), google_pago(vendas, j["ant_ini"], j["ant_fim"])
    g4, g4a = ga4_cpc(ga4, j["ini"], j["fim"]), ga4_cpc(ga4, j["ant_ini"], j["ant_fim"])
    roas, roas_a = _div(r["fat"], a["custo"]), _div(ra["fat"], aa["custo"])
    mc_custo, mc_custo_a = _div(r["mc"], a["custo"]), _div(ra["mc"], aa["custo"])

    # ═══ NÚMEROS DA SEMANA ═══
    section_title(f"Semana {sm.rotulo(seg, j['fim'])}" + (f" — acumulado de {j['n']} dia{'s' if j['n'] > 1 else ''} (parcial)" if j["parcial"] else ""))
    if j["parcial"]:
        note(f"<strong>Semana em andamento:</strong> acumulado de {j['ini'].strftime('%d/%m')} a {j['fim'].strftime('%d/%m')} ({j['n']} de 7 dias), comparado com os mesmos dias da semana anterior. "
             "O custo do Google Ads chega com 1 dia de atraso, por isso o dia de hoje não entra, e as compras que o Ads credita aos últimos dias ainda sobem nos dias seguintes. "
             + ("Com poucos dias, custo e cliques já dizem algo; compras e ROAS ainda não." if j["n"] < 4 else ""), variant="warn" if j["n"] < 3 else "")
    fr = lambda v: brl(v)
    t, c = variacao(a["custo"], aa["custo"], rot_ant, fmt=fr, neutro=True)
    t2, c2 = variacao(a["cliques"], aa["cliques"], rot_ant, fmt=lambda v: f"{v:.0f}")
    t3, c3 = variacao(a["cpc"], aa["cpc"], rot_ant, fmt=fr, menor_melhor=True)
    t4, c4 = variacao(a["ctr"], aa["ctr"], rot_ant, tipo="pp")
    t5, c5 = variacao(a["compras"], aa["compras"], rot_ant, fmt=lambda v: f"{v:.1f}".replace(".", ","))
    t6, c6 = variacao(a["cpa"], aa["cpa"], rot_ant, fmt=fr, menor_melhor=True)
    render_cards([
        card("Investimento", brl(a["custo"]), f"{brl(_div(a['custo'], j['n']))} por dia", delta=t, delta_color=c),
        card("Cliques", f"{a['cliques']:.0f}", f"{a['impr']:.0f} impressões", delta=t2, delta_color=c2),
        card("CPC médio", brl(a["cpc"]), "investimento ÷ cliques", delta=t3, delta_color=c3),
        card("CTR", pct(a["ctr"]), "cliques ÷ impressões", delta=t4, delta_color=c4),
        card("Compras (segundo o Ads)", f"{a['compras']:.1f}".replace(".", ","), "só conversões de compra, no dia do clique", delta=t5, delta_color=c5,
             variant="warn" if a["compras"] < AMOSTRA_MIN else "neutral", ref=f"amostra pequena (< {AMOSTRA_MIN}): não é tendência" if a["compras"] < AMOSTRA_MIN else ""),
        card("CPA (segundo o Ads)", brl(a["cpa"]), "investimento ÷ compras do Ads", delta=t6, delta_color=c6, ref="alvo por categoria: ver playbook (R$ 27–33)"),
    ])
    t7, c7 = variacao(r["pedidos"], ra["pedidos"], rot_ant, fmt=lambda v: f"{int(v)}")
    t8, c8 = variacao(r["fat"], ra["fat"], rot_ant, fmt=fr)
    t9, c9 = variacao(roas, roas_a, rot_ant, fmt=lambda v: f"{v:.1f}×".replace(".", ","))
    t10, c10 = variacao(mc_custo, mc_custo_a, rot_ant, fmt=lambda v: f"{v:.1f}×".replace(".", ","))
    infl = _div(a["valor"], r["fat"])
    render_cards([
        card("Pedidos de Google pago (real)", f"{r['pedidos']}", f"{r['novos']} de clientes novos · origem google / cpc pela URL de entrada", delta=t7, delta_color=c7,
             variant="warn" if r["pedidos"] < AMOSTRA_MIN else "neutral", ref=f"amostra pequena (< {AMOSTRA_MIN} pedidos)" if r["pedidos"] < AMOSTRA_MIN else ""),
        card("Faturamento de Google pago", brl(r["fat"]), "produtos líquidos + frete pago", delta=t8, delta_color=c8),
        card("ROAS real", f"{roas:.1f}×".replace(".", ",") if roas is not None else "—", "faturamento de Google pago ÷ investimento", delta=t9, delta_color=c9,
             variant=("neutral" if r["pedidos"] < AMOSTRA_MIN else "ok" if roas and roas >= 3 else "warn" if roas and roas >= 1.4 else "bad" if roas is not None else "neutral"),
             ref="equilíbrio ~1,4× · confortável ≥ 3×" + (" · amostra pequena: sem semáforo" if r["pedidos"] < AMOSTRA_MIN else "")),
        card("Margem de contribuição ÷ custo", f"{mc_custo:.1f}×".replace(".", ",") if mc_custo is not None else "—", "≥ 1× = a mídia se pagou só com a margem", delta=t10, delta_color=c10,
             variant=("neutral" if r["pedidos"] < AMOSTRA_MIN else "ok" if mc_custo and mc_custo >= 1 else "bad" if mc_custo is not None else "neutral")),
        card("Valor que o Ads reporta ÷ real", f"{infl:.1f}×".replace(".", ",") if infl else "—", f"Ads: {brl(a['valor'])} · real: {brl(r['fat'])}",
             ref="acima de 1× = o Ads credita mais venda do que a nossa base confirma"),
    ])
    t11, c11 = variacao(g4["sessoes"], g4a["sessoes"], rot_ant, fmt=lambda v: f"{v:.0f}")
    t12, c12 = variacao(g4["pct_eng"], g4a["pct_eng"], rot_ant, tipo="pp")
    ratio = _div(g4["sessoes"], a["cliques"])
    render_cards([
        card("Sessões vindas de Google pago (GA4)", f"{g4['sessoes']:.0f}", "fonte google / meio cpc", delta=t11, delta_color=c11),
        card("Sessões engajadas", pct(g4["pct_eng"]), "% das sessões de Google pago", delta=t12, delta_color=c12),
        card("Sessões ÷ cliques do Ads", f"{ratio:.2f}".replace(".", ",") if ratio else "—", "rastreio saudável entre 0,7 e 1,3",
             variant=("ok" if ratio and 0.7 <= ratio <= 1.3 else "warn" if ratio else "neutral"), ref="fora da faixa: cliques sem sessão = UTM/gclid/consentimento"),
        card("Custo por sessão", brl(_div(a["custo"], g4["sessoes"])), "investimento ÷ sessões de Google pago"),
    ])
    note("<strong>Duas réguas de retorno, de propósito:</strong> <em>Compras (Ads)</em> e <em>CPA</em> usam o que o Google credita à campanha (só conversões de compra, no dia do clique); "
         "<em>ROAS real</em> e <em>margem ÷ custo</em> usam os pedidos que a nossa base confirma como Google pago. Compare a semana com as anteriores, não o número isolado: com ~5 pedidos de Google pago por semana, "
         "1 pedido a mais ou a menos muda o ROAS em 20–30%. ROAS/CPA antes da mídia de outras origens: pedidos de Google orgânico, Instagram e direto <strong>não</strong> entram aqui.")

    # ═══ SEMANA A SEMANA ═══
    section_title(f"Semana a semana (últimas {SEMANAS_TENDENCIA})")
    tab = tabela_semanas(perf, lei, vendas, ga4, fechado, INICIO_ADS, 80)
    tab = tab[tab["seg"] <= seg].tail(SEMANAS_TENDENCIA)
    if not tab.empty:
        out = pd.DataFrame({
            "Semana": [sm.rotulo(s, f) for s, f in zip(tab["seg"], tab["fim"])], "Dias": tab["n"], "Investimento": tab["a_custo"], "Por dia": tab["a_custo"] / tab["n"],
            "Impressões": tab["a_impr"], "Cliques": tab["a_cliques"], "CTR": tab["a_ctr"], "CPC": tab["a_cpc"], "Compras (Ads)": tab["a_compras"], "CPA (Ads)": tab["a_cpa"],
            "Sessões GA4 (cpc)": tab["sess"], "Pedidos reais": tab["pedidos"], "Faturamento real": tab["fat"], "ROAS real": tab["roas"], "Margem ÷ custo": tab["mc_custo"],
        }).iloc[::-1]
        st.dataframe(out, hide_index=True, use_container_width=True,
                     column_config={"Semana": st.column_config.TextColumn(width=170), "Dias": st.column_config.NumberColumn(format="%d", width=55), "Investimento": st.column_config.NumberColumn(format="R$ %.0f", width=100),
                                    "Por dia": st.column_config.NumberColumn(format="R$ %.1f", width=80), "Impressões": st.column_config.NumberColumn(format="%.0f", width=90), "Cliques": st.column_config.NumberColumn(format="%.0f", width=75),
                                    "CTR": st.column_config.NumberColumn(format="percent", width=70), "CPC": st.column_config.NumberColumn(format="R$ %.2f", width=75), "Compras (Ads)": st.column_config.NumberColumn(format="%.1f", width=110),
                                    "CPA (Ads)": st.column_config.NumberColumn(format="R$ %.0f", width=90), "Sessões GA4 (cpc)": st.column_config.NumberColumn(format="%.0f", width=130),
                                    "Pedidos reais": st.column_config.NumberColumn(format="%d", width=100), "Faturamento real": st.column_config.NumberColumn(format="R$ %.0f", width=120),
                                    "ROAS real": st.column_config.NumberColumn(format="%.1f×", width=90), "Margem ÷ custo": st.column_config.NumberColumn(format="%.1f×", width=120)})
        col1, col2 = st.columns(2)
        with col1:
            st.html('<div class="c-label" style="margin:0 0 10px">Investimento por dia × ROAS real</div>')
            with st.container(border=True):
                st.plotly_chart(_grafico_custo_roas(tab), use_container_width=True)
        with col2:
            st.html('<div class="c-label" style="margin:0 0 10px">Cliques por dia × CPC</div>')
            with st.container(border=True):
                st.plotly_chart(_grafico_cliques_cpc(tab), use_container_width=True)
        note("Eixo: segunda-feira de cada semana; <strong>*</strong> = semana em andamento (só os dias fechados). Nos gráficos os volumes estão <strong>por dia</strong> para a semana incompleta não parecer queda. "
             "A tabela mostra o total da semana (na semana em andamento, o acumulado). O histórico do Ads no BigQuery começa em 15/06/2026; o do GA4, em 01/07/2026 (a coluna de sessões fica vazia antes disso). "
             "Em 31/08 a estrutura mudou (campanhas novas e Grupo Marca pausado): a queda de investimento, impressões e cliques a partir dali é decisão de estrutura, não perda de tráfego por si só.")

    # ═══ POR CAMPANHA ═══
    section_title("Por campanha na semana")
    camp, nao_ident = _campanhas_janela(perf, lei, vendas, dcamp["ponte"], ga4, j["ini"], j["fim"], j["ant_ini"], j["ant_fim"])
    if camp.empty:
        st.info("Sem custo nem impressões nesta janela.")
    else:
        tot = {"campanha": "Total", "tipo": "", "custo": camp["custo"].sum(), "custo_ant": camp["custo_ant"].sum(), "impr": camp["impr"].sum(), "cliques": camp["cliques"].sum(),
               "compras": camp["compras"].sum(), "sess": camp["sess"].sum(), "pedidos": camp["pedidos"].sum(), "fat": camp["fat"].sum()}
        tot["ctr"], tot["cpc"], tot["cpa"] = _div(tot["cliques"], tot["impr"]), _div(tot["custo"], tot["cliques"]), _div(tot["custo"], tot["compras"])
        tot["roas"] = _div(tot["fat"], tot["custo"])
        camp2 = pd.concat([camp, pd.DataFrame([tot])], ignore_index=True)
        st.dataframe(pd.DataFrame({
            "Campanha": camp2["campanha"], "Tipo": camp2["tipo"], "Situação": camp2["status"].map({"ENABLED": "ativa", "PAUSED": "pausada", "REMOVED": "removida"}).fillna(""),
            "Investimento": camp2["custo"], "Δ vs. anterior (R$)": camp2["custo"] - camp2["custo_ant"], "Impressões": camp2["impr"], "Cliques": camp2["cliques"], "CTR": camp2["ctr"], "CPC": camp2["cpc"],
            "Compras (Ads)": camp2["compras"], "CPA (Ads)": camp2["cpa"], "Sessões GA4": camp2["sess"], "Pedidos reais": camp2["pedidos"], "ROAS real": camp2["roas"],
            "Impr. ganhas": camp2["is_"], "Perda orçamento": camp2["perda_orc"], "Perda classificação": camp2["perda_rank"],
        }), hide_index=True, use_container_width=True,
            column_config={"Campanha": st.column_config.TextColumn(width="large"), "Tipo": st.column_config.TextColumn(width=80), "Situação": st.column_config.TextColumn(width=80),
                           "Investimento": st.column_config.NumberColumn(format="R$ %.2f", width=100), "Δ vs. anterior (R$)": st.column_config.NumberColumn(format="R$ %+.2f", width=130),
                           "Impressões": st.column_config.NumberColumn(format="%.0f", width=90), "Cliques": st.column_config.NumberColumn(format="%.0f", width=75), "CTR": st.column_config.NumberColumn(format="percent", width=70),
                           "CPC": st.column_config.NumberColumn(format="R$ %.2f", width=75), "Compras (Ads)": st.column_config.NumberColumn(format="%.1f", width=110), "CPA (Ads)": st.column_config.NumberColumn(format="R$ %.0f", width=90),
                           "Sessões GA4": st.column_config.NumberColumn(format="%.0f", width=100), "Pedidos reais": st.column_config.NumberColumn(format="%.0f", width=100), "ROAS real": st.column_config.NumberColumn(format="%.1f×", width=90),
                           "Impr. ganhas": st.column_config.NumberColumn(format="percent", width=100), "Perda orçamento": st.column_config.NumberColumn(format="percent", width=120), "Perda classificação": st.column_config.NumberColumn(format="percent", width=140)})
        note(f"<strong>Como ler:</strong> <em>Pedidos reais</em> ligam o pedido à campanha pelo <code>gclid</code> da URL de entrada; {nao_ident} pedido(s) de Google pago da semana ficam em “{NAO_IDENTIFICADA}” "
             "(iPhone/sem gclid ou clique fora do histórico) e por isso a soma das campanhas pode ficar abaixo do total do topo. <em>Sessões GA4</em> casam pelo nome da campanha. "
             "<em>Impr. ganhas / perdas</em> = participação de impressões, ponderada pelas impressões elegíveis; perda por <strong>classificação</strong> é lance/qualidade do anúncio, perda por <strong>orçamento</strong> é o teto diário. "
             "Abaixo de 10% o Google só informa “&lt; 10%” (aparece como 9,99%). Campanhas de Shopping/PMax têm atribuição de pedido mais incerta (ver Canais &amp; Unit Economics): ROAS real ali é piso. "
             "Poucos pedidos por campanha: leia como hipótese.")

        # campanha × semana
        st.html('<div class="c-label" style="margin:14px 0 6px">Campanha × semana</div>')
        metrica = st.selectbox("Métrica", list(FORMATOS.keys()), index=0, key="ads_pivot_metrica")
        pv = _pivot(perf, lei, fechado, metrica, INICIO_ADS)
        pv = pv[pv.notna().any(axis=1)]
        pv.insert(0, "Campanha", pv.index)
        st.dataframe(pv.reset_index(drop=True), hide_index=True, use_container_width=True,
                     column_config={"Campanha": st.column_config.TextColumn(width="large"), **{c: st.column_config.NumberColumn(format=FORMATOS[metrica], width=85) for c in pv.columns if c != "Campanha"}})
        note("Colunas = segunda-feira de cada semana (<strong>*</strong> = em andamento, só os dias fechados). Volumes estão <strong>por dia da janela</strong>, então a semana incompleta é comparável às fechadas; "
             "razões (CPC, CTR, CPA) são Σ ÷ Σ da semana. Célula vazia = campanha sem custo/impressões naquela semana. “Compras (Ads) na semana” é o total da semana (na em andamento, o acumulado).")

    # ═══ LEILÃO ═══
    section_title("Leilão: onde perdemos impressões")
    if camp.empty or camp["is_"].notna().sum() == 0:
        st.info("Sem participação de impressões informada pelo Google nesta janela.")
    else:
        with st.container(border=True):
            st.plotly_chart(_grafico_leilao(camp), use_container_width=True)
        lc, lca = leilao_conta(perf, lei, j["ini"], j["fim"]), leilao_conta(perf, lei, j["ant_ini"], j["ant_fim"])
        if lc:
            texto = f"<strong>Pesquisa (todas as campanhas):</strong> ganhamos {pct(lc['is_'], 0)} das impressões possíveis; perdemos {pct(lc['perda_rank'], 0)} por classificação e {pct(lc['perda_orc'], 0)} por orçamento"
            if lca:
                texto += f" (semana anterior: {pct(lca['is_'], 0)} ganhas, {pct(lca['perda_rank'], 0)} por classificação, {pct(lca['perda_orc'], 0)} por orçamento)"
            texto += ". "
            if lc["perda_rank"] > lc["perda_orc"] * 2 and lc["perda_rank"] > 0.4:
                texto += "O que limita é <strong>classificação</strong>: mais orçamento não compra mais impressão; o caminho é lance, qualidade do anúncio e relevância da palavra. "
            elif lc["perda_orc"] > 0.2:
                texto += "Há impressão perdida por <strong>orçamento</strong>: as campanhas afetadas poderiam ter mais cliques com o lance atual. "
            note(texto + "Participação de impressões de poucos dias (semana em andamento) oscila bastante.")

    # ═══ DIA A DIA ═══
    section_title("Dia a dia da semana")
    fig, a_dia, o_dia, dias = _grafico_dias(perf, orc, ga4, vendas, j)
    with st.container(border=True):
        st.plotly_chart(fig, use_container_width=True)
    vd = vendas[(vendas["origem"] == "google") & (vendas["midia"] == "cpc")]
    ped_dia = vd.groupby(vd["dt_pedido"].dt.normalize())["cd_codigo_interno"].nunique()
    sess_dia = ga4.groupby("dt_data")["sessoes"].sum()
    linhas = []
    for d in dias:
        if d > j["fim"]:
            continue
        custo = float(a_dia["custo"].get(d, 0.0))
        teto = float(o_dia.get(d)) if d in o_dia.index else None
        linhas.append({"Dia": f"{sm.DIAS[d.weekday()]} {d.strftime('%d/%m')}", "Investimento": custo, "Teto (orçamento)": teto, "% do teto": _div(custo, teto), "Cliques": float(a_dia["cliques"].get(d, 0.0)),
                       "CPC": _div(custo, float(a_dia["cliques"].get(d, 0.0))), "Compras (Ads)": float(a_dia["compras"].get(d, 0.0)), "Sessões GA4 (cpc)": float(sess_dia.get(d, 0.0)), "Pedidos reais": int(ped_dia.get(d, 0))})
    if linhas:
        st.dataframe(pd.DataFrame(linhas), hide_index=True, use_container_width=True,
                     column_config={"Investimento": st.column_config.NumberColumn(format="R$ %.2f", width=110), "Teto (orçamento)": st.column_config.NumberColumn(format="R$ %.2f", width=120),
                                    "% do teto": st.column_config.NumberColumn(format="percent", width=90), "Cliques": st.column_config.NumberColumn(format="%.0f", width=80), "CPC": st.column_config.NumberColumn(format="R$ %.2f", width=80),
                                    "Compras (Ads)": st.column_config.NumberColumn(format="%.1f", width=110), "Sessões GA4 (cpc)": st.column_config.NumberColumn(format="%.0f", width=130), "Pedidos reais": st.column_config.NumberColumn(format="%d", width=110)})
    note("<strong>Teto</strong> = soma dos orçamentos diários das campanhas <em>ativas</em> naquele dia. É limite, não meta: o Google pode gastar até ~2× o orçamento num dia (compensa em outros, dentro do mês) e gasta menos quando "
         "a demanda é baixa. Gasto sempre abaixo do teto com perda por classificação alta = o gargalo não é orçamento. <em>Pedidos reais</em> por dia usam a data do pedido; a compra pode ter sido dias depois do clique.")

    # ═══ PALAVRAS-CHAVE ═══
    section_title("Palavras-chave da semana (campanhas de Pesquisa)")
    opcoes = ["Todas"] + sorted(kw["nm_campanha"].dropna().unique())
    escolha = st.selectbox("Campanha", opcoes, key="ads_kw_campanha")
    p = _palavras(kw, j["ini"], j["fim"], escolha)
    if p.empty:
        st.info("Sem palavras-chave com custo ou cliques nesta janela.")
    else:
        total_custo = float(p["custo"].sum())
        sem = p[(p["compras"] == 0) & (p["custo"] > 0)]
        st.html(f'<div class="c-label" style="margin:6px 0">{brl(total_custo)} gastos em palavras-chave · {pct(_div(float(sem["custo"].sum()), total_custo), 0)} em palavras sem conversão na semana ({brl(float(sem["custo"].sum()))})</div>')
        p2 = p.head(25)
        st.dataframe(pd.DataFrame({
            "Campanha": p2["nm_campanha"].map(lambda n: nome_curto(n, 34)), "Palavra-chave": p2["ds_keyword"], "Correspondência": p2["ds_correspondencia"].map({"BROAD": "ampla", "PHRASE": "frase", "EXACT": "exata"}).fillna(p2["ds_correspondencia"]),
            "Investimento": p2["custo"], "Impressões": p2["impr"], "Cliques": p2["cliques"], "CTR": p2["ctr"], "CPC": p2["cpc"], "Conversões (Ads)": p2["compras"], "Custo por conversão": p2["cpa"],
            "Situação": ["com conversão" if c > 0 else ("sem conversão" if cl >= 5 else "poucos cliques") for c, cl in zip(p2["compras"], p2["cliques"])],
        }), hide_index=True, use_container_width=True,
            column_config={"Campanha": st.column_config.TextColumn(width=200), "Palavra-chave": st.column_config.TextColumn(width=200), "Correspondência": st.column_config.TextColumn(width=110),
                           "Investimento": st.column_config.NumberColumn(format="R$ %.2f", width=100), "Impressões": st.column_config.NumberColumn(format="%.0f", width=90), "Cliques": st.column_config.NumberColumn(format="%.0f", width=75),
                           "CTR": st.column_config.NumberColumn(format="percent", width=70), "CPC": st.column_config.NumberColumn(format="R$ %.2f", width=75), "Conversões (Ads)": st.column_config.NumberColumn(format="%.1f", width=125),
                           "Custo por conversão": st.column_config.NumberColumn(format="R$ %.0f", width=130), "Situação": st.column_config.TextColumn(width=110)})
    note("Top 25 por investimento; fonte <code>tb_gads_keyword_performance</code> (só Pesquisa: Shopping não tem palavra-chave). A mesma palavra em correspondências diferentes aparece em linhas separadas. <strong>Conversões por palavra-chave são as conversões que o Ads credita, sem o filtro “só compra”</strong> (a fonte de palavra-chave não traz a categoria da conversão), por isso o nome é “conversões”, não “compras”. "
         "“Sem conversão” = 5+ cliques e nenhuma conversão na semana — com 1 semana isso é um <strong>alerta para olhar</strong> (a palavra é ampla e atrai busca fora do nosso público?), não sentença para negativar; "
         "confirme no histórico de 3–4 semanas e nos termos de pesquisa do painel (os termos digitados ainda não são extraídos para o BigQuery). Palavras “ampla” custam mais para revisar: são as que mais trazem termos fora do foco.")
