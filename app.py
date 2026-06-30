import streamlit as st
from google.oauth2.service_account import Credentials
from google.cloud import bigquery
import pandas as pd
import plotly.graph_objects as go

st.set_page_config(
    page_title="Relatório Google Ads — Shibari Brasil",
    page_icon="📊",
    layout="wide"
)

PROJECT = "igneous-sandbox-381622"
DATASET = "dbt_dw_us_rpt"

# ── Design System — mesmo tema do relatório HTML (sb_marketing_team/relatorios) ──
PLUM    = "#5b1e4b"
PLUM_DK = "#1c0c18"
SCARLET = "#d10f2f"
TAUPE   = "#8c6f68"
SKIN    = "#e6cfc3"
BG      = "#f4ece7"
OK      = "#1e7a4f"
OK_BG   = "#4caf82"
WARN    = "#8a6010"
WARN_BG = "#c9963a"
BAD     = "#d10f2f"
BORDER  = "rgba(91,30,75,0.18)"
GRID    = "rgba(91,30,75,0.10)"

CSS = f"""
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@600;700&family=Montserrat:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  .stApp {{ background: {BG}; font-family: 'Montserrat', sans-serif; }}
  .block-container {{ padding-top: 1.5rem; max-width: 1280px; }}
  h1, h2, h3 {{ font-family: 'Playfair Display', serif !important; }}

  .report-header {{
    background: {PLUM_DK}; border-bottom: 3px solid {SCARLET};
    padding: 22px 32px; border-radius: 10px; margin-bottom: 18px;
    display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px;
  }}
  .report-brand {{ font-size: 10px; font-weight: 600; letter-spacing: 0.16em; text-transform: uppercase; color: {TAUPE}; margin-bottom: 4px; }}
  .report-title {{ font-family: 'Playfair Display', serif; font-size: 22px; font-weight: 700; color: {BG}; }}
  .report-title span {{ color: {SCARLET}; }}
  .report-meta {{ color: {TAUPE}; font-size: 12px; margin-top: 3px; }}
  .report-badge {{
    background: rgba(244,236,231,0.08); border: 1px solid rgba(244,236,231,0.15); border-radius: 6px;
    padding: 8px 16px; font-size: 12px; color: {SKIN}; text-align: right; line-height: 1.8;
  }}
  .report-badge strong {{ color: {BG}; }}

  .section-title {{
    display: flex; align-items: center; gap: 10px; font-size: 11px; font-weight: 700;
    letter-spacing: 0.12em; text-transform: uppercase; color: {TAUPE};
    margin: 30px 0 14px 0; padding-bottom: 8px; border-bottom: 1px solid {BORDER};
  }}
  .section-title::before {{ content: ''; display: block; width: 3px; height: 14px; background: {SCARLET}; border-radius: 2px; }}

  .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px; margin-bottom: 6px; }}
  .card {{ background: #fff; border: 1px solid {BORDER}; border-radius: 10px; padding: 16px 18px; position: relative; box-shadow: 0 1px 4px rgba(91,30,75,0.06); }}
  .card::before {{ content: ''; position: absolute; top: 0; left: 0; right: 0; height: 3px; border-radius: 10px 10px 0 0; }}
  .card.c-neutral::before {{ background: {TAUPE}; }}
  .card.c-ok::before {{ background: {OK_BG}; }}
  .card.c-warn::before {{ background: {WARN_BG}; }}
  .card.c-bad::before {{ background: {BAD}; }}
  .c-label {{ font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.07em; color: {TAUPE}; margin-bottom: 8px; }}
  .c-value {{ font-size: 26px; font-weight: 700; font-family: 'Playfair Display', serif; line-height: 1; }}
  .card.c-neutral .c-value {{ color: #141419; }}
  .card.c-ok .c-value {{ color: {OK}; }}
  .card.c-warn .c-value {{ color: {WARN}; }}
  .card.c-bad .c-value {{ color: {BAD}; }}
  .c-sub {{ font-size: 11px; color: {TAUPE}; margin-top: 6px; }}
  .c-ref {{ font-size: 10px; color: {TAUPE}; margin-top: 2px; opacity: 0.7; }}

  .bench-row {{ display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 10px; }}
  .bench {{ background: rgba(91,30,75,0.06); border: 1px solid {BORDER}; border-radius: 5px; padding: 3px 10px; font-size: 10px; color: {TAUPE}; font-weight: 500; }}
  .bench strong {{ color: #141419; }}

  .note {{
    font-size: 12px; color: {TAUPE}; line-height: 1.6; margin-top: 6px;
    background: rgba(91,30,75,0.04); border: 1px solid {BORDER}; border-radius: 8px; padding: 10px 14px;
  }}
  .note strong {{ color: #141419; }}

  .tag {{ display: inline-block; padding: 2px 9px; border-radius: 4px; font-size: 10px; font-weight: 700; }}
  .t-ok    {{ background: rgba(76,175,130,0.14);  color: #1a6b40; }}
  .t-warn  {{ background: rgba(201,150,58,0.18);  color: #7a5208; }}
  .t-bad   {{ background: rgba(209,15,47,0.12);   color: {BAD}; }}
  .t-muted {{ background: rgba(140,111,104,0.14); color: #5e3e3a; }}
</style>
"""


