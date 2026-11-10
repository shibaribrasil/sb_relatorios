"""Relatório Pulso do Dia — Shibari Brasil (camada diária; a "tela" para acompanhar o dia).

Regras e definição de cada indicador: ver specs/pulso-dia.md. Não altere cálculo sem ler o spec.

Toda regra de negócio vive no dbt (`tb_pedido`, `tb_tempo`, `tb_objetivo_faturamento`). Aqui só se
filtra, soma e apresenta. As seções "Vendas e meta do período" e "Perfil dos pedidos" são as mesmas de
Vendas & Margem (reports/vendas_margem.py, reports/perfil_pedidos.py), aplicadas ao mês corrente.
"""
import os
from datetime import datetime, timedelta, timezone

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from common import bigquery as bq
from common.design import (
    COLORS, METRIC_COLORS, inject_css, card, render_cards, section_title, note, plotly_layout,
    kpi_delta_color, brl,
)
from reports.perfil_pedidos import secao_perfil, pedidos_de_linhas
from reports.vendas_margem import calcular_periodo, secao_origem, secao_vendas_meta, carregar_dados as carregar_vendas

DATASET = os.environ.get("SB_DATASET_PEDIDO", "dbt_dw_az")
TABELA = os.environ.get("SB_TABELA_PEDIDO", "tb_pedido")
BRT = timezone(timedelta(hours=-3))

DIAS_UTEIS_ATRASO = 2  # pedido pago e ainda não postado há mais que isso é "atrasado" (alinhado ao B048)
DIAS_SEMANA = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]


def _hoje_brt():
    return datetime.now(BRT).date()


@st.cache_data(ttl=300)
def carregar_dados():
    client = bq.get_client()
    base = f"`{bq.PROJECT}.{DATASET}.{TABELA}`"
    inicio = (pd.Timestamp(_hoje_brt()).replace(day=1) - pd.offsets.MonthBegin(2)).date()
    # 1 linha por pedido válido; faturamento = soma das linhas (vl_liquido_item), brinde não conta como item
    pedidos = bq.query_df(client, f"""
        SELECT p.cd_codigo_interno,
               ANY_VALUE(p.cd_pedido) AS cd_pedido, ANY_VALUE(p.cd_pedido_nuvemshop) AS cd_pedido_nuvemshop,
               ANY_VALUE(p.nm_contato) AS nm_contato, ANY_VALUE(p.dt_pedido) AS dt_pedido,
               ANY_VALUE(p.dt_pagamento_nuvemshop) AS dt_pagamento, ANY_VALUE(p.ds_status_pedido) AS ds_status_pedido,
               SUM(p.vl_liquido_item) AS vl_liquido, SUM(IF(p.fg_brinde, 0, p.qt_item)) AS qt_item,
               ANY_VALUE(p.ts_load) AS ts_load
          FROM {base} AS p
         WHERE p.fg_pedido_valido AND p.dt_pedido >= DATE '{inicio}'
      GROUP BY p.cd_codigo_interno
    """)
    tempo = bq.query_df(client, f"""
        SELECT dt_data, fg_dia_util, dt_prim_dia_mes
          FROM `{bq.PROJECT}.dbt_dw_az.tb_tempo`
         WHERE dt_data >= DATE '{inicio}' AND dt_data <= DATE_ADD(CURRENT_DATE('America/Sao_Paulo'), INTERVAL 45 DAY)
    """)
    pedidos["dt_pedido"] = pd.to_datetime(pedidos["dt_pedido"])
    pedidos["dt_pagamento"] = pd.to_datetime(pedidos["dt_pagamento"])
    pedidos["vl_liquido"] = pd.to_numeric(pedidos["vl_liquido"]).fillna(0.0)
    pedidos["qt_item"] = pd.to_numeric(pedidos["qt_item"]).fillna(0.0)
    tempo["dt_data"] = pd.to_datetime(tempo["dt_data"])
    tempo["dt_prim_dia_mes"] = pd.to_datetime(tempo["dt_prim_dia_mes"])
    tempo["fg_dia_util"] = tempo["fg_dia_util"].astype(bool)
    return {"pedidos": pedidos, "tempo": tempo}



def _dia(ped, d):
    x = ped[ped["dt_pedido"] == pd.Timestamp(d)]
    return {"fat": float(x["vl_liquido"].sum()), "ped": int(len(x)), "itens": float(x["qt_item"].sum())}


def _periodo(ped, ini, fim):
    x = ped[(ped["dt_pedido"] >= pd.Timestamp(ini)) & (ped["dt_pedido"] <= pd.Timestamp(fim))]
    return {"fat": float(x["vl_liquido"].sum()), "ped": int(len(x))}


