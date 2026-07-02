"""Relatório de Estoque — Shibari Brasil.

Regras de negócio e definição de cada indicador: ver specs/estoque.md.
Não altere cálculo/filtro sem antes ler (e, se preciso, atualizar) esse spec.
"""
import pandas as pd
import streamlit as st

from common import bigquery as bq
from common.design import inject_css, card, render_cards, section_title

DATASET = "dbt_dw_rpt"

TABELAS = {
    "produtos": "rpt_estoque_produtos",
}

CLASSIFICACOES_PROBLEMAS = ["a. 🆘 Estoque Rompido", "b. 🛑 Urgente"]


@st.cache_data(ttl=3600)
def carregar_dados():
    client = bq.get_client()
    return bq.query_tables(client, DATASET, TABELAS)


def _tabela_classificacao_risco(df_produtos, classificacoes=None):
    """Quantidade de produtos por classificação de risco, ordenada pela
    própria label (o prefixo de letra já ordena por severidade). Se
    `classificacoes` for informado, filtra só nelas — usado na v1 pra
    mostrar só Rompido/Urgente; passar None mostra todas (fase futura).
    Ver specs/estoque.md."""
    df = df_produtos
    if classificacoes is not None:
        df = df[df["ds_classificacao_risco"].isin(classificacoes)]

    contagem = df.groupby("ds_classificacao_risco").size().sort_index()

    return pd.DataFrame({
        "Classificação de Risco": contagem.index,
        "Quantidade de Produtos": contagem.values,
    })


def render():
    inject_css()

    with st.spinner("Carregando dados do BigQuery..."):
        try:
            dados = carregar_dados()
        except Exception as e:
            st.error(f"Erro ao carregar dados do BigQuery: {e}")
            return

    df = dados["produtos"]

    st.html(f"""
    <div class="report-header">
      <div>
        <div class="report-brand">shibari brasil · estoque</div>
        <div class="report-title">Estoque <span>—</span> Relatório</div>
        <div class="report-meta">Dados via BigQuery · {bq.PROJECT}</div>
      </div>
      <div class="report-badge">
        Estado atual do estoque<br>
        Atualizado de hora em hora
      </div>
    </div>
    """)

    section_title("Problemas")
    tabela_risco = _tabela_classificacao_risco(df, CLASSIFICACOES_PROBLEMAS)
    st.dataframe(tabela_risco, hide_index=True, use_container_width=True)

    em_risco = df[df["ds_classificacao_risco"].isin(CLASSIFICACOES_PROBLEMAS)]
    vl_em_risco = (em_risco["vl_custo_cadastro"] * em_risco["qt_estoque_atual"]).sum()

    section_title("Indicadores Gerais")
    vl_custo_total = (df["vl_custo_cadastro"] * df["qt_estoque_atual"]).sum()
    qt_pendente = int((df["qt_item_compra_pendente"] > 0).sum())

    render_cards([
        card("Custo Total dos Produtos de Estoque", f"R$ {vl_custo_total:,.2f}", variant="neutral"),
        card("Produtos com Estoque Pendente", f"{qt_pendente}", variant="neutral"),
        card("Valor Total em Risco", f"R$ {vl_em_risco:,.2f}",
             "custo dos produtos em Estoque Rompido + Urgente", variant="neutral"),
    ])
