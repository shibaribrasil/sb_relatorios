"""Relatório Estoque & Reposição — Shibari Brasil (camada semanal, R7 do plano de KPIs).

Regras e definição de cada bloco: ver specs/estoque.md (v2). Toda regra (classificação de risco, cobertura, lead time, ABC,
ciclo de vida, quantidade sugerida, giro/GMROI) mora no dbt (`tb_estoque_analitico`, `tb_sugestao_reposicao`,
`tb_giro_papel_mes`, `tb_estoque_ruptura`); aqui só se filtra, soma e apresenta. Tudo na região us-east4 (uma consulta por tabela).

Escopo da 1ª leva (Hugo, 29/09/2026): só produto físico comercial. Insumos/matéria-prima/suprimento ficam para a 2ª leva
(categorias `[Interno] Insumos` e `[Interno] Inativos` saem de todas as contas).

ÁREAS (abas, na ordem do fluxo de decisão — v3, 29/09/2026):
  1. Compras — o que comprar ou produzir agora e quanto do orçamento isso consome (lista de compra, fora da lista, produção própria, compras em aberto).
  2. Cobertura e rupturas — vou ficar sem produto antes da reposição chegar? onde já faltou? (cobertura × lead time, rupturas).
  3. Volume e giro — quanto capital está em estoque e com que velocidade ele vira (capital hoje, giro/GMROI, o que gira devagar).
  4. Cadastro — os dados que alimentam as outras áreas estão completos?
"""
import re

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from common import bigquery as bq
from common.design import COLORS, METRIC_COLORS, inject_css, card, render_cards, section_title, note, plotly_layout, brl, pct, nome_curto
from common.frescor import carregar_frescor, badge_atualizacao, detalhe_atualizacao, alerta_atraso
from reports.vendas_margem import _hoje_brt

TABELAS = ("tb_estoque_analitico", "tb_sugestao_reposicao", "tb_giro_papel_mes", "tb_estoque_ruptura", "tb_orcamento_mercadoria_mes", "tb_compra")
EXTRATORES = ("bling_products", "bling_orders", "bling_purchases")

CATEGORIAS_FORA = ("[Interno] Insumos", "[Interno] Inativos")  # 2ª leva
PAPEIS = ["Core", "Complementar", "Impulso", "Sem papel"]
CICLOS_NAO_REPOR = ("Em Saída", "Descontinuado")
JANELAS_RUPTURA = {"Últimos 30 dias": 30, "Últimos 60 dias": 60, "Desde o início da série (02/07)": None}
TOP_COBERTURA = 15


@st.cache_data(ttl=900)
def carregar_dados():
    client = bq.get_client()
    A = f"{bq.PROJECT}.dbt_dw_az"
    fora = ", ".join(f"'{c}'" for c in CATEGORIAS_FORA)
    est = bq.query_df(client, f"""
        SELECT cd_produto_bling, cd_produto, nm_produto, nm_produto_completo, ds_categoria, ds_papel, ds_origem_produto, nm_fornecedor,
               fg_produto_fabricado, ds_ciclo, qt_lead_time, qt_estoque_minimo, qt_estoque_atual, vl_custo_cadastro, vl_custo_ultima_compra,
               qt_cobertura_atual, qt_cobertura_total, qt_cobertura_max_efetiva, ds_classificacao_risco, ds_classificacao_abc,
               qt_pecas_sessenta_dias, qt_item_compra_pendente
          FROM `{A}.tb_estoque_analitico`
         WHERE COALESCE(ds_categoria, '') NOT IN ({fora})
    """)
    sug = bq.query_df(client, f"SELECT * FROM `{A}.tb_sugestao_reposicao` ORDER BY nr_prioridade")
    orc = bq.query_df(client, f"""
        SELECT vl_orcado, vl_consumido, vl_disponivel FROM `{A}.tb_orcamento_mercadoria_mes`
         WHERE dt_prim_dia_mes = DATE_TRUNC(CURRENT_DATE('America/Sao_Paulo'), MONTH)
    """)
    giro = bq.query_df(client, f"SELECT * FROM `{A}.tb_giro_papel_mes`")
    rup = bq.query_df(client, f"SELECT cd_produto_bling, dt_inicio, dt_fim FROM `{A}.tb_estoque_ruptura`")
    pos = bq.query_df(client, f"SELECT MAX(dt_posicao) AS dt FROM `{A}.tb_estoque_posicao_dia`")
    comp = bq.query_df(client, f"""
        SELECT cd_compra, MAX(dt_compra) AS dt_compra, MAX(dt_prevista_compra) AS dt_prevista, ANY_VALUE(nm_fornecedor) AS nm_fornecedor,
               COUNT(*) AS itens, SUM(vl_total_item) AS vl_total
          FROM `{A}.tb_compra` WHERE ds_status_compra = 'EM ABERTO' GROUP BY cd_compra
    """)
    for c in ["qt_lead_time", "qt_estoque_minimo", "qt_estoque_atual", "vl_custo_cadastro", "vl_custo_ultima_compra", "qt_cobertura_atual",
              "qt_cobertura_total", "qt_cobertura_max_efetiva", "qt_pecas_sessenta_dias", "qt_item_compra_pendente"]:
        est[c] = pd.to_numeric(est[c])
    for c in ["qt_estoque_atual", "qt_pecas_sessenta_dias", "qt_item_compra_pendente"]:
        est[c] = est[c].fillna(0.0)
    est["fg_produto_fabricado"] = est["fg_produto_fabricado"].fillna(False).astype(bool)
    est["ds_papel"] = est["ds_papel"].fillna("Sem papel")
    est["ds_ciclo"] = est["ds_ciclo"].fillna("Sem ciclo")
    est["risco"] = est["ds_classificacao_risco"].fillna("").str.replace(r"^[a-h]\.\s*", "", regex=True)
    est["cod_risco"] = est["ds_classificacao_risco"].fillna("").str[:1]
    cad = est["vl_custo_cadastro"].where(est["vl_custo_cadastro"] > 0)
    ult = est["vl_custo_ultima_compra"].where(est["vl_custo_ultima_compra"] > 0)
    est["vl_custo_base"] = cad.fillna(ult)  # base da spec: cadastro, com fallback na última compra
    est["vl_estoque_custo"] = est["qt_estoque_atual"].clip(lower=0) * est["vl_custo_base"].fillna(0.0)
    for c in ["vl_custo_sugerido", "qt_sugerida", "qt_estoque_atual", "qt_estoque_minimo", "qt_item_compra_pendente", "qt_pecas_sessenta_dias"]:
        sug[c] = pd.to_numeric(sug[c]).fillna(0.0)
    giro["dt_mes"] = pd.to_datetime(giro["dt_mes"])
    for c in giro.columns.drop(["dt_mes", "ds_papel", "ds_origem"]):
        giro[c] = pd.to_numeric(giro[c])
    for c in ["dt_inicio", "dt_fim"]:
        rup[c] = pd.to_datetime(rup[c])
    for c in ["dt_compra", "dt_prevista"]:
        comp[c] = pd.to_datetime(comp[c])
    comp["vl_total"] = pd.to_numeric(comp["vl_total"]).fillna(0.0)
    return {"est": est, "sug": sug, "orc": orc, "giro": giro, "rup": rup, "dt_pos": pd.to_datetime(pos["dt"].iloc[0]), "comp": comp}


