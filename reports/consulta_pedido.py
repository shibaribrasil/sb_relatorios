"""Relatório Consulta de Pedidos — Shibari Brasil (camada diária).

Um pedido por vez, do carrinho à margem: itens (com foto) → descontos e frete → valor pago → taxa, envio e imposto →
o que entrou na conta → custo e margem de contribuição → origem, custo de aquisição e margem após aquisição.
Regras e limites: specs/consulta-pedido.md.

Regra de negócio vem pronta do dbt (`tb_pedido`, `tb_atribuicao_pedido`, `tb_produto_gestao`, `tb_gads_*`).
Aqui só se filtra, soma e apresenta; a única conta própria é o custo de aquisição estimado (ver `custo_aquisicao`).
"""
import html
import re

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from common import bigquery as bq
from common.design import (
    COLORS, METRIC_COLORS, CATEGORICAL, inject_css, card, render_cards, section_title, note, plotly_layout, brl, pct,
)
from reports.origem_campanha import GOOGLE_PAGO, carregar_campanhas
from reports.produto_desempenho import _chips, _foto, _in
from reports.vendas_margem import _hoje_brt, _variant_margem

AZ = f"{bq.PROJECT}.dbt_dw_az"
INICIO = "2025-08-01"                      # taxa e frete reais da Nuvemshop só são confiáveis desde ago/2025
INICIO_ADS = pd.Timestamp("2026-06-01")    # o histórico de custo por campanha do Google Ads começa aqui
META_PAGO = ("instagram", "paid_social")   # mídia paga sem custo na base (não há dados do Meta Ads)
MEIOS = {"credit_card": "Cartão de crédito", "pix": "Pix", "boleto": "Boleto", "wallet": "Carteira digital", "other": "Outro meio"}


# ── carga ────────────────────────────────────────────────────────────────────

def _digitos(v):
    """Id numérico vindo do próprio BigQuery; valida antes de entrar em SQL."""
    v = str(v)
    if not re.fullmatch(r"\d+", v):
        raise ValueError(f"id inválido: {v!r}")
    return v


@st.cache_data(ttl=300)
def carregar_lista():
    """1 linha por pedido válido, do mais recente para o mais antigo (seletor)."""
    client = bq.get_client()
    df = bq.query_df(client, f"""
        SELECT CAST(cd_codigo_interno AS STRING) AS cd, ANY_VALUE(CAST(cd_pedido AS STRING)) AS cd_pedido,
               ANY_VALUE(CAST(cd_pedido_nuvemshop AS STRING)) AS cd_nuvemshop, ANY_VALUE(dt_pedido) AS dt_pedido,
               ANY_VALUE(nm_contato) AS nm_contato, SUM(vl_liquido_item) AS vl_liquido
          FROM `{AZ}.tb_pedido` WHERE fg_pedido_valido AND dt_pedido >= DATE '{INICIO}'
      GROUP BY cd_codigo_interno
    """)
    df["dt_pedido"] = pd.to_datetime(df["dt_pedido"])
    df["vl_liquido"] = pd.to_numeric(df["vl_liquido"]).fillna(0.0)
    df["codigo"] = df["cd_nuvemshop"].where(df["cd_nuvemshop"].notna(), "Bling " + df["cd_pedido"].astype(str))
    # no mesmo dia, o código maior da Nuvemshop é o mais recente
    df["_n"] = pd.to_numeric(df["cd_nuvemshop"], errors="coerce").fillna(0)
    return df.sort_values(["dt_pedido", "_n"], ascending=False).drop(columns="_n").reset_index(drop=True)