def _dias_uteis_desde(tempo, dt_base, hoje):
    """Dias úteis decorridos depois de dt_base, até hoje (inclusive)."""
    if pd.isna(dt_base):
        return None
    t = tempo[(tempo["dt_data"] > pd.Timestamp(dt_base)) & (tempo["dt_data"] <= pd.Timestamp(hoje)) & (tempo["fg_dia_util"])]
    return int(len(t))


def _tabela_a_postar(ped, tempo, hoje):
    a = ped[ped["ds_status_pedido"] == "EM ABERTO"].copy()
    a["base"] = a["dt_pagamento"].fillna(a["dt_pedido"])
    a["uteis"] = a["base"].apply(lambda d: _dias_uteis_desde(tempo, d, hoje))
    a["codigo"] = a["cd_pedido_nuvemshop"].where(a["cd_pedido_nuvemshop"].notna(), "Bling " + a["cd_pedido"].astype(str))
    return a.sort_values(["base", "codigo"])


def _grafico_semana(ped, hoje):
    seg = pd.Timestamp(hoje) - pd.Timedelta(days=pd.Timestamp(hoje).weekday())
    ant = seg - pd.Timedelta(days=7)
    fig = go.Figure()
    for ini, nome, cor in [(ant, "Semana anterior", COLORS["text_muted"]), (seg, "Semana atual", METRIC_COLORS["receita"])]:
        ys = []
        for i in range(7):
            d = ini + pd.Timedelta(days=i)
            ys.append(None if d > pd.Timestamp(hoje) else float(ped.loc[ped["dt_pedido"] == d, "vl_liquido"].sum()))
        fig.add_bar(x=DIAS_SEMANA, y=ys, name=nome, marker_color=cor,
                    hovertemplate="%{x}<br>R$ %{y:,.0f}<extra>" + nome + "</extra>")
    plotly_layout(fig, height=300, barmode="group", xaxis=dict(tickmode="array", tickvals=DIAS_SEMANA), yaxis=dict(tickprefix="R$ ", gridcolor=COLORS["grid"]))
    return fig


def _tabela_canal(sel, ant=None):
    """Uma linha por canal no período `sel`: pedidos, faturamento, margem de contribuição (R$ e %), participação do canal na margem total e
    crescimento do faturamento contra `ant` (mesmo trecho do mês anterior; None = sem comparação). Crescimento NaN quando o canal não vendeu
    no período anterior (não existe base para comparar)."""
    g = sel.groupby("ds_canal", as_index=False).agg(
        pedidos=("cd_codigo_interno", "nunique"), fat=("vl_liquido_item", "sum"),
        rec=("vl_receita_liquida_produto", "sum"), marg=("vl_margem_contribuicao", "sum"),
    ).sort_values("fat", ascending=False).reset_index(drop=True)
    g["pct"] = g["marg"] / g["rec"].where(g["rec"] != 0)
    g["part"] = g["marg"] / (g["marg"].sum() if g["marg"].sum() != 0 else float("nan"))
    if ant is not None:
        fat_ant = ant.groupby("ds_canal")["vl_liquido_item"].sum()
        base = g["ds_canal"].map(fat_ant)
        g["cresc"] = (g["fat"] - base) / base.where(base > 0)
    return g


def _fmt_cresc(v):
    return "—" if pd.isna(v) else f"{'+' if v >= 0 else '−'}{abs(v) * 100:.1f}%".replace(".", ",")


def _cor_cresc(v):
    """Formatação condicional do crescimento: verde quando cresce, vermelho quando cai (cores só do tema)."""
    if pd.isna(v) or v == 0:
        return ""
    ok = v > 0
    return f"color: {COLORS['success'] if ok else COLORS['danger']}; background-color: {COLORS['success_bg'] if ok else COLORS['danger_bg']}; font-weight: 600"


