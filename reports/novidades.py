"""Relatório Novidades — Oportunidades — Shibari Brasil (camada mensal).

Das novidades mapeadas na aba `produto_novidade` da planilha ADM e Processos, quais implementar primeiro: custo da última
cotação nos fornecedores, preço de venda sugerido pela meta de margem do cadastro, margem de contribuição projetada na
régua da `tb_pedido`, vendas da família do catálogo, demanda do site (busca interna, Google e GA4) e capital do lote de
teste. Definição de cada indicador, pesos e limites: specs/novidades-oportunidades.md.

Regra de negócio: custo de referência, preço sugerido, margem projetada, lote e payback vêm prontos do dbt
(`tb_novidade_preco`). O SCORE é calculado aqui — exceção assumida e documentada na spec (mesma do Mapa de Interesse):
a az (us-east4) e o GA4/Search Console (US) estão em regiões diferentes e não se juntam em SQL. O score é ranking de
apresentação (ordem e faixa); nenhum número financeiro é recalculado. A cotação NÃO tem botão: é feita no navegador do
Hugo e a página mostra o preço da última vez.
"""
import html
import re
import unicodedata

import numpy as np
import pandas as pd
import streamlit as st

from common import bigquery as bq
from common.design import inject_css, card, render_cards, section_title, note, brl, pct
from common.frescor import carregar_frescor, detalhe_atualizacao, alerta_atraso

TABELAS = ("tb_novidade_preco", "tb_novidade_cotacao", "tb_novidade_familia_venda", "tb_pedido")
EXTRATORES = ("bling_orders", "bling_products")

# Pesos do score (soma 1). Cada componente é a posição percentil entre as novidades COTADAS e ainda não implementadas.
PESOS = {"margem": 0.35, "venda": 0.25, "demanda": 0.20, "capital": 0.20}
FAIXAS = [("Implementar já", 70), ("Boa", 50), ("Avaliar", 30), ("Deixar", 0)]
DIAS_GA4 = 30            # janela do GA4 de produto (eventos por produto existem desde 29/08/2026)
DIAS_BUSCA = 90          # janela da busca interna
CONF_ALTA, CONF_MEDIA = 20, 10   # pedidos da família em 12 meses

# termos de marca / frente Shibari: não são demanda por item de Curadoria
MARCA = re.compile(r"shi?b|xibar|shob|shin|shiv|shub|chib|ahib|shiba|kuromi|corda|juta|algodao|tenugui|furoshiki|nylon|cera|reclame")


def _n(s):
    """minúsculas, sem acento (mesma chave do dbt: chave_texto)."""
    return "".join(c for c in unicodedata.normalize("NFD", str(s).lower().strip()) if unicodedata.category(c) != "Mn")


@st.cache_data(ttl=900)
def carregar_dados():
    client = bq.get_client()
    az = f"`{bq.PROJECT}.dbt_dw_az"
    us = f"`{bq.PROJECT}.dbt_dw_us_az"
    preco = bq.query_df(client, f"SELECT * FROM {az}.tb_novidade_preco`")
    cot = bq.query_df(client, f"SELECT * FROM {az}.tb_novidade_cotacao`")
    fam = bq.query_df(client, f"SELECT * FROM {az}.tb_novidade_familia_venda`")
    prem = bq.query_df(client, f"SELECT * FROM {az}.tb_novidade_premissa`")
    atr = bq.query_df(client, f"SELECT * FROM `{bq.PROJECT}.dbt_dw_stg.stg_novidade_atributo`")
    catalogo = bq.query_df(client, f"""
        SELECT DISTINCT nm_produto, ds_subcategoria FROM {az}.tb_preco_produto`
         WHERE NOT STARTS_WITH(COALESCE(ds_categoria, ''), '[Interno]')""")
    busca = bq.query_df(client, f"""
        SELECT ds_termo_normalizado AS termo, COUNT(DISTINCT cd_sessao) AS qt_sessoes
          FROM {us}.tb_ga4_busca_interna` WHERE dt_data >= DATE_SUB(CURRENT_DATE('America/Sao_Paulo'), INTERVAL {DIAS_BUSCA} DAY)
         GROUP BY 1""", )
    gsc = bq.query_df(client, f"""
        SELECT ds_consulta AS termo, SUM(qt_cliques) AS qt_cliques, SUM(qt_impressoes) AS qt_impressoes,
               SAFE_DIVIDE(SUM(qt_soma_posicao), NULLIF(SUM(qt_impressoes), 0)) AS posicao
          FROM {us}.tb_gsc_consulta_diaria` WHERE NOT fl_consulta_anonimizada GROUP BY 1""")
    ga4 = bq.query_df(client, f"""
        SELECT nm_item_ga4, nm_evento, SUM(qt_sessoes) AS qt_sessoes
          FROM {us}.tb_ga4_produto_dia`
         WHERE dt_data >= DATE_SUB(CURRENT_DATE('America/Sao_Paulo'), INTERVAL {DIAS_GA4} DAY)
           AND nm_evento IN ('view_item', 'add_to_cart')
         GROUP BY 1, 2""")
    return {"preco": preco, "cot": cot, "fam": fam, "prem": prem, "atr": atr, "catalogo": catalogo,
            "busca": busca, "gsc": gsc, "ga4": ga4}


