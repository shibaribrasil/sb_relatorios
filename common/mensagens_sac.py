"""Mensagens de WhatsApp do SAC — uma por situação, com o link `wa.me` pronto.

Isto é texto de atendimento (apresentação), não regra de negócio: quem decide QUEM entra em cada lista e o
tipo de cada situação é o dbt (tb_logistica_pedido, tb_carrinho_abandonado, tb_pedido_cancelado). Aqui só se
monta a conversa. Os dois textos de carrinho abandonado são os mesmos da rotina de recuperação
(Base de Conhecimento 10.1.3 / tarefa agendada `recuperacao-carrinho-abandonado`) — mudou lá, mude aqui.

O link só abre o WhatsApp com a mensagem escrita; nada é enviado sem o atendente revisar e apertar enviar.
"""
import re
from urllib.parse import quote

ATENDENTE = "Robson"
LOJA = "Shibari"


def primeiro_nome(nome) -> str:
    if not isinstance(nome, str) or not nome.strip():
        return ""
    return nome.strip().split()[0].capitalize()


def telefone_whatsapp(telefone) -> str | None:
    """Só dígitos, com DDI 55 (a Nuvemshop grava em E.164: +5511999999999). None se não der para ligar."""
    if not isinstance(telefone, str):
        return None
    d = re.sub(r"\D", "", telefone)
    if len(d) in (10, 11):  # DDD + número, sem DDI
        d = "55" + d
    return d if len(d) >= 12 else None


def link_whatsapp(telefone, mensagem: str) -> str | None:
    fone = telefone_whatsapp(telefone)
    if not fone:
        return None
    return f"https://wa.me/{fone}?text={quote(mensagem, safe='')}"


def _valor(v) -> str:
    return f"R$ {float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _oi(nome) -> str:
    n = primeiro_nome(nome)
    return f"Oi{' ' + n if n else ''}, tudo bem? Aqui é o {ATENDENTE}, da {LOJA}."


# --- Carrinho abandonado (textos da rotina de recuperação) ------------------------------------------------------

def msg_carrinho(nome, recorrente: bool, url_recuperacao) -> str:
    n = primeiro_nome(nome)
    if recorrente:
        return (f"Oi {n}, tudo bem? Aqui é o {ATENDENTE}, da {LOJA}. Você já comprou com a gente e começou um novo pedido "
                f"que acabou não sendo finalizado. Ainda tem interesse? Se quiser, te ajudo a fechar por aqui: {url_recuperacao}")
    return (f"Oi {n}, aqui é o {ATENDENTE}, da {LOJA} 🌸 Vi que você montou um pedido com a gente e não chegou a finalizar. "
            f"Ficou alguma dúvida sobre frete ou forma de pagamento? Está tudo salvo aqui, é só finalizar por este link: {url_recuperacao}")


# --- Pedido cancelado (por tipo de cancelamento — ds_motivo_cancelamento da tb_pedido_cancelado) ----------------

_MEIO = {"pix": "Pix", "boleto": "boleto", "credit_card": "cartão"}


def msg_cancelado(nome, numero, valor, motivo, estorno_a_conferir: bool, meio_pagamento=None) -> str:
    pedido = f"pedido #{numero}"
    if estorno_a_conferir:
        return (f"{_oi(nome)} Estou acompanhando o cancelamento do seu {pedido} ({_valor(valor)}) e queria confirmar com você: "
                f"o valor já voltou para a sua conta ou cartão? Se ainda não apareceu, me avisa que eu resolvo por aqui.")
    if motivo in ("automatic", "expired"):
        meio = _MEIO.get(meio_pagamento, "pagamento")
        return (f"{_oi(nome)} Vi que o pagamento do seu {pedido} ({_valor(valor)}) não chegou a ser concluído e o sistema "
                f"cancelou o pedido automaticamente. Teve alguma dificuldade com o {meio}? Se ainda tiver interesse, "
                f"te ajudo a refazer o pedido por aqui.")
    if motivo == "customer":
        return (f"{_oi(nome)} Seu {pedido} foi cancelado e eu queria entender com você: aconteceu alguma coisa que a gente "
                f"possa melhorar? Se quiser rever algum item ou tirar alguma dúvida, é só me chamar por aqui.")
    if motivo == "inventory":
        return (f"{_oi(nome)} Precisamos cancelar o seu {pedido} porque um dos itens ficou sem estoque — desculpa pelo "
                f"transtorno. Posso te sugerir uma alternativa parecida ou te avisar assim que o item voltar?")
    return (f"{_oi(nome)} Estou passando para falar sobre o cancelamento do seu {pedido}. Ficou alguma pendência ou "
            f"dúvida que eu possa resolver por aqui?")


def acao_cancelado(motivo, estorno_a_conferir: bool) -> str:
    """O que o SAC faz com aquele cancelamento (coluna "O que fazer")."""
    if estorno_a_conferir:
        return "Conferir estorno no meio de pagamento antes de chamar"
    return {
        "automatic": "Recuperar a venda (pagamento não concluído)",
        "expired": "Recuperar a venda (pagamento expirado)",
        "customer": "Entender o motivo da desistência",
        "inventory": "Pedir desculpas e oferecer alternativa",
    }.get(motivo, "Entender o motivo e resolver pendências")


# --- Entrega com problema (motivo montado em reports/sac.py a partir das flags da tb_logistica_pedido) ----------

def msg_entrega(nome, numero, rastreio, url_rastreio, problema: bool, atrasado: bool, dias_parado) -> str:
    pedido = f"pedido #{numero}"
    acompanhe = f" Rastreio: {rastreio}" + (f" — {url_rastreio}" if isinstance(url_rastreio, str) and url_rastreio else "") if isinstance(rastreio, str) and rastreio else ""
    if problema:
        return (f"{_oi(nome)} A transportadora registrou um problema na entrega do seu {pedido}. Pode confirmar se o endereço "
                f"está certo e se tem alguém para receber? Assim a gente resolve rapidinho.{acompanhe}")
    if atrasado:
        return (f"{_oi(nome)} Seu {pedido} passou do prazo estimado de entrega e já estamos acompanhando com a transportadora. "
                f"Você chegou a receber? Qualquer novidade eu te aviso por aqui.{acompanhe}")
    return (f"{_oi(nome)} O rastreio do seu {pedido} está sem atualização há {int(dias_parado)} dias e estamos verificando "
            f"com a transportadora. Você chegou a receber? Qualquer novidade eu te aviso por aqui.{acompanhe}")
