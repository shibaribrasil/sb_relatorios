"""UTM dos links de WhatsApp do SAC (plano de rastreamento de 19/10/2026, frente 3): convenção, idempotência e cada origem da mensagem."""
import re
from datetime import datetime
from urllib.parse import parse_qs, unquote, urlsplit

import pandas as pd
import pytest

from common import cupom as cup
from common import mensagens_sac as msg
from common import utm
from reports import sac

AGORA = datetime(2026, 10, 20, 10, 0)


def _utm(url):
    q = parse_qs(urlsplit(url).query)
    return {k: v[0] for k, v in q.items() if k.startswith("utm_")}


@pytest.mark.parametrize("tipo,campanha", [
    ("jornada_w0", "posvenda_w0"), ("jornada_w1", "posvenda_w1"), ("jornada_w2", "posvenda_w2"), ("jornada_w2_final", "posvenda_w2"),
    ("esgotamento_maturacao", "posvenda_esgotamento"), ("esgotamento_dormente", "posvenda_esgotamento"),
    ("recompra_perdido", "posvenda_perdido"), ("recuperacao", "recuperacao"),
])
def test_link_do_cupom_leva_utm_da_mensagem(tipo, campanha):
    link = cup.link_cupom("CASHBACKPOSCOMPRAAB12", tipo)
    assert link.startswith("https://shibaribrasil.com.br/discount/CASHBACKPOSCOMPRAAB12?")
    assert _utm(link) == {"utm_source": "whatsapp", "utm_medium": "messaging", "utm_campaign": campanha}


def test_toda_campanha_segue_a_convencao_minuscula_sem_acento():
    for tipo in utm.CAMPANHA_UTM:
        valor = utm.normaliza(utm.utm_campaign(tipo))
        assert valor == utm.utm_campaign(tipo) and re.fullmatch(r"[a-z0-9_]+", valor)


def test_normaliza_tira_acento_caixa_e_espaco():
    assert utm.normaliza("Seleção Prazer") == "selecao_prazer"
    assert utm.normaliza(" Pós-Venda W0 ") == "pos_venda_w0"


def test_campanha_desconhecida_quebra_em_vez_de_mandar_link_sem_rastreio():
    with pytest.raises(KeyError):
        cup.link_cupom("X", "campanha_que_nao_existe")


def test_com_utm_mantem_query_existente_e_e_idempotente():
    url = "https://shibaribrasil.com.br/checkout/v3/proxy/1/abc?foo=1&utm_source=outro"
    uma = utm.com_utm(url, "recuperacao")
    assert _utm(uma) == {"utm_source": "whatsapp", "utm_medium": "messaging", "utm_campaign": "recuperacao"}
    assert "foo=1" in uma and "outro" not in uma
    assert utm.com_utm(uma, "recuperacao") == uma


def test_com_utm_nao_mexe_em_url_vazia():
    assert utm.com_utm(None, "recuperacao") is None
    assert utm.com_utm("", "recuperacao") == ""
    assert pd.isna(utm.com_utm(float("nan"), "recuperacao"))


def test_carrinho_leva_utm_de_recuperacao_na_mensagem():
    base = "https://shibaribrasil.com.br/checkout/v3/proxy/2096625617/e7efa67f47352867bd7fa07388691ec9d5795266"
    car = pd.DataFrame([{
        "cd_carrinho": 1, "ts_criacao": pd.Timestamp.now() - pd.Timedelta(hours=3), "vl_total_carrinho": 100.0, "nm_cliente": "Ana",
        "nr_telefone": "+5521999990000", "ds_email_cliente": "a@x.com", "fg_cliente_recorrente": False, "ds_url_recuperacao": base,
    }])
    texto = unquote(sac._lista_carrinhos(car).iloc[0]["whatsapp"]).split("?text=", 1)[1]
    url = next(l for l in texto.split("\n") if l.startswith("https://"))
    assert url.startswith(base + "?") and _utm(url)["utm_campaign"] == "recuperacao"


def test_recontato_de_carrinho_leva_utm_e_o_cancelado_nao_tem_link():
    base = "https://shibaribrasil.com.br/checkout/v3/proxy/1/abc"
    t = msg.msg_recontato_cupom("Ana", "carrinho", None, "SEGUNDACHANCE", 20, AGORA, utm.com_utm(base, "recuperacao"))
    assert "utm_campaign=recuperacao" in t
    assert "https://" not in msg.msg_recontato_cupom("Ana", "cancelado", 123, "SEGUNDACHANCE", 20, AGORA)
