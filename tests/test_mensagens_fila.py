"""Mensagens da fila do fluxo de pós-venda (B065 etapa 4): padrão fixo do CLAUDE.md (rodapé SAIR, negrito, blocos, link sozinho, sem emoji)."""
import re
from datetime import datetime

from common import mensagens_sac as m

LINK = "https://shibaribrasil.com.br/discount/CASHBACKPOSCOMPRAAB12"
EXPIRA = datetime(2026, 11, 6, 12, 0)


def _padrao(texto):
    assert texto.endswith(m.RODAPE_SAIR)
    assert "\n\n" in texto  # blocos separados por linha em branco
    assert not m._EMOJI.search(texto)
    assert "Shibari Brasil" in texto
    # link sozinho na linha, sem negrito colado
    linhas = texto.split("\n")
    i = next(k for k, l in enumerate(linhas) if l.startswith("https://"))
    assert linhas[i] == LINK and linhas[i - 1].endswith(":")
    assert not re.search(r"\*https?://", texto) and not re.search(r"https?://\S*\*", texto)


def test_w1_segue_o_texto_aprovado_e_o_padrao():
    t = m.msg_jornada_w1("hugo", "kit de corda de juta", LINK, 20, 120, EXPIRA)
    _padrao(t)
    assert t.startswith("Oi Hugo, tudo bem? Aqui é o Robson, da Shibari Brasil.")
    assert "o kit de corda de juta *chegou certinho e era como você esperava?*" in t
    assert "*cashback de R$ 20*" in t
    assert "*descontado automaticamente no carrinho*" in t
    assert "*até 06/11*" in t and "*R$ 120*" in t
    assert "*sticker exclusivo*" in t and "*3% de desconto pagando no Pix*" in t


def test_w1_sem_produto_fala_do_pedido():
    t = m.msg_jornada_w1("Ana", None, LINK, 20, 120, EXPIRA)
    assert "o seu pedido *chegou certinho e era como você esperava?*" in t


def test_w2_lembra_o_mesmo_cashback():
    t = m.msg_jornada_w2("Ana", LINK, 20, 120, EXPIRA)
    _padrao(t)
    assert "continua valendo *até 06/11*" in t
    assert "É de uso único, em compras a partir de *R$ 120*" in t


def test_esgotamento_maturacao_e_dormente():
    for fn, trecho in ((m.msg_esgotamento_maturacao, "Faz um tempo"), (m.msg_esgotamento_dormente, "Faz alguns meses")):
        t = fn("Ana", LINK, 20, 120, EXPIRA)
        _padrao(t)
        assert trecho in t and "*crédito de R$ 20*" in t and "*até 06/11*" in t


def test_resultados_da_fila_incluem_nao_retomar_e_whatsapp_invalido():
    for tipo in ("jornada_w1", "jornada_w2", "esgotamento_maturacao", "esgotamento_dormente"):
        ops = m.RESULTADOS[tipo]
        assert "Não retomar contato" in ops and "WhatsApp inválido" in ops and "Sem resposta" in ops
    assert "Comprou com o cashback" in m.RESULTADOS["jornada_w1"]
