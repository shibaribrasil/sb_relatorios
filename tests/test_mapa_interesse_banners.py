"""Lógica dos banners do Mapa de Interesse: layout atual, janela por arte, CTR, funil, score e quadrante."""
import datetime as dt

import pandas as pd

from reports import mapa_interesse_banners as mb

D = dt.date


def _blocos():
    linhas = []
    for dev, base in (("desktop", "d"), ("mobile", "m")):
        for pos in (1, 2):
            linhas.append(dict(nr_ordem_bloco=1, ds_bloco="slider", nm_bloco="slider", ds_dispositivo=dev, nr_posicao=pos,
                               ds_destino=f"https://loja.com/destino{pos}", cd_arte=f"{base}{pos}", lk_arte=f"https://cdn/{base}{pos}.webp", nm_rotulo=f"Carrossel {pos}"))
    for pos in (1, 2):
        linhas.append(dict(nr_ordem_bloco=2, ds_bloco="categories", nm_bloco="categories", ds_dispositivo="todos", nr_posicao=pos,
                           ds_destino=f"https://loja.com/cat{pos}/", cd_arte=f"c{pos}", lk_arte=f"https://cdn/c{pos}.webp", nm_rotulo=f"Cat {pos}"))
    linhas.append(dict(nr_ordem_bloco=3, ds_bloco="new", nm_bloco="Novidades", ds_dispositivo="todos", nr_posicao=None,
                       ds_destino=None, cd_arte=None, lk_arte=None, nm_rotulo=None))
    return pd.DataFrame(linhas)


def _promo():
    def l(dia, disp, local, slot, arte, cliques, cart=0, ped=0, trans=None):
        return dict(dt_data=dia, ds_dispositivo=disp, cd_local=local, cd_slot=slot, cd_arte=arte, ds_destino="/x", qt_cliques=cliques,
                    qt_sessoes_clique=cliques, qt_sessoes_carrinho_pos=cart, qt_sessoes_checkout_pos=cart, qt_pedidos_pos=ped,
                    vl_receita_ga4_pos=100.0 * ped, ar_transacoes=trans or [])
    return pd.DataFrame([
        l(D(2026, 10, 10), "desktop", "home_hero", "hero_1", "d1", 12, cart=4, ped=1, trans=["2530"]),
        l(D(2026, 10, 10), "mobile", "home_hero", "hero_1", "m1", 8, cart=1),
        l(D(2026, 10, 10), "desktop", "home_hero", "hero_2", "d2", 2),
        l(D(2026, 10, 10), "desktop", "home_hero", "hero_2", "velha", 5),          # arte antiga no mesmo slot
        l(D(2026, 10, 10), "mobile", "home_categorias", "cat_1", "c1", 3, cart=1),
    ])


def _sessoes():
    return pd.DataFrame({"dt_data": [D(2026, 10, 10), D(2026, 10, 11)], "ds_dispositivo": ["desktop", "desktop"], "qt_sessoes_home": [40, 60]})


def _calc(entrada=None):
    lay = mb.layout_banners(_blocos())
    margens = pd.DataFrame({"cd_pedido_loja": ["2530"], "vl_margem": [70.0], "vl_receita": [100.0]})
    ent = entrada if entrada is not None else pd.DataFrame({"cd_arte": [], "dt_entrada": []})
    return mb.calcular_banners(lay, _promo(), _sessoes(), ent, margens, D(2026, 10, 10), D(2026, 10, 11))


def test_layout_junta_desktop_e_mobile_e_respeita_a_ordem_do_site():
    lay = mb.layout_banners(_blocos())
    assert list(lay["cd_slot"]) == ["hero_1", "hero_2", "cat_1", "cat_2"]
    assert list(lay["nr_ordem_bloco"]) == [1, 1, 2, 2]
    h1 = lay.iloc[0]
    assert (h1["cd_arte_desktop"], h1["cd_arte_mobile"], h1["lk_arte"]) == ("d1", "m1", "https://cdn/m1.webp")   # cartão usa a arte mobile
    assert lay.iloc[2]["cd_arte_desktop"] == lay.iloc[2]["cd_arte_mobile"] == "c1"


def test_metricas_somam_desktop_e_mobile_da_mesma_arte_e_separam_arte_antiga():
    d, p0 = _calc()
    h1 = d[d["cd_slot"] == "hero_1"].iloc[0]
    assert (h1["qt_sessoes_clique"], h1["qt_sessoes_carrinho_pos"], h1["qt_pedidos_pos"]) == (20, 5, 1)
    assert h1["ctr"] == 20 / 100                      # 100 sessões de home de 10 a 11/10
    assert h1["vl_margem"] == 70.0                    # margem real do pedido 2530 na tb_pedido
    h2 = d[d["cd_slot"] == "hero_2"].iloc[0]
    assert h2["qt_sessoes_clique"] == 2 and h2["qt_cliques_outras_artes"] == 5
    assert round(p0, 4) == round((5 + 1) / (20 + 2 + 3), 4)       # taxa de carrinho de todos os banners


def test_janela_comeca_na_entrada_da_arte_no_numerador_e_no_denominador():
    ent = pd.DataFrame({"cd_arte": ["d1", "m1"], "dt_entrada": [D(2026, 10, 11), D(2026, 10, 11)]})
    d, _ = _calc(ent)
    h1 = d[d["cd_slot"] == "hero_1"].iloc[0]
    assert h1["dt_inicio"] == D(2026, 10, 11) and h1["sessoes_home"] == 60 and h1["qt_sessoes_clique"] == 0   # cliques de 10/10 ficam de fora


def test_score_faixa_e_quadrante_com_amostra_pequena():
    d, p0 = _calc()
    assert d.loc[d["cd_slot"] == "hero_1", "score"].iloc[0] > d.loc[d["cd_slot"] == "cat_1", "score"].iloc[0] > 0
    assert d.loc[d["cd_slot"] == "cat_2", "faixa"].iloc[0] == "Sem sinal"
    assert d.loc[d["cd_slot"] == "hero_1", "quadrante"].iloc[0] == "Estrela"      # 20 cliques (≥10), score alto e taxa acima da média
    assert d.loc[d["cd_slot"] == "cat_1", "quadrante"].iloc[0] == "Poucos dados"  # 3 cliques (<10)


def test_cartao_e_linha_html():
    d, _ = _calc()
    html = mb.linha_de_banners(d[d["ds_bloco"] == "slider"])
    assert "176px" in html and "1º" in html and "CTR 20,0%" in html and "outras artes" in html


def test_nome_legivel_troca_rotulo_generico_do_tema():
    assert mb.nome_banner("Carrossel 1", "https://loja.com/produtos/kit/") == "produtos/kit"
    assert mb.nome_banner("Cordas", "https://loja.com/shibari/cordas/") == "Cordas"
    assert mb.nome_banner(None, "https://loja.com/liquidacao") == "liquidacao"
