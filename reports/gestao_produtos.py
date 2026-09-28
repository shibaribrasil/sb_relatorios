"""Relatório Gestão de Produtos — Shibari Brasil (camada diária).

Página de trabalho: cada seção termina numa ação no Bling ou na Nuvemshop. Regras e definição de cada lista:
ver specs/gestao-produtos.md. Toda regra (visível, tem estoque, alerta de vitrine, oferta sem estoque, componente
de kit, nível de exposição) mora na `tb_produto_gestao` do dbt; aqui só se filtra, agrupa por família e apresenta.
Visitas vêm do GA4 (região US), consultadas à parte e cruzadas no pandas pelo handle da página do produto.

ORDEM (decisão do Hugo, 28/09/2026): as seções seguem a URGÊNCIA — do maior problema para o menor. Primeiro o
estoque errado entre Bling e Nuvemshop (pode vender o que não tem), depois estoque parado fora do site, depois o
que o cliente vê e não consegue comprar. Dentro das seções, a prioridade é a EXPOSIÇÃO no site: prateleira da
home primeiro (informação principal, com destaque visual), depois oferta no Cashing, depois outras vitrines.
"""
import re

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from common import bigquery as bq
from common.design import COLORS, inject_css, card, render_cards, section_title, note, plotly_layout, brl
from common.frescor import carregar_frescor, badge_atualizacao, detalhe_atualizacao, alerta_atraso
from reports.vendas_margem import _hoje_brt

TABELAS = ("tb_produto_gestao", "tb_produto_vitrine", "tb_estoque_movimento_dia", "tb_produto_teste", "tb_produto_ciclo_monitor")
EXTRATORES = ("bling_products", "bling_orders", "bling_purchases", "nuvemshop_products", "nuvemshop_storefront", "nuvemshop_orders")

DIAS_MOVIMENTO = 7          # janela da seção "O que mudou no estoque"
DIAS_TESTE_A_VENCER = 30    # decisão do Hugo (28/09/2026)
DIAS_VISITAS = 30           # janela das visitas (GA4)
PRECO_MAX_OFERTA = 60.0     # teto de preço dos candidatos a oferta (filtro de apresentação)
CORES_MOV = {"Zerou": COLORS["danger"], "Voltou": COLORS["success"], "Subiu": COLORS["info"], "Baixou": COLORS["text_muted"]}

# rótulos padronizados — sempre dizendo "onde" (site, Bling, Nuvemshop) e "quando" (janela)
COL_HOME = "Prateleira da home"
COL_OUTRA = "Outra exposição no site"
COL_VIS = "Visível no site"
COL_EST_BLING = "Estoque no Bling"
COL_VISITAS = f"Visitas à página ({DIAS_VISITAS} dias)"
COL_VENDIDAS_90 = "Unidades vendidas (90 dias)"


@st.cache_data(ttl=300)
def carregar_dados():
    client = bq.get_client()
    az = f"`{bq.PROJECT}.dbt_dw_az"
    g = bq.query_df(client, f"SELECT * FROM {az}.tb_produto_gestao`")
    mov = bq.query_df(client, f"""
        SELECT m.cd_produto_bling, m.dt_posicao, m.qt_estoque_anterior, m.qt_estoque_atual, m.qt_variacao, m.ds_movimento
          FROM {az}.tb_estoque_movimento_dia` AS m
         WHERE m.dt_posicao >= DATE_SUB(CURRENT_DATE('America/Sao_Paulo'), INTERVAL {DIAS_MOVIMENTO - 1} DAY)
    """)
    teste = bq.query_df(client, f"SELECT * FROM {az}.tb_produto_teste`")
    monitor = bq.query_df(client, f"SELECT * FROM {az}.tb_produto_ciclo_monitor`")
    categorias = bq.query_df(client, f"SELECT * FROM {az}.tb_categoria_loja`")
    visitas = bq.query_df(client, f"""
        SELECT REGEXP_EXTRACT(ds_pagina_url, r'/produtos/([^/?#]+)') AS ds_url_produto,
               SUM(qt_pageviews) AS qt_visitas, SUM(qt_usuarios_unicos) AS qt_usuarios
          FROM `{bq.PROJECT}.dbt_dw_us_az.tb_ga4_pagina_diaria`
         WHERE dt_data BETWEEN DATE_SUB(CURRENT_DATE('America/Sao_Paulo'), INTERVAL {DIAS_VISITAS} DAY)
                           AND DATE_SUB(CURRENT_DATE('America/Sao_Paulo'), INTERVAL 1 DAY)
           AND REGEXP_CONTAINS(ds_pagina_url, r'/produtos/[^/?#]+')
         GROUP BY 1
    """)
    for c in ["qt_estoque_bling", "qt_estoque_nuvemshop", "qt_estoque_familia", "vl_estoque_custo", "qt_pecas_30d", "qt_pecas_60d",
              "qt_pecas_90d", "qt_pedidos_90d", "vl_receita_liquida_90d", "vl_margem_contribuicao_90d", "qt_compra_pendente",
              "vl_custo_cadastro", "vl_preco_bling", "vl_preco_de_nuvemshop", "vl_preco_por_nuvemshop", "qt_cobertura_atual",
              "nr_nivel_exposicao", "nr_posicao_prateleira_principal", "qt_paginas_como_similar", "qt_paginas_como_complementar",
              "qt_imagens_sem_alt", "qt_variacoes_sem_gtin_produto", "nr_ranking_mais_vendidos"]:
        g[c] = pd.to_numeric(g[c], errors="coerce")
    for c in ["fg_visivel_site", "fg_tem_estoque", "fg_estoque_divergente", "fg_sem_custo", "fg_sem_papel", "fg_sem_ciclo",
              "fg_sem_imagem", "fg_sem_peso", "fg_preco_divergente", "fg_em_promocao", "fg_na_nuvemshop", "fg_oferta_sem_estoque",
              "fg_familia_visivel", "fl_na_home", "fl_sem_seo", "fg_componente_kit_visivel"]:
        g[c] = g[c].fillna(False).astype(bool)
    for c in ["nr_nivel_exposicao", "qt_paginas_como_similar", "qt_paginas_como_complementar", "qt_imagens_sem_alt",
              "qt_variacoes_sem_gtin_produto"]:
        g[c] = g[c].fillna(0)
    g["home"] = [_texto_home(r) for r in g.itertuples()]
    g["outra_exposicao"] = [_texto_outra(r) for r in g.itertuples()]
    g["em_oferta"] = g["ds_tipo_oferta"].notna()
    # prioridade de exposição: home > oferta no Cashing > vitrine > só categoria > fora do site
    g["prioridade"] = g["fl_na_home"].astype(int) * 100 + g["em_oferta"].astype(int) * 10 + g["nr_nivel_exposicao"]
    mov["dt_posicao"] = pd.to_datetime(mov["dt_posicao"])
    visitas["qt_visitas"] = pd.to_numeric(visitas["qt_visitas"]).fillna(0)
    return {"g": g, "mov": mov, "teste": teste, "monitor": monitor, "visitas": visitas, "categorias": categorias}


