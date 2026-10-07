"""Vendas com ação ou promoção — seção de Vendas & Margem (camada mensal).

Pedido do Hugo (07/10/2026): abrir as vendas que usaram alguma ação ou promoção, EXCETO o brinde (sticker) e o desconto de meio de
pagamento (Pix), com destaque para as ações mapeadas do SAC e do pós-venda (prefixo do cupom).

Toda regra vive no dbt: `tb_pedido_acao` (1 linha por pedido válido, com a ação, o grupo e os descontos) e `stg_acao_cupom` (prefixo -> ação).
Aqui só se filtra, soma e apresenta. Os cupons gerados pelo SAC (conversão) vêm da view `raw_control.vw_vendas_cupom_acao`.
Spec: specs/vendas-acao-promocao.md.
"""
import pandas as pd
import streamlit as st

from common import bigquery as bq
from common.design import brl, card, note, pct, render_cards, section_title

INICIO_HISTORICO = "2025-08-01"  # mesmo recorte de Vendas & Margem (antes disso o custo não é confiável)
GRUPOS_REFERENCIA = ["SAC e pós-venda", "Cupom permanente", "Recuperação automática", "Cupom sem ação mapeada", "Promoção automática",
                     "Pedido manual", "Desconto manual"]
_DESTAQUE = "background-color: #FFF4D6"  # linha de ação mapeada do SAC/pós-venda (âmbar suave, igual ao destaque da home em Gestão de Produtos)


@st.cache_data(ttl=900)
def carregar():
    client = bq.get_client()
    df = bq.query_df(client, f"""
        SELECT cd_pedido_loja, nm_contato, dt_pedido, ds_codigo_cupom, ds_acao, ds_grupo_acao, fg_com_acao, fg_cupom, fg_promocao,
               fg_sac_marketing, ds_meio_pagamento, nr_pedido_cliente, vl_desconto_cupom, vl_desconto_promocional, vl_desconto_acao,
               vl_receita_bruta_produto, vl_receita_liquida_produto, vl_faturamento, vl_margem_contribuicao
          FROM `{bq.PROJECT}.dbt_dw_az.tb_pedido_acao`
         WHERE dt_pedido >= DATE '{INICIO_HISTORICO}'
    """)
    df["dt_pedido"] = pd.to_datetime(df["dt_pedido"])
    df["mes"] = df["dt_pedido"].dt.to_period("M").dt.to_timestamp()
    for c in ("fg_com_acao", "fg_cupom", "fg_promocao", "fg_sac_marketing"):
        df[c] = df[c].fillna(False).astype(bool)
    for c in ("vl_desconto_cupom", "vl_desconto_promocional", "vl_desconto_acao", "vl_receita_bruta_produto", "vl_receita_liquida_produto",
              "vl_faturamento", "vl_margem_contribuicao"):
        df[c] = pd.to_numeric(df[c]).fillna(0.0)
    try:  # a view só existe depois do 1º cupom gerado pela function
        gerados = bq.query_df(client, f"""
            SELECT campanha, prefixo, referencia, codigo, criado_em, fg_comprou, vl_receita_liquida_produto, vl_margem_contribuicao, vl_desconto_cupom
              FROM `{bq.PROJECT}.raw_control.vw_vendas_cupom_acao`
             WHERE NOT STARTS_WITH(referencia, 'teste')  -- cupons de teste (teste_dev, teste_hugo) não contam
        """)
    except Exception as e:
        if "Not found" not in str(e):
            raise
        gerados = pd.DataFrame(columns=["campanha", "prefixo", "codigo", "criado_em", "fg_comprou", "vl_receita_liquida_produto",
                                        "vl_margem_contribuicao", "vl_desconto_cupom"])
    gerados["criado_em"] = pd.to_datetime(gerados["criado_em"], utc=True).dt.tz_convert("America/Sao_Paulo").dt.tz_localize(None)
    gerados["mes"] = gerados["criado_em"].dt.to_period("M").dt.to_timestamp()
    gerados["fg_comprou"] = gerados["fg_comprou"].fillna(False).astype(bool)
    for c in ("vl_receita_liquida_produto", "vl_margem_contribuicao", "vl_desconto_cupom"):
        gerados[c] = pd.to_numeric(gerados[c]).fillna(0.0)
    return {"pedidos": df, "gerados": gerados}


