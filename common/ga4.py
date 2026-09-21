"""Dados do GA4 compartilhados pelas páginas (Site & Funil, Vendas & Margem, Canais & Unit Economics).

Fonte: `dbt_dw_us_az.tb_ga4_canal_diario` e `tb_ga4_funil` (região US — consulta separada, não junta com a `az` de
us-east4). O histórico começa em 01/07/2026 (correção do Data Transfer): períodos anteriores ficam sem sessões.
"""
import pandas as pd
import streamlit as st

from common import bigquery as bq

INICIO_GA4 = pd.Timestamp("2026-07-01")


@st.cache_data(ttl=900)
def carregar_ga4():
    client = bq.get_client()
    canal = bq.query_df(client, f"""
        SELECT dt_data, COALESCE(ds_canal, '(não atribuído)') AS ds_canal, ds_canal_fonte, ds_canal_meio,
               qt_sessoes, qt_usuarios, qt_sessoes_engajadas, qt_conversoes, vl_receita
          FROM `{bq.PROJECT}.dbt_dw_us_az.tb_ga4_canal_diario`
    """)
    funil = bq.query_df(client, f"""
        SELECT dt_data, ds_etapa_funil, qt_ocorrencias FROM `{bq.PROJECT}.dbt_dw_us_az.tb_ga4_funil`
    """)
    for df in (canal, funil):
        df["dt_data"] = pd.to_datetime(df["dt_data"])
    for c in ["qt_sessoes", "qt_usuarios", "qt_sessoes_engajadas", "qt_conversoes", "vl_receita"]:
        canal[c] = pd.to_numeric(canal[c]).fillna(0.0)
    funil["qt_ocorrencias"] = pd.to_numeric(funil["qt_ocorrencias"]).fillna(0.0)
    return {"canal": canal, "funil": funil}


def sessoes(canal, ini, fim):
    """Total de sessões do GA4 no intervalo [ini, fim]; None se o intervalo começa antes de o GA4 ter histórico."""
    ini, fim = pd.Timestamp(ini), pd.Timestamp(fim)
    if fim < INICIO_GA4:
        return None
    x = canal[(canal["dt_data"] >= ini) & (canal["dt_data"] <= fim)]
    return float(x["qt_sessoes"].sum())