def _limpa_titulo(t):
    return re.sub(r"[✦★☆•]+", "", str(t)).strip() if isinstance(t, str) else ""


def _texto_home(r):
    """Prateleira da home — a informação principal de exposição (vazio = não está na home)."""
    if not r.fl_na_home:
        return None
    pos = f" · {int(r.nr_posicao_prateleira_principal)}º" if pd.notna(r.nr_posicao_prateleira_principal) else ""
    outras = len(str(r.ds_prateleiras_home).split(", ")) - 1 if isinstance(r.ds_prateleiras_home, str) else 0
    return f"{_limpa_titulo(r.nm_prateleira_principal)}{pos}" + (f" (+{outras} prateleira)" if outras == 1 else f" (+{outras} prateleiras)" if outras > 1 else "")


def _texto_outra(r):
    """Demais exposições: oferta no Cashing, categorias de vitrine, sugestões em outras páginas."""
    partes = []
    if isinstance(r.ds_tipo_oferta, str) and r.ds_tipo_oferta:
        partes.append(f"Oferta no Cashing ({r.ds_tipo_oferta})")
    if isinstance(r.ds_categorias_vitrine, str) and r.ds_categorias_vitrine:
        partes.append(f"Categoria {r.ds_categorias_vitrine}")
    n = int(r.qt_paginas_como_similar + r.qt_paginas_como_complementar)
    if n:
        partes.append(f"sugerido em {n} página{'s' if n > 1 else ''} de produto")
    if partes:
        return " · ".join(partes)
    if r.fl_na_home:
        return "—"
    return "Só na categoria" if r.nr_nivel_exposicao == 1 else "Fora do site"


def _familias(g, visitas):
    """Agrupa os SKUs no produto como aparece na loja (família) e cruza com as visitas pela página.
    Ordena pela prioridade de exposição antes de agrupar: o `first` pega a exposição mais alta da família."""
    g = g.sort_values("prioridade", ascending=False)
    f = g.groupby("cd_produto_bling_familia", as_index=False).agg(
        produto=("nm_produto", "first"), frente=("ds_frente", "first"), categoria=("ds_categoria", "first"),
        papel=("ds_papel", "first"), ciclo=("ds_ciclo", "first"), oferta=("ds_tipo_oferta", "first"),
        url=("ds_url_produto", "first"), fornecedor=("nm_fornecedor", "first"),
        skus=("cd_produto_bling", "count"), visivel=("fg_visivel_site", "any"), estoque=("qt_estoque_familia", "first"),
        estoque_custo=("vl_estoque_custo", "sum"), compra_pendente=("qt_compra_pendente", "sum"),
        pecas_30d=("qt_pecas_30d", "sum"), pecas_90d=("qt_pecas_90d", "sum"), pedidos_90d=("qt_pedidos_90d", "sum"),
        receita_90d=("vl_receita_liquida_90d", "sum"), margem_90d=("vl_margem_contribuicao_90d", "sum"),
        dias_sem_venda=("qt_dias_sem_venda", "min"), preco_por=("vl_preco_por_nuvemshop", "min"),
        risco=("ds_classificacao_risco", lambda s: " · ".join(sorted({str(x)[3:].strip() for x in s.dropna()}))),
        prioridade=("prioridade", "max"), nivel=("nr_nivel_exposicao", "max"), home=("home", "first"),
        outra_exposicao=("outra_exposicao", "first"), alerta_exposicao=("ds_alerta_exposicao", "first"),
        ranking=("nr_ranking_mais_vendidos", "min"),
    )
    v = visitas.groupby("ds_url_produto", as_index=False)["qt_visitas"].sum().rename(columns={"ds_url_produto": "url", "qt_visitas": "visitas"})
    f = f.merge(v, on="url", how="left")
    f["visitas"] = f["visitas"].fillna(0).astype(int)
    return f


