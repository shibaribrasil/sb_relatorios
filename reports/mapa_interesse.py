"""Relatório Mapa de Interesse — Shibari Brasil (camada semanal).

Cruza ONDE o produto está no site (prateleira e posição da home, capturadas do HTML público) com QUANTO o cliente se
interessa por ele (visita à página, clique vindo da home, carrinho, compra) para mostrar onde a escolha da vitrine
acerta e onde erra. Definição de cada indicador, pesos e limites: specs/mapa-de-interesse.md.

Regra de negócio: exposição (`tb_produto_vitrine`), vendas e margem (`tb_pedido`) e eventos do GA4 por produto
(`tb_ga4_produto_dia`) vêm prontos do dbt. O SCORE de interesse é calculado aqui — exceção assumida e documentada na
spec: a az (us-east4) e o GA4 (US) estão em regiões diferentes e não se juntam em SQL, então o cruzamento é no pandas.
O score é um ranking de apresentação (não muda nenhum número financeiro); a margem mostrada vem somada da `tb_pedido`.
"""
import html
import re

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from common import bigquery as bq
from common.design import COLORS, inject_css, card, render_cards, section_title, note, plotly_layout, brl
from common.frescor import carregar_frescor, badge_atualizacao, detalhe_atualizacao, alerta_atraso
from reports.vendas_margem import _hoje_brt

TABELAS = ("tb_produto_vitrine", "tb_produto_gestao", "tb_pedido")
EXTRATORES = ("nuvemshop_storefront", "nuvemshop_orders", "bling_products")

INICIO_ITENS_GA4 = pd.Timestamp("2026-08-29").date()   # primeiro dia com eventos de e-commerce por produto
JANELAS = {"Últimos 14 dias": 14, "Últimos 30 dias": 30}

# Pesos do score (pontos por evento): quanto mais raro o evento no funil, mais vale. Visita 1, carrinho 5, pedido 20.
PESO_VISITA, PESO_CARRINHO, PESO_PEDIDO = 1, 5, 20
K_SUAVIZACAO = 20        # "visitas de prior" da taxa de carrinho suavizada (evita 1 carrinho em 2 visitas = 50%)
MIN_VISITAS = 10         # abaixo disso o produto não entra nos quadrantes (amostra pequena não é tendência)
CORTE_INTERESSE = 60     # score a partir do qual o interesse é considerado alto

# escala do score: faixa, limite inferior, cor de fundo, cor do texto (uma única matiz azul: mais escuro = mais interesse)
FAIXAS = [
    ("Estrela", 85, "#1B3558", "#FFFFFF"),
    ("Quente", 60, "#4677B0", "#FFFFFF"),
    ("Morno", 30, "#8AAED9", "#1E293B"),
    ("Frio", 0.01, "#D0E0F4", "#1E293B"),
    ("Sem sinal", 0, "#E2E8F0", "#94A3B8"),
]
ORDEM_FAIXA = [f[0] for f in FAIXAS][::-1]