def _tabela(df, config=None, altura=None):
    kw = {"height": altura} if altura else {}
    st.dataframe(df, hide_index=True, use_container_width=True, column_config=config or {}, **kw)


def _motivo_fora_da_sugestao(r):
    """Por que um produto em risco não está na lista de compra (só rotula; a regra de compra é a da tb_sugestao_reposicao)."""
    if r["fg_produto_fabricado"]:
        return "Produção própria (ver bloco abaixo)"
    if r["ds_ciclo"] in CICLOS_NAO_REPOR:
        return f"Ciclo do produto: {r['ds_ciclo']} (não repor)"
    if r["ds_ciclo"] == "Em Teste":
        return "Em teste, critério ainda não atingido"
    if pd.isna(r["vl_custo_ultima_compra"]) or r["vl_custo_ultima_compra"] <= 0:
        return "Sem custo de última compra"
    return "Fora da regra de compra (cobertura suficiente com a compra pendente)"


# ═══ COMPRAR AGORA ═══
def _bloco_comprar(d):
    est, sug, orc, comp = d["est"], d["sug"], d["orc"], d["comp"]
    section_title("Comprar agora — revenda")
    total = float(sug["vl_custo_sugerido"].sum())
    disp = float(orc["vl_disponivel"].iloc[0]) if not orc.empty else None
    consumido = float(orc["vl_consumido"].iloc[0]) if not orc.empty else None
    orcado = float(orc["vl_orcado"].iloc[0]) if not orc.empty else None
    saldo = (disp - total) if disp is not None else None
    render_cards([
        card("Produtos na lista de compra", f"{len(sug)}", "revenda, já filtrada por ciclo de vida, caixa fechada e pedido mínimo"),
        card("Custo da lista", brl(total), "quantidade sugerida × custo da última compra"),
        card("Orçamento de mercadoria disponível", brl(disp) if disp is not None else "—",
             f"orçado {brl(orcado)} · já consumido {brl(consumido)}" if orcado is not None else "sem orçamento cadastrado para o mês"),
        card("Saldo depois da lista", brl(saldo) if saldo is not None else "—", "orçamento disponível − custo da lista",
             variant=("ok" if saldo >= 0 else "bad") if saldo is not None else "neutral"),
    ])
    if sug.empty:
        st.info("Nenhum produto na lista de compra hoje.")
    else:
        t = sug.copy()
        t["acum"] = t["vl_custo_sugerido"].cumsum()
        t["cabe"] = "—" if disp is None else (t["acum"] <= disp).map({True: "Sim", False: "Não"})
        t["risco"] = t["ds_classificacao_risco"].fillna("").str.replace(r"^[a-h]\.\s*", "", regex=True)
        out = pd.DataFrame({
            "#": t["nr_prioridade"], "Produto": t["nm_produto_completo"], "Fornecedor": t["nm_fornecedor"].fillna("—"), "Risco": t["risco"],
            "Estoque": t["qt_estoque_atual"], "Mínimo": t["qt_estoque_minimo"], "Compra pendente": t["qt_item_compra_pendente"],
            "Venda 60d": t["qt_pecas_sessenta_dias"], "Sugerida": t["qt_sugerida"], "Custo sugerido": t["vl_custo_sugerido"],
            "Custo acumulado": t["acum"], "Cabe no orçamento": t["cabe"], "Ciclo": t["ds_ciclo"].fillna("—"), "Papel": t["ds_papel"].fillna("—")})
        _tabela(out, {"#": st.column_config.NumberColumn(format="%d", width=40), "Produto": st.column_config.TextColumn(width="large"),
                      "Estoque": st.column_config.NumberColumn(format="%d", width=70), "Mínimo": st.column_config.NumberColumn(format="%d", width=70),
                      "Compra pendente": st.column_config.NumberColumn(format="%d", width=100), "Venda 60d": st.column_config.NumberColumn(format="%d", width=80),
                      "Sugerida": st.column_config.NumberColumn(format="%d", width=80),
                      "Custo sugerido": st.column_config.NumberColumn(format="R$ %.2f", width=110),
                      "Custo acumulado": st.column_config.NumberColumn(format="R$ %.2f", width=120)},
                altura=min(38 + 35 * len(out), 520))
    note("Lista da <code>tb_sugestao_reposicao</code> (a mesma da rotina de segunda e sexta), na ordem de prioridade. A regra de quantidade, o "
         "ciclo de vida (Em Saída e Descontinuado nunca entram), a caixa fechada e o pedido mínimo por fornecedor moram no dbt. "
         "O corte pelo caixa é decisão sua: a coluna \"Cabe no orçamento\" só mostra até onde o orçamento do mês disponível chega, na ordem da lista.")

    fora = est[est["cod_risco"].isin(["a", "b", "c"]) & (est["qt_pecas_sessenta_dias"] > 0) & ~est["cd_produto"].isin(sug["cd_produto_nuvem"])].copy()
    if not fora.empty:
        st.markdown("**Em risco e fora da lista de compra** — com venda nos últimos 60 dias, mas sem sugestão automática")
        fora["motivo"] = fora.apply(_motivo_fora_da_sugestao, axis=1)
        fora = fora.sort_values(["cod_risco", "qt_pecas_sessenta_dias"], ascending=[True, False])
        out = pd.DataFrame({"Produto": fora["nm_produto_completo"], "Risco": fora["risco"], "Estoque": fora["qt_estoque_atual"], "Mínimo": fora["qt_estoque_minimo"],
                            "Compra pendente": fora["qt_item_compra_pendente"], "Venda 60d": fora["qt_pecas_sessenta_dias"],
                            "Por que não está na lista": fora["motivo"]})
        _tabela(out, {"Produto": st.column_config.TextColumn(width="large"), "Por que não está na lista": st.column_config.TextColumn(width="large"),
                      "Estoque": st.column_config.NumberColumn(format="%d", width=70), "Mínimo": st.column_config.NumberColumn(format="%d", width=70),
                      "Compra pendente": st.column_config.NumberColumn(format="%d", width=100), "Venda 60d": st.column_config.NumberColumn(format="%d", width=80)},
                altura=min(38 + 35 * len(out), 420))
        note("Motivo é só um rótulo do que já explica a ausência (produção própria, ciclo, teste, custo faltando); não é uma segunda regra de compra.")


