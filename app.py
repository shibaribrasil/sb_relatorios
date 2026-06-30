import streamlit as st
from google.oauth2.service_account import Credentials
from google.cloud import bigquery
import pandas as pd
import plotly.express as px

st.set_page_config(
    page_title="Relatório Google Ads — Shibari Brasil",
    page_icon="📊",
    layout="wide"
)

PROJECT = "igneous-sandbox-381622"
DATASET = "dbt_dw_us_rpt"


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


st.title("📊 Relatório Google Ads — Shibari Brasil")
st.caption("Dados dos últimos 30 dias · Atualizado de hora em hora")

with st.spinner("Carregando dados do BigQuery..."):
    try:
        dados = carregar_dados()
        r = dados["resumo_conta"].iloc[0]

        st.subheader("Visão geral — últimos 30 dias")
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Custo total",      f"R$ {r['vl_custo_total']:,.2f}")
        c2.metric("Cliques",          f"{int(r['qt_cliques_total']):,}")
        c3.metric("Conversões",       f"{r['qt_conversoes_total']:,.1f}")
        c4.metric("ROAS",             f"{r['vl_roas']:.2f}x")
        c5.metric("CPA",              f"R$ {r['vl_cpa']:,.2f}")

        c6, c7, c8 = st.columns(3)
        c6.metric("Impressões",       f"{int(r['qt_impressoes_total']):,}")
        c7.metric("CTR",              f"{r['pct_ctr']*100:.2f}%")
        c8.metric("CPC médio",        f"R$ {r['vl_cpc']:,.2f}")

        st.subheader("Performance por campanha")
        df_camp = dados["performance_campanhas"].sort_values("vl_custo_total", ascending=True)
        fig_camp = px.bar(
            df_camp, x="vl_custo_total", y="nm_campanha",
            orientation="h",
            labels={"vl_custo_total": "Custo (R$)", "nm_campanha": ""},
            color="vl_custo_total",
            color_continuous_scale="Purples",
            custom_data=["vl_roas", "vl_cpa", "pct_ctr", "qt_conversoes_total"],
        )
        fig_camp.update_traces(
            hovertemplate="<b>%{y}</b><br>Custo: R$ %{x:,.2f}<br>ROAS: %{customdata[0]:.2f}x<br>CPA: R$ %{customdata[1]:,.2f}<br>CTR: %{customdata[2]:.2%}<br>Conversões: %{customdata[3]:.1f}<extra></extra>",
            texttemplate="R$ %{x:,.2f}", textposition="outside"
        )
        fig_camp.update_layout(plot_bgcolor="white", coloraxis_showscale=False)
        st.plotly_chart(fig_camp, use_container_width=True)

        st.dataframe(
            df_camp[["nm_campanha", "vl_custo_total", "pct_ctr", "vl_roas", "vl_cpa", "qt_conversoes_total"]]
            .sort_values("vl_custo_total", ascending=False)
            .assign(
                vl_custo_total=df_camp["vl_custo_total"].apply(lambda v: f"R$ {v:,.2f}"),
                pct_ctr=df_camp["pct_ctr"].apply(lambda v: f"{v*100:.2f}%"),
                vl_roas=df_camp["vl_roas"].apply(lambda v: f"{v:.2f}x"),
                vl_cpa=df_camp["vl_cpa"].apply(lambda v: f"R$ {v:,.2f}"),
                qt_conversoes_total=df_camp["qt_conversoes_total"].apply(lambda v: f"{v:.1f}"),
            )
            .rename(columns={"nm_campanha": "Campanha", "vl_custo_total": "Custo", "pct_ctr": "CTR", "vl_roas": "ROAS", "vl_cpa": "CPA", "qt_conversoes_total": "Conversões"}),
            hide_index=True, use_container_width=True
        )

        st.subheader("Tendência diária — últimos 30 dias")
        df_tend = dados["tendencia_diaria"].sort_values("dt_data")
        fig_tend = px.line(
            df_tend, x="dt_data", y="vl_custo",
            labels={"dt_data": "Data", "vl_custo": "Custo (R$)"},
            markers=True
        )
        fig_tend.update_traces(line_shape="spline", line_color="#7B2FBE", line_width=2.5)
        fig_tend.update_layout(hovermode="x unified", plot_bgcolor="white", yaxis=dict(gridcolor="#f0f0f0"))
        st.plotly_chart(fig_tend, use_container_width=True)

        st.subheader("Conversões por tipo")
        st.dataframe(dados["conversoes_tipo"])

        st.subheader("Orçamento — budget vs gasto médio diário")
        df_orc = dados["orcamento"].sort_values("vl_gasto_total", ascending=False)
        df_orc_melt = df_orc.melt(
            id_vars="nm_campanha",
            value_vars=["vl_orcamento_diario", "vl_gasto_medio_diario"],
            var_name="tipo", value_name="valor"
        ).replace({"vl_orcamento_diario": "Budget diário", "vl_gasto_medio_diario": "Gasto médio diário"})
        fig_orc = px.bar(
            df_orc_melt, x="nm_campanha", y="valor", color="tipo",
            barmode="group",
            labels={"nm_campanha": "", "valor": "R$", "tipo": ""},
            color_discrete_map={"Budget diário": "#c4b0e0", "Gasto médio diário": "#7B2FBE"},
            custom_data=["tipo"],
        )
        fig_orc.update_traces(hovertemplate="<b>%{x}</b><br>%{customdata[0]}: R$ %{y:,.2f}<extra></extra>")
        fig_orc.update_layout(plot_bgcolor="white", legend=dict(orientation="h", y=1.1))
        st.plotly_chart(fig_orc, use_container_width=True)

        st.dataframe(
            df_orc[["nm_campanha", "vl_orcamento_diario", "vl_gasto_medio_diario", "pct_utilizacao_media", "vl_gasto_total"]]
            .assign(
                vl_orcamento_diario=df_orc["vl_orcamento_diario"].apply(lambda v: f"R$ {v:,.2f}"),
                vl_gasto_medio_diario=df_orc["vl_gasto_medio_diario"].apply(lambda v: f"R$ {v:,.2f}"),
                pct_utilizacao_media=df_orc["pct_utilizacao_media"].apply(lambda v: f"{v*100:.1f}%"),
                vl_gasto_total=df_orc["vl_gasto_total"].apply(lambda v: f"R$ {v:,.2f}"),
            )
            .rename(columns={"nm_campanha": "Campanha", "vl_orcamento_diario": "Budget diário", "vl_gasto_medio_diario": "Gasto médio", "pct_utilizacao_media": "Utilização", "vl_gasto_total": "Gasto total"}),
            hide_index=True, use_container_width=True
        )

        st.subheader("Top keywords")
        st.dataframe(dados["keywords_top"])

        st.subheader("Impression Share")
        st.dataframe(dados["impression_share"])

        st.subheader("Anúncios ativos")
        st.dataframe(dados["anuncios"])

    except Exception as e:
        st.error(f"Erro ao carregar dados: {e}")
