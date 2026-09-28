"""Relatório Gestão de Produtos — Shibari Brasil (camada diária).

Página de trabalho: cada seção termina numa ação no Bling ou na Nuvemshop. Regras e definição de cada lista:
ver specs/gestao-produtos.md. Toda regra (visível, tem estoque, alerta de vitrine, oferta sem estoque) mora na
`tb_produto_gestao` do dbt; aqui só se filtra, agrupa por família e apresenta. Visitas vêm do GA4 (região US),
consultadas à parte e cruzadas no pandas pelo handle da página do produto.

VITRINE (28/09/2026): cada lista traz a EXPOSIÇÃO do produto no site (tb_produto_vitrine via tb_produto_gestao:
3 Home › 2 Vitrine › 1 Só categoria › 0 Fora do site) e é ordenada por ela — o mesmo problema é mais grave quanto
mais exposto o produto está (ex.: sem estoque na home é pior que sem estoque só na categoria).
"""
import re

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from common import bigquery as bq
from common.design import COLORS, inject_css, card, render_cards, section_title, note, plotly_layout, brl
from common.frescor import carregar_frescor, badge_atualizacao, detalhe_atualizacao
from reports.vendas_margem import _hoje_brt

TABELAS = ("tb_produto_gestao", "tb_produto_vitrine", "tb_estoque_movimento_dia", "tb_produto_teste", "tb_produto_ciclo_monitor")
EXTRATORES = ("bling_products", "bling_orders", "bling_purchases", "nuvemshop_products", "nuvemshop_storefront", "nuvemshop_orders")

DIAS_MOVIMENTO = 7          # janela da seção "O que mudou no estoque"
DIAS_TESTE_A_VENCER = 30    # decisão do Hugo (28/09/2026)
DIAS_VISITAS = 30           # janela das visitas (GA4)
PRECO_MAX_OFERTA = 60.0     # teto de preço dos candidatos a oferta (filtro de apresentação)
CORES_MOV = {"Zerou": COLORS["danger"], "Voltou": COLORS["success"], "Subiu": COLORS["info"], "Baixou": COLORS["text_muted"]}


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
              "qt_imagens_sem_alt", "qt_variacoes_sem_gtin_produto"]:
        g[c] = pd.to_numeric(g[c], errors="coerce")
    for c in ["fg_visivel_site", "fg_tem_estoque", "fg_estoque_divergente", "fg_sem_custo", "fg_sem_papel", "fg_sem_ciclo",
              "fg_sem_imagem", "fg_sem_peso", "fg_preco_divergente", "fg_em_promocao", "fg_na_nuvemshop", "fg_oferta_sem_estoque",
              "fg_familia_visivel", "fl_na_home", "fl_sem_seo"]:
        g[c] = g[c].fillna(False).astype(bool)
    for c in ["nr_nivel_exposicao", "qt_paginas_como_similar", "qt_paginas_como_complementar", "qt_imagens_sem_alt",
              "qt_variacoes_sem_gtin_produto"]:
        g[c] = g[c].fillna(0)
    g["exposicao"] = [_texto_exposicao(r) for r in g.itertuples()]
    mov["dt_posicao"] = pd.to_datetime(mov["dt_posicao"])
    visitas["qt_visitas"] = pd.to_numeric(visitas["qt_visitas"]).fillna(0)
    return {"g": g, "mov": mov, "teste": teste, "monitor": monitor, "visitas": visitas, "categorias": categorias}


def _limpa_titulo(t):
    return re.sub(r"[✦★☆•]+", "", str(t)).strip() if t is not None and t == t else ""


