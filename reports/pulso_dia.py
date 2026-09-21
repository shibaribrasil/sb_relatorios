"""Relatório Pulso do Dia — Shibari Brasil (camada diária).

Regras e definição de cada indicador: ver specs/pulso-dia.md. Não altere cálculo sem ler o spec.

Toda regra de negócio vive no dbt (`tb_pedido`, `tb_tempo`, `tb_objetivo_faturamento`). Aqui só se
filtra, soma e apresenta. Sem percentuais de margem por dia: com ~1 pedido/dia eles não significam nada.
"""
import os
from datetime import datetime, timedelta, timezone

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from common import bigquery as bq
from common.design import (
    COLORS, METRIC_COLORS, inject_css, card, render_cards, section_title, note, plotly_layout,
    kpi_delta_color, brl, pct,
)
from reports.vendas_margem import _grafico_meta_acumulada

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
               ANY_VALUE(p.ts_load) AS ts_load,
               COALESCE(ANY_VALUE(a.ds_origem_venda), '(sem parametro)') AS origem,
               COALESCE(ANY_VALUE(a.ds_midia_venda), '(sem parametro)') AS midia
          FROM {base} AS p
     LEFT JOIN (SELECT cd_pedido, ANY_VALUE(ds_origem_venda) AS ds_origem_venda, ANY_VALUE(ds_midia_venda) AS ds_midia_venda
                  FROM `{bq.PROJECT}.dbt_dw_az.tb_atribuicao_pedido` GROUP BY cd_pedido) AS a USING (cd_pedido)
         WHERE p.fg_pedido_valido AND p.dt_pedido >= DATE '{inicio}'
      GROUP BY p.cd_codigo_interno
    """)
    tempo = bq.query_df(client, f"""
        SELECT dt_data, fg_dia_util, dt_prim_dia_mes
          FROM `{bq.PROJECT}.dbt_dw_az.tb_tempo`
         WHERE dt_data >= DATE '{inicio}' AND dt_data <= DATE_ADD(CURRENT_DATE('America/Sao_Paulo'), INTERVAL 45 DAY)
    """)
    metas = bq.query_df(client, f"""
        SELECT dt_prim_dia_mes, dt_data, vl_meta_dia_acumulado, vl_objetivo_total
          FROM `{bq.PROJECT}.dbt_dw_az.tb_objetivo_faturamento` WHERE dt_prim_dia_mes >= DATE '{inicio}'
    """)
    ads = bq.query_df(client, f"""
        SELECT dt_data, SUM(vl_custo) AS vl_custo FROM `{bq.PROJECT}.dbt_dw_us_az.tb_gads_conta_diario`
         WHERE dt_data >= DATE '{inicio}' GROUP BY dt_data
    """)
    pedidos["dt_pedido"] = pd.to_datetime(pedidos["dt_pedido"])
    pedidos["dt_pagamento"] = pd.to_datetime(pedidos["dt_pagamento"])
    pedidos["vl_liquido"] = pd.to_numeric(pedidos["vl_liquido"]).fillna(0.0)
    pedidos["qt_item"] = pd.to_numeric(pedidos["qt_item"]).fillna(0.0)
    pedidos["vl_liquido_item"] = pedidos["vl_liquido"]  # nome esperado por _grafico_meta_acumulada
    pedidos["mes"] = pedidos["dt_pedido"].dt.to_period("M").dt.to_timestamp()
    tempo["dt_data"] = pd.to_datetime(tempo["dt_data"])
    tempo["dt_prim_dia_mes"] = pd.to_datetime(tempo["dt_prim_dia_mes"])
    tempo["fg_dia_util"] = tempo["fg_dia_util"].astype(bool)
    metas["dt_data"] = pd.to_datetime(metas["dt_data"])
    metas["mes"] = pd.to_datetime(metas["dt_prim_dia_mes"])
    ads["dt_data"] = pd.to_datetime(ads["dt_data"])
    ads["vl_custo"] = pd.to_numeric(ads["vl_custo"]).fillna(0.0)
    return {"pedidos": pedidos, "tempo": tempo, "metas": metas, "ads": ads}


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


def render():
    inject_css()
    with st.spinner("Carregando dados do BigQuery..."):
        try:
            dados = carregar_dados()
        except Exception as e:
            st.error(f"Erro ao carregar dados do BigQuery: {e}")
            return
    ped, tempo, metas, ads = dados["pedidos"], dados["tempo"], dados["metas"], dados["ads"]
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
        <div class="report-meta">Pedidos válidos (pagos, não cancelados) · faturamento = produtos líquidos + frete pago · sem margem por dia · fonte: {DATASET}.{TABELA}</div>
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
    render_cards([
        card("Faturamento hoje", brl(h["fat"]), f"{h['ped']} pedidos · dia em andamento"),
        card("Faturamento ontem", brl(o["fat"]), f"{o['ped']} pedidos · {int(o['itens'])} itens"),
        card("Ticket médio hoje", brl(h["fat"] / h["ped"]) if h["ped"] else "—", "faturamento ÷ pedidos"),
        card("Ticket médio ontem", brl(o["fat"] / o["ped"]) if o["ped"] else "—", "faturamento ÷ pedidos"),
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

    # ═══ RITMO DO MÊS ═══
    section_title("Ritmo do mês")
    mes = pd.Timestamp(hoje).replace(day=1)
    dm = ped[ped["mes"] == mes]
    fat_mes = float(dm["vl_liquido"].sum())
    mm = metas[metas["mes"] == mes]
    t_mes = tempo[tempo["dt_prim_dia_mes"] == mes]
    total_uteis = int(t_mes["fg_dia_util"].sum())
    uteis_fechados = int(t_mes[(t_mes["dt_data"] < pd.Timestamp(hoje))]["fg_dia_util"].sum())
    fat_fechado = float(dm.loc[dm["dt_pedido"] < pd.Timestamp(hoje), "vl_liquido"].sum())
    proj = (fat_fechado / uteis_fechados * total_uteis) if uteis_fechados else None
    meta_total = float(mm["vl_objetivo_total"].iloc[0]) if not mm.empty else None
    linha = mm[mm["dt_data"] == pd.Timestamp(hoje)]
    meta_acum = float(linha["vl_meta_dia_acumulado"].iloc[0]) if not linha.empty else None
    render_cards([
        card("Faturamento no mês", brl(fat_mes), f"01 a {hoje.day:02d}/{hoje.month:02d} · {len(dm)} pedidos"),
        card("Meta acumulada até hoje", brl(meta_acum) if meta_acum else "—",
             f"atingimento {pct(fat_mes / meta_acum, 0)}" if meta_acum else "sem meta cadastrada",
             variant=("ok" if meta_acum and fat_mes >= meta_acum else "bad" if meta_acum else "neutral")),
        card("Dias úteis", f"{uteis_fechados} de {total_uteis}", "fechados no mês (sem hoje) · calendário tb_tempo"),
        card("Faturamento por dia útil", brl(fat_fechado / uteis_fechados) if uteis_fechados else "—", "dias fechados ÷ dias úteis fechados"),
        card("Projeção do mês", brl(proj) if proj else "—",
             f"{pct(proj / meta_total, 0)} da meta do mês ({brl(meta_total)})" if proj and meta_total else "ritmo atual × dias úteis do mês",
             variant=("ok" if proj and meta_total and proj >= meta_total else "bad" if proj and meta_total else "neutral")),
    ])
    fig = _grafico_meta_acumulada(ped, metas, mes, hoje)
    if fig is not None:
        with st.container(border=True):
            st.plotly_chart(fig, use_container_width=True)
    note("<strong>Projeção</strong> = faturamento dos dias fechados ÷ dias úteis fechados × dias úteis do mês (vendas de fim de semana entram no "
         "faturamento e são diluídas nos dias úteis). É uma referência de ritmo, não uma previsão. A meta ainda precisa ser revista.")

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

    # ═══ ORIGEM E MÍDIA ═══
    section_title("Origem e mídia")
    col1, col2 = st.columns([3, 2])
    with col1:
        o7 = ped[ped["dt_pedido"] >= pd.Timestamp(hoje) - pd.Timedelta(days=6)]
        g = o7.groupby(["origem", "midia"], as_index=False).agg(pedidos=("cd_codigo_interno", "nunique"), fat=("vl_liquido", "sum")).sort_values("fat", ascending=False)
        st.html('<div class="c-label" style="margin:0 0 10px">Últimos 7 dias, por origem</div>')
        st.dataframe(pd.DataFrame({"Origem": g["origem"], "Mídia": g["midia"], "Pedidos": g["pedidos"], "Faturamento": g["fat"]}),
                     hide_index=True, use_container_width=True,
                     column_config={"Pedidos": st.column_config.NumberColumn(width=80),
                                    "Faturamento": st.column_config.NumberColumn(format="R$ %.2f", width=120)})
    with col2:
        ult = ads["dt_data"].max()
        ads_mes = float(ads.loc[ads["dt_data"] >= mes, "vl_custo"].sum())
        ped_ate = int(((ped["mes"] == mes) & (ped["dt_pedido"] <= ult)).sum()) if pd.notna(ult) else 0
        render_cards([
            card("Google Ads no mês", brl(ads_mes), f"até {ult.strftime('%d/%m')} (último dia disponível)" if pd.notna(ult) else "sem dados"),
            card("Investimento por pedido", brl(ads_mes / ped_ate) if ped_ate else "—", "investimento ÷ pedidos do mesmo período"),
        ])
    note("Origem detectada pela URL de entrada (classificação <code>tb_atribuicao_pedido</code>). O custo do Google Ads chega com 1–2 dias de atraso; "
         "o investimento por pedido usa só os pedidos até o último dia com custo disponível.")