def _agrega(d: pd.DataFrame) -> dict:
    """Somas e razões (soma ÷ soma) de um conjunto de pedidos."""
    n = len(d)
    rec = float(d["vl_receita_liquida_produto"].sum())
    fat = float(d["vl_faturamento"].sum())
    bruta = float(d["vl_receita_bruta_produto"].sum())
    return {
        "n": n, "fat": fat, "rec": rec, "mc": float(d["vl_margem_contribuicao"].sum()), "desc": float(d["vl_desconto_acao"].sum()),
        "ticket": (fat / n) if n else None,
        "margem_pct": (float(d["vl_margem_contribuicao"].sum()) / rec) if rec else None,
        "desc_pct": (float(d["vl_desconto_acao"].sum()) / bruta) if bruta else None,
    }


def _tabela_por_acao(d: pd.DataFrame, total_pedidos: int) -> pd.DataFrame:
    linhas = []
    for (grupo, acao), x in d.groupby(["ds_grupo_acao", "ds_acao"]):
        a = _agrega(x)
        linhas.append({"Grupo": grupo, "Ação": acao, "Mapeada pelo SAC / pós-venda": "Sim" if bool(x["fg_sac_marketing"].any()) else "",
                       "Pedidos": a["n"], "% dos pedidos": a["n"] / total_pedidos if total_pedidos else None, "Faturamento": a["fat"],
                       "Ticket médio": a["ticket"], "Desconto da ação": a["desc"], "Desconto ÷ receita bruta": a["desc_pct"],
                       "Margem de contribuição": a["mc"], "Margem %": a["margem_pct"]})
    t = pd.DataFrame(linhas)
    if t.empty:
        return t
    ordem = {g: i for i, g in enumerate(GRUPOS_REFERENCIA)}
    t["_o"] = t["Grupo"].map(ordem).fillna(99)
    return t.sort_values(["_o", "Pedidos"], ascending=[True, False]).drop(columns="_o").reset_index(drop=True)


def _estilo(t: pd.DataFrame, destaque_col: str):
    def linha(r):
        return [_DESTAQUE if r[destaque_col] == "Sim" else ""] * len(r)
    return (t.style.apply(linha, axis=1)
             .format({"Faturamento": brl, "Ticket médio": brl, "Desconto da ação": brl, "Margem de contribuição": brl,
                      "% dos pedidos": lambda v: pct(v, 0) if pd.notna(v) else "—",
                      "Desconto ÷ receita bruta": lambda v: pct(v) if pd.notna(v) else "—",
                      "Margem %": lambda v: pct(v) if pd.notna(v) else "—"}, na_rep="—"))