def _secao_canal(sel, ant=None, rot=None):
    """Canal de venda no mês: pedidos, faturamento, margem de contribuição, participação na margem e crescimento do faturamento contra o mesmo
    trecho do mês anterior (`rot`). Canal = loja do Bling (Nuvemshop, Mercado Livre) com o pós-venda separado como "WhatsApp"
    (`vendas_margem.canal_venda`): esses pedidos saem do total da Nuvemshop."""
    section_title("Canal de venda")
    g = _tabela_canal(sel, ant)
    col1, col2 = st.columns([2, 7])
    with col1:
        fig = go.Figure(go.Bar(
            y=g["ds_canal"], x=g["fat"], orientation="h", marker_color=METRIC_COLORS["receita"],
            text=[brl(v, 0) for v in g["fat"]], textposition="outside", cliponaxis=False,
            hovertemplate="%{y}<br>Faturamento R$ %{x:,.0f}<extra></extra>"))
        plotly_layout(fig, height=max(140, 56 * len(g) + 50), showlegend=False,
                      xaxis=dict(showticklabels=False, showgrid=False, range=[0, max(float(g["fat"].max()), 1.0) * 1.6]),
                      yaxis=dict(autorange="reversed", automargin=True))
        with st.container(border=True):
            st.plotly_chart(fig, use_container_width=True)
    with col2:
        tab = pd.DataFrame({"Canal": g["ds_canal"], "Pedidos": g["pedidos"], "Faturamento": g["fat"]})
        cfg = {"Pedidos": st.column_config.NumberColumn(width=70),
               "Faturamento": st.column_config.NumberColumn(format="R$ %.2f"),
               "Margem de contrib. (R$)": st.column_config.NumberColumn(format="R$ %.2f"),
               "Margem de contrib. (%)": st.column_config.NumberColumn(format="percent"),
               "Part. na margem total": st.column_config.NumberColumn(format="percent")}
        col_cresc = f"Cresc. vs. {rot}" if rot and "cresc" in g else None
        if col_cresc:
            tab[col_cresc] = g["cresc"].map(_fmt_cresc)  # texto: sinal explícito e "—" para o canal sem base de comparação
            cfg[col_cresc] = st.column_config.Column()
        tab["Margem de contrib. (R$)"] = g["marg"]
        tab["Margem de contrib. (%)"] = g["pct"]
        tab["Part. na margem total"] = g["part"]
        estilo = tab.style
        if col_cresc:
            estilo = estilo.apply(lambda _: [_cor_cresc(v) for v in g["cresc"]], subset=[col_cresc])
        st.dataframe(estilo, hide_index=True, use_container_width=True, column_config=cfg)


