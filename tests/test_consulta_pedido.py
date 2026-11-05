"""Consulta de Pedidos: a cascata fecha com a margem do dbt e o custo de aquisição segue cada regra (specs/consulta-pedido.md)."""
import pandas as pd
import pytest

from reports import consulta_pedido as cp

DIA = pd.Timestamp("2026-11-02")


def _linhas():
    """Pedido real #2603 (04/11/2026): kit + brinde (sticker), frete 27,35 pago = etiqueta, cartão."""
    base = dict(vl_desconto_cupom_rateio=0.0, vl_desconto_pagamento_rateio=0.0, vl_desconto_promocional_rateio=0.0, vl_reembolso_rateio=0.0)
    kit = dict(base, fg_brinde=False, vl_receita_bruta_produto=149.99, vl_receita_liquida_produto=149.99, vl_frete_pago_rateio=27.35,
               vl_frete_real_rateio=27.35, vl_taxa_pedido_rateio=9.55, vl_embalagem_rateio=4.0, vl_imposto_rateio=7.09,
               vl_custo_linha=45.12, vl_margem_contribuicao=84.23)
    sticker = dict(base, fg_brinde=True, vl_desconto_promocional_rateio=9.99, vl_receita_bruta_produto=0.0, vl_receita_liquida_produto=0.0,
                   vl_frete_pago_rateio=0.0, vl_frete_real_rateio=0.0, vl_taxa_pedido_rateio=0.0, vl_embalagem_rateio=0.0,
                   vl_imposto_rateio=0.0, vl_custo_linha=1.18, vl_margem_contribuicao=-1.18)
    return pd.DataFrame([kit, sticker])


def test_cascata_fecha_com_a_margem_do_dbt():
    r = cp.resumo_pedido(_linhas())
    assert r["pago"] == pytest.approx(177.34)
    assert r["entrou"] == pytest.approx(167.79)          # valor pago − taxa
    assert r["custo"] == pytest.approx(46.30)            # inclui o brinde
    assert r["custo_brinde"] == pytest.approx(1.18)
    assert r["margem"] == pytest.approx(83.05)
    assert r["folga"] == pytest.approx(0.0, abs=0.005)
    assert r["desconto"] == pytest.approx(0.0)           # o desconto promocional do brinde não é desconto ao cliente
    assert r["promo"] == pytest.approx(0.0)


PONTE = pd.DataFrame({"cd_gclid": ["g1", "g2", "g3"], "cd_campanha": ["A", "A", "B"], "nm_campanha": ["Camp A", "Camp A", "Camp B"]})
PERF = pd.DataFrame({"cd_campanha": ["A", "B", "A"], "dt_data": pd.to_datetime(["2026-11-01", "2026-11-02", "2026-10-30"]), "vl_custo": [60.0, 40.0, 100.0]})
GOOGLE = pd.DataFrame({"cd_pedido": ["1", "2", "3", "4"], "ds_gclid": ["g1", "g2", "g3", None],
                       "dt_pedido": pd.to_datetime(["2026-11-01", "2026-11-02", "2026-11-02", "2026-11-02"])})


def test_custo_google_da_campanha_do_clique():
    valor, expl = cp.custo_aquisicao("google", "cpc", "g1", DIA, GOOGLE, PONTE, PERF)
    assert valor == pytest.approx(30.0)                  # R$ 60 da campanha A em nov ÷ 2 pedidos dela (o de outubro não entra)
    assert "Camp A" in expl


def test_custo_google_sem_campanha_usa_a_media_por_venda_dos_ultimos_30_dias():
    valor, expl = cp.custo_aquisicao("google", "cpc", None, DIA, GOOGLE, PONTE, PERF)
    assert valor == pytest.approx(200.0 / 4)             # R$ 100 (out) + 60 + 40 ÷ 4 pedidos Google pago da janela
    assert "estimativa" in expl and "sem clique" in expl
    # clique que o Google Ads ainda não ligou à campanha (defasagem de ~1 dia): mesma média, motivo diferente
    valor, expl = cp.custo_aquisicao("google", "cpc", "g9", DIA, GOOGLE, PONTE, PERF)
    assert valor == pytest.approx(50.0) and "ainda não identificada" in expl


def test_media_nao_conta_pedido_depois_do_ultimo_dia_com_custo():
    extra = pd.concat([GOOGLE, pd.DataFrame({"cd_pedido": ["5"], "ds_gclid": [None], "dt_pedido": pd.to_datetime(["2026-11-04"])})])
    valor, _ = cp.custo_aquisicao("google", "cpc", "g9", pd.Timestamp("2026-11-04"), extra, PONTE, PERF)
    assert valor == pytest.approx(50.0)                  # custo vai até 02/11: o pedido de 04/11 não entra no divisor