@st.cache_data(ttl=300)
def carregar_itens(cd: str):
    """Linhas do pedido (uma por produto) com todas as parcelas da margem de contribuição."""
    client = bq.get_client()
    df = bq.query_df(client, f"""
        SELECT CAST(cd_produto_bling AS STRING) AS sku, nm_produto, ds_variacao, qt_item, fg_brinde,
               vl_receita_bruta_produto, vl_desconto_cupom_rateio, vl_desconto_pagamento_rateio, vl_desconto_promocional_rateio,
               vl_receita_liquida_produto, vl_frete_pago_rateio, vl_frete_real_rateio, vl_taxa_pedido_rateio,
               vl_embalagem_rateio, vl_imposto_rateio, vl_reembolso_rateio, vl_custo_linha, vl_margem_contribuicao,
               ds_incompletude, CAST(cd_pedido AS STRING) AS cd_pedido, CAST(cd_pedido_nuvemshop AS STRING) AS cd_nuvemshop,
               dt_pedido, nm_contato, nr_pedido_cliente, fg_cliente_recorrente, ds_tipo_loja, ds_status_pedido,
               ds_meio_pagamento_nuvemshop, qt_parcelas_nuvemshop, ds_codigo_cupom_nuvemshop, dt_recebimento_pedido
          FROM `{AZ}.tb_pedido` WHERE fg_pedido_valido AND CAST(cd_codigo_interno AS STRING) = '{_digitos(cd)}'
    """)
    for c in ["qt_item", "vl_receita_bruta_produto", "vl_desconto_cupom_rateio", "vl_desconto_pagamento_rateio",
              "vl_desconto_promocional_rateio", "vl_receita_liquida_produto", "vl_frete_pago_rateio", "vl_frete_real_rateio",
              "vl_taxa_pedido_rateio", "vl_embalagem_rateio", "vl_imposto_rateio", "vl_reembolso_rateio", "vl_custo_linha",
              "vl_margem_contribuicao"]:
        df[c] = pd.to_numeric(df[c]).fillna(0.0)
    df["fg_brinde"] = df["fg_brinde"].fillna(False).astype(bool)
    df["dt_pedido"] = pd.to_datetime(df["dt_pedido"])
    df["dt_recebimento_pedido"] = pd.to_datetime(df["dt_recebimento_pedido"])
    return df


@st.cache_data(ttl=900)
def carregar_fotos(skus: tuple):
    """Foto por SKU (a da própria variação; se faltar, a de outra variação da família). Link do Bling é assinado e vence."""
    client = bq.get_client()
    g = bq.query_df(client, f"""
        SELECT CAST(cd_produto_bling AS STRING) AS sku, CAST(cd_produto_bling_familia AS STRING) AS familia, lk_imagem_produto AS foto
          FROM `{AZ}.tb_produto_gestao`
         WHERE CAST(cd_produto_bling_familia AS STRING) IN (
               SELECT CAST(cd_produto_bling_familia AS STRING) FROM `{AZ}.tb_produto_gestao` WHERE CAST(cd_produto_bling AS STRING) IN ({_in(skus)}))
    """)
    por_familia = g.groupby("familia")["foto"].apply(lambda s: _foto(list(s))).to_dict()
    proprio = dict(zip(g["sku"], g["foto"]))
    fam = dict(zip(g["sku"], g["familia"]))
    return {s: (proprio.get(s) if isinstance(proprio.get(s), str) and proprio.get(s) else por_familia.get(fam.get(s))) for s in skus}


@st.cache_data(ttl=900)
def carregar_origem(cd_pedido: str):
    """Origem e mídia do pedido (tb_atribuicao_pedido): URL de entrada, com o clique do Google Ads recuperado pelo GA4."""
    client = bq.get_client()
    df = bq.query_df(client, f"""
        SELECT ds_origem_venda AS origem, ds_midia_venda AS midia, ds_gclid, ds_utm_campaign
          FROM `{AZ}.tb_atribuicao_pedido` WHERE CAST(cd_pedido AS STRING) = '{_digitos(cd_pedido)}' LIMIT 1
    """)
    return df.iloc[0].to_dict() if not df.empty else {"origem": "(sem parametro)", "midia": "(sem parametro)", "ds_gclid": None, "ds_utm_campaign": None}


@st.cache_data(ttl=900)
def carregar_pedidos_google_mes(mes: str):
    """Pedidos válidos do mês com origem Google pago, para dividir o custo do Ads (mes = 'AAAA-MM-01')."""
    if not re.fullmatch(r"\d{4}-\d{2}-01", mes):
        raise ValueError(f"mês inválido: {mes!r}")
    client = bq.get_client()
    return bq.query_df(client, f"""
        SELECT CAST(a.cd_pedido AS STRING) AS cd_pedido, a.ds_gclid
          FROM `{AZ}.tb_atribuicao_pedido` AS a
         WHERE a.ds_origem_venda = '{GOOGLE_PAGO[0]}' AND a.ds_midia_venda = '{GOOGLE_PAGO[1]}'
           AND CAST(a.cd_pedido AS STRING) IN (SELECT CAST(cd_pedido AS STRING) FROM `{AZ}.tb_pedido`
                                                WHERE fg_pedido_valido AND dt_prim_dia_mes = DATE '{mes}')
    """)


