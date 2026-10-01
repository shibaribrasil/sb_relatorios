"""Relatório Fechamento Financeiro (caixa) v2 — entradas reais do extrato da conta PJ — Shibari Brasil (camada mensal).

Regras: ver specs/fechamento-financeiro.md. Mesmas saídas da v1 (contas pagas no Bling); a diferença está nas ENTRADAS:
aqui é o dinheiro que caiu de fato na conta PJ (repasses do Nuvem Pago / Mercado Pago, `tb_extrato_conta`), e a página
compara com a estimativa da v1 (`tb_caixa_entrada_pedido`) para mostrar onde as duas divergem.
"""
import pandas as pd
import streamlit as st

from common import bigquery as bq
from common.design import inject_css, card, render_cards, section_title, note, brl
from common.frescor import carregar_frescor, detalhe_atualizacao
from reports.fechamento_financeiro import (carregar_caixa, carregar_saidas, cabecalho, seletor_mes,
                                           rotulo_mes, tabela_saidas_mes)

TABELAS = ("tb_caixa_mes", "tb_extrato_conta", "tb_caixa_saida")
EXTRATORES = ("bling_accounts_payable",)

ROTULO_CLASSE = {
    "repasse_nuvem_pago": "Repasse Nuvem Pago (venda)",
    "repasse_mercado_pago": "Repasse Mercado Pago (venda)",
    "aporte_socio": "Aporte de sócio",
    "outras_entradas": "Outras entradas (revisar)",
    "estorno_cliente": "Estorno a cliente",
    "imposto": "Imposto (DAS)",
    "transferencia_entre_contas": "Transferência entre contas da loja",
    "transferencia_socio": "Transferência a sócio (reembolso de cartão / retirada)",
    "pagamento_boleto": "Pagamento de boleto",
    "pagamento_pix": "Pagamento por Pix",
}


@st.cache_data(ttl=900)
def carregar_extrato():
    client = bq.get_client()
    x = bq.query_df(client, f"""
        SELECT cd_lancamento, ds_conta, dt_lancamento, dt_mes, vl_lancamento, ds_tipo, ds_descricao, ds_classe, fg_entrada_venda
          FROM `{bq.PROJECT}.dbt_dw_az.tb_extrato_conta` ORDER BY dt_lancamento, vl_lancamento""")
    x["dt_lancamento"] = pd.to_datetime(x["dt_lancamento"])
    x["dt_mes"] = pd.to_datetime(x["dt_mes"])
    x["vl_lancamento"] = pd.to_numeric(x["vl_lancamento"]).fillna(0.0)
    return x


def _comparativo(r):
    """v1 (estimado pelos pedidos) × v2 (caiu na conta), por origem."""
    linhas = [
        ("Nuvem Pago", r["vl_entrada_v1_nuvem_pago"], r["vl_entrada_v2_nuvem_pago"]),
        ("Mercado Pago", r["vl_entrada_v1_mercado_pago"], r["vl_entrada_v2_mercado_pago"]),
        ("Outras origens (Pagamentos Personalizados, Pix direto...)", r["vl_entrada_v1_outras"], 0.0),
        ("(−) Reembolsos / estornos", -r["vl_reembolso_v1"], -r["vl_estorno_v2"]),
        ("= Entrada líquida", r["vl_entrada_liquida_v1"], r["vl_entrada_liquida_v2"]),
    ]
    t = pd.DataFrame(linhas, columns=["Origem", "v1 — estimado pelos pedidos", "v2 — caiu na conta PJ"])
    t["v1 − v2"] = t["v1 — estimado pelos pedidos"] - t["v2 — caiu na conta PJ"]
    return t