def _bloco_compras_abertas(comp):
    section_title("Compras já feitas — em aberto no Bling")
    if comp.empty:
        st.info("Nenhuma compra em aberto no Bling.")
        return
    hoje = pd.Timestamp(_hoje_brt())
    c = comp.sort_values("dt_prevista")
    out = pd.DataFrame({"Compra": c["cd_compra"].astype(str), "Fornecedor": c["nm_fornecedor"].fillna("—"), "Data da compra": c["dt_compra"].dt.date,
                        "Previsão": c["dt_prevista"].dt.date, "Itens": c["itens"], "Valor": c["vl_total"],
                        "Situação": ["Previsão vencida" if pd.notna(p) and p < hoje else "No prazo" for p in c["dt_prevista"]]})
    _tabela(out, {"Data da compra": st.column_config.DateColumn(format="DD/MM/YYYY"), "Previsão": st.column_config.DateColumn(format="DD/MM/YYYY"),
                  "Itens": st.column_config.NumberColumn(format="%d", width=60), "Valor": st.column_config.NumberColumn(format="R$ %.2f", width=110)})
    if (out["Situação"] == "Previsão vencida").any():
        note("<strong>Compra em aberto com previsão vencida</strong> conta como reposição a caminho no cálculo de cobertura. Se ela já chegou ou foi cancelada, "
             "dê baixa no Bling — senão o risco de alguns produtos aparece menor do que é.", variant="warn")


