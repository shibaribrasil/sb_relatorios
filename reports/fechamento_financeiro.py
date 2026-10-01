"""Relatório Fechamento Financeiro (caixa) v1 — Shibari Brasil (camada mensal).

Regras: ver specs/fechamento-financeiro.md. Regime de CAIXA (entra quando recebe, sai quando paga), visão financeira
complementar à DRE (gerencial, por competência). Tudo nasce no dbt (`tb_caixa_mes`, `tb_caixa_entrada_pedido`,
`tb_caixa_saida`); a página filtra, soma e apresenta. Única conta fora do dbt: a ponte com o Google Ads consumido, que
vive no dataset US e é juntado no pandas (mesma exceção da DRE).

A v2 (entradas reais do extrato da conta PJ) está em `reports/fechamento_financeiro_extrato.py` e reaproveita as cargas daqui.
"""
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from common import bigquery as bq
from common.design import COLORS, METRIC_COLORS, inject_css, card, render_cards, section_title, note, plotly_layout, brl, pct, demonstrativo, regras_aplicadas
from common.frescor import carregar_frescor, badge_atualizacao, detalhe_atualizacao, alerta_atraso
from reports.vendas_margem import _hoje_brt

TABELAS = ("tb_caixa_mes", "tb_caixa_entrada_pedido", "tb_caixa_saida")
EXTRATORES = ("nuvemshop_orders", "bling_accounts_payable")

LINHAS_SAIDA = [
    ("Mercadoria e matéria-prima", "vl_saida_mercadoria"),
    ("Frete (fatura)", "vl_saida_frete"),
    ("Google Ads (pago no mês, consumo do mês anterior)", "vl_saida_midia_google"),
    ("Meta e outras mídias", "vl_saida_midia_outras"),
    ("Pró-labore", "vl_saida_pessoal"),
    ("Ferramentas e tecnologia", "vl_saida_ferramentas"),
    ("Suprimentos (caixas, material de envio)", "vl_saida_suprimentos"),
    ("Investimentos e melhorias", "vl_saida_investimento"),
    ("Despesas financeiras", "vl_saida_financeira"),
    ("Despesas adicionais", "vl_saida_adicionais"),
    ("Sem categoria", "vl_saida_sem_categoria"),
]
ROTULO_LINHA = {
    "mercadoria": "Mercadoria", "frete": "Frete", "midia_google": "Google Ads", "midia_meta": "Meta", "midia_outras": "Outras mídias",
    "pessoal": "Pró-labore", "ferramentas": "Ferramentas", "suprimentos": "Suprimentos", "investimento": "Investimento",
    "financeira": "Financeira", "adicionais": "Adicionais", "sem_categoria": "Sem categoria",
}


def _num(df, excluir):
    for c in df.columns:
        if c not in excluir:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


@st.cache_data(ttl=900)
def carregar_caixa():
    """tb_caixa_mes (1 linha por mês). Colunas da v2 ficam NaN nos meses sem extrato."""
    client = bq.get_client()
    d = bq.query_df(client, f"SELECT * EXCEPT(ts_load) FROM `{bq.PROJECT}.dbt_dw_az.tb_caixa_mes` ORDER BY dt_mes")
    d["dt_mes"] = pd.to_datetime(d["dt_mes"])
    d = _num(d, ("dt_mes", "fg_mes_parcial", "fg_extrato_disponivel"))
    d["fg_mes_parcial"] = d["fg_mes_parcial"].fillna(False).astype(bool)
    d["fg_extrato_disponivel"] = d["fg_extrato_disponivel"].fillna(False).astype(bool)
    return d


@st.cache_data(ttl=900)
def carregar_entradas():
    client = bq.get_client()
    e = bq.query_df(client, f"""
        SELECT cd_codigo_interno, cd_pedido_nuvemshop, nm_loja, dt_pedido, ds_origem_recebimento, ds_meio_recebimento,
               dt_pagamento_venda, qt_dias_recebimento, dt_recebimento, vl_faturamento, vl_taxa, vl_recebido, vl_reembolso, dt_reembolso
          FROM `{bq.PROJECT}.dbt_dw_az.tb_caixa_entrada_pedido`
         WHERE dt_recebimento >= DATE '2026-08-01' OR dt_reembolso >= DATE '2026-08-01'""")
    for c in ("dt_pedido", "dt_pagamento_venda", "dt_recebimento", "dt_reembolso"):
        e[c] = pd.to_datetime(e[c])
    for c in ("vl_faturamento", "vl_taxa", "vl_recebido", "vl_reembolso", "qt_dias_recebimento"):
        e[c] = pd.to_numeric(e[c]).fillna(0.0)
    return e


