"""Dados de logística compartilhados pelas páginas (Pulso do Dia, Vendas da Semana).

Fonte: `dbt_dw_az.tb_logistica_pedido` (1 linha por pedido). Toda regra (situação, atraso, problema de entrega,
fila do SAC) vive no dbt; aqui só se lê. Ver specs/pulso-dia.md e specs/vendas-semana.md.
"""
import pandas as pd
import streamlit as st

from common import bigquery as bq

COLUNAS = """
    cd_codigo_interno, cd_pedido_nuvemshop, cd_pedido, nm_cliente, dt_pedido, dt_pagamento, dt_expedicao, dt_entrega,
    ds_situacao_logistica, ds_ultimo_evento, qt_dias_sem_movimento, nm_transportadora,
    qt_dias_ate_expedicao, qt_dias_transito, qt_dias_pedido_a_entrega, fg_entregue_no_prazo, qt_dias_atraso_entrega,
    fg_entrega_confirmada, fg_atrasado_em_aberto, fg_problema_entrega_ativo, fg_acao_sac, fg_tratado_sac, fg_cancelado
"""
INICIO = "2026-01-01"


@st.cache_data(ttl=600)
def carregar_logistica():
    client = bq.get_client()
    df = bq.query_df(client, f"""
        SELECT {COLUNAS} FROM `{bq.PROJECT}.dbt_dw_az.tb_logistica_pedido`
         WHERE dt_pedido >= DATE '{INICIO}' AND NOT COALESCE(fg_cancelado, FALSE)
    """)
    for c in ["dt_pedido", "dt_pagamento", "dt_expedicao", "dt_entrega"]:
        df[c] = pd.to_datetime(df[c])
    for c in ["qt_dias_ate_expedicao", "qt_dias_transito", "qt_dias_pedido_a_entrega", "qt_dias_sem_movimento", "qt_dias_atraso_entrega"]:
        df[c] = pd.to_numeric(df[c])
    for c in ["fg_entrega_confirmada", "fg_atrasado_em_aberto", "fg_problema_entrega_ativo", "fg_acao_sac", "fg_tratado_sac"]:
        df[c] = df[c].fillna(False).astype(bool)
    return df
