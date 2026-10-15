"""Mensagens de WhatsApp do SAC — uma por situação, com o link `wa.me` pronto.

Isto é texto de atendimento (apresentação), não regra de negócio: quem decide QUEM entra em cada lista e o
tipo de cada situação é o dbt (tb_logistica_pedido, tb_carrinho_abandonado, tb_pedido_cancelado). Aqui só se
monta a conversa. Os dois textos de carrinho abandonado vieram da antiga rotina de recuperação (desligada em 25/09/2026),
sem o emoji e com o nome completo da loja.

O link só abre o WhatsApp com a mensagem escrita; nada é enviado sem o atendente revisar e apertar enviar.

REGRAS DE TEXTO (decisão do Hugo, 28/09/2026 — valem para toda mensagem nova):
- **Sem emoji.** Não renderiza quando a mensagem é enviada pelo link (chega como caractere quebrado). `link_whatsapp`
  remove qualquer emoji que escape (`_sem_emoji`), mas não escreva emoji nos textos.
- **Nome da loja sempre completo: "Shibari Brasil"** (constante `LOJA`), nunca só "Shibari".

PADRÃO DAS MENSAGENS (decisão do Hugo, 07/10/2026 — vale para TODA mensagem de WhatsApp enviada a cliente, atual ou nova):
- **Rodapé obrigatório** no fim de toda mensagem: "Se preferir não receber mais mensagens, é só responder *SAIR*." (constante
  `RODAPE_SAIR`). Cada `msg_*` termina com `_fecha(...)` e `link_whatsapp` ainda garante o rodapé (idempotente): mensagem nova
  não consegue sair sem ele. Quem responde SAIR vira "Não retomar contato" na Resolução do SAC.
- **Negrito do WhatsApp (`*texto*`, asteriscos colados, sem espaço por dentro; helper `_neg`)** só no que importa na leitura rápida:
  o **cupom/código**, a **vantagem oferecida** (% ou valor de crédito, sticker, Pix), o **prazo/validade**, o **valor mínimo** e a
  **pergunta ou ação principal** pedida ao cliente. Nome do cliente, saudação e o resto do texto ficam sem negrito. Não pôr negrito
  colado em link.
- **Quebra de linha:** a mensagem é escrita em blocos curtos separados por linha em branco (helper `_p`): saudação, contexto,
  pergunta/oferta, link (sempre sozinho na linha, depois de dois-pontos) e, por último, o rodapé. Nada de parágrafo corrido.
"""
import re
from urllib.parse import quote

ATENDENTE = "Robson"
LOJA = "Shibari Brasil"  # nome completo, sempre (regra de texto acima)
RODAPE_SAIR = "Se preferir não receber mais mensagens, é só responder *SAIR*."  # obrigatório em toda mensagem (ver PADRÃO acima)
# emoji e modificadores (variação, ZWJ, tons de pele): não renderizam na mensagem enviada pelo link
_EMOJI = re.compile("[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF\uFE0F\u200D\u2B50\u2B55\u2934\u2935\u3030\u303D\u3297\u3299]")


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


def _sem_emoji(texto: str) -> str:
    """Remove emoji (regra de texto) e arruma os espaços que sobrarem."""
    t = _EMOJI.sub("", texto)
    t = re.sub(r"[ \t]+", " ", t)           # espaços repetidos (sem mexer nas quebras de linha)
    t = re.sub(r" *\n *", "\n", t)          # espaço sobrando em volta da quebra
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def _neg(texto) -> str:
    """Negrito do WhatsApp: *texto* (asteriscos colados, sem espaço por dentro)."""
    return f"*{str(texto).strip()}*"


def _fecha(texto: str) -> str:
    """Põe o rodapé obrigatório (SAIR) no fim da mensagem. Idempotente: não duplica se já estiver lá."""
    texto = texto.rstrip()
    return texto if texto.endswith(RODAPE_SAIR) else f"{texto}\n\n{RODAPE_SAIR}"


