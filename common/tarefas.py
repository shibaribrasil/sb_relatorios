"""Tarefas do SAC (e futuras listas com check de execução) — leitura e escrita.

Fonte: `raw_control.sac_tarefas`. Essa tabela **não é gerenciada pelo dbt** — nenhum model do
sb_dw_dbt a referencia, e o job horário (`dbt run`) nunca a recria nem a apaga. É por isso que o
que o usuário marca ou desmarca aqui permanece entre as atualizações de dados: a cada carga, as
páginas só fazem um LEFT JOIN desta tabela (pela chave estável do item, ex.: cd_codigo_interno)
com os dados novos que vêm do dbt — a tabela de tarefas em si nunca é tocada pela carga.

Uma linha = 1 item de 1 tipo de tarefa (`tipo_tarefa` + `chave`), com o estado ATUAL do check
(`fg_feito`) e do resultado do contato (`ds_resultado`). O resultado pode ser trocado quantas vezes
for preciso; cada gravação também entra em `raw_control.sac_tarefas_historico` (append-only), para
dar para ver a evolução (ex.: "vai pensar" → "comprou"). Uso: `reports/sac.py`.
"""
from datetime import datetime, timezone

import pandas as pd
import streamlit as st
from google.cloud import bigquery

from common import bigquery as bq

TABELA = f"{bq.PROJECT}.raw_control.sac_tarefas"
HISTORICO = f"{bq.PROJECT}.raw_control.sac_tarefas_historico"


@st.cache_data(ttl=60)
def carregar_tarefas(tipo_tarefa: str) -> pd.DataFrame:
    """1 linha por item já marcado/desmarcado desse tipo (chave, fg_feito, ds_resultado, dt_atualizacao)."""
    client = bq.get_client()
    job = client.query(
        f"SELECT chave, fg_feito, ds_resultado, ds_observacao, dt_atualizacao FROM `{TABELA}` WHERE tipo_tarefa = @tipo",
        job_config=bigquery.QueryJobConfig(query_parameters=[bigquery.ScalarQueryParameter("tipo", "STRING", tipo_tarefa)]),
    )
    df = job.result().to_dataframe()
    if not df.empty:
        df["dt_atualizacao"] = pd.to_datetime(df["dt_atualizacao"])
        # dois cliques quase simultâneos podem inserir a mesma chave 2x (MERGE concorrente): vale o estado mais recente
        df = df.sort_values("dt_atualizacao").drop_duplicates("chave", keep="last")
    return df


def salvar_tarefas(tipo_tarefa: str, itens: list[tuple[str, bool, str | None, str | None]]):
    """Grava (upsert) o estado atual de VÁRIOS itens de uma vez — cada item = (chave, fg_feito, resultado, observacao),
    check, resultado e observação juntos — num único MERGE, e registra as mudanças no histórico. A página só chama isto
    quando o usuário clica em "Salvar alterações": trocas intermediárias na tela não geram gravação nem
    histórico, e um único comando evita gravações concorrentes (que duplicavam a chave). Não identifica quem
    marcou (decisão do Hugo, 23/set/2026) — `nm_responsavel` fica na tabela para uso futuro, mas sempre vazio.
    Chama carregar_tarefas.clear() depois, para a página já mostrar o valor novo no mesmo rerun."""
    if not itens:
        return
    client = bq.get_client()
    agora = datetime.now(timezone.utc)
    linhas = [
        bigquery.StructQueryParameter(
            None,
            bigquery.ScalarQueryParameter("chave", "STRING", str(chave)),
            bigquery.ScalarQueryParameter("feito", "BOOL", bool(feito)),
            bigquery.ScalarQueryParameter("resultado", "STRING", resultado or None),
            bigquery.ScalarQueryParameter("observacao", "STRING", (observacao or "").strip()),
        )
        for chave, feito, resultado, observacao in itens
    ]
    params = [
        bigquery.ScalarQueryParameter("tipo", "STRING", tipo_tarefa),
        bigquery.ScalarQueryParameter("agora", "TIMESTAMP", agora),
        bigquery.ArrayQueryParameter("itens", "STRUCT", linhas),
    ]
    client.query(
        f"""
        MERGE `{TABELA}` T
        USING (SELECT @tipo AS tipo_tarefa, i.chave, i.feito, i.resultado, i.observacao FROM UNNEST(@itens) i) S
           ON T.tipo_tarefa = S.tipo_tarefa AND T.chave = S.chave
         WHEN MATCHED THEN UPDATE SET
              fg_feito = S.feito, ds_resultado = S.resultado, ds_observacao = S.observacao, dt_atualizacao = @agora
         WHEN NOT MATCHED THEN
           INSERT (tipo_tarefa, chave, fg_feito, ds_resultado, ds_observacao, dt_criacao, dt_atualizacao)
           VALUES (S.tipo_tarefa, S.chave, S.feito, S.resultado, S.observacao, @agora, @agora);
        INSERT INTO `{HISTORICO}` (tipo_tarefa, chave, fg_feito, ds_resultado, ds_observacao, dt_evento)
        SELECT @tipo, i.chave, i.feito, i.resultado, i.observacao, @agora FROM UNNEST(@itens) i;
        """,
        job_config=bigquery.QueryJobConfig(query_parameters=params),
    ).result()
    carregar_tarefas.clear()
