"""Relatório de Vendas — Shibari Brasil.

Regras de negócio e definição de cada indicador: ver specs/vendas.md.
Não altere cálculo/filtro sem antes ler (e, se preciso, atualizar) esse spec.
"""
from datetime import datetime, timedelta, timezone

import pandas as pd
import streamlit as st

from common import bigquery as bq
from common.design import inject_css, card, render_cards, section_title

DATASET = "dbt_dw_rpt"

TABELAS = {
    "vendas_dia": "rpt_vendas_dia",
}

BRT = timezone(timedelta(hours=-3))  # Brasil não observa horário de verão desde 2019 — offset fixo


@st.cache_data(ttl=3600)
def carregar_dados():
    client = bq.get_client()
    return bq.query_tables(client, DATASET, TABELAS)


def _hoje_brt():
    return datetime.now(BRT).date()


def _linha_do_dia(df, data):
    """Bloco 'Hoje' é sempre a data corrente (BRT) — nunca é afetado por
    filtro de mês, mesmo quando a seção de mês agregado (fase futura)
    estiver olhando um mês diferente. Ver specs/vendas.md."""
    linhas = df[pd.to_datetime(df["dt_data"]).dt.date == data]
    return linhas.iloc[0] if not linhas.empty else None


def render():
    inject_css()

    with st.spinner("Carregando dados do BigQuery..."):
        try:
            dados = carregar_dados()
        except Exception as e:
            st.error(f"Erro ao carregar dados do BigQuery: {e}")
            return

    hoje = _hoje_brt()
    linha = _linha_do_dia(dados["vendas_dia"], hoje)

    st.html(f"""
    <div class="report-header">
      <div>
        <div class="report-brand">shibari brasil · vendas</div>
        <div class="report-title">Vendas <span>—</span> Relatório</div>
        <div class="report-meta">Dados via BigQuery · {bq.PROJECT}</div>
      </div>
      <div class="report-badge">
        Hoje: <strong>{hoje.strftime("%d/%m/%Y")}</strong><br>
        Atualizado de hora em hora
      </div>
    </div>
    """)

    section_title("Hoje")

    if linha is None:
        st.info(
            "Sem dados ainda hoje — a tabela `rpt_vendas_dia` ainda não tem uma linha "
            "para a data de hoje (provável atraso da próxima atualização horária)."
        )
        return

    meta_dia = linha["vl_meta_dia"]
    faturamento = linha["vl_faturamento_bruto"] or 0
    faturamento_liquido = linha["vl_faturamento_liquido"] or 0
    lucro = linha["vl_lucro_bruto"] or 0
    qt_pedidos = int(linha["qt_pedidos"] or 0)
    qt_item = int(linha["qt_item"] or 0)

    tem_meta = pd.notna(meta_dia) and meta_dia
    atingimento = (faturamento / meta_dia) if tem_meta else None
    margem = (lucro / faturamento_liquido) if faturamento_liquido else None

    render_cards([
        card("Meta do Dia",
             f"R$ {meta_dia:,.2f}" if tem_meta else "sem meta cadastrada",
             "meta do mês ÷ dias do mês", variant="neutral"),
        card("Faturamento Bruto", f"R$ {faturamento:,.2f}",
             f"{qt_item} item(ns) vendido(s) hoje", variant="neutral"),
        card("Atingimento da Meta",
             f"{atingimento * 100:.0f}%" if atingimento is not None else "—",
             "faturamento bruto ÷ meta do dia", variant="neutral"),
        card("Pedidos", f"{qt_pedidos}", variant="neutral"),
        card("Margem de Lucro",
             f"{margem * 100:.1f}%" if margem is not None else "—",
             "lucro ÷ faturamento líquido", variant="neutral"),
        card("Lucro", f"R$ {lucro:,.2f}",
             "faturamento líquido − custo de mercadoria", variant="neutral"),
    ])