# ── contas ───────────────────────────────────────────────────────────────────

def resumo_pedido(it):
    """Parcelas do pedido, da cascata. Fecha com a margem de contribuição do dbt (campo `folga` = diferença, deve ser ~0)."""
    bruto = float(it.loc[~it["fg_brinde"], "vl_receita_bruta_produto"].sum())
    rec = float(it["vl_receita_liquida_produto"].sum())
    s = {c: float(it[c].sum()) for c in ["vl_frete_pago_rateio", "vl_frete_real_rateio", "vl_taxa_pedido_rateio", "vl_embalagem_rateio",
                                         "vl_imposto_rateio", "vl_reembolso_rateio", "vl_custo_linha", "vl_margem_contribuicao",
                                         "vl_desconto_cupom_rateio", "vl_desconto_pagamento_rateio", "vl_desconto_promocional_rateio"]}
    nb = ~it["fg_brinde"]  # o desconto promocional do brinde (100% do valor dele) não é desconto que o cliente recebeu
    pago = rec + s["vl_frete_pago_rateio"]
    r = {
        "bruto": bruto, "rec": rec, "desconto": bruto - rec, "frete": s["vl_frete_pago_rateio"], "pago": pago,
        "taxa": s["vl_taxa_pedido_rateio"], "frete_real": s["vl_frete_real_rateio"], "embalagem": s["vl_embalagem_rateio"],
        "imposto": s["vl_imposto_rateio"], "reembolso": s["vl_reembolso_rateio"], "custo": s["vl_custo_linha"],
        "custo_brinde": float(it.loc[it["fg_brinde"], "vl_custo_linha"].sum()), "margem": s["vl_margem_contribuicao"],
        "cupom": float(it.loc[nb, "vl_desconto_cupom_rateio"].sum()), "pgto": float(it.loc[nb, "vl_desconto_pagamento_rateio"].sum()),
        "promo": float(it.loc[nb, "vl_desconto_promocional_rateio"].sum()),
    }
    r["entrou"] = pago - r["taxa"]
    r["margem_pct"] = (r["margem"] / rec) if rec else None
    r["folga"] = pago - r["taxa"] - r["frete_real"] - r["embalagem"] - r["imposto"] - r["reembolso"] - r["custo"] - r["margem"]
    return r


def custo_aquisicao(origem, midia, gclid, mes, pedidos_google, ponte, perf):
    """Custo de mídia paga atribuído ao pedido: (valor | None, explicação curta). None = não dá para saber.

    Só o Google Ads tem custo na base. Pedido Google pago: custo da campanha do clique (gclid → campanha) no mês do pedido
    ÷ pedidos do mês atribuídos a ela; sem campanha identificada, custo de todo o Ads no mês ÷ pedidos Google pago do mês.
    shortcut: o divisor conta só pedidos com clique identificável (piso), então o custo por pedido é um teto; se o Ads
    passar a mandar conversão por pedido, trocar por ela.
    """
    if (origem, midia) == GOOGLE_PAGO:
        if mes < INICIO_ADS:
            return None, "sem histórico de custo do Google Ads antes de jun/2026"
        no_mes = perf[perf["dt_data"].dt.to_period("M").dt.to_timestamp() == mes]
        if no_mes.empty:
            return None, "sem custo do Google Ads lançado neste mês"
        ponte = ponte.drop_duplicates("cd_gclid").set_index("cd_gclid")
        camp = ponte["cd_campanha"].get(gclid) if gclid else None
        ped = pedidos_google.assign(cd_campanha=pedidos_google["ds_gclid"].map(ponte["cd_campanha"]))
        if camp is not None and pd.notna(camp) and (ped["cd_campanha"] == camp).any():
            n = int((ped["cd_campanha"] == camp).sum())
            custo = float(no_mes.loc[no_mes["cd_campanha"] == camp, "vl_custo"].sum())
            return custo / n, f"campanha {ponte['nm_campanha'].get(gclid)}: {brl(custo)} no mês ÷ {n} {'pedido' if n == 1 else 'pedidos'}"
        n = len(ped)
        if not n:
            return None, "sem pedidos Google pago no mês"
        custo = float(no_mes["vl_custo"].sum())
        return custo / n, f"campanha não identificada: todo o Google Ads do mês ({brl(custo)}) ÷ {n} pedidos Google pago"
    if (origem, midia) == META_PAGO:
        return None, "mídia paga do Instagram: o custo do Meta Ads não está na base"
    return 0.0, "sem mídia paga rastreada"


