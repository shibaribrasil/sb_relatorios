"""Relatório Gestão de Produtos — Shibari Brasil (camada diária).

Página de trabalho: cada seção termina numa ação no Bling ou na Nuvemshop. Regras e definição de cada lista:
ver specs/gestao-produtos.md. Toda regra (visível, tem estoque, alerta de vitrine, oferta sem estoque) mora na
`tb_produto_gestao` do dbt; aqui só se filtra, agrupa por família e apresenta. Visitas vêm do GA4 (região US),
consultadas à parte e cruzadas no pandas pelo handle da página do produto.
"""
import re

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from common import bigquery as bq
from common.design import COLORS, inject_css, card, render_cards, section_title, note, plotly_layout, brl
from reports.vendas_margem import _hoje_brt

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
              "vl_custo_cadastro", "vl_preco_bling", "vl_preco_de_nuvemshop", "vl_preco_por_nuvemshop", "qt_cobertura_atual"]:
        g[c] = pd.to_numeric(g[c], errors="coerce")
    for c in ["fg_visivel_site", "fg_tem_estoque", "fg_estoque_divergente", "fg_sem_custo", "fg_sem_papel", "fg_sem_ciclo",
              "fg_sem_imagem", "fg_sem_peso", "fg_preco_divergente", "fg_em_promocao", "fg_na_nuvemshop", "fg_oferta_sem_estoque",
              "fg_familia_visivel"]:
        g[c] = g[c].fillna(False).astype(bool)
    mov["dt_posicao"] = pd.to_datetime(mov["dt_posicao"])
    visitas["qt_visitas"] = pd.to_numeric(visitas["qt_visitas"]).fillna(0)
    return {"g": g, "mov": mov, "teste": teste, "monitor": monitor, "visitas": visitas}