def _p(*blocos) -> str:
    """Junta os blocos da mensagem separados por linha em branco (ignora blocos vazios)."""
    return "\n\n".join(b.strip() for b in blocos if b and str(b).strip())


def link_whatsapp(telefone, mensagem: str) -> str | None:
    fone = telefone_whatsapp(telefone)
    if not fone:
        return None
    return f"https://wa.me/{fone}?text={quote(_fecha(_sem_emoji(mensagem)), safe='')}"


def _valor(v) -> str:
    return f"R$ {float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _oi(nome) -> str:
    n = primeiro_nome(nome)
    return f"Oi{' ' + n if n else ''}, tudo bem? Aqui é o {ATENDENTE}, da {LOJA}."


# --- Carrinho abandonado (textos da rotina de recuperação) ------------------------------------------------------

def msg_carrinho(nome, recorrente: bool, url_recuperacao) -> str:
    n = primeiro_nome(nome)
    if recorrente:
        return _fecha(_p(
            f"Oi {n}, tudo bem? Aqui é o {ATENDENTE}, da {LOJA}.",
            f"Você já comprou com a gente e começou um novo pedido que acabou não sendo finalizado. {_neg('Ainda tem interesse?')}",
            f"Se quiser, te ajudo a fechar por aqui:\n{url_recuperacao}"))
    return _fecha(_p(
        f"Oi {n}, aqui é o {ATENDENTE}, da {LOJA}.",
        "Vi que você montou um pedido com a gente e não chegou a finalizar. Ficou alguma dúvida sobre frete ou forma de pagamento?",
        f"{_neg('Está tudo salvo aqui')}, é só finalizar por este link:\n{url_recuperacao}"))


# --- Pedido cancelado (por tipo de cancelamento — ds_motivo_cancelamento da tb_pedido_cancelado) ----------------

_MEIO = {"pix": "Pix", "boleto": "boleto", "credit_card": "cartão"}


def msg_cancelado(nome, numero, valor, motivo, estorno_a_conferir: bool, meio_pagamento=None) -> str:
    pedido = f"pedido #{numero}"
    if estorno_a_conferir:
        return _fecha(_p(
            _oi(nome),
            f"Estou acompanhando o cancelamento do seu {pedido} ({_valor(valor)}) e queria confirmar com você: "
            f"{_neg('o valor já voltou para a sua conta ou cartão?')}",
            "Se ainda não apareceu, me avisa que eu resolvo por aqui."))
    if motivo in ("automatic", "expired"):
        meio = _MEIO.get(meio_pagamento, "pagamento")
        return _fecha(_p(
            _oi(nome),
            f"Vi que o pagamento do seu {pedido} ({_valor(valor)}) não chegou a ser concluído e o sistema cancelou o pedido automaticamente.",
            _neg("Teve alguma dificuldade com o " + meio + "?"),
            "Se ainda tiver interesse, te ajudo a refazer o pedido por aqui."))
    if motivo == "customer":
        return _fecha(_p(
            _oi(nome),
            f"Seu {pedido} foi cancelado e eu queria entender com você: {_neg('aconteceu alguma coisa que a gente possa melhorar?')}",
            "Se quiser rever algum item ou tirar alguma dúvida, é só me chamar por aqui."))
    if motivo == "inventory":
        return _fecha(_p(
            _oi(nome),
            f"Precisamos cancelar o seu {pedido} porque um dos itens ficou sem estoque — desculpa pelo transtorno.",
            _neg("Posso te sugerir uma alternativa parecida ou te avisar assim que o item voltar?")))
    return _fecha(_p(
        _oi(nome),
        f"Estou passando para falar sobre o cancelamento do seu {pedido}.",
        _neg("Ficou alguma pendência ou dúvida que eu possa resolver por aqui?")))


def acao_cancelado(motivo, estorno_a_conferir: bool) -> str:
    """O que o SAC faz com aquele cancelamento (coluna "O que fazer")."""
    if estorno_a_conferir:
        return "Conferir estorno antes de chamar"
    return {
        "automatic": "Recuperar a venda",
        "expired": "Recuperar a venda",
        "customer": "Entender a desistência",
        "inventory": "Oferecer alternativa",
    }.get(motivo, "Entender e resolver pendência")