@st.cache_data(ttl=900)
def carregar_dados(dias):
    client = bq.get_client()
    az = f"`{bq.PROJECT}.dbt_dw_az"
    hoje = pd.Timestamp(_hoje_brt())
    fim = hoje - pd.Timedelta(days=1)                       # dia em andamento não entra
    ini = max(fim - pd.Timedelta(days=dias - 1), pd.Timestamp(INICIO_ITENS_GA4))
    ini, fim = pd.Timestamp(ini).date(), pd.Timestamp(fim).date()

    home = bq.query_df(client, f"""
        SELECT nm_bloco, nr_ordem_bloco, nr_posicao, cd_produto_nuvemshop, ts_captura
          FROM `{bq.PROJECT}.dbt_dw_stg.stg_nuvemshop_vitrine`
         WHERE ds_tipo = 'home'
           AND ts_captura = (SELECT MAX(ts_captura) FROM `{bq.PROJECT}.dbt_dw_stg.stg_nuvemshop_vitrine` WHERE ds_tipo = 'home')
    """)
    prod = bq.query_df(client, f"""
        SELECT v.cd_produto_nuvemshop, v.nm_produto, v.nr_nivel_exposicao, v.ds_nivel_exposicao, v.fl_na_home,
               v.qt_dias_na_home_30d, v.dt_entrada_home, v.fl_publicado,
               g.lk_imagem_produto, g.ds_frente, g.ds_ciclo, g.tem_estoque, g.qt_estoque_bling
          FROM {az}.tb_produto_vitrine` AS v
          LEFT JOIN (
                SELECT cd_produto_nuvemshop,
                       ANY_VALUE(lk_imagem_produto) AS lk_imagem_produto,
                       ANY_VALUE(ds_frente) AS ds_frente,
                       ANY_VALUE(ds_ciclo) AS ds_ciclo,
                       LOGICAL_OR(fg_tem_estoque) AS tem_estoque,
                       SUM(GREATEST(COALESCE(qt_estoque_bling, 0), 0)) AS qt_estoque_bling
                  FROM {az}.tb_produto_gestao`
                 GROUP BY 1) AS g USING (cd_produto_nuvemshop)
    """)
    vendas = bq.query_df(client, f"""
        SELECT p.cd_produto_nuvemshop,
               COUNT(DISTINCT t.cd_codigo_interno) AS qt_pedidos,
               SUM(t.qt_item) AS qt_unidades,
               SUM(t.vl_receita_liquida_produto) AS vl_receita,
               SUM(t.vl_margem_contribuicao) AS vl_margem
          FROM {az}.tb_pedido` AS t
          JOIN (SELECT DISTINCT cd_produto_bling, cd_produto_nuvemshop FROM {az}.tb_produto`
                 WHERE cd_produto_nuvemshop IS NOT NULL) AS p USING (cd_produto_bling)
         WHERE t.fg_pedido_valido AND NOT COALESCE(t.fg_brinde, FALSE)
           AND DATE(t.dt_pedido) BETWEEN DATE '{ini}' AND DATE '{fim}'
         GROUP BY 1
    """)
    variacao = bq.query_df(client, f"""
        SELECT CAST(cd_variacao_nuvemshop AS STRING) AS cd_item, cd_produto_nuvemshop
          FROM `{bq.PROJECT}.dbt_dw_stg.stg_nuvemshop_variacao_produto`
    """)
    ga4 = bq.query_df(client, f"""
        SELECT cd_item_ga4, nm_evento, ds_lista, SUM(qt_sessoes) AS qt_sessoes, SUM(qt_eventos) AS qt_eventos
          FROM `{bq.PROJECT}.dbt_dw_us_az.tb_ga4_produto_dia`
         WHERE dt_data BETWEEN DATE '{ini}' AND DATE '{fim}'
         GROUP BY 1, 2, 3
    """)
    for c in ["nr_nivel_exposicao", "qt_dias_na_home_30d", "qt_estoque_bling"]:
        prod[c] = pd.to_numeric(prod[c], errors="coerce").fillna(0)
    prod["fl_na_home"] = prod["fl_na_home"].fillna(False).astype(bool)
    prod["fl_publicado"] = prod["fl_publicado"].fillna(False).astype(bool)
    prod["tem_estoque"] = prod["tem_estoque"].fillna(False).astype(bool)
    for c in ["qt_pedidos", "qt_unidades", "vl_receita", "vl_margem"]:
        vendas[c] = pd.to_numeric(vendas[c]).fillna(0.0)
    for c in ["qt_sessoes", "qt_eventos"]:
        ga4[c] = pd.to_numeric(ga4[c]).fillna(0)
    for d in (home, prod, vendas, variacao):
        d["cd_produto_nuvemshop"] = d["cd_produto_nuvemshop"].astype("Int64")
    return {"home": home, "prod": prod, "vendas": vendas, "variacao": variacao, "ga4": ga4, "ini": ini, "fim": fim}


