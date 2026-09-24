"""Perfil dos pedidos — bloco reutilizado nas páginas Diária, Semanal e Mensal (ver specs/perfil-pedidos.md).

Trabalha sobre 1 linha por pedido (`pedidos_de_linhas` converte a tabela por linha da tb_pedido).
Sem regra de negócio nova: só agrega o que a az já entrega (receita líquida, itens, frete, frente).
"""
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from common.design import COLORS, METRIC_COLORS, card, render_cards, section_title, note, plotly_layout, brl, pct

TIPOS = ["Só Shibari", "Só Curadoria", "Misto"]
FAIXAS_ITENS = [(1, 1, "1 item"), (2, 2, "2 itens"), (3, 3, "3 itens"), (4, 10**6, "4+ itens")]
FAIXAS_TICKET = [(0, 100, "até R$ 100"), (100, 150, "R$ 100–150"), (150, 200, "R$ 150–200"), (200, 300, "R$ 200–300"), (300, 10**9, "R$ 300+")]


def pedidos_de_linhas(df):
    """1 linha por pedido a partir das linhas da tb_pedido (brinde não conta como item nem como frente)."""
    cols = ["vl_liquido", "vl_produtos", "vl_bruto", "vl_desconto", "vl_frete", "fg_recorrente", "qt_item", "qt_skus", "fg_shibari", "fg_curadoria"]
    if df is None or df.empty:
        return pd.DataFrame(columns=cols)
    g = df.groupby("cd_codigo_interno").agg(
        vl_liquido=("vl_liquido_item", "sum"), vl_produtos=("vl_receita_liquida_produto", "sum"),
        vl_bruto=("vl_receita_bruta_produto", "sum"), vl_desconto=("vl_desconto_venda_rateio", "sum"),
        vl_frete=("vl_frete_pago_rateio", "sum"), fg_recorrente=("fg_cliente_recorrente", "first"))
    v = df[~df["fg_brinde"]]
    h = v.groupby("cd_codigo_interno").agg(
        qt_item=("qt_item", "sum"), qt_skus=("nm_produto", "nunique"),
        fg_shibari=("ds_frente", lambda s: bool((s == "Shibari").any())),
        fg_curadoria=("ds_frente", lambda s: bool((s != "Shibari").any())))
    out = g.join(h)
    out[["qt_item", "qt_skus"]] = out[["qt_item", "qt_skus"]].fillna(0)
    out[["fg_shibari", "fg_curadoria"]] = out[["fg_shibari", "fg_curadoria"]].fillna(False).astype(bool)
    out["fg_recorrente"] = out["fg_recorrente"].fillna(False).astype(bool)
    return out


def _tipo(p):
    tipo = pd.Series(pd.NA, index=p.index, dtype="object")
    tipo[p["fg_shibari"] & ~p["fg_curadoria"]] = "Só Shibari"
    tipo[~p["fg_shibari"] & p["fg_curadoria"]] = "Só Curadoria"
    tipo[p["fg_shibari"] & p["fg_curadoria"]] = "Misto"
    return tipo


def resumo(p):
    n = len(p)
    if not n:
        return None
    com_prod = p[p["qt_item"] > 0]
    itens = float(com_prod["qt_item"].sum())
    tipo = _tipo(p)
    mix = {}
    for t in TIPOS:
        x = p[tipo == t]
        mix[t] = {"n": len(x), "share": len(x) / len(com_prod) if len(com_prod) else 0,
                  "ticket": float(x["vl_liquido"].mean()) if len(x) else None,
                  "itens": float(x["qt_item"].mean()) if len(x) else None,
                  "receita": float(x["vl_produtos"].sum())}
    rec_total = sum(m["receita"] for m in mix.values())
    for m in mix.values():
        m["pct_receita"] = m["receita"] / rec_total if rec_total else 0
    bruto = float(p["vl_bruto"].sum())
    return {
        "n": n,
        "ticket": float(p["vl_liquido"].sum()) / n,
        "ticket_prod": float(p["vl_produtos"].sum()) / n,
        "preco_item": float(com_prod["vl_produtos"].sum()) / itens if itens else None,
        "itens_pedido": itens / len(com_prod) if len(com_prod) else None,
        "mediana_itens": float(com_prod["qt_item"].median()) if len(com_prod) else None,
        "pct_1_item": float((com_prod["qt_item"] == 1).mean()) if len(com_prod) else None,
        "pct_desc": float((p["vl_desconto"].abs() > 0.005).mean()),
        "desc_pct_bruto": abs(float(p["vl_desconto"].sum())) / bruto if bruto else None,
        "frete_medio": float(p["vl_frete"].mean()),
        "pct_frete_gratis": float((p["vl_frete"] <= 0.005).mean()),
        "pct_recorrente": float(p["fg_recorrente"].mean()),
        "mix": mix,
        "tipo": tipo,
    }


def _faixas(serie, faixas):
    return [int(((serie >= lo) & (serie <= hi if lo == hi else serie < hi)).sum()) for lo, hi, _ in faixas]


def _grafico_faixas(serie, faixas, titulo, cor):
    n = int(serie.notna().sum())
    contagens = _faixas(serie, faixas)
    pcts = [c / n if n else 0 for c in contagens]
    fig = go.Figure(go.Bar(
        x=[r for _, _, r in faixas], y=pcts, marker_color=cor,
        text=[f"{pct(v, 0)}<br>({c})" for v, c in zip(pcts, contagens)], textposition="outside",
        hovertemplate="%{x}: %{y:.0%}<extra></extra>"))
    plotly_layout(fig, height=280, showlegend=False, yaxis=dict(tickformat=".0%", gridcolor=COLORS["grid"], range=[0, max(pcts + [0.1]) * 1.25]))
    return fig