# ═══ 2. PRODUÇÃO PRÓPRIA ═══
def _bloco_producao(est):
    section_title("Produzir — produção própria abaixo do mínimo")
    p = est[est["fg_produto_fabricado"] & (est["qt_estoque_minimo"] > 0) & (est["qt_estoque_atual"] < est["qt_estoque_minimo"])].copy()
    if p.empty:
        st.info("Nenhum produto de produção própria abaixo do mínimo.")
        return
    p["falta"] = (p["qt_estoque_minimo"] - p["qt_estoque_atual"]).clip(lower=0)
    g = p.groupby("nm_produto", as_index=False).agg(var=("cd_produto_bling", "nunique"), est=("qt_estoque_atual", "sum"), mini=("qt_estoque_minimo", "sum"),
                                                     falta=("falta", "sum"), venda=("qt_pecas_sessenta_dias", "sum")).sort_values(["venda", "falta"], ascending=False)
    render_cards([card("Famílias abaixo do mínimo", f"{len(g)}", f"{int(g['var'].sum())} variações"),
                  card("Unidades a produzir para o mínimo", f"{int(g['falta'].sum())}", "soma de (mínimo − estoque) das variações abaixo")])
    _tabela(pd.DataFrame({"Família": g["nm_produto"], "Variações abaixo": g["var"], "Estoque": g["est"], "Mínimo": g["mini"], "Faltam": g["falta"], "Venda 60d": g["venda"]}),
            {"Família": st.column_config.TextColumn(width="large"), "Variações abaixo": st.column_config.NumberColumn(format="%d", width=100),
             "Estoque": st.column_config.NumberColumn(format="%d", width=80), "Mínimo": st.column_config.NumberColumn(format="%d", width=80),
             "Faltam": st.column_config.NumberColumn(format="%d", width=80), "Venda 60d": st.column_config.NumberColumn(format="%d", width=90)},
            altura=min(38 + 35 * len(g), 420))
    note("Produtos com <code>fg_produto_fabricado</code>, agrupados por família (o nome-base sem a variação). A necessidade de matéria-prima "
         "(fio de juta, algodão, óleo) fica para a segunda leva — os insumos não estão nesta página.")


# ═══ COBERTURA × LEAD TIME ═══
def _grafico_cobertura(v):
    v = v.sort_values("qt_pecas_sessenta_dias", ascending=False).head(TOP_COBERTURA).iloc[::-1]
    y = list(range(len(v)))
    nomes = [nome_curto(n, 36) for n in v["nm_produto_completo"]]
    cor = [COLORS["danger"] if c < 0 else (COLORS["warning"] if c < 15 else METRIC_COLORS["receita"]) for c in v["qt_cobertura_total"].fillna(0)]
    fig = go.Figure()
    fig.add_bar(y=y, x=v["qt_cobertura_atual"].clip(upper=150), orientation="h", marker_color=cor, name="Cobertura atual (dias)", showlegend=False,
                customdata=list(zip(v["nm_produto_completo"], v["qt_estoque_atual"], v["qt_pecas_sessenta_dias"])),
                hovertemplate="%{customdata[0]}<br>cobertura %{x:.0f} dias · estoque %{customdata[1]:.0f} · venda 60d %{customdata[2]:.0f}<extra></extra>")
    fig.add_trace(go.Scatter(y=y, x=v["qt_lead_time"], mode="markers", name="Lead time (dias)",
                             marker=dict(symbol="line-ns-open", size=18, color=COLORS["text"], line=dict(width=3, color=COLORS["text"])),
                             hovertemplate="lead time %{x:.0f} dias<extra></extra>"))
    plotly_layout(fig, height=60 + 28 * len(v), xaxis=dict(title="dias (barra cortada em 150)", gridcolor=COLORS["grid"]),
                  yaxis=dict(tickmode="array", tickvals=y, ticktext=nomes, automargin=True))
    return fig