# --- Entrega com problema (motivo montado em reports/sac.py a partir das flags da tb_logistica_pedido) ----------

def msg_entrega(nome, numero, rastreio, url_rastreio, problema: bool, atrasado: bool, dias_parado) -> str:
    pedido = f"pedido #{numero}"
    acompanhe = ""
    if isinstance(rastreio, str) and rastreio:
        acompanhe = f"Rastreio: {_neg(rastreio)}" + (f"\n{url_rastreio}" if isinstance(url_rastreio, str) and url_rastreio else "")
    if problema:
        return _fecha(_p(
            _oi(nome),
            f"A transportadora registrou um problema na entrega do seu {pedido}.",
            _neg("Pode confirmar se o endereço está certo e se tem alguém para receber?") + " Assim a gente resolve rapidinho.",
            acompanhe))
    if atrasado:
        return _fecha(_p(
            _oi(nome),
            f"Seu {pedido} {_neg('passou do prazo estimado de entrega')} e já estamos acompanhando com a transportadora.",
            f"{_neg('Você chegou a receber?')} Qualquer novidade eu te aviso por aqui.",
            acompanhe))
    parado = _neg("sem atualização há " + str(int(dias_parado)) + " dias")
    return _fecha(_p(
        _oi(nome),
        f"O rastreio do seu {pedido} está {parado} e estamos verificando com a transportadora.",
        f"{_neg('Você chegou a receber?')} Qualquer novidade eu te aviso por aqui.",
        acompanhe))


# --- Recontato com cupom (última tentativa, 7 dias depois do contato do SAC) ---------------------------------------

def msg_recontato_cupom(nome, origem, numero_pedido, codigo, valor_pct, expira_em, url_recuperacao=None) -> str:
    """`origem`: "carrinho" ou "cancelado". `expira_em`: datetime (horário de Brasília) em que o cupom vence — a escassez
    da mensagem (48 horas) só é verdadeira porque o cupom nasce na hora do envio e vence de fato nesse horário."""
    assunto = f"o seu pedido #{numero_pedido}" if origem == "cancelado" else "a sua compra"
    quando = expira_em.strftime("%d/%m às %H:%M")
    vantagem = _neg("cupom de " + str(int(valor_pct)) + "% de desconto")
    prazo = _neg("48 horas, até " + quando)
    checkout = "É só aplicar o código no checkout."
    if origem == "carrinho" and isinstance(url_recuperacao, str) and url_recuperacao:
        checkout += f"\nO carrinho continua salvo aqui:\n{url_recuperacao}"
    return _fecha(_p(
        _oi(nome),
        f"Separei um {vantagem} pra você finalizar {assunto}.",
        f"O código é {_neg(codigo)}, de uso único, e vale só por {prazo}. Depois disso ele expira.",
        checkout,
        "Qualquer dúvida, me chama por aqui."))


# --- Recompra: último contato dos "Perdido" (piloto do Ecossistema de Pós-Venda, 07/10/2026) ---------------------

def msg_recompra_ultimo_contato(nome, link, valor, minimo, expira_em) -> str:
    """Contato humano para quem não compra há mais de 1 ano: pergunta como tem sido a prática, oferece o crédito de retorno pessoal
    (uso único, validade REAL de `expira_em`, valor mínimo `minimo`), lembra o sticker exclusivo e os 3% de desconto no Pix e dá a saída
    ("SAIR"). Sem urgência artificial. Texto aprovado pelo Hugo em 07/10/2026 (com o complemento "curtir nossas novidades" e a frase do
    sticker e do Pix); a forma em blocos (quebras de linha) é só de apresentação. O cupom COMBINA com outros descontos (decisão do Hugo,
    07/10/2026) justamente para o desconto de 3% do Pix valer junto; antes da onda 1, confirmar num pedido de teste (cupom + Pix) que o
    Pix aparece no checkout."""
    ate = expira_em.strftime("%d/%m")
    return _fecha(_p(
        _oi(nome),
        "Faz um tempo desde a sua última compra com a gente e eu queria saber como tem sido a sua prática com o que você levou.",
        f"Se quiser voltar a explorar, separei um {_neg('crédito de ' + _valor(valor))} só para você curtir nossas novidades: "
        f"é de uso único e vale {_neg('até ' + ate)}, em compras a partir de {_neg(_valor(minimo))}.",
        f"É só abrir este link, que o crédito já entra aplicado no carrinho:\n{link}",
        f"Na compra, você ainda leva um {_neg('sticker exclusivo')} e tem {_neg('3% de desconto pagando no Pix')}.",
        "Qualquer dúvida, me chama por aqui."))