# ── apresentação ─────────────────────────────────────────────────────────────

def _itens_html(it, fotos):
    cards = []
    for _, r in it.sort_values("fg_brinde", kind="stable").iterrows():
        foto = fotos.get(r["sku"])
        img = (f'<img src="{html.escape(foto)}" style="width:84px;height:84px;object-fit:cover;border-radius:8px;flex:none" onerror="this.style.display=\'none\'">'
               if foto else f'<div style="width:84px;height:84px;border-radius:8px;background:{COLORS["bg_secondary"]};flex:none"></div>')
        var = "" if str(r["ds_variacao"]).lower().startswith("sem var") or pd.isna(r["ds_variacao"]) else f' · {html.escape(str(r["ds_variacao"]))}'
        qt = int(r["qt_item"])
        if r["fg_brinde"]:
            linha, valor = f"{qt} × brinde", "R$ 0,00"
        else:
            unit = r["vl_receita_bruta_produto"] / qt if qt else 0.0
            linha = f"{qt} × {brl(unit)}"
            valor = brl(r["vl_receita_bruta_produto"])
            if r["vl_receita_liquida_produto"] < r["vl_receita_bruta_produto"] - 0.005:
                linha += f" · paga {brl(r['vl_receita_liquida_produto'])}"
        cards.append(f"""<div style="display:flex;gap:12px;align-items:center;border:1px solid {COLORS['border']};border-radius:10px;padding:10px;background:{COLORS['bg']}">
          {img}
          <div style="min-width:0;flex:1">
            <div style="font-size:14px;font-weight:700;color:{COLORS['primary_dark']};line-height:1.25">{html.escape(str(r['nm_produto']))}</div>
            <div style="font-size:12px;color:{COLORS['text_secondary']};margin:2px 0 6px">{var.lstrip(' ·') or '&nbsp;'}</div>
            <div style="font-size:12px;color:{COLORS['text_muted']}">{linha}</div>
          </div>
          <div style="font-size:16px;font-weight:800;color:{COLORS['text']};white-space:nowrap">{valor}</div>
        </div>""")
    st.html(f'<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:12px;margin:6px 0 4px">{"".join(cards)}</div>')


def _cascata(r, aq):
    passos = [
        ("Itens", r["bruto"], "absolute"), ("− Descontos", -r["desconto"], "relative"), ("+ Frete cobrado", r["frete"], "relative"),
        ("= Valor pago", None, "total"), ("− Taxa de pagamento", -r["taxa"], "relative"), ("− Frete (etiqueta)", -r["frete_real"], "relative"),
        ("− Embalagem", -r["embalagem"], "relative"), ("− Imposto", -r["imposto"], "relative"), ("− Reembolso", -r["reembolso"], "relative"),
        ("− Custo dos produtos", -r["custo"], "relative"), ("= Margem de contribuição", None, "total"),
        ("− Custo de aquisição", -(aq or 0.0), "relative"), ("= Margem após aquisição", None, "total"),
    ]
    passos = [p for p in passos if p[2] == "total" or abs(p[1]) >= 0.005]   # passo zerado só polui
    if aq is None:  # custo de aquisição desconhecido: a cascata termina na margem de contribuição
        passos = [p for p in passos if p[0] not in ("− Custo de aquisição", "= Margem após aquisição")]
    sub = {"= Valor pago": r["pago"], "= Margem de contribuição": r["margem"], "= Margem após aquisição": r["margem"] - (aq or 0.0)}
    valores = [sub[p[0]] if p[1] is None else p[1] for p in passos]
    fig = go.Figure(go.Waterfall(
        x=[p[0] for p in passos], y=valores, measure=[p[2] for p in passos],
        text=[brl(v) for v in valores], textposition="outside", cliponaxis=False,
        connector=dict(line=dict(color=COLORS["border"], width=1)),
        increasing=dict(marker=dict(color=CATEGORICAL[7])), decreasing=dict(marker=dict(color=COLORS["text_muted"])),
        totals=dict(marker=dict(color=METRIC_COLORS["receita"])), hovertemplate="%{x}<br>%{text}<extra></extra>"))
    plotly_layout(fig, height=420, showlegend=False, yaxis=dict(tickprefix="R$ ", gridcolor=COLORS["grid"]),
                  xaxis=dict(tickangle=-35, automargin=True, dtick=1, tickfont=dict(size=11)))
    return fig