def _n(v):
    return f"{int(v):,}".replace(",", ".")


NUM = st.column_config.NumberColumn
TXT = st.column_config.TextColumn
BRL0 = NUM(format="R$ %.0f")
BRL2 = NUM(format="R$ %.2f")
CFG_HOME = TXT(COL_HOME, width=210, help="Prateleira da home onde o produto aparece agora e a posição dentro dela. "
                                         "Linhas destacadas = produto na home: é o que mais gente vê, trate primeiro.")
CFG_OUTRA = TXT(COL_OUTRA, width=230, help="Oferta no Cashing, categoria de vitrine (Liquidação, Seleção Prazer...) ou sugestão "
                                           "em \"Produtos similares\"/\"Para comprar com esse produto\" da página de outros produtos.")


def _fmt_num(v, fmt):
    if v is None or pd.isna(v):
        return "—"
    if fmt == "brl0":
        return brl(v, 0)
    if fmt == "brl2":
        return brl(v, 2)
    if fmt == "x":
        return f"{v:.2f}×".replace(".", ",")
    return _n(v) if float(v).is_integer() else f"{v:.1f}".replace(".", ",")


def _tabela(df, colunas, config=None, formatos=None, altura=None):
    """Tabela padrão. Linhas de produto NA HOME ganham destaque (fundo âmbar, negrito). `formatos` = {coluna exibida:
    'brl0'|'brl2'|'x'} para colunas numéricas especiais (com Styler, o formato de número é feito aqui)."""
    if df.empty:
        st.success("Nada nesta lista agora.")
        return
    kw = {"height": altura} if altura else {}
    t = df[list(colunas)].copy()
    for c in t.columns:
        if c.startswith("dt_"):          # datas chegam do BigQuery como objeto `date`: padroniza para datetime
            t[c] = pd.to_datetime(t[c], errors="coerce")
    t = t.rename(columns=colunas).reset_index(drop=True)
    na_home = (df["home"].notna() if "home" in df.columns else pd.Series(False, index=df.index)).reset_index(drop=True)
    formatos = formatos or {}
    fmt = {}
    for c in t.columns:
        if pd.api.types.is_bool_dtype(t[c]):
            t[c] = t[c].map({True: "Sim", False: "Não"})
        elif pd.api.types.is_numeric_dtype(t[c]) and not pd.api.types.is_datetime64_any_dtype(t[c]):
            fmt[c] = (lambda f: (lambda v: _fmt_num(v, f)))(formatos.get(c, "n"))
        elif pd.api.types.is_datetime64_any_dtype(t[c]):
            fmt[c] = lambda v: v.strftime("%d/%m/%y") if pd.notna(v) else "—"
        else:
            t[c] = t[c].astype(object).where(t[c].notna(), "—")
    estilo = f"background-color: {COLORS['warning_bg']}; font-weight: 600"
    sty = t.style.format(fmt).apply(lambda linha: [estilo if na_home.iloc[linha.name] else "" for _ in linha], axis=1)
    st.dataframe(sty, hide_index=True, use_container_width=True, column_config=config or {}, **kw)