def render():
    inject_css()
    with st.spinner("Carregando dados do BigQuery..."):
        try:
            d = carregar_caixa()
            x = carregar_extrato()
            s = carregar_saidas()
        except Exception as ex:
            st.error(f"Erro ao carregar dados do BigQuery: {ex}")
            return
    try:
        fr = carregar_frescor(TABELAS, EXTRATORES)
    except Exception:
        fr = None
    cabecalho("Fechamento Financeiro", "(extrato · v2)",
              "Regime de caixa · entradas = o que caiu na conta PJ (extrato do banco) · mesmas saídas da v1 · desde 09/2026", fr)
    if x.empty or not d["fg_extrato_disponivel"].any():
        st.info("Nenhum extrato importado ainda. Importe o CSV do banco com `scripts/importar_extrato.py` no sb_data_pipeline.")
        return

    d = d.sort_values("dt_mes")
    mes = seletor_mes(d, "mes_caixa_v2", so_com_extrato=True)
    r = d[d["dt_mes"] == mes].iloc[0]
    parcial = bool(r["fg_mes_parcial"])
    xm = x[x["dt_mes"] == mes]
    res = float(r["vl_resultado_caixa_v2"])
    dif = float(r["vl_dif_entrada_v1_v2"])
    ultimo = xm["dt_lancamento"].max()

    section_title("Caixa de " + rotulo_mes(mes, parcial) + " pelo extrato")
    note(f"Extrato importado até <strong>{ultimo:%d/%m/%Y}</strong> ({int(r['qt_lancamentos_extrato'])} lançamentos). "
         "Se o mês não estiver completo no extrato, os números abaixo estão incompletos: exporte o CSV do mês inteiro e importe de novo (não duplica).",
         variant="warn" if parcial else "")
    render_cards([
        card("Entrada de vendas", brl(float(r["vl_entrada_v2"])), "repasses do Nuvem Pago / Mercado Pago que caíram na conta"),
        card("Estornos pagos", brl(float(r["vl_estorno_v2"])), "devolvidos a clientes pela conta PJ"),
        card("Saídas", brl(float(r["vl_saida_total"])), "contas pagas no Bling (iguais à v1)"),
        card("Resultado de caixa", brl(res), "entrada líquida real − saídas", variant="ok" if res > 0 else "bad"),
        card("Diferença v1 − v2", brl(dif), "estimado pelos pedidos − caiu na conta",
             variant="warn" if abs(dif) > 0.05 * max(float(r["vl_entrada_liquida_v1"]), 1.0) else "ok"),
    ])
    note("<strong>Aporte de sócio não é venda</strong> e não entra no resultado"
         + (f" (neste mês: {brl(float(r['vl_aporte_socio']), 2)})" if float(r["vl_aporte_socio"] or 0) else "") + ". "
         "Transferência a sócio também não é saída aqui: ela reembolsa despesas pagas no cartão pessoal, que já estão no Bling como contas pagas.")

    section_title("Estimado (v1) × caiu na conta (v2)")
    t = _comparativo(r)
    st.dataframe(t, hide_index=True, use_container_width=True,
                 column_config={c: st.column_config.NumberColumn(format="R$ %.2f") for c in t.columns if c != "Origem"})
    note("<strong>Por que divergem:</strong> (1) <strong>saldo parado no gateway</strong> — a venda só vira dinheiro na conta quando há saque/repasse do Nuvem Pago; "
         "(2) <strong>prazo</strong> — cartão pago no fim do mês cai no mês seguinte; "
         "(3) <strong>descontos no saldo do gateway</strong> — estorno ou fatura debitados direto no Nuvem Pago reduzem o repasse sem passar pelo Bling; "
         "(4) recebimentos fora da loja e entradas não classificadas. Diferença que se repete mês a mês é sinal de saldo acumulando no gateway: confira o saldo no painel do Nuvem Pago.")

    section_title("Lançamentos do extrato")
    tx = pd.DataFrame({
        "Data": xm["dt_lancamento"].dt.strftime("%d/%m/%Y"),
        "Classe": xm["ds_classe"].map(ROTULO_CLASSE).fillna(xm["ds_classe"]),
        "Descrição do banco": xm["ds_descricao"],
        "Valor": xm["vl_lancamento"],
    })
    st.dataframe(tx, hide_index=True, use_container_width=True, column_config={"Valor": st.column_config.NumberColumn(format="R$ %.2f")})
    resumo = xm.groupby("ds_classe")["vl_lancamento"].agg(["count", "sum"]).reset_index()
    resumo["ds_classe"] = resumo["ds_classe"].map(ROTULO_CLASSE).fillna(resumo["ds_classe"])
    resumo.columns = ["Classe", "Lançamentos", "Total"]
    st.dataframe(resumo.sort_values("Total", ascending=False), hide_index=True, use_container_width=True,
                 column_config={"Total": st.column_config.NumberColumn(format="R$ %.2f")})
    note("Classificação por regra de texto da descrição do banco (no dbt, <code>tb_extrato_conta</code>): \"FITS IP\" = conta da loja no Nuvem Pago; CNPJ da loja no Mercado Pago = repasse do Mercado Pago; "
         "contas pessoais dos sócios = aporte/transferência a sócio. Lançamento em <em>Outras entradas</em> precisa de revisão: avise para virar regra.")

    section_title("Saídas pelo extrato × contas pagas no Bling")
    saidas_extrato = float(r["vl_saida_extrato_total"])
    render_cards([
        card("Saiu da conta PJ (extrato)", brl(saidas_extrato), "todas as saídas, inclusive transferências a sócios e entre contas"),
        card("Contas pagas (Bling)", brl(float(r["vl_saida_total"])), "inclui o que foi pago no cartão pessoal e reembolsado"),
    ])
    note("Não precisam ser iguais: parte das contas do Bling é paga no cartão (pessoal ou da loja) e sai do extrato como uma transferência só, no dia do reembolso; "
         "parte das saídas do extrato (estornos, transferências entre contas) não é conta a pagar. Use para conferir se há pagamento no extrato que não foi lançado no Bling.")
    with st.expander("Contas pagas no mês (Bling)"):
        st.dataframe(tabela_saidas_mes(s, mes), hide_index=True, use_container_width=True,
                     column_config={"Valor": st.column_config.NumberColumn(format="R$ %.2f")})

    note("<strong>Limites:</strong> só existem os meses com extrato importado (hoje a conta PJ desde 09/2026; a importação é manual, a partir do CSV do banco). "
         "O dinheiro que fica no saldo do Nuvem Pago não aparece no extrato até ser sacado.")
    if fr:
        detalhe_atualizacao(fr)
