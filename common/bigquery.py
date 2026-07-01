"""Client BigQuery e helpers de query compartilhados por todos os relatórios.

Extraído de app.py (relatório Google Ads) na Fase 9 da migração — ver
MIGRACAO-RELATORIOS.md e CLAUDE.md.

DATASET não é fixo aqui de propósito: cada relatório lê de um dataset/região
diferente (Google Ads fica em `dbt_dw_us_rpt`, região US; relatórios futuros
como Vendas/Financeiro devem usar `dbt_dw_rpt`, região us-east4 — ver
"Schema DBT target" em MIGRACAO-RELATORIOS.md). Cache (`@st.cache_data`) fica
por conta de cada relatório, não daqui, para não forçar o mesmo TTL em tudo.
"""
from google.oauth2.service_account import Credentials
from google.cloud import bigquery
import streamlit as st

PROJECT = "igneous-sandbox-381622"


def get_client():
    credentials = Credentials.from_service_account_info(
        dict(st.secrets["gcp"]),
        scopes=["https://www.googleapis.com/auth/bigquery"]
    )
    return bigquery.Client(credentials=credentials, project=PROJECT)


def query_table(client, dataset, tabela):
    return client.query(f"SELECT * FROM `{PROJECT}.{dataset}.{tabela}`").to_dataframe()


def query_tables(client, dataset, tabelas):
    """tabelas: dict {chave: nome_da_tabela_no_bq} -> dict {chave: DataFrame}"""
    return {chave: query_table(client, dataset, nome) for chave, nome in tabelas.items()}