def render():
    inject_css()
    with st.spinner("Carregando dados do BigQuery..."):
        try:
            dados = carregar_dados()
        except Exception as e:
            st.error(f"Erro ao carregar dados do BigQuery: {e}")
            return
    hoje = _hoje_brt()
    try:
        fr = carregar_frescor(TABELAS, EXTRATORES, com_ga4=True)
    except Exception:
        fr = None
    st.html(f"""
    <div class="report-header">
      <div>
        <div class="report-brand">shibari brasil · camada diária</div>
        <div class="report-title">Gestão de <span>Produtos</span></div>
        <div class="report-meta">Do problema mais urgente para o menor · dentro de cada lista, o produto mais exposto no site vem primeiro (linhas destacadas = na home)</div>
      </div>
      {badge_atualizacao(fr) if fr else f'<div class="report-badge">Hoje: <strong>{hoje.strftime("%d/%m/%Y")}</strong></div>'}
    </div>
    """)
    if fr:
        alerta_atraso(fr)

    frente = st.selectbox("Frente", options=["Todas", "Shibari", "Curadoria"])
    g = dados["g"]
    if frente != "Todas":
        g = g[g["ds_frente"] == frente]
    if g.empty:
        st.info("Sem produtos para a frente escolhida.")
        return
    f = _familias(g, dados["visitas"])
    mediana_visitas = float(dados["visitas"].loc[dados["visitas"]["qt_visitas"] > 0, "qt_visitas"].median() or 0)

    # ── listas (na ordem das seções) ──
    diverg = g[g["fg_estoque_divergente"]].copy()
    diverg["diferenca"] = diverg["qt_estoque_nuvemshop"] - diverg["qt_estoque_bling"].clip(lower=0)
    diverg["risco_div"] = diverg["diferenca"].map(lambda d: "Nuvemshop mostra MAIS do que existe — pode vender sem ter" if d > 0
                                                  else "Nuvemshop mostra MENOS — deixa de vender o que tem")
    diverg = diverg.sort_values(["diferenca", "prioridade"], ascending=[False, False])
    fora_site = g[g["ds_alerta_vitrine"].isin(["Com estoque e fora da Nuvemshop", "Com estoque e produto não publicado",
                                               "Com estoque e variação oculta"])].sort_values("qt_estoque_bling", ascending=False)
    kit_ok = g[g["fg_componente_kit_visivel"] & g["fg_tem_estoque"] & ~g["fg_visivel_site"]]
    vis_sem_est = f[f["visivel"] & (f["estoque"] <= 0)].sort_values(["prioridade", "visitas", "pecas_90d"], ascending=False)
    ofertas = g[g["em_oferta"]].sort_values(["fg_oferta_sem_estoque", "prioridade"], ascending=False)
    mov = dados["mov"].merge(g[["cd_produto_bling", "nm_produto_completo", "ds_frente", "fg_visivel_site", "prioridade",
                                "nr_nivel_exposicao", "home", "outra_exposicao"]], on="cd_produto_bling", how="inner")
    procura = f[(f["estoque"] <= 0) & ((f["visitas"] > 0) | (f["pecas_90d"] > 0))].sort_values(["prioridade", "visitas", "pecas_90d"],
                                                                                              ascending=False)
    teste = dados["teste"].copy()
    if not teste.empty:
        teste["dt_limite_decisao"] = pd.to_datetime(teste["dt_limite_decisao"])
        teste["dias_ate_prazo"] = (teste["dt_limite_decisao"] - pd.Timestamp(hoje)).dt.days
    ativos_teste = teste[teste["ds_status_final"].isna()] if not teste.empty else teste
    a_decidir = ativos_teste[(ativos_teste["dias_ate_prazo"] <= DIAS_TESTE_A_VENCER) | ativos_teste["fg_criterio_atingido"].fillna(False)] \
        if not ativos_teste.empty else ativos_teste
    sem_custo_est = g[g["fg_sem_custo"] & (g["fg_tem_estoque"] | g["fg_visivel_site"])]
    expo_fam = f.set_index("cd_produto_bling_familia")[["prioridade", "nivel", "home", "outra_exposicao", "alerta_exposicao"]]
    conflitos = f[f["alerta_exposicao"].notna()]
    cats = dados["categorias"]
    cats_vazias = cats[cats["fl_visivel_vazia"].fillna(False).astype(bool)] if not cats.empty else cats
    n_home_sem = int(vis_sem_est["home"].notna().sum())
    n_oferta_sem = int(ofertas["fg_oferta_sem_estoque"].sum())
    n_div_mais = int((diverg["diferenca"] > 0).sum())

    # ═══ ONDE AGIR HOJE ═══
    section_title("Onde agir hoje — do mais urgente para o menos")
    render_cards([
        card("1. Estoque diferente Bling × Nuvemshop", _n(len(diverg)), f"{n_div_mais} com a Nuvemshop mostrando mais do que existe",
             variant="bad" if len(diverg) else "ok"),
        card("2. Com estoque e fora do site", _n(len(fora_site)), "variações paradas por cadastro (componentes de kit visível já excluídos)",
             variant="bad" if len(fora_site) else "ok"),
        card("3. Visíveis no site sem estoque", _n(len(vis_sem_est)), f"{n_home_sem} numa prateleira da home",
             variant="bad" if len(vis_sem_est) else "ok"),
        card("4. Ofertas do Cashing sem estoque", _n(n_oferta_sem), f"de {_n(len(ofertas))} variações em oferta",
             variant="bad" if n_oferta_sem else "ok"),
        card(f"5. Zeraram em {DIAS_MOVIMENTO} dias", _n((mov["ds_movimento"] == "Zerou").sum()), "variações que ficaram sem estoque no Bling",
             variant="warn" if (mov["ds_movimento"] == "Zerou").any() else "ok"),
        card("6. Procura sem estoque", _n(len(procura)), "zerados com visita ou venda recente",
             variant="warn" if len(procura) else "ok"),
        card("7. Testes a decidir", _n(len(a_decidir)), f"prazo em até {DIAS_TESTE_A_VENCER} dias ou critério atingido",
             variant="warn" if len(a_decidir) else "ok"),
        card("Conflitos de vitrine", _n(len(conflitos) + len(cats_vazias)),
             f"{len(conflitos)} ciclo de vida × vitrine · {len(cats_vazias)} categoria(s) do site vazia(s)",
             variant="warn" if len(conflitos) + len(cats_vazias) else "ok"),
        card("Sem custo no Bling", _n(len(sem_custo_est)), "variações com estoque ou visíveis — margem superestimada",
             variant="warn" if len(sem_custo_est) else "ok"),
    ])
    note("Os números seguem a ordem das seções abaixo, do problema mais grave para o menos grave. Vermelho = venda perdida ou errada agora. "
         f"Em todas as listas, <strong style='background:{COLORS['warning_bg']};padding:0 4px'>linhas destacadas</strong> são produtos numa "
         "prateleira da home — o que mais gente vê; trate primeiro.")

    # ═══ 1. ESTOQUE DIFERENTE ═══
    section_title("1. Estoque diferente entre Bling e Nuvemshop — corrigir primeiro")
    _tabela(diverg,
            {"nm_produto_completo": "Variação", "home": COL_HOME, "cd_produto": "SKU", "qt_estoque_bling": COL_EST_BLING,
             "qt_estoque_nuvemshop": "Estoque na Nuvemshop", "diferenca": "Diferença (Nuvemshop − Bling)", "risco_div": "Risco",
             "fg_visivel_site": COL_VIS, "outra_exposicao": COL_OUTRA},
            {"Variação": TXT(width=260), COL_HOME: CFG_HOME, COL_OUTRA: CFG_OUTRA, "Risco": TXT(width=320)})
    note("O estoque do Bling é a verdade; a Nuvemshop deveria espelhá-lo. <strong>Nuvemshop maior que o Bling</strong> = o site aceita pedido "
         "de uma peça que não existe (vem primeiro). <strong>Nuvemshop menor</strong> = o site mostra esgotado ou menos do que temos. Só entram "
         "variações com controle de estoque ligado na Nuvemshop. Ação: sincronizar o estoque do produto no Bling com a loja.")

    # ═══ 2. COM ESTOQUE E FORA DO SITE ═══
    section_title("2. Com estoque e fora do site — venda parada por cadastro")
    _tabela(fora_site, {"nm_produto_completo": "Variação", "cd_produto": "SKU", "qt_estoque_bling": COL_EST_BLING,
                        "ds_alerta_vitrine": "Situação na Nuvemshop", "ds_kits_do_componente": "Kits que usam este item",
                        "qt_pecas_90d": COL_VENDIDAS_90},
            {"Variação": TXT(width=280), "Situação na Nuvemshop": TXT(width=240), "Kits que usam este item": TXT(width=280)})
    with st.expander(f"Componentes de kit visível no site, fora da lista ({len(kit_ok)})"):
        _tabela(kit_ok, {"nm_produto_completo": "Variação", "qt_estoque_bling": COL_EST_BLING, "ds_kits_do_componente": "Kits que usam este item"},
                {"Variação": TXT(width=280), "Kits que usam este item": TXT(width=320)})
    note("Tem estoque no Bling e o cliente não encontra no site: fora da Nuvemshop, produto não publicado ou variação oculta. Ação: publicar o "
         "produto, mostrar a variação ou cadastrar na Nuvemshop. <strong>Não entra</strong> quem é componente de um kit/composição visível no site "
         "— esse item é vendido dentro do kit (lista no expansor). Se o único kit que usa o item está oculto, ele continua aqui, com o kit indicado.")

    # ═══ 3. VISÍVEIS SEM ESTOQUE ═══
    section_title("3. Visíveis no site sem nenhum estoque — o cliente vê e não consegue comprar")
    _tabela(vis_sem_est, {"produto": "Produto", "home": COL_HOME, "outra_exposicao": COL_OUTRA, "frente": "Frente",
                          "visitas": COL_VISITAS, "pecas_90d": COL_VENDIDAS_90, "compra_pendente": "Compra pendente (un.)",
                          "ciclo": "Ciclo de vida"},
            {"Produto": TXT(width=230), COL_HOME: CFG_HOME, COL_OUTRA: CFG_OUTRA})
    esgot = g[(g["ds_alerta_vitrine"] == "Visível sem estoque") & (g["qt_estoque_familia"] > 0)].sort_values("prioridade", ascending=False)
    sem_ctrl = g[g["ds_alerta_vitrine"] == "Visível, sem estoque e vendendo sem controle"]
    with st.expander(f"Variações esgotadas em produtos com estoque ({len(esgot)}) e vendendo sem controle de estoque ({len(sem_ctrl)})"):
        _tabela(pd.concat([sem_ctrl, esgot]), {"nm_produto_completo": "Variação", "home": COL_HOME, "ds_alerta_vitrine": "Situação",
                                               "qt_estoque_familia": "Estoque do produto no Bling (todas as variações)",
                                               "qt_pecas_90d": COL_VENDIDAS_90},
                {"Variação": TXT(width=280), COL_HOME: CFG_HOME})
    note("Produto publicado e com alguma variação visível, mas sem nenhuma unidade no Bling. Ordem: <strong>prateleira da home</strong> › oferta no "
         "Cashing › categoria de vitrine ou sugestão em outras páginas › só na categoria. Ação: na home, tirar da prateleira ou repor já; "
         "no resto, ocultar ou repor. Variação esgotada dentro de um produto com estoque é normal (o site mostra a opção como esgotada) — "
         "fica no expansor. \"Vendendo sem controle\" = controle de estoque desligado na Nuvemshop: aceita pedido com o Bling zerado.")

    # ═══ 4. OFERTAS (CASHING) ═══
    section_title("4. Ofertas no Cashing — oferta sem estoque é a pior vitrine")
    if ofertas.empty:
        st.info("Nenhum produto com o campo \"Tipo de oferta\" preenchido no Bling.")
    else:
        of = ofertas.assign(situacao=ofertas["fg_oferta_sem_estoque"].map({True: "Sem estoque — trocar a oferta já", False: "Ok"}))
        _tabela(of, {"nm_produto_completo": "Variação", "ds_tipo_oferta": "Tipo de oferta", "situacao": "Situação",
                     "qt_estoque_bling": COL_EST_BLING, "fg_visivel_site": COL_VIS, "home": COL_HOME,
                     "qt_pecas_30d": "Unidades vendidas (30 dias)", "qt_pecas_90d": COL_VENDIDAS_90,
                     "vl_margem_contribuicao_90d": "Margem de contribuição (90 dias)"},
                {"Variação": TXT(width=240), "Situação": TXT(width=220), COL_HOME: CFG_HOME},
                formatos={"Margem de contribuição (90 dias)": "brl0"})
    cand = f[f["visivel"] & (f["estoque"] > 0) & f["oferta"].isna() & f["papel"].isin(["Impulso", "Complementar"])
             & (f["preco_por"] <= PRECO_MAX_OFERTA)].sort_values("estoque_custo", ascending=False)
    with st.expander(f"Candidatos a oferta ({len(cand)})"):
        _tabela(cand, {"produto": "Produto", "papel": "Papel", "preco_por": "Preço no site", "estoque": COL_EST_BLING,
                       "estoque_custo": "Estoque a custo", "pecas_90d": COL_VENDIDAS_90, "home": COL_HOME, "visitas": COL_VISITAS},
                {"Produto": TXT(width=220), COL_HOME: CFG_HOME}, formatos={"Preço no site": "brl2", "Estoque a custo": "brl0"})
    note(f"Ofertas = campo <strong>Tipo de oferta</strong> do cadastro do Bling (order bump e upsell no Cashing). A oferta aparece para todo "
         f"cliente no carrinho/checkout: <strong>sem estoque, vem primeiro</strong> — é a vitrine mais cara de errar. Candidatos: visíveis no site, "
         f"com estoque no Bling, sem oferta, papel Impulso ou Complementar e preço até {brl(PRECO_MAX_OFERTA, 0)}, do maior estoque parado para o menor.")

    # ═══ 5. MOVIMENTOS ═══
    section_title(f"5. O que mudou no estoque — últimos {DIAS_MOVIMENTO} dias")
    if mov.empty:
        st.info("Nenhuma variação mudou de estoque na janela.")
    else:
        d = mov.groupby([mov["dt_posicao"].dt.date, "ds_movimento"]).size().unstack(fill_value=0)
        fig = go.Figure()
        for m in ["Zerou", "Voltou", "Subiu", "Baixou"]:
            if m in d.columns:
                fig.add_bar(x=pd.to_datetime(d.index), y=d[m], name=m, marker_color=CORES_MOV[m],
                            hovertemplate=m + " · %{x|%d/%m}: %{y}<extra></extra>")
        plotly_layout(fig, height=240, barmode="stack", xaxis=dict(tickformat="%d/%m", dtick=86400000, gridcolor=COLORS["grid"]),
                      yaxis=dict(title="variações", gridcolor=COLORS["grid"]))
        with st.container(border=True):
            st.plotly_chart(fig, use_container_width=True)

        def _acao(r):
            if r.ds_movimento == "Zerou":
                if not r.fg_visivel_site:
                    return "Ok (já oculta no site)"
                return "Tirar da home ou repor já" if isinstance(r.home, str) else "Ocultar ou repor"
            if r.ds_movimento == "Voltou":
                if not r.fg_visivel_site:
                    return "Publicar no site"
                return "Divulgar a volta" if r.nr_nivel_exposicao >= 2 else "Divulgar a volta e colocar em vitrine"
            if r.ds_movimento == "Subiu":
                return "Conferir publicação e preço" if not r.fg_visivel_site else "Entrada registrada"
            return "—"
        mov = mov.assign(acao=[_acao(r) for r in mov.itertuples()]).sort_values(["dt_posicao", "prioridade", "ds_movimento"],
                                                                               ascending=[False, False, True])
        mostrar_baixas = st.toggle("Mostrar também as baixas (vendas/ajustes sem zerar)", value=False)
        m2 = mov if mostrar_baixas else mov[mov["ds_movimento"] != "Baixou"]
        _tabela(m2, {"dt_posicao": "Dia", "nm_produto_completo": "Variação", "home": COL_HOME, "ds_movimento": "Movimento",
                     "qt_estoque_anterior": "Estoque antes (Bling)", "qt_estoque_atual": "Estoque agora (Bling)",
                     "fg_visivel_site": COL_VIS, "acao": "Ação"},
                {"Variação": TXT(width=260), COL_HOME: CFG_HOME})
    note("Foto diária do estoque do Bling (desde 02/07/2026). <strong>Zerou</strong>: tinha e ficou sem · <strong>Voltou</strong>: estava sem e voltou · "
         "<strong>Subiu</strong>: tinha e aumentou (entrada) · <strong>Baixou</strong>: diminuiu sem zerar. No mesmo dia, o produto da home vem primeiro. "
         "Se faltar a foto de um dia, a comparação é com a última foto disponível.")

    # ═══ 6. PROCURA SEM ESTOQUE ═══
    section_title("6. Procura sem estoque — prioridade de reposição")
    _tabela(procura, {"produto": "Produto", "home": COL_HOME, "outra_exposicao": COL_OUTRA, "visitas": COL_VISITAS,
                      "pecas_90d": COL_VENDIDAS_90, "ranking": "Posição no \"mais vendidos\" da loja", "compra_pendente": "Compra pendente (un.)",
                      "fornecedor": "Fornecedor", "ciclo": "Ciclo de vida", "visivel": COL_VIS},
            {"Produto": TXT(width=230), COL_HOME: CFG_HOME, COL_OUTRA: CFG_OUTRA})
    note("Zerados no Bling que as pessoas continuam procurando (visitas à página) ou que venderam nos últimos 90 dias, do mais exposto para o "
         "menos: zerado e ainda em vitrine é demanda empurrada para um produto que não dá para comprar. Produto Em Saída ou Descontinuado "
         "não se repõe: tirar da vitrine e ocultar.")

    # ═══ 7. TESTE ═══
    section_title("7. Produtos em teste (Curadoria)")
    if teste.empty:
        st.info("Nenhum produto em teste.")
    else:
        at = ativos_teste.join(expo_fam, on="cd_produto_bling_familia")
        at["alerta"] = [" · ".join(x for x in [
                            "Critério atingido — decidir" if c else "",
                            f"Vence em {int(dd)} dias" if pd.notna(dd) and dd <= DIAS_TESTE_A_VENCER else "",
                            "Sem vitrine — o teste precisa de exposição" if pd.notna(nv) and nv <= 1 else ""] if x)
                        for c, dd, nv in zip(at["fg_criterio_atingido"].fillna(False), at["dias_ate_prazo"], at["nivel"])]
        at = at.sort_values(["alerta", "dias_ate_prazo"], ascending=[False, True], na_position="last")
        _tabela(at, {"nm_produto": "Produto", "home": COL_HOME, "outra_exposicao": COL_OUTRA, "ds_papel_pretendido": "Papel pretendido",
                     "dt_ini_teste": "Início do teste", "qt_dias_disponiveis": "Dias com estoque no teste", "dt_limite_decisao": "Prazo de decisão",
                     "dias_ate_prazo": "Dias até o prazo", "qt_pedidos": "Pedidos no teste", "vl_payback": "Payback do lote",
                     "ds_status": "Status recomendado", "alerta": "Alerta"},
                {"Produto": TXT(width=200), COL_HOME: CFG_HOME, COL_OUTRA: CFG_OUTRA, "Status recomendado": TXT(width=220),
                 "Alerta": TXT(width=260)}, formatos={"Payback do lote": "x"})
        saiu = teste[teste["ds_status_final"].notna()]
        if not saiu.empty:
            st.markdown("**Saíram do teste nos últimos 90 dias**")
            _tabela(saiu, {"nm_produto": "Produto", "ds_status_final": "Resultado", "dt_saida": "Saída", "qt_pedidos": "Pedidos no teste",
                           "vl_payback": "Payback do lote"}, formatos={"Payback do lote": "x"})
    note(f"Critérios por papel aprovados em 25/09/2026 (Core 180 dias e ≥ 7 pedidos; Complementar 120 dias e payback; Impulso 90 dias e attach rate ≥ 3%). "
         f"A contagem só corre com estoque disponível. <strong>Alerta</strong>: prazo em até {DIAS_TESTE_A_VENCER} dias, critério atingido antes do prazo "
         "ou <strong>sem vitrine</strong> (só na categoria: o teste não tem chance justa — colocar na home, numa categoria de vitrine ou como sugestão "
         "em produtos parecidos). Payback = margem de contribuição acumulada ÷ investimento no lote. A troca de ciclo é manual no Bling.")

    # ═══ 8. EM SAÍDA / DIFICULDADE ═══
    section_title("8. Em Saída e Dificuldade de Reposição (Curadoria)")
    mon = dados["monitor"].join(expo_fam, on="cd_produto_bling_familia") if not dados["monitor"].empty else dados["monitor"]
    if mon.empty:
        st.info("Nenhum produto nesses ciclos.")
    else:
        for r in mon[mon["alerta_exposicao"].notna()].itertuples():
            note(f"<strong>{r.nm_produto}</strong>: {r.alerta_exposicao}.", variant="warn")
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Em Saída** — liquidar o estoque")
            _tabela(mon[mon["ds_ciclo"] == "Em Saída"].sort_values("prioridade", ascending=False),
                    {"nm_produto": "Produto", "home": COL_HOME, "qt_estoque": COL_EST_BLING, "qt_vendas_60d": "Unidades vendidas (60 dias)",
                     "qt_dias_para_zerar": "Dias para zerar", "ds_status_saida": "Status", "ds_acao_saida": "Ação"},
                    {"Produto": TXT(width=180), "Ação": TXT(width=200), COL_HOME: CFG_HOME})
        with c2:
            st.markdown("**Dificuldade de Reposição** — buscar alternativa")
            _tabela(mon[mon["ds_ciclo"] == "Dificuldade de Reposição"].sort_values("prioridade", ascending=False),
                    {"nm_produto": "Produto", "home": COL_HOME, "qt_estoque": COL_EST_BLING, "qt_dias_ruptura": "Dias sem estoque",
                     "vl_venda_perdida_estimada": "Margem perdida estimada", "ds_status_reposicao": "Status"},
                    {"Produto": TXT(width=180), COL_HOME: CFG_HOME}, formatos={"Margem perdida estimada": "brl0"})
    note("Em Saída: dias para zerar = estoque ÷ ritmo de venda dos últimos 60 dias; vazio = sem venda no período. Produto Em Saída numa prateleira "
         "de <em>novidade</em> passa a mensagem errada — o lugar dele é liquidação/oferta. Margem perdida = dias sem estoque × ritmo anterior × "
         "margem de contribuição por unidade (estimativa).")

    # ═══ 9. EMPURRÃOZINHO ═══
    section_title("9. Empurrãozinho — produtos com estoque que giram devagar")
    lento = f[f["visivel"] & (f["estoque"] > 0) & (f["papel"] != "Brinde")
              & ((f["pecas_90d"] == 0) | f["risco"].str.contains("Encalhado|Sobreestoque", regex=True))].copy()

    def _diag(r):
        if r.nivel <= 1:
            return "Sem vitrine — levar para a home, uma categoria de vitrine ou sugestão de produto parecido"
        if r.visitas < mediana_visitas:
            return "Em vitrine e pouca visita — divulgar fora do site (Instagram, anúncio, oferta)"
        return "Visitam e não compram — revisar preço, fotos e descrição"
    lento["diagnostico"] = [_diag(r) for r in lento.itertuples()]
    lento = lento.sort_values(["nivel", "estoque_custo"], ascending=[True, False])
    sem_vit = lento["nivel"] <= 1
    render_cards([
        card("Produtos girando devagar", _n(len(lento)), "visíveis no site e com estoque no Bling"),
        card("Estoque parado a custo", brl(lento["estoque_custo"].sum(), 0), "estoque desses produtos pelo custo de cadastro"),
        card("Sem vitrine", _n(sem_vit.sum()), "só aparecem na categoria — primeiro passo: expor"),
        card("Pouca visita", _n((~sem_vit & (lento["visitas"] < mediana_visitas)).sum()), f"em vitrine, abaixo de {_n(mediana_visitas)} visitas em {DIAS_VISITAS} dias"),
        card("Visitam e não compram", _n((~sem_vit & (lento["visitas"] >= mediana_visitas)).sum()), "problema de página, preço ou foto"),
    ])
    _tabela(lento, {"produto": "Produto", "home": COL_HOME, "outra_exposicao": COL_OUTRA, "estoque": COL_EST_BLING,
                    "estoque_custo": "Estoque a custo", "visitas": COL_VISITAS, "pecas_90d": COL_VENDIDAS_90,
                    "dias_sem_venda": "Dias sem venda", "risco": "Risco de estoque", "diagnostico": "Diagnóstico"},
            {"Produto": TXT(width=210), COL_HOME: CFG_HOME, COL_OUTRA: CFG_OUTRA, "Diagnóstico": TXT(width=300)},
            formatos={"Estoque a custo": "brl0"})
    note(f"Entra quem está visível no site, com estoque e sem venda em 90 dias ou com risco \"Encalhado\"/\"Sobreestoque\". Aqui a ordem se inverte: "
         f"o <strong>menos exposto vem primeiro</strong>, porque o primeiro empurrão é de graça — expor. Corte de visitas = mediana das páginas de "
         f"produto com visita nos últimos {DIAS_VISITAS} dias ({_n(mediana_visitas)}). Dias sem venda vazio = nunca vendeu.")

    # ═══ 10. CADASTRO ═══
    section_title("10. Cadastro — corrigir no Bling ou na Nuvemshop")
    problemas = {
        "Sem custo no Bling": g["fg_sem_custo"], "Sem papel": g["fg_sem_papel"], "Sem ciclo de vida (Curadoria)": g["fg_sem_ciclo"] & (g["ds_frente"] == "Curadoria"),
        "Sem peso no Bling": g["fg_sem_peso"], "Preço Bling ≠ Nuvemshop": g["fg_preco_divergente"], "Sem imagem na Nuvemshop": g["fg_sem_imagem"],
        "Fora da Nuvemshop": ~g["fg_na_nuvemshop"] & ~g["fg_componente_kit_visivel"],
        "Sem SEO na Nuvemshop": g["fl_sem_seo"] & g["fg_na_nuvemshop"],
        "Fotos sem texto alternativo": g["qt_imagens_sem_alt"] > 0,
        "Sem GTIN na Nuvemshop": g["qt_variacoes_sem_gtin_produto"] > 0,
    }
    render_cards([card(k, _n(v.sum()), "variações") for k, v in problemas.items()])
    escolha = st.multiselect("Problemas", options=list(problemas), default=["Sem custo no Bling"])
    so_ativos = st.toggle("Só variações com estoque no Bling ou visíveis no site", value=True)
    mask = pd.Series(False, index=g.index)
    for k in escolha:
        mask |= problemas[k]
    if so_ativos:
        mask &= g["fg_tem_estoque"] | g["fg_visivel_site"]
    cad = g[mask].copy()
    cad["problemas"] = [", ".join(k for k, v in problemas.items() if v.loc[i]) for i in cad.index]
    _tabela(cad.sort_values(["prioridade", "nm_produto_completo"], ascending=[False, True]),
            {"nm_produto_completo": "Variação", "home": COL_HOME, "cd_produto": "SKU", "ds_frente": "Frente", "qt_estoque_bling": COL_EST_BLING,
             "vl_preco_bling": "Preço no Bling", "vl_preco_de_nuvemshop": "Preço cheio na Nuvemshop", "problemas": "Problemas"},
            {"Variação": TXT(width=250), COL_HOME: CFG_HOME, "Problemas": TXT(width=260)},
            formatos={"Preço no Bling": "brl2", "Preço cheio na Nuvemshop": "brl2"})
    st.markdown("**Categorias do site visíveis e vazias** — o cliente clica no menu e cai numa página sem produto: ocultar a categoria ou preencher")
    _tabela(cats_vazias, {"nm_categoria": "Categoria", "nm_categoria_pai": "Dentro de", "qt_produtos": "Produtos (inclui não publicados)"},
            {"Categoria": TXT(width=240)})
    note("<strong>Sem custo</strong> é o mais caro: a margem de contribuição sai superestimada em todos os relatórios. <strong>Sem peso</strong> afeta o "
         "frete. <strong>Preço</strong> compara o preço cheio da Nuvemshop com o de venda do Bling (promoção fica de fora). <strong>SEO, texto "
         "alternativo e GTIN</strong> pesam na busca do Google e no Google Shopping. Papel e ciclo herdam do produto pai quando a variação está vazia. "
         "Componente de kit visível no site não conta como \"fora da Nuvemshop\".")

    # ═══ DE QUANDO SÃO OS DADOS ═══
    if fr:
        section_title("De quando são os dados desta página")
        detalhe_atualizacao(fr)