def _texto_exposicao(r):
    """Rótulo curto da exposição do produto no site (a regra do nível vem pronta do dbt)."""
    nivel = int(r.nr_nivel_exposicao or 0)
    if nivel >= 3:
        pos = f" ({int(r.nr_posicao_prateleira_principal)}º)" if r.nr_posicao_prateleira_principal == r.nr_posicao_prateleira_principal else ""
        outras = int(len(str(r.ds_prateleiras_home).split(", ")) - 1) if isinstance(r.ds_prateleiras_home, str) else 0
        return f"Home · {_limpa_titulo(r.nm_prateleira_principal)}{pos}" + (f" +{outras}" if outras > 0 else "")
    if nivel == 2:
        partes = []
        if isinstance(r.ds_categorias_vitrine, str) and r.ds_categorias_vitrine:
            partes.append(r.ds_categorias_vitrine)
        n = int(r.qt_paginas_como_similar + r.qt_paginas_como_complementar)
        if n:
            partes.append(f"sugerido em {n} página{'s' if n > 1 else ''}")
        return "Vitrine · " + ", ".join(partes)
    return "Só categoria" if nivel == 1 else "Fora do site"


EXPOSICAO_CFG = st.column_config.TextColumn("Exposição", width=210,
                                            help="Onde o produto aparece no site: Home › Vitrine (categoria de vitrine ou sugerido em outro "
                                                 "produto) › Só categoria › Fora do site. Quanto mais exposto, mais grave o problema.")


def _familias(g, visitas):
    """Agrupa os SKUs no produto como aparece na loja (família) e cruza com as visitas pela página.
    Ordena por exposição antes de agrupar: o `first` pega a exposição mais alta da família."""
    g = g.sort_values("nr_nivel_exposicao", ascending=False)
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
        nivel=("nr_nivel_exposicao", "max"), exposicao=("exposicao", "first"), alerta_exposicao=("ds_alerta_exposicao", "first"),
        na_home=("fl_na_home", "any"), ranking=("nr_ranking_mais_vendidos", "min"),
    )
    v = visitas.groupby("ds_url_produto", as_index=False)["qt_visitas"].sum().rename(columns={"ds_url_produto": "url", "qt_visitas": "visitas"})
    f = f.merge(v, on="url", how="left")
    f["visitas"] = f["visitas"].fillna(0).astype(int)
    return f


def _n(v):
    return f"{int(v):,}".replace(",", ".")


def _tabela(df, colunas, config=None, altura=None):
    if df.empty:
        st.success("Nada nesta lista agora.")
        return
    kw = {"height": altura} if altura else {}
    t = df[list(colunas)].copy()
    for c in t.columns:
        if pd.api.types.is_object_dtype(t[c]) or pd.api.types.is_string_dtype(t[c]):
            t[c] = t[c].astype(object).where(t[c].notna(), "—")
    st.dataframe(t.rename(columns=colunas), hide_index=True, use_container_width=True, column_config=config or {}, **kw)


