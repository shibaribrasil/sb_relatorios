"""Relatório Biblioteca — Shibari Brasil (camada de consulta).

O que a biblioteca de shibari, BDSM, fetiches e sexualidade tem e o que não tem (cobertura por assunto, por coleção e do
dicionário) e onde está o que se procura (busca de fontes e de verbetes). Regras e definição de cada indicador:
specs/biblioteca.md.

Dados: dataset `raw_biblioteca` (us-east4), carregado por `sb_data_pipeline/scripts/carregar_biblioteca.py`. Não é dbt: a
regra mora nas views do dataset; aqui só se filtra, soma e apresenta. Tema, escopo e tipo de conteúdo são propostas da
triagem por IA, ainda sem conferência do Hugo.
"""
import unicodedata

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from common import bigquery as bq
from common.design import COLORS, inject_css, card, render_cards, section_title, note, plotly_layout

DATASET = "raw_biblioteca"
POUCA_FONTE_CONFIAVEL = 3      # tema com menos fontes confiáveis que isso é "pouca fonte confiável"
LINHAS_BUSCA = 500             # linhas mostradas na busca de fontes

FRENTES = {"shibari": "Shibari", "bdsm-fetiches": "BDSM e fetiches", "sexualidade": "Sexualidade", "demais-materiais": "Demais materiais"}
ESCOPOS = {"nucleo": "Núcleo", "adjacente": "Adjacente", "fora-de-foco": "Fora de foco"}
UNIVERSOS = {"shibari": "Shibari", "bdsm-fetiches": "Fetiches e BDSM", "sexualidade": "Sexualidade"}
CONFIAVEIS = ("primaria", "academica", "praticante")


def _n(s):
    """minúsculas, sem acento (busca)."""
    return "".join(c for c in unicodedata.normalize("NFD", str(s).lower()) if unicodedata.category(c) != "Mn")


def _int(v):
    return f"{int(v):,}".replace(",", ".")


@st.cache_data(ttl=900)
def carregar_dados():
    client = bq.get_client()
    base = f"`{bq.PROJECT}.{DATASET}"
    d = {
        "tema": bq.query_df(client, f"SELECT * FROM {base}.vw_cobertura_tema`"),
        "colecao": bq.query_df(client, f"SELECT * FROM {base}.vw_cobertura_colecao`"),
        "dic": bq.query_df(client, f"SELECT * FROM {base}.vw_cobertura_dicionario`"),
        "busca": bq.query_df(client, f"SELECT * FROM {base}.vw_busca_fontes`"),
        "verbetes": bq.query_df(client, f"SELECT * FROM {base}.verbetes`"),
        "vf": bq.query_df(client, f"SELECT universo, slug, fonte, termo_na_fonte, idioma, resumo FROM {base}.verbete_fonte`"),
        "carga": bq.query_df(client, f"SELECT MAX(_loaded_at) AS carga FROM {base}.fontes`"),
    }
    return d


def _situacao(r):
    if r["frente"] == "demais-materiais":
        return "—"
    if r["n_fontes"] == 0:
        return "Sem fonte"
    if r["n_confiaveis"] == 0:
        return "Sem fonte confiável"
    if r["n_confiaveis"] < POUCA_FONTE_CONFIAVEL:
        return "Pouca fonte confiável"
    return "Coberto"