def test_custo_desconhecido_ou_zero():
    assert cp.custo_aquisicao("google", "cpc", "g1", pd.Timestamp("2026-05-01"), GOOGLE, PONTE, PERF)[0] is None   # antes do histórico
    assert cp.custo_aquisicao("google", "cpc", "g1", pd.Timestamp("2026-12-01"), GOOGLE, PONTE, PERF)[0] == pytest.approx(50.0)   # mês sem custo: média dos últimos 30 dias
    assert cp.custo_aquisicao("instagram", "paid_social", None, DIA, GOOGLE, PONTE, PERF)[0] is None              # sem Meta Ads na base
    assert cp.custo_aquisicao("(sem parametro)", "(sem parametro)", None, DIA, GOOGLE, PONTE, PERF)[0] == 0.0     # sem mídia paga
    assert cp.custo_aquisicao("google", "organico_srsltid", None, DIA, GOOGLE, PONTE, PERF)[0] == 0.0


def _r(**kw):
    base = dict(desconto=0.0, cupom=0.0, pgto=0.0, promo=0.0)
    return {**base, **kw}


def test_descontos_destrinchados_somam_o_desconto_do_pedido():
    r = _r(desconto=26.5, cupom=14.0, pgto=2.0, promo=10.5)
    d = cp.detalhe_descontos(r, "SEGUNDACHANCE3EYP", {"ds_tipo": "percentage", "vl_desconto": 20.0, "vl_pedido_minimo": None}, "pix")
    assert [t for t, _, _ in d] == ["Cupom SEGUNDACHANCE3EYP", "Desconto Pix", "Promoção da loja"]
    assert sum(v for _, v, _ in d) == pytest.approx(r["desconto"])
    assert "20% de desconto" in d[0][2]


def test_cupom_de_valor_fixo_mostra_minimo_e_pedido_sem_desconto_nao_lista_nada():
    d = cp.detalhe_descontos(_r(desconto=5.0, cupom=5.0), "PRIMEIRODATE", {"ds_tipo": "absolute", "vl_desconto": 5.0, "vl_pedido_minimo": 49.99}, "credit_card")
    assert d[0][0] == "Cupom PRIMEIRODATE" and "R$ 5,00 fixo" in d[0][2] and "mín." in d[0][2]
    assert cp.detalhe_descontos(_r(), None, {}, "pix") == []


def test_desconto_que_sobra_vira_ajuste_manual_e_cupom_apagado_nao_quebra():
    d = cp.detalhe_descontos(_r(desconto=12.0, cupom=5.0), "VELHO", {}, "pix")
    assert [t for t, _, _ in d] == ["Cupom VELHO", "Outro desconto"]
    assert d[1][1] == pytest.approx(7.0)
    assert "indisponível" in d[0][2]


def test_telefone_vira_texto_e_link_do_whatsapp():
    assert cp.formatar_telefone("11992417785") == ("(11) 99241-7785", "https://wa.me/5511992417785")
    assert cp.formatar_telefone("5561993791414")[1] == "https://wa.me/5561993791414"
    assert cp.formatar_telefone("123") == ("", "") and cp.formatar_telefone(None) == ("", "")


def test_status_envio_nao_confia_em_situacao_logistica():
    # pedido pago e sem expedição: "aguardando", mesmo que ds_situacao_logistica diga 'entregue'
    assert cp.status_envio({"ds_situacao_logistica": "entregue", "dt_estimada_entrega_max": pd.Timestamp("2026-11-10")}, "EM ABERTO")["expedicao"][0] == "Aguardando postagem"
    r = cp.status_envio({"dt_expedicao": pd.Timestamp("2026-10-21"), "dt_entrega": pd.Timestamp("2026-10-27")}, "ENVIADO")
    assert r["expedicao"][0] == "Expedido em 21/10" and r["entrega"][0] == "Entregue em 27/10"
    assert cp.status_envio({"dt_estimada_entrega_max": pd.Timestamp("2026-11-10")}, "EM ABERTO")["entrega"][0] == "Previsão até 10/11"


def test_status_cashback():
    cupom = {"codigo": "CASHBACKPOSCOMPRAWV26", "valor": 14.99, "expira_em": pd.Timestamp("2026-12-19 16:16", tz="UTC"), "cancelado_em": None}
    w0 = {"tipo_tarefa": "jornada_w0", "dt_contato": pd.Timestamp("2026-11-04 16:38", tz="UTC"), "fg_alcancou": True, "ds_resultado": None}
    texto, det, tipo = cp.status_cashback([w0], cupom, 1)
    assert texto == "Recebeu em 04/11" and tipo == "ok" and "CASHBACKPOSCOMPRAWV26" in det and "vence 19/12" in det
    assert cp.status_cashback([dict(w0, fg_alcancou=False, ds_resultado="WhatsApp inválido")], cupom, 1)[2] == "warn"
    assert cp.status_cashback([], cupom, 1)[0].startswith("Cupom gerado")
    assert cp.status_cashback([], {}, 4)[0] == "Sem cashback (4º pedido)"
    assert cp.status_cashback([], {}, 1)[0] == "Não enviada"
