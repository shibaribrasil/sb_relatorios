"""Consulta de Pedidos: a cascata fecha com a margem do dbt e o custo de aquisição segue cada regra (specs/consulta-pedido.md)."""
import pandas as pd
import pytest

from reports import consulta_pedido as cp

MES = pd.Timestamp("2026-11-01")


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
PERF = pd.DataFrame({"cd_campanha": ["A", "B", "A"], "dt_data": pd.to_datetime(["2026-11-01", "2026-11-02", "2026-10-30"]), "vl_custo": [60.0, 40.0, 999.0]})
GOOGLE = pd.DataFrame({"cd_pedido": ["1", "2", "3", "4"], "ds_gclid": ["g1", "g2", "g3", None]})


def test_custo_google_da_campanha_do_clique():
    valor, expl = cp.custo_aquisicao("google", "cpc", "g1", MES, GOOGLE, PONTE, PERF)
    assert valor == pytest.approx(30.0)                  # R$ 60 da campanha A em nov ÷ 2 pedidos dela (o de outubro não entra)
    assert "Camp A" in expl


def test_custo_google_sem_campanha_divide_o_ads_todo():
    valor, _ = cp.custo_aquisicao("google", "cpc", None, MES, GOOGLE, PONTE, PERF)
    assert valor == pytest.approx(100.0 / 4)             # R$ 60 + R$ 40 ÷ 4 pedidos Google pago


def test_custo_desconhecido_ou_zero():
    assert cp.custo_aquisicao("google", "cpc", "g1", pd.Timestamp("2026-05-01"), GOOGLE, PONTE, PERF)[0] is None   # antes do histórico
    assert cp.custo_aquisicao("google", "cpc", "g1", pd.Timestamp("2026-12-01"), GOOGLE, PONTE, PERF)[0] is None   # mês sem custo lançado
    assert cp.custo_aquisicao("instagram", "paid_social", None, MES, GOOGLE, PONTE, PERF)[0] is None              # sem Meta Ads na base
    assert cp.custo_aquisicao("(sem parametro)", "(sem parametro)", None, MES, GOOGLE, PONTE, PERF)[0] == 0.0     # sem mídia paga
    assert cp.custo_aquisicao("google", "organico_srsltid", None, MES, GOOGLE, PONTE, PERF)[0] == 0.0


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
