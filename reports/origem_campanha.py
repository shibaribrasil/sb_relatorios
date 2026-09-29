"""Drill-down origem → campanha (ver specs/origem-campanha.md).

Abaixo da tabela por origem, um bloco expansível por origem/mídia mostra as campanhas e suas conversões (pedidos).
- Google pago (cpc): campanha via gclid da URL de entrada do pedido → `tb_gads_clique_campanha` (dataset US, junta aqui no pandas).
  Junta também custo, cliques e compras que o próprio Ads reporta por campanha (`tb_gads_campanha_performance`).
- Demais origens: campanha = `utm_campaign` da URL de entrada (Instagram pago traz o ID da campanha do Meta, sem nome).
Sem regra de negócio nova: a classificação de origem/mídia e o gclid vêm da `tb_atribuicao_pedido`.
"""
import pandas as pd
import streamlit as st

from common import bigquery as bq
from common.design import note, brl

GOOGLE_PAGO = ("google", "cpc")
SEM_CAMPANHA = "(sem campanha)"
NAO_IDENTIFICADA = "(campanha não identificada)"


@st.cache_data(ttl=900)
def carregar_campanhas():
    client = bq.get_client()
    ponte = bq.query_df(client, f"""
        SELECT cd_gclid, cd_campanha, nm_campanha FROM `{bq.PROJECT}.dbt_dw_us_az.tb_gads_clique_campanha`
    """)
    perf = bq.query_df(client, f"""
        SELECT cd_campanha, nm_campanha, dt_data, vl_custo, qt_cliques, qt_conversoes
          FROM `{bq.PROJECT}.dbt_dw_us_az.tb_gads_campanha_performance`
         WHERE dt_data >= DATE '2026-06-01'
    """)
    perf["dt_data"] = pd.to_datetime(perf["dt_data"])
    for c in ["vl_custo", "qt_cliques", "qt_conversoes"]:
        perf[c] = pd.to_numeric(perf[c]).fillna(0.0)
    return {"ponte": ponte, "perf": perf}


@st.cache_data(ttl=1800)
def carregar_atribuicao_primeiro_pedido():
    """gclid e utm_campaign da URL de entrada do 1º pedido de cada cliente (tb_atribuicao_pedido, az). O tb_cliente traz só origem/mídia."""
    client = bq.get_client()
    a = bq.query_df(client, f"""
        SELECT CAST(cd_contato AS STRING) AS cd_contato, ANY_VALUE(ds_gclid) AS ds_gclid, ANY_VALUE(ds_utm_campaign) AS ds_utm_campaign
          FROM `{bq.PROJECT}.dbt_dw_az.tb_atribuicao_pedido`
         WHERE fg_primeiro_pedido_cliente AND cd_contato IS NOT NULL GROUP BY 1
    """)
    return a


def pedidos_com_campanha(pedidos, ponte):
    """pedidos: 1 linha por pedido com origem, midia, ds_gclid, ds_utm_campaign. Acrescenta cd_campanha e campanha."""
    p = pedidos.merge(ponte.rename(columns={"cd_gclid": "ds_gclid", "nm_campanha": "nm_ads"}), on="ds_gclid", how="left")
    google = (p["origem"] == GOOGLE_PAGO[0]) & (p["midia"] == GOOGLE_PAGO[1])
    utm = p["ds_utm_campaign"].where(p["ds_utm_campaign"].notna() & (p["ds_utm_campaign"] != ""))
    p["campanha"] = utm.fillna(SEM_CAMPANHA)
    p.loc[google, "campanha"] = p.loc[google, "nm_ads"].fillna(utm[google]).fillna(NAO_IDENTIFICADA)
    return p


