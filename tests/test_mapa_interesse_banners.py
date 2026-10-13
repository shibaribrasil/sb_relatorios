"""Lógica dos banners do Mapa de Interesse: layout atual, identidade por local + destino, janela, CTR, funil, score e quadrante."""
import datetime as dt

import pandas as pd

from reports import mapa_interesse_banners as mb

D = dt.date


def _blocos(arte_hero1_desktop="d1", arte_hero1_mobile="m1", ordem_hero=(1, 2)):
    """Carrossel com 2 slides (desktop e mobile), 2 quadrados e 1 prateleira. `ordem_hero` troca quem está em cada posição."""
    destinos = {1: "https://loja.com/produtos/kit/", 2: "https://loja.com/liquidacao"}
    linhas = []
    for dev, base in (("desktop", "d"), ("mobile", "m")):
        for pos, quem in zip((1, 2), ordem_hero):
            arte = (arte_hero1_desktop if dev == "desktop" else arte_hero1_mobile) if quem == 1 else f"{base}{quem}"
            linhas.append(dict(nr_ordem_bloco=1, ds_bloco="slider", nm_bloco="slider", ds_dispositivo=dev, nr_posicao=pos,
                               ds_destino=destinos[quem], cd_arte=arte, lk_arte=f"https://cdn/{arte}.webp", nm_rotulo=f"Carrossel {pos}"))
    for pos in (1, 2):
        linhas.append(dict(nr_ordem_bloco=2, ds_bloco="categories", nm_bloco="categories", ds_dispositivo="todos", nr_posicao=pos,
                           ds_destino=f"https://loja.com/cat{pos}/", cd_arte=f"c{pos}", lk_arte=f"https://cdn/c{pos}.webp", nm_rotulo=f"Cat {pos}"))
    linhas.append(dict(nr_ordem_bloco=3, ds_bloco="new", nm_bloco="Novidades", ds_dispositivo="todos", nr_posicao=None,
                       ds_destino=None, cd_arte=None, lk_arte=None, nm_rotulo=None))
    return pd.DataFrame(linhas)


def _linha(dia, disp, local, slot, arte, destino, cliques, cart=0, ped=0, trans=None):
    return dict(dt_data=dia, ds_dispositivo=disp, cd_local=local, cd_slot=slot, cd_arte=arte, ds_destino=destino, qt_cliques=cliques,
                qt_sessoes_clique=cliques, qt_sessoes_carrinho_pos=cart, qt_sessoes_checkout_pos=cart, qt_pedidos_pos=ped,
                vl_receita_ga4_pos=100.0 * ped, ar_transacoes=trans or [])


def _promo():
    k, l = "/produtos/kit/", "/liquidacao"
    return pd.DataFrame([
        _linha(D(2026, 10, 10), "desktop", "home_hero", "hero_1", "d1", k, 12, cart=4, ped=1, trans=["2530"]),
        _linha(D(2026, 10, 10), "mobile", "home_hero", "hero_1", "m1", k, 8, cart=1),
        _linha(D(2026, 10, 10), "desktop", "home_hero", "hero_2", "d2", l, 2),
        _linha(D(2026, 10, 10), "mobile", "home_categorias", "cat_1", "c1", "/cat1/", 3, cart=1),
    ])


def _sessoes():
    return pd.DataFrame({"dt_data": [D(2026, 10, 10), D(2026, 10, 11)], "ds_dispositivo": ["desktop", "desktop"], "qt_sessoes_home": [40, 60]})


def _entrada(data=D(2026, 10, 9)):
    return pd.DataFrame({"ds_bloco": ["slider", "slider", "categories", "categories"],
                         "ds_destino": ["https://loja.com/produtos/kit/", "https://loja.com/liquidacao", "https://loja.com/cat1/", "https://loja.com/cat2/"],
                         "dt_entrada": [data] * 4})


def _calc(blocos=None, promo=None, entrada=None):
    lay = mb.layout_banners(blocos if blocos is not None else _blocos())
    margens = pd.DataFrame({"cd_pedido_loja": ["2530"], "vl_margem": [70.0], "vl_receita": [100.0]})
    return mb.calcular_banners(lay, promo if promo is not None else _promo(), _sessoes(), entrada if entrada is not None else _entrada(),
                               margens, D(2026, 10, 10), D(2026, 10, 11))


def test_layout_junta_desktop_e_mobile_e_respeita_a_ordem_do_site():
    lay = mb.layout_banners(_blocos())
    assert list(lay["cd_slot"]) == ["hero_1", "hero_2", "cat_1", "cat_2"]
    assert list(lay["nr_ordem_bloco"]) == [1, 1, 2, 2]
    h1 = lay.iloc[0]
    assert (h1["cd_arte_desktop"], h1["cd_arte_mobile"], h1["lk_arte"]) == ("d1", "m1", "https://cdn/m1.webp")   # cartão usa a arte mobile
    assert lay.iloc[2]["cd_arte_desktop"] == lay.iloc[2]["cd_arte_mobile"] == "c1"