def eventos_por_produto(ga4, variacao, produtos_validos):
    """Resolve o item_id do GA4 para o produto Nuvemshop (variação → produto; o select_item da home já manda o produto)
    e devolve, por produto: visitas, carrinhos, cliques na home e cliques em categoria/busca (sessões distintas por
    variação somadas). Também devolve a fração de eventos que não casou com nenhum produto."""
    v2p = dict(zip(variacao["cd_item"], variacao["cd_produto_nuvemshop"]))
    validos = {str(int(p)) for p in produtos_validos}
    ga = ga4.copy()
    ga["cd_produto_nuvemshop"] = [v2p.get(i) if i in v2p else (int(i) if i in validos else None) for i in ga["cd_item_ga4"].astype(str)]
    relevantes = ga[ga["nm_evento"].isin(["view_item", "add_to_cart", "select_item"])]
    sem_casar = relevantes.loc[relevantes["cd_produto_nuvemshop"].isna(), "qt_eventos"].sum() / max(relevantes["qt_eventos"].sum(), 1)
    ga = ga.dropna(subset=["cd_produto_nuvemshop"])
    ga["cd_produto_nuvemshop"] = ga["cd_produto_nuvemshop"].astype("Int64")
    chave = np.select(
        [ga["nm_evento"] == "view_item", ga["nm_evento"] == "add_to_cart",
         (ga["nm_evento"] == "select_item") & (ga["ds_lista"] == "Home"), ga["nm_evento"] == "select_item"],
        ["qt_visitas", "qt_carrinhos", "qt_cliques_home", "qt_cliques_outras"], default="")
    ga["metrica"] = chave
    ga = ga[ga["metrica"] != ""]
    out = ga.pivot_table(index="cd_produto_nuvemshop", columns="metrica", values="qt_sessoes", aggfunc="sum", fill_value=0).reset_index()
    for c in ["qt_visitas", "qt_carrinhos", "qt_cliques_home", "qt_cliques_outras"]:
        if c not in out:
            out[c] = 0
    return out, float(sem_casar)


def calcular_interesse(prod, eventos, vendas):
    """Uma linha por produto publicado com pontos, score 0–100, faixa e quadrante. Ver spec (fórmulas e limites)."""
    d = prod[prod["fl_publicado"]].merge(eventos, on="cd_produto_nuvemshop", how="left").merge(vendas, on="cd_produto_nuvemshop", how="left")
    for c in ["qt_visitas", "qt_carrinhos", "qt_cliques_home", "qt_cliques_outras", "qt_pedidos", "qt_unidades", "vl_receita", "vl_margem"]:
        d[c] = pd.to_numeric(d[c]).fillna(0)
    d["pontos"] = d["qt_visitas"] * PESO_VISITA + d["qt_carrinhos"] * PESO_CARRINHO + d["qt_pedidos"] * PESO_PEDIDO
    d["score"] = np.where(d["pontos"] > 0, d["pontos"].rank(pct=True, method="average") * 100, 0.0)
    d["faixa"] = d["score"].map(_faixa)
    p0 = d["qt_carrinhos"].sum() / max(d["qt_visitas"].sum(), 1)
    d["taxa_carrinho"] = (d["qt_carrinhos"] + K_SUAVIZACAO * p0) / (d["qt_visitas"] + K_SUAVIZACAO)
    d["margem_por_visita"] = np.where(d["qt_visitas"] >= MIN_VISITAS, d["vl_margem"] / d["qt_visitas"].where(d["qt_visitas"] > 0), np.nan)
    d["quadrante"] = [_quadrante(r, p0) for r in d.itertuples()]
    return d, p0


def _faixa(score):
    for nome, minimo, _, _ in FAIXAS:
        if score >= minimo:
            return nome
    return "Sem sinal"


def _quadrante(r, p0):
    if r.qt_visitas < MIN_VISITAS:
        return "Poucos dados"
    alto = r.score >= CORTE_INTERESSE
    conv = r.taxa_carrinho >= p0
    if alto and conv:
        return "Estrela"
    if alto:
        return "Vitrine que não fecha"
    if conv:
        return "Joia escondida"
    return "Cão"


def _limpa(t):
    return re.sub(r"[✦★☆•]+", "", str(t)).strip()