# ── Cobertura ───────────────────────────────────────────────────────────────
def _aba_cobertura(d):
    tema = d["tema"].copy()
    tema["situacao"] = tema.apply(_situacao, axis=1)
    tema["frente_nome"] = tema["frente"].map(FRENTES)
    ref = tema[tema["frente"] != "demais-materiais"]
    lacunas = ref[ref["situacao"].isin(["Sem fonte", "Sem fonte confiável"])]
    render_cards([
        card("Temas de referência", _int(len(ref)), "shibari, BDSM e fetiches, sexualidade"),
        card("Sem fonte confiável", _int(len(lacunas)), "nenhuma fonte primária, acadêmica ou de praticante", variant="danger" if len(lacunas) else "success"),
        card("Pouca fonte confiável", _int((ref["situacao"] == "Pouca fonte confiável").sum()), f"menos de {POUCA_FONTE_CONFIAVEL} fontes confiáveis", variant="warning"),
        card("Cobertos", _int((ref["situacao"] == "Coberto").sum()), f"{POUCA_FONTE_CONFIAVEL}+ fontes confiáveis", variant="success"),
    ])
    section_title("Cobertura por tema")
    frente = st.radio("Frente", ["Todas"] + [FRENTES[f] for f in ("shibari", "bdsm-fetiches", "sexualidade")], horizontal=True, key="bib_frente")
    v = ref if frente == "Todas" else ref[ref["frente_nome"] == frente]
    v = v.sort_values(["n_confiaveis", "n_fontes"], ascending=[True, True])
    if v.empty:
        st.info("Sem temas para mostrar.")
        return
    altura = max(260, 26 * len(v) + 80)
    fig = go.Figure()
    for col, rotulo, cor in (("n_confiaveis", "Confiável (primária, acadêmica, praticante)", COLORS["success"]),
                             ("n_comunidade", "Comunidade", COLORS["info"]),
                             ("n_comercial", "Comercial", COLORS["warning"])):
        fig.add_bar(y=v["tema"], x=v[col], orientation="h", name=rotulo, marker_color=cor,
                    hovertemplate="%{y}<br>" + rotulo + ": %{x}<extra></extra>")
    fig.update_layout(barmode="stack", height=altura, yaxis=dict(autorange="reversed", automargin=True))
    st.plotly_chart(plotly_layout(fig, margin=dict(l=10, r=10, t=30, b=10)), use_container_width=True)
    st.dataframe(
        v[["frente_nome", "tema", "situacao", "n_fontes", "n_nucleo", "n_adjacente", "n_fora_de_foco", "n_confiaveis", "n_comunidade",
           "n_comercial", "n_em_portugues", "n_guias_praticos", "n_com_tutorial"]]
        .rename(columns={"frente_nome": "Frente", "tema": "Tema", "situacao": "Situação", "n_fontes": "Fontes", "n_nucleo": "Núcleo",
                         "n_adjacente": "Adjacente", "n_fora_de_foco": "Fora de foco", "n_confiaveis": "Confiáveis",
                         "n_comunidade": "Comunidade", "n_comercial": "Comercial", "n_em_portugues": "Em português",
                         "n_guias_praticos": "Guias práticos", "n_com_tutorial": "Com tutorial"}),
        hide_index=True, use_container_width=True)
    dm = tema[tema["frente"] == "demais-materiais"]
    if not dm.empty:
        note("<strong>Demais materiais</strong> (avisos, publicidade, assuntos soltos): "
             + " · ".join(f"{r['tema']} {_int(r['n_fontes'])}" for _, r in dm.iterrows())
             + ". Todos fora de foco; só se consulta em última hipótese.")
    note("Tema, escopo e confiança da triagem são <strong>propostas de IA</strong>, ainda não conferidas. "
         "<strong>Lacuna</strong> = tema sem fonte confiável: é onde buscar material novo, não um juízo sobre o que existe.")