def render():
    inject_css()
    st.html("""
    <div class="report-header"><div>
      <div class="report-brand">shibari brasil · camada diária</div>
      <div class="report-title">Consulta de <span>Pedidos</span></div>
      <div class="report-meta">Um pedido por vez, do carrinho à margem · pedidos válidos (pagos, não cancelados)</div>
    </div></div>""")
    try:
        lista = carregar_lista()
    except Exception as e:  # noqa: BLE001
        st.error(f"Erro ao carregar os pedidos do BigQuery: {e}")
        return
    if lista.empty:
        st.info("Sem pedidos válidos no período.")
        return

    rot = {r.cd: f"#{r.codigo} · {r.dt_pedido:%d/%m/%Y} · {r.nm_contato or '—'} · {brl(r.vl_liquido)}" for r in lista.itertuples()}
    cd = st.selectbox("Pedido", list(rot), format_func=rot.get, help="O mais recente vem selecionado. Digite o código ou o nome do cliente para buscar.")
    try:
        it = carregar_itens(cd)
        fotos = carregar_fotos(tuple(sorted(it["sku"].dropna().unique())))
        org = carregar_origem(it["cd_pedido"].iloc[0])
    except Exception as e:  # noqa: BLE001
        st.error(f"Erro ao carregar o pedido: {e}")
        return
    r = resumo_pedido(it)
    h = it.iloc[0]
    hoje = pd.Timestamp(_hoje_brt())

    azul, cinza = (COLORS["primary_light"], COLORS["primary_dark"]), (COLORS["bg_secondary"], COLORS["text_secondary"])
    meio = MEIOS.get(h["ds_meio_pagamento_nuvemshop"], str(h["ds_meio_pagamento_nuvemshop"]) if pd.notna(h["ds_meio_pagamento_nuvemshop"]) else "Meio não informado")
    parc = int(h["qt_parcelas_nuvemshop"]) if pd.notna(h["qt_parcelas_nuvemshop"]) else 0
    recorrente = bool(h["fg_cliente_recorrente"]) if pd.notna(h["fg_cliente_recorrente"]) else False
    n_ped = f" · {int(h['nr_pedido_cliente'])}º pedido" if pd.notna(h["nr_pedido_cliente"]) else ""
    st.html(f"""<div style="display:flex;gap:18px;align-items:baseline;flex-wrap:wrap;margin:4px 0 6px">
      <div style="font-size:24px;font-weight:800;color:{COLORS['primary_dark']}">Pedido #{html.escape(str(h['cd_nuvemshop'] if pd.notna(h['cd_nuvemshop']) else 'Bling ' + str(h['cd_pedido'])))}</div>
      <div style="font-size:13px;color:{COLORS['text_secondary']}">{h['dt_pedido']:%d/%m/%Y} · {html.escape(str(h['nm_contato'] or '—'))}</div></div>
      {_chips([
          (str(h['ds_tipo_loja']) if pd.notna(h['ds_tipo_loja']) else "", *azul),
          (str(h['ds_status_pedido']).title() if pd.notna(h['ds_status_pedido']) else "", *cinza),
          (f"{meio}{f' {parc}x' if parc > 1 else ''}", *cinza),
          (("Recompra" if recorrente else "1ª compra") + n_ped, *cinza),
          (f"Cupom {h['ds_codigo_cupom_nuvemshop']}" if pd.notna(h['ds_codigo_cupom_nuvemshop']) and str(h['ds_codigo_cupom_nuvemshop']).strip() else "", *azul),
      ])}""")

    if it["ds_incompletude"].notna().any():
        faltas = " + ".join(sorted({t.strip() for v in it["ds_incompletude"].dropna() for t in v.split("+")}))
        note(f"<strong>Dado faltante neste pedido:</strong> {html.escape(faltas)}. A margem está superestimada.", variant="warn")
    if abs(r["folga"]) > 0.02:
        note(f"A cascata não fecha com a margem do dbt (diferença de {brl(r['folga'])}); confira o pedido.", variant="warn")

    # ═══ O PEDIDO ═══
    section_title("O pedido")
    _itens_html(it, fotos)

    # ═══ DO PREÇO AO VALOR PAGO ═══
    section_title("Do preço ao valor pago")
    partes = [f"{n} {brl(v)}" for n, v in [("cupom", r["cupom"]), ("Pix", r["pgto"]), ("promoção", r["promo"])] if v > 0.005]
    render_cards([
        card("Itens", brl(r["bruto"]), f"{int(it.loc[~it['fg_brinde'], 'qt_item'].sum())} un · preço cheio"),
        card("(−) Descontos", brl(r["desconto"]), " · ".join(partes) or "sem desconto"),
        card("(+) Frete cobrado", brl(r["frete"]), "pago pelo cliente" if r["frete"] > 0.005 else "frete grátis"),
        card("Valor pago", brl(r["pago"]), "o que o cliente pagou", variant="ok"),
    ])

    # ═══ DO VALOR PAGO AO CAIXA ═══
    section_title("Do valor pago ao caixa")
    receb = h["dt_recebimento_pedido"]
    quando = "" if pd.isna(receb) else (f"cai em {receb:%d/%m}" if receb > hoje else f"caiu em {receb:%d/%m}")
    cards = [
        card("(−) Taxa de pagamento", brl(r["taxa"]), f"{pct(r['taxa'] / r['pago']) if r['pago'] else '—'} do valor pago · taxa real do gateway"),
        card("Entrou na conta", brl(r["entrou"]), f"valor pago − taxa · {quando}" if quando else "valor pago − taxa", variant="ok"),
        card("(−) Frete (etiqueta)", brl(r["frete_real"]), f"o cliente pagou {brl(r['frete'])}"),
        card("(−) Embalagem", brl(r["embalagem"]), "estimada, por pedido"),
        card("(−) Imposto", brl(r["imposto"]), "sobre o valor já com desconto"),
    ]
    if r["reembolso"] > 0.005:
        cards.append(card("(−) Reembolso", brl(r["reembolso"]), "estornado ao cliente", variant="bad"))
    render_cards(cards)

    # ═══ CUSTO E MARGEM ═══
    section_title("Custo e margem")
    render_cards([
        card("(−) Custo dos produtos", brl(r["custo"]), f"inclui {brl(r['custo_brinde'])} de brinde" if r["custo_brinde"] > 0.005 else "CMV do pedido"),
        card("Margem de contribuição", brl(r["margem"]), "antes de mídia", variant=_variant_margem(r["margem_pct"])),
        card("Margem de contribuição (%)", pct(r["margem_pct"]), "sobre a receita líquida de produtos", variant=_variant_margem(r["margem_pct"])),
    ])

    # ═══ ORIGEM E CUSTO DE AQUISIÇÃO ═══
    section_title("Origem e custo de aquisição")
    mes = pd.Timestamp(h["dt_pedido"]).to_period("M").to_timestamp()
    aq, expl = None, ""
    try:
        google = carregar_pedidos_google_mes(f"{mes:%Y-%m-%d}") if (org["origem"], org["midia"]) == GOOGLE_PAGO else pd.DataFrame(columns=["cd_pedido", "ds_gclid"])
        camp = carregar_campanhas()
        aq, expl = custo_aquisicao(org["origem"], org["midia"], org["ds_gclid"], mes, google, camp["ponte"], camp["perf"])
    except Exception as e:  # noqa: BLE001
        expl = f"custo do Google Ads indisponível ({e})"
    pos = None if aq is None else r["margem"] - aq
    pos_pct = (pos / r["rec"]) if pos is not None and r["rec"] else None
    campanha = org["ds_utm_campaign"] if isinstance(org.get("ds_utm_campaign"), str) and org["ds_utm_campaign"] else "—"
    render_cards([
        card("Origem", html.escape(str(org["origem"])), f"mídia: {html.escape(str(org['midia']))}" + (f" · campanha: {html.escape(campanha)}" if campanha != "—" else "")),
        card("Custo de aquisição", brl(aq) if aq is not None else "—", expl),
        card("Margem após aquisição", brl(pos) if pos is not None else "—", "margem de contribuição − custo de aquisição",
             variant=_variant_margem(pos_pct) if pos is not None else "neutral"),
        card("Margem após aquisição (%)", pct(pos_pct), "sobre a receita líquida de produtos", variant=_variant_margem(pos_pct) if pos is not None else "neutral"),
    ])

    # ═══ PANORAMA ═══
    section_title("Panorama da venda")
    with st.container(border=True):
        st.plotly_chart(_cascata(r, aq), use_container_width=True)
