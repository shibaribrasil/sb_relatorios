"""Relatório Desempenho do Produto — Shibari Brasil (menu Catálogo).

Análise completa de um produto (família = o produto como aparece na loja): vendas, preço, custo e margem, evolução,
variações, perfil de compra, origem e interesse, trocas de custo seguidas de troca de preço e estoque. Filtros em
cascata Categoria → Subcategoria → Produto. Definição de cada indicador e limites: specs/produto-desempenho.md.

Regra de negócio: vendas, margem esperada, histórico de preço, trocas de custo, cesta e rupturas vêm prontos do dbt
(`tb_produto_venda_dia`, `tb_produto_margem_atual`, `tb_produto_historico_preco`, `tb_produto_troca_custo_preco`,
`tb_produto_cesta`, `tb_produto_ruptura_impacto`, `tb_produto_estoque_resumo`). Aqui só se filtra, soma e apresenta;
o GA4 (região US) é lido à parte e cruzado no pandas.
"""
import html
import re

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from common import bigquery as bq
from common.design import COLORS, inject_css, card, render_cards, section_title, note, plotly_layout, brl, pct
from common.frescor import carregar_frescor, badge_atualizacao, detalhe_atualizacao, alerta_atraso
from common.theme import METRIC_COLORS
from reports.vendas_margem import _hoje_brt

AZ = f"{bq.PROJECT}.dbt_dw_az"
TABELAS = ("tb_produto_gestao", "tb_produto_venda_dia", "tb_produto_margem_atual", "tb_produto_historico_preco",
           "tb_produto_troca_custo_preco", "tb_produto_ruptura_impacto", "tb_produto_estoque_resumo", "tb_produto_cesta")
EXTRATORES = ("nuvemshop_orders", "bling_products")

HISTORICO_CONFIAVEL = pd.Timestamp("2025-08-01")      # custo e taxa reais da Nuvemshop desde ago/2025
INICIO_ITENS_GA4 = pd.Timestamp("2026-08-29")         # primeiro dia com eventos de e-commerce por produto
MIN_PEDIDOS_LIFT = 3                                  # lift abaixo disso é ruído
COR_PRECO = COLORS["accent"]
COR_CUSTO = COLORS["text_muted"]


# ── carga ────────────────────────────────────────────────────────────────────

def _in(ids):
    """Lista SQL de ids numéricos (valida: só dígitos, ids vêm do próprio BigQuery)."""
    ids = [str(i) for i in ids if re.fullmatch(r"\d+", str(i))]
    return ", ".join(f"'{i}'" for i in ids) or "''"