def _valor_curto(v) -> str:
    """Valor sem centavos quando é inteiro ("R$ 20", "R$ 120"), como nos textos aprovados da fila do fluxo; com centavos se não for."""
    f = float(v)
    return f"R$ {int(f)}" if f.is_integer() else _valor(f)


# --- Fila do fluxo de pós-venda (B065 etapa 4): jornada W1 e W2 e esgotamento em maturação e dormente ---------------
# Textos W1 e W2 APROVADOS pelo Hugo em 07/10/2026 (planejamento/Textos do Fluxo de Pós-Venda.md); os de esgotamento seguem o texto
# aprovado do piloto dos Perdido e AGUARDAM a aprovação do Hugo antes do 1º envio. Padrão fixo do CLAUDE.md: rodapé SAIR, negrito nas partes
# importantes, blocos separados por linha em branco, link sozinho na linha, sem emoji, "Shibari Brasil".

# Itens citados de forma leve na W1 (Hugo, 13/10/2026): nome CURTO (artigo + substantivo), nunca o nome completo, e SÓ de itens neutros. A marca promete
# envio discreto e a prévia do WhatsApp pode aparecer na tela bloqueada: item fora desta lista NÃO é citado (a mensagem fala "o seu pedido").
# Chave = primeira palavra do nome do produto, em minúsculas.
ITENS_NEUTROS = {
    "kit": "o kit", "corda": "a corda", "tesoura": "a tesoura", "óleo": "o óleo", "oleo": "o óleo", "cera": "a cera", "toalha": "a toalha",
    "tenugui": "o tenugui", "furoshiki": "o furoshiki", "saquinho": "o saquinho", "gancho": "o gancho",
}


def nome_curto_item(nome_produto):
    """"Kit Shibari Iniciante" -> "o kit"; "Corda de Juta Tratada - 6mm X 8m" -> "a corda"; item fora da lista neutra -> None."""
    if not isinstance(nome_produto, str) or not nome_produto.strip():
        return None
    return ITENS_NEUTROS.get(nome_produto.strip().split(" ")[0].lower())


def trecho_itens(itens) -> str:
    """Como a W1 se refere ao que o cliente comprou. `itens` = nomes de produto do pedido, do MAIOR para o menor valor (ou um nome só).
    1 item: "o kit"; 2: "a corda e a tesoura"; 3 ou mais: "a corda e o restante do pedido". Principal fora da lista neutra, sem itens ou sem
    nome: "o seu pedido" (genérico)."""
    if isinstance(itens, str):
        itens = [itens]
    itens = [i for i in (itens or []) if isinstance(i, str) and i.strip()]
    if not itens:
        return "o seu pedido"
    principal = nome_curto_item(itens[0])
    if not principal:
        return "o seu pedido"
    if len(itens) == 1:
        return principal
    segundo = nome_curto_item(itens[1])
    if len(itens) == 2 and segundo:
        return f"{principal} e {segundo}"
    return f"{principal} e o restante do pedido"


def _pergunta_itens(trecho: str) -> str:
    if trecho == "o seu pedido":
        return f"O seu pedido {_neg('chegou certinho e era como você esperava?')}"
    inicial = trecho[0].upper() + trecho[1:]
    verbo = "eram" if " e " in trecho else "era"
    return f"{inicial} {_neg(verbo + ' como você esperava?')}"