def card(label, value, sub="", ref="", variant="neutral"):
    ref_html = f'<div class="c-ref">{ref}</div>' if ref else ""
    return f"""<div class="card c-{variant}">
        <div class="c-label">{label}</div>
        <div class="c-value">{value}</div>
        <div class="c-sub">{sub}</div>
        {ref_html}
    </div>"""


def render_cards(cards_html):
    st.html(f'<div class="cards">{"".join(cards_html)}</div>')


def section_title(text):
    st.html(f'<div class="section-title">{text}</div>')


def bench_row(items):
    spans = "".join(f'<div class="bench">{label}: <strong>{value}</strong></div>' for label, value in items)
    st.html(f'<div class="bench-row">{spans}</div>')


def note(html):
    st.html(f'<div class="note">{html}</div>')


def tag(text, variant):
    return f'<span class="tag t-{variant}">{text}</span>'


def roas_variant(v):
    if v >= 3:
        return "ok"
    if v >= 2:
        return "warn"
    return "bad"


def util_variant(pct):
    if pct >= 1.0:
        return "bad"
    if pct < 0.7:
        return "muted"
    return "warn"


def qs_variant(qs):
    if qs >= 9:
        return "ok"
    if qs >= 7:
        return "warn"
    return "bad"


def plotly_layout(fig, **kwargs):
    layout = dict(
        plot_bgcolor="white", paper_bgcolor="white",
        font=dict(family="Montserrat, sans-serif", color=TAUPE, size=12),
        margin=dict(l=10, r=10, t=10, b=10),
        xaxis=dict(gridcolor=GRID), yaxis=dict(gridcolor=GRID),
        legend=dict(orientation="h", y=1.12, font=dict(size=11)),
    )
    layout.update(kwargs)
    fig.update_layout(**layout)
    return fig


@st.cache_data(ttl=3600)
def carregar_dados():
    credentials = Credentials.from_service_account_info(
        dict(st.secrets["gcp"]),
        scopes=["https://www.googleapis.com/auth/bigquery"]
    )
    client = bigquery.Client(credentials=credentials, project=PROJECT)

    def query(tabela):
        return client.query(f"SELECT * FROM `{PROJECT}.{DATASET}.{tabela}`").to_dataframe()

    return {
        "resumo_conta":          query("rpt_gads_resumo_conta"),
        "performance_campanhas": query("rpt_gads_performance_campanhas"),
        "tendencia_diaria":      query("rpt_gads_tendencia_diaria"),
        "conversoes_tipo":       query("rpt_gads_conversoes_tipo"),
        "orcamento":             query("rpt_gads_orcamento"),
        "keywords_top":          query("rpt_gads_keywords_top"),
        "impression_share":      query("rpt_gads_impression_share"),
        "anuncios":              query("rpt_gads_anuncios"),
    }


st.html(CSS)

