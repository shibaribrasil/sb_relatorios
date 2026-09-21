"""Relatório Clientes & Coorte — Shibari Brasil (camada mensal).

Regras e definição de cada indicador: ver specs/clientes-coorte.md. Não altere cálculo sem ler o spec.

Regra de negócio no dbt (`tb_cliente`: pedidos válidos, valor do cliente = margem de contribuição acumulada,
recorrência, origem do 1º pedido; `tb_pedido`: pedidos válidos). Aqui só se filtra, soma e apresenta.
Valor do cliente = margem de contribuição (antes de mídia) acumulada; nunca média de razões.
"""
import os
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from common import bigquery as bq
from common.design import (
    COLORS, METRIC_COLORS, CATEGORICAL, inject_css, card, render_cards, section_title, note, plotly_layout, brl, pct,
)

DATASET_CLIENTE = os.environ.get("SB_DATASET_CLIENTE", "dbt_dw_az")
TABELA_CLIENTE = os.environ.get("SB_TABELA_CLIENTE", "tb_cliente")
DATASET_PEDIDO = os.environ.get("SB_DATASET_PEDIDO", "dbt_dw_az")
TABELA_PEDIDO = os.environ.get("SB_TABELA_PEDIDO", "tb_pedido")
BRT = timezone(timedelta(hours=-3))

COORTE_DESDE = pd.Timestamp("2024-01-01")  # histórico do DW começa em nov/2023: coortes antes disso misturam clientes antigos
DIAS_REATIVAR = 180   # cliente recorrente sem comprar há tantos dias entra na lista de reativação
MESES_COORTE = 12
COORTES_EXIBIDAS = 24  # últimas 24 coortes no mapa
JANELAS_RECOMPRA = [30, 60, 90, 180, 365]


def _hoje_brt():
    return datetime.now(BRT).date()


@st.cache_data(ttl=1800)
def carregar_dados():
    client = bq.get_client()
    cli = bq.query_df(client, f"""
        SELECT cd_contato, nm_contato, ds_endereco_uf, qt_pedido, vl_total_pedido, vl_receita_liquida_produto,
               vl_margem_contribuicao, dt_prim_pedido, dt_ult_pedido, fg_recorrente, qt_dias_entre_compras,
               qt_dias_desde_ultima_compra, ds_origem_primeiro_pedido, ds_midia_primeiro_pedido
          FROM `{bq.PROJECT}.{DATASET_CLIENTE}.{TABELA_CLIENTE}` WHERE qt_pedido > 0
    """)
    ped = bq.query_df(client, f"""
        SELECT cd_contato, cd_codigo_interno, ANY_VALUE(dt_pedido) AS dt_pedido,
               SUM(vl_margem_contribuicao) AS marg, SUM(vl_receita_liquida_produto) AS rec, SUM(vl_liquido_item) AS fat
          FROM `{bq.PROJECT}.{DATASET_PEDIDO}.{TABELA_PEDIDO}` WHERE fg_pedido_valido AND cd_contato IS NOT NULL
      GROUP BY cd_contato, cd_codigo_interno
    """)
    for c in ["dt_prim_pedido", "dt_ult_pedido"]:
        cli[c] = pd.to_datetime(cli[c])
    for c in ["vl_total_pedido", "vl_receita_liquida_produto", "vl_margem_contribuicao", "qt_dias_entre_compras", "qt_dias_desde_ultima_compra", "qt_pedido"]:
        cli[c] = pd.to_numeric(cli[c])
    cli["fg_recorrente"] = cli["fg_recorrente"].fillna(False).astype(bool)
    ped["dt_pedido"] = pd.to_datetime(ped["dt_pedido"])
    for c in ["marg", "rec", "fat"]:
        ped[c] = pd.to_numeric(ped[c]).fillna(0.0)
    ped["mes"] = ped["dt_pedido"].dt.to_period("M")
    ped = ped.sort_values(["cd_contato", "dt_pedido", "cd_codigo_interno"])
    ped["nr"] = ped.groupby("cd_contato").cumcount() + 1
    prim = ped[ped["nr"] == 1].set_index("cd_contato")
    ped["mes_coorte"] = ped["cd_contato"].map(prim["mes"])
    ped["k"] = (ped["mes"] - ped["mes_coorte"]).apply(lambda x: x.n)  # meses desde a 1ª compra
    seg = ped[ped["nr"] == 2].set_index("cd_contato")["dt_pedido"]
    cli["dt_seg_pedido"] = cli["cd_contato"].map(seg)
    return {"cli": cli, "ped": ped}