@st.cache_data(ttl=900)
def carregar_catalogo():
    client = bq.get_client()
    df = bq.query_df(client, f"""
        SELECT g.cd_produto_bling, g.cd_produto_bling_familia AS cd_familia, g.cd_produto_nuvemshop, g.cd_variacao_nuvemshop,
               g.nm_produto, g.ds_variacao, g.ds_categoria, g.ds_subcategoria, g.ds_frente, g.ds_ciclo, g.ds_papel, g.ds_origem,
               g.ds_tipo_oferta, g.fl_publicado_nuvemshop, g.fg_visivel_site, g.fg_em_promocao,
               g.vl_preco_de_nuvemshop, g.vl_preco_por_nuvemshop, g.lk_imagem_produto,
               g.qt_estoque_bling, g.qt_cobertura_atual, g.qt_compra_pendente, g.qt_dias_sem_venda, g.dt_ultima_venda,
               g.qt_pecas_90d, g.vl_receita_liquida_90d, g.vl_margem_contribuicao_90d,
               g.ds_nivel_exposicao, g.fl_na_home, g.ds_prateleiras_home, g.qt_paginas_como_similar, g.qt_paginas_como_complementar,
               g.nr_ranking_mais_vendidos, g.fl_sem_seo, g.qt_imagens_sem_alt, g.qt_variacoes_sem_gtin_produto,
               g.fg_sem_custo, g.fg_sem_peso, g.fg_sem_imagem,
               m.vl_preco_atual, m.vl_custo_atual, m.fg_sem_custo AS fg_custo_zero,
               m.vl_margem_unitaria_tabela, m.pct_margem_tabela, m.vl_margem_unitaria_pix, m.pct_margem_pix,
               e.dt_primeira_foto, e.qt_dias_observados, e.qt_dias_sem_estoque, e.pct_tempo_sem_estoque,
               e.qt_rupturas, e.qt_dias_medio_ruptura
          FROM `{AZ}.tb_produto_gestao` AS g
          LEFT JOIN `{AZ}.tb_produto_margem_atual` AS m USING (cd_produto_bling)
          LEFT JOIN `{AZ}.tb_produto_estoque_resumo` AS e USING (cd_produto_bling)
    """)
    for c in ["vl_preco_atual", "vl_custo_atual", "qt_estoque_bling", "qt_cobertura_atual", "qt_compra_pendente", "qt_dias_sem_venda",
              "qt_pecas_90d", "vl_receita_liquida_90d", "vl_margem_contribuicao_90d", "vl_margem_unitaria_tabela", "pct_margem_tabela",
              "vl_margem_unitaria_pix", "pct_margem_pix", "qt_dias_observados", "qt_dias_sem_estoque", "pct_tempo_sem_estoque",
              "qt_rupturas", "qt_dias_medio_ruptura", "qt_paginas_como_similar", "qt_paginas_como_complementar",
              "nr_ranking_mais_vendidos", "qt_imagens_sem_alt", "qt_variacoes_sem_gtin_produto"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    for c in ["cd_produto_bling", "cd_familia"]:
        df[c] = df[c].astype(str)
    df["dt_ultima_venda"] = pd.to_datetime(df["dt_ultima_venda"])
    df["rotulo_variacao"] = df["ds_variacao"].fillna("").replace({"Sem Variacao": "Única", "": "Única"})
    return df


@st.cache_data(ttl=900)
def carregar_produto(skus: tuple, familia: str):
    """Tudo que depende dos SKUs da família: vendas, histórico de preço, trocas, rupturas, estoque, cesta e perfil."""
    client = bq.get_client()
    lista = _in(skus)
    vendas = bq.query_df(client, f"""
        SELECT dt_data, cd_produto_bling, qt_pedidos, qt_unidades, vl_receita_bruta, vl_receita_liquida, vl_custo, vl_margem_contribuicao
          FROM `{AZ}.tb_produto_venda_dia` WHERE cd_produto_bling IN ({lista})
    """)
    hist = bq.query_df(client, f"""
        SELECT * EXCEPT (ts_load) FROM `{AZ}.tb_produto_historico_preco` WHERE cd_produto_bling IN ({lista})
    """)
    troca = bq.query_df(client, f"""
        SELECT * EXCEPT (ts_load) FROM `{AZ}.tb_produto_troca_custo_preco` WHERE cd_produto_bling IN ({lista})
    """)
    ruptura = bq.query_df(client, f"""
        SELECT * EXCEPT (ts_load) FROM `{AZ}.tb_produto_ruptura_impacto` WHERE cd_produto_bling IN ({lista})
    """)
    posicao = bq.query_df(client, f"""
        SELECT dt_posicao, cd_produto_bling, qt_estoque_atual FROM `{AZ}.tb_estoque_posicao_dia` WHERE cd_produto_bling IN ({lista})
    """)
    cesta = bq.query_df(client, f"""
        SELECT cd_produto_familia_b, nm_produto_familia_b, qt_pedidos_a, qt_pedidos_b, qt_pedidos_juntos, pct_pedidos_a, vl_lift, fg_sozinho
          FROM `{AZ}.tb_produto_cesta` WHERE cd_produto_familia_a = '{re.sub(r"[^0-9]", "", familia)}'
    """)
    perfil = bq.query_df(client, f"""
        WITH ped AS (
            SELECT cd_codigo_interno, ANY_VALUE(cd_pedido) AS cd_pedido, ANY_VALUE(dt_pedido) AS dt_pedido,
                   ANY_VALUE(fg_primeiro_pedido_cliente) AS fg_primeiro, ANY_VALUE(dt_proxima_compra_cliente) AS dt_proxima
              FROM `{AZ}.tb_pedido`
             WHERE fg_pedido_valido AND NOT COALESCE(fg_brinde, FALSE) AND cd_produto_bling IN ({lista})
             GROUP BY 1),
        loja AS (
            SELECT cd_codigo_interno, ANY_VALUE(dt_pedido) AS dt_pedido,
                   ANY_VALUE(fg_primeiro_pedido_cliente) AS fg_primeiro, ANY_VALUE(dt_proxima_compra_cliente) AS dt_proxima
              FROM `{AZ}.tb_pedido` WHERE fg_pedido_valido AND NOT COALESCE(fg_brinde, FALSE) GROUP BY 1),
        corte AS (SELECT DATE_SUB(CURRENT_DATE('America/Sao_Paulo'), INTERVAL 90 DAY) AS d)
        SELECT 'produto' AS base, COUNT(*) AS pedidos, COUNTIF(fg_primeiro) AS primeiros,
               COUNTIF(dt_pedido <= (SELECT d FROM corte)) AS elegiveis,
               COUNTIF(dt_pedido <= (SELECT d FROM corte) AND dt_proxima IS NOT NULL AND DATE_DIFF(dt_proxima, dt_pedido, DAY) <= 90) AS voltaram
          FROM ped
        UNION ALL
        SELECT 'loja', COUNT(*), COUNTIF(fg_primeiro), COUNTIF(dt_pedido <= (SELECT d FROM corte)),
               COUNTIF(dt_pedido <= (SELECT d FROM corte) AND dt_proxima IS NOT NULL AND DATE_DIFF(dt_proxima, dt_pedido, DAY) <= 90)
          FROM loja
    """)
    origem = bq.query_df(client, f"""
        WITH ped AS (
            SELECT DISTINCT cd_pedido FROM `{AZ}.tb_pedido`
             WHERE fg_pedido_valido AND NOT COALESCE(fg_brinde, FALSE) AND cd_produto_bling IN ({lista}))
        SELECT COALESCE(a.ds_origem_venda, '(sem parâmetro)') AS origem, COUNT(*) AS pedidos
          FROM ped LEFT JOIN `{AZ}.tb_atribuicao_pedido` AS a USING (cd_pedido)
         GROUP BY 1 ORDER BY 2 DESC
    """)
    acao = bq.query_df(client, f"""
        WITH ped AS (
            SELECT DISTINCT cd_codigo_interno FROM `{AZ}.tb_pedido`
             WHERE fg_pedido_valido AND NOT COALESCE(fg_brinde, FALSE) AND cd_produto_bling IN ({lista}))
        SELECT COALESCE(a.ds_grupo_acao, 'Sem ação') AS acao, COUNT(*) AS pedidos
          FROM ped LEFT JOIN `{AZ}.tb_pedido_acao` AS a USING (cd_codigo_interno)
         GROUP BY 1 ORDER BY 2 DESC
    """)
    for d, cols in ((vendas, ["qt_pedidos", "qt_unidades", "vl_receita_bruta", "vl_receita_liquida", "vl_custo", "vl_margem_contribuicao"]),
                    (cesta, ["qt_pedidos_a", "qt_pedidos_b", "qt_pedidos_juntos", "pct_pedidos_a", "vl_lift"]),
                    (perfil, ["pedidos", "primeiros", "elegiveis", "voltaram"]),
                    (origem, ["pedidos"]), (acao, ["pedidos"]),
                    (posicao, ["qt_estoque_atual"])):
        for c in cols:
            d[c] = pd.to_numeric(d[c], errors="coerce")
    vendas["dt_data"] = pd.to_datetime(vendas["dt_data"])
    posicao["dt_posicao"] = pd.to_datetime(posicao["dt_posicao"])
    for d, cols in ((hist, ["dt_inicio", "dt_fim"]), (troca, ["dt_troca_custo", "dt_troca_preco"]), (ruptura, ["dt_inicio", "dt_fim"])):
        for c in cols:
            d[c] = pd.to_datetime(d[c])
    for d in (hist, troca, ruptura):
        for c in d.columns:
            if c.startswith(("vl_", "qt_", "pct_")):
                d[c] = pd.to_numeric(d[c], errors="coerce")
    return {"vendas": vendas, "hist": hist, "troca": troca, "ruptura": ruptura, "posicao": posicao, "cesta": cesta,
            "perfil": perfil, "origem": origem, "acao": acao}


@st.cache_data(ttl=900)
def carregar_interesse(itens_ga4: tuple, skus: tuple, ini, fim):
    """Funil do GA4 (visitas, carrinhos) do produto e da loja na janela; pedidos do produto e da loja (az)."""
    client = bq.get_client()
    ga = bq.query_df(client, f"""
        SELECT nm_evento,
               SUM(IF(cd_item_ga4 IN ({_in(itens_ga4)}), qt_sessoes, 0)) AS produto,
               SUM(qt_sessoes) AS loja
          FROM `{bq.PROJECT}.dbt_dw_us_az.tb_ga4_produto_dia`
         WHERE dt_data BETWEEN DATE '{ini}' AND DATE '{fim}' AND nm_evento IN ('view_item', 'add_to_cart')
         GROUP BY 1
    """)
    ped = bq.query_df(client, f"""
        SELECT COUNT(DISTINCT IF(cd_produto_bling IN ({_in(skus)}), cd_codigo_interno, NULL)) AS produto,
               COUNT(DISTINCT cd_codigo_interno) AS loja
          FROM `{AZ}.tb_pedido`
         WHERE fg_pedido_valido AND NOT COALESCE(fg_brinde, FALSE) AND dt_pedido BETWEEN DATE '{ini}' AND DATE '{fim}'
    """)
    for d in (ga, ped):
        for c in ("produto", "loja"):
            d[c] = pd.to_numeric(d[c], errors="coerce").fillna(0)
    return ga, ped


# ── cálculos ─────────────────────────────────────────────────────────────────

def janelas(vendas, hoje):
    """Unidades e receita nas janelas pedidas (ver spec): mês atual até hoje, 30 e 90 dias fechados, mês de pico."""
    hoje = pd.Timestamp(hoje)
    ontem = hoje - pd.Timedelta(days=1)
    mes_ini = hoje.replace(day=1)
    ant_ini = mes_ini - pd.DateOffset(months=1)
    ant_fim = min(ant_ini + (hoje - mes_ini), mes_ini - pd.Timedelta(days=1))

    def soma(d, ini, fim):
        s = d[(d["dt_data"] >= ini) & (d["dt_data"] <= fim)]
        return {"un": float(s["qt_unidades"].sum()), "rec": float(s["vl_receita_liquida"].sum()),
                "ped": float(s["qt_pedidos"].sum()), "mc": float(s["vl_margem_contribuicao"].sum())}

    out = {
        "mes": soma(vendas, mes_ini, hoje), "mes_ant": soma(vendas, ant_ini, ant_fim),
        "d30": soma(vendas, ontem - pd.Timedelta(days=29), ontem), "d90": soma(vendas, ontem - pd.Timedelta(days=89), ontem),
        "ant_ini": ant_ini, "ant_fim": ant_fim,
    }
    base = vendas[vendas["dt_data"] >= HISTORICO_CONFIAVEL]
    if base.empty:
        out["pico"] = None
    else:
        m = base.groupby(base["dt_data"].dt.to_period("M"))["qt_unidades"].sum()
        out["pico"] = (m.idxmax().to_timestamp(), float(m.max()))
    return out


def _media_ponderada(valores, pesos):
    v = pd.to_numeric(valores, errors="coerce")
    p = pd.to_numeric(pesos, errors="coerce").fillna(0)
    ok = v.notna()
    if not ok.any():
        return None
    if p[ok].sum() > 0:
        return float((v[ok] * p[ok]).sum() / p[ok].sum())
    return float(v[ok].mean())


def serie(vendas, gran, ini, fim):
    """Unidades e receita por dia/semana/mês, com os períodos sem venda preenchidos com zero."""
    dias = pd.date_range(ini, fim, freq="D")
    d = vendas.groupby("dt_data")[["qt_unidades", "vl_receita_liquida"]].sum().reindex(dias, fill_value=0)
    if gran == "Semana":
        d = d.resample("W-SUN", label="left", closed="left").sum()
    elif gran == "Mês":
        d = d.resample("MS").sum()
    return d.reset_index().rename(columns={"index": "periodo"})


def resumo_trocas(troca, piso, detalhar, rot_var):
    """Trocas de custo seguidas de troca de preço com variação líquida do custo ≥ piso. Família: agrupa por dia da troca de preço."""
    t = troca[troca["pct_var_custo"].abs() >= piso].copy()
    if t.empty:
        return t
    t["Variação"] = t["cd_produto_bling"].map(rot_var)
    if detalhar:
        return t.sort_values("dt_troca_preco", ascending=False)
    g = t.groupby("dt_troca_preco").agg(
        dt_troca_custo=("dt_troca_custo", "min"), qt_var=("cd_produto_bling", "nunique"),
        pct_var_custo=("pct_var_custo", "mean"), pct_var_preco=("pct_var_preco", "mean"),
        vl_custo_anterior=("vl_custo_anterior", "mean"), vl_custo_novo=("vl_custo_novo", "mean"),
        vl_preco_anterior=("vl_preco_anterior", "mean"), vl_preco_novo=("vl_preco_novo", "mean"),
        pct_margem_lista_antes=("pct_margem_lista_antes", "mean"), pct_margem_lista_depois=("pct_margem_lista_depois", "mean"),
        vl_unidades_dia_preco_anterior=("vl_unidades_dia_preco_anterior", "sum"), vl_unidades_dia_preco_novo=("vl_unidades_dia_preco_novo", "sum"),
        qt_unidades_preco_anterior=("qt_unidades_preco_anterior", "sum"), qt_unidades_preco_novo=("qt_unidades_preco_novo", "sum"),
        qt_dias_preco_novo=("qt_dias_preco_novo", "min"),
    ).reset_index()
    g["vl_repasse"] = g["pct_var_preco"] / g["pct_var_custo"]
    g["qt_dias_ate_reacao"] = (g["dt_troca_preco"] - g["dt_troca_custo"]).dt.days
    return g.sort_values("dt_troca_preco", ascending=False)


# ── apresentação ─────────────────────────────────────────────────────────────

def _foto(links):
    """Link do Bling é assinado (S3) e vence: vale o de maior validade entre os SKUs."""
    ls = [l for l in links if isinstance(l, str) and l]
    if not ls:
        return None
    return max(ls, key=lambda l: int((re.search(r"Expires=(\d+)", l) or [0, 0])[1]))


def _chips(itens):
    return "".join(f'<span style="background:{bg};color:{fg};border-radius:4px;padding:2px 8px;font-size:11px;font-weight:600;margin:0 6px 6px 0;display:inline-block">{html.escape(t)}</span>'
                   for t, bg, fg in itens if t)


def _cabecalho_produto(f, cat_sku):
    foto = _foto(cat_sku["lk_imagem_produto"])
    img = (f'<img src="{html.escape(foto)}" style="width:150px;height:150px;object-fit:cover;border-radius:10px;border:1px solid {COLORS["border"]}" '
           f'onerror="this.style.display=\'none\'">') if foto else f'<div style="width:150px;height:150px;border-radius:10px;background:{COLORS["bg_secondary"]}"></div>'
    r0 = cat_sku.iloc[0]
    est = float(cat_sku["qt_estoque_bling"].clip(lower=0).sum())
    publicado = bool(cat_sku["fg_visivel_site"].fillna(False).any())
    promo = bool(cat_sku["fg_em_promocao"].fillna(False).any())
    oferta = next((o for o in cat_sku["ds_tipo_oferta"].dropna().unique() if str(o).strip()), "")
    azul, cinza = (COLORS["primary_light"], COLORS["primary_dark"]), (COLORS["bg_secondary"], COLORS["text_secondary"])
    verde, ambar, verm = (COLORS["success_bg"], COLORS["success"]), (COLORS["warning_bg"], COLORS["warning"]), (COLORS["danger_bg"], COLORS["danger"])
    chips = _chips([
        (str(r0["ds_frente"]) if pd.notna(r0["ds_frente"]) else "", *azul),
        (str(r0["ds_papel"]) if pd.notna(r0["ds_papel"]) else "", *cinza),
        (str(r0["ds_ciclo"]) if pd.notna(r0["ds_ciclo"]) else "Sem ciclo de vida", *cinza),
        (str(r0["ds_origem"]) if pd.notna(r0["ds_origem"]) else "", *cinza),
        ("No site" if publicado else "Fora do site", *(verde if publicado else verm)),
        (f'Exposição: {r0["ds_nivel_exposicao"]}' if pd.notna(r0["ds_nivel_exposicao"]) else "", *azul),
        (f"Estoque: {est:.0f} un" if est > 0 else "Sem estoque", *(cinza if est > 0 else verm)),
        ("Promoção ativa" if promo else "", *ambar),
        (f"Oferta: {oferta}" if oferta else "", *ambar),
    ])
    sub = f'{html.escape(str(r0["ds_categoria"]))} › {html.escape(str(r0["ds_subcategoria"]))} · {len(cat_sku)} variação(ões)'
    st.html(f"""<div style="display:flex;gap:18px;align-items:center;margin:6px 0 14px">
      {img}
      <div><div style="font-size:22px;font-weight:800;color:{COLORS['primary_dark']};line-height:1.2">{html.escape(f)}</div>
      <div style="font-size:12px;color:{COLORS['text_secondary']};margin:3px 0 10px">{sub}</div>{chips}</div></div>""")


def _delta_txt(atual, ant, rot):
    if ant is None or ant == 0:
        return "", ""
    d = atual / ant - 1
    return f"{'+' if d >= 0 else '−'}{abs(d) * 100:.0f}% {rot}", (COLORS["success"] if d >= 0 else COLORS["danger"])


def _faixa(vals, fmt):
    v = pd.to_numeric(vals, errors="coerce").dropna()
    if v.empty:
        return ""
    return f"faixa {fmt(v.min())} a {fmt(v.max())}" if v.max() - v.min() > 0.005 else ""


def _numeros(cat_sku, w, vendas):
    peso = cat_sku["qt_pecas_90d"]
    preco = _media_ponderada(cat_sku["vl_preco_atual"], peso)
    custo = _media_ponderada(cat_sku["vl_custo_atual"].where(cat_sku["vl_custo_atual"] > 0), peso)
    m_tab = _media_ponderada(cat_sku["pct_margem_tabela"], peso)
    m_pix = _media_ponderada(cat_sku["pct_margem_pix"], peso)
    rec90 = float(cat_sku["vl_receita_liquida_90d"].sum())
    m_real = float(cat_sku["vl_margem_contribuicao_90d"].sum()) / rec90 if rec90 > 0 else None
    mes_txt, mes_cor = _delta_txt(w["mes"]["un"], w["mes_ant"]["un"], f"vs {w['ant_ini']:%d/%m}–{w['ant_fim']:%d/%m}")
    pico = w["pico"]
    sem_custo = cat_sku["fg_custo_zero"].fillna(True).all()
    render_cards([
        card("Vendas no mês (até hoje)", f"{w['mes']['un']:.0f} un", f"{brl(w['mes']['rec'], 0)} · {w['mes']['ped']:.0f} pedidos", delta=mes_txt, delta_color=mes_cor,
             ref=f"Mês anterior no mesmo intervalo: {w['mes_ant']['un']:.0f} un"),
        card("Últimos 30 dias", f"{w['d30']['un']:.0f} un", f"{brl(w['d30']['rec'], 0)} · {w['d30']['ped']:.0f} pedidos", ref="30 dias fechados (até ontem)"),
        card("Últimos 90 dias", f"{w['d90']['un']:.0f} un", f"{brl(w['d90']['rec'], 0)} · {w['d90']['ped']:.0f} pedidos", ref="90 dias fechados (até ontem)"),
        card("Mês de maior venda", f"{pico[1]:.0f} un" if pico else "—", f"{pico[0]:%m/%Y}" if pico else "sem histórico", ref="Desde ago/2025 (histórico confiável)"),
    ])
    render_cards([
        card("Preço atual", brl(preco), _faixa(cat_sku["vl_preco_atual"], brl) or "preço de venda hoje",
             ref="Média ponderada pelas vendas de 90 dias" if len(cat_sku) > 1 else "Preço “por” da Nuvemshop"),
        card("Custo atual", brl(custo) if custo is not None else "sem custo", _faixa(cat_sku["vl_custo_atual"].where(cat_sku["vl_custo_atual"] > 0), brl) or "custo da última compra/produção",
             ref="CMV do cadastro"),
        card("Margem de contribuição esperada", "sem custo" if sem_custo else pct(m_tab),
             "" if sem_custo else f"no Pix (−3%): {pct(m_pix)}", ref="Parâmetros da tabela de precificação · antes de mídia"),
        card("Margem realizada (90 dias)", pct(m_real) if m_real is not None else "—", "da tb_pedido: cupom, frete e mix de pagamento",
             ref="Margem de contribuição ÷ receita líquida"),
    ])
    note("A margem <b>esperada</b> é a do preço de hoje com os parâmetros da tabela de precificação (taxa Pix, R$ 2,50 de embalagem por unidade, desconto padrão). "
         "A <b>realizada</b> vem dos pedidos e já inclui cupom, frete e o mix de pagamento — por isso costuma ficar abaixo da esperada. Margem antes de mídia.")


def _grafico_evolucao(vendas, hist, rot_var):
    c1, c2, c3 = st.columns([1.2, 1, 3])
    gran = c1.radio("Agrupar por", ["Dia", "Semana", "Mês"], index=2, horizontal=True, key="pd_gran")
    jan = c2.selectbox("Período", ["Últimos 12 meses", "Desde ago/2025", "Todo o histórico"], index=0, key="pd_jan")
    hoje = pd.Timestamp(_hoje_brt())
    ini = {"Últimos 12 meses": hoje - pd.DateOffset(months=12), "Desde ago/2025": HISTORICO_CONFIAVEL}.get(jan)
    if ini is None:
        ini = vendas["dt_data"].min() if not vendas.empty else hoje - pd.DateOffset(months=12)
    if gran == "Dia":
        ini = max(ini, hoje - pd.Timedelta(days=120))
    s = serie(vendas, gran, ini.normalize(), hoje)
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_bar(x=s["periodo"], y=s["qt_unidades"], name="Unidades", marker_color=METRIC_COLORS["receita"],
                hovertemplate="%{x|%d/%m/%Y}<br>%{y:.0f} un<extra></extra>", secondary_y=False)
    fig.add_scatter(x=s["periodo"], y=s["vl_receita_liquida"], name="Receita líquida (R$)", mode="lines+markers",
                    line=dict(color=COLORS["accent"], width=2), marker=dict(size=5),
                    hovertemplate="%{x|%d/%m/%Y}<br>R$ %{y:,.0f}<extra></extra>", secondary_y=True)
    trocas = hist[(hist["nr_periodo"] > 1) & (hist["dt_inicio"] >= ini)]    # o 1º período não nasceu de uma troca
    if not trocas.empty:
        g = trocas.groupby("dt_inicio").apply(lambda d: "<br>".join(f"{rot_var.get(r.cd_produto_bling, '')}: {brl(r.vl_preco)}" for r in d.itertuples()), include_groups=False)
        topo = float(s["qt_unidades"].max()) or 1.0
        fig.add_scatter(x=g.index, y=[topo * 1.08] * len(g), mode="markers", name="Troca de preço",
                        marker=dict(symbol="diamond", size=9, color=COR_PRECO, line=dict(color="#fff", width=1)),
                        text=g.values, hovertemplate="%{x|%d/%m/%Y} — preço novo<br>%{text}<extra></extra>", secondary_y=False)
    plotly_layout(fig, height=360, bargap=0.25)
    fig.update_yaxes(title_text="Unidades", secondary_y=False, rangemode="tozero")
    fig.update_yaxes(title_text="Receita (R$)", secondary_y=True, rangemode="tozero", showgrid=False)
    st.plotly_chart(fig, width="stretch")
    note("Cada losango laranja é uma troca de preço de alguma variação (passe o mouse para ver o preço novo). "
         "Com ~1 pedido por dia, semana e dia oscilam muito: leia a tendência pelo mês.")


def _tabela_variacoes(cat_sku, vendas, hoje):
    ontem = pd.Timestamp(hoje) - pd.Timedelta(days=1)
    mes_ini = pd.Timestamp(hoje).replace(day=1)

    def un(ini, fim):
        s = vendas[(vendas["dt_data"] >= ini) & (vendas["dt_data"] <= fim)]
        return s.groupby("cd_produto_bling")["qt_unidades"].sum()

    base = vendas[vendas["dt_data"] >= HISTORICO_CONFIAVEL]
    pico = base.groupby(["cd_produto_bling", base["dt_data"].dt.to_period("M")])["qt_unidades"].sum().groupby("cd_produto_bling").max()
    r90 = vendas[(vendas["dt_data"] >= ontem - pd.Timedelta(days=89)) & (vendas["dt_data"] <= ontem)].groupby("cd_produto_bling")[
        ["vl_receita_liquida", "vl_margem_contribuicao"]].sum()
    t = cat_sku.set_index("cd_produto_bling")
    out = pd.DataFrame({
        "Variação": t["rotulo_variacao"],
        "Preço": t["vl_preco_atual"], "Custo": t["vl_custo_atual"].where(t["vl_custo_atual"] > 0),
        "Margem esperada": t["pct_margem_tabela"],
        "Mês": un(mes_ini, pd.Timestamp(hoje)), "30 dias": un(ontem - pd.Timedelta(days=29), ontem),
        "90 dias": un(ontem - pd.Timedelta(days=89), ontem), "Mês de pico": pico,
        "Receita 90d": r90["vl_receita_liquida"],
        "Margem realizada 90d": (r90["vl_margem_contribuicao"] / r90["vl_receita_liquida"].replace(0, pd.NA)).astype(float),
        "Estoque": t["qt_estoque_bling"].clip(lower=0), "Sem venda há (dias)": t["qt_dias_sem_venda"],
        "% do tempo sem estoque": t["pct_tempo_sem_estoque"],
    })
    for c in ["Mês", "30 dias", "90 dias", "Mês de pico", "Receita 90d"]:
        out[c] = out[c].fillna(0)
    out = out.sort_values("90 dias", ascending=False).reset_index(drop=True)
    st.dataframe(out, hide_index=True, width="stretch", column_config={
        "Preço": st.column_config.NumberColumn(format="R$ %.2f"), "Custo": st.column_config.NumberColumn(format="R$ %.2f"),
        "Margem esperada": st.column_config.NumberColumn(format="percent"),
        "Mês": st.column_config.NumberColumn(format="%d un"), "30 dias": st.column_config.NumberColumn(format="%d un"),
        "90 dias": st.column_config.NumberColumn(format="%d un"), "Mês de pico": st.column_config.NumberColumn(format="%d un"),
        "Receita 90d": st.column_config.NumberColumn(format="R$ %.0f"),
        "Margem realizada 90d": st.column_config.NumberColumn(format="percent"),
        "Estoque": st.column_config.NumberColumn(format="%d un"), "Sem venda há (dias)": st.column_config.NumberColumn(format="%d"),
        "% do tempo sem estoque": st.column_config.NumberColumn(format="percent"),
    })
    note("Margem esperada = preço de hoje com os parâmetros da tabela de precificação; realizada = o que os pedidos dos últimos 90 dias deixaram. "
         "Variação sem custo cadastrado fica sem margem esperada. “% do tempo sem estoque” conta só desde 02/07/2026.")


def _secao_perfil(d, nm_familia):
    cesta = d["cesta"]
    p = d["perfil"].set_index("base")
    if "produto" not in p.index or p.loc["produto", "pedidos"] == 0:
        note("Este produto ainda não tem pedido válido no histórico.")
        return
    n = float(p.loc["produto", "pedidos"])
    sozinho = cesta[cesta["fg_sozinho"].astype(bool)]
    pct_sozinho = float(sozinho["pct_pedidos_a"].iloc[0]) if not sozinho.empty else 0.0
    prim = p.loc["produto", "primeiros"] / n
    prim_loja = p.loc["loja", "primeiros"] / max(p.loc["loja", "pedidos"], 1)
    elig, elig_loja = p.loc["produto", "elegiveis"], p.loc["loja", "elegiveis"]
    volt = p.loc["produto", "voltaram"] / elig if elig >= 5 else None
    volt_loja = p.loc["loja", "voltaram"] / max(elig_loja, 1)
    render_cards([
        card("Pedidos com o produto", f"{n:.0f}", "histórico inteiro, sem brinde"),
        card("Comprado sozinho", pct(pct_sozinho), f"{(1 - pct_sozinho) * 100:.0f}% vão com outro produto", ref="Só ele no pedido"),
        card("Porta de entrada", pct(prim), f"loja: {pct(prim_loja)}", ref="Pedidos que foram a 1ª compra do cliente"),
        card("Cliente volta em até 90 dias", pct(volt) if volt is not None else "poucos dados", f"loja: {pct(volt_loja)}",
             ref=f"Entre {elig:.0f} pedidos com mais de 90 dias"),
    ])
    c1, c2 = st.columns([1.5, 1])
    comp = cesta[~cesta["fg_sozinho"].astype(bool)].copy()
    comp = comp[comp["qt_pedidos_juntos"] >= 1].sort_values(["qt_pedidos_juntos", "vl_lift"], ascending=False)
    with c1:
        st.markdown("**Com o que ele é comprado**")
        if comp.empty:
            st.caption("Nenhum pedido com outro produto.")
        else:
            top = comp.head(10).copy()
            fig = go.Figure(go.Bar(y=top["nm_produto_familia_b"].fillna("(sem nome)"), x=top["pct_pedidos_a"], orientation="h",
                                   marker_color=METRIC_COLORS["receita"], customdata=top[["qt_pedidos_juntos"]],
                                   hovertemplate="%{y}<br>%{x:.1%} dos pedidos (%{customdata[0]:.0f} pedidos)<extra></extra>"))
            fig.update_yaxes(autorange="reversed")
            fig.update_xaxes(tickformat=".0%")
            plotly_layout(fig, height=max(240, 30 * len(top) + 60), margin=dict(l=10, r=10, t=10, b=10))
            st.plotly_chart(fig, width="stretch")
    with c2:
        st.markdown("**Afinidade (lift)**")
        t = comp.head(10).copy()
        t["Lift"] = t["vl_lift"].where(t["qt_pedidos_juntos"] >= MIN_PEDIDOS_LIFT)
        st.dataframe(t.rename(columns={"nm_produto_familia_b": "Produto", "qt_pedidos_juntos": "Pedidos juntos"})[["Produto", "Pedidos juntos", "Lift"]],
                     hide_index=True, width="stretch",
                     column_config={"Lift": st.column_config.NumberColumn(format="%.1f×"), "Pedidos juntos": st.column_config.NumberColumn(format="%d")})
    note(f"“% dos pedidos” = parcela dos pedidos de <b>{html.escape(nm_familia)}</b> que levaram também o outro produto. "
         f"<b>Lift</b> acima de 1 = os dois aparecem juntos mais do que o acaso (só mostrado com {MIN_PEDIDOS_LIFT}+ pedidos juntos); 1 = sem relação. "
         "Com ~1 pedido por dia, poucos pedidos juntos já pesam: leia como pista, não como regra.")


def _secao_acao(d, cat_sku):
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Em ação hoje**")
        promo = cat_sku["fg_em_promocao"].fillna(False).any()
        oferta = [o for o in cat_sku["ds_tipo_oferta"].dropna().unique() if str(o).strip()]
        de, por = cat_sku["vl_preco_de_nuvemshop"].max(), cat_sku["vl_preco_por_nuvemshop"].replace(0, pd.NA).min()
        linhas = [("Tipo de oferta (Cashing, campo do Bling)", ", ".join(oferta) if oferta else "Nenhuma"),
                  ("Promoção ativa na Nuvemshop", f"Sim — de {brl(de)} por {brl(float(por))}" if promo and pd.notna(por) else ("Sim" if promo else "Não"))]
        st.dataframe(pd.DataFrame(linhas, columns=["Ação", "Situação"]), hide_index=True, width="stretch")
        if not oferta:
            st.caption("O time ainda está preenchendo o campo “Tipo de oferta” no Bling: vazio não quer dizer que o produto não esteja em ação.")
    with c2:
        st.markdown("**Ações dos pedidos que levaram o produto**")
        a = d["acao"].copy()
        tot = a["pedidos"].sum()
        a["% dos pedidos"] = a["pedidos"] / tot if tot else 0
        st.dataframe(a.rename(columns={"acao": "Ação", "pedidos": "Pedidos"}), hide_index=True, width="stretch",
                     column_config={"% dos pedidos": st.column_config.NumberColumn(format="percent")})


def _secao_origem_interesse(d, cat_sku, hoje):
    c1, c2 = st.columns([1, 1.3])
    with c1:
        st.markdown("**De onde vêm as vendas**")
        o = d["origem"].copy()
        if o.empty:
            st.caption("Sem pedidos.")
        else:
            fig = go.Figure(go.Bar(y=o["origem"], x=o["pedidos"], orientation="h", marker_color=METRIC_COLORS["pedidos"],
                                   hovertemplate="%{y}: %{x:.0f} pedidos<extra></extra>"))
            fig.update_yaxes(autorange="reversed")
            plotly_layout(fig, height=max(200, 34 * len(o) + 50))
            st.plotly_chart(fig, width="stretch")
            st.caption("Origem do pedido pela URL de entrada; “(sem parâmetro)” inclui direto e orgânico sem UTM (cerca de 90% dos pedidos não têm UTM).")
    with c2:
        st.markdown("**Interesse × compra (últimos 30 dias)**")
        fim = pd.Timestamp(hoje) - pd.Timedelta(days=1)
        ini = max(fim - pd.Timedelta(days=29), INICIO_ITENS_GA4)
        itens = tuple(sorted({str(int(x)) for x in cat_sku["cd_variacao_nuvemshop"].dropna()} | {str(int(x)) for x in cat_sku["cd_produto_nuvemshop"].dropna()}))
        if not itens:
            st.caption("Produto sem ID da Nuvemshop: não há como ligar ao GA4.")
            return
        ga, ped = carregar_interesse(itens, tuple(cat_sku["cd_produto_bling"]), ini.date(), fim.date())
        g = ga.set_index("nm_evento")
        vis = float(g["produto"].get("view_item", 0)); car = float(g["produto"].get("add_to_cart", 0))
        vis_l = float(g["loja"].get("view_item", 0)); car_l = float(g["loja"].get("add_to_cart", 0))
        pe, pe_l = float(ped["produto"].iloc[0]), float(ped["loja"].iloc[0])
        render_cards([
            card("Visitas à página", f"{vis:.0f}", f"{(ini):%d/%m} a {fim:%d/%m}", ref="Sessões com view_item (soma das variações)"),
            card("Foram ao carrinho", f"{car:.0f}", f"{pct(car / vis) if vis else '—'} das visitas", ref=f"loja: {pct(car_l / vis_l) if vis_l else '—'}"),
            card("Pedidos", f"{pe:.0f}", f"{pct(pe / vis) if vis else '—'} das visitas", ref=f"loja: {pct(pe_l / vis_l) if vis_l else '—'} (pedidos ÷ visitas a produtos)"),
        ])
        st.caption(f"GA4 só tem itens de produto desde 29/08/2026. Visita muito acima da loja e pedidos abaixo = “vitrine que não fecha” (preço, foto, descrição, frete); "
                   "o contrário (poucas visitas, taxa alta) = produto pouco exposto.")
    r0 = cat_sku.iloc[0]
    st.markdown("**Vitrine e cadastro**")
    nao_ok = []
    if cat_sku["fl_sem_seo"].fillna(False).any():
        nao_ok.append("sem SEO")
    if cat_sku["qt_imagens_sem_alt"].fillna(0).max() > 0:
        nao_ok.append(f"{int(cat_sku['qt_imagens_sem_alt'].max())} foto(s) sem texto alternativo")
    if cat_sku["qt_variacoes_sem_gtin_produto"].fillna(0).max() > 0:
        nao_ok.append(f"{int(cat_sku['qt_variacoes_sem_gtin_produto'].max())} variação(ões) sem GTIN")
    if cat_sku["fg_sem_peso"].fillna(False).any():
        nao_ok.append("sem peso")
    if cat_sku["fg_sem_custo"].fillna(False).any():
        nao_ok.append("variação sem custo")
    prat = r0["ds_prateleiras_home"] if pd.notna(r0["ds_prateleiras_home"]) and str(r0["ds_prateleiras_home"]).strip() else ""
    rank = cat_sku["nr_ranking_mais_vendidos"].dropna()
    render_cards([
        card("Exposição no site", str(r0["ds_nivel_exposicao"]) if pd.notna(r0["ds_nivel_exposicao"]) else "—", f"Prateleira da home: {prat}" if prat else "fora da home"),
        card("Sugerido em outras páginas", f"{int(cat_sku['qt_paginas_como_similar'].max() or 0)} + {int(cat_sku['qt_paginas_como_complementar'].max() or 0)}",
             "como similar + em “comprar com esse produto”"),
        card("Ranking “mais vendidos”", f"{int(rank.min())}º" if not rank.empty else "fora do ranking", "ranking da Nuvemshop"),
        card("Cadastro", "ok" if not nao_ok else f"{len(nao_ok)} pendência(s)", "; ".join(nao_ok) if nao_ok else "SEO, fotos, GTIN, peso e custo preenchidos",
             variant="ok" if not nao_ok else "warn"),
    ])


def _secao_preco_custo(d, cat_sku, rot_var):
    hist, troca = d["hist"], d["troca"]
    st.markdown("**Troca de custo seguida de troca de preço**")
    c1, c2 = st.columns([1.3, 1])
    piso = c1.slider("Variação mínima do custo (líquida)", 1, 30, 5, 1, format="%d%%", key="pd_piso",
                     help="O custo oscila no cadastro (sobe e volta). Conta a variação líquida entre o custo antes da 1ª troca e o custo no dia da troca de preço.") / 100
    detalhar = c2.toggle("Detalhar por variação", value=False, key="pd_detalhar")
    t = resumo_trocas(troca, piso, detalhar, rot_var)
    if t.empty:
        note(f"Nenhuma troca de preço antecedida por troca de custo de {piso * 100:.0f}% ou mais nos 60 dias anteriores. "
             "Troca de custo que não virou troca de preço não entra nesta análise.")
    else:
        com_base = t[(t["qt_unidades_preco_anterior"].fillna(0) + t["qt_unidades_preco_novo"].fillna(0)) >= 3]
        antes, depois = float(t["vl_unidades_dia_preco_anterior"].fillna(0).sum()), float(t["vl_unidades_dia_preco_novo"].fillna(0).sum())
        rep = t["vl_repasse"].replace([float("inf"), -float("inf")], pd.NA).dropna()
        render_cards([
            card("Trocas de preço analisadas", f"{len(t)}", "com troca de custo antes", ref=f"Custo variou {piso * 100:.0f}%+ nos 60 dias anteriores"),
            card("Repasse mediano", f"{rep.median():.2f}×" if not rep.empty else "—", "variação % do preço ÷ variação % do custo", ref="1,0 = repassou na mesma proporção"),
            card("Unidades por dia (soma das trocas)", f"{antes:.2f} → {depois:.2f}", "preço anterior → preço novo", ref=f"{len(com_base)} troca(s) com 3+ unidades vendidas na base"),
        ])
        cols = ["dt_troca_custo", "dt_troca_preco", "qt_dias_ate_reacao", "vl_custo_anterior", "vl_custo_novo", "pct_var_custo",
                "vl_preco_anterior", "vl_preco_novo", "pct_var_preco", "vl_repasse", "pct_margem_lista_antes", "pct_margem_lista_depois",
                "vl_unidades_dia_preco_anterior", "vl_unidades_dia_preco_novo", "qt_unidades_preco_anterior", "qt_unidades_preco_novo"]
        nomes = {"dt_troca_custo": "Troca de custo", "dt_troca_preco": "Troca de preço", "qt_dias_ate_reacao": "Dias até o preço",
                 "vl_custo_anterior": "Custo antes", "vl_custo_novo": "Custo depois", "pct_var_custo": "Custo %",
                 "vl_preco_anterior": "Preço antes", "vl_preco_novo": "Preço depois", "pct_var_preco": "Preço %", "vl_repasse": "Repasse",
                 "pct_margem_lista_antes": "Margem de lista antes", "pct_margem_lista_depois": "Margem de lista depois",
                 "vl_unidades_dia_preco_anterior": "Un/dia antes", "vl_unidades_dia_preco_novo": "Un/dia depois",
                 "qt_unidades_preco_anterior": "Unidades antes", "qt_unidades_preco_novo": "Unidades depois", "qt_var": "Variações", "Variação": "Variação"}
        mostrar = (["Variação"] if detalhar else ["qt_var"]) + cols
        tt = t[mostrar].rename(columns=nomes).copy()
        for c in ("Troca de custo", "Troca de preço"):
            tt[c] = pd.to_datetime(tt[c])
        st.dataframe(tt, hide_index=True, width="stretch", column_config={
            "Troca de custo": st.column_config.DateColumn(format="DD/MM/YYYY"), "Troca de preço": st.column_config.DateColumn(format="DD/MM/YYYY"),
            "Custo antes": st.column_config.NumberColumn(format="R$ %.2f"), "Custo depois": st.column_config.NumberColumn(format="R$ %.2f"),
            "Preço antes": st.column_config.NumberColumn(format="R$ %.2f"), "Preço depois": st.column_config.NumberColumn(format="R$ %.2f"),
            "Custo %": st.column_config.NumberColumn(format="percent"), "Preço %": st.column_config.NumberColumn(format="percent"),
            "Repasse": st.column_config.NumberColumn(format="%.2f×"),
            "Margem de lista antes": st.column_config.NumberColumn(format="percent"), "Margem de lista depois": st.column_config.NumberColumn(format="percent"),
            "Un/dia antes": st.column_config.NumberColumn(format="%.3f"), "Un/dia depois": st.column_config.NumberColumn(format="%.3f"),
            "Unidades antes": st.column_config.NumberColumn(format="%d"), "Unidades depois": st.column_config.NumberColumn(format="%d"),
            "Variações": st.column_config.NumberColumn(format="%d"),
        })
        note("<b>Como ler:</b> a troca de custo aparece quando o custo cadastrado mudou; a de preço, quando o preço de venda mudou depois (até 60 dias). "
             "<b>Repasse</b> = quanto do aumento de custo foi para o preço. <b>Margem de lista</b> = (preço − custo) ÷ preço, sem taxa nem embalagem. "
             "<b>Un/dia</b> = unidades vendidas por dia no período do preço (em família, a soma das variações). "
             "Com ~1 pedido por dia e pouca unidade por variação, a comparação antes × depois é <b>indicativa, não elasticidade</b> — cupom, vitrine, estoque e sazonalidade mudam junto. "
             "Reprecificações em lote (muitos SKUs no mesmo dia) aparecem como várias linhas na mesma data.")

    st.markdown("**Preço, custo e vendas em cada faixa de preço**")
    ordem = (d["vendas"].groupby("cd_produto_bling")["qt_unidades"].sum().reindex(list(rot_var)).fillna(0).sort_values(ascending=False).index.tolist())
    sel = st.selectbox("Variação", ordem, format_func=lambda s: rot_var.get(s, s), key="pd_var_preco") if len(ordem) > 1 else ordem[0]
    h = hist[hist["cd_produto_bling"] == sel].sort_values("dt_inicio").copy()
    if h.empty:
        st.caption("Sem histórico de preço para esta variação.")
        return
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fim_graf = pd.Timestamp(_hoje_brt())
    h["dt_fim_graf"] = h["dt_fim"].clip(upper=fim_graf)
    h["meio"] = h["dt_inicio"] + (h["dt_fim_graf"] - h["dt_inicio"]) / 2
    fig.add_bar(x=h["meio"], y=h["vl_unidades_por_dia"], name="Unidades por dia", marker_color=COLORS["primary_light"],
                width=((h["dt_fim_graf"] - h["dt_inicio"]).dt.days + 1) * 86400000 * 0.92, secondary_y=True,
                customdata=h[["qt_dias", "qt_unidades"]], hovertemplate="%{x|%d/%m/%Y}<br>%{y:.3f} un/dia · %{customdata[0]} dias · %{customdata[1]:.0f} un<extra></extra>")
    px = pd.concat([h[["dt_inicio", "vl_preco"]].rename(columns={"dt_inicio": "x"}), h[["dt_fim_graf", "vl_preco"]].rename(columns={"dt_fim_graf": "x"})]).sort_values("x")
    fig.add_scatter(x=px["x"], y=px["vl_preco"], name="Preço", mode="lines", line=dict(color=COR_PRECO, width=3, shape="hv"), secondary_y=False,
                    hovertemplate="%{x|%d/%m/%Y}<br>Preço R$ %{y:,.2f}<extra></extra>")
    cx = pd.concat([h[["dt_inicio", "vl_custo_inicio"]].rename(columns={"dt_inicio": "x", "vl_custo_inicio": "y"}),
                    h[["dt_fim_graf", "vl_custo_fim"]].rename(columns={"dt_fim_graf": "x", "vl_custo_fim": "y"})]).sort_values("x")
    fig.add_scatter(x=cx["x"], y=cx["y"], name="Custo", mode="lines", line=dict(color=COR_CUSTO, width=2, dash="dot", shape="hv"), secondary_y=False,
                    hovertemplate="%{x|%d/%m/%Y}<br>Custo R$ %{y:,.2f}<extra></extra>")
    plotly_layout(fig, height=330)
    fig.update_yaxes(title_text="R$", secondary_y=False, rangemode="tozero")
    fig.update_yaxes(title_text="Unidades por dia", secondary_y=True, rangemode="tozero", showgrid=False)
    st.plotly_chart(fig, width="stretch")
    tab = h.sort_values("dt_inicio", ascending=False)[["dt_inicio", "dt_fim", "qt_dias", "vl_preco", "vl_custo_inicio", "vl_custo_fim", "qt_unidades",
                                                          "vl_unidades_por_dia", "vl_preco_praticado_medio", "qt_dias_sem_estoque", "qt_dias_com_posicao"]].copy()
    tab["dt_fim"] = tab["dt_fim"].where(~h.sort_values("dt_inicio", ascending=False)["fg_vigente"].values, pd.NaT)
    tab = tab.rename(columns={"dt_inicio": "De", "dt_fim": "Até", "qt_dias": "Dias", "vl_preco": "Preço", "vl_custo_inicio": "Custo no início", "vl_custo_fim": "Custo no fim",
                              "qt_unidades": "Unidades", "vl_unidades_por_dia": "Un/dia", "vl_preco_praticado_medio": "Preço médio cobrado",
                              "qt_dias_sem_estoque": "Dias sem estoque", "qt_dias_com_posicao": "Dias com foto de estoque"})
    st.dataframe(tab, hide_index=True, width="stretch", column_config={
        "De": st.column_config.DateColumn(format="DD/MM/YYYY"), "Até": st.column_config.DateColumn(format="DD/MM/YYYY"),
        "Preço": st.column_config.NumberColumn(format="R$ %.2f"), "Custo no início": st.column_config.NumberColumn(format="R$ %.2f"),
        "Custo no fim": st.column_config.NumberColumn(format="R$ %.2f"), "Preço médio cobrado": st.column_config.NumberColumn(format="R$ %.2f"),
        "Un/dia": st.column_config.NumberColumn(format="%.3f"), "Unidades": st.column_config.NumberColumn(format="%d"),
    })
    note("Cada linha é um período com o mesmo preço de venda (a linha sem “Até” é o preço de hoje; o período vigente subestima levemente as unidades por dia porque o dia em andamento conta inteiro). "
         "O histórico começa em 17/07/2025 — o primeiro período pode ter começado antes. “Dias sem estoque” só existe desde 02/07/2026; sem estoque o produto não vende, então a taxa por dia desses períodos fica menor.")


def _secao_estoque(d, cat_sku, rot_var):
    r, pos, res = d["ruptura"].copy(), d["posicao"], cat_sku
    est = float(res["qt_estoque_bling"].clip(lower=0).sum())
    obs = float(res["qt_dias_observados"].fillna(0).sum())
    sem = float(res["qt_dias_sem_estoque"].fillna(0).sum())
    em_curso = int(r["fg_em_curso"].astype(bool).sum()) if not r.empty else 0
    base_ok = r[r["fg_base_suficiente"].astype(bool)] if not r.empty else r
    perdida = float(base_ok["qt_unidades_perdidas_estimadas"].sum()) if not base_ok.empty else 0.0
    sem_base = int((~r["fg_base_suficiente"].astype(bool)).sum()) if not r.empty else 0
    cobertura = est / (float(res["qt_pecas_90d"].sum()) / 90) if res["qt_pecas_90d"].sum() > 0 else None
    preco = _media_ponderada(res["vl_preco_atual"], res["qt_pecas_90d"])
    render_cards([
        card("Estoque atual", f"{est:.0f} un", f"cobre {cobertura:.0f} dias no ritmo de 90 dias" if cobertura is not None else "sem venda nos últimos 90 dias",
             ref=f"{res['qt_compra_pendente'].fillna(0).sum():.0f} un em compra pendente"),
        card("Tempo sem estoque", pct(sem / obs) if obs else "sem fotos", f"{sem:.0f} de {obs:.0f} dias-SKU observados" if obs else "kit/composição não tem foto diária de estoque",
             ref="Fotos diárias do estoque, desde 02/07/2026"),
        card("Rupturas", f"{len(r)}", f"{em_curso} em curso agora" if em_curso else "nenhuma em curso", ref=f"duração média {r['qt_dias_ruptura'].mean():.0f} dias" if not r.empty else ""),
        card("Venda perdida estimada", f"{perdida:.0f} un" if not base_ok.empty else "sem base", f"≈ {brl(perdida * preco, 0)} no preço de hoje" if not base_ok.empty and preco else f"{sem_base} ruptura(s) sem base de venda",
             ref="Só rupturas com 3+ unidades vendidas nos 90 dias anteriores"),
    ])
    if not pos.empty:
        tot = pos.groupby("dt_posicao")["qt_estoque_atual"].sum().clip(lower=0).reset_index()
        fig = go.Figure(go.Scatter(x=tot["dt_posicao"], y=tot["qt_estoque_atual"], mode="lines", fill="tozeroy", line=dict(color=METRIC_COLORS["receita"], width=2),
                                   hovertemplate="%{x|%d/%m/%Y}<br>%{y:.0f} un<extra></extra>", name="Estoque"))
        plotly_layout(fig, height=240)
        fig.update_yaxes(title_text="Unidades em estoque (soma das variações)", rangemode="tozero")
        st.plotly_chart(fig, width="stretch")
    if r.empty:
        note("Nenhuma ruptura registrada desde 02/07/2026 para este produto.")
        return
    r["Variação"] = r["cd_produto_bling"].map(rot_var)
    r["Fim"] = r["dt_fim"].where(~r["fg_em_curso"].astype(bool), pd.NaT)
    r["Leitura"] = r.apply(lambda x: ("começou antes da 1ª foto; " if x["fg_inicio_censurado"] else "") + ("em curso" if x["fg_em_curso"] else "encerrada"), axis=1)
    r["Perda estimada (un)"] = r["qt_unidades_perdidas_estimadas"]
    tab = r.sort_values("dt_inicio", ascending=False)[["Variação", "dt_inicio", "Fim", "qt_dias_ruptura", "vl_unidades_dia_antes", "qt_unidades_base", "Perda estimada (un)",
                                                       "qt_unidades_durante", "Leitura"]].rename(columns={
        "dt_inicio": "Início", "qt_dias_ruptura": "Dias sem estoque", "vl_unidades_dia_antes": "Un/dia antes", "qt_unidades_base": "Unidades na base (90d)",
        "qt_unidades_durante": "Vendeu durante"})
    st.dataframe(tab, hide_index=True, width="stretch", column_config={
        "Início": st.column_config.DateColumn(format="DD/MM/YYYY"), "Fim": st.column_config.DateColumn(format="DD/MM/YYYY"),
        "Un/dia antes": st.column_config.NumberColumn(format="%.3f"), "Perda estimada (un)": st.column_config.NumberColumn(format="%.1f"),
        "Dias sem estoque": st.column_config.NumberColumn(format="%d"), "Unidades na base (90d)": st.column_config.NumberColumn(format="%d"),
        "Vendeu durante": st.column_config.NumberColumn(format="%d"),
    })
    note("<b>Venda perdida estimada</b> = unidades por dia dos 90 dias antes da ruptura (sem os dias que já estavam sem estoque) × dias da ruptura. "
         "Só aparece quando a base tem 3+ unidades e 30+ dias; em produto que vende 0 ou 1 por mês a estimativa seria ruído, e fica “sem base”. "
         "É estimativa de <b>unidades</b>, não de receita (a conta em R$ usa o preço de hoje) e supõe que a demanda seria a mesma de antes — sem sazonalidade nem efeito da própria falta. "
         "“Vendeu durante” existe porque a Nuvemshop pode vender com o Bling zerado. O estoque do Bling só tem histórico desde 02/07/2026: rupturas que começam na primeira foto podem ter começado antes. "
         "Produto descontinuado ou em saída fica “em curso” de propósito — confira o ciclo de vida no topo.")


# ── página ───────────────────────────────────────────────────────────────────

def render():
    inject_css()
    st.html("""
    <div class="report-header"><div>
      <div class="report-brand">shibari brasil · catálogo</div>
      <div class="report-title">Desempenho do <span>Produto</span></div>
      <div class="report-meta">Vendas, preço, custo e margem · perfil de compra · reação do cliente às trocas de preço · estoque</div>
    </div></div>""")
    try:
        cat = carregar_catalogo()
        fr = carregar_frescor(TABELAS, EXTRATORES, com_ga4=True)
    except Exception as e:  # noqa: BLE001
        st.error(f"Não consegui carregar o catálogo: {e}")
        return
    alerta_atraso(fr)

    # filtros em cascata: Categoria → Subcategoria → Produto (família)
    c1, c2, c3 = st.columns([1, 1, 2])
    cat_sel = c1.selectbox("Categoria", ["Todas"] + sorted(cat["ds_categoria"].dropna().unique()))
    base = cat if cat_sel == "Todas" else cat[cat["ds_categoria"] == cat_sel]
    sub_sel = c2.selectbox("Subcategoria", ["Todas"] + sorted(base["ds_subcategoria"].dropna().unique()))
    base = base if sub_sel == "Todas" else base[base["ds_subcategoria"] == sub_sel]
    fam = (base.groupby("cd_familia").agg(nm=("nm_produto", "first"), rec=("vl_receita_liquida_90d", "sum")).reset_index()
           .sort_values(["rec", "nm"], ascending=[False, True]))
    if fam.empty:
        st.info("Nenhum produto neste filtro.")
        return
    rotulos = dict(zip(fam["cd_familia"], fam["nm"]))
    dup = fam["nm"].duplicated(keep=False)
    rotulos = {k: (f"{v} ({k[-5:]})" if d else v) for (k, v), d in zip(rotulos.items(), dup)}
    cd_fam = c3.selectbox("Produto", list(rotulos), format_func=rotulos.get, help="Ordenado pela receita dos últimos 90 dias.")

    cat_sku = cat[cat["cd_familia"] == cd_fam].copy()
    skus = tuple(sorted(cat_sku["cd_produto_bling"]))
    rot_var = dict(zip(cat_sku["cd_produto_bling"], cat_sku["rotulo_variacao"]))
    nm_familia = str(cat_sku["nm_produto"].iloc[0])
    hoje = _hoje_brt()

    with st.spinner("Carregando o produto…"):
        d = carregar_produto(skus, cd_fam)

    _cabecalho_produto(nm_familia, cat_sku)
    if d["vendas"].empty:
        note("Este produto ainda não tem venda registrada: as seções de vendas e perfil de compra ficam vazias.", variant="warn")

    w = janelas(d["vendas"], hoje)
    section_title("1. Números do produto")
    _numeros(cat_sku, w, d["vendas"])

    section_title("2. Evolução das vendas")
    _grafico_evolucao(d["vendas"], d["hist"], rot_var)

    section_title("3. Variações")
    _tabela_variacoes(cat_sku, d["vendas"], hoje)

    section_title("4. Perfil de compra")
    _secao_perfil(d, nm_familia)
    _secao_acao(d, cat_sku)

    section_title("5. Origem, interesse e vitrine")
    _secao_origem_interesse(d, cat_sku, hoje)

    section_title("6. Preço e custo")
    _secao_preco_custo(d, cat_sku, rot_var)

    section_title("7. Estoque")
    _secao_estoque(d, cat_sku, rot_var)

    detalhe_atualizacao(fr)
