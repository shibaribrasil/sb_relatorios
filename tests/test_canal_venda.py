"""Canal de venda do Pulso do Dia: pedido do pós-venda vira "WhatsApp" e sai do total da Nuvemshop (specs/pulso-dia.md, v8)."""
import pandas as pd
import pytest

from reports import vendas_margem as vm


def _canal(lojas, posvenda):
    return vm.canal_venda(pd.Series(lojas), pd.Series(posvenda)).tolist()


def test_posvenda_da_nuvemshop_vira_whatsapp():
    assert _canal(["Nuvemshop", "Nuvemshop"], [True, False]) == ["WhatsApp", "Nuvemshop"]


def test_outro_canal_nunca_e_reclassificado():
    assert _canal(["MercadoLivre", "(sem canal)"], [True, True]) == ["MercadoLivre", "(sem canal)"]


def test_o_pedido_sai_da_nuvemshop_e_a_soma_fecha():
    df = pd.DataFrame({"ds_tipo_loja": ["Nuvemshop", "Nuvemshop", "Nuvemshop", "MercadoLivre"],
                       "fg_posvenda": [False, True, False, False], "fat": [100.0, 115.55, 50.0, 80.0]})
    df["ds_canal"] = vm.canal_venda(df["ds_tipo_loja"], df["fg_posvenda"])
    por_canal = df.groupby("ds_canal")["fat"].sum()
    assert por_canal["WhatsApp"] == 115.55
    assert por_canal["Nuvemshop"] == 150.0                 # o total da Nuvemshop foi debitado
    assert por_canal.sum() == df["fat"].sum()              # nada some nem duplica
    assert df["ds_tipo_loja"].tolist() == ["Nuvemshop", "Nuvemshop", "Nuvemshop", "MercadoLivre"]  # a loja real não muda


def _linhas(canais, fats, margs, pedidos=None):
    n = len(canais)
    return pd.DataFrame({"ds_canal": canais, "cd_codigo_interno": pedidos or list(range(n)), "vl_liquido_item": fats,
                         "vl_receita_liquida_produto": fats, "vl_margem_contribuicao": margs})


def test_tabela_canal_participacao_na_margem_soma_100():
    from reports import pulso_dia as pd_
    sel = _linhas(["Nuvemshop", "Nuvemshop", "WhatsApp", "MercadoLivre"], [100.0, 100.0, 50.0, 50.0], [60.0, 40.0, 30.0, 20.0])
    g = pd_._tabela_canal(sel).set_index("ds_canal")
    assert g.loc["Nuvemshop", "part"] == 100 / 150
    assert g["part"].sum() == 1.0
    assert "cresc" not in g.columns                      # sem período anterior não há coluna de crescimento


def test_tabela_canal_crescimento_contra_o_mes_anterior():
    from reports import pulso_dia as pd_
    sel = _linhas(["Nuvemshop", "WhatsApp", "MercadoLivre"], [120.0, 40.0, 80.0], [60.0, 20.0, 30.0])
    ant = _linhas(["Nuvemshop", "WhatsApp"], [100.0, 80.0], [50.0, 40.0])
    g = pd_._tabela_canal(sel, ant).set_index("ds_canal")
    assert g.loc["Nuvemshop", "cresc"] == pytest.approx(0.20)
    assert g.loc["WhatsApp", "cresc"] == pytest.approx(-0.50)
    assert pd.isna(g.loc["MercadoLivre", "cresc"])        # não vendeu no período anterior: sem base, vira "—"
    assert pd_._fmt_cresc(0.2) == "+20,0%" and pd_._fmt_cresc(-0.5) == "−50,0%" and pd_._fmt_cresc(float("nan")) == "—"


def test_cor_do_crescimento_so_com_cores_do_tema():
    from reports import pulso_dia as pd_
    from common.design import COLORS
    assert COLORS["success"] in pd_._cor_cresc(0.1) and COLORS["danger"] in pd_._cor_cresc(-0.1)
    assert pd_._cor_cresc(0.0) == "" and pd_._cor_cresc(float("nan")) == ""