def test_destino_norm_iguala_url_completa_e_caminho_do_ga4():
    assert mb.destino_norm("https://www.loja.com/Produtos/Kit/?utm=1#x") == mb.destino_norm("/produtos/kit") == "/produtos/kit"
    assert mb.destino_norm("https://loja.com/") == mb.destino_norm("") or mb.destino_norm("https://loja.com/") == "/"


def test_metricas_somam_desktop_e_mobile_do_mesmo_banner():
    d, p0 = _calc()
    h1 = d[d["cd_slot"] == "hero_1"].iloc[0]
    assert (h1["qt_sessoes_clique"], h1["qt_sessoes_carrinho_pos"], h1["qt_pedidos_pos"]) == (20, 5, 1)
    assert h1["ctr"] == 20 / 100                      # 100 sessões de home de 10 a 11/10
    assert h1["vl_margem"] == 70.0                    # margem real do pedido 2530 na tb_pedido
    assert round(p0, 4) == round((5 + 1) / (20 + 2 + 3), 4)       # taxa de carrinho de todos os banners


def test_trocar_o_arquivo_da_arte_nao_zera_a_contagem():
    """Caso real (12/10/2026): arte refeita em resolução melhor = arquivo novo, mesmo link e mesmo slot."""
    k = "/produtos/kit/"
    promo = pd.concat([_promo(), pd.DataFrame([
        _linha(D(2026, 10, 11), "desktop", "home_hero", "hero_1", "d1_v2", k, 5, cart=2),
        _linha(D(2026, 10, 11), "mobile", "home_hero", "hero_1", "m1_v2", k, 3)])], ignore_index=True)
    d, _ = _calc(blocos=_blocos(arte_hero1_desktop="d1_v2", arte_hero1_mobile="m1_v2"), promo=promo)
    h1 = d[d["cd_slot"] == "hero_1"].iloc[0]
    assert h1["qt_sessoes_clique"] == 28                       # 20 da arte antiga + 8 da nova
    assert h1["n_artes"] == 4 and h1["dt_inicio"] == D(2026, 10, 10)
    assert h1["sessoes_home"] == 100                           # a janela não encolheu para o dia da troca


def test_girar_a_ordem_do_carrossel_leva_o_historico_junto():
    k, l = "/produtos/kit/", "/liquidacao"
    promo = pd.concat([_promo(), pd.DataFrame([
        _linha(D(2026, 10, 11), "desktop", "home_hero", "hero_2", "d1", k, 4),    # o kit passou para a 2ª posição
        _linha(D(2026, 10, 11), "desktop", "home_hero", "hero_1", "d2", l, 6)])], ignore_index=True)
    d, _ = _calc(blocos=_blocos(ordem_hero=(2, 1)), promo=promo)
    kit = d[d["ds_destino"].str.contains("produtos/kit")].iloc[0]
    assert kit["cd_slot"] == "hero_2" and kit["qt_sessoes_clique"] == 24 and kit["slots_vistos"] == "hero_1, hero_2"
    liq = d[d["ds_destino"].str.contains("liquidacao")].iloc[0]
    assert liq["cd_slot"] == "hero_1" and liq["qt_sessoes_clique"] == 8


def test_janela_comeca_na_primeira_captura_do_banner_nao_do_arquivo():
    d, _ = _calc(entrada=_entrada(D(2026, 10, 11)))
    h1 = d[d["cd_slot"] == "hero_1"].iloc[0]
    assert h1["dt_inicio"] == D(2026, 10, 11) and h1["sessoes_home"] == 60 and h1["qt_sessoes_clique"] == 0   # cliques de 10/10 ficam de fora


def test_score_faixa_e_quadrante_com_amostra_pequena():
    d, p0 = _calc()
    assert d.loc[d["cd_slot"] == "hero_1", "score"].iloc[0] > d.loc[d["cd_slot"] == "cat_1", "score"].iloc[0] > 0
    assert d.loc[d["cd_slot"] == "cat_2", "faixa"].iloc[0] == "Sem sinal"
    assert d.loc[d["cd_slot"] == "hero_1", "quadrante"].iloc[0] == "Estrela"      # 20 cliques (≥10), score alto e taxa acima da média
    assert d.loc[d["cd_slot"] == "cat_1", "quadrante"].iloc[0] == "Poucos dados"  # 3 cliques (<10)


def test_cartao_e_linha_html():
    k = "/produtos/kit/"
    promo = pd.concat([_promo(), pd.DataFrame([_linha(D(2026, 10, 11), "desktop", "home_hero", "hero_1", "d1_v2", k, 1)])], ignore_index=True)
    d, _ = _calc(promo=promo)
    html = mb.linha_de_banners(d[d["ds_bloco"] == "slider"])
    assert "176px" in html and "1º" in html and "CTR 21,0%" in html and "versões de arte" in html


def test_nome_legivel_troca_rotulo_generico_do_tema():
    assert mb.nome_banner("Carrossel 1", "https://loja.com/produtos/kit/") == "produtos/kit"
    assert mb.nome_banner("Cordas", "https://loja.com/shibari/cordas/") == "Cordas"
    assert mb.nome_banner(None, "https://loja.com/liquidacao") == "liquidacao"