def secao_perfil(p, ant=None, rot_ant="", titulo="Perfil dos pedidos", contexto=""):
    """p e ant: DataFrames de pedidos (1 linha por pedido; ver pedidos_de_linhas). ant é opcional (comparação)."""
    from reports.vendas_margem import _delta  # import tardio: vendas_margem importa este módulo

    section_title(titulo)
    s = resumo(p)
    if s is None:
        st.info("Sem pedidos no período para traçar o perfil.")
        return
    a = resumo(ant) if ant is not None and len(ant) else None

    def dl(chave, tipo="rel", fmt=brl):
        if a is None or s[chave] is None or a[chave] is None:
            return {}
        t, c = _delta(s[chave], a[chave], rot_ant, tipo, fmt if tipo == "rel" else None)
        return {"delta": t, "delta_color": c}

    f1 = lambda v: f"{v:.1f}".replace(".", ",")
    render_cards([
        card("Ticket médio", brl(s["ticket"]), "faturamento (produtos + frete pago) ÷ pedidos", **dl("ticket")),
        card("Ticket de produtos", brl(s["ticket_prod"]), "receita líq. de produtos ÷ pedidos · sem frete", **dl("ticket_prod")),
        card("Valor médio por item", brl(s["preco_item"]) if s["preco_item"] else "—", "receita líq. de produtos ÷ itens vendidos", **dl("preco_item")),
        card("Itens por pedido", f1(s["itens_pedido"]) if s["itens_pedido"] else "—",
             f"mediana {f1(s['mediana_itens'])} · {pct(s['pct_1_item'], 0)} dos pedidos têm só 1 item" if s["itens_pedido"] else "",
             **dl("itens_pedido", fmt=f1)),
    ])
    mix = s["mix"]
    render_cards([
        card(t, pct(mix[t]["share"], 0),
             f"{mix[t]['n']} pedidos · ticket {brl(mix[t]['ticket'])}" if mix[t]["n"] else "nenhum pedido")
        for t in TIPOS
    ] + [
        card("Clientes recorrentes", pct(s["pct_recorrente"], 0), "% dos pedidos de quem já tinha comprado antes", **dl("pct_recorrente", "pp")),
    ])
    render_cards([
        card("Pedidos com frete grátis", pct(s["pct_frete_gratis"], 0), "cliente não pagou frete", **dl("pct_frete_gratis", "pp")),
        card("Frete médio pago", brl(s["frete_medio"]), "por pedido (inclui os grátis)", **dl("frete_medio")),
        card("Pedidos com desconto", pct(s["pct_desc"], 0), "cupom, PIX ou promoção", **dl("pct_desc", "pp")),
        card("Desconto médio", pct(s["desc_pct_bruto"]) if s["desc_pct_bruto"] is not None else "—", "descontos ÷ receita bruta de produtos", **dl("desc_pct_bruto", "pp")),
    ])

    col1, col2 = st.columns(2)
    with col1:
        st.html('<div class="c-label" style="margin:0 0 10px">Pedidos por quantidade de itens</div>')
        with st.container(border=True):
            st.plotly_chart(_grafico_faixas(p.loc[p["qt_item"] > 0, "qt_item"], FAIXAS_ITENS, "itens", METRIC_COLORS["receita"]), use_container_width=True)
    with col2:
        st.html('<div class="c-label" style="margin:0 0 10px">Pedidos por faixa de ticket (faturamento do pedido)</div>')
        with st.container(border=True):
            st.plotly_chart(_grafico_faixas(p["vl_liquido"], FAIXAS_TICKET, "ticket", METRIC_COLORS["margem_contribuicao"]), use_container_width=True)

    tab = pd.DataFrame([{
        "Tipo de pedido": t, "Pedidos": mix[t]["n"], "% dos pedidos": mix[t]["share"],
        "Ticket médio": mix[t]["ticket"], "Itens por pedido": mix[t]["itens"], "% da receita líq.": mix[t]["pct_receita"],
    } for t in TIPOS])
    st.dataframe(tab, hide_index=True, use_container_width=True, column_config={
        "Pedidos": st.column_config.NumberColumn(width=80),
        "% dos pedidos": st.column_config.NumberColumn(format="percent", width=110),
        "Ticket médio": st.column_config.NumberColumn(format="R$ %.2f", width=110),
        "Itens por pedido": st.column_config.NumberColumn(format="%.1f", width=110),
        "% da receita líq.": st.column_config.NumberColumn(format="percent", width=120)})
    note((contexto + " " if contexto else "") +
         "<strong>Só Shibari / Só Curadoria / Misto</strong> classificam o pedido pelas frentes dos itens comprados (brindes ficam de fora; frente definida pela categoria no dbt, "
         "<code>stg_frente_categoria</code>) — aqui cada pedido cai em um único grupo, ao contrário da seção Shibari × Curadoria, que divide a receita por linha. "
         "Ticket médio inclui o frete pago; ticket de produtos e valor por item, não. Com poucos pedidos, os percentuais oscilam bastante — use como tendência.")