# ── sinais de demanda por novidade (pandas: regiões diferentes) ───────────────────────────────────────────────
def sinais_demanda(atr, busca, gsc, ga4):
    b = busca.assign(t=busca["termo"].map(_n))
    g = gsc.assign(t=gsc["termo"].map(_n))
    g = g[~g["t"].map(lambda x: bool(MARCA.search(x)))]
    a = ga4.assign(t=ga4["nm_item_ga4"].map(_n))
    v, c = a[a["nm_evento"] == "view_item"], a[a["nm_evento"] == "add_to_cart"]
    linhas = []
    for r in atr.itertuples():
        rb = re.compile(r.ds_regex_busca) if isinstance(r.ds_regex_busca, str) else None
        rf = re.compile(r.ds_regex_familia) if isinstance(r.ds_regex_familia, str) else None
        bb = b[b["t"].map(lambda x: bool(rb.search(x))) & ~b["t"].map(lambda x: bool(MARCA.search(x)))] if rb else b.iloc[0:0]
        gg = g[g["t"].map(lambda x: bool(rb.search(x)))] if rb else g.iloc[0:0]
        linhas.append({
            "ch_novidade": r.ch_novidade,
            "qt_busca_sessoes": int(bb["qt_sessoes"].sum()),
            "qt_gsc_impressoes": int(gg["qt_impressoes"].sum()),
            "qt_gsc_cliques": int(gg["qt_cliques"].sum()),
            "qt_ga4_visitas": int(v[v["t"].map(lambda x: bool(rf.search(x)))]["qt_sessoes"].sum()) if rf else 0,
            "qt_ga4_carrinhos": int(c[c["t"].map(lambda x: bool(rf.search(x)))]["qt_sessoes"].sum()) if rf else 0,
        })
    return pd.DataFrame(linhas)


def _rank(s, ascendente=True):
    """Posição percentil 0–1 (média nos empates); NaN fica no meio (0,5) para não punir nem premiar falta de dado."""
    r = s.rank(pct=True, method="average", ascending=ascendente)
    return r.fillna(0.5)