def _cartao(r):
    """Cartão de um produto na prateleira: foto, posição, nome e score na escala azul."""
    _, _, bg, fg = next(f for f in FAIXAS if f[0] == r["faixa"])
    img = f'<img src="{html.escape(r["lk_imagem_produto"])}" style="width:100%;height:92px;object-fit:cover;border-radius:6px 6px 0 0" onerror="this.style.display=\'none\'">' \
        if isinstance(r.get("lk_imagem_produto"), str) and r["lk_imagem_produto"] else '<div style="height:92px;background:#F1F5F9"></div>'
    sem_estoque = '<span style="background:#FEE2E2;color:#DC2626;border-radius:4px;padding:1px 5px;font-size:9px;font-weight:700">SEM ESTOQUE</span>' if not r["tem_estoque"] else ""
    nome = html.escape(str(r["nm_produto"]))[:46]
    return f"""<div style="flex:0 0 138px;background:#fff;border:1px solid {COLORS['border']};border-radius:8px;overflow:hidden;position:relative">
      <div style="position:absolute;top:4px;left:4px;background:rgba(255,255,255,.92);border-radius:4px;padding:0 5px;font-size:10px;font-weight:700">{int(r['nr_posicao'])}º</div>
      {img}
      <div style="background:{bg};color:{fg};padding:4px 8px;font-size:11px;font-weight:700;display:flex;justify-content:space-between"><span>{r['faixa']}</span><span>{r['score']:.0f}</span></div>
      <div style="padding:6px 8px;font-size:11px;line-height:1.25;height:42px;overflow:hidden">{nome}</div>
      <div style="padding:0 8px 8px;font-size:10px;color:{COLORS['text_secondary']}">visitas {int(r['qt_visitas'])} · cliques da home {int(r['qt_cliques_home'])} · vendas {int(r['qt_unidades'])}<div style="margin-top:3px">{sem_estoque}</div></div>
    </div>"""


def _prateleiras(layout):
    for (ordem, nome), g in layout.sort_values(["nr_ordem_bloco", "nr_posicao"]).groupby(["nr_ordem_bloco", "nm_bloco"], sort=True):
        yield ordem, nome, g


def _legenda():
    chips = "".join(f'<span style="background:{bg};color:{fg};border-radius:4px;padding:2px 8px;font-size:11px;font-weight:600;margin-right:6px">{n} {("≥ " + str(int(m)) ) if m >= 1 else ""}</span>'
                    for n, m, bg, fg in FAIXAS)
    st.html(f'<div style="margin:2px 0 10px">{chips}</div>')