NUM = st.column_config.NumberColumn
TXT = st.column_config.TextColumn
BRL0 = NUM(format="R$ %.0f")
BRL2 = NUM(format="R$ %.2f")


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
        <div class="report-meta">Site × estoque, movimentos, teste, ciclo de vida, ofertas, giro e cadastro — cada problema pesado pela exposição do produto no site · fontes: tb_produto_gestao, tb_produto_vitrine, GA4</div>
      </div>
      {badge_atualizacao(fr) if fr else f'<div class="report-badge">Hoje: <strong>{hoje.strftime("%d/%m/%Y")}</strong></div>'}
    </div>
    """)
    if fr:
        detalhe_atualizacao(fr)

    frente = st.selectbox("Frente", options=["Todas", "Shibari", "Curadoria"])
    g = dados["g"]
    if frente != "Todas":
        g = g[g["ds_frente"] == frente]
    if g.empty:
        st.info("Sem produtos para a frente escolhida.")
        return
    f = _familias(g, dados["visitas"])
    mediana_visitas = float(dados["visitas"].loc[dados["visitas"]["qt_visitas"] > 0, "qt_visitas"].median() or 0)

    # listas usadas nos cards e nas seções
    vis_sem_est = f[f["visivel"] & (f["estoque"] <= 0)].sort_values(["nivel", "visitas", "pecas_90d"], ascending=False)
    fora_site = g[g["ds_alerta_vitrine"].isin(["Com estoque e fora da Nuvemshop", "Com estoque e produto não publicado",
                                               "Com estoque e variação oculta"])]
    diverg = g[g["fg_estoque_divergente"]].sort_values("nr_nivel_exposicao", ascending=False)
    ofertas = g[g["ds_tipo_oferta"].notna()]
    mov = dados["mov"].merge(g[["cd_produto_bling", "nm_produto_completo", "ds_frente", "fg_visivel_site", "fl_publicado_nuvemshop",
                                "nr_nivel_exposicao", "exposicao"]],
                             on="cd_produto_bling", how="inner")
    teste = dados["teste"].copy()
    if not teste.empty:
        teste["dt_limite_decisao"] = pd.to_datetime(teste["dt_limite_decisao"])
        teste["dias_ate_prazo"] = (teste["dt_limite_decisao"] - pd.Timestamp(hoje)).dt.days
    ativos_teste = teste[teste["ds_status_final"].isna()] if not teste.empty else teste
    a_decidir = ativos_teste[(ativos_teste["dias_ate_prazo"] <= DIAS_TESTE_A_VENCER) | ativos_teste["fg_criterio_atingido"].fillna(False)] \
        if not ativos_teste.empty else ativos_teste
    sem_custo_est = g[g["fg_sem_custo"] & (g["fg_tem_estoque"] | g["fg_visivel_site"])]
    expo_fam = f.set_index("cd_produto_bling_familia")[["nivel", "exposicao", "alerta_exposicao"]]
    conflitos = f[f["alerta_exposicao"].notna()]
    cats = dados["categorias"]
    cats_vazias = cats[cats["fl_visivel_vazia"].fillna(False).astype(bool)] if not cats.empty else cats
    n_home_sem = int((vis_sem_est["nivel"] >= 3).sum())
    n_vit_sem = int((vis_sem_est["nivel"] == 2).sum())

    # ═══ 0. ONDE AGIR HOJE ═══
    section_title("Onde agir hoje")
    render_cards([
        card("Visíveis sem estoque", _n(len(vis_sem_est)), f"{n_home_sem} na home · {n_vit_sem} em vitrine · {len(vis_sem_est) - n_home_sem - n_vit_sem} só na categoria",
             variant="bad" if len(vis_sem_est) else "ok"),
        card("Com estoque fora do site", _n(len(fora_site)), "variações paradas por cadastro", variant="bad" if len(fora_site) else "ok"),
        card("Ofertas sem estoque", _n(int(ofertas["fg_oferta_sem_estoque"].sum())), f"de {_n(len(ofertas))} variações em oferta",
             variant="bad" if ofertas["fg_oferta_sem_estoque"].any() else "ok"),
        card("Testes a decidir", _n(len(a_decidir)), f"prazo em até {DIAS_TESTE_A_VENCER} dias ou critério atingido",
             variant="warn" if len(a_decidir) else "ok"),
        card(f"Zeraram em {DIAS_MOVIMENTO} dias", _n((mov["ds_movimento"] == "Zerou").sum()), "variações que ficaram sem estoque",
             variant="warn" if (mov["ds_movimento"] == "Zerou").any() else "ok"),
        card("Sem custo", _n(len(sem_custo_est)), "variações com estoque ou visíveis — margem superestimada",
             variant="warn" if len(sem_custo_est) else "ok"),
        card("Conflitos de vitrine", _n(len(conflitos) + len(cats_vazias)),
             f"{len(conflitos)} de ciclo de vida × vitrine · {len(cats_vazias)} categoria(s) visível(is) vazia(s)",
             variant="warn" if len(conflitos) + len(cats_vazias) else "ok"),
    ])
    note("Cada card é uma fila das seções abaixo, na ordem em que a ação é mais urgente. Vermelho = o cliente está vendo algo errado agora. "
         "Dentro de cada lista, o produto <strong>mais exposto no site vem primeiro</strong> (Home › Vitrine › Só categoria): o mesmo problema pesa "
         "mais numa prateleira da home do que escondido numa categoria.")

    # ═══ 1. SITE × ESTOQUE ═══
    section_title("1. Site × estoque — o que o cliente está vendo")
    st.markdown("**Visíveis sem nenhum estoque** — na home: tirar da prateleira ou repor já; no resto: ocultar ou repor")
    _tabela(vis_sem_est, {"produto": "Produto", "exposicao": "Exposição", "frente": "Frente", "skus": "Variações", "compra_pendente": "Compra pendente",
                          "visitas": f"Visitas {DIAS_VISITAS}d", "pecas_90d": "Peças 90d", "ciclo": "Ciclo"},
            {"Produto": TXT(width=240), "Exposição": EXPOSICAO_CFG})
    st.markdown("**Com estoque e fora do site** — publicar o produto, mostrar a variação ou cadastrar na Nuvemshop")
    _tabela(fora_site.sort_values("qt_estoque_bling", ascending=False),
            {"nm_produto_completo": "Variação", "cd_produto": "SKU", "qt_estoque_bling": "Estoque", "ds_alerta_vitrine": "Situação",
             "qt_pecas_90d": "Peças 90d"}, {"Variação": TXT(width=300), "Situação": TXT(width=240)})
    st.markdown("**Estoque diferente entre Bling e Nuvemshop** — sincronizar")
    _tabela(diverg, {"nm_produto_completo": "Variação", "exposicao": "Exposição", "cd_produto": "SKU", "qt_estoque_bling": "Bling",
                     "qt_estoque_nuvemshop": "Nuvemshop", "fg_visivel_site": "Visível"}, {"Variação": TXT(width=280), "Exposição": EXPOSICAO_CFG})
    esgot = g[(g["ds_alerta_vitrine"] == "Visível sem estoque") & (g["qt_estoque_familia"] > 0)]
    sem_ctrl = g[g["ds_alerta_vitrine"] == "Visível, sem estoque e vendendo sem controle"]
    with st.expander(f"Variações esgotadas em produtos com estoque ({len(esgot)}) e vendendo sem controle de estoque ({len(sem_ctrl)})"):
        _tabela(pd.concat([sem_ctrl, esgot]), {"nm_produto_completo": "Variação", "ds_alerta_vitrine": "Situação",
                                               "qt_estoque_familia": "Estoque do produto", "qt_pecas_90d": "Peças 90d"},
                {"Variação": TXT(width=300)})
    note("<strong>Visível</strong> = produto publicado <em>e</em> variação visível na Nuvemshop (são dois interruptores diferentes no cadastro). "
         "<strong>Estoque</strong> = Bling, a fonte do estoque. Variação esgotada dentro de um produto com estoque é normal (o site mostra a opção como "
         "esgotada) — fica no expansor, com prioridade baixa. \"Vendendo sem controle\" = a variação está com o controle de estoque desligado na "
         "Nuvemshop, então ela aceita pedido mesmo com o Bling zerado. <strong>Exposição \"sugerido em N páginas\"</strong> = o produto aparece em "
         "\"Produtos similares\" ou \"Para comprar com esse produto\" na página de outros produtos — zerado, ele leva o cliente a um beco sem saída.")

    # ═══ 2. MOVIMENTOS ═══
    section_title(f"2. O que mudou no estoque — últimos {DIAS_MOVIMENTO} dias")
    if mov.empty:
        st.info("Nenhuma variação mudou de estoque na janela.")
    else:
        d = mov.groupby([mov["dt_posicao"].dt.date, "ds_movimento"]).size().unstack(fill_value=0)
        fig = go.Figure()
        for m in ["Zerou", "Voltou", "Subiu", "Baixou"]:
            if m in d.columns:
                fig.add_bar(x=pd.to_datetime(d.index), y=d[m], name=m, marker_color=CORES_MOV[m],
                            hovertemplate=m + " · %{x|%d/%m}: %{y}<extra></extra>")
        plotly_layout(fig, height=260, barmode="stack", xaxis=dict(tickformat="%d/%m", dtick=86400000, gridcolor=COLORS["grid"]),
                      yaxis=dict(title="variações", gridcolor=COLORS["grid"]))
        with st.container(border=True):
            st.plotly_chart(fig, use_container_width=True)

        def _acao(r):
            if r.ds_movimento == "Zerou":
                if not r.fg_visivel_site:
                    return "Ok (já oculta)"
                return "Tirar da home ou repor já" if r.nr_nivel_exposicao >= 3 else "Ocultar ou repor"
            if r.ds_movimento == "Voltou":
                if not r.fg_visivel_site:
                    return "Publicar"
                return "Divulgar a volta" if r.nr_nivel_exposicao >= 2 else "Divulgar a volta e colocar em vitrine"
            if r.ds_movimento == "Subiu":
                return "Conferir publicação e preço" if not r.fg_visivel_site else "Entrada registrada"
            return "—"
        mov = mov.assign(acao=[_acao(r) for r in mov.itertuples()]).sort_values(["dt_posicao", "nr_nivel_exposicao", "ds_movimento"],
                                                                               ascending=[False, False, True])
        so_acao = st.toggle("Mostrar também as baixas (vendas/ajustes sem zerar)", value=False)
        m2 = mov if so_acao else mov[mov["ds_movimento"] != "Baixou"]
        _tabela(m2, {"dt_posicao": "Dia", "nm_produto_completo": "Variação", "ds_movimento": "Movimento", "qt_estoque_anterior": "Antes",
                     "qt_estoque_atual": "Agora", "exposicao": "Exposição", "acao": "Ação"},
                {"Dia": st.column_config.DateColumn(format="DD/MM"), "Variação": TXT(width=280), "Exposição": EXPOSICAO_CFG})
    note("Foto diária do estoque do Bling (desde 02/07/2026). <strong>Zerou</strong>: tinha e ficou sem · <strong>Voltou</strong>: estava sem e voltou · "
         "<strong>Subiu</strong>: tinha e aumentou (entrada) · <strong>Baixou</strong>: diminuiu sem zerar. Se faltar a foto de um dia, a comparação é "
         "com a última foto disponível.")

    # ═══ 3. TESTE ═══
    section_title("3. Produtos em teste (Curadoria)")
    if teste.empty:
        st.info("Nenhum produto em teste.")
    else:
        at = ativos_teste.copy()
        at = at.join(expo_fam, on="cd_produto_bling_familia")
        at["alerta"] = [" · ".join(x for x in [
                            "Critério atingido — decidir" if c else "",
                            f"Vence em {int(dd)} dias" if pd.notna(dd) and dd <= DIAS_TESTE_A_VENCER else "",
                            "Sem vitrine — o teste precisa de exposição" if pd.notna(nv) and nv <= 1 else ""] if x)
                        for c, dd, nv in zip(at["fg_criterio_atingido"].fillna(False), at["dias_ate_prazo"], at["nivel"])]
        at = at.sort_values(["alerta", "dias_ate_prazo"], ascending=[False, True], na_position="last")
        _tabela(at, {"nm_produto": "Produto", "exposicao": "Exposição", "ds_papel_pretendido": "Papel pretendido", "dt_ini_teste": "Início", "qt_dias_disponiveis": "Dias c/ estoque",
                     "dt_limite_decisao": "Prazo", "dias_ate_prazo": "Dias até o prazo", "qt_pedidos": "Pedidos", "vl_payback": "Payback",
                     "ds_status": "Status recomendado", "alerta": "Alerta"},
                {"Produto": TXT(width=220), "Prazo": st.column_config.DateColumn(format="DD/MM/YY"),
                 "Início": st.column_config.DateColumn(format="DD/MM/YY"), "Payback": NUM(format="%.2f×"), "Status recomendado": TXT(width=220),
                 "Exposição": EXPOSICAO_CFG, "Alerta": TXT(width=260)})
        saiu = teste[teste["ds_status_final"].notna()]
        if not saiu.empty:
            st.markdown("**Saíram do teste nos últimos 90 dias**")
            _tabela(saiu, {"nm_produto": "Produto", "ds_status_final": "Resultado", "dt_saida": "Saída", "qt_pedidos": "Pedidos", "vl_payback": "Payback"})
    note(f"Critérios por papel aprovados em 25/09/2026 (Core 180 dias e ≥ 7 pedidos; Complementar 120 dias e payback; Impulso 90 dias e attach rate ≥ 3%). "
         f"A contagem só corre com estoque disponível. <strong>Alerta</strong>: prazo em até {DIAS_TESTE_A_VENCER} dias ou critério atingido antes do prazo. "
         "Payback = margem de contribuição acumulada ÷ investimento no lote. O status é recomendação: a troca de ciclo é manual no Bling. "
         "<strong>Sem vitrine</strong>: produto em teste que só aparece na categoria não tem chance justa de vender — colocar numa prateleira da home, "
         "numa categoria de vitrine ou como sugestão em produtos parecidos.")

    # ═══ 4. EM SAÍDA / DIFICULDADE ═══
    section_title("4. Em Saída e Dificuldade de Reposição (Curadoria)")
    mon = dados["monitor"].join(expo_fam, on="cd_produto_bling_familia") if not dados["monitor"].empty else dados["monitor"]
    if mon.empty:
        st.info("Nenhum produto nesses ciclos.")
    else:
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Em Saída** — liquidar o estoque")
            _tabela(mon[mon["ds_ciclo"] == "Em Saída"].sort_values("nivel", ascending=False),
                    {"nm_produto": "Produto", "exposicao": "Exposição", "qt_estoque": "Estoque", "qt_vendas_60d": "Vendas 60d",
                     "qt_dias_para_zerar": "Dias p/ zerar", "ds_status_saida": "Status", "ds_acao_saida": "Ação"},
                    {"Produto": TXT(width=180), "Ação": TXT(width=200), "Exposição": EXPOSICAO_CFG})
        with c2:
            st.markdown("**Dificuldade de Reposição** — buscar alternativa")
            _tabela(mon[mon["ds_ciclo"] == "Dificuldade de Reposição"],
                    {"nm_produto": "Produto", "exposicao": "Exposição", "qt_estoque": "Estoque", "qt_dias_ruptura": "Dias sem estoque",
                     "vl_venda_perdida_estimada": "Venda perdida (MC)", "ds_status_reposicao": "Status"},
                    {"Produto": TXT(width=180), "Venda perdida (MC)": BRL0, "Exposição": EXPOSICAO_CFG})
        conf_saida = mon[mon["alerta_exposicao"].notna()] if "alerta_exposicao" in mon else mon.iloc[0:0]
        for r in conf_saida.itertuples():
            note(f"<strong>{r.nm_produto}</strong>: {r.alerta_exposicao}.", variant="warn")
    note("Em Saída: dias para zerar = estoque ÷ ritmo de venda dos últimos 60 dias; vazio = sem venda no período. Venda perdida = dias sem estoque × ritmo "
         "anterior × margem de contribuição por unidade (estimativa). Produto Em Saída numa prateleira de <em>novidade</em> passa a mensagem errada: "
         "o lugar dele é liquidação/oferta.")

    # ═══ 5. OFERTAS ═══
    section_title("5. Ofertas (Cashing)")
    if ofertas.empty:
        st.info("Nenhum produto com o campo \"Tipo de oferta\" preenchido no Bling.")
    else:
        of = ofertas.sort_values(["fg_oferta_sem_estoque", "qt_pecas_90d"], ascending=[False, False])
        of = of.assign(situacao=of["fg_oferta_sem_estoque"].map({True: "Sem estoque — trocar a oferta", False: "Ok"}))
        _tabela(of, {"nm_produto_completo": "Variação", "ds_tipo_oferta": "Tipo de oferta", "exposicao": "Exposição", "qt_estoque_bling": "Estoque",
                     "qt_pecas_30d": "Peças 30d", "qt_pecas_90d": "Peças 90d", "vl_margem_contribuicao_90d": "Margem 90d", "situacao": "Situação"},
                {"Variação": TXT(width=240), "Margem 90d": BRL0, "Situação": TXT(width=200), "Exposição": EXPOSICAO_CFG})
    cand = f[f["visivel"] & (f["estoque"] > 0) & f["oferta"].isna() & f["papel"].isin(["Impulso", "Complementar"])
             & (f["preco_por"] <= PRECO_MAX_OFERTA)].sort_values("estoque_custo", ascending=False)
    with st.expander(f"Candidatos a oferta ({len(cand)})"):
        _tabela(cand, {"produto": "Produto", "exposicao": "Exposição", "papel": "Papel", "preco_por": "Preço", "estoque": "Estoque",
                       "estoque_custo": "Estoque a custo", "pecas_90d": "Peças 90d", "visitas": f"Visitas {DIAS_VISITAS}d"},
                {"Produto": TXT(width=220), "Preço": BRL2, "Estoque a custo": BRL0, "Exposição": EXPOSICAO_CFG})
    note(f"Ofertas = campo <strong>Tipo de oferta</strong> do cadastro do Bling (order bump e upsell no Cashing). Oferta sem estoque mostra ao cliente algo "
         f"que ele não consegue levar — trocar no Cashing na hora. Candidatos: visíveis, com estoque, sem oferta, papel Impulso ou Complementar e preço até "
         f"{brl(PRECO_MAX_OFERTA, 0)}, do maior estoque parado para o menor.")

    # ═══ 6. EMPURRÃOZINHO ═══
    section_title("6. Empurrãozinho — produtos com estoque que giram devagar")
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
        card("Produtos girando devagar", _n(len(lento)), "visíveis e com estoque"),
        card("Estoque parado a custo", brl(lento["estoque_custo"].sum(), 0), "soma do estoque desses produtos pelo custo de cadastro"),
        card("Sem vitrine", _n(sem_vit.sum()), "só aparecem na categoria — primeiro passo: expor"),
        card("Pouca visita", _n((~sem_vit & (lento["visitas"] < mediana_visitas)).sum()), f"em vitrine, abaixo de {_n(mediana_visitas)} visitas em {DIAS_VISITAS} dias"),
        card("Visitam e não compram", _n((~sem_vit & (lento["visitas"] >= mediana_visitas)).sum()), "problema de página, preço ou foto"),
    ])
    _tabela(lento, {"produto": "Produto", "exposicao": "Exposição", "frente": "Frente", "estoque": "Estoque", "estoque_custo": "Estoque a custo", "visitas": f"Visitas {DIAS_VISITAS}d",
                    "pecas_90d": "Peças 90d", "dias_sem_venda": "Dias sem venda", "risco": "Risco", "diagnostico": "Diagnóstico"},
            {"Produto": TXT(width=220), "Estoque a custo": BRL0, "Diagnóstico": TXT(width=300), "Exposição": EXPOSICAO_CFG})
    note(f"Entra quem está visível, com estoque e sem venda em 90 dias ou com risco \"Encalhado\"/\"Sobreestoque\". O corte de visitas é a mediana das páginas "
         f"de produto com visita nos últimos {DIAS_VISITAS} dias ({_n(mediana_visitas)}). Aqui a lógica se inverte: o <strong>menos exposto vem primeiro</strong>, "
         "porque o primeiro empurrão é de graça — <strong>Sem vitrine</strong>: levar para a home, uma categoria de vitrine ou como sugestão em produtos parecidos. "
         "<strong>Pouca visita</strong> mesmo exposto: divulgar fora do site. <strong>Visitam e não compram</strong>: o problema está na página (preço, fotos, "
         "descrição). Dias sem venda vazio = nunca vendeu.")

    # ═══ 7. PROCURA SEM ESTOQUE ═══
    section_title("7. Procura sem estoque — prioridade de reposição")
    procura = f[(f["estoque"] <= 0) & ((f["visitas"] > 0) | (f["pecas_90d"] > 0))].sort_values(["nivel", "visitas", "pecas_90d"], ascending=False)
    _tabela(procura, {"produto": "Produto", "exposicao": "Exposição", "frente": "Frente", "visitas": f"Visitas {DIAS_VISITAS}d", "pecas_90d": "Peças 90d",
                      "ranking": "Ranking mais vendidos", "compra_pendente": "Compra pendente", "fornecedor": "Fornecedor", "ciclo": "Ciclo"},
            {"Produto": TXT(width=240), "Exposição": EXPOSICAO_CFG})
    note("Produtos zerados que as pessoas continuam procurando (visitas no site) ou que venderam nos últimos 90 dias, <strong>do mais exposto para o menos</strong>: "
         "zerado e ainda em vitrine é demanda sendo empurrada para um produto que não dá para comprar. Ranking = posição no \"mais vendidos\" da Nuvemshop. "
         "Produto Em Saída ou Descontinuado não se repõe: nesse caso, tirar da vitrine e ocultar.")

    # ═══ 8. CADASTRO ═══
    section_title("8. Cadastro — corrigir no Bling ou na Nuvemshop")
    problemas = {
        "Sem custo": g["fg_sem_custo"], "Sem papel": g["fg_sem_papel"], "Sem ciclo (Curadoria)": g["fg_sem_ciclo"] & (g["ds_frente"] == "Curadoria"),
        "Sem peso": g["fg_sem_peso"], "Preço Bling ≠ Nuvemshop": g["fg_preco_divergente"], "Sem imagem na Nuvemshop": g["fg_sem_imagem"],
        "Fora da Nuvemshop": ~g["fg_na_nuvemshop"],
        "Sem SEO (título/descrição)": g["fl_sem_seo"] & g["fg_na_nuvemshop"],
        "Fotos sem texto alternativo": g["qt_imagens_sem_alt"] > 0,
        "Sem GTIN na Nuvemshop": g["qt_variacoes_sem_gtin_produto"] > 0,
    }
    render_cards([card(k, _n(v.sum()), "variações") for k, v in problemas.items()])
    escolha = st.multiselect("Problemas", options=list(problemas), default=["Sem custo"])
    so_ativos = st.toggle("Só variações com estoque ou visíveis", value=True)
    mask = pd.Series(False, index=g.index)
    for k in escolha:
        mask |= problemas[k]
    if so_ativos:
        mask &= g["fg_tem_estoque"] | g["fg_visivel_site"]
    cad = g[mask].copy()
    cad["problemas"] = [", ".join(k for k, v in problemas.items() if v.loc[i]) for i in cad.index]
    _tabela(cad.sort_values(["nr_nivel_exposicao", "nm_produto_completo"], ascending=[False, True]),
            {"nm_produto_completo": "Variação", "exposicao": "Exposição", "cd_produto": "SKU", "ds_frente": "Frente", "qt_estoque_bling": "Estoque",
             "vl_preco_bling": "Preço Bling", "vl_preco_de_nuvemshop": "Preço Nuvemshop", "problemas": "Problemas"},
            {"Variação": TXT(width=260), "Preço Bling": BRL2, "Preço Nuvemshop": BRL2, "Problemas": TXT(width=260), "Exposição": EXPOSICAO_CFG})
    st.markdown("**Categorias da loja visíveis e vazias** — o cliente clica no menu e cai numa página sem produto: ocultar a categoria ou preencher")
    _tabela(cats_vazias, {"nm_categoria": "Categoria", "nm_categoria_pai": "Dentro de", "qt_produtos": "Produtos (inclui não publicados)"},
            {"Categoria": TXT(width=240)})
    note("<strong>Sem custo</strong> é o mais caro: a margem de contribuição do produto sai superestimada em todos os relatórios. <strong>Sem peso</strong> "
         "afeta o cálculo do frete. <strong>Preço</strong> compara o preço cheio da Nuvemshop com o de venda do Bling (promoção fica de fora). Papel e ciclo "
         "herdam do produto pai quando a variação está vazia. <strong>SEO, texto alternativo das fotos e GTIN</strong> pesam na busca do Google e no Google "
         "Shopping (Merchant Center) — prioridade para quem está mais exposto (lista ordenada pela exposição).")