def calcular_score(d):
    """Score 0–100 e faixa entre as novidades COTADAS e abertas; as demais ficam sem score. Ver spec."""
    d = d.copy()
    base = d[(~d["fg_implementado"]) & d["vl_custo_referencia"].notna() & ~d["fg_sem_mapeamento"]].copy()
    if not base.empty:
        momento = (base["nr_pedidos_90d"] / (base["nr_pedidos_familia_12m"] / 4).replace(0, np.nan)).clip(0, 2)
        venda = 0.7 * _rank(base["nr_pedidos_familia_12m"]) + 0.3 * _rank(momento)
        demanda = (_rank(base["qt_busca_sessoes"]) + _rank(base["qt_gsc_impressoes"]) + _rank(base["qt_ga4_carrinhos"])) / 3
        payback = base["qt_meses_payback_lote"].replace([np.inf, -np.inf], np.nan)
        capital = 0.5 * _rank(base["vl_lote_teste_custo"], ascendente=False) + 0.5 * _rank(payback.fillna(payback.max()), ascendente=False)
        base["c_margem"] = _rank(base["vl_mc_unitaria"])
        base["c_venda"], base["c_demanda"], base["c_capital"] = venda, demanda, capital
        base["score"] = 100 * (PESOS["margem"] * base["c_margem"] + PESOS["venda"] * base["c_venda"]
                               + PESOS["demanda"] * base["c_demanda"] + PESOS["capital"] * base["c_capital"])
        base["faixa"] = base["score"].map(lambda s: next(n for n, m in FAIXAS if s >= m))
        # confiança: tamanho da amostra da família; cotação vencida rebaixa um nível
        niv = np.where(base["nr_pedidos_familia_12m"] >= CONF_ALTA, 2, np.where(base["nr_pedidos_familia_12m"] >= CONF_MEDIA, 1, 0))
        niv = np.where(base["fg_cotacao_vencida"].fillna(True), np.maximum(niv - 1, 0), niv)
        base["confianca"] = pd.Series(niv, index=base.index).map({2: "Alta", 1: "Média", 0: "Baixa"})
        d = d.merge(base[["ch_novidade", "c_margem", "c_venda", "c_demanda", "c_capital", "score", "faixa", "confianca"]], on="ch_novidade", how="left")
    else:
        for c in ["c_margem", "c_venda", "c_demanda", "c_capital", "score", "faixa", "confianca"]:
            d[c] = np.nan
    d["faixa"] = np.where(d["fg_implementado"], "Implementada", np.where(d["fg_sem_mapeamento"], "Sem mapeamento",
                          np.where(d["faixa"].isna(), "Sem cotação", d["faixa"])))
    return d


def avisos(r):
    a = []
    if r.fg_promocao_observada:
        a.append("promoção na cotação ignorada (custo regular)")
    if r.ds_status_cotacao == "vencida":
        a.append(f"cotação com {int(r.qt_dias_cotacao)} dias")
    if pd.notna(r.vl_custo_referencia_max) and pd.notna(r.vl_custo_referencia) and r.vl_custo_referencia_max > r.vl_custo_referencia * 1.05:
        a.append("variações com preços diferentes (usa o menor)")
    if r.ds_posicao_preco_familia == "acima da faixa":
        a.append("preço acima da faixa da família")
    if r.fg_acima_teto_impulso:
        a.append("Impulso acima de R$ 30")
    if r.ds_fonte_meta == "markup impulso":
        a.append("Impulso sem meta no cadastro: preço = custo × 2,5")
    elif r.ds_fonte_meta == "fallback complementar":
        a.append("sem meta no cadastro: usa a do Complementar")
    if pd.notna(r.nr_pedidos_familia_12m) and r.nr_pedidos_familia_12m < CONF_MEDIA:
        a.append(f"família com só {int(r.nr_pedidos_familia_12m)} pedidos")
    return "; ".join(a)


def _brl(v, casas=2):
    """brl() com o cifrão escapado: st.markdown trata $...$ como fórmula."""
    return brl(v, casas).replace("$", "\\$")


FORNECEDORES = {"sexy_import": "Sexy Import", "vip_mix": "Vip Mix", "gall": "Gall"}