@st.cache_data(ttl=900)
def carregar_saidas():
    client = bq.get_client()
    s = bq.query_df(client, f"""
        SELECT id, dt_pagamento, ds_origem_data, dt_mes, dt_vencimento, dt_competencia, ds_descricao, nm_fornecedor, ds_subcategoria,
               ds_situacao, nm_forma_pagamento, vl_valor, ds_linha_caixa, ds_tratamento
          FROM `{bq.PROJECT}.dbt_dw_az.tb_caixa_saida`""")
    for c in ("dt_pagamento", "dt_mes", "dt_vencimento", "dt_competencia"):
        s[c] = pd.to_datetime(s[c])
    s["vl_valor"] = pd.to_numeric(s["vl_valor"]).fillna(0.0)
    return s


@st.cache_data(ttl=900)
def carregar_ponte():
    """Lado gerencial da ponte: CMV, frete real e embalagem da DRE (az) + Google Ads consumido (US)."""
    client = bq.get_client()
    g = bq.query_df(client, f"""SELECT dt_mes, vl_cmv, vl_frete_real, vl_embalagem, vl_faturamento
                                 FROM `{bq.PROJECT}.dbt_dw_az.tb_dre_mes`""")
    ads = bq.query_df(client, f"""SELECT DATE_TRUNC(dt_data, MONTH) AS dt_mes, SUM(vl_custo) AS vl_google_ads
                                   FROM `{bq.PROJECT}.dbt_dw_us_az.tb_gads_conta_diario`
                                  WHERE dt_data >= DATE '2026-07-01' GROUP BY 1""")
    g["dt_mes"] = pd.to_datetime(g["dt_mes"])
    ads["dt_mes"] = pd.to_datetime(ads["dt_mes"])
    g = _num(g, ("dt_mes",))
    ads = _num(ads, ("dt_mes",))
    return g, ads


def rotulo_mes(m, parcial):
    return pd.Timestamp(m).strftime("%m/%Y") + (" (em andamento)" if parcial else "")


def seletor_mes(d, chave, so_com_extrato=False):
    base = d[d["fg_extrato_disponivel"]] if so_com_extrato else d
    if base.empty:
        return None
    fechados = base[~base["fg_mes_parcial"]]
    ordem = list(base.sort_values("dt_mes", ascending=False)["dt_mes"])
    padrao = ordem.index(fechados["dt_mes"].max()) if not fechados.empty else 0
    parcial = dict(zip(base["dt_mes"], base["fg_mes_parcial"]))
    return st.selectbox("Mês", options=ordem, index=padrao, format_func=lambda m: rotulo_mes(m, parcial[m]), key=chave)


def cabecalho(titulo, destaque, meta, fr):
    hoje = _hoje_brt()
    st.html(f"""
    <div class="report-header">
      <div>
        <div class="report-brand">shibari brasil · camada mensal</div>
        <div class="report-title">{titulo} <span>{destaque}</span></div>
        <div class="report-meta">{meta}</div>
      </div>
      {badge_atualizacao(fr) if fr else f'<div class="report-badge">Hoje: <strong>{hoje.strftime("%d/%m/%Y")}</strong></div>'}
    </div>
    """)
    if fr:
        alerta_atraso(fr)


def tabela_saidas_mes(s, mes):
    """Lançamentos que saíram no mês (inclui duplicados excluídos, sinalizados)."""
    m = s[s["dt_mes"] == mes].sort_values(["dt_pagamento", "vl_valor"], ascending=[True, False])
    return pd.DataFrame({
        "Pago em": m["dt_pagamento"].dt.strftime("%d/%m/%Y"),
        "Data de": m["ds_origem_data"].map({"baixa": "baixa no Bling", "vencimento": "vencimento (sem baixa)"}),
        "Linha": m["ds_linha_caixa"].map(ROTULO_LINHA).fillna(m["ds_linha_caixa"]),
        "Fornecedor": m["nm_fornecedor"].fillna("—"),
        "Descrição": m["ds_descricao"].fillna(""),
        "Forma de pagamento": m["nm_forma_pagamento"].fillna("—"),
        "Valor": m["vl_valor"],
        "Conta?": m["ds_tratamento"].map({"saida": "sim", "excluido_duplicado": "não (duplicado)"}),
    })