def _bloco_cobertura(est):
    section_title("Quanto tempo o estoque dura — cobertura × lead time")
    v = est[(est["qt_pecas_sessenta_dias"] > 0) & (~est["fg_produto_fabricado"])].copy()
    if v.empty:
        st.info("Sem produtos com venda nos últimos 60 dias.")
        return
    g = v.groupby("ds_papel").agg(prod=("cd_produto_bling", "nunique"), cob=("qt_cobertura_atual", "median"), lt=("qt_lead_time", "mean"),
                                  antes=("qt_cobertura_total", lambda s: int((s < 0).sum())), venda=("qt_pecas_sessenta_dias", "sum")).reindex(PAPEIS).dropna(subset=["prod"]).reset_index()
    render_cards([
        card("Vão acabar antes da reposição chegar", f"{int((v['qt_cobertura_total'] < 0).sum())}",
             f"de {len(v)} produtos de revenda com venda em 60 dias · cobertura total negativa"),
        card("Cobertura mediana", f"{v['qt_cobertura_atual'].median():.0f} dias".replace(".", ","), "estoque ÷ venda diária (60 dias)"),
        card("Lead time médio", f"{v['qt_lead_time'].mean():.0f} dias".replace(".", ","), "cadastro do produto no Bling"),
    ])
    _tabela(pd.DataFrame({"Papel": g["ds_papel"], "Produtos com venda": g["prod"], "Cobertura mediana (dias)": g["cob"], "Lead time médio (dias)": g["lt"],
                          "Acabam antes da reposição": g["antes"], "Venda 60d (un.)": g["venda"]}),
            {"Produtos com venda": st.column_config.NumberColumn(format="%d", width=110), "Cobertura mediana (dias)": st.column_config.NumberColumn(format="%.0f", width=150),
             "Lead time médio (dias)": st.column_config.NumberColumn(format="%.0f", width=150), "Acabam antes da reposição": st.column_config.NumberColumn(format="%d", width=170),
             "Venda 60d (un.)": st.column_config.NumberColumn(format="%d", width=110)})
    st.markdown(f"**Os {TOP_COBERTURA} produtos que mais vendem** — cobertura atual (barra) contra o lead time (traço)")
    with st.container(border=True):
        st.plotly_chart(_grafico_cobertura(v), use_container_width=True)
    note("<strong>Cobertura atual</strong> = estoque ÷ (venda 60 dias ÷ 60). <strong>Cobertura total</strong> = cobertura atual + compra pendente em dias − lead time: "
         "abaixo de zero significa que o produto acaba antes de a reposição chegar. Barra vermelha = cobertura total negativa; âmbar = menos de 15 dias de folga. "
         "O traço é o lead time cadastrado; se a barra não passa dele, comprar hoje já é tarde. Com ~1 pedido por dia, a venda de 60 dias de itens de baixo giro é poucas unidades: leia com cautela.")