def render():
    inject_css()
    st.html("""
    <div class="report-header"><div>
      <div class="report-brand">shibari brasil · camada semanal</div>
      <div class="report-title">Mapa de <span>Interesse</span></div>
      <div class="report-meta">Onde cada produto está no site × quanto o cliente se interessa por ele</div>
    </div></div>""")
    janela = st.radio("Janela", list(JANELAS), horizontal=True, index=1)
    with st.spinner("Carregando dados do BigQuery..."):
        try:
            dados = carregar_dados(JANELAS[janela])
        except Exception as e:
            st.error(f"Erro ao carregar dados do BigQuery: {e}")
            return
    if dados["ga4"].empty:
        st.info("Sem eventos de produto do GA4 na janela.")
        return
    eventos, sem_casar = eventos_por_produto(dados["ga4"], dados["variacao"], dados["prod"]["cd_produto_nuvemshop"].dropna())
    d, p0 = calcular_interesse(dados["prod"], eventos, dados["vendas"])
    ini, fim = dados["ini"], dados["fim"]
    dias = (fim - ini).days + 1
    home = dados["home"].merge(d, on="cd_produto_nuvemshop", how="left")
    home["nm_bloco"] = home["nm_bloco"].map(_limpa)
    home["faixa"] = home["faixa"].fillna("Sem sinal")
    for c, v in [("score", 0.0), ("qt_visitas", 0), ("qt_cliques_home", 0), ("qt_unidades", 0)]:
        home[c] = home[c].fillna(v)
    home["tem_estoque"] = home["tem_estoque"].fillna(False).astype(bool)
    # o mesmo produto pode estar em mais de uma prateleira e o GA4 não diz de qual delas veio o clique:
    # cliques e vendas do produto são divididos igualmente entre as prateleiras onde ele aparece (só nas somas por prateleira/posição)
    n_prat = home.groupby("cd_produto_nuvemshop")["nm_bloco"].transform("count").clip(lower=1)
    home["cliques_rateio"] = home["qt_cliques_home"] / n_prat
    home["unidades_rateio"] = home["qt_unidades"] / n_prat

    na_home = d[d["fl_na_home"]]
    cliques_home = int(d["qt_cliques_home"].sum())
    frios_home = int(na_home["faixa"].isin(["Frio", "Sem sinal"]).sum())
    fora_quentes = d[(~d["fl_na_home"]) & d["faixa"].isin(["Quente", "Estrela"])]
    render_cards([
        card("Janela analisada", f"{dias} dias", sub=f"{ini:%d/%m} a {fim:%d/%m}", ref="dia em andamento fora"),
        card("Produtos na home", f"{len(na_home)}", sub=f"{len(home)} posições em {home['nm_bloco'].nunique()} prateleiras"),
        card("Na home sem interesse", f"{frios_home}", sub=f"de {len(na_home)} (faixa Frio ou Sem sinal)", variant="warn" if frios_home else "ok"),
        card("Quentes fora da home", f"{len(fora_quentes)}", sub="faixa Quente ou Estrela", variant="ok" if len(fora_quentes) else "neutral"),
        card("Cliques na home", f"{cliques_home}", sub=f"{cliques_home / dias:.1f} por dia", ref="sessões que clicaram em algum produto"),
    ])

    # ── 1. A home montada ────────────────────────────────────────────────────────────────
    section_title("1 · A home montada — cor = interesse do produto")
    _legenda()
    for _, nome, g in _prateleiras(home):
        cartoes = "".join(_cartao(r) for r in g.to_dict("records"))
        st.html(f'<div style="font-size:12px;font-weight:700;color:{COLORS["text_secondary"]};margin:14px 0 6px">{html.escape(nome)}</div>'
                f'<div style="display:flex;gap:8px;overflow-x:auto;padding-bottom:6px">{cartoes}</div>')
    note("Prateleiras e posições são as capturadas da home no último horário (o site é lido de hora em hora). "
         "<b>Score</b> = posição percentil (0–100) dos pontos do produto entre os produtos publicados; pontos = 1 por visita à página + "
         f"{PESO_CARRINHO} por carrinho + {PESO_PEDIDO} por pedido no período. Escala relativa: Estrela é o topo <i>do nosso catálogo</i>, não um padrão de mercado.")

    # ── 2. Veredito por prateleira ───────────────────────────────────────────────────────
    section_title("2 · Veredito por prateleira")
    prat = home.groupby(["nr_ordem_bloco", "nm_bloco"]).agg(
        posicoes=("cd_produto_nuvemshop", "count"), cliques=("cliques_rateio", "sum"), score_medio=("score", "mean"),
        frios=("faixa", lambda s: int(s.isin(["Frio", "Sem sinal"]).sum())),
        estrelas=("faixa", lambda s: int(s.isin(["Quente", "Estrela"]).sum())),
        sem_estoque=("tem_estoque", lambda s: int((~s).sum())), vendidas=("qt_unidades", "sum")).reset_index()
    prat["pct_cliques"] = prat["cliques"] / max(prat["cliques"].sum(), 1)
    prat["pct_posicoes"] = prat["posicoes"] / max(prat["posicoes"].sum(), 1)
    tabela = prat.rename(columns={"nm_bloco": "Prateleira", "posicoes": "Posições", "cliques": f"Cliques na home ({dias}d)",
                                  "pct_cliques": "% dos cliques", "pct_posicoes": "% das posições", "score_medio": "Score médio",
                                  "frios": "Produtos frios", "estrelas": "Quentes/Estrelas", "sem_estoque": "Sem estoque",
                                  "vendidas": f"Unidades vendidas pelos produtos ({dias}d)"})
    cols = ["Prateleira", "Posições", f"Cliques na home ({dias}d)", "% dos cliques", "% das posições", "Score médio", "Produtos frios", "Quentes/Estrelas", "Sem estoque", f"Unidades vendidas pelos produtos ({dias}d)"]
    st.dataframe(tabela[cols], hide_index=True, use_container_width=True, column_config={
        "% dos cliques": st.column_config.NumberColumn(format="percent"), "% das posições": st.column_config.NumberColumn(format="percent"),
        "Score médio": st.column_config.NumberColumn(format="%.0f"),
        f"Cliques na home ({dias}d)": st.column_config.NumberColumn(format="%.0f"),
        f"Unidades vendidas pelos produtos ({dias}d)": st.column_config.NumberColumn(format="%.0f")})
    note("Uma prateleira rende bem quando o <b>% dos cliques</b> é maior que o <b>% das posições</b> que ela ocupa. Cliques na home só existem "
         "para quem clicou no card da prateleira. Produto que aparece em duas prateleiras tem os cliques divididos entre elas (o GA4 não diz de qual veio; a coluna arredonda). As unidades vendidas são as do produto inteiro, sem divisão, e por isso a soma de prateleiras repete produtos. A impressão da home não é registrada para todas as prateleiras, por isso não há CTR.")

    # ── 3. Onde erro / onde acerto ───────────────────────────────────────────────────────
    section_title("3 · Onde estou errando e onde estou acertando")
    def _lista(df, cols_map):
        out = df.rename(columns=cols_map)[list(cols_map.values())]
        st.dataframe(out, hide_index=True, use_container_width=True, column_config={
            "Score": st.column_config.NumberColumn(format="%.0f"), "Margem (R$)": st.column_config.NumberColumn(format="R$ %.2f")})
    base = {"nm_produto": "Produto", "faixa": "Faixa", "score": "Score", "qt_visitas": f"Visitas ({dias}d)", "qt_carrinhos": "Carrinhos",
            "qt_unidades": "Unidades", "vl_margem": "Margem (R$)"}
    h = home.copy()
    h["Onde"] = h["nm_bloco"] + " · " + h["nr_posicao"].astype(int).astype(str) + "º"
    h["Estoque"] = np.where(h["tem_estoque"], "com estoque", "SEM estoque")
    st.markdown("**Ocupam vaga da home sem gerar interesse** (candidatos a sair)")
    erros = h[h["faixa"].isin(["Frio", "Sem sinal"])].sort_values(["score", "qt_visitas"])
    if erros.empty:
        st.success("Nenhum produto frio na home.")
    else:
        _lista(erros.assign(Onde=erros["Onde"]), {**{"nm_produto": "Produto", "Onde": "Onde", "faixa": "Faixa", "score": "Score",
              "qt_visitas": f"Visitas ({dias}d)", "qt_cliques_home": "Cliques na home", "Estoque": "Estoque"}})
    st.markdown("**Acertos: a home entrega interesse e vende** (Quente ou Estrela)")
    acertos = h[h["faixa"].isin(["Quente", "Estrela"])].sort_values("score", ascending=False)
    if acertos.empty:
        st.info("Nenhum produto da home chegou à faixa Quente no período.")
    else:
        _lista(acertos, {"nm_produto": "Produto", "Onde": "Onde", "faixa": "Faixa", "score": "Score", "qt_visitas": f"Visitas ({dias}d)",
                         "qt_unidades": "Unidades", "vl_margem": "Margem (R$)", "Estoque": "Estoque"})
    st.markdown("**Quentes fora da home** (candidatos a subir; confira o estoque antes)")
    fq = fora_quentes.assign(Estoque=np.where(fora_quentes["tem_estoque"], "com estoque", "SEM estoque"),
                             Exposição=fora_quentes["ds_nivel_exposicao"]).sort_values("score", ascending=False)
    if fq.empty:
        st.info("Nenhum produto fora da home está na faixa Quente ou Estrela.")
    else:
        _lista(fq, {"nm_produto": "Produto", "Exposição": "Exposição", "faixa": "Faixa", "score": "Score", "qt_visitas": f"Visitas ({dias}d)",
                    "qt_unidades": "Unidades", "vl_margem": "Margem (R$)", "Estoque": "Estoque"})
    note("Interesse alto fora da home pode ser tráfego de anúncio, busca ou recompra direta, não falta de vitrine. Antes de trocar, "
         "olhe a origem das visitas em Tráfego & Conteúdo. <b>Correlação não é causa</b>: produto que vende bem costuma ser posto na home.")

    # ── 4. Quadrantes ────────────────────────────────────────────────────────────────────
    section_title("4 · Quadrantes: interesse × conversão em carrinho")
    q = d[d["quadrante"] != "Poucos dados"]
    if q.empty:
        st.info(f"Nenhum produto com {MIN_VISITAS}+ visitas na janela.")
    else:
        ordem_q = ["Estrela", "Vitrine que não fecha", "Joia escondida", "Cão"]
        cont = q.groupby("quadrante").size().reindex(ordem_q, fill_value=0)
        st.html('<div class="cards">' + "".join(card(n, str(int(v))) for n, v in cont.items()) + "</div>")
        escolha = st.radio("Mostrar", ["Todos"] + ordem_q, horizontal=True, key="mapa_quadrante")
        t = q if escolha == "Todos" else q[q["quadrante"] == escolha]
        t = t.assign(_o=t["quadrante"].map({n: i for i, n in enumerate(ordem_q)})).sort_values(["_o", "score"], ascending=[True, False])
        tabela_q = pd.DataFrame({
            "Produto": t["nm_produto"], "Quadrante": t["quadrante"], "Exposição": t["ds_nivel_exposicao"], "Score": t["score"],
            f"Visitas ({dias}d)": t["qt_visitas"], "Carrinhos": t["qt_carrinhos"], "Taxa de carrinho": t["taxa_carrinho"],
            "Taxa vs média da loja": t["taxa_carrinho"] / p0, "Unidades": t["qt_unidades"], "Margem (R$)": t["vl_margem"],
            "Margem por visita (R$)": t["margem_por_visita"], "Estoque": np.where(t["tem_estoque"], "com estoque", "SEM estoque")})
        st.dataframe(tabela_q, hide_index=True, use_container_width=True, height=min(640, 38 + 35 * len(tabela_q)), column_config={
            "Score": st.column_config.ProgressColumn(format="%.0f", min_value=0, max_value=100),
            "Taxa de carrinho": st.column_config.NumberColumn(format="percent"),
            "Taxa vs média da loja": st.column_config.NumberColumn(format="%.1fx"),
            f"Visitas ({dias}d)": st.column_config.NumberColumn(format="%.0f"), "Carrinhos": st.column_config.NumberColumn(format="%.0f"),
            "Unidades": st.column_config.NumberColumn(format="%.0f"),
            "Margem (R$)": st.column_config.NumberColumn(format="R$ %.2f"), "Margem por visita (R$)": st.column_config.NumberColumn(format="R$ %.2f")})
    note(f"Ordenado por quadrante e, dentro dele, por score; a margem é a de contribuição do período (antes de mídia). "
         f"<b>Taxa de carrinho suavizada</b> = (carrinhos + {K_SUAVIZACAO} × média da loja) ÷ (visitas + {K_SUAVIZACAO}), para 1 carrinho em 2 visitas não virar 50%. "
         f"Só entram produtos com {MIN_VISITAS}+ visitas. <b>Estrela</b> = score ≥ {CORTE_INTERESSE} e taxa acima da média; "
         "<b>Vitrine que não fecha</b> = muita visita e pouco carrinho (preço, foto, descrição, frete); <b>Joia escondida</b> = pouca visita e taxa alta "
         "(candidata a subir); <b>Cão</b> = pouca visita e taxa baixa.")

    # ── 5. Efeito da posição ─────────────────────────────────────────────────────────────
    section_title("5 · Efeito da posição na prateleira")
    pos = home.groupby("nr_posicao").agg(cliques=("cliques_rateio", "sum"), cards=("cd_produto_nuvemshop", "count")).reset_index()
    pos["media"] = pos["cliques"] / pos["cards"]
    fig2 = go.Figure(go.Bar(x=pos["nr_posicao"].astype(int).astype(str), y=pos["media"], marker_color=COLORS["primary"],
                            hovertemplate="posição %{x}<br>%{y:.1f} cliques por produto<extra></extra>"))
    plotly_layout(fig2, height=300, xaxis=dict(title="Posição dentro da prateleira", type="category"), yaxis=dict(title=f"Cliques médios por produto ({dias}d)"))
    st.plotly_chart(fig2, use_container_width=True)
    note("Cliques médios na home por posição, somando todas as prateleiras. Confunde posição com produto (o 1º costuma ser o mais forte) e o volume é pequeno: "
         "leia como indício. O teste limpo é trocar a ordem e comparar, o que exige o histórico da vitrine que começou a ser coletado em 28/09/2026.")

    # ── de quando são os dados ───────────────────────────────────────────────────────────
    section_title("De quando são os dados")
    try:
        fr = carregar_frescor(TABELAS, EXTRATORES, com_ga4=True)
        detalhe_atualizacao(fr)
    except Exception:
        pass
    st.caption(
        f"Eventos por produto do GA4 existem desde 29/08/2026; janela usada: {ini:%d/%m/%Y} a {fim:%d/%m/%Y}. "
        f"{sem_casar:.1%} dos eventos de produto não casaram com nenhum produto atual (produto removido ou item de kit). "
        "Visitas somam as sessões de cada variação, então quem viu duas cores conta duas vezes.")