def _valor_cashback_txt(v) -> str:
    """Cashback sempre com centavos ("R$ 14,10", "R$ 27,00"): como o valor varia por cliente, "R$ 27" ao lado de "R$ 14,10" ficaria estranho."""
    return _valor(v)


def _ate(expira_em) -> str:
    return expira_em.strftime("%d/%m")


def msg_jornada_w0(nome, nr_pedido, link, valor, minimo, expira_em, substitui_anterior=False, prazo_maior=False, mostrar_pct=True) -> str:
    """W0 — cashback logo depois do pagamento (até 1 dia útil), ANTES de o pedido chegar. Texto aprovado pelo Hugo em 14/10/2026.
    `nr_pedido`: 1 = boas-vindas; 2 e 3 = agradece por voltar. `substitui_anterior`: avisa que o novo cashback substitui um anterior não usado
    (a function o cancela). `prazo_maior` (3º pedido): "Como você já é de casa, deixei um prazo maior". O percentual só aparece aqui, como
    "10% do seu pedido" (`mostrar_pct`: só quando o valor é mesmo 10% do pedido, sem piso nem teto: ver cup.cashback_e_10_por_cento)."""
    pct = " (10% do seu pedido)" if mostrar_pct else ""
    abertura = ("Obrigado pela sua compra! Separei um presente para você: um " if int(nr_pedido) <= 1 else
                "Que bom ver você por aqui de novo, obrigado pela confiança! Separei um novo ")
    oferta = (f"{abertura}{_neg('cashback de ' + _valor_cashback_txt(valor))}{pct} para a sua próxima compra, que fica "
              f"{_neg('descontado automaticamente no carrinho')}. É só abrir este link:\n{link}")
    substitui = "Ele substitui o cashback anterior, que deixou de valer." if substitui_anterior else ""
    condicoes = (f"{'Como você já é de casa, deixei um prazo maior: vale' if prazo_maior else 'Vale'} {_neg('até ' + _ate(expira_em))}, é de uso único "
                 f"e vale em compras a partir de {_neg(_valor_curto(minimo))}. {'Você' if int(nr_pedido) > 1 else 'Na compra, você'} ainda leva um "
                 f"{_neg('sticker exclusivo')} e tem {_neg('3% de desconto pagando no Pix')}.")
    fecho = ("Salve este número para falar com a gente quando quiser. Quando o seu pedido chegar, eu volto aqui para saber se deu tudo certo."
             if int(nr_pedido) <= 1 else "Quando o pedido chegar, passo aqui para saber se deu tudo certo.")
    return _fecha(_p(_oi(nome), oferta, substitui, condicoes, fecho))


def msg_jornada_w1(nome, itens, link, valor, minimo, expira_em, com_lembrete=True) -> str:
    """W1 — check-in depois da entrega confirmada (entrega + 2 dias; janela até entrega + 9), com LEMBRETE do cashback e da validade (o cashback já foi
    enviado na W0). `itens` = nomes dos produtos do pedido do MAIOR para o menor valor (ver `trecho_itens`). `com_lembrete=False` (4º pedido em diante,
    sem cupom): só o check-in. Texto aprovado pelo Hugo em 14/10/2026."""
    lembrete = ""
    if com_lembrete:
        lembrete = (f"E só para lembrar: o seu {_neg('cashback de ' + _valor_cashback_txt(valor))} continua valendo {_neg('até ' + _ate(expira_em))}, "
                    f"{_neg('descontado automaticamente no carrinho')}, em compras a partir de {_neg(_valor_curto(minimo))}. "
                    f"É só abrir o mesmo link:\n{link}")
    return _fecha(_p(
        _oi(nome),
        "Vi que o seu pedido chegou e queria saber se deu tudo certo com a entrega. " + _pergunta_itens(trecho_itens(itens)),
        "Se tiver qualquer dúvida de prática ou de segurança, me chama por aqui. E se puder, a sua avaliação ajuda muito a gente.",
        lembrete))