def render():
    inject_css()
    st.html("""
    <div class="report-header"><div>
      <div class="report-brand">shibari brasil · camada mensal</div>
      <div class="report-title">Novidades — <span>Oportunidades</span></div>
      <div class="report-meta">Quais novidades mapeadas implementar primeiro, com base em custo, margem, vendas e demanda</div>
    </div></div>""")
    with st.spinner("Carregando dados do BigQuery..."):
        try:
            dados = carregar_dados()
        except Exception as e:
            st.error(f"Erro ao carregar dados do BigQuery: {e}")
            return
    try:
        fr = carregar_frescor(TABELAS, EXTRATORES, com_ga4=True)
        alerta_atraso(fr)
    except Exception:
        fr = None

    sin = sinais_demanda(dados["atr"], dados["busca"], dados["gsc"], dados["ga4"])
    d = dados["preco"].merge(sin, on="ch_novidade", how="left").merge(
        dados["fam"][["ch_novidade", "nr_pedidos_90d", "pr_pedidos_recorrentes", "nr_clientes_12m", "vl_mc_por_unidade"]], on="ch_novidade", how="left")
    for c in ["fg_implementado", "fg_sem_mapeamento", "fg_promocao_observada", "fg_cotacao_vencida", "fg_acima_teto_impulso"]:
        d[c] = d[c].astype("boolean").fillna(False).astype(bool)
    for c in ["qt_busca_sessoes", "qt_gsc_impressoes", "qt_gsc_cliques", "qt_ga4_visitas", "qt_ga4_carrinhos"]:
        d[c] = d[c].fillna(0)
    d = calcular_score(d)
    d["avisos"] = [avisos(r) for r in d.itertuples()]
    abertas = d[~d["fg_implementado"]]
    cotadas = abertas[abertas["vl_custo_referencia"].notna()]
    ult_cot = pd.to_datetime(dados["cot"]["dt_cotacao"]).max()

    # ── cartões ───────────────────────────────────────────────────────────────────────────
    n_ja = int((cotadas["faixa"] == "Implementar já").sum())
    capital_ja = cotadas.loc[cotadas["faixa"] == "Implementar já", "vl_lote_teste_custo"].sum()
    render_cards([
        card("Novidades abertas", f"{len(abertas)}", sub=f"{int(d['fg_implementado'].sum())} já implementadas"),
        card("Com cotação", f"{len(cotadas)}", sub=f"de {len(abertas)} · última em {ult_cot:%d/%m}" if pd.notna(ult_cot) else "nenhuma cotação ainda",
             variant="ok" if len(cotadas) == len(abertas) and len(abertas) else "warn"),
        card("Implementar já", f"{n_ja}", sub="faixa de score ≥ 70"),
        card("Capital do lote (Implementar já)", brl(capital_ja, 0), sub="soma dos lotes de teste (3 ou 5 un)"),
    ])
    if cotadas.empty:
        note("<b>Nenhuma novidade cotada ainda.</b> A cotação é feita no navegador do Hugo (pedida ao agente comprador) e grava o preço de "
             "cada fornecedor com a data. Sem ela só aparece a evidência de venda da família.", variant="warn")

    st.html(f'<div class="note"><b>Resumo — Kimba</b> <span style="font-weight:400;opacity:.7">(texto automático, sem IA)</span><br>{html.escape(resumo_kimba(d))}</div>')

    # ── 1. Ranking ────────────────────────────────────────────────────────────────────────
    section_title("1 · Ranking das novidades")
    faixas_ord = [f[0] for f in FAIXAS] + ["Sem cotação", "Sem mapeamento"]
    escolha = st.multiselect("Faixas", faixas_ord, default=[f for f in faixas_ord if f in abertas["faixa"].unique()], key="nov_faixas")
    t = abertas[abertas["faixa"].isin(escolha)].copy()
    t["_o"] = t["faixa"].map({n: i for i, n in enumerate(faixas_ord)})
    t = t.sort_values(["_o", "score"], ascending=[True, False])
    tab = pd.DataFrame({
        "Novidade": t["nm_novidade"], "Faixa": t["faixa"], "Score": t["score"], "Confiança": t["confianca"],
        "Papel pretendido": t["ds_papel_pretendido"], "Fornecedor": t["cd_fornecedor_referencia"].map(FORNECEDORES),
        "Custo (à vista)": t["vl_custo_referencia"], "Preço sugerido": t["vl_preco_recomendado"],
        "MC unitária (R$)": t["vl_mc_unitaria"], "MC projetada": t["pr_mc_projetada"], "Meta": t["pr_meta_margem"],
        "Lote (R$)": t["vl_lote_teste_custo"], "Payback (meses)": t["qt_meses_payback_lote"],
        "Pedidos da família 12m": t["nr_pedidos_familia_12m"], "Pedidos 90d": t["nr_pedidos_90d"],
        "Preço vs família": t["ds_posicao_preco_familia"], "Avisos": t["avisos"],
    })
    st.dataframe(tab, hide_index=True, use_container_width=True, height=min(760, 38 + 35 * max(len(tab), 1)), column_config={
        "Score": st.column_config.ProgressColumn(format="%.0f", min_value=0, max_value=100),
        "Custo (à vista)": st.column_config.NumberColumn(format="R$ %.2f"), "Preço sugerido": st.column_config.NumberColumn(format="R$ %.2f"),
        "MC unitária (R$)": st.column_config.NumberColumn(format="R$ %.2f"),
        "MC projetada": st.column_config.NumberColumn(format="percent"), "Meta": st.column_config.NumberColumn(format="percent"),
        "Lote (R$)": st.column_config.NumberColumn(format="R$ %.0f"), "Payback (meses)": st.column_config.NumberColumn(format="%.1f"),
        "Pedidos da família 12m": st.column_config.NumberColumn(format="%.0f"), "Pedidos 90d": st.column_config.NumberColumn(format="%.0f")})
    note("<b>Custo</b> = menor custo regular à vista entre Sexy e Vip (Gall só se for a única fonte); a promoção da Sexy nunca entra. "
         "<b>Preço sugerido</b> = o menor preço que entrega a <b>meta de margem do cadastro</b> (papel pretendido × Revenda Nacional; Impulso, sem meta, = custo × 2,5) depois de desconto, taxa, "
         "embalagem, reembolso e frete médios realizados na <code>tb_pedido</code>, arredondado para terminar em ,90. "
         "<b>Score</b> é relativo às novidades cotadas desta lista (posição percentil de margem 35% · vendas da família 25% · demanda do site 20% · capital e payback 20%), "
         "não uma nota absoluta. <b>Payback</b> usa o giro de um produto médio da família: ordem de grandeza, não previsão. A amostra é pequena (cerca de 1 pedido por dia): leia faixas, não posições exatas.")

    # ── 2. Detalhe ────────────────────────────────────────────────────────────────────────
    section_title("2 · Detalhe de uma novidade")
    opcoes = abertas.sort_values(["score", "nm_novidade"], ascending=[False, True])["nm_novidade"].tolist()
    if opcoes:
        nome = st.selectbox("Novidade", opcoes, key="nov_detalhe")
        r = abertas[abertas["nm_novidade"] == nome].iloc[0]
        _detalhe(r, dados["cot"], dados["prem"].iloc[0])

    # ── 3. Lacunas ────────────────────────────────────────────────────────────────────────
    section_title("3 · Radar de lacunas: o que o cliente procura e não temos")
    _lacunas(dados)

    # ── 4. Já implementadas ───────────────────────────────────────────────────────────────
    section_title("4 · Já implementadas")
    impl = d[d["fg_implementado"]]
    if impl.empty:
        st.info("Nenhuma novidade marcada como implementada na planilha ainda. Quando houver, o desempenho de cada uma aparece aqui, "
                "contra o critério de \"Em teste\" (pedidos distintos, payback do lote e attach rate), e ajuda a calibrar este ranking.")
    else:
        st.dataframe(impl[["nm_novidade", "ds_papel_pretendido", "vl_custo_referencia", "vl_preco_recomendado", "pr_mc_projetada"]].rename(columns={
            "nm_novidade": "Novidade", "ds_papel_pretendido": "Papel pretendido", "vl_custo_referencia": "Custo (última cotação)",
            "vl_preco_recomendado": "Preço sugerido", "pr_mc_projetada": "MC projetada"}), hide_index=True, use_container_width=True)
        note("O desempenho real (pedidos, MC realizada) entra aqui quando o produto for cadastrado com SKU e ligado à novidade.")

    # ── última cotação por fornecedor ─────────────────────────────────────────────────────
    section_title("Última cotação por fornecedor")
    cot = dados["cot"].copy()
    if cot["dt_cotacao"].notna().any():
        res = cot.groupby("cd_fornecedor").agg(ultima=("dt_cotacao", "max"), links=("ds_url", "count"), cotados=("vl_custo_regular", lambda s: int(s.notna().sum()))).reset_index()
        res["Fornecedor"] = res["cd_fornecedor"].map(FORNECEDORES)
        st.dataframe(res[["Fornecedor", "ultima", "cotados", "links"]].rename(columns={"ultima": "Última cotação", "cotados": "Links cotados", "links": "Links na planilha"}),
                     hide_index=True, use_container_width=True, column_config={"Última cotação": st.column_config.DateColumn(format="DD/MM/YYYY")})
    else:
        st.info("Ainda sem cotação gravada.")
    note("A cotação é feita no navegador do Hugo, pelo agente comprador, e gravada em <code>raw_compras.cotacao_fornecedor</code> com a data. "
         "Preço de fornecedor com mais de 14 dias deve ser reconfirmado antes de comprar. Para atualizar, peça ao agente a cotação das novidades.")

    # ── regras e dados ────────────────────────────────────────────────────────────────────
    prem = dados["prem"].iloc[0]
    with st.expander("Regras aplicadas nesta página"):
        st.markdown(
            f"- **Premissas de margem** (da `tb_pedido`, {int(prem['nr_pedidos'])} pedidos nos últimos 12 meses): desconto {pct(prem['pr_desconto'])} sobre o bruto; "
            f"taxa de pagamento {pct(prem['pr_taxa'])}, embalagem {pct(prem['pr_embalagem'])}, reembolso {pct(prem['pr_reembolso'])} e resultado de frete {pct(prem['pr_resultado_frete'])} da receita líquida; imposto {pct(prem['pr_imposto'])}.\n"
            "- **Meta de margem**: a do cadastro por papel pretendido × Revenda Nacional. O Impulso não tem meta no cadastro: o preço é o custo × 2,5 (decisão do Hugo, 02/10/2026) e a MC projetada é só informativa.\n"
            "- **Papel pretendido e família** de cada novidade: rascunho do agente no dbt (`stg_novidade_atributo`), a revisar. Novidade nova na planilha aparece como \"Sem mapeamento\" até ser mapeada.\n"
            "- **Limites**: ~1 pedido/dia; GA4 de produto desde 29/08/2026; Google com poucos dias; só se vendeu o que já escolhemos (viés de sobrevivência); correlação não é causa.")
    section_title("De quando são os dados")
    if fr is not None:
        try:
            detalhe_atualizacao(fr)
        except Exception:
            pass


