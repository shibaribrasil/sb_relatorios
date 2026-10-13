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


def test_w1_um_produto_cita_o_item_de_forma_leve_e_traz_o_percentual():
    t = m.msg_jornada_w1("hugo", ["Kit Shibari Iniciante"], LINK, 14.10, 120, EXPIRA, pct_do_pedido=10)
    _padrao(t)
    assert t.startswith("Oi Hugo, tudo bem? Aqui é o Robson, da Shibari Brasil.")
    assert "se deu tudo certo com a entrega. O kit *era como você esperava?*" in t
    assert "Kit Shibari Iniciante" not in t                       # nunca o nome completo
    assert "*cashback de R$ 14,10* (10% do seu último pedido) para a sua próxima compra" in t
    assert "*descontado automaticamente no carrinho*" in t
    assert "*até 06/11*" in t and "*R$ 120*" in t
    assert "*sticker exclusivo*" in t and "*3% de desconto pagando no Pix*" in t


def test_w1_varios_produtos_cita_o_principal_e_o_restante():
    itens = ["Corda de Juta Tratada - 6mm X 8m", "Tesoura de Segurança", "Óleo de Massagem e Hidratação de Cordas"]
    t = m.msg_jornada_w1("Ana", itens, LINK, 27, 120, EXPIRA, pct_do_pedido=10)
    _padrao(t)
    assert "A corda e o restante do pedido *eram como você esperava?*" in t
    assert "*cashback de R$ 27,00*" in t                          # sempre com centavos


def test_w1_dois_produtos_cita_os_dois():
    t = m.msg_jornada_w1("Ana", ["Corda de Juta Tratada - 6mm X 8m", "Tesoura de Segurança"], LINK, 20, 120, EXPIRA)
    assert "A corda e a tesoura *eram como você esperava?*" in t
    assert "do seu último pedido" not in t                                  # sem pct_do_pedido, o percentual não aparece


def test_w1_item_fora_da_lista_neutra_fica_generico():
    for itens in (["Plug Anal em Inox"], ["Plug Anal em Inox", "Tesoura de Segurança"], [], None, ["", " "]):
        t = m.msg_jornada_w1("Ana", itens, LINK, 20, 120, EXPIRA)
        assert "O seu pedido *chegou certinho e era como você esperava?*" in t
        assert "Plug" not in t and "plug" not in t
    # principal neutro e segundo fora da lista: não nomeia o segundo
    assert m.trecho_itens(["Corda de Juta", "Plug Anal"]) == "a corda e o restante do pedido"


def test_nome_curto_e_trecho():
    assert m.nome_curto_item("Kit Shibari Iniciante") == "o kit"
    assert m.nome_curto_item("Óleo de Massagem e Hidratação de Cordas") == "o óleo"
    assert m.nome_curto_item("Flogger Isis - 43cm") is None
    assert m.trecho_itens("Tesoura de Segurança") == "a tesoura"
    assert m.trecho_itens(["Kit A", "Tesoura", "Cera"]) == "o kit e o restante do pedido"


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


def test_w1_e_w2_personalizadas_com_valor_variavel():
    t1 = m.msg_jornada_w1("Ana", "corda", LINK, 22.8, 120, EXPIRA)
    t2 = m.msg_jornada_w2("Ana", LINK, 22.8, 120, EXPIRA)
    assert "*cashback de R$ 22,80*" in t1 and "*cashback de R$ 22,80*" in t2