def drill_campanhas(pedidos, ini, fim, meses=None, valor_label="Receita líq.", com_margem=True):
    """pedidos: 1 linha por pedido (origem, midia, ds_gclid, ds_utm_campaign, valor e, se com_margem, marg).
    ini/fim: período (custo do Ads por campanha); meses: lista de inícios de mês se a seleção não for contígua."""
    try:
        dados = carregar_campanhas()
    except Exception as e:
        st.warning(f"Campanhas indisponíveis: {e}")
        return
    p = pedidos_com_campanha(pedidos, dados["ponte"])
    perf = dados["perf"]
    perf = perf[perf["dt_data"].dt.to_period("M").dt.to_timestamp().isin(meses)] if meses else perf[(perf["dt_data"] >= pd.Timestamp(ini)) & (perf["dt_data"] <= pd.Timestamp(fim))]
    custo = perf.groupby("cd_campanha", as_index=False).agg(nm=("nm_campanha", "last"), custo=("vl_custo", "sum"), cliques=("qt_cliques", "sum"), compras_ads=("qt_conversoes", "sum"))

    st.html('<div class="c-label" style="margin:14px 0 6px">Campanhas de cada origem (clique para abrir)</div>')
    combos = (p.groupby(["origem", "midia"]).size().sort_values(ascending=False))
    for i, ((origem, midia), n) in enumerate(combos.items()):
        x = p[(p["origem"] == origem) & (p["midia"] == midia)]
        google = (origem, midia) == GOOGLE_PAGO
        tem_info = (x["campanha"] != SEM_CAMPANHA).any() or google
        if not tem_info:
            continue
        agg = {"pedidos": ("campanha", "size"), "valor": ("valor", "sum")}
        if com_margem:
            agg["marg"] = ("marg", "sum")
        g = x.groupby(["cd_campanha", "campanha"], as_index=False, dropna=False).agg(**agg) if google else x.groupby("campanha", as_index=False).agg(**agg)
        if google:
            g = g.merge(custo, on="cd_campanha", how="outer")
            g["campanha"] = g["campanha"].fillna(g["nm"])
            g[["pedidos", "valor"] + (["marg"] if com_margem else [])] = g[["pedidos", "valor"] + (["marg"] if com_margem else [])].fillna(0)
            g = g[(g["pedidos"] > 0) | (g["custo"] > 0)]
        g = g.sort_values(["pedidos", "valor"], ascending=False)
        tot = int(g["pedidos"].sum())
        with st.expander(f"{origem} · {midia} — {tot} pedidos" + (f" · {brl(float(g['custo'].sum()))} de custo no Ads" if google else ""), expanded=(i == 0)):
            cols = {"Campanha": g["campanha"], "Pedidos": g["pedidos"], "% dos pedidos": g["pedidos"] / tot if tot else 0, valor_label: g["valor"]}
            cfg = {"Pedidos": st.column_config.NumberColumn(width=80), "% dos pedidos": st.column_config.NumberColumn(format="percent", width=110),
                   valor_label: st.column_config.NumberColumn(format="R$ %.2f", width=120)}
            if com_margem:
                cols["Margem de contrib. (R$)"] = g["marg"]
                cols["Margem de contrib. (%)"] = g["marg"] / g["valor"].where(g["valor"] != 0)
                cfg["Margem de contrib. (R$)"] = st.column_config.NumberColumn(format="R$ %.2f", width=150)
                cfg["Margem de contrib. (%)"] = st.column_config.NumberColumn(format="percent", width=150)
            if google:
                cols["Custo no Ads"] = g["custo"]
                cols["Cliques"] = g["cliques"]
                cols["Compras (Ads)"] = g["compras_ads"]
                cfg["Custo no Ads"] = st.column_config.NumberColumn(format="R$ %.2f", width=110)
                cfg["Cliques"] = st.column_config.NumberColumn(format="%d", width=80)
                cfg["Compras (Ads)"] = st.column_config.NumberColumn(format="%.0f", width=110)
                if com_margem:
                    cols["Margem ÷ custo"] = g["marg"] / g["custo"].where(g["custo"] > 0)
                    cfg["Margem ÷ custo"] = st.column_config.NumberColumn(format="%.1f×", width=110)
            st.dataframe(pd.DataFrame(cols), hide_index=True, use_container_width=True, column_config=cfg)
    note("<strong>Google pago:</strong> a campanha vem do <code>gclid</code> da URL de entrada do pedido, cruzado com os cliques do Google Ads (histórico de cliques só desde 15/06/2026 e guardado ~90 dias). "
         "\"(campanha não identificada)\" = pedido de clique Google sem <code>gclid</code> (iOS traz só <code>gbraid</code>/<code>wbraid</code>) ou com clique fora do histórico. "
         "<strong>Custo, cliques e compras (Ads)</strong> são o que o Google reporta por campanha no período e podem incluir compras sem gclid na nossa base — por isso \"Compras (Ads)\" "
         "não bate com \"Pedidos\". <strong>Demais origens:</strong> campanha = <code>utm_campaign</code> da URL (só ~10% dos pedidos trazem UTM); no Instagram pago é o ID da campanha do Meta, "
         "ainda sem nome (não há dados do Meta Ads na base). \"(sem campanha)\" = origem sem <code>utm_campaign</code>.")