def msg_jornada_w2_final(nome, link, valor, minimo, expira_em, dias) -> str:
    """Lembrete FINAL (só o 3º pedido), cerca de 7 dias antes de o cashback vencer. Texto aprovado pelo Hugo em 14/10/2026."""
    return _fecha(_p(
        _oi(nome),
        f"O seu {_neg('cashback de ' + _valor_cashback_txt(valor))} vence em {_neg(str(int(dias)) + ' dias')}, no dia {_neg(_ate(expira_em))}. "
        f"Se ainda quiser usar, ele continua {_neg('descontado automaticamente no carrinho')} por este link:\n{link}",
        f"É de uso único e vale em compras a partir de {_neg(_valor_curto(minimo))}. Se quiser ajuda para escolher, é só me chamar."))


def msg_jornada_w0_combinada(nome, itens, link, valor, minimo, expira_em, pct_do_pedido=None) -> str:
    """W0 COMBINADA (só na onda de lançamento, 14/10/2026): o pedido JÁ FOI ENTREGUE quando o cashback sai; uma mensagem só, check-in + cashback (a
    W1 antiga aprovada em 07 e 13/10/2026) e o cliente sai da lista W1. Check-in depois da entrega, com o cashback pessoal (uso único, validade REAL de `expira_em`, mínimo `minimo`).
    `itens` = nomes dos produtos do 1º pedido do MAIOR para o menor valor (ver `trecho_itens`). `pct_do_pedido` (ex.: 10): se informado, a
    mensagem diz que o cashback é esse percentual do último pedido do cliente (use só quando for verdade; ver cup.cashback_e_10_por_cento).
    Texto aprovado pelo Hugo em 07 e 13/10/2026."""
    ate = expira_em.strftime("%d/%m")
    pct = f" ({int(pct_do_pedido)}% do seu último pedido)" if pct_do_pedido else ""
    return _fecha(_p(
        _oi(nome),
        "Vi que o seu pedido chegou e queria saber se deu tudo certo com a entrega. " + _pergunta_itens(trecho_itens(itens)),
        "Se tiver qualquer dúvida de prática ou de segurança, me chama por aqui. E se puder, a sua avaliação ajuda muito a gente.",
        f"Como boas-vindas, separei um {_neg('cashback de ' + _valor_cashback_txt(valor))}{pct} para a sua próxima compra, que fica "
        f"{_neg('descontado automaticamente no carrinho')}. É só abrir este link:\n{link}",
        f"Vale {_neg('até ' + ate)}, é de uso único e vale em compras a partir de {_neg(_valor_curto(minimo))}. Na compra, você ainda leva um "
        f"{_neg('sticker exclusivo')} e tem {_neg('3% de desconto pagando no Pix')}."))


def msg_jornada_w2(nome, link, valor, minimo, expira_em) -> str:
    """W2 — lembrete de uso do MESMO cashback da W0 (W1 + 15 dias, só se ainda não comprou e o cupom vale)."""
    ate = expira_em.strftime("%d/%m")
    return _fecha(_p(
        _oi(nome),
        f"Passando só para lembrar que o seu {_neg('cashback de ' + _valor_cashback_txt(valor))} continua valendo {_neg('até ' + ate)}. Ele entra "
        f"{_neg('descontado automaticamente no carrinho')} por este link:\n{link}",
        f"É de uso único, em compras a partir de {_neg(_valor_curto(minimo))}, e você ainda tem {_neg('3% de desconto pagando no Pix')}. "
        "Se quiser ajuda para escolher o próximo item, é só me chamar."))


def _msg_esgotamento(nome, abertura, link, valor, minimo, expira_em) -> str:
    ate = expira_em.strftime("%d/%m")
    return _fecha(_p(
        _oi(nome),
        abertura,
        f"Para a sua próxima exploração, separei um {_neg('crédito de ' + _valor_curto(valor))} só para você: é de uso único e vale "
        f"{_neg('até ' + ate)}, em compras a partir de {_neg(_valor_curto(minimo))}.",
        f"É só abrir este link, que o crédito já entra aplicado no carrinho:\n{link}",
        f"Na compra, você ainda leva um {_neg('sticker exclusivo')} e tem {_neg('3% de desconto pagando no Pix')}.",
        "Qualquer dúvida, me chama por aqui."))