def secao_acao(meses_sel, hoje):
    section_title("Vendas com ação ou promoção")
    try:
        dados = carregar()
    except Exception as e:
        st.warning(f"Não foi possível carregar as vendas com ação: {e}")
        return
    df, gerados = dados["pedidos"], dados["gerados"]
    d = df[df["mes"].isin([pd.Timestamp(m) for m in meses_sel])]
    if d.empty:
        st.info("Sem pedidos válidos no período selecionado.")
        return
    com, sem = d[d["fg_com_acao"]], d[~d["fg_com_acao"]]
    a, s = _agrega(com), _agrega(sem)
    tot = _agrega(d)

    # ═══ visão geral ═══
    dif = (a["margem_pct"] - s["margem_pct"]) * 100 if a["margem_pct"] is not None and s["margem_pct"] is not None else None
    render_cards([
        card("Pedidos com ação", f"{a['n']}", f"{pct(a['n'] / tot['n'], 0)} dos {tot['n']} pedidos do período", ref="cupom, promoção da loja ou desconto manual"),
        card("Faturamento com ação", brl(a["fat"]), f"{pct(a['fat'] / tot['fat'], 0) if tot['fat'] else '—'} do faturamento do período"),
        card("Desconto concedido pelas ações", brl(a["desc"]),
             f"{pct(a['desc_pct']) if a['desc_pct'] is not None else '—'} da receita bruta desses pedidos", ref="cupom + promoção; Pix e sticker ficam de fora"),
        card("Margem de contribuição (com ação)", pct(a["margem_pct"]) if a["margem_pct"] is not None else "—",
             f"sem ação: {pct(s['margem_pct']) if s['margem_pct'] is not None else '—'}",
             variant=("neutral" if dif is None else "ok" if dif >= -1 else "warn" if dif >= -5 else "bad"),
             ref=(f"{'+' if dif >= 0 else '−'}{abs(dif):.1f} p.p. vs. sem ação".replace(".", ",") if dif is not None else ""),
             ),
        card("Ticket médio (com ação)", brl(a["ticket"]) if a["ticket"] is not None else "—",
             f"sem ação: {brl(s['ticket']) if s['ticket'] is not None else '—'}", ref="faturamento ÷ pedidos"),
    ])

    # ═══ destaque: ações do SAC e do pós-venda ═══
    st.html('<div class="c-label" style="margin:18px 0 8px">Ações mapeadas do SAC e do pós-venda</div>')
    sac = d[d["fg_sac_marketing"]]
    g = gerados[gerados["mes"].isin([pd.Timestamp(m) for m in meses_sel])]
    sa = _agrega(sac)
    render_cards([
        card("Pedidos com cupom de ação do SAC / pós-venda", f"{sa['n']}", f"{brl(sa['fat'])} faturados" if sa["n"] else "nenhum no período"),
        card("Cupons gerados no período", f"{len(g)}", f"{int(g['fg_comprou'].sum())} usados em compra" if len(g) else "nenhum cupom gerado",
             ref=(f"conversão {pct(g['fg_comprou'].mean(), 0)}" if len(g) else "")),
        card("Margem de contribuição dessas vendas", pct(sa["margem_pct"]) if sa["margem_pct"] is not None else "—",
             f"{brl(sa['mc'])} de margem · desconto {brl(sa['desc'])}" if sa["n"] else "sem vendas ainda"),
    ])
    # A tabela parte dos PEDIDOS (a mesma base do card) e cruza com os cupons gerados pela function: o prefixo de cada campanha liga um ao outro.
    # Cupom recriado à mão na Nuvemshop vende com o mesmo prefixo, mas não passou pela function: vira "usado fora da function".
    codigo = sac["ds_codigo_cupom"].fillna("").str.upper()
    cobertos = pd.Series(False, index=sac.index)
    linhas = []

    def _linha(acao, prefixo, gx, ped):
        x = _agrega(ped)
        usados = int(gx["fg_comprou"].sum()) if gx is not None else 0
        n_g = len(gx) if gx is not None else 0
        return {"Ação": acao, "Prefixo do cupom": prefixo, "Cupons gerados": n_g, "Usados (gerados)": usados,
                "Conversão": (usados / n_g) if n_g else None, "Pedidos com o cupom": x["n"],
                "Usados fora da function": max(x["n"] - usados, 0) if n_g else x["n"], "Faturamento": x["fat"],
                "Margem de contribuição": x["mc"], "Desconto": x["desc"]}

    for (camp, pref), _ in gerados.groupby(["campanha", "prefixo"]):
        gx = g[(g["campanha"] == camp) & (g["prefixo"] == pref)]
        ped = sac[codigo.str.startswith(pref.upper())]
        cobertos.loc[ped.index] = True
        if len(gx) or len(ped):
            linhas.append(_linha(camp, pref, gx, ped))
    for acao, ped in sac[~cobertos].groupby("ds_acao"):  # ação mapeada que não tem cupom gerado pela function
        linhas.append(_linha(acao, "—", None, ped))
    if linhas:
        st.dataframe(pd.DataFrame(linhas), hide_index=True, use_container_width=True,
                     column_config={"Faturamento": st.column_config.NumberColumn(format="R$ %.2f"),
                                    "Margem de contribuição": st.column_config.NumberColumn(format="R$ %.2f"),
                                    "Desconto": st.column_config.NumberColumn(format="R$ %.2f"),
                                    "Conversão": st.column_config.NumberColumn(format="percent")})
    note("Ações mapeadas = cupons cujo prefixo está em <code>stg_acao_cupom</code> como SAC e pós-venda: <code>SEGUNDACHANCE</code> (recontato com cupom), "
         "<code>RETORNO…</code> (crédito de retorno da recompra), <code>CASHBACKPOSCOMPRA</code> e <code>EXPLORAR20</code>. Cupons gerados vêm da function "
         "<code>nuvemshop-criar-cupom</code> (<code>raw_control.cupons_gerados</code>); a conversão conta o cupom gerado no mês que foi usado em compra, mesmo depois do mês. "
         "<strong>Pedidos com o cupom</strong> conta toda venda com o prefixo (mesma base do card de cima); <strong>Usados (gerados)</strong> conta só os cupons que a function criou; a diferença "
         "(<strong>usados fora da function</strong>) são cupons recriados à mão na Nuvemshop. Volume pequeno: leia como sinal, não como taxa estável.")

    # ═══ por ação ═══
    st.html('<div class="c-label" style="margin:18px 0 8px">Vendas por ação (linhas em destaque = ação mapeada do SAC / pós-venda)</div>')
    t = _tabela_por_acao(com, tot["n"])
    if t.empty:
        st.info("Nenhum pedido com ação no período.")
    else:
        ref = pd.DataFrame([{"Grupo": "Sem ação", "Ação": "Referência: pedidos sem ação nem promoção", "Mapeada pelo SAC / pós-venda": "",
                             "Pedidos": s["n"], "% dos pedidos": s["n"] / tot["n"], "Faturamento": s["fat"], "Ticket médio": s["ticket"],
                             "Desconto da ação": 0.0, "Desconto ÷ receita bruta": None, "Margem de contribuição": s["mc"], "Margem %": s["margem_pct"]}])
        st.dataframe(_estilo(pd.concat([t, ref], ignore_index=True), "Mapeada pelo SAC / pós-venda"), hide_index=True, use_container_width=True)
    note("Conta como ação: <strong>cupom de qualquer tipo</strong>, <strong>promoção automática da loja</strong> (desconto promocional real, já sem o sticker) e "
         "<strong>desconto manual</strong>. <strong>Não conta</strong> o brinde (sticker) nem o desconto de meio de pagamento (Pix): pedido só com isso é "
         "&quot;sem ação&quot;. Quando há cupom e promoção no mesmo pedido, ele entra no grupo do cupom. O <code>PRIMEIRODATE</code> (R$ 5, sem prazo) é um cupom "
         "<strong>permanente</strong>: boa parte dos clientes novos usa, então ele não mede eficiência de campanha (todo mundo que sabe do código usa). "
         "Faturamento = produtos líquidos + frete pago; margem de contribuição antes de mídia, razão soma ÷ soma. Pedido manual (rascunho do admin) costuma ter valor zero.")

    # ═══ pedidos ═══
    st.html('<div class="c-label" style="margin:18px 0 8px">Pedidos com ação no período</div>')
    grupos = ["Todas as ações", "Só SAC e pós-venda"] + [g_ for g_ in GRUPOS_REFERENCIA if g_ in set(com["ds_grupo_acao"])]
    filtro = st.selectbox("Mostrar", grupos, key="acao_filtro_pedidos")
    p = com if filtro == "Todas as ações" else sac if filtro == "Só SAC e pós-venda" else com[com["ds_grupo_acao"] == filtro]
    if p.empty:
        st.info("Nenhum pedido nesse filtro.")
    else:
        p = p.sort_values("dt_pedido", ascending=False)
        tab = pd.DataFrame({
            "Data": p["dt_pedido"].dt.date, "Pedido": p["cd_pedido_loja"].astype(str), "Cliente": p["nm_contato"].fillna("—"),
            "Ação": p["ds_acao"], "Cupom": p["ds_codigo_cupom"].fillna("—"), "Desconto do cupom": p["vl_desconto_cupom"],
            "Desconto de promoção": p["vl_desconto_promocional"], "Faturamento": p["vl_faturamento"],
            "Margem %": p["vl_margem_contribuicao"] / p["vl_receita_liquida_produto"].where(p["vl_receita_liquida_produto"] > 0),
            "Pagamento": p["ds_meio_pagamento"].fillna("—"), "Cliente é": p["nr_pedido_cliente"].apply(lambda n: "Novo" if n == 1 else "Recorrente" if pd.notna(n) else "—"),
            "SAC / pós-venda": p["fg_sac_marketing"].map({True: "Sim", False: ""}),
        })
        st.dataframe(tab.style.apply(lambda r: [_DESTAQUE if r["SAC / pós-venda"] == "Sim" else ""] * len(r), axis=1)
                        .format({"Desconto do cupom": brl, "Desconto de promoção": brl, "Faturamento": brl,
                                 "Margem %": lambda v: pct(v) if pd.notna(v) else "—"}),
                     hide_index=True, use_container_width=True, height=min(520, 38 + 35 * len(tab)))

    # ═══ cupons sem ação mapeada ═══
    sem_map = com[com["ds_grupo_acao"] == "Cupom sem ação mapeada"]
    if not sem_map.empty:
        with st.expander(f"Cupons sem ação mapeada ({sem_map['ds_codigo_cupom'].nunique()} códigos) — classifique para o relatório destacar"):
            x = (sem_map.groupby("ds_codigo_cupom").agg(Pedidos=("cd_pedido_loja", "count"), Faturamento=("vl_faturamento", "sum"),
                                                       Desconto=("vl_desconto_cupom", "sum"), Primeiro=("dt_pedido", "min"), Ultimo=("dt_pedido", "max"))
                          .reset_index().rename(columns={"ds_codigo_cupom": "Código"}).sort_values("Pedidos", ascending=False))
            x["Primeiro"], x["Ultimo"] = x["Primeiro"].dt.date, x["Ultimo"].dt.date
            st.dataframe(x, hide_index=True, use_container_width=True,
                         column_config={"Faturamento": st.column_config.NumberColumn(format="R$ %.2f"), "Desconto": st.column_config.NumberColumn(format="R$ %.2f")})
            note("Para o relatório destacar uma ação nova, o prefixo do cupom entra em <code>stg_acao_cupom</code> (sb_dw_dbt) com o nome da ação e o grupo.")
