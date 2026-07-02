"""Relatório de Vendas — Shibari Brasil.

Regras de negócio e definição de cada indicador: ver specs/vendas.md.
Não altere cálculo/filtro sem antes ler (e, se preciso, atualizar) esse spec.
"""
from datetime import datetime, timedelta, timezone

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from common import bigquery as bq
from common.design import (
    inject_css, card, render_cards, section_title, note,
    PLUM, TAUPE, OK_BG, BAD, GRID, plotly_layout,
    CATEGORICAL_PALETTE, MUTED,
)

DATASET = "dbt_dw_rpt"

TABELAS = {
    "vendas_dia": "rpt_vendas_dia",
    "pedidos": "rpt_vendas_pedidos",
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
    filtro de mês, mesmo quando a seção de mês agregado estiver olhando um
    mês diferente. Ver specs/vendas.md."""
    linhas = df[pd.to_datetime(df["dt_data"]).dt.date == data]
    return linhas.iloc[0] if not linhas.empty else None


def _fmt_mes(d):
    return d.strftime("%m/%Y")


def _atingimento_variant(pct):
    if pct is None:
        return "neutral"
    return "ok" if pct >= 1 else "bad"


def _meses_disponiveis(df):
    return sorted(pd.to_datetime(df["dt_prim_dia_mes"]).dt.date.unique(), reverse=True)


def _agregado_mes(df_dia, meses_selecionados, hoje):
    """Todo agregado do bloco 'Mês' é soma simples de rpt_vendas_dia filtrada
    pelos meses selecionados — sem caso especial mês atual vs. mês passado
    (dias futuros já têm faturamento zero, dias passados já são <= hoje).
    vl_objetivo_total é excluído da regra: é o mesmo valor repetido em cada
    dia do mês, por isso usa 1 valor por mês (drop_duplicates) antes de somar
    entre os meses selecionados. Ver specs/vendas.md."""
    df = df_dia.copy()
    df["dt_prim_dia_mes"] = pd.to_datetime(df["dt_prim_dia_mes"]).dt.date
    df["dt_data"] = pd.to_datetime(df["dt_data"]).dt.date
    sel = df[df["dt_prim_dia_mes"].isin(meses_selecionados)]

    meta_total = sel.drop_duplicates("dt_prim_dia_mes")["vl_objetivo_total"].sum()
    meta_acumulada = sel.loc[sel["dt_data"] <= hoje, "vl_meta_dia"].sum()

    return {
        "meta_total": meta_total,
        "meta_acumulada": meta_acumulada,
        "faturamento_bruto": sel["vl_faturamento_bruto"].sum(),
        "vl_frete": sel["vl_frete"].sum(),
        "vl_desconto": sel["vl_desconto"].sum(),
        "vl_faturamento_liquido": sel["vl_faturamento_liquido"].sum(),
        "vl_custo_mercadoria": sel["vl_custo_mercadoria"].sum(),
        "vl_lucro_bruto": sel["vl_lucro_bruto"].sum(),
        "qt_pedidos": int(sel["qt_pedidos"].sum()),
        "qt_pedidos_cancelados": int(sel["qt_pedidos_cancelados"].sum()),
        "qt_item": int(sel["qt_item"].sum()),
    }


def _grafico_venda_diaria(df_dia, meses_selecionados):
    """Barra por dia (verde = dia acima da meta, vermelho = abaixo, cinza =
    mês sem meta cadastrada) + linha tracejada da meta do dia. Ver
    specs/vendas.md."""
    df = df_dia.copy()
    df["dt_prim_dia_mes"] = pd.to_datetime(df["dt_prim_dia_mes"]).dt.date
    sel = df[df["dt_prim_dia_mes"].isin(meses_selecionados)].sort_values("dt_data")

    def _cor(faturamento, meta):
        if pd.isna(meta):
            return TAUPE
        return OK_BG if faturamento >= meta else BAD

    cores = [_cor(f, m) for f, m in zip(sel["vl_faturamento_bruto"], sel["vl_meta_dia"])]

    fig = go.Figure()
    fig.add_bar(x=sel["dt_data"], y=sel["vl_faturamento_bruto"], name="Faturamento Bruto", marker_color=cores)
    fig.add_trace(go.Scatter(x=sel["dt_data"], y=sel["vl_meta_dia"], name="Meta do Dia",
                              line=dict(color=PLUM, width=2, dash="dash")))
    plotly_layout(fig, height=300, hovermode="x unified", yaxis=dict(gridcolor=GRID, tickprefix="R$"))
    return fig


def _filtrar_pedidos_mes(df_pedidos, meses_selecionados):
    df = df_pedidos.copy()
    df["dt_prim_dia_mes"] = pd.to_datetime(df["dt_prim_dia_mes"]).dt.date
    return df[df["dt_prim_dia_mes"].isin(meses_selecionados)]


def _tabela_pedidos_detalhe(df_pedidos, meses_selecionados):
    """1 linha por item de pedido — usada só quando o usuário marca 'Mostrar
    produtos'. Ver specs/vendas.md."""
    sel = _filtrar_pedidos_mes(df_pedidos, meses_selecionados).sort_values("dt_pedido", ascending=False)

    return pd.DataFrame({
        "Código do Pedido": sel["cd_pedido"],
        "Cliente": sel["nm_cliente"],
        "Produto": sel["nm_produto"],
        "Quantidade": sel["qt_item"],
        "Custo do Produto": sel["vl_custo_produto"].apply(lambda v: f"R$ {v:,.2f}"),
        "Faturamento Total": sel["vl_faturamento_total"].apply(lambda v: f"R$ {v:,.2f}"),
        "Frete": sel["vl_frete"].apply(lambda v: f"R$ {v:,.2f}"),
        "Desconto": sel["vl_desconto"].apply(lambda v: f"R$ {v:,.2f}"),
        "Total do Pedido": sel["vl_total_pedido"].apply(lambda v: f"R$ {v:,.2f}"),
        "Taxa": sel["vl_taxa"].apply(lambda v: f"R$ {v:,.2f}"),
        "Lucro": sel["vl_lucro"].apply(lambda v: f"R$ {v:,.2f}"),
        "Margem de Lucro %": sel["pct_margem_lucro"].apply(lambda v: f"{v * 100:.1f}%" if pd.notna(v) else "—"),
    })


def _tabela_pedidos_resumo(df_pedidos, meses_selecionados):
    """1 linha por pedido/cliente — view padrão da tabela 'Pedidos do Mês'.
    Soma os itens de cada pedido (produto não aparece aqui — é o nível a
    mais, só exibido via 'Mostrar produtos'). Margem recalculada no nível do
    pedido (lucro do pedido ÷ total do pedido), não é a média das margens
    dos itens. Ver specs/vendas.md."""
    sel = _filtrar_pedidos_mes(df_pedidos, meses_selecionados)

    resumo = sel.groupby(["cd_pedido", "nm_cliente", "dt_pedido"], as_index=False).agg(
        qt_item=("qt_item", "sum"),
        vl_custo_produto=("vl_custo_produto", "sum"),
        vl_faturamento_total=("vl_faturamento_total", "sum"),
        vl_frete=("vl_frete", "sum"),
        vl_desconto=("vl_desconto", "sum"),
        vl_total_pedido=("vl_total_pedido", "sum"),
        vl_taxa=("vl_taxa", "sum"),
        vl_lucro=("vl_lucro", "sum"),
    ).sort_values("dt_pedido", ascending=False)

    # Líquido do pedido = Total do Pedido − Frete − Taxa (mesma base do
    # numerador de vl_lucro em rpt_vendas_pedidos.sql) — não usar Total do
    # Pedido puro aqui, senão a margem infla (mesmo bug de vl_lucro que
    # esquecia de descontar o frete, corrigido em 2026-07-01).
    liquido_pedido = resumo["vl_total_pedido"] - resumo["vl_frete"] - resumo["vl_taxa"]
    pct_margem = resumo["vl_lucro"] / liquido_pedido

    return pd.DataFrame({
        "Código do Pedido": resumo["cd_pedido"],
        "Cliente": resumo["nm_cliente"],
        "Quantidade": resumo["qt_item"],
        "Custo do Produto": resumo["vl_custo_produto"].apply(lambda v: f"R$ {v:,.2f}"),
        "Faturamento Total": resumo["vl_faturamento_total"].apply(lambda v: f"R$ {v:,.2f}"),
        "Frete": resumo["vl_frete"].apply(lambda v: f"R$ {v:,.2f}"),
        "Desconto": resumo["vl_desconto"].apply(lambda v: f"R$ {v:,.2f}"),
        "Total do Pedido": resumo["vl_total_pedido"].apply(lambda v: f"R$ {v:,.2f}"),
        "Taxa": resumo["vl_taxa"].apply(lambda v: f"R$ {v:,.2f}"),
        "Lucro": resumo["vl_lucro"].apply(lambda v: f"R$ {v:,.2f}"),
        "Margem de Lucro %": pct_margem.apply(lambda v: f"{v * 100:.1f}%" if pd.notna(v) else "—"),
    })


def _tabela_venda_produto(df_pedidos, meses_selecionados):
    """1 linha por produto, agrupando pelo nome sem variação (nm_produto_base
    — ex.: cor/tamanho não separam a linha, mas um comprimento diferente de
    corda continua sendo outro produto na fonte, ver specs/vendas.md)."""
    sel = _filtrar_pedidos_mes(df_pedidos, meses_selecionados)

    resumo = sel.groupby("nm_produto_base", as_index=False).agg(
        qt_item=("qt_item", "sum"),
        vl_custo_produto=("vl_custo_produto", "sum"),
        vl_faturamento_total=("vl_faturamento_total", "sum"),
        vl_lucro=("vl_lucro", "sum"),
    ).sort_values("vl_faturamento_total", ascending=False)

    pct_margem = resumo["vl_lucro"] / resumo["vl_faturamento_total"]

    return pd.DataFrame({
        "Produto": resumo["nm_produto_base"],
        "Quantidade Vendida": resumo["qt_item"],
        "Custo do Produto": resumo["vl_custo_produto"].apply(lambda v: f"R$ {v:,.2f}"),
        "Faturamento Total": resumo["vl_faturamento_total"].apply(lambda v: f"R$ {v:,.2f}"),
        "Lucro": resumo["vl_lucro"].apply(lambda v: f"R$ {v:,.2f}"),
        "Margem de Lucro %": pct_margem.apply(lambda v: f"{v * 100:.1f}%" if pd.notna(v) else "—"),
    })


def _mapa_cores_categoria(df_pedidos_todos, coluna_grupo, top_n=7):
    """Mapa fixo nome-do-grupo → cor, baseado no ranking de faturamento de
    TODO o histórico (não do mês filtrado). Cor segue a identidade da
    categoria, nunca o rank do período em tela — se seguisse o rank atual,
    trocar o filtro de mês poderia repintar a mesma categoria com cor
    diferente (anti-padrão de recolorir por filtro). Capado em `top_n`
    grupos com cor própria; o resto dobra em 'Outros' (cinza neutro,
    `MUTED`) em vez de gerar uma 9ª cor categórica. Ver specs/vendas.md."""
    ranking_global = df_pedidos_todos.groupby(coluna_grupo)["vl_faturamento_total"].sum().sort_values(ascending=False)
    topo = ranking_global.head(top_n).index
    return {nome: CATEGORICAL_PALETTE[i] for i, nome in enumerate(topo)}


def _grafico_pizza_participacao(df_pedidos_todos, meses_selecionados, coluna_grupo, top_n=7):
    """Pizza de Faturamento Total (já 'sem frete' — vl_faturamento_total é o
    valor bruto do item, antes de frete/desconto) por categoria/subcategoria,
    com rótulo fora da fatia (linha conectora automática do Plotly quando
    `textposition='outside'`). Categorias fora do top `top_n` (por
    faturamento global) somam em 'Outros'. Sem legenda: cada fatia já é
    identificada pelo próprio rótulo — uma legenda repetiria a mesma
    informação. Ver specs/vendas.md."""
    mapa_cores = _mapa_cores_categoria(df_pedidos_todos, coluna_grupo, top_n)
    sel = _filtrar_pedidos_mes(df_pedidos_todos, meses_selecionados).copy()
    sel["_grupo"] = sel[coluna_grupo].apply(lambda v: v if v in mapa_cores else "Outros")

    agrupado = sel.groupby("_grupo")["vl_faturamento_total"].sum().sort_values(ascending=False)
    cores = [mapa_cores.get(nome, MUTED) for nome in agrupado.index]

    fig = go.Figure(go.Pie(
        labels=agrupado.index, values=agrupado.values, sort=False,
        marker=dict(colors=cores), textposition="outside", textinfo="label+percent",
    ))
    plotly_layout(fig, height=380, showlegend=False)
    return fig


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
             "faturamento bruto ÷ meta do dia", variant=_atingimento_variant(atingimento)),
        card("Pedidos", f"{qt_pedidos}", variant="neutral"),
        card("Margem de Lucro",
             f"{margem * 100:.1f}%" if margem is not None else "—",
             "lucro ÷ faturamento líquido", variant="neutral"),
        card("Lucro", f"R$ {lucro:,.2f}",
             "faturamento líquido − custo de mercadoria", variant="neutral"),
    ])

    # ═══ MÊS ═══
    section_title("Mês")

    meses_disponiveis = _meses_disponiveis(dados["vendas_dia"])
    mes_atual = hoje.replace(day=1)
    meses_selecionados = st.multiselect(
        "Meses", options=meses_disponiveis, default=[mes_atual] if mes_atual in meses_disponiveis else [],
        format_func=_fmt_mes,
    )

    if not meses_selecionados:
        st.info("Selecione ao menos um mês para ver os indicadores abaixo.")
        return

    agg = _agregado_mes(dados["vendas_dia"], meses_selecionados, hoje)

    atingimento_mes = (agg["faturamento_bruto"] / agg["meta_acumulada"]) if agg["meta_acumulada"] else None
    margem_mes = (agg["vl_lucro_bruto"] / agg["vl_faturamento_liquido"]) if agg["vl_faturamento_liquido"] else None
    ticket_medio = (agg["faturamento_bruto"] / agg["qt_pedidos"]) if agg["qt_pedidos"] else None
    preco_medio = (agg["faturamento_bruto"] / agg["qt_item"]) if agg["qt_item"] else None
    qtd_media_produtos = (agg["qt_item"] / agg["qt_pedidos"]) if agg["qt_pedidos"] else None
    qt_pedidos_totais = agg["qt_pedidos"] + agg["qt_pedidos_cancelados"]
    taxa_cancelamento = (agg["qt_pedidos_cancelados"] / qt_pedidos_totais) if qt_pedidos_totais else None

    st.html('<div class="c-label" style="margin-bottom:10px">Atingimento da Meta</div>')
    render_cards([
        card("Meta Total do Mês", f"R$ {agg['meta_total']:,.2f}", variant="neutral"),
        card("Meta Acumulada até Hoje", f"R$ {agg['meta_acumulada']:,.2f}", variant="neutral"),
        card("Faturamento Bruto", f"R$ {agg['faturamento_bruto']:,.2f}", variant="neutral"),
        card("Atingimento da Meta",
             f"{atingimento_mes * 100:.0f}%" if atingimento_mes is not None else "—",
             "faturamento bruto ÷ meta acumulada até hoje", variant=_atingimento_variant(atingimento_mes)),
    ])

    st.html('<div class="c-label" style="margin:20px 0 10px">Vendas por Dia</div>')
    with st.container(border=True):
        st.plotly_chart(_grafico_venda_diaria(dados["vendas_dia"], meses_selecionados), use_container_width=True)
        note("Barras verdes = dia acima da meta diária; vermelhas = abaixo; cinza = mês sem meta cadastrada. "
             "Linha tracejada é a meta do dia (meta do mês ÷ dias do mês).")

    st.html('<div class="c-label" style="margin:20px 0 10px">Detalhamento do Lucro</div>')
    render_cards([
        card("Frete", f"R$ {agg['vl_frete']:,.2f}", variant="neutral"),
        card("Descontos Totais", f"R$ {agg['vl_desconto']:,.2f}", variant="neutral"),
        card("Faturamento Líquido", f"R$ {agg['vl_faturamento_liquido']:,.2f}", variant="neutral"),
        card("Custo Total dos Produtos", f"R$ {agg['vl_custo_mercadoria']:,.2f}", variant="neutral"),
        card("Lucro", f"R$ {agg['vl_lucro_bruto']:,.2f}", variant="neutral"),
        card("Margem de Lucro",
             f"{margem_mes * 100:.1f}%" if margem_mes is not None else "—",
             "lucro ÷ faturamento líquido", variant="neutral"),
    ])
    note("Descontos Totais é informativo — já está embutido no Faturamento Bruto (que já vem líquido de desconto), "
         "não é subtraído de novo para chegar no Faturamento Líquido. Ver specs/vendas.md.")

    st.html('<div class="c-label" style="margin:20px 0 10px">Indicadores de Pedidos</div>')
    render_cards([
        card("Pedidos", f"{agg['qt_pedidos']}", variant="neutral"),
        card("Produtos Vendidos", f"{agg['qt_item']}", variant="neutral"),
        card("Ticket Médio", f"R$ {ticket_medio:,.2f}" if ticket_medio is not None else "—", variant="neutral"),
        card("Preço Médio dos Produtos", f"R$ {preco_medio:,.2f}" if preco_medio is not None else "—", variant="neutral"),
        card("Produtos por Pedido", f"{qtd_media_produtos:.1f}" if qtd_media_produtos is not None else "—", variant="neutral"),
        card("Taxa de Cancelamento",
             f"{taxa_cancelamento * 100:.1f}%" if taxa_cancelamento is not None else "—",
             f"{agg['qt_pedidos_cancelados']} cancelado(s) de {qt_pedidos_totais}", variant="neutral"),
    ])

    section_title("Pedidos do Mês")
    mostrar_produtos = st.checkbox("Mostrar produtos")
    if mostrar_produtos:
        tabela_pedidos = _tabela_pedidos_detalhe(dados["pedidos"], meses_selecionados)
    else:
        tabela_pedidos = _tabela_pedidos_resumo(dados["pedidos"], meses_selecionados)
    st.dataframe(tabela_pedidos, hide_index=True, use_container_width=True)

    # ═══ PRODUTOS ═══
    section_title("Produtos")

    st.html('<div class="c-label" style="margin-bottom:10px">Venda por Produto</div>')
    st.dataframe(_tabela_venda_produto(dados["pedidos"], meses_selecionados), hide_index=True, use_container_width=True)
    note("Agrupado pelo nome do produto sem variação de cor/tamanho — vendas de cores diferentes do mesmo produto somam na mesma linha.")

    col_cat, col_subcat = st.columns(2)
    with col_cat:
        st.html('<div class="c-label" style="margin:20px 0 10px">Faturamento por Categoria</div>')
        with st.container(border=True):
            st.plotly_chart(_grafico_pizza_participacao(dados["pedidos"], meses_selecionados, "ds_categoria"), use_container_width=True)
    with col_subcat:
        st.html('<div class="c-label" style="margin:20px 0 10px">Faturamento por Subcategoria</div>')
        with st.container(border=True):
            st.plotly_chart(_grafico_pizza_participacao(dados["pedidos"], meses_selecionados, "ds_subcategoria"), use_container_width=True)
    note("Faturamento sem frete (valor do item antes de frete/desconto) — % é a participação no faturamento total do período selecionado. "
         "Categorias fora das 7 de maior faturamento (no histórico completo) somam em \"Outros\".")