def _coorte_retencao(ped, hoje):
    """% da coorte (clientes cuja 1ª compra foi no mês) que comprou de novo k meses depois. Célula só existe se o mês já aconteceu."""
    mes_hoje = pd.Period(hoje, "M")
    coortes = sorted(m for m in ped["mes_coorte"].unique() if m.to_timestamp() >= COORTE_DESDE)[-COORTES_EXIBIDAS:]
    tam = ped[ped["nr"] == 1].groupby("mes_coorte")["cd_contato"].nunique()
    ativos = ped[ped["k"] >= 1].groupby(["mes_coorte", "k"])["cd_contato"].nunique()
    z, txt = [], []
    for m in coortes:
        linha, tlinha = [], []
        for k in range(1, MESES_COORTE + 1):
            if (m + k) > mes_hoje:
                linha.append(None); tlinha.append("")
            else:
                n = int(ativos.get((m, k), 0))
                linha.append(n / tam[m]); tlinha.append(f"{n}" if n else "·")
        z.append(linha); txt.append(tlinha)
    return coortes, tam, z, txt


def _grafico_heatmap(coortes, tam, z, txt):
    y = [f"{m.strftime('%m/%Y')} ({int(tam[m])})" for m in coortes]
    fig = go.Figure(go.Heatmap(
        z=z, x=[f"+{k}" for k in range(1, MESES_COORTE + 1)], y=y, text=txt, texttemplate="%{text}",
        colorscale=[[0, "#F2F4F8"], [1, METRIC_COLORS["receita"]]], zmin=0, zmax=0.15, showscale=False, xgap=2, ygap=2,
        hovertemplate="Coorte %{y}<br>%{x} meses: %{z:.1%} voltaram a comprar<extra></extra>",
    ))
    plotly_layout(fig, height=max(320, 26 * len(coortes) + 80), xaxis=dict(title="meses depois da 1ª compra", side="top"),
                  yaxis=dict(autorange="reversed", title=""))
    return fig


def _grafico_ltv(ped, hoje):
    """Margem de contribuição acumulada por cliente, por coorte trimestral (só meses já completos para toda a coorte)."""
    mes_hoje = pd.Period(hoje, "M")
    p = ped[ped["mes_coorte"].apply(lambda m: m.to_timestamp() >= COORTE_DESDE)].copy()
    p["tri"] = p["mes_coorte"].apply(lambda m: f"{m.year}T{(m.month - 1) // 3 + 1}")
    tam = p[p["nr"] == 1].groupby("tri")["cd_contato"].nunique()
    ult_mes = p.groupby("tri")["mes_coorte"].max()
    fig = go.Figure()
    for i, tri in enumerate(sorted(tam.index)):
        sub = p[p["tri"] == tri]
        max_k = (mes_hoje - ult_mes[tri]).n  # último k completo para todos da coorte
        if max_k < 1:
            continue
        ks = list(range(0, min(max_k, 24) + 1))
        y = [sub.loc[sub["k"] <= k, "marg"].sum() / tam[tri] for k in ks]
        fig.add_trace(go.Scatter(x=ks, y=y, mode="lines", name=f"{tri} ({int(tam[tri])} clientes)",
                                 line=dict(color=CATEGORICAL[i % len(CATEGORICAL)], width=2),
                                 hovertemplate=f"{tri}<br>+%{{x}} meses: R$ %{{y:,.0f}} por cliente<extra></extra>"))
    plotly_layout(fig, height=340, hovermode="x unified", xaxis=dict(title="meses depois da 1ª compra", dtick=3, gridcolor=COLORS["grid"]),
                  yaxis=dict(tickprefix="R$ ", gridcolor=COLORS["grid"]))
    return fig