def _linhas_caixa(d):
    """Demonstrativo de caixa por mês, com o tipo de cada linha (cor)."""
    linhas = [
        ("(+) Vendas recebidas (produtos líquidos + frete pago)", "entrada", "vl_faturamento_recebido", 1),
        ("(−) Taxa de pagamento (retida na venda)", "saida", "vl_taxa", -1),
        ("= Recebido dos pedidos", "subtotal", "vl_entrada_v1", 1),
        ("(−) Reembolsos (no mês em que aconteceram)", "saida", "vl_reembolso_v1", -1),
        ("= Entrada líquida", "subtotal", "vl_entrada_liquida_v1", 1),
    ] + [(f"(−) {n}", "saida", c, -1) for n, c in LINHAS_SAIDA] + [
        ("= Total de saídas", "subtotal", "vl_saida_total", -1),
        ("= Resultado de caixa", "resultado", "vl_resultado_caixa_v1", 1),
    ]
    return [(n, tipo, [sinal * float(v) for v in d[col]]) for n, tipo, col, sinal in linhas]


def _grafico_meses(d):
    x = [rotulo_mes(m, p) for m, p in zip(d["dt_mes"], d["fg_mes_parcial"])]
    fig = go.Figure()
    fig.add_bar(x=x, y=d["vl_entrada_liquida_v1"], name="Entrada líquida", marker_color=COLORS["success"],
                hovertemplate="%{x}<br>Entradas R$ %{y:,.0f}<extra></extra>")
    fig.add_bar(x=x, y=-d["vl_saida_total"], name="Saídas", marker_color=COLORS["danger"],
                hovertemplate="%{x}<br>Saídas R$ %{y:,.0f}<extra></extra>")
    fig.add_scatter(x=x, y=d["vl_resultado_caixa_v1"], name="Resultado de caixa", mode="lines+markers",
                    line=dict(color=COLORS["text"], width=2), hovertemplate="%{x}<br>Resultado R$ %{y:,.0f}<extra></extra>")
    plotly_layout(fig, height=300, barmode="relative", yaxis=dict(tickprefix="R$ ", gridcolor=COLORS["grid"]))
    return fig