# ═══ CAPITAL EM ESTOQUE ═══
def _bloco_capital(d):
    est, giro = d["est"], d["giro"]
    section_title("Quanto capital está em estoque")
    tot = float(est["vl_estoque_custo"].sum())
    parado = float(est.loc[est["cod_risco"].isin(["e", "f"]), "vl_estoque_custo"].sum())
    render_cards([
        card("Estoque hoje, a custo", brl(tot), f"{int((est['qt_estoque_atual'] > 0).sum())} produtos com estoque · custo de cadastro (fallback: última compra)"),
        card("Em sobreestoque ou encalhado", brl(parado), f"{pct(parado / tot if tot else None, 0)} do valor · cobertura acima de 60 dias ou sem venda em 60 dias",
             variant="warn" if tot and parado / tot > 0.3 else "neutral"),
    ])
    h = est[est["qt_estoque_atual"] > 0].groupby("ds_papel").agg(prod=("cd_produto_bling", "nunique"), un=("qt_estoque_atual", "sum"), val=("vl_estoque_custo", "sum"),
                                                                  par=("vl_estoque_custo", lambda s: float(s[est.loc[s.index, "cod_risco"].isin(["e", "f"])].sum()))).reindex(PAPEIS).dropna(subset=["prod"]).reset_index()
    _tabela(pd.DataFrame({"Papel": h["ds_papel"], "Produtos com estoque": h["prod"], "Unidades": h["un"], "Valor a custo": h["val"],
                          "% do valor": h["val"] / tot if tot else 0, "Em sobreestoque/encalhado": h["par"]}),
            {"Produtos com estoque": st.column_config.NumberColumn(format="%d", width=130), "Unidades": st.column_config.NumberColumn(format="%d", width=90),
             "Valor a custo": st.column_config.NumberColumn(format="R$ %.2f", width=120), "% do valor": st.column_config.NumberColumn(format="percent", width=100),
             "Em sobreestoque/encalhado": st.column_config.NumberColumn(format="R$ %.2f", width=180)})

    gm = giro[giro["ds_papel"] != "Sem papel"].copy()
    if gm.empty:
        return
    section_title("Com que velocidade o estoque vira — giro e retorno")
    meses = sorted(gm["dt_mes"].unique(), reverse=True)
    mes_atual = pd.Timestamp(_hoje_brt()).replace(day=1)
    rot = lambda m: pd.Timestamp(m).strftime("%m/%Y") + (" (parcial)" if pd.Timestamp(m) == mes_atual else "")
    mes = st.selectbox("Mês do giro e do GMROI", options=meses, format_func=rot)
    g = gm[gm["dt_mes"] == mes]
    est_med, cmv, marg = g["vl_estoque_medio_custo"].sum(), g["vl_cmv"].sum(), g["vl_margem_contribuicao"].sum()
    dias = g["qt_dias_periodo"].max()
    giro_ano = (cmv / est_med) * 365 / dias if est_med and dias else None
    render_cards([
        card("Estoque médio a custo", brl(est_med), f"média diária no mês, Core + Complementar + Impulso · {rot(mes)}"),
        card("Giro anualizado", f"{giro_ano:.1f}×".replace(".", ",") if giro_ano is not None else "—", "CMV ÷ estoque médio, anualizado · referência 6–8× ao ano"),
        card("GMROI do mês", f"{marg / est_med:.2f}".replace(".", ",") if est_med else "—", "margem de contribuição ÷ estoque médio · referência > 2,5 (anualizado)"),
        card("Estoque parado", brl(g["vl_estoque_parado"].sum()), f"{int(g['qt_itens_parados'].sum())} itens sem giro no mês"),
    ])
    out = g.sort_values(["ds_papel", "ds_origem"])
    _tabela(pd.DataFrame({"Papel": out["ds_papel"], "Origem": out["ds_origem"], "Produtos": out["qt_produtos"], "Estoque médio (custo)": out["vl_estoque_medio_custo"],
                          "CMV": out["vl_cmv"], "Giro anualizado": out["vl_giro_anualizado"], "Cobertura (dias)": out["qt_cobertura_dias"], "GMROI": out["vl_gmroi"],
                          "Estoque parado": out["vl_estoque_parado"], "Itens parados": out["qt_itens_parados"]}),
            {"Produtos": st.column_config.NumberColumn(format="%d", width=80), "Estoque médio (custo)": st.column_config.NumberColumn(format="R$ %.2f", width=150),
             "CMV": st.column_config.NumberColumn(format="R$ %.2f", width=100), "Giro anualizado": st.column_config.NumberColumn(format="%.1f", width=110),
             "Cobertura (dias)": st.column_config.NumberColumn(format="%.0f", width=110), "GMROI": st.column_config.NumberColumn(format="%.2f", width=80),
             "Estoque parado": st.column_config.NumberColumn(format="R$ %.2f", width=120), "Itens parados": st.column_config.NumberColumn(format="%d", width=100)})
    fig = go.Figure()
    cores = {"Core": METRIC_COLORS["receita"], "Complementar": METRIC_COLORS["margem_contribuicao"], "Impulso": COLORS["text_muted"]}
    ev = gm.groupby(["dt_mes", "ds_papel"])["vl_estoque_medio_custo"].sum().reset_index()
    for papel in ["Core", "Complementar", "Impulso"]:
        e = ev[ev["ds_papel"] == papel].sort_values("dt_mes")
        fig.add_bar(x=[m.strftime("%m/%Y") for m in e["dt_mes"]], y=e["vl_estoque_medio_custo"], name=papel, marker_color=cores[papel],
                    hovertemplate="%{x}<br>R$ %{y:,.0f}<extra>" + papel + "</extra>")
    plotly_layout(fig, height=260, barmode="stack", xaxis=dict(type="category"), yaxis=dict(tickprefix="R$ ", gridcolor=COLORS["grid"]))
    st.markdown("**Estoque médio a custo por mês e papel**")
    with st.container(border=True):
        st.plotly_chart(fig, use_container_width=True)
    note("Giro e GMROI vêm da <code>tb_giro_papel_mes</code> (custo vigente de cada dia; posição diária desde 02/07/2026 — só 3 meses de histórico). "
         "O mês corrente é parcial: cobertura e giro usam só os dias já passados. GMROI do mês não é anualizado; a referência de mercado (&gt; 2,5) é anual. "
         "Produtos \"Sem papel\" (em geral insumos e uso e consumo) ficam fora desta leva. Com poucas unidades vendidas por mês, o giro oscila muito: olhe a tendência de vários meses.")


