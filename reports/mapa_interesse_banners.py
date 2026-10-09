"""Mapa de Interesse — banners da home (carrossel e quadrados de categoria).

Mesma lógica do mapa de produtos aplicada às artes da home: ONDE a arte está (ordem dos blocos, posição no carrossel ou
nos quadrados, capturados do HTML público) × QUANTO o cliente se interessa (clique, carrinho e pedido na MESMA sessão
depois do clique, do evento `select_promotion` da tag GTM v32). Definição, pesos e limites: specs/mapa-de-interesse.md (§ Banners).

Regra de negócio: cliques/carrinhos/pedidos vêm da `tb_ga4_promocao_dia` e as sessões da home da `tb_ga4_home_sessao_dia`
(dbt); a margem vem da `tb_pedido` (soma da az). Aqui só se calcula o SCORE (ranking de apresentação, mesma exceção da spec:
GA4 está na região US e a az em us-east4, então o cruzamento é no pandas) e a taxa de carrinho suavizada.
Os pesos, a escala de faixas e os quadrantes são os MESMOS do mapa de produtos (importados de `reports.mapa_interesse`).
"""
import html
import re

import numpy as np
import pandas as pd
import streamlit as st

from common import bigquery as bq
from common.design import COLORS, card, note, render_cards, section_title
from reports.mapa_interesse import (CORTE_INTERESSE, FAIXAS, JANELAS, K_SUAVIZACAO, MIN_VISITAS, PESO_CARRINHO, PESO_PEDIDO,
                                    PESO_VISITA, _faixa)
from reports.vendas_margem import _hoje_brt

INICIO_BANNERS = pd.Timestamp("2026-10-10").date()   # 1º dia COMPLETO com a tag v32 (publicada em 09/10/2026, no meio do dia)
LARGURA_CARTAO = 176                                  # px; o cartão de produto do mapa tem 138
LOCAIS = {"slider": ("home_hero", "hero", "Carrossel da home"), "categories": ("home_categorias", "cat", "Quadrados de categoria")}
COLUNAS_METRICA = ["qt_cliques", "qt_sessoes_clique", "qt_sessoes_carrinho_pos", "qt_sessoes_checkout_pos", "qt_pedidos_pos", "vl_receita_ga4_pos"]


@st.cache_data(ttl=900)
def carregar_banners(dias):
    """Layout atual (última captura), data de entrada de cada arte, cliques/funil, sessões da home e margem dos pedidos.
    Devolve None quando a captura dos banners ainda não existe no BigQuery (extrator novo não implantado)."""
    client = bq.get_client()
    hoje = pd.Timestamp(_hoje_brt())
    fim = (hoje - pd.Timedelta(days=1)).date()
    ini = max((hoje - pd.Timedelta(days=dias)).date(), INICIO_BANNERS)
    try:
        blocos = bq.query_df(client, f"""
            SELECT nr_ordem_bloco, ds_bloco, nm_bloco, ds_dispositivo, nr_posicao, ds_destino, cd_arte, lk_arte, nm_rotulo, ts_captura
              FROM `{bq.PROJECT}.dbt_dw_stg.stg_nuvemshop_vitrine_bloco`
             WHERE ts_captura = (SELECT MAX(ts_captura) FROM `{bq.PROJECT}.dbt_dw_stg.stg_nuvemshop_vitrine_bloco`)""")
        entrada = bq.query_df(client, f"""
            SELECT cd_arte, DATE(MIN(ts_captura), 'America/Sao_Paulo') AS dt_entrada
              FROM `{bq.PROJECT}.dbt_dw_stg.stg_nuvemshop_vitrine_bloco`
             WHERE cd_arte IS NOT NULL GROUP BY 1""")
    except Exception:       # noqa: BLE001 — tabela ainda não existe: a página mostra o aviso em vez de quebrar
        return None
    promo = bq.query_df(client, f"""
        SELECT dt_data, ds_dispositivo, cd_local, cd_slot, cd_arte, ds_destino, qt_cliques, qt_sessoes_clique, qt_sessoes_carrinho_pos,
               qt_sessoes_checkout_pos, qt_pedidos_pos, vl_receita_ga4_pos, ar_transacoes
          FROM `{bq.PROJECT}.dbt_dw_us_az.tb_ga4_promocao_dia`
         WHERE dt_data BETWEEN DATE '{ini}' AND DATE '{fim}'""")
    sessoes = bq.query_df(client, f"""
        SELECT dt_data, ds_dispositivo, qt_sessoes_home
          FROM `{bq.PROJECT}.dbt_dw_us_az.tb_ga4_home_sessao_dia`
         WHERE dt_data BETWEEN DATE '{ini}' AND DATE '{fim}'""")
    ids = sorted({t for lista in promo["ar_transacoes"] for t in (lista if lista is not None else []) if re.fullmatch(r"\d+", str(t))})
    if ids:
        lista_sql = ", ".join(f"'{i}'" for i in ids)
        margens = bq.query_df(client, f"""
            SELECT CAST(cd_pedido_loja AS STRING) AS cd_pedido_loja, SUM(vl_margem_contribuicao) AS vl_margem, SUM(vl_receita_liquida_produto) AS vl_receita
              FROM `{bq.PROJECT}.dbt_dw_az.tb_pedido`
             WHERE fg_pedido_valido AND NOT COALESCE(fg_brinde, FALSE) AND CAST(cd_pedido_loja AS STRING) IN ({lista_sql})
             GROUP BY 1""")
    else:
        margens = pd.DataFrame({"cd_pedido_loja": [], "vl_margem": [], "vl_receita": []})
    return {"blocos": blocos, "entrada": entrada, "promo": promo, "sessoes": sessoes, "margens": margens, "ini": ini, "fim": fim}