def _curva_recompra(cli, hoje):
    """% de clientes que fizeram a 2ª compra em até N dias da 1ª — só clientes com N dias completos desde a 1ª compra."""
    linhas = []
    for d in JANELAS_RECOMPRA:
        base = cli[cli["dt_prim_pedido"] + pd.Timedelta(days=d) <= pd.Timestamp(hoje)]
        if base.empty:
            continue
        voltou = base["dt_seg_pedido"].notna() & ((base["dt_seg_pedido"] - base["dt_prim_pedido"]).dt.days <= d)
        linhas.append((d, len(base), int(voltou.sum()), float(voltou.mean())))
    return pd.DataFrame(linhas, columns=["dias", "base", "voltaram", "pct"])


def render():
    inject_css()
    with st.spinner("Carregando dados do BigQuery..."):
        try:
            dados = carregar_dados()
        except Exception as e:
            st.error(f"Erro ao carregar dados do BigQuery: {e}")
            return
    cli, ped = dados["cli"], dados["ped"]
    hoje = _hoje_brt()
    if cli.empty:
        st.info("Sem clientes com compra válida.")
        return
    st.html(f"""
    <div class="report-header">
      <div>
        <div class="report-brand">shibari brasil · camada mensal</div>
        <div class="report-title">Clientes <span>&amp;</span> Coorte</div>
        <div class="report-meta">Compras = pedidos válidos · valor do cliente = margem de contribuição acumulada (antes de mídia) · histórico do DW desde nov/2023 · fonte: {DATASET_CLIENTE}.{TABELA_CLIENTE}</div>
      </div>
      <div class="report-badge">Hoje: <strong>{hoje.strftime("%d/%m/%Y")}</strong></div>
    </div>
    """)

    # ═══ VISÃO GERAL ═══
    section_title("A base de clientes")
    n = len(cli)
    rec = cli[cli["fg_recorrente"]]
    ativos = cli[cli["qt_dias_desde_ultima_compra"] <= 90]
    marg_total = float(cli["vl_margem_contribuicao"].sum())
    marg_rec = float(rec["vl_margem_contribuicao"].sum())
    dias_entre = rec["qt_dias_entre_compras"].median()
    render_cards([
        card("Clientes com compra", f"{n:,}".replace(",", "."), "desde nov/2023 · pedidos válidos"),
        card("Recorrentes", f"{len(rec)} · {pct(len(rec) / n)}", "clientes com 2+ pedidos"),
        card("Compraram nos últimos 90 dias", f"{len(ativos)}", "base ativa", ref=f"{pct(len(ativos) / n)} da base"),
        card("Valor médio do cliente", brl(marg_total / n), "margem de contribuição acumulada ÷ clientes"),
        card("Valor médio — recorrente", brl(marg_rec / len(rec)) if len(rec) else "—",
             f"vs {brl((marg_total - marg_rec) / (n - len(rec)))} do cliente de 1 compra" if n > len(rec) else ""),
        card("Recorrentes concentram", pct(marg_rec / marg_total) if marg_total else "—", "da margem de contribuição total",
             ref=f"{pct(len(rec) / n)} dos clientes"),
        card("Mediana entre compras", f"{dias_entre:.0f} dias".replace(".", ",") if pd.notna(dias_entre) else "—", "só clientes recorrentes"),
    ])
    note("Valor do cliente = margem de contribuição acumulada dos pedidos válidos, antes de mídia (embalagem estimada; reembolso no pedido). Antes de ago/2025 "
         "a taxa e o frete reais da Nuvemshop não existem, então o valor dos clientes antigos é aproximado. Quem comprou antes de nov/2023 aparece com a 1ª compra a partir dessa data.")

    # ═══ CURVA DE RECOMPRA ═══
    section_title("Em quanto tempo o cliente volta")
    cr = _curva_recompra(cli, hoje)
    col1, col2 = st.columns([3, 2])
    with col1:
        fig = go.Figure(go.Bar(x=[f"até {d} dias" for d in cr["dias"]], y=cr["pct"], marker_color=METRIC_COLORS["receita"],
                               text=[f"{p:.1%}".replace(".", ",") for p in cr["pct"]], textposition="outside", cliponaxis=False,
                               customdata=cr[["voltaram", "base"]].to_numpy(),
                               hovertemplate="%{x}<br>%{customdata[0]} de %{customdata[1]} clientes<extra></extra>"))
        plotly_layout(fig, height=300, yaxis=dict(tickformat=".0%", rangemode="tozero", gridcolor=COLORS["grid"]))
        with st.container(border=True):
            st.plotly_chart(fig, use_container_width=True)
    with col2:
        dist = cli.assign(faixa=np.where(cli["qt_pedido"] >= 4, "4+", cli["qt_pedido"].astype(int).astype(str))).groupby("faixa").agg(
            clientes=("cd_contato", "size"), marg=("vl_margem_contribuicao", "sum")).reset_index()
        dist["% clientes"] = dist["clientes"] / dist["clientes"].sum()
        dist["% margem"] = dist["marg"] / dist["marg"].sum()
        st.dataframe(pd.DataFrame({"Pedidos": dist["faixa"], "Clientes": dist["clientes"], "% clientes": dist["% clientes"], "% da margem": dist["% margem"]}),
                     hide_index=True, use_container_width=True,
                     column_config={"Pedidos": st.column_config.TextColumn(width=70), "Clientes": st.column_config.NumberColumn(width=80),
                                    "% clientes": st.column_config.NumberColumn(format="percent", width=90),
                                    "% da margem": st.column_config.NumberColumn(format="percent", width=100)})
    note("Curva: % dos clientes que fizeram a 2ª compra em até N dias da 1ª, contando só quem já teve N dias completos desde a 1ª compra. "
         "Serve para escolher <strong>quando</strong> ativar recompra (o pós-venda deve acontecer antes de a curva achatar). Tabela: como a base e a margem se distribuem por nº de pedidos.")

    # ═══ COORTE ═══
    section_title("Coorte: quem voltou a comprar")
    coortes, tam, z, txt = _coorte_retencao(ped, hoje)
    with st.container(border=True):
        st.plotly_chart(_grafico_heatmap(coortes, tam, z, txt), use_container_width=True)
    note("Cada linha é o mês da 1ª compra (entre parênteses, quantos clientes). Cada coluna é quantos meses depois; o número na célula é quantos clientes da coorte "
         "compraram <em>naquele mês</em> e a cor mostra a % da coorte (escala até 15%). \"·\" = ninguém; célula vazia = mês ainda não aconteceu. "
         "Volume pequeno: leia o padrão geral, não uma célula.")

    section_title("Valor acumulado por cliente, por coorte")
    with st.container(border=True):
        st.plotly_chart(_grafico_ltv(ped, hoje), use_container_width=True)
    note("Margem de contribuição acumulada por cliente da coorte (trimestre da 1ª compra), a cada mês depois da 1ª compra — só meses já completos para toda a coorte. "
         "Uma coorte mais alta que as anteriores no mesmo ponto = clientes mais valiosos; a distância entre 0 e +3 meses mostra o quanto vem de recompra.")

    # ═══ ORIGEM DO 1º PEDIDO ═══
    section_title("De onde vêm os clientes que ficam")
    g = cli.groupby(["ds_origem_primeiro_pedido", "ds_midia_primeiro_pedido"], as_index=False).agg(
        clientes=("cd_contato", "size"), recorrentes=("fg_recorrente", "sum"), marg=("vl_margem_contribuicao", "sum")).sort_values("clientes", ascending=False)
    g["pct_rec"] = g["recorrentes"] / g["clientes"]
    g["marg_cli"] = g["marg"] / g["clientes"]
    st.dataframe(pd.DataFrame({"Origem do 1º pedido": g["ds_origem_primeiro_pedido"], "Mídia": g["ds_midia_primeiro_pedido"], "Clientes": g["clientes"],
                               "Recorrentes": g["recorrentes"], "% recorrentes": g["pct_rec"], "Valor médio (R$)": g["marg_cli"]}),
                 hide_index=True, use_container_width=True,
                 column_config={"Clientes": st.column_config.NumberColumn(width=90), "Recorrentes": st.column_config.NumberColumn(width=110),
                                "% recorrentes": st.column_config.NumberColumn(format="percent", width=120),
                                "Valor médio (R$)": st.column_config.NumberColumn(format="R$ %.2f", width=140)})
    note("Origem detectada pela URL de entrada do 1º pedido (<code>tb_atribuicao_pedido</code>); \"(sem parametro)\" agrega direto/orgânico/sem marcação. "
         "Serve para ver qual canal traz cliente que volta, não só cliente que compra uma vez. Ignore linhas com poucos clientes.")

    # ═══ LISTAS ═══
    section_title("Melhores clientes")
    top = cli.sort_values("vl_margem_contribuicao", ascending=False).head(15)
    st.dataframe(pd.DataFrame({"Cliente": top["nm_contato"], "UF": top["ds_endereco_uf"].fillna("—"), "Pedidos": top["qt_pedido"].astype(int),
                               "Faturamento": top["vl_total_pedido"], "Margem contrib.": top["vl_margem_contribuicao"],
                               "Última compra": top["dt_ult_pedido"].dt.date, "Dias sem comprar": top["qt_dias_desde_ultima_compra"]}),
                 hide_index=True, use_container_width=True,
                 column_config={"Cliente": st.column_config.TextColumn(width=220), "UF": st.column_config.TextColumn(width=50),
                                "Pedidos": st.column_config.NumberColumn(width=80), "Faturamento": st.column_config.NumberColumn(format="R$ %.2f", width=110),
                                "Margem contrib.": st.column_config.NumberColumn(format="R$ %.2f", width=120),
                                "Última compra": st.column_config.DateColumn(width=110), "Dias sem comprar": st.column_config.NumberColumn(width=120)})

    section_title(f"Para reativar (recorrentes há {DIAS_REATIVAR}+ dias sem comprar)")
    rea = cli[cli["fg_recorrente"] & (cli["qt_dias_desde_ultima_compra"] >= DIAS_REATIVAR)].sort_values("vl_margem_contribuicao", ascending=False)
    st.caption(f"{len(rea)} clientes · ordenados por valor (margem de contribuição acumulada); mostrando os 25 maiores")
    rea = rea.head(25)
    st.dataframe(pd.DataFrame({"Cliente": rea["nm_contato"], "UF": rea["ds_endereco_uf"].fillna("—"), "Pedidos": rea["qt_pedido"].astype(int),
                               "Margem contrib.": rea["vl_margem_contribuicao"], "Última compra": rea["dt_ult_pedido"].dt.date,
                               "Dias sem comprar": rea["qt_dias_desde_ultima_compra"], "Costuma comprar a cada (dias)": rea["qt_dias_entre_compras"]}),
                 hide_index=True, use_container_width=True,
                 column_config={"Cliente": st.column_config.TextColumn(width=220), "UF": st.column_config.TextColumn(width=50),
                                "Pedidos": st.column_config.NumberColumn(width=80), "Margem contrib.": st.column_config.NumberColumn(format="R$ %.2f", width=120),
                                "Última compra": st.column_config.DateColumn(width=110), "Dias sem comprar": st.column_config.NumberColumn(width=120),
                                "Costuma comprar a cada (dias)": st.column_config.NumberColumn(format="%.0f", width=190)})
    note("Lista para uma ação de recompra (mensagem/cupom): clientes que já provaram que compram de novo, mas estão há mais de "
         f"{DIAS_REATIVAR} dias parados. A coluna \"costuma comprar a cada\" ajuda a ver quem já passou muito do próprio ritmo.")
