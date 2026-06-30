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
        st.dataframe(dados["performance_campanhas"])

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

        st.subheader("Orçamento")
        st.dataframe(dados["orcamento"])

        st.subheader("Top keywords")
        st.dataframe(dados["keywords_top"])

        st.subheader("Impression Share")
        st.dataframe(dados["impression_share"])

        st.subheader("Anúncios ativos")
        st.dataframe(dados["anuncios"])

    except Exception as e:
        st.error(f"Erro ao carregar dados: {e}")