def layout_banners(blocos):
    """Um registro por banner da vitrine atual (slot = hero_N no carrossel, cat_N nos quadrados), na ordem do site.
    No carrossel junta a arte desktop e a mobile da mesma posição; nos quadrados a arte é a mesma nos dois."""
    linhas = []
    for chave, (cd_local, prefixo, nome_local) in LOCAIS.items():
        b = blocos[blocos["ds_bloco"] == chave]
        for pos, g in b.groupby("nr_posicao"):
            mobile = g[g["ds_dispositivo"].isin(["mobile", "todos"])].iloc[0] if g["ds_dispositivo"].isin(["mobile", "todos"]).any() else g.iloc[0]
            desktop = g[g["ds_dispositivo"].isin(["desktop", "todos"])].iloc[0] if g["ds_dispositivo"].isin(["desktop", "todos"]).any() else g.iloc[0]
            linhas.append({
                "nr_ordem_bloco": int(g["nr_ordem_bloco"].iloc[0]), "ds_bloco": chave, "cd_local": cd_local, "nome_local": nome_local,
                "nr_posicao": int(pos), "cd_slot": f"{prefixo}_{int(pos)}", "ds_destino": mobile["ds_destino"],
                "nm_rotulo": mobile["nm_rotulo"], "lk_arte": mobile["lk_arte"],
                "cd_arte_desktop": desktop["cd_arte"], "cd_arte_mobile": mobile["cd_arte"]})
    return pd.DataFrame(linhas)


def _destino_curto(url):
    if not isinstance(url, str):
        return ""
    caminho = re.sub(r"^https?://[^/]+", "", url).strip("/")
    return caminho or "home"


def nome_banner(rotulo, destino):
    """Nome legível: o rótulo do tema ("Carrossel 1", "Categoria 2") não diz nada; nesse caso vale o caminho do destino."""
    if isinstance(rotulo, str) and rotulo.strip() and not re.fullmatch(r"(Carrossel|Categoria|Slide|Banner)\s*\d*", rotulo.strip(), re.I):
        return rotulo.strip()
    return _destino_curto(destino)


def _pct(v, casas=1):
    return f"{v * 100:.{casas}f}%".replace(".", ",") if pd.notna(v) else "-"


