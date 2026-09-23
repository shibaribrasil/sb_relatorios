"""Tarefas do SAC (e futuras listas com check de execução) — leitura e escrita.

Fonte: `raw_control.sac_tarefas`. Essa tabela **não é gerenciada pelo dbt** — nenhum model do
sb_dw_dbt a referencia, e o job horário (`dbt run`) nunca a recria nem a apaga. É por isso que o
que o usuário marca ou desmarca aqui permanece entre as atualizações de dados: a cada carga, as
páginas só fazem um LEFT JOIN desta tabela (pela chave estável do item, ex.: cd_codigo_interno)
com os dados novos que vêm do dbt — a tabela de tarefas em si nunca é tocada pela carga.

Uma linha = 1 item de 1 tipo de tarefa (`tipo_tarefa` + `chave`). Uso: `reports/sac.py`.
"""
from datetime import datetime, timezone

import pandas as pd
import streamlit as st
from google.cloud import bigquery

from common import bigquery as bq

TABELA = f"{bq.PROJECT}.raw_control.sac_tarefas"


@st.cache_data(ttl=60)
def carregar_tarefas(tipo_tarefa: str) -> pd.DataFrame:
    """1 linha por item já marcado/desmarcado desse tipo (chave, fg_feito, dt_atualizacao)."""
    client = bq.get_client()
    job = client.query(
        f"SELECT chave, fg_feito, ds_observacao, dt_atualizacao FROM `{TABELA}` WHERE tipo_tarefa = @tipo",
        job_config=bigquery.QueryJobConfig(query_parameters=[bigquery.ScalarQueryParameter("tipo", "STRING", tipo_tarefa)]),
    )
    df = job.result().to_dataframe()
    if not df.empty:
        df["dt_atualizacao"] = pd.to_datetime(df["dt_atualizacao"])
    return df


def marcar_tarefa(tipo_tarefa: str, chave: str, fg_feito: bool, observacao: str = ""):
    """Grava (upsert) o estado de um item. Não identifica quem marcou (decisão do Hugo, 23/set/2026) —
    `nm_responsavel` fica na tabela para uso futuro, mas sempre vazio. Chama carregar_tarefas.clear()
    depois, para a página já mostrar o valor novo no mesmo rerun."""
    client = bq.get_client()
    job = client.query(
        f"""
        MERGE `{TABELA}` T
        USING (SELECT @tipo AS tipo_tarefa, @chave AS chave) S
           ON T.tipo_tarefa = S.tipo_tarefa AND T.chave = S.chave
         WHEN MATCHED THEN UPDATE SET
              fg_feito = @feito, ds_observacao = @obs, dt_atualizacao = @agora
         WHEN NOT MATCHED THEN
           INSERT (tipo_tarefa, chave, fg_feito, ds_observacao, dt_criacao, dt_atualizacao)
           VALUES (@tipo, @chave, @feito, @obs, @agora, @agora)
        """,
        job_config=bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("tipo", "STRING", tipo_tarefa),
            bigquery.ScalarQueryParameter("chave", "STRING", str(chave)),
            bigquery.ScalarQueryParameter("feito", "BOOL", bool(fg_feito)),
            bigquery.ScalarQueryParameter("obs", "STRING", observacao or ""),
            bigquery.ScalarQueryParameter("agora", "TIMESTAMP", datetime.now(timezone.utc)),
        ]),
    )
    job.result()
    carregar_tarefas.clear()