def resumo_kimba(d):
    """Resumo escrito em texto corrido a partir dos números JÁ calculados (nenhum número ou produto é inventado).
    Versão sem IA (template): a versão com IA do Kimba exige o ranking dentro do dbt, o que hoje não é possível porque
    a demanda (GA4/Search Console) está em outra região do BigQuery. Ver spec."""
    abertas = d[~d["fg_implementado"]]
    cot = abertas[abertas["vl_custo_referencia"].notna() & ~abertas["fg_sem_mapeamento"]]
    sem_cot = abertas[abertas["vl_custo_referencia"].isna() & ~abertas["fg_sem_mapeamento"]]
    sem_map = abertas[abertas["fg_sem_mapeamento"]]
    if abertas.empty:
        return "Não há novidades abertas na planilha."
    if cot.empty:
        return (f"Há {len(abertas)} novidades abertas, mas nenhuma com cotação. Sem custo não dá para projetar preço nem margem: "
                "peça a cotação ao agente comprador (feita no navegador do Hugo) para o ranking aparecer.")
    topo = cot.sort_values("score", ascending=False)
    ja = topo[topo["faixa"] == "Implementar já"]
    partes = [f"{len(cot)} das {len(abertas)} novidades abertas têm cotação."]
    if ja.empty:
        partes.append("Nenhuma chegou à faixa Implementar já; a melhor posição é de "
                      f"{topo.iloc[0]['nm_novidade']} (score {topo.iloc[0]['score']:.0f}, faixa {topo.iloc[0]['faixa']}).")
    else:
        itens = "; ".join(f"{r.nm_novidade} (custo {brl(r.vl_custo_referencia)}, preço sugerido {brl(r.vl_preco_recomendado)}, "
                          f"MC {brl(r.vl_mc_unitaria)} por unidade)" for r in ja.head(5).itertuples())
        partes.append(f"Na faixa Implementar já: {itens}. Capital dos lotes de teste dessa faixa: {brl(ja['vl_lote_teste_custo'].sum(), 0)}.")
    n_acima = int((cot["ds_posicao_preco_familia"] == "acima da faixa").sum())
    if n_acima:
        partes.append(f"{n_acima} têm preço sugerido acima da faixa que a família costuma vender: a meta de margem pede mais do que o cliente paga hoje nessa família, "
                      "então vale testar o preço antes de comprar o lote.")
    n_baixa = int((cot["confianca"] == "Baixa").sum())
    if n_baixa:
        partes.append(f"{n_baixa} têm confiança baixa (família com poucos pedidos ou cotação vencida); trate o score delas como indício.")
    if len(sem_cot):
        partes.append(f"Aguardam cotação: {len(sem_cot)} novidade(s).")
    if len(sem_map):
        partes.append(f"{len(sem_map)} novidade(s) nova(s) na planilha ainda sem mapeamento no dbt: {', '.join(sem_map['nm_novidade'].head(3))}.")
    return " ".join(partes)