def msg_esgotamento_maturacao(nome, link, valor, minimo, expira_em) -> str:
    """Esgotamento da base existente, EM MATURAÇÃO (31 a 90 dias desde o último pedido): 1 toque com o crédito de retorno. Texto proposto,
    aguarda aprovação do Hugo."""
    return _msg_esgotamento(nome, "Faz um tempo desde o seu último pedido com a gente e eu queria saber como tem sido a sua prática com o "
                                  "que você levou.", link, valor, minimo, expira_em)


def msg_esgotamento_dormente(nome, link, valor, minimo, expira_em) -> str:
    """Esgotamento da base existente, DORMENTE (91 a 365 dias desde o último pedido): 1 toque com o crédito de retorno. Texto proposto,
    aguarda aprovação do Hugo."""
    return _msg_esgotamento(nome, "Faz alguns meses desde o seu último pedido com a gente e eu queria saber como tem sido a sua prática "
                                  "com o que você levou.", link, valor, minimo, expira_em)


# --- Proximidade: contato amistoso depois da entrega de uma recompra --------------------------------------------

def msg_proximidade(nome, numero_pedido, produto, nr_pedido_cliente, dias_desde_entrega, atrasado: bool) -> str:
    """Relacionamento, não venda: pergunta como foi a experiência (sem cupom, sem oferta). `dias_desde_entrega` 1 = chegou ontem; mais de 3 = registro que ficou aguardando ("chegou faz alguns dias").
    `atrasado`: a entrega passou do prazo — a mensagem reconhece e pede desculpa antes de perguntar."""
    d = int(dias_desde_entrega)
    # até 3 dias: fala o prazo exato; acima disso (o registro ficou aguardando o atendimento) não cravamos o número de dias
    chegou = "chegou ontem" if d <= 1 else (f"chegou há {d} dias" if d <= 3 else "chegou faz alguns dias")
    item = f" ({produto})" if isinstance(produto, str) and produto.strip() else ""
    desculpa = "Sei que a entrega demorou mais do que o combinado e peço desculpa por isso." if atrasado else ""
    volta = "Obrigado por voltar a comprar com a gente." if int(nr_pedido_cliente) == 2 else "Obrigado por continuar comprando com a gente."
    return _fecha(_p(
        _oi(nome),
        f"Vi que o seu pedido #{numero_pedido}{item} {chegou} e queria saber {_neg('como foi a sua experiência')}. {desculpa}",
        "O que você recebeu cumpriu o que esperava? Se tiver qualquer feedback, elogio ou sugestão, eu gosto muito de ouvir.",
        volta))


# --- Resultado do contato (coluna "Resultado" de cada lista; editável, cada troca vai para o histórico) ---------
# "WhatsApp inválido" e "Não retomar contato" (28/09, pedido do Hugo) valem em todas as listas; "Não retomar contato" também tira o cliente do
# recontato com cupom. Mudou uma opção aqui? Os valores antigos já gravados continuam na tabela; se sumirem da lista, a célula
# aparece vazia até o atendente escolher de novo — por isso prefira ACRESCENTAR opção a renomear.