def render():
    inject_css()
    with st.spinner("Carregando dados do BigQuery..."):
        try:
            dados = carregar_dados()
        except Exception as e:
            st.error(f"Erro ao carregar dados do BigQuery: {e}")
            return
    ped, tempo = dados["pedidos"], dados["tempo"]
    hoje = _hoje_brt()
    if ped.empty:
        st.info("Sem pedidos válidos no período.")
        return
    ontem = hoje - timedelta(days=1)
    atualizado = pd.to_datetime(ped["ts_load"]).max()
    st.html(f"""
    <div class="report-header">
      <div>
        <div class="report-brand">shibari brasil · camada diária</div>
        <div class="report-title">Pulso <span>do</span> Dia</div>
        <div class="report-meta">Pedidos válidos (pagos, não cancelados) · faturamento = produtos líquidos + frete pago · fonte: {DATASET}.{TABELA}</div>
      </div>
      <div class="report-badge">
        Hoje: <strong>{hoje.strftime("%d/%m/%Y")}</strong><br>
        Dados atualizados em: <strong>{atualizado.strftime("%d/%m %H:%M") if pd.notna(atualizado) else "—"}</strong>
      </div>
    </div>
    """)

    # ═══ HOJE E ONTEM ═══
    section_title("Hoje e ontem")
    h, o = _dia(ped, hoje), _dia(ped, ontem)
    m7 = _periodo(ped, ontem - timedelta(days=6), ontem)
    render_cards([
        card("Faturamento hoje", brl(h["fat"]), f"{h['ped']} pedidos · dia em andamento"),
        card("Faturamento ontem", brl(o["fat"]), f"{o['ped']} pedidos · {int(o['itens'])} itens"),
        card("Ticket médio hoje", brl(h["fat"] / h["ped"]) if h["ped"] else "—", "faturamento ÷ pedidos"),
        card("Ticket médio ontem", brl(o["fat"] / o["ped"]) if o["ped"] else "—", "faturamento ÷ pedidos"),
        card("Média por dia (7 dias)", brl(m7["fat"] / 7), f"{m7['ped'] / 7:.1f} pedidos/dia · 7 dias fechados até ontem".replace(".", ",")),
    ])
    note("O dia de hoje está em andamento e os dados atualizam de hora em hora (7h–23h); não compare hoje com um dia fechado.")

    # ═══ A POSTAR ═══
    section_title("Pedidos a postar")
    a = _tabela_a_postar(ped, tempo, hoje)
    atrasados = a[a["uteis"] > DIAS_UTEIS_ATRASO] if not a.empty else a
    mais_antigo = a["uteis"].max() if not a.empty else None
    render_cards([
        card("Aguardando postagem", f"{len(a)}", f"{brl(float(a['vl_liquido'].sum()))} em pedidos pagos"),
        card("Atrasados", f"{len(atrasados)}", f"pagos há mais de {DIAS_UTEIS_ATRASO} dias úteis sem postagem",
             variant="bad" if len(atrasados) else "ok"),
        card("Mais antigo", f"{int(mais_antigo)} {'dia útil' if int(mais_antigo) == 1 else 'dias úteis'}" if pd.notna(mais_antigo) else "—", "desde o pagamento"),
    ])
    if not a.empty:
        tab = pd.DataFrame({
            "Pedido": a["codigo"].astype(str), "Cliente": a["nm_contato"].fillna("—"),
            "Pago em": a["base"].dt.date, "Dias úteis": a["uteis"], "Valor": a["vl_liquido"],
            "Situação": a["uteis"].apply(lambda u: "Atrasado" if pd.notna(u) and u > DIAS_UTEIS_ATRASO else "No prazo"),
        })
        st.dataframe(tab, hide_index=True, use_container_width=True,
                     column_config={"Pedido": st.column_config.TextColumn(width=80),
                                    "Cliente": st.column_config.TextColumn(width=280),
                                    "Pago em": st.column_config.DateColumn(width=110),
                                    "Dias úteis": st.column_config.NumberColumn(width=90),
                                    "Valor": st.column_config.NumberColumn(format="R$ %.2f", width=110),
                                    "Situação": st.column_config.TextColumn(width=110)})
    else:
        note("Nenhum pedido aguardando postagem.")
    note("A postar = pedido válido com status EM ABERTO no Bling (pago, aguardando postagem). Dias úteis contados a partir da data de pagamento na "
         "Nuvemshop (ou do pedido, se não houver) pelo calendário <code>tb_tempo</code>; sábados, domingos e feriados não contam.")

    # ═══ SEMANA ═══
    section_title("Semana (segunda a domingo)")
    seg = pd.Timestamp(hoje) - pd.Timedelta(days=pd.Timestamp(hoje).weekday())
    ontem_ts = pd.Timestamp(ontem)
    ant_ini, ant_fim = seg - pd.Timedelta(days=7), seg - pd.Timedelta(days=1)
    tem_fechado = ontem_ts >= seg  # na segunda-feira ainda não há dia fechado na semana
    sa = _periodo(ped, seg, ontem_ts) if tem_fechado else None
    sm = _periodo(ped, ant_ini, ontem_ts - pd.Timedelta(days=7)) if tem_fechado else None
    sf = _periodo(ped, ant_ini, ant_fim)
    var = ((sa["fat"] - sm["fat"]) / sm["fat"]) if tem_fechado and sm["fat"] else None
    txt_var = (f"{'+' if var >= 0 else '−'}{abs(var) * 100:.1f}%".replace(".", ",") + " na semana atual") if var is not None else ""
    render_cards([
        card("Semana atual (dias fechados)", brl(sa["fat"]) if sa else "—",
             f"seg {seg.day:02d}/{seg.month:02d} até ontem · {sa['ped']} pedidos" if sa else "segunda-feira: ainda sem dia fechado"),
        card("Mesmo trecho da semana anterior", brl(sm["fat"]) if sm else "—", f"{sm['ped']} pedidos · mesmos dias da semana" if sm else "—",
             delta=txt_var, delta_color=kpi_delta_color(var) if var is not None else ""),
        card("Semana anterior inteira", brl(sf["fat"]), f"{sf['ped']} pedidos · seg–dom fechada"),
    ])
    with st.container(border=True):
        st.plotly_chart(_grafico_semana(ped, hoje), use_container_width=True)

    # ═══ VENDAS E META DO PERÍODO e PERFIL DOS PEDIDOS (mesmas seções de Vendas & Margem, mês corrente) ═══
    with st.spinner("Carregando vendas e meta do mês..."):
        try:
            vendas = carregar_vendas()
        except Exception as e:
            st.error(f"Erro ao carregar vendas e meta do mês: {e}")
            return
    df = vendas["vendas"]
    mes = pd.Timestamp(hoje.replace(day=1))
    if mes not in set(df["mes"]):  # 1º dia do mês sem pedido ainda: usa o último mês com vendas (igual a Vendas & Margem)
        mes = df["mes"].max()
    meses_sel = [mes]
    per = calcular_periodo(df, vendas["cancel"], vendas["metas"], vendas["refs"], meses_sel, hoje)
    secao_vendas_meta(per, meses_sel, hoje)
    secao_perfil(pedidos_de_linhas(per["sel"]), pedidos_de_linhas(per["ant"]) if per["ant"] is not None else None, per["rot"] or "",
                 contexto="Mês corrente (hoje incluído), comparado com o mesmo trecho do mês anterior." if per["rot"] else "")
    _secao_canal(per["sel"], per["ant"], per["rot"])
    secao_origem(per["sel"], meses_sel, com_nota=False)