# ── Coleções ────────────────────────────────────────────────────────────────
def _aba_colecoes(d):
    c = d["colecao"].groupby("colecao", as_index=False)[["n_fontes", "n_nucleo", "n_adjacente", "n_fora_de_foco", "palavras"]].sum()
    c = c.sort_values("n_fontes", ascending=False)
    top = c.head(15).sort_values("n_fontes")
    section_title("De onde vem o material e para que serve")
    fig = go.Figure()
    for col, rotulo, cor in (("n_nucleo", "Núcleo", COLORS["primary"]), ("n_adjacente", "Adjacente", COLORS["info"]),
                             ("n_fora_de_foco", "Fora de foco", COLORS["text_muted"])):
        fig.add_bar(y=top["colecao"], x=top[col], orientation="h", name=rotulo, marker_color=cor)
    fig.update_layout(barmode="stack", height=max(320, 28 * len(top) + 80), yaxis=dict(automargin=True))
    st.plotly_chart(plotly_layout(fig, margin=dict(l=10, r=10, t=30, b=10)), use_container_width=True)
    conf = d["colecao"].groupby("colecao")["confiabilidade"].agg(lambda s: ", ".join(sorted(set(s)))).rename("Confiabilidade")
    t = c.merge(conf, on="colecao", how="left").rename(columns={
        "colecao": "Coleção", "n_fontes": "Fontes", "n_nucleo": "Núcleo", "n_adjacente": "Adjacente",
        "n_fora_de_foco": "Fora de foco", "palavras": "Palavras"})
    st.dataframe(t, hide_index=True, use_container_width=True)
    note("O escopo é decidido <strong>por material, não por coleção</strong>: a mesma coleção tem núcleo, adjacente e fora de foco.")


# ── Dicionário ──────────────────────────────────────────────────────────────
def _aba_dicionario(d):
    dic = d["dic"]
    tot = dic[["n_verbetes", "n_uma_fonte", "n_duas_ou_mais", "n_quatro_ou_mais", "n_com_divergencia"]].sum()
    render_cards([
        card("Verbetes", _int(tot["n_verbetes"]), "um por termo, em três universos"),
        card("Com 2+ fontes", _int(tot["n_duas_ou_mais"]), f"{tot['n_duas_ou_mais'] / max(tot['n_verbetes'], 1):.0%} dos verbetes", variant="success"),
        card("Só uma fonte", _int(tot["n_uma_fonte"]), "menor lastro", variant="warning"),
        card("Com divergência", _int(tot["n_com_divergencia"]), "as fontes não dizem o mesmo"),
    ])
    section_title("Verbetes por universo")
    u = dic.groupby("universo", as_index=False)[["n_verbetes", "n_uma_fonte", "n_duas_ou_mais", "n_quatro_ou_mais", "n_com_divergencia"]].sum()
    u["Universo"] = u["universo"].map(UNIVERSOS)
    fig = go.Figure()
    fig.add_bar(x=u["Universo"], y=u["n_uma_fonte"], name="1 fonte", marker_color=COLORS["warning"])
    fig.add_bar(x=u["Universo"], y=u["n_duas_ou_mais"], name="2+ fontes", marker_color=COLORS["success"])
    fig.update_layout(barmode="stack", height=300)
    st.plotly_chart(plotly_layout(fig, margin=dict(l=10, r=10, t=30, b=10)), use_container_width=True)
    st.dataframe(u[["Universo", "n_verbetes", "n_uma_fonte", "n_duas_ou_mais", "n_quatro_ou_mais", "n_com_divergencia"]].rename(columns={
        "n_verbetes": "Verbetes", "n_uma_fonte": "1 fonte", "n_duas_ou_mais": "2+ fontes",
        "n_quatro_ou_mais": "4+ fontes", "n_com_divergencia": "Com divergência"}), hide_index=True, use_container_width=True)
    section_title("Por tipo de termo")
    st.dataframe(dic.groupby("tipo", as_index=False)[["n_verbetes", "n_uma_fonte", "n_duas_ou_mais"]].sum()
                 .sort_values("n_verbetes", ascending=False)
                 .rename(columns={"tipo": "Tipo", "n_verbetes": "Verbetes", "n_uma_fonte": "1 fonte", "n_duas_ou_mais": "2+ fontes"}),
                 hide_index=True, use_container_width=True)
    note("Verbete = um termo; cada fonte tem a sua definição, lado a lado e sem fusão. Resumos são <strong>rascunho de IA</strong>, não conferidos.")