# ═══ RUPTURAS ═══
def _bloco_rupturas(d):
    est, rup, dt_pos = d["est"], d["rup"], d["dt_pos"]
    section_title("O que já faltou — rupturas (dias sem estoque)")
    sel = st.selectbox("Janela", options=list(JANELAS_RUPTURA))
    n = JANELAS_RUPTURA[sel]
    ini = rup["dt_inicio"].min() if n is None else dt_pos - pd.Timedelta(days=n - 1)
    escopo = est[(est["qt_pecas_sessenta_dias"] > 0) | est["ds_classificacao_abc"].isin(["A", "B"])][
        ["cd_produto_bling", "nm_produto_completo", "ds_papel", "ds_classificacao_abc", "qt_pecas_sessenta_dias", "ds_ciclo"]]
    r = rup.merge(escopo, on="cd_produto_bling", how="inner")
    r["a"] = r["dt_inicio"].clip(lower=ini)
    r["b"] = r["dt_fim"].clip(upper=dt_pos)
    r = r[r["a"] <= r["b"]].copy()
    r["dias"] = (r["b"] - r["a"]).dt.days + 1
    if r.empty:
        st.info("Nenhuma ruptura de produto com venda ou curva A/B na janela.")
        return
    janela_dias = (dt_pos - ini).days + 1
    g = r.groupby(["cd_produto_bling", "nm_produto_completo", "ds_papel", "ds_classificacao_abc", "ds_ciclo"], as_index=False, dropna=False).agg(dias=("dias", "sum"))
    atual = r[r["dt_fim"] >= dt_pos].set_index("cd_produto_bling")["dt_inicio"]
    g["hoje"] = g["cd_produto_bling"].isin(atual.index)
    g["desde"] = g["cd_produto_bling"].map(atual)
    g = g.sort_values(["ds_classificacao_abc", "dias"], ascending=[True, False]).sort_values("dias", ascending=False, kind="stable")
    a_rup = g[g["ds_classificacao_abc"] == "A"]
    render_cards([
        card("Produtos que ficaram sem estoque", f"{len(g)}", f"com venda em 60 dias ou curva A/B · janela de {janela_dias} dias"),
        card("Sem estoque hoje", f"{int(g['hoje'].sum())}", "ruptura ainda aberta na última posição diária"),
        card("Curva A com ruptura", f"{len(a_rup)}", f"{int(a_rup['dias'].sum())} dias-produto sem estoque", variant="bad" if len(a_rup) else "ok"),
    ])
    out = pd.DataFrame({"Produto": g["nm_produto_completo"], "Papel": g["ds_papel"], "Curva": g["ds_classificacao_abc"].fillna("—"), "Dias sem estoque": g["dias"],
                        "% da janela": g["dias"] / janela_dias, "Sem estoque hoje": g["hoje"].map({True: "Sim", False: "Não"}), "Em ruptura desde": g["desde"].dt.date})
    _tabela(out, {"Produto": st.column_config.TextColumn(width="large"), "Curva": st.column_config.TextColumn(width=60),
                  "Dias sem estoque": st.column_config.NumberColumn(format="%d", width=120), "% da janela": st.column_config.NumberColumn(format="percent", width=100),
                  "Em ruptura desde": st.column_config.DateColumn(format="DD/MM/YYYY")}, altura=min(38 + 35 * len(out), 460))
    note("Dia sem estoque = posição diária com estoque ≤ 0 (série <code>tb_estoque_posicao_dia</code>, desde 02/07/2026; produtos que já começam a série em zero contam desde o primeiro dia). "
         "Só entram produtos com venda nos últimos 60 dias ou curva A/B — a ruptura de produto sem histórico de venda não é perda. "
         "Não estimamos venda perdida: com ~1 pedido por dia, qualquer estimativa por produto seria chute.")


# ═══ QUALIDADE DO CADASTRO ═══
def _bloco_cadastro(est):
    section_title("Campos que faltam no cadastro")
    rel = est[(est["qt_estoque_atual"] > 0) | (est["qt_pecas_sessenta_dias"] > 0)].copy()
    prob = {
        "Sem estoque mínimo": rel["qt_estoque_minimo"].isna() | (rel["qt_estoque_minimo"] <= 0),
        "Sem lead time": rel["qt_lead_time"].isna(),
        "Sem origem": rel["ds_origem_produto"].isna(),
        "Sem papel": rel["ds_papel"] == "Sem papel",
        "Sem custo (cadastro e última compra)": rel["vl_custo_base"].isna(),
        "Sem classificação de risco": rel["cod_risco"] == "h",
    }
    render_cards([card(k, f"{int(m.sum())}", f"de {len(rel)} produtos com estoque ou venda", variant="warn" if m.sum() else "ok") for k, m in prob.items()])
    escolha = st.selectbox("Ver a lista de", options=list(prob))
    t = rel[prob[escolha]].sort_values("qt_pecas_sessenta_dias", ascending=False)
    if t.empty:
        st.success("Nenhum produto com esse problema.")
    else:
        _tabela(pd.DataFrame({"Produto": t["nm_produto_completo"], "Papel": t["ds_papel"], "Origem": t["ds_origem_produto"].fillna("—"), "Estoque": t["qt_estoque_atual"],
                              "Mínimo": t["qt_estoque_minimo"], "Lead time": t["qt_lead_time"], "Venda 60d": t["qt_pecas_sessenta_dias"], "Risco": t["risco"]}),
                {"Produto": st.column_config.TextColumn(width="large"), "Estoque": st.column_config.NumberColumn(format="%d", width=70),
                 "Mínimo": st.column_config.NumberColumn(format="%d", width=70), "Lead time": st.column_config.NumberColumn(format="%d", width=80),
                 "Venda 60d": st.column_config.NumberColumn(format="%d", width=80)}, altura=min(38 + 35 * len(t), 420))
    note("O risco depende de mínimo, lead time e origem: produto sem esses campos é classificado com o que sobrou e pode parecer estável sem ser. "
         "A correção é no Bling (Melhorias Manuais de Dados). Considera produtos com estoque ou venda em 60 dias, sem insumos. \"Sem classificação\" é a lacuna "
         "conhecida da regra de risco (estoque acima do mínimo com cobertura entre 30 e 45 dias).")