def _familias(g, visitas):
    """Agrupa os SKUs no produto como aparece na loja (família) e cruza com as visitas pela página."""
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
    st.html(f"""
    <div class="report-header">
      <div>
        <div class="report-brand">shibari brasil · camada diária</div>
        <div class="report-title">Gestão de <span>Produtos</span></div>
        <div class="report-meta">Site × estoque, movimentos, teste, ciclo de vida, ofertas, giro e cadastro · fontes: tb_produto_gestao, GA4</div>
      </div>
      <div class="report-badge">Hoje: <strong>{hoje.strftime("%d/%m/%Y")}</strong></div>
    </div>
    """)

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
    vis_sem_est = f[f["visivel"] & (f["estoque"] <= 0)].sort_values(["visitas", "pecas_90d"], ascending=False)
    fora_site = g[g["ds_alerta_vitrine"].isin(["Com estoque e fora da Nuvemshop", "Com estoque e produto não publicado",
                                               "Com estoque e variação oculta"])]
    diverg = g[g["fg_estoque_divergente"]]
    ofertas = g[g["ds_tipo_oferta"].notna()]
    mov = dados["mov"].merge(g[["cd_produto_bling", "nm_produto_completo", "ds_frente", "fg_visivel_site", "fl_publicado_nuvemshop"]],
                             on="cd_produto_bling", how="inner")
    teste = dados["teste"].copy()
    if not teste.empty:
        teste["dt_limite_decisao"] = pd.to_datetime(teste["dt_limite_decisao"])
        teste["dias_ate_prazo"] = (teste["dt_limite_decisao"] - pd.Timestamp(hoje)).dt.days
    ativos_teste = teste[teste["ds_status_final"].isna()] if not teste.empty else teste
    a_decidir = ativos_teste[(ativos_teste["dias_ate_prazo"] <= DIAS_TESTE_A_VENCER) | ativos_teste["fg_criterio_atingido"].fillna(False)] \
        if not ativos_teste.empty else ativos_teste
    sem_custo_est = g[g["fg_sem_custo"] & (g["fg_tem_estoque"] | g["fg_visivel_site"])]

    # ═══ 0. ONDE AGIR HOJE ═══
    section_title("Onde agir hoje")
    render_cards([
        card("Visíveis sem estoque", _n(len(vis_sem_est)), "produtos no site sem nenhuma unidade", variant="bad" if len(vis_sem_est) else "ok"),
        card("Com estoque fora do site", _n(len(fora_site)), "variações paradas por cadastro", variant="bad" if len(fora_site) else "ok"),
        card("Ofertas sem estoque", _n(int(ofertas["fg_oferta_sem_estoque"].sum())), f"de {_n(len(ofertas))} variações em oferta",
             variant="bad" if ofertas["fg_oferta_sem_estoque"].any() else "ok"),
        card("Testes a decidir", _n(len(a_decidir)), f"prazo em até {DIAS_TESTE_A_VENCER} dias ou critério atingido",
             variant="warn" if len(a_decidir) else "ok"),
        card(f"Zeraram em {DIAS_MOVIMENTO} dias", _n((mov["ds_movimento"] == "Zerou").sum()), "variações que ficaram sem estoque",
             variant="warn" if (mov["ds_movimento"] == "Zerou").any() else "ok"),
        card("Sem custo", _n(len(sem_custo_est)), "variações com estoque ou visíveis — margem superestimada",
             variant="warn" if len(sem_custo_est) else "ok"),
    ])
    note("Cada card é uma fila das seções abaixo, na ordem em que a ação é mais urgente. Vermelho = o cliente está vendo algo errado agora.")

    # ═══ 1. SITE × ESTOQUE ═══
    section_title("1. Site × estoque — o que o cliente está vendo")
    st.markdown("**Visíveis sem nenhum estoque** — ocultar ou repor")
    _tabela(vis_sem_est, {"produto": "Produto", "frente": "Frente", "skus": "Variações", "compra_pendente": "Compra pendente",
                          "visitas": f"Visitas {DIAS_VISITAS}d", "pecas_90d": "Peças 90d", "ciclo": "Ciclo"},
            {"Produto": TXT(width=260)})
    st.markdown("**Com estoque e fora do site** — publicar o produto, mostrar a variação ou cadastrar na Nuvemshop")
    _tabela(fora_site.sort_values("qt_estoque_bling", ascending=False),
            {"nm_produto_completo": "Variação", "cd_produto": "SKU", "qt_estoque_bling": "Estoque", "ds_alerta_vitrine": "Situação",
             "qt_pecas_90d": "Peças 90d"}, {"Variação": TXT(width=300), "Situação": TXT(width=240)})
    st.markdown("**Estoque diferente entre Bling e Nuvemshop** — sincronizar")
    _tabela(diverg, {"nm_produto_completo": "Variação", "cd_produto": "SKU", "qt_estoque_bling": "Bling", "qt_estoque_nuvemshop": "Nuvemshop",
                     "fg_visivel_site": "Visível"}, {"Variação": TXT(width=300)})
    esgot = g[(g["ds_alerta_vitrine"] == "Visível sem estoque") & (g["qt_estoque_familia"] > 0)]
    sem_ctrl = g[g["ds_alerta_vitrine"] == "Visível, sem estoque e vendendo sem controle"]
    with st.expander(f"Variações esgotadas em produtos com estoque ({len(esgot)}) e vendendo sem controle de estoque ({len(sem_ctrl)})"):
        _tabela(pd.concat([sem_ctrl, esgot]), {"nm_produto_completo": "Variação", "ds_alerta_vitrine": "Situação",
                                               "qt_estoque_familia": "Estoque do produto", "qt_pecas_90d": "Peças 90d"},
                {"Variação": TXT(width=300)})
    note("<strong>Visível</strong> = produto publicado <em>e</em> variação visível na Nuvemshop (são dois interruptores diferentes no cadastro). "
         "<strong>Estoque</strong> = Bling, a fonte do estoque. Variação esgotada dentro de um produto com estoque é normal (o site mostra a opção como "
         "esgotada) — fica no expansor, com prioridade baixa. \"Vendendo sem controle\" = a variação está com o controle de estoque desligado na "
         "Nuvemshop, então ela aceita pedido mesmo com o Bling zerado.")

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
                return "Ocultar ou repor" if r.fg_visivel_site else "Ok (já oculta)"
            if r.ds_movimento == "Voltou":
                return "Divulgar a volta" if r.fg_visivel_site else "Publicar"
            if r.ds_movimento == "Subiu":
                return "Conferir publicação e preço" if not r.fg_visivel_site else "Entrada registrada"
            return "—"
        mov = mov.assign(acao=[_acao(r) for r in mov.itertuples()]).sort_values(["dt_posicao", "ds_movimento"], ascending=[False, True])
        so_acao = st.toggle("Mostrar também as baixas (vendas/ajustes sem zerar)", value=False)
        m2 = mov if so_acao else mov[mov["ds_movimento"] != "Baixou"]
        _tabela(m2, {"dt_posicao": "Dia", "nm_produto_completo": "Variação", "ds_movimento": "Movimento", "qt_estoque_anterior": "Antes",
                     "qt_estoque_atual": "Agora", "fg_visivel_site": "Visível", "acao": "Ação"},
                {"Dia": st.column_config.DateColumn(format="DD/MM"), "Variação": TXT(width=300)})
    note("Foto diária do estoque do Bling (desde 02/07/2026). <strong>Zerou</strong>: tinha e ficou sem · <strong>Voltou</strong>: estava sem e voltou · "
         "<strong>Subiu</strong>: tinha e aumentou (entrada) · <strong>Baixou</strong>: diminuiu sem zerar. Se faltar a foto de um dia, a comparação é "
         "com a última foto disponível.")

    # ═══ 3. TESTE ═══
    section_title("3. Produtos em teste (Curadoria)")
    if teste.empty:
        st.info("Nenhum produto em teste.")
    else:
        at = ativos_teste.copy()
        at["alerta"] = [("Critério atingido — decidir" if c else "") or (f"Vence em {int(dd)} dias" if pd.notna(dd) and dd <= DIAS_TESTE_A_VENCER else "")
                        for c, dd in zip(at["fg_criterio_atingido"].fillna(False), at["dias_ate_prazo"])]
        at = at.sort_values(["alerta", "dias_ate_prazo"], ascending=[False, True], na_position="last")
        _tabela(at, {"nm_produto": "Produto", "ds_papel_pretendido": "Papel pretendido", "dt_ini_teste": "Início", "qt_dias_disponiveis": "Dias c/ estoque",
                     "dt_limite_decisao": "Prazo", "dias_ate_prazo": "Dias até o prazo", "qt_pedidos": "Pedidos", "vl_payback": "Payback",
                     "ds_status": "Status recomendado", "alerta": "Alerta"},
                {"Produto": TXT(width=220), "Prazo": st.column_config.DateColumn(format="DD/MM/YY"),
                 "Início": st.column_config.DateColumn(format="DD/MM/YY"), "Payback": NUM(format="%.2f×"), "Status recomendado": TXT(width=220)})
        saiu = teste[teste["ds_status_final"].notna()]
        if not saiu.empty:
            st.markdown("**Saíram do teste nos últimos 90 dias**")
            _tabela(saiu, {"nm_produto": "Produto", "ds_status_final": "Resultado", "dt_saida": "Saída", "qt_pedidos": "Pedidos", "vl_payback": "Payback"})
    note(f"Critérios por papel aprovados em 25/09/2026 (Core 180 dias e ≥ 7 pedidos; Complementar 120 dias e payback; Impulso 90 dias e attach rate ≥ 3%). "
         f"A contagem só corre com estoque disponível. <strong>Alerta</strong>: prazo em até {DIAS_TESTE_A_VENCER} dias ou critério atingido antes do prazo. "
         "Payback = margem de contribuição acumulada ÷ investimento no lote. O status é recomendação: a troca de ciclo é manual no Bling.")

    # ═══ 4. EM SAÍDA / DIFICULDADE ═══
    section_title("4. Em Saída e Dificuldade de Reposição (Curadoria)")
    mon = dados["monitor"]
    if mon.empty:
        st.info("Nenhum produto nesses ciclos.")
    else:
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Em Saída** — liquidar o estoque")
            _tabela(mon[mon["ds_ciclo"] == "Em Saída"], {"nm_produto": "Produto", "qt_estoque": "Estoque", "qt_vendas_60d": "Vendas 60d",
                                                          "qt_dias_para_zerar": "Dias p/ zerar", "ds_status_saida": "Status", "ds_acao_saida": "Ação"},
                    {"Produto": TXT(width=200), "Ação": TXT(width=220)})
        with c2:
            st.markdown("**Dificuldade de Reposição** — buscar alternativa")
            _tabela(mon[mon["ds_ciclo"] == "Dificuldade de Reposição"],
                    {"nm_produto": "Produto", "qt_estoque": "Estoque", "qt_dias_ruptura": "Dias sem estoque",
                     "vl_venda_perdida_estimada": "Venda perdida (MC)", "ds_status_reposicao": "Status"},
                    {"Produto": TXT(width=200), "Venda perdida (MC)": BRL0})
    note("Em Saída: dias para zerar = estoque ÷ ritmo de venda dos últimos 60 dias; vazio = sem venda no período. Venda perdida = dias sem estoque × ritmo "
         "anterior × margem de contribuição por unidade (estimativa).")

    # ═══ 5. OFERTAS ═══
    section_title("5. Ofertas (Cashing)")
    if ofertas.empty:
        st.info("Nenhum produto com o campo \"Tipo de oferta\" preenchido no Bling.")
    else:
        of = ofertas.sort_values(["fg_oferta_sem_estoque", "qt_pecas_90d"], ascending=[False, False])
        of = of.assign(situacao=of["fg_oferta_sem_estoque"].map({True: "Sem estoque — trocar a oferta", False: "Ok"}))
        _tabela(of, {"nm_produto_completo": "Variação", "ds_tipo_oferta": "Tipo de oferta", "qt_estoque_bling": "Estoque", "fg_visivel_site": "Visível",
                     "qt_pecas_30d": "Peças 30d", "qt_pecas_90d": "Peças 90d", "vl_margem_contribuicao_90d": "Margem 90d", "situacao": "Situação"},
                {"Variação": TXT(width=260), "Margem 90d": BRL0, "Situação": TXT(width=200)})
    cand = f[f["visivel"] & (f["estoque"] > 0) & f["oferta"].isna() & f["papel"].isin(["Impulso", "Complementar"])
             & (f["preco_por"] <= PRECO_MAX_OFERTA)].sort_values("estoque_custo", ascending=False)
    with st.expander(f"Candidatos a oferta ({len(cand)})"):
        _tabela(cand, {"produto": "Produto", "papel": "Papel", "preco_por": "Preço", "estoque": "Estoque", "estoque_custo": "Estoque a custo",
                       "pecas_90d": "Peças 90d", "visitas": f"Visitas {DIAS_VISITAS}d"}, {"Produto": TXT(width=240), "Preço": BRL2, "Estoque a custo": BRL0})
    note(f"Ofertas = campo <strong>Tipo de oferta</strong> do cadastro do Bling (order bump e upsell no Cashing). Oferta sem estoque mostra ao cliente algo "
         f"que ele não consegue levar — trocar no Cashing na hora. Candidatos: visíveis, com estoque, sem oferta, papel Impulso ou Complementar e preço até "
         f"{brl(PRECO_MAX_OFERTA, 0)}, do maior estoque parado para o menor.")

    # ═══ 6. EMPURRÃOZINHO ═══
    section_title("6. Empurrãozinho — produtos com estoque que giram devagar")
    lento = f[f["visivel"] & (f["estoque"] > 0) & ((f["pecas_90d"] == 0) | f["risco"].str.contains("Encalhado|Sobreestoque", regex=True))].copy()
    lento["diagnostico"] = lento["visitas"].apply(lambda v: "Pouca visita — dar visibilidade" if v < mediana_visitas
                                                 else "Visitam e não compram — revisar preço, fotos e descrição")
    lento = lento.sort_values("estoque_custo", ascending=False)
    render_cards([
        card("Produtos girando devagar", _n(len(lento)), "visíveis e com estoque"),
        card("Estoque parado a custo", brl(lento["estoque_custo"].sum(), 0), "soma do estoque desses produtos pelo custo de cadastro"),
        card("Pouca visita", _n((lento["visitas"] < mediana_visitas).sum()), f"abaixo de {_n(mediana_visitas)} visitas em {DIAS_VISITAS} dias"),
        card("Visitam e não compram", _n((lento["visitas"] >= mediana_visitas).sum()), "problema de página, preço ou foto"),
    ])
    _tabela(lento, {"produto": "Produto", "frente": "Frente", "estoque": "Estoque", "estoque_custo": "Estoque a custo", "visitas": f"Visitas {DIAS_VISITAS}d",
                    "pecas_90d": "Peças 90d", "dias_sem_venda": "Dias sem venda", "risco": "Risco", "diagnostico": "Diagnóstico"},
            {"Produto": TXT(width=240), "Estoque a custo": BRL0, "Diagnóstico": TXT(width=280)})
    note(f"Entra quem está visível, com estoque e sem venda em 90 dias ou com risco \"Encalhado\"/\"Sobreestoque\". O corte de visitas é a mediana das páginas "
         f"de produto com visita nos últimos {DIAS_VISITAS} dias ({_n(mediana_visitas)}). <strong>Pouca visita</strong>: o produto não está sendo visto — destaque "
         "no site, Instagram, entrar como oferta. <strong>Visitam e não compram</strong>: o problema está na página (preço, fotos, descrição). Dias sem venda "
         "vazio = nunca vendeu.")

    # ═══ 7. PROCURA SEM ESTOQUE ═══
    section_title("7. Procura sem estoque — prioridade de reposição")
    procura = f[(f["estoque"] <= 0) & ((f["visitas"] > 0) | (f["pecas_90d"] > 0))].sort_values(["visitas", "pecas_90d"], ascending=False)
    _tabela(procura, {"produto": "Produto", "frente": "Frente", "visitas": f"Visitas {DIAS_VISITAS}d", "pecas_90d": "Peças 90d",
                      "compra_pendente": "Compra pendente", "fornecedor": "Fornecedor", "ciclo": "Ciclo", "visivel": "Visível"},
            {"Produto": TXT(width=260)})
    note("Produtos zerados que as pessoas continuam procurando (visitas no site) ou que venderam nos últimos 90 dias. Produto Em Saída ou Descontinuado "
         "não se repõe: nesse caso, ocultar.")

    # ═══ 8. CADASTRO ═══
    section_title("8. Cadastro — corrigir no Bling ou na Nuvemshop")
    problemas = {
        "Sem custo": g["fg_sem_custo"], "Sem papel": g["fg_sem_papel"], "Sem ciclo (Curadoria)": g["fg_sem_ciclo"] & (g["ds_frente"] == "Curadoria"),
        "Sem peso": g["fg_sem_peso"], "Preço Bling ≠ Nuvemshop": g["fg_preco_divergente"], "Sem imagem na Nuvemshop": g["fg_sem_imagem"],
        "Fora da Nuvemshop": ~g["fg_na_nuvemshop"],
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
    _tabela(cad.sort_values("nm_produto_completo"),
            {"nm_produto_completo": "Variação", "cd_produto": "SKU", "ds_frente": "Frente", "qt_estoque_bling": "Estoque", "fg_visivel_site": "Visível",
             "vl_preco_bling": "Preço Bling", "vl_preco_de_nuvemshop": "Preço Nuvemshop", "problemas": "Problemas"},
            {"Variação": TXT(width=280), "Preço Bling": BRL2, "Preço Nuvemshop": BRL2, "Problemas": TXT(width=260)})
    note("<strong>Sem custo</strong> é o mais caro: a margem de contribuição do produto sai superestimada em todos os relatórios. <strong>Sem peso</strong> "
         "afeta o cálculo do frete. <strong>Preço</strong> compara o preço cheio da Nuvemshop com o de venda do Bling (promoção fica de fora). Papel e ciclo "
         "herdam do produto pai quando a variação está vazia.")