def render():
    inject_css()
    with st.spinner("Carregando dados do BigQuery..."):
        try:
            d = carregar_caixa()
            e = carregar_entradas()
            s = carregar_saidas()
        except Exception as ex:
            st.error(f"Erro ao carregar dados do BigQuery: {ex}")
            return
    try:
        fr = carregar_frescor(TABELAS, EXTRATORES)
    except Exception:
        fr = None
    cabecalho("Fechamento Financeiro", "(caixa · v1)",
              "Regime de caixa · entra quando recebe, sai quando paga · entradas estimadas pelos pedidos · desde 08/2026", fr)
    if d.empty:
        st.info("Ainda sem meses no fechamento financeiro.")
        return

    d = d.sort_values("dt_mes")
    mes = seletor_mes(d, "mes_caixa_v1")
    r = d[d["dt_mes"] == mes].iloc[0]
    parcial = bool(r["fg_mes_parcial"])
    res = float(r["vl_resultado_caixa_v1"])

    section_title("Caixa de " + rotulo_mes(mes, parcial))
    if parcial:
        note("<strong>Mês em andamento:</strong> entradas e saídas até hoje. Contas ainda não pagas e vendas no cartão dos últimos dias ainda não entraram.", variant="warn")
    render_cards([
        card("Entrada líquida", brl(float(r["vl_entrada_liquida_v1"])),
             f"{int(r['qt_pedidos_recebidos'])} pedidos recebidos · já sem a taxa e sem reembolsos"),
        card("Taxa retida", brl(float(r["vl_taxa"])),
             f"{pct(float(r['vl_taxa']) / float(r['vl_faturamento_recebido']) if r['vl_faturamento_recebido'] else None)} das vendas recebidas"),
        card("Saídas", brl(float(r["vl_saida_total"])), "contas pagas no mês (Bling), inclusive mercadoria e mídia M+1"),
        card("Resultado de caixa", brl(res), "entrada líquida − saídas", variant="ok" if res > 0 else "bad"),
    ])
    note("<strong>Visão financeira</strong>, não gerencial: aqui a compra de mercadoria, a fatura de frete e o Google Ads entram <em>quando são pagos</em> "
         "(o Google de um mês é pago no seguinte). Para saber se a operação dá lucro, use <strong>Resultado (DRE)</strong>, que é por competência. "
         "Mês negativo por compra grande é resultado de caixa, não é ajustado.")

    section_title("Entradas por origem do pagamento")
    em = e[(e["dt_recebimento"].dt.to_period("M") == pd.Timestamp(mes).to_period("M"))]
    if em.empty:
        st.info("Nenhum recebimento neste mês.")
    else:
        g = (em.groupby(["ds_origem_recebimento", "ds_meio_recebimento"], dropna=False)
               .agg(pedidos=("cd_codigo_interno", "count"), vendas=("vl_faturamento", "sum"), taxa=("vl_taxa", "sum"),
                    recebido=("vl_recebido", "sum"), prazo=("qt_dias_recebimento", "max"))
               .reset_index().sort_values("recebido", ascending=False))
        tot = g["recebido"].sum()
        tab = pd.DataFrame({
            "Origem": g["ds_origem_recebimento"].fillna("—"), "Meio": g["ds_meio_recebimento"].fillna("—"),
            "Pedidos": g["pedidos"], "Vendas": g["vendas"], "Taxa": g["taxa"], "Recebido": g["recebido"],
            "% do recebido": g["recebido"] / tot if tot else None, "Prazo (dias)": g["prazo"].astype(int),
        })
        st.dataframe(tab, hide_index=True, use_container_width=True, column_config={
            "Vendas": st.column_config.NumberColumn(format="R$ %.2f"), "Taxa": st.column_config.NumberColumn(format="R$ %.2f"),
            "Recebido": st.column_config.NumberColumn(format="R$ %.2f"), "% do recebido": st.column_config.NumberColumn(format="percent"),
        })
    note("Pedido da loja Nuvemshop: origem e meio vêm da Nuvemshop (o Bling marca tudo como \"[Nuvem] PIX\"); pedidos de outras lojas (marketplace) usam a forma de pagamento do Bling. "
         "Data de recebimento = dia em que o cliente pagou + prazo da origem × meio (Nuvem Pago: Pix 0, cartão 2, boleto 2 dias). "
         "<strong>O prazo do cartão é o do cadastro do Bling e precisa ser confirmado.</strong> É estimativa: o que caiu de fato na conta está na v2 (extrato).")

    section_title("Mês a mês")
    with st.container(border=True):
        st.plotly_chart(_grafico_meses(d), use_container_width=True)
    demonstrativo(_linhas_caixa(d), [rotulo_mes(m, p) for m, p in zip(d["dt_mes"], d["fg_mes_parcial"])], ocultar_zeradas=True)
    note("Verde = entra, vermelho = sai (em negativo), azul = subtotais; o resultado fica verde ou vermelho pelo sinal. Linhas zeradas em todos os meses ficam ocultas. As saídas são o contas a pagar do Bling pela <strong>data da baixa</strong> (quando não há baixa, o vencimento), só até hoje; "
         "o mesmo gasto lançado duas vezes (mesmo mês de pagamento e de competência, fornecedor e valor) conta uma vez. Reembolso parcial sai no mês em que aconteceu; pedido cancelado ou estornado por inteiro não entra.")

    section_title("Ponte com a visão gerencial (DRE)")
    try:
        ger, ads = carregar_ponte()
        mes_ant = pd.Timestamp(mes) - pd.DateOffset(months=1)
        gm = ger[ger["dt_mes"] == pd.Timestamp(mes)]
        ga = ger[ger["dt_mes"] == mes_ant]
        ads_ant = ads[ads["dt_mes"] == mes_ant]["vl_google_ads"].sum()
        ads_mes = ads[ads["dt_mes"] == pd.Timestamp(mes)]["vl_google_ads"].sum()
        def _l(item, ger_mes, ger_ant, fin, esperado):
            return {"Item": item, "Gerencial no mês": float(ger_mes), "Gerencial no mês anterior": float(ger_ant),
                    "Financeiro (pago no mês)": float(fin), "Como ler": esperado}
        ponte = pd.DataFrame([
            _l("Mercadoria", gm["vl_cmv"].sum(), ga["vl_cmv"].sum(), r["vl_saida_mercadoria"],
               "compras pagas × custo do que foi vendido: a diferença é o estoque; compare em vários meses"),
            _l("Frete", gm["vl_frete_real"].sum(), ga["vl_frete_real"].sum(), r["vl_saida_frete"],
               "fatura da Nuvemshop: pode ser do próprio mês ou do anterior (veja a descrição do lançamento)"),
            _l("Google Ads", ads_mes, ads_ant, r["vl_saida_midia_google"],
               "pago no mês = consumo do mês anterior (M+1)"),
            _l("Embalagem", gm["vl_embalagem"].sum(), ga["vl_embalagem"].sum(), r["vl_saida_suprimentos"],
               "estimativa (R$ 2,50/pedido) × suprimentos comprados"),
        ])
        cols_v = ("Gerencial no mês", "Gerencial no mês anterior", "Financeiro (pago no mês)")
        st.dataframe(ponte, hide_index=True, use_container_width=True,
                     column_config={c: st.column_config.NumberColumn(format="R$ %.0f") for c in cols_v})
        note("Serve para checar se os lançamentos fazem sentido com as vendas. O gerencial é por competência (mês da venda ou do consumo); o financeiro é o que foi pago no mês. Google Ads é pago em M+1; a fatura de frete varia. "
             "Mercadoria nunca fecha num mês só (a diferença é o estoque); se ficar sempre muito acima ou abaixo por vários meses, há compra não lançada, lançamento em dobro ou custo de produto errado no cadastro. "
             "Frete pago zerado com frete real alto no mês anterior indica fatura ainda não lançada no Bling, ou descontada direto do saldo do Nuvem Pago.")
    except Exception as ex:
        st.warning(f"Ponte com a DRE indisponível: {ex}")

    section_title("O que saiu no mês")
    ts = tabela_saidas_mes(s, mes)
    if ts.empty:
        st.info("Nenhuma conta paga neste mês.")
    else:
        st.dataframe(ts, hide_index=True, use_container_width=True, column_config={"Valor": st.column_config.NumberColumn(format="R$ %.2f")})
        sem_baixa = (s[(s["dt_mes"] == mes)]["ds_origem_data"] == "vencimento").sum()
        if sem_baixa:
            note(f"<strong>{sem_baixa} lançamento(s) sem baixa no Bling</strong> entraram pela data de vencimento. Dê baixa com a data real do pagamento para o caixa ficar exato.", variant="warn")

    note("<strong>Limites:</strong> só existe o que foi lançado no Bling; saídas desde 08/2026. O saldo parado no Nuvem Pago não aparece aqui: a entrada é contada quando o pedido é recebido no gateway, "
         "não quando o dinheiro é sacado para a conta PJ (isso é a v2). Mês corrente parcial.")
    if fr:
        detalhe_atualizacao(fr)
    regras_aplicadas([
        ("Visão", "financeira, regime de <strong>caixa</strong> — <strong>v1: entradas estimadas pelos pedidos</strong>. Entra quando o dinheiro fica disponível no gateway; sai quando a conta é paga."),
        ("Entradas", "pedidos válidos; vendas (produtos líquidos de desconto + frete pago) menos a taxa que o gateway retém na venda."),
        ("Data e origem do recebimento", "loja Nuvemshop: origem, meio e data de pagamento da Nuvemshop; outras lojas (marketplace): forma de pagamento do Bling. Data = pagamento do cliente + prazo (Nuvem Pago: Pix 0, cartão 2, boleto 2 dias; Mercado Pago: Pix 0, cartão 0, boleto 3)."),
        ("Reembolsos", "parcial sai no mês em que aconteceu; pedido cancelado ou estornado por inteiro não entra."),
        ("Saídas", "contas a pagar do Bling pela <strong>data da baixa</strong> (sem baixa, o vencimento), só até hoje. Tudo entra: mercadoria, fatura de frete, mídia (Google pago em M+1), investimento, suprimentos. Lançamento repetido (mesmo mês de pagamento e de competência, fornecedor e valor) conta uma vez; conta apagada no Bling não entra."),
        ("Resultado de caixa", "entrada líquida − saídas. Mês negativo por compra grande não é ajustado."),
        ("Limites", "desde 08/2026; gasto não lançado no Bling não existe aqui; o saldo parado no Nuvem Pago não aparece (ver v2, extrato); prazo do cartão a confirmar; mês corrente parcial."),
    ], titulo="Regras aplicadas — v1 (estimado pelos pedidos)")