# ── Busca ───────────────────────────────────────────────────────────────────
def _busca_fontes(d):
    b = d["busca"].copy()
    c1, c2, c3 = st.columns([2, 1, 1])
    texto = c1.text_input("Buscar por título, autor ou tema", key="bib_txt")
    frentes = c2.multiselect("Frente", [FRENTES[f] for f in ("shibari", "bdsm-fetiches", "sexualidade")], key="bib_fr")
    escopos = c3.multiselect("Escopo", list(ESCOPOS.values()), key="bib_esc")
    c4, c5, c6, c7 = st.columns(4)
    temas = c4.multiselect("Tema", sorted(b["tema"].dropna().unique()), key="bib_tema")
    idiomas = c5.multiselect("Idioma", sorted(b["idioma"].dropna().unique()), key="bib_idi")
    conf = c6.multiselect("Confiabilidade", sorted(b["confiabilidade"].dropna().unique()), key="bib_conf")
    tipos = c7.multiselect("Tipo de conteúdo", sorted(b["tipo_conteudo"].dropna().unique()), key="bib_tipo")
    incluir_dm = st.checkbox("Incluir demais materiais (avisos, publicidade, assuntos soltos: última hipótese)", key="bib_dm")
    if not incluir_dm:
        b = b[b["frente"] != "demais-materiais"]
    if texto.strip():
        for palavra in _n(texto).split():
            b = b[b["texto_busca"].map(_n).str.contains(palavra, regex=False)]
    if frentes:
        b = b[b["frente"].map(FRENTES).isin(frentes)]
    if escopos:
        b = b[b["escopo"].map(ESCOPOS).isin(escopos)]
    for col, sel in (("tema", temas), ("idioma", idiomas), ("confiabilidade", conf), ("tipo_conteudo", tipos)):
        if sel:
            b = b[b[col].isin(sel)]
    b = b.assign(_conf=b["confiabilidade"].isin(CONFIAVEIS).map({True: 0, False: 1}))
    b = b.sort_values(["prioridade_busca", "_conf", "palavras"], ascending=[True, True, False])
    st.caption(f"{_int(len(b))} fontes" + (f" (mostrando as primeiras {LINHAS_BUSCA})" if len(b) > LINHAS_BUSCA else "")
               + ". Ordem: núcleo, adjacente, fora de foco; dentro de cada, fonte confiável primeiro e as mais longas antes.")
    v = b.head(LINHAS_BUSCA)
    st.dataframe(
        v[["titulo", "colecao", "tema", "escopo", "tipo_conteudo", "idioma", "confiabilidade", "palavras", "id", "url"]]
        .assign(escopo=v["escopo"].map(ESCOPOS))
        .rename(columns={"titulo": "Título", "colecao": "Coleção", "tema": "Tema", "escopo": "Escopo", "tipo_conteudo": "Tipo",
                         "idioma": "Idioma", "confiabilidade": "Confiabilidade", "palavras": "Palavras", "id": "ID", "url": "Endereço"}),
        hide_index=True, use_container_width=True,
        column_config={"Endereço": st.column_config.LinkColumn("Endereço", display_text="abrir")})
    note("Tema e escopo são <strong>propostas de IA</strong> ainda não conferidas. O texto completo está no repositório "
         "<code>sb_cerebro</code> (<code>06_biblioteca/extracao/</code>); aqui está só o catálogo.")


