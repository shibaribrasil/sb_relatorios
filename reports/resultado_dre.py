"""Relatório Resultado (DRE mensal) — Shibari Brasil (camada mensal).

Regras e definição de cada linha: ver specs/resultado-dre.md. A DRE é por COMPETÊNCIA e nasce no dbt (`tb_dre_mes`,
`tb_despesa_dre`): o topo é a mesma margem de contribuição da `tb_pedido` (reembolso no mês em que aconteceu) e as despesas
vêm do contas a pagar do Bling, já classificadas e sem duplicados. O Streamlit só apresenta, com uma exceção assumida:
o Google Ads vive no dataset US (não junta com a `az` em SQL) e é subtraído aqui, no pandas — a mesma exceção de "margem após mídia".
"""
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from common import bigquery as bq
from common.design import COLORS, METRIC_COLORS, inject_css, card, render_cards, section_title, note, plotly_layout, brl, pct
from common.frescor import carregar_frescor, badge_atualizacao, detalhe_atualizacao, alerta_atraso
from reports.vendas_margem import _hoje_brt

TABELAS = ("tb_dre_mes", "tb_despesa_dre", "tb_pedido")
EXTRATORES = ("bling_orders", "nuvemshop_orders", "bling_accounts_payable")


@st.cache_data(ttl=900)
def carregar_dre():
    client = bq.get_client()
    dre = bq.query_df(client, f"SELECT * FROM `{bq.PROJECT}.dbt_dw_az.tb_dre_mes` ORDER BY dt_mes")
    ads = bq.query_df(client, f"""
        SELECT DATE_TRUNC(dt_data, MONTH) AS dt_mes, SUM(vl_custo) AS vl_google_ads, MAX(dt_data) AS dt_ultimo_dia
          FROM `{bq.PROJECT}.dbt_dw_us_az.tb_gads_conta_diario` GROUP BY 1
    """)
    dre["dt_mes"] = pd.to_datetime(dre["dt_mes"])
    ads["dt_mes"] = pd.to_datetime(ads["dt_mes"])
    for c in dre.columns:
        if c not in ("dt_mes", "fg_mes_parcial"):
            dre[c] = pd.to_numeric(dre[c]).fillna(0.0)
    dre["fg_mes_parcial"] = dre["fg_mes_parcial"].fillna(False).astype(bool)
    ads["vl_google_ads"] = pd.to_numeric(ads["vl_google_ads"]).fillna(0.0)
    d = dre.merge(ads[["dt_mes", "vl_google_ads"]], on="dt_mes", how="left")
    d["vl_google_ads"] = d["vl_google_ads"].fillna(0.0)
    # resultado depois da mídia do Ads: única conta feita aqui (a mídia do Ads não está na az)
    d["vl_margem_apos_midia"] = d["vl_margem_contribuicao"] - d["vl_google_ads"] - d["vl_midia_bling"]
    d["vl_resultado_operacional"] = d["vl_margem_apos_midia"] - d["vl_despesa_operacional"]
    d["vl_midia_total"] = d["vl_google_ads"] + d["vl_midia_bling"]
    return d


def _rotulo(m, parcial):
    return pd.Timestamp(m).strftime("%m/%Y") + (" (em andamento)" if parcial else "")


def _tabela_dre(d):
    """Demonstrativo por mês: deduções negativas, subtotais com '='. Valores do dbt, sem recálculo (exceto Ads)."""
    linhas = [
        ("Receita líquida de produtos", "vl_receita_liquida_produto", 1),
        ("(+) Resultado de frete (frete pago − frete real)", "vl_resultado_frete", 1),
        ("(−) CMV (custo dos produtos vendidos)", "vl_cmv", -1),
        ("(−) Taxa de pagamento", "vl_taxa_pagamento", -1),
        ("(−) Embalagem (estimada)", "vl_embalagem", -1),
        ("(−) Imposto (0% sem CNPJ)", "vl_imposto", -1),
        ("(−) Reembolsos (no mês em que aconteceram)", "vl_reembolso", -1),
        ("= Margem de contribuição (antes de mídia)", "vl_margem_contribuicao", 1),
        ("(−) Google Ads", "vl_google_ads", -1),
        ("(−) Meta e outras mídias (lançadas no Bling)", "vl_midia_bling", -1),
        ("= Margem depois da mídia", "vl_margem_apos_midia", 1),
        ("(−) Pró-labore", "vl_pessoal", -1),
        ("(−) Ferramentas e tecnologia", "vl_ferramentas", -1),
        ("(−) Despesas adicionais", "vl_adicionais", -1),
        ("(−) Despesas financeiras", "vl_financeira", -1),
        ("(−) Sem categoria", "vl_sem_categoria", -1),
        ("= Resultado operacional (antes de imposto)", "vl_resultado_operacional", 1),
    ]
    out = {"Linha": [n for n, _, _ in linhas]}
    for _, r in d.iterrows():
        out[_rotulo(r["dt_mes"], r["fg_mes_parcial"])] = [sinal * float(r[col]) + 0.0 for _, col, sinal in linhas]
    return pd.DataFrame(out)