RESULTADOS = {
    "carrinho_abandonado": [
        "Comprou",
        "Vai pensar",
        "Sem interesse — preço/frete",
        "Sem interesse — outro motivo",
        "Sem resposta",
        "Telefone inválido / sem WhatsApp",
        "WhatsApp inválido",
        "Não retomar contato",
    ],
    "entrega_problema": [
        "Cliente já recebeu",
        "Aguardando transportadora",
        "Endereço corrigido / nova tentativa",
        "Reenvio feito",
        "Reembolsado",
        "Reclamação aberta na transportadora",
        "Sem resposta",
        "WhatsApp inválido",
        "Não retomar contato",
    ],
    "pedido_cancelado": [
        "Refez o pedido",
        "Vai pensar",
        "Estorno confirmado",
        "Estorno pendente — resolver",
        "Desistiu — preço/frete",
        "Desistiu — prazo de entrega",
        "Desistiu — outro motivo",
        "Sem resposta",
        "WhatsApp inválido",
        "Não retomar contato",
    ],
    # proximidade: contato amistoso pós-entrega de recompra
    "proximidade_pos_entrega": [
        "Respondeu — satisfeito",
        "Respondeu — com feedback",
        "Reclamação — abrir tratativa",
        "Sem resposta",
        "WhatsApp inválido",
        "Não retomar contato",
    ],
    # recompra: último contato dos "Perdido" (piloto). Quem responder SAIR vira "Não retomar contato".
    # fila do fluxo de pós-venda (B065 etapa 4): tipo_tarefa = campanha da `tb_posvenda_fila`; chave = cd_contato. "Não retomar contato" vale
    # em todas as listas; "Sem resposta" e "Respondeu — quer ver produtos" da W1 liberam o lembrete (W2).
    # jornada por PEDIDO (14/10/2026): chave = "<cd_contato>|<cd_codigo_interno>". W0 = cashback logo após o pagamento; W1 = check-in; W2 = lembrete.
    "jornada_w0": ["Enviado — sem resposta", "Respondeu — agradeceu", "Respondeu — quer ver produtos", "WhatsApp inválido", "Não retomar contato"],
    "jornada_w1": ["Comprou com o cashback", "Respondeu — quer ver produtos", "Respondeu — sem interesse", "Sem resposta",
                   "WhatsApp inválido", "Não retomar contato"],
    "jornada_w2_final": ["Comprou com o cashback", "Respondeu — quer ver produtos", "Respondeu — sem interesse", "Sem resposta",
                         "WhatsApp inválido", "Não retomar contato"],
    "jornada_w2": ["Comprou com o cashback", "Respondeu — quer ver produtos", "Respondeu — sem interesse", "Sem resposta",
                   "WhatsApp inválido", "Não retomar contato"],
    "esgotamento_maturacao": ["Comprou com o crédito", "Respondeu — quer ver produtos", "Respondeu — sem interesse", "Sem resposta",
                              "WhatsApp inválido", "Não retomar contato"],
    "esgotamento_dormente": ["Comprou com o crédito", "Respondeu — quer ver produtos", "Respondeu — sem interesse", "Sem resposta",
                             "WhatsApp inválido", "Não retomar contato"],
    "recompra_piloto": [
        "Comprou com o cupom",
        "Respondeu — quer ver produtos",
        "Respondeu — sem interesse",
        "Sem resposta",
        "WhatsApp inválido",
        "Não retomar contato",
    ],
    # recontato com cupom (última tentativa)
    "recontato_cupom": [
        "Comprou com o cupom",
        "Vai pensar",
        "Sem interesse",
        "Sem resposta",
        "WhatsApp inválido",
        "Não retomar contato",
    ],
}

# Resoluções do 1º contato que IMPEDEM o recontato com cupom (não orientam nova abordagem: já resolvido, contato inviável
# ou pedido do cliente/SAC para não insistir). Resolução vazia NÃO impede.
RESOLUCOES_SEM_RECONTATO = {
    "Não retomar contato", "WhatsApp inválido", "Telefone inválido / sem WhatsApp",
    "Comprou", "Refez o pedido", "Estorno confirmado", "Estorno pendente — resolver",
}

# Resolução da REPESCAGEM (recontato_cupom) que cancela a geração do cupom: o Robson decidiu não gerar. Ao salvar, o cliente sai da lista
# "Gerar cupom" na próxima execução (Hugo, 07/10/2026). "Vai pensar" e "Sem resposta" NÃO cancelam (ainda dá para gerar).
RESOLUCOES_SEM_CUPOM = {"Sem interesse", "WhatsApp inválido", "Não retomar contato"}