def _detalhe(r, cot, prem):
    c1, c2 = st.columns(2)
    with c1:
        st.markdown(f"**{html.escape(r['nm_novidade'])}**")
        st.markdown(
            f"- Faixa: **{r['faixa']}**" + (f" · score {r['score']:.0f} · confiança {r['confianca']}" if pd.notna(r["score"]) else "") + "\n"
            f"- Papel pretendido: {r['ds_papel_pretendido']} · família: {r['ds_familia'] if pd.notna(r['ds_familia']) else '—'}\n"
            f"- Meta de margem: {pct(r['pr_meta_margem'], 0) if pd.notna(r['pr_meta_margem']) else 'sem meta (markup 2,5 sobre o custo)'} ({r['ds_fonte_meta']})")
        if pd.notna(r["score"]):
            comp = pd.DataFrame({"Componente": ["Margem (35%)", "Vendas da família (25%)", "Demanda do site (20%)", "Capital e payback (20%)"],
                                 "Posição (0–100)": [100 * r["c_margem"], 100 * r["c_venda"], 100 * r["c_demanda"], 100 * r["c_capital"]]})
            st.dataframe(comp, hide_index=True, use_container_width=True, column_config={
                "Posição (0–100)": st.column_config.ProgressColumn(format="%.0f", min_value=0, max_value=100)})
        if r["avisos"]:
            note("<b>Avisos:</b> " + html.escape(r["avisos"]), variant="warn")
    with c2:
        st.markdown("**Custo por fornecedor (última cotação)**")
        lc = cot[cot["ch_novidade"] == r["ch_novidade"]].copy()
        if lc.empty:
            st.info("A planilha não tem link para esta novidade.")
        else:
            lc["Fornecedor"] = lc["cd_fornecedor"].map(FORNECEDORES)
            lc["Tabela"] = lc["vl_preco_tabela"]
            lc["À vista (regular)"] = lc["vl_custo_regular"]
            lc["À vista (no dia)"] = lc["vl_preco_avista_observado"]
            lc["Cotado em"] = pd.to_datetime(lc["dt_cotacao"])
            lc["Situação"] = lc["ds_status_cotacao"]
            st.dataframe(lc[["Fornecedor", "Situação", "Tabela", "À vista (regular)", "À vista (no dia)", "Cotado em", "ds_url"]].rename(columns={"ds_url": "Link"}),
                         hide_index=True, use_container_width=True, column_config={
                "Tabela": st.column_config.NumberColumn(format="R$ %.2f"), "À vista (regular)": st.column_config.NumberColumn(format="R$ %.2f"),
                "À vista (no dia)": st.column_config.NumberColumn(format="R$ %.2f"), "Cotado em": st.column_config.DateColumn(format="DD/MM/YYYY"),
                "Link": st.column_config.LinkColumn("Link", display_text="abrir")})
        if pd.notna(r["vl_preco_recomendado"]):
            st.markdown(
                f"- Piso que entrega a meta: **{_brl(r['vl_preco_piso'])}** · sugerido (,90): **{_brl(r['vl_preco_recomendado'])}**\n"
                f"- MC por unidade: **{_brl(r['vl_mc_unitaria'])}** ({pct(r['pr_mc_projetada'])})\n"
                f"- Lote de teste: {int(r['qt_lote_teste'])} un = **{_brl(r['vl_lote_teste_custo'], 0)}**"
                + (f" · payback ~{r['qt_meses_payback_lote']:.1f} meses" if pd.notna(r["qt_meses_payback_lote"]) else ""))
    st.markdown("**A família do catálogo (proxy de demanda)**")
    if pd.notna(r["nr_produtos_familia"]) and r["nr_produtos_familia"] > 0:
        st.markdown(
            f"- {int(r['nr_pedidos_familia_12m'])} pedidos em 12 meses ({int(r['nr_pedidos_90d'])} nos últimos 90 dias) de {int(r['nr_produtos_familia'])} produtos · "
            f"preço vendido: P25 {_brl(r['vl_preco_familia_p25'])} · mediana {_brl(r['vl_preco_familia_mediana'])} · P75 {_brl(r['vl_preco_familia_p75'])} "
            f"→ preço sugerido **{r['ds_posicao_preco_familia']}**\n"
            f"- Demanda do site: busca interna {int(r['qt_busca_sessoes'])} sessões ({DIAS_BUSCA}d) · Google {int(r['qt_gsc_impressoes'])} impressões / {int(r['qt_gsc_cliques'])} cliques · "
            f"GA4 {int(r['qt_ga4_visitas'])} visitas e {int(r['qt_ga4_carrinhos'])} carrinhos ({DIAS_GA4}d)")
    else:
        st.info("Sem produto parecido no catálogo (nenhuma venda de proxy): a decisão não tem evidência de venda.")