def drill_clientes_campanhas(clientes):
    """Drill origem -> campanha para CLIENTES, pela origem do 1º pedido. clientes: 1 linha por cliente com cd_contato,
    ds_origem_primeiro_pedido, ds_midia_primeiro_pedido, fg_recorrente e vl_margem_contribuicao (valor acumulado do cliente).
    Google pago: campanha via gclid do 1º pedido -> tb_gads_clique_campanha; demais: utm_campaign. Sem regra nova: só cruza e soma."""
    try:
        dados = carregar_campanhas()
        atrib = carregar_atribuicao_primeiro_pedido()
    except Exception as e:
        st.warning(f"Campanhas indisponíveis: {e}")
        return
    c = clientes.assign(cd_contato=clientes["cd_contato"].astype(str)).merge(atrib, on="cd_contato", how="left")
    c = c.rename(columns={"ds_origem_primeiro_pedido": "origem", "ds_midia_primeiro_pedido": "midia"})
    p = pedidos_com_campanha(c, dados["ponte"])

    st.html('<div class="c-label" style="margin:14px 0 6px">Campanhas do 1º pedido de cada origem (clique para abrir)</div>')
    combos = p.groupby(["origem", "midia"]).size().sort_values(ascending=False)
    mostrou = 0
    for (origem, midia), n in combos.items():
        x = p[(p["origem"] == origem) & (p["midia"] == midia)]
        google = (origem, midia) == GOOGLE_PAGO
        if not ((x["campanha"] != SEM_CAMPANHA).any() or google):
            continue
        g = x.groupby("campanha", as_index=False).agg(clientes=("cd_contato", "size"), rec=("fg_recorrente", "sum"), marg=("vl_margem_contribuicao", "sum"))
        g = g.sort_values(["clientes", "marg"], ascending=False)
        tot = int(g["clientes"].sum())
        with st.expander(f"{origem} · {midia} — {tot} clientes", expanded=(mostrou == 0)):
            st.dataframe(pd.DataFrame({
                "Campanha": g["campanha"], "Clientes": g["clientes"], "% dos clientes": g["clientes"] / tot if tot else 0, "Recorrentes": g["rec"],
                "% recorrentes": g["rec"] / g["clientes"], "Valor médio (R$)": g["marg"] / g["clientes"]}),
                hide_index=True, use_container_width=True,
                column_config={"Clientes": st.column_config.NumberColumn(format="%d", width=90), "% dos clientes": st.column_config.NumberColumn(format="percent", width=120),
                               "Recorrentes": st.column_config.NumberColumn(format="%d", width=110), "% recorrentes": st.column_config.NumberColumn(format="percent", width=120),
                               "Valor médio (R$)": st.column_config.NumberColumn(format="R$ %.2f", width=140)})
        mostrou += 1
    note("<strong>Google pago:</strong> a campanha vem do <code>gclid</code> da URL de entrada do <strong>1º pedido</strong>, cruzado com os cliques do Ads — que só existem desde 15/06/2026 "
         "e o Google guarda ~90 dias: clientes que entraram antes disso, ou com clique sem <code>gclid</code> (iPhone), ficam em \"" + NAO_IDENTIFICADA + "\". "
         "<strong>Demais origens:</strong> campanha = <code>utm_campaign</code> (só ~10% dos pedidos trazem UTM). <strong>Valor médio</strong> = margem de contribuição acumulada do cliente até hoje; "
         "clientes de entrada recente ainda não tiveram tempo de recomprar, então <strong>% recorrentes</strong> baixo em campanha nova não é ruim. Poucos clientes por linha: leia como hipótese.")