with st.spinner("Carregando dados do BigQuery..."):
    try:
        dados = carregar_dados()
        r = dados["resumo_conta"].iloc[0]
        periodo_ini = pd.to_datetime(r["dt_inicio_periodo"]).strftime("%d/%m/%Y")
        periodo_fim = pd.to_datetime(r["dt_fim_periodo"]).strftime("%d/%m/%Y")
        receita = r["vl_conversoes_total"]
        custo = r["vl_custo_total"]
        roi = (receita - custo) / custo if custo else 0

        st.html(f"""
        <div class="report-header">
          <div>
            <div class="report-brand">shibari brasil · tráfego pago</div>
            <div class="report-title">Google Ads <span>—</span> Relatório</div>
            <div class="report-meta">Dados via BigQuery · {PROJECT}</div>
          </div>
          <div class="report-badge">
            Período: <strong>{periodo_ini} → {periodo_fim}</strong><br>
            Atualizado de hora em hora
          </div>
        </div>
        """)

        # ═══ 1 — SAÚDE FINANCEIRA ═══
        section_title("1 — Saúde Financeira")
        render_cards([
            card("Custo Total", f"R$ {custo:,.2f}", "últimos 30 dias", variant="neutral"),
            card("Receita Gerada", f"R$ {receita:,.2f}", "valor de conversão total", variant="neutral"),
            card("ROI", f"{roi*100:.0f}%", "(receita − gasto) ÷ gasto", "meta: > 100%", variant="ok" if roi >= 1 else ("warn" if roi >= 0 else "bad")),
            card("ROAS Médio", f"{r['vl_roas']:.2f}×", "receita ÷ custo", "meta: 3–5× · mínimo: 2×", variant=roas_variant(r["vl_roas"])),
            card("CPA Médio", f"R$ {r['vl_cpa']:,.2f}", f"{r['qt_conversoes_total']:,.1f} conversões", variant="neutral"),
        ])
        render_cards([
            card("Impressões", f"{int(r['qt_impressoes_total']):,}", variant="neutral"),
            card("Cliques", f"{int(r['qt_cliques_total']):,}", variant="neutral"),
            card("CTR", f"{r['pct_ctr']*100:.2f}%", "benchmark: 2–6%+", variant="neutral"),
            card("CPC Médio", f"R$ {r['vl_cpc']:,.2f}", variant="neutral"),
        ])
        note("<strong>Como ler:</strong> ROI considera só o gasto de mídia — não inclui custo do produto. "
             "ROAS abaixo de 2× indica campanha no prejuízo considerando margem; entre 2× e 3× está na zona de atenção; acima de 3× está saudável.")

        st.subheader("")
        col1, col2 = st.columns(2)
        df_camp = dados["performance_campanhas"].sort_values("vl_custo_total", ascending=False)

        with col1:
            with st.container(border=True):
                st.html('<div class="c-label" style="margin-bottom:10px">ROAS por Campanha</div>')
                bench_row([("Meta", "3–5×"), ("Mínimo", "2×"), ("Crítico", "< 2×")])
                fig = go.Figure()
                cores = [{"ok": OK_BG, "warn": WARN_BG, "bad": BAD}[roas_variant(v)] for v in df_camp["vl_roas"]]
                fig.add_bar(x=df_camp["nm_campanha"], y=df_camp["vl_roas"], marker_color=cores,
                            text=[f"{v:.2f}×" for v in df_camp["vl_roas"]], textposition="outside")
                fig.add_hline(y=2, line_dash="dash", line_color=WARN_BG, annotation_text="mínimo 2×", annotation_font_size=10)
                plotly_layout(fig, showlegend=False, height=300, yaxis=dict(gridcolor=GRID, ticksuffix="×"))
                st.plotly_chart(fig, use_container_width=True)
                note("Barras abaixo da linha tracejada estão perdendo dinheiro considerando margem mínima de 2×.")

        with col2:
            with st.container(border=True):
                st.html('<div class="c-label" style="margin-bottom:10px">Investido vs. Receita por Campanha</div>')
                bench_row([("Barras iguais", "= ROAS 1× (empate)")])
                fig = go.Figure()
                fig.add_bar(name="Investido", x=df_camp["nm_campanha"], y=df_camp["vl_custo_total"], marker_color=PLUM)
                fig.add_bar(name="Receita", x=df_camp["nm_campanha"], y=df_camp["vl_conversoes_total"], marker_color=SCARLET)
                plotly_layout(fig, barmode="group", height=300, yaxis=dict(gridcolor=GRID, tickprefix="R$"))
                st.plotly_chart(fig, use_container_width=True)
                note("Quando a barra de receita (vermelha) é menor que a de investido (roxa), a campanha está no prejuízo no período.")

        # ═══ 2 — PERFORMANCE POR CAMPANHA ═══
        section_title("2 — Performance por Campanha")
        with st.container(border=True):
            df_tab = df_camp.copy()
            df_tab["pct_conv_rate"] = df_tab["qt_conversoes_total"] / df_tab["qt_cliques_total"]
            tabela = pd.DataFrame({
                "Campanha": df_tab["nm_campanha"],
                "Tipo": df_tab["ds_tipo_canal"],
                "Status": df_tab["ds_status_campanha"],
                "Investido": df_tab["vl_custo_total"].apply(lambda v: f"R$ {v:,.2f}"),
                "Impr.": df_tab["qt_impressoes_total"].apply(lambda v: f"{int(v):,}"),
                "Cliques": df_tab["qt_cliques_total"].apply(lambda v: f"{int(v):,}"),
                "CTR": df_tab["pct_ctr"].apply(lambda v: f"{v*100:.2f}%"),
                "CPC": df_tab["vl_cpc"].apply(lambda v: f"R$ {v:,.2f}"),
                "Conv. Rate": df_tab["pct_conv_rate"].apply(lambda v: f"{v*100:.2f}%" if v == v else "—"),
                "Conversões": df_tab["qt_conversoes_total"].apply(lambda v: f"{v:.1f}"),
                "CPA": df_tab["vl_cpa"].apply(lambda v: f"R$ {v:,.2f}" if v == v else "—"),
                "Receita": df_tab["vl_conversoes_total"].apply(lambda v: f"R$ {v:,.2f}"),
                "ROAS": df_tab["vl_roas"],
            })
            styled = tabela.style.applymap(
                lambda v: f"color: {OK}; font-weight:700" if v >= 3 else (f"color: {WARN}; font-weight:700" if v >= 2 else f"color: {BAD}; font-weight:700"),
                subset=["ROAS"]
            ).format({"ROAS": "{:.2f}×"})
            st.dataframe(styled, hide_index=True, use_container_width=True)
            note("<strong>Benchmarks:</strong> CTR Search &gt;2% aceitável / &gt;6% bom · Conv. Rate e-commerce &gt;1% mínimo / &gt;3% bom · "
                 "ROAS mínimo 2× / meta 3–5×.")

        # ═══ 3 — TENDÊNCIA + CONVERSÕES ═══
        section_title("3 — Tendência Temporal e Conversões")
        col3, col4 = st.columns([3, 2])
        with col3:
            with st.container(border=True):
                st.html('<div class="c-label" style="margin-bottom:10px">Gasto diário e Cliques — últimos 30 dias</div>')
                df_tend = dados["tendencia_diaria"].sort_values("dt_data")
                fig = go.Figure()
                fig.add_trace(go.Scatter(x=df_tend["dt_data"], y=df_tend["vl_custo"], name="Gasto (R$)",
                                          line=dict(color=SCARLET, width=2.5, shape="spline"), fill="tozeroy",
                                          fillcolor="rgba(209,15,47,0.06)", yaxis="y"))
                fig.add_trace(go.Scatter(x=df_tend["dt_data"], y=df_tend["qt_cliques"], name="Cliques",
                                          line=dict(color=PLUM, width=2, dash="dash"), yaxis="y2"))
                plotly_layout(fig, height=300, hovermode="x unified",
                              yaxis=dict(gridcolor=GRID, tickprefix="R$", title=None),
                              yaxis2=dict(overlaying="y", side="right", showgrid=False, title=None))
                st.plotly_chart(fig, use_container_width=True)
                note("Gasto subindo com cliques estáveis = CPC subindo (leilão mais competitivo). Cliques caindo com gasto constante geralmente indica perda de impression share.")

        with col4:
            with st.container(border=True):
                st.html('<div class="c-label" style="margin-bottom:10px">Conversões por Tipo</div>')
                df_conv = dados["conversoes_tipo"].sort_values("qt_conversoes_total", ascending=False)
                fig = go.Figure(go.Pie(labels=df_conv["nm_acao_conversao"], values=df_conv["qt_conversoes_total"],
                                        hole=0.5, marker_colors=[PLUM, SCARLET, WARN_BG, TAUPE, SKIN],
                                        textinfo="percent"))
                plotly_layout(fig, height=220, showlegend=True, legend=dict(orientation="v", y=0.5, font=dict(size=10)))
                st.plotly_chart(fig, use_container_width=True)
                st.dataframe(
                    df_conv[["nm_acao_conversao", "ds_categoria_conversao", "qt_conversoes_total", "vl_conversoes_total"]]
                    .assign(
                        qt_conversoes_total=df_conv["qt_conversoes_total"].apply(lambda v: f"{v:.1f}"),
                        vl_conversoes_total=df_conv["vl_conversoes_total"].apply(lambda v: f"R$ {v:,.2f}"),
                    )
                    .rename(columns={"nm_acao_conversao": "Ação", "ds_categoria_conversao": "Categoria",
                                      "qt_conversoes_total": "Qtd", "vl_conversoes_total": "Valor"}),
                    hide_index=True, use_container_width=True
                )
                note("Conversões de categoria diferente de \"compra\" (ex.: visualização de página) não são receita real — não devem entrar no cálculo de ROAS.")

        # ═══ 4 — ORÇAMENTO ═══
        section_title("4 — Orçamento: Budget vs. Gasto Médio Diário")
        with st.container(border=True):
            df_orc = dados["orcamento"].sort_values("vl_gasto_total", ascending=False)
            fig = go.Figure()
            fig.add_bar(name="Budget diário", x=df_orc["nm_campanha"], y=df_orc["vl_orcamento_diario"], marker_color=SKIN)
            fig.add_bar(name="Gasto médio diário", x=df_orc["nm_campanha"], y=df_orc["vl_gasto_medio_diario"], marker_color=PLUM)
            plotly_layout(fig, barmode="group", height=300, yaxis=dict(gridcolor=GRID, tickprefix="R$"))
            st.plotly_chart(fig, use_container_width=True)

            tabela_orc = pd.DataFrame({
                "Campanha": df_orc["nm_campanha"],
                "Status": df_orc["ds_status_campanha"],
                "Budget diário": df_orc["vl_orcamento_diario"].apply(lambda v: f"R$ {v:,.2f}"),
                "Gasto médio": df_orc["vl_gasto_medio_diario"].apply(lambda v: f"R$ {v:,.2f}"),
                "Utilização": df_orc["pct_utilizacao_media"],
                "Gasto total": df_orc["vl_gasto_total"].apply(lambda v: f"R$ {v:,.2f}"),
            })
            styled_orc = tabela_orc.style.applymap(
                lambda v: f"color: {BAD}; font-weight:700" if v >= 1.0 else (f"color: {WARN}; font-weight:700" if v >= 0.7 else f"color: {TAUPE}"),
                subset=["Utilização"]
            ).format({"Utilização": "{:.0%}"})
            st.dataframe(styled_orc, hide_index=True, use_container_width=True)
            note("<strong>Como ler:</strong> Utilização ≥ 100% = campanha <strong>limitada por orçamento</strong> — está perdendo impressões/cliques que poderiam converter; "
                 "70–99% é normal; abaixo de 70% = orçamento subutilizado (pode ter espaço para aumentar lance ou indicar audiência pequena).")

        # ═══ 5 — KEYWORDS ═══
        section_title("5 — Top Keywords por Gasto")
        with st.container(border=True):
            bench_row([("QS meta", "≥ 7"), ("Aceitável", "5–6"), ("Crítico", "< 5"), ("Perfeito", "QS 10")])
            df_kw = dados["keywords_top"].sort_values("vl_custo_total", ascending=False)
            tabela_kw = pd.DataFrame({
                "Keyword": df_kw["ds_keyword"],
                "Correspondência": df_kw["ds_correspondencia"],
                "Campanha": df_kw["nm_campanha"],
                "Grupo": df_kw["nm_grupo_anuncio"],
                "Custo": df_kw["vl_custo_total"].apply(lambda v: f"R$ {v:,.2f}"),
                "Cliques": df_kw["qt_cliques_total"].apply(lambda v: f"{int(v):,}"),
                "CTR": df_kw["pct_ctr"].apply(lambda v: f"{v*100:.2f}%"),
                "CPC": df_kw["vl_cpc"].apply(lambda v: f"R$ {v:,.2f}"),
                "Conversões": df_kw["qt_conversoes_total"].apply(lambda v: f"{v:.1f}"),
                "CPA": df_kw["vl_cpa"].apply(lambda v: f"R$ {v:,.2f}" if v == v else "—"),
                "QS": df_kw["nr_quality_score"],
                "CTR Previsto": df_kw["ds_ctr_previsto"],
                "Relevância": df_kw["ds_relevancia_anuncio"],
                "Exp. LP": df_kw["ds_experiencia_lp"],
            })
            styled_kw = tabela_kw.style.applymap(
                lambda v: f"color: {OK}; font-weight:700" if v >= 9 else (f"color: {WARN}; font-weight:700" if v >= 7 else f"color: {BAD}; font-weight:700"),
                subset=["QS"]
            )
            st.dataframe(styled_kw, hide_index=True, use_container_width=True)
            note("QS abaixo de 5 normalmente eleva o CPC e reduz a posição no leilão — priorize melhorar anúncio/landing page dessas keywords antes de aumentar lance.")

        # ═══ 6 — IMPRESSION SHARE ═══
        section_title("6 — Impression Share por Campanha")
        with st.container(border=True):
            df_is = dados["impression_share"].sort_values("pct_impression_share", ascending=False)
            fig = go.Figure()
            fig.add_bar(name="IS conquistado", y=df_is["nm_campanha"], x=df_is["pct_impression_share"], orientation="h", marker_color=PLUM)
            fig.add_bar(name="Perda por budget", y=df_is["nm_campanha"], x=df_is["pct_perda_budget"], orientation="h", marker_color="#e0b0ff")
            fig.add_bar(name="Perda por ranking", y=df_is["nm_campanha"], x=df_is["pct_perda_ranking"], orientation="h", marker_color="#f5e6ff")
            plotly_layout(fig, barmode="stack", height=300, xaxis=dict(gridcolor=GRID, tickformat=".0%"))
            st.plotly_chart(fig, use_container_width=True)
            note("<strong>Perda por budget</strong> se resolve aumentando orçamento. <strong>Perda por ranking</strong> se resolve melhorando Quality Score ou lance — são diagnósticos opostos, não confundir.")

        # ═══ 7 — ANÚNCIOS ATIVOS ═══
        section_title("7 — Anúncios Ativos")
        with st.container(border=True):
            df_ads = dados["anuncios"]
            st.dataframe(
                df_ads[["nm_campanha", "nm_grupo_anuncio", "ds_tipo_anuncio", "nm_anuncio",
                        "ds_forca_anuncio", "ds_status_aprovacao", "ds_url_final"]]
                .rename(columns={
                    "nm_campanha": "Campanha", "nm_grupo_anuncio": "Grupo", "ds_tipo_anuncio": "Tipo",
                    "nm_anuncio": "Nome", "ds_forca_anuncio": "Força", "ds_status_aprovacao": "Aprovação",
                    "ds_url_final": "URL",
                }),
                hide_index=True, use_container_width=True
            )
            note("Anúncios com força \"Ruim\" ou \"Regular\" perdem posição no leilão — adicionar mais variações de título/descrição costuma elevar para \"Boa\"/\"Excelente\".")

    except Exception as e:
        st.error(f"Erro ao carregar dados: {e}")