def _grafico_cascata(r):
    x = ["Margem de contribuição", "Google Ads", "Meta e outras mídias", "Pró-labore", "Ferramentas", "Adicionais + financeiras", "Resultado operacional"]
    vals = [r["vl_margem_contribuicao"], -r["vl_google_ads"], -r["vl_midia_bling"], -r["vl_pessoal"], -r["vl_ferramentas"],
            -(r["vl_adicionais"] + r["vl_financeira"] + r["vl_sem_categoria"]), r["vl_resultado_operacional"]]
    fig = go.Figure(go.Waterfall(
        x=x, y=vals, measure=["absolute", "relative", "relative", "relative", "relative", "relative", "total"],
        increasing=dict(marker=dict(color=COLORS["success"])), decreasing=dict(marker=dict(color=COLORS["danger"])),
        totals=dict(marker=dict(color=METRIC_COLORS["receita"])), connector=dict(line=dict(color=COLORS["grid"])),
        hovertemplate="%{x}<br>R$ %{y:,.0f}<extra></extra>"))
    plotly_layout(fig, height=320, showlegend=False, yaxis=dict(tickprefix="R$ ", gridcolor=COLORS["grid"]), xaxis=dict(tickangle=-20))
    return fig


def render():
    inject_css()
    with st.spinner("Carregando dados do BigQuery..."):
        try:
            d = carregar_dre()
        except Exception as e:
            st.error(f"Erro ao carregar dados do BigQuery: {e}")
            return
    hoje = _hoje_brt()
    try:
        fr = carregar_frescor(TABELAS, EXTRATORES)
    except Exception:
        fr = None
    st.html(f"""
    <div class="report-header">
      <div>
        <div class="report-brand">shibari brasil · camada mensal</div>
        <div class="report-title">Resultado <span>(DRE)</span></div>
        <div class="report-meta">Por competência · margem de contribuição − mídia − despesas operacionais do Bling · antes de imposto · desde 08/2026</div>
      </div>
      {badge_atualizacao(fr) if fr else f'<div class="report-badge">Hoje: <strong>{hoje.strftime("%d/%m/%Y")}</strong></div>'}
    </div>
    """)
    if fr:
        alerta_atraso(fr)
    if d.empty:
        st.info("Ainda sem meses na DRE.")
        return

    d = d.sort_values("dt_mes")
    fechados = d[~d["fg_mes_parcial"]]
    ordem = list(d.sort_values("dt_mes", ascending=False)["dt_mes"])
    padrao = ordem.index(fechados["dt_mes"].max()) if not fechados.empty else 0
    parcial_por_mes = dict(zip(d["dt_mes"], d["fg_mes_parcial"]))
    mes = st.selectbox("Mês", options=ordem, index=padrao, format_func=lambda m: _rotulo(m, parcial_por_mes[m]))
    r = d[d["dt_mes"] == mes].iloc[0]
    parcial = bool(r["fg_mes_parcial"])

    fat, mc = float(r["vl_faturamento"]), float(r["vl_margem_contribuicao"])
    fixo = float(r["vl_midia_total"] + r["vl_despesa_operacional"])
    mc_pct_fat = (mc / fat) if fat else None
    equilibrio = (fixo / mc_pct_fat) if mc_pct_fat and mc_pct_fat > 0 else None
    res = float(r["vl_resultado_operacional"])

    section_title("Resultado de " + _rotulo(mes, parcial))
    if parcial:
        note("<strong>Mês em andamento:</strong> a receita é até hoje, e a despesa é só a que já foi lançada no Bling (o pró-labore e outras contas do mês podem ainda não estar lançados). "
             "O resultado tende a estar <strong>melhor do que será</strong> no fechamento: espere o mês fechar antes de tirar conclusão.", variant="warn")
    render_cards([
        card("Faturamento", brl(fat), f"{int(r['qt_pedidos'])} pedidos · produtos líquidos + frete pago"),
        card("Margem de contribuição", brl(mc), f"antes de mídia · {pct(mc / float(r['vl_receita_liquida_produto']) if r['vl_receita_liquida_produto'] else None)} da receita líquida de produtos"),
        card("Mídia", brl(float(r["vl_midia_total"])), f"Google Ads {brl(float(r['vl_google_ads']), 0)} + Meta e outras {brl(float(r['vl_midia_bling']), 0)}"),
        card("Despesas operacionais", brl(float(r["vl_despesa_operacional"])), "pró-labore, ferramentas, adicionais e financeiras (Bling)"),
        card("Resultado operacional", brl(res), "margem − mídia − despesas · antes de imposto", variant="ok" if res > 0 else "bad"),
        card("Ponto de equilíbrio", brl(equilibrio) if equilibrio else "—",
             (f"faturamento para empatar · realizado {brl(fat, 0)} ({pct(fat / equilibrio, 0)})" if equilibrio else "margem de contribuição não positiva"),
             variant=("ok" if equilibrio and fat >= equilibrio else "warn" if equilibrio else "neutral")),
    ])
    note("<strong>Resultado operacional</strong> = margem de contribuição − mídia (Google Ads do próprio Ads + Meta lançada no Bling) − despesas operacionais do Bling, por competência e antes de imposto. "
         "<strong>Ponto de equilíbrio</strong> = (mídia + despesas operacionais) ÷ (margem de contribuição ÷ faturamento): o faturamento que empata o mês com a margem do próprio mês. "
         "Com ~40 pedidos por mês, o resultado de um mês oscila com o mix de produtos e com o calendário de pagamentos: leia a tendência de vários meses.")

    section_title("Da margem ao resultado")
    with st.container(border=True):
        st.plotly_chart(_grafico_cascata(r), use_container_width=True)

    section_title("Demonstrativo por mês")
    t = _tabela_dre(d.sort_values("dt_mes"))
    cfg = {c: st.column_config.NumberColumn(format="R$ %.0f", width=140) for c in t.columns if c != "Linha"}
    cfg["Linha"] = st.column_config.TextColumn(width=330)
    st.dataframe(t, hide_index=True, use_container_width=True, column_config=cfg, height=38 + 35 * len(t))
    note("Deduções em negativo. Do topo até a <strong>margem de contribuição</strong> é a mesma conta de Vendas & Margem (vem pronta do dbt, <code>tb_pedido</code>). "
         "<strong>Google Ads</strong> vem do próprio Ads por mês de consumo (o lançamento no Bling sai um mês depois). "
         "As <strong>despesas</strong> vêm do contas a pagar do Bling por <em>competência</em>, contando Pago, Atrasado e Em Aberto, sem meses futuros.")

    section_title("O que ficou de fora do resultado, e por quê")
    fora = d.sort_values("dt_mes")
    tab = pd.DataFrame({
        "Mês": [_rotulo(m, p) for m, p in zip(fora["dt_mes"], fora["fg_mes_parcial"])],
        "Total lançado no Bling": fora["vl_contas_lancado"],
        "Mercadoria (o CMV já está na margem)": fora["vl_excl_mercadoria"],
        "Frete (o frete real já está na margem)": fora["vl_excl_frete"],
        "Google Ads lançado (usa-se o do Ads)": fora["vl_excl_google_ads"],
        "Duplicados": fora["vl_excl_duplicado"],
        "Investimentos (memo)": fora["vl_memo_investimento"],
        "Suprimentos (memo)": fora["vl_memo_suprimentos"],
        "Entra no resultado": fora["vl_despesa_operacional"] + fora["vl_midia_bling"],
    })
    st.dataframe(tab, hide_index=True, use_container_width=True,
                 column_config={c: st.column_config.NumberColumn(format="R$ %.0f") for c in tab.columns if c != "Mês"})
    note("Fecha com o contas a pagar: <em>total lançado</em> = tudo o que aparece nas outras colunas (teste no dbt). "
         "<strong>Duplicados:</strong> o Bling tem o mesmo gasto lançado duas vezes (um com o nome antigo, outro com o novo; mesmo mês, fornecedor e valor): conta uma vez só. "
         "<strong>Investimentos</strong> (estante, caixas) são capex e ficam abaixo da linha. <strong>Suprimentos</strong> (caixas, material de envio): a embalagem já entra <em>estimada</em> "
         f"na margem (R$ 2,50 por pedido); neste mês foram lançados {brl(float(r['vl_memo_suprimentos']), 0)} contra {brl(float(r['vl_embalagem']), 0)} estimados — útil para calibrar a estimativa.")

    note("<strong>Limites:</strong> só há despesa lançada por competência a partir de 08/2026. O cartão Nubank <em>não</em> entra (decisão: o Bling é a fonte única): gasto que não foi lançado no Bling não aparece, "
         "então o resultado é um <strong>teto</strong>. A mídia de Meta só existe quando é lançada no Bling. Imposto 0% até haver CNPJ; embalagem é estimada.")
    if fr:
        detalhe_atualizacao(fr)