def calcular_banners(lay, promo, sessoes, entrada, margens, ini, fim):
    """Uma linha por banner do layout atual, com cliques, CTR, funil, margem, pontos, score, faixa e quadrante.
    A janela de cada banner começa no maior entre o início da janela, o 1º dia completo da tag v32 e a entrada da arte
    (1ª captura), no numerador e no denominador, para o CTR não misturar dias em que a arte nem estava no ar."""
    if lay.empty:
        return lay.copy(), 0.0
    promo = promo.copy()
    sessoes = sessoes.copy()
    promo["dt_data"] = pd.to_datetime(promo["dt_data"]).dt.date
    sessoes["dt_data"] = pd.to_datetime(sessoes["dt_data"]).dt.date
    entradas = {r.cd_arte: pd.Timestamp(r.dt_entrada).date() for r in entrada.itertuples()} if len(entrada) else {}
    mg = margens.set_index("cd_pedido_loja") if len(margens) else pd.DataFrame(columns=["vl_margem", "vl_receita"])
    saida = []
    for r in lay.to_dict("records"):
        artes = {a for a in (r["cd_arte_desktop"], r["cd_arte_mobile"]) if isinstance(a, str)}
        dt_ini = max([ini, INICIO_BANNERS] + [entradas[a] for a in artes if a in entradas])
        mask_local = (promo["cd_local"] == r["cd_local"]) & (promo["dt_data"] >= dt_ini)
        meu = promo[mask_local & promo["cd_arte"].isin(artes)]
        outras = promo[mask_local & (promo["cd_slot"] == r["cd_slot"]) & ~promo["cd_arte"].isin(artes)]
        trans = {str(t) for lista in meu["ar_transacoes"] for t in (lista if lista is not None else [])}
        sess_home = sessoes[sessoes["dt_data"] >= dt_ini]["qt_sessoes_home"].sum()
        linha = {**r, "dt_inicio": dt_ini, "dias_no_ar": max((fim - dt_ini).days + 1, 0), "sessoes_home": float(sess_home),
                 "qt_cliques_outras_artes": float(outras["qt_sessoes_clique"].sum()),
                 "vl_margem": float(mg.loc[mg.index.intersection(list(trans)), "vl_margem"].sum()) if len(mg) else 0.0}
        for c in COLUNAS_METRICA:
            linha[c] = float(pd.to_numeric(meu[c]).sum())
        saida.append(linha)
    d = pd.DataFrame(saida)
    d["ctr"] = np.where(d["sessoes_home"] > 0, d["qt_sessoes_clique"] / d["sessoes_home"].where(d["sessoes_home"] > 0), np.nan)
    d["pontos"] = d["qt_sessoes_clique"] * PESO_VISITA + d["qt_sessoes_carrinho_pos"] * PESO_CARRINHO + d["qt_pedidos_pos"] * PESO_PEDIDO
    d["score"] = np.where(d["pontos"] > 0, d["pontos"].rank(pct=True, method="average") * 100, 0.0)
    d["faixa"] = d["score"].map(_faixa)
    p0 = d["qt_sessoes_carrinho_pos"].sum() / max(d["qt_sessoes_clique"].sum(), 1)
    d["taxa_carrinho"] = (d["qt_sessoes_carrinho_pos"] + K_SUAVIZACAO * p0) / (d["qt_sessoes_clique"] + K_SUAVIZACAO)
    d["quadrante"] = [_quadrante(x, p0) for x in d.itertuples()]
    return d, float(p0)


def _quadrante(r, p0):
    if r.qt_sessoes_clique < MIN_VISITAS:
        return "Poucos dados"
    alto, conv = r.score >= CORTE_INTERESSE, r.taxa_carrinho >= p0
    if alto and conv:
        return "Estrela"
    if alto:
        return "Vitrine que não fecha"
    return "Joia escondida" if conv else "Cão"


def cartao_banner(r):
    """Cartão da arte na home montada: um pouco maior que o de produto (176 × arte inteira), com a cor da faixa de interesse."""
    _, _, bg, fg = next(f for f in FAIXAS if f[0] == r["faixa"])
    quadrado = r["ds_bloco"] == "categories"
    altura = LARGURA_CARTAO if quadrado else round(LARGURA_CARTAO * 1.2)
    url = r.get("lk_arte")
    img = (f'<img src="{html.escape(url)}" style="width:100%;height:{altura}px;object-fit:cover;border-radius:6px 6px 0 0" '
           f'onerror="this.style.display=\'none\'">') if isinstance(url, str) and url else f'<div style="height:{altura}px;background:#F1F5F9"></div>'
    ctr = _pct(r["ctr"])
    nome = html.escape(nome_banner(r.get("nm_rotulo"), r["ds_destino"]))[:44]
    outras = (f'<div style="margin-top:3px;font-size:9px;color:#B45309">+{int(r["qt_cliques_outras_artes"])} cliques de outras artes neste slot</div>'
              if r.get("qt_cliques_outras_artes", 0) > 0 else "")
    return f"""<div style="flex:0 0 {LARGURA_CARTAO}px;background:#fff;border:1px solid {COLORS['border']};border-radius:8px;overflow:hidden;position:relative">
      <div style="position:absolute;top:4px;left:4px;background:rgba(255,255,255,.92);border-radius:4px;padding:0 5px;font-size:10px;font-weight:700">{int(r['nr_posicao'])}º</div>
      {img}
      <div style="background:{bg};color:{fg};padding:4px 8px;font-size:11px;font-weight:700;display:flex;justify-content:space-between"><span>{r['faixa']}</span><span>{r['score']:.0f}</span></div>
      <div style="padding:6px 8px;font-size:11px;line-height:1.25;height:30px;overflow:hidden">{nome}</div>
      <div style="padding:0 8px 8px;font-size:10px;color:{COLORS['text_secondary']}">cliques {int(r['qt_sessoes_clique'])} · CTR {ctr}<br>carrinhos {int(r['qt_sessoes_carrinho_pos'])} · pedidos {int(r['qt_pedidos_pos'])}{outras}</div>
    </div>"""