def _lacunas(dados):
    atr, busca, gsc, cat = dados["atr"], dados["busca"], dados["gsc"], dados["catalogo"]
    vocab = set(re.findall(r"[a-z]{4,}", " ".join(cat["nm_produto"].map(_n).tolist() + cat["ds_subcategoria"].fillna("").map(_n).tolist())))
    rxs = [re.compile(x) for x in atr["ds_regex_busca"].dropna().unique()]

    def coberto(t):
        ws = re.findall(r"[a-z]{4,}", t)
        return bool(ws) and all(w in vocab or any(w[:5] == v[:5] for v in vocab) for w in ws)

    b = busca.assign(t=busca["termo"].map(_n)).groupby("t", as_index=False)["qt_sessoes"].sum()
    g = gsc.assign(t=gsc["termo"].map(_n)).groupby("t", as_index=False).agg(qt_impressoes=("qt_impressoes", "sum"), qt_cliques=("qt_cliques", "sum"))
    m = b.merge(g, on="t", how="outer").fillna(0)
    m = m[~m["t"].map(lambda x: bool(MARCA.search(x)))]
    m["tem_produto"] = m["t"].map(coberto)
    m["na_lista"] = m["t"].map(lambda t: any(x.search(t) for x in rxs))
    m["demanda"] = m["qt_sessoes"] + m["qt_impressoes"] / 20
    sem = m[~m["tem_produto"]].sort_values("demanda", ascending=False).head(25)
    sem = sem.assign(Situação=np.where(sem["na_lista"], "tema já coberto por novidade da lista", "fora da lista de novidades"))
    st.dataframe(sem.rename(columns={"t": "Termo", "qt_sessoes": f"Busca interna ({DIAS_BUSCA}d, sessões)", "qt_impressoes": "Google (impressões)", "qt_cliques": "Google (cliques)"})
                 [["Termo", f"Busca interna ({DIAS_BUSCA}d, sessões)", "Google (impressões)", "Google (cliques)", "Situação"]],
                 hide_index=True, use_container_width=True)
    note("Termos buscados no site ou no Google <b>sem nenhum produto parecido no catálogo</b> (ignorados os da marca e da frente Shibari). "
         "É um <b>radar, não uma recomendação</b>: os volumes são muito pequenos (a maioria tem 1 ou 2 sessões) e o Search Console só tem alguns dias. "
         "O que dá para afirmar hoje é o tema com demanda repetida; \"fora da lista de novidades\" é onde vale olhar se falta um tipo de item. Ganha força com 8 a 12 semanas de histórico.")