def _busca_verbetes(d):
    v = d["verbetes"].copy()
    c1, c2 = st.columns([2, 1])
    texto = c1.text_input("Buscar termo (nome ou nome alternativo)", key="bib_vtxt")
    univ = c2.multiselect("Universo", list(UNIVERSOS.values()), key="bib_vuni")
    if univ:
        v = v[v["universo"].map(UNIVERSOS).isin(univ)]
    if texto.strip():
        chave = v["nome"].fillna("") + " " + v["nomes"].fillna("").str.replace("|", " ", regex=False)
        for palavra in _n(texto).split():
            v = v[chave.loc[v.index].map(_n).str.contains(palavra, regex=False)]
    v = v.sort_values(["n_fontes", "nome"], ascending=[False, True])
    st.caption(f"{_int(len(v))} verbetes")
    mostra = v.head(300)
    st.dataframe(mostra[["nome", "universo", "tipo", "n_fontes", "idiomas", "tema", "nomes"]]
                 .assign(universo=mostra["universo"].map(UNIVERSOS))
                 .rename(columns={"nome": "Verbete", "universo": "Universo", "tipo": "Tipo", "n_fontes": "Fontes",
                                  "idiomas": "Idiomas", "tema": "Tema", "nomes": "Também chamado"}),
                 hide_index=True, use_container_width=True)
    if mostra.empty:
        return
    opcoes = {f"{r['nome']} ({UNIVERSOS.get(r['universo'], r['universo'])}, {int(r['n_fontes'])} fontes)": (r["universo"], r["slug"])
              for _, r in mostra.head(100).iterrows()}
    escolha = st.selectbox("Ver as definições de um verbete", [""] + list(opcoes), key="bib_vsel")
    if escolha:
        uni, slug = opcoes[escolha]
        titulos = d["busca"].set_index("id")["titulo"].to_dict()
        for _, r in d["vf"][(d["vf"]["universo"] == uni) & (d["vf"]["slug"] == slug)].iterrows():
            st.markdown(f"**{r['fonte']}** · {titulos.get(r['fonte'], '')[:70]} · “{r['termo_na_fonte']}”")
            st.write(r["resumo"])
        note("Resumos feitos por IA, em palavras nossas, <strong>rascunho</strong>. A página completa do verbete está em "
             f"<code>sb_cerebro/05_conhecimento/dicionario/{uni}/{slug}.md</code>.")


def _aba_busca(d):
    modo = st.radio("Buscar", ["Fontes", "Verbetes do dicionário"], horizontal=True, key="bib_modo")
    if modo == "Fontes":
        _busca_fontes(d)
    else:
        _busca_verbetes(d)


def render():
    inject_css()
    with st.spinner("Carregando dados do BigQuery..."):
        try:
            d = carregar_dados()
        except Exception as e:
            st.error(f"Erro ao carregar dados do BigQuery: {e}")
            return
    ativos = d["busca"]
    carga = pd.to_datetime(d["carga"]["carga"].iloc[0]) if not d["carga"].empty else None
    st.html(f"""
    <div class="report-header">
      <div>
        <div class="report-brand">shibari brasil · camada de consulta</div>
        <div class="report-title">Biblioteca <span>&amp;</span> Dicionário</div>
        <div class="report-meta">O que temos e o que não temos, e onde está · catálogo de fontes, temas e verbetes · tema e escopo são propostas de IA, ainda não conferidas</div>
      </div>
      <div class="report-badge">Catálogo carregado em: <strong>{carga.strftime('%d/%m/%Y %H:%M') if carga is not None and not pd.isna(carga) else '—'}</strong></div>
    </div>
    """)
    nucleo = int((ativos["escopo"] == "nucleo").sum())
    render_cards([
        card("Fontes ativas", _int(len(ativos)), f"{_int(ativos['palavras'].sum())} palavras"),
        card("Núcleo", _int(nucleo), f"{nucleo / max(len(ativos), 1):.0%} · shibari, BDSM e fetiches", variant="success"),
        card("Adjacente", _int((ativos["escopo"] == "adjacente").sum()), "sexualidade"),
        card("Fora de foco", _int((ativos["escopo"] == "fora-de-foco").sum()), "acessório; inclui demais materiais"),
        card("Verbetes", _int(len(d["verbetes"])), f"{_int((d['verbetes']['n_fontes'] >= 2).sum())} com 2+ fontes"),
    ])
    abas = st.tabs(["Cobertura por tema", "Coleções", "Dicionário", "Buscar"])
    with abas[0]:
        _aba_cobertura(d)
    with abas[1]:
        _aba_colecoes(d)
    with abas[2]:
        _aba_dicionario(d)
    with abas[3]:
        _aba_busca(d)