# ═══ GIRANDO DEVAGAR ═══
def _bloco_giro_lento(est):
    section_title("O que gira devagar — capital parado")
    t = est[est["cod_risco"].isin(["e", "f"]) & (est["qt_estoque_atual"] > 0)].copy()
    if t.empty:
        st.info("Nenhum produto em sobreestoque ou encalhado.")
        return
    enc, sob = t[t["cod_risco"] == "f"], t[t["cod_risco"] == "e"]
    render_cards([card("Encalhado (sem venda em 60 dias)", brl(enc["vl_estoque_custo"].sum()), f"{len(enc)} produtos com estoque"),
                  card("Sobreestoque (cobertura > 60 dias)", brl(sob["vl_estoque_custo"].sum()), f"{len(sob)} produtos")])
    t = t.sort_values("vl_estoque_custo", ascending=False)
    _tabela(pd.DataFrame({"Produto": t["nm_produto_completo"], "Situação": t["risco"], "Papel": t["ds_papel"], "Ciclo": t["ds_ciclo"], "Estoque": t["qt_estoque_atual"],
                          "Venda 60d": t["qt_pecas_sessenta_dias"], "Cobertura (dias)": t["qt_cobertura_atual"], "Cobertura máx. (dias)": t["qt_cobertura_max_efetiva"],
                          "Valor a custo": t["vl_estoque_custo"]}),
            {"Produto": st.column_config.TextColumn(width="large"), "Estoque": st.column_config.NumberColumn(format="%d", width=70),
             "Venda 60d": st.column_config.NumberColumn(format="%d", width=80), "Cobertura (dias)": st.column_config.NumberColumn(format="%.0f", width=110),
             "Cobertura máx. (dias)": st.column_config.NumberColumn(format="%.0f", width=130), "Valor a custo": st.column_config.NumberColumn(format="R$ %.2f", width=110)},
            altura=min(38 + 35 * len(t), 460))
    note("Isto <strong>não é recomendação de compra</strong>: é alerta para descontinuar, baixar o mínimo ou agir na vitrine. A ação no site (tirar da home, oferta) "
         "fica em <em>Gestão de Produtos</em>; aqui só o capital parado. Cobertura máxima = teto do papel do produto (Core 90 dias, Complementar 150, Impulso 120).")


def render():
    inject_css()
    with st.spinner("Carregando dados do BigQuery..."):
        try:
            d = carregar_dados()
        except Exception as e:
            st.error(f"Erro ao carregar dados do BigQuery: {e}")
            return
    hoje = _hoje_brt()
    try:
        fr = carregar_frescor(TABELAS, EXTRATORES)
    except Exception:
        fr = None
    st.html(f"""
    <div class="report-header">
      <div>
        <div class="report-brand">shibari brasil · camada semanal</div>
        <div class="report-title">Estoque <span>&amp;</span> Reposição</div>
        <div class="report-meta">Quatro áreas na ordem da decisão: compras → cobertura e rupturas → volume e giro → cadastro · produtos físicos comerciais (insumos e matéria-prima ficam para a próxima leva)</div>
      </div>
      {badge_atualizacao(fr) if fr else f'<div class="report-badge">Hoje: <strong>{hoje.strftime("%d/%m/%Y")}</strong></div>'}
    </div>
    """)
    if fr:
        alerta_atraso(fr)
    est = d["est"]
    if est.empty:
        st.info("Sem produtos de estoque para mostrar.")
        return
    rompidos, urgentes = int((est["cod_risco"] == "a").sum()), int((est["cod_risco"] == "b").sum())
    render_cards([
        card("Estoque rompido", f"{rompidos}", "produtos sem estoque, com venda nos últimos 60 dias", variant="bad" if rompidos else "ok"),
        card("Urgente", f"{urgentes}", "cobertura total abaixo de 30 dias", variant="warn" if urgentes else "ok"),
        card("Estoque a custo", brl(est["vl_estoque_custo"].sum()), "todos os produtos físicos comerciais"),
    ])

    t_compras, t_cobertura, t_volume, t_cadastro = st.tabs(["Compras", "Cobertura e rupturas", "Volume e giro", "Cadastro"])
    with t_compras:
        note("<strong>O que comprar ou produzir agora, e quanto do orçamento de mercadoria isso consome.</strong> Comece pela lista de compra; "
             "depois veja o que está em risco mas ficou fora dela, a produção própria e o que já foi comprado e ainda não chegou.")
        _bloco_comprar(d)
        _bloco_producao(est)
        _bloco_compras_abertas(d["comp"])
    with t_cobertura:
        note("<strong>Vou ficar sem produto antes de a reposição chegar? Onde já faltou?</strong> Primeiro o que ainda vai acontecer (cobertura contra o lead time), "
             "depois o que já aconteceu (rupturas).")
        _bloco_cobertura(est)
        _bloco_rupturas(d)
    with t_volume:
        note("<strong>Quanto dinheiro está parado em estoque e com que velocidade ele vira?</strong> Do total, para o giro e o retorno, até a lista do que gira devagar.")
        _bloco_capital(d)
        _bloco_giro_lento(est)
    with t_cadastro:
        note("<strong>Os dados que alimentam as outras áreas estão completos?</strong> Estoque mínimo, lead time, origem, papel e custo definem o risco e a "
             "sugestão de compra: campo faltando aqui distorce as áreas anteriores. A correção é no Bling.")
        _bloco_cadastro(est)
    if fr:
        detalhe_atualizacao(fr)