def linha_de_banners(d_bloco):
    """Linha horizontal de cartões de um bloco de banners, na ordem de posição."""
    return '<div style="display:flex;gap:8px;overflow-x:auto;padding-bottom:6px">' + "".join(
        cartao_banner(r) for r in d_bloco.sort_values("nr_posicao").to_dict("records")) + "</div>"


def secao_banners(d, p0, dados, dias):
    """Seção 6: indicadores, veredito por slot, quadrantes e efeito da posição dos banners."""
    ini, fim = dados["ini"], dados["fim"]
    n_dias = (fim - ini).days + 1
    section_title("6 · Banners e quadrados de categoria")
    cliques, carrinhos, pedidos = d["qt_sessoes_clique"].sum(), d["qt_sessoes_carrinho_pos"].sum(), d["qt_pedidos_pos"].sum()
    home_total = float(dados["sessoes"]["qt_sessoes_home"].sum())
    render_cards([
        card("Janela dos banners", f"{n_dias} dia(s)" if n_dias > 0 else "sem dia fechado", sub=f"{ini:%d/%m} a {fim:%d/%m}", ref="tag de clique no ar desde 09/10"),
        card("Sessões na home", f"{int(home_total)}", sub="denominador do CTR"),
        card("Sessões que clicaram em banner", f"{int(cliques)}", sub=f"CTR geral {_pct(cliques / home_total)}" if home_total else "", ref="uma sessão pode clicar em mais de um"),
        card("Carrinhos depois do clique", f"{int(carrinhos)}", sub="mesma sessão"),
        card("Pedidos depois do clique", f"{int(pedidos)}", sub="visto pelo GA4 (piso)", variant="ok" if pedidos else "neutral"),
    ])
    st.markdown("**Veredito por banner** (na ordem em que aparecem no site)")
    t = d.sort_values(["nr_ordem_bloco", "nr_posicao"])
    tabela = pd.DataFrame({
        "Local": t["nome_local"], "Pos.": t["nr_posicao"], "Destino": t["ds_destino"].map(_destino_curto),
        "Faixa": t["faixa"], "Score": t["score"], f"Cliques ({n_dias}d)": t["qt_sessoes_clique"],
        "% dos cliques de banner": t["qt_sessoes_clique"] / max(cliques, 1), "CTR": t["ctr"],
        "Carrinhos": t["qt_sessoes_carrinho_pos"], "Checkouts": t["qt_sessoes_checkout_pos"], "Pedidos": t["qt_pedidos_pos"],
        "Receita GA4 (R$)": t["vl_receita_ga4_pos"], "Margem (R$)": t["vl_margem"], "Dias no ar": t["dias_no_ar"],
        "Cliques de outras artes no slot": t["qt_cliques_outras_artes"]})
    st.dataframe(tabela, hide_index=True, use_container_width=True, column_config={
        "Score": st.column_config.ProgressColumn(format="%.0f", min_value=0, max_value=100),
        "% dos cliques de banner": st.column_config.NumberColumn(format="percent"), "CTR": st.column_config.NumberColumn(format="percent"),
        "Receita GA4 (R$)": st.column_config.NumberColumn(format="R$ %.2f"), "Margem (R$)": st.column_config.NumberColumn(format="R$ %.2f")})
    note("<b>Clique</b> = sessão que clicou na arte; <b>carrinho/checkout/pedido</b> = o que a MESMA sessão fez depois do clique (o GA4 não liga o pedido "
         "a um só banner: sessão com dois cliques conta nos dois). <b>CTR</b> = sessões que clicaram ÷ sessões que viram a home desde que a arte entrou no ar; "
         "os quadrados ficam abaixo da dobra, então o CTR deles subestima quem de fato os viu. <b>Receita GA4</b> é o que o GA4 enxergou (piso), a <b>margem</b> é a de "
         "contribuição da tb_pedido dos mesmos pedidos (antes de mídia). Score, faixas e quadrantes usam os mesmos pesos do mapa de produtos "
         f"(clique {PESO_VISITA}, carrinho {PESO_CARRINHO}, pedido {PESO_PEDIDO}) e a escala é relativa <i>aos banners da home</i> (no máximo 10 posições: leia faixa, não posição exata do score).")
    st.markdown("**Quadrantes: interesse × conversão em carrinho**")
    q = d[d["quadrante"] != "Poucos dados"]
    if q.empty:
        st.info(f"Nenhum banner com {MIN_VISITAS}+ cliques na janela ainda (tráfego pequeno: ~40 sessões por dia na home). A leitura começa a fazer sentido com 2 a 4 semanas.")
    else:
        ordem_q = ["Estrela", "Vitrine que não fecha", "Joia escondida", "Cão"]
        cont = q.groupby("quadrante").size().reindex(ordem_q, fill_value=0)
        st.html('<div class="cards">' + "".join(card(n, str(int(v))) for n, v in cont.items()) + "</div>")
        st.dataframe(pd.DataFrame({"Banner": [nome_banner(a, b) for a, b in zip(q["nm_rotulo"], q["ds_destino"])], "Quadrante": q["quadrante"], "Score": q["score"],
                                   "Cliques": q["qt_sessoes_clique"], "Carrinhos": q["qt_sessoes_carrinho_pos"],
                                   "Taxa de carrinho": q["taxa_carrinho"], "Taxa vs média": q["taxa_carrinho"] / p0 if p0 else np.nan}).sort_values("Score", ascending=False),
                     hide_index=True, use_container_width=True, column_config={
                         "Score": st.column_config.ProgressColumn(format="%.0f", min_value=0, max_value=100),
                         "Taxa de carrinho": st.column_config.NumberColumn(format="percent"), "Taxa vs média": st.column_config.NumberColumn(format="%.1fx")})
    note(f"Só entram banners com {MIN_VISITAS}+ cliques. Mesma regra dos produtos: <b>Estrela</b> = score ≥ {CORTE_INTERESSE} e taxa de carrinho acima da média dos banners; "
         "<b>Vitrine que não fecha</b> = muito clique e pouco carrinho (a página de destino não sustenta a promessa da arte); <b>Joia escondida</b> = pouco clique e taxa alta "
         f"(candidata a subir de posição); <b>Cão</b> = pouco clique e taxa baixa. Taxa suavizada com {K_SUAVIZACAO} cliques de prior.")
    st.markdown("**Efeito da posição e do rodízio**")
    p = dados["promo"].copy()
    p["dt_data"] = pd.to_datetime(p["dt_data"]).dt.date
    p = p[p["dt_data"] >= max(ini, INICIO_BANNERS)]
    if p.empty:
        st.info("Ainda sem cliques de banner na janela.")
    else:
        rot = {}
        for r in d.itertuples():
            for a in (r.cd_arte_desktop, r.cd_arte_mobile):
                if isinstance(a, str):
                    rot[a] = nome_banner(r.nm_rotulo, r.ds_destino)
        g = p.groupby(["cd_local", "cd_slot", "cd_arte"]).agg(cliques=("qt_sessoes_clique", "sum")).reset_index()
        g["Banner"] = g["cd_arte"].map(rot).fillna("(arte que saiu do ar)")
        piv = g.pivot_table(index="Banner", columns="cd_slot", values="cliques", aggfunc="sum", fill_value=0)
        ordem_cols = sorted(piv.columns, key=lambda c: (0 if c.startswith("hero") else 1, int(c.split("_")[1])))
        st.dataframe(piv[ordem_cols].reset_index(), hide_index=True, use_container_width=True)
    note("Cliques por banner (linha) em cada slot (coluna). O carrossel gira sozinho: o slide 1 aparece primeiro e leva vantagem, por isso o teste limpo é <b>trocar a ordem "
         "no meio da janela</b> e ver se o clique acompanha a arte (interesse real) ou a posição (viés). Banner em vários slots = a ordem já foi girada. "
         "Os dados começam em 10/10/2026 (1º dia completo da tag); antes disso não existe clique por banner.")
