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
    return re.sub(r"\s{2,}", " ", _EMOJI.sub("", texto)).strip()


def _neg(texto) -> str:
    """Negrito do WhatsApp: *texto* (asteriscos colados, sem espaço por dentro)."""
    return f"*{str(texto).strip()}*"


def _fecha(texto: str) -> str:
    """Põe o rodapé obrigatório (SAIR) no fim da mensagem. Idempotente: não duplica se já estiver lá."""
    texto = texto.rstrip()
    return texto if texto.endswith(RODAPE_SAIR) else f"{texto} {RODAPE_SAIR}"


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
        return _fecha(f"Oi {n}, tudo bem? Aqui é o {ATENDENTE}, da {LOJA}. Você já comprou com a gente e começou um novo pedido "
                      f"que acabou não sendo finalizado. {_neg('Ainda tem interesse?')} Se quiser, te ajudo a fechar por aqui: {url_recuperacao}")
    return _fecha(f"Oi {n}, aqui é o {ATENDENTE}, da {LOJA}. Vi que você montou um pedido com a gente e não chegou a finalizar. "
                  f"Ficou alguma dúvida sobre frete ou forma de pagamento? {_neg('Está tudo salvo aqui')}, é só finalizar por este link: {url_recuperacao}")


# --- Pedido cancelado (por tipo de cancelamento — ds_motivo_cancelamento da tb_pedido_cancelado) ----------------

_MEIO = {"pix": "Pix", "boleto": "boleto", "credit_card": "cartão"}


def msg_cancelado(nome, numero, valor, motivo, estorno_a_conferir: bool, meio_pagamento=None) -> str:
    pedido = f"pedido #{numero}"
    if estorno_a_conferir:
        return _fecha(f"{_oi(nome)} Estou acompanhando o cancelamento do seu {pedido} ({_valor(valor)}) e queria confirmar com você: "
                      f"{_neg('o valor já voltou para a sua conta ou cartão?')} Se ainda não apareceu, me avisa que eu resolvo por aqui.")
    if motivo in ("automatic", "expired"):
        meio = _MEIO.get(meio_pagamento, "pagamento")
        dificuldade = _neg("Teve alguma dificuldade com o " + meio + "?")
        return _fecha(f"{_oi(nome)} Vi que o pagamento do seu {pedido} ({_valor(valor)}) não chegou a ser concluído e o sistema "
                      f"cancelou o pedido automaticamente. {dificuldade} Se ainda tiver interesse, "
                      f"te ajudo a refazer o pedido por aqui.")
    if motivo == "customer":
        return _fecha(f"{_oi(nome)} Seu {pedido} foi cancelado e eu queria entender com você: "
                      f"{_neg('aconteceu alguma coisa que a gente possa melhorar?')} Se quiser rever algum item ou tirar alguma dúvida, "
                      f"é só me chamar por aqui.")
    if motivo == "inventory":
        return _fecha(f"{_oi(nome)} Precisamos cancelar o seu {pedido} porque um dos itens ficou sem estoque — desculpa pelo "
                      f"transtorno. {_neg('Posso te sugerir uma alternativa parecida ou te avisar assim que o item voltar?')}")
    return _fecha(f"{_oi(nome)} Estou passando para falar sobre o cancelamento do seu {pedido}. "
                  f"{_neg('Ficou alguma pendência ou dúvida que eu possa resolver por aqui?')}")


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
    acompanhe = f" Rastreio: {_neg(rastreio) if isinstance(rastreio, str) else rastreio}" + (f" — {url_rastreio}" if isinstance(url_rastreio, str) and url_rastreio else "") if isinstance(rastreio, str) and rastreio else ""
    if problema:
        return _fecha(f"{_oi(nome)} A transportadora registrou um problema na entrega do seu {pedido}. "
                      f"{_neg('Pode confirmar se o endereço está certo e se tem alguém para receber?')} Assim a gente resolve rapidinho.{acompanhe}")
    if atrasado:
        return _fecha(f"{_oi(nome)} Seu {pedido} {_neg('passou do prazo estimado de entrega')} e já estamos acompanhando com a transportadora. "
                      f"{_neg('Você chegou a receber?')} Qualquer novidade eu te aviso por aqui.{acompanhe}")
    parado = _neg("sem atualização há " + str(int(dias_parado)) + " dias")
    return _fecha(f"{_oi(nome)} O rastreio do seu {pedido} está {parado} e estamos verificando "
                  f"com a transportadora. {_neg('Você chegou a receber?')} Qualquer novidade eu te aviso por aqui.{acompanhe}")


# --- Recontato com cupom (última tentativa, 7 dias depois do contato do SAC) ---------------------------------------

def msg_recontato_cupom(nome, origem, numero_pedido, codigo, valor_pct, expira_em, url_recuperacao=None) -> str:
    """`origem`: "carrinho" ou "cancelado". `expira_em`: datetime (horário de Brasília) em que o cupom vence — a escassez
    da mensagem (48 horas) só é verdadeira porque o cupom nasce na hora do envio e vence de fato nesse horário."""
    assunto = f"o seu pedido #{numero_pedido}" if origem == "cancelado" else "a sua compra"
    quando = expira_em.strftime("%d/%m às %H:%M")
    link = f" O carrinho continua salvo aqui: {url_recuperacao}" if origem == "carrinho" and isinstance(url_recuperacao, str) and url_recuperacao else ""
    vantagem = _neg("cupom de " + str(int(valor_pct)) + "% de desconto")
    prazo = _neg("48 horas, até " + quando)
    return _fecha(f"{_oi(nome)} Passei para te avisar de uma última cortesia: separei um {vantagem} para você "
                  f"concluir {assunto}. O código é {_neg(codigo)}, de uso único, e vale só por {prazo}. Depois disso ele expira. "
                  f"É só aplicar o código no checkout.{link} Qualquer dúvida, me chama por aqui.")


# --- Recompra: último contato dos "Perdido" (piloto do Ecossistema de Pós-Venda, 07/10/2026) ---------------------

def msg_recompra_ultimo_contato(nome, link, valor, minimo, expira_em) -> str:
    """Contato humano para quem não compra há mais de 1 ano: pergunta como tem sido a prática, oferece o crédito de retorno pessoal
    (uso único, validade REAL de `expira_em`, valor mínimo `minimo`), lembra o sticker exclusivo e os 3% de desconto no Pix e dá a saída
    ("SAIR"). Sem urgência artificial. Texto aprovado pelo Hugo em 07/10/2026 (com o complemento "curtir nossas novidades" e a frase do
    sticker e do Pix). O cupom COMBINA com outros descontos (decisão do Hugo, 07/10/2026) justamente para o desconto de 3% do Pix valer junto;
    antes da onda 1, confirmar num pedido de teste (cupom + Pix) que o Pix aparece no checkout."""
    ate = expira_em.strftime("%d/%m")
    # *negrito* é a marcação do WhatsApp (asteriscos colados no texto, sem espaço por dentro): destaca só o que importa na leitura rápida.
    return _fecha(f"{_oi(nome)} Faz um tempo desde a sua última compra com a gente e eu queria saber como tem sido a sua prática com o "
            f"que você levou. Se quiser voltar a explorar, separei um *crédito de {_valor(valor)}* só para você curtir nossas novidades: "
            f"é de uso único e vale *até {ate}*, em compras a partir de *{_valor(minimo)}*. É só abrir este link, que o crédito já entra "
            f"aplicado no carrinho: {link} Na compra, você ainda leva um *sticker exclusivo* e tem *3% de desconto pagando no Pix*. "
            f"Qualquer dúvida, me chama por aqui.")


# --- Proximidade: contato amistoso depois da entrega de uma recompra --------------------------------------------

def msg_proximidade(nome, numero_pedido, produto, nr_pedido_cliente, dias_desde_entrega, atrasado: bool) -> str:
    """Relacionamento, não venda: pergunta como foi a experiência (sem cupom, sem oferta). `dias_desde_entrega` 1 = chegou ontem; mais de 3 = registro que ficou aguardando ("chegou faz alguns dias").
    `atrasado`: a entrega passou do prazo — a mensagem reconhece e pede desculpa antes de perguntar."""
    d = int(dias_desde_entrega)
    # até 3 dias: fala o prazo exato; acima disso (o registro ficou aguardando o atendimento) não cravamos o número de dias
    chegou = "chegou ontem" if d <= 1 else (f"chegou há {d} dias" if d <= 3 else "chegou faz alguns dias")
    item = f" ({produto})" if isinstance(produto, str) and produto.strip() else ""
    desculpa = "Sei que a entrega demorou mais do que o combinado e peço desculpa por isso. " if atrasado else ""
    volta = "Obrigado por voltar a comprar com a gente." if int(nr_pedido_cliente) == 2 else "Obrigado por continuar comprando com a gente."
    return _fecha(f"{_oi(nome)} Vi que o seu pedido #{numero_pedido}{item} {chegou} e queria saber {_neg('como foi a sua experiência')}. {desculpa}"
                  f"O que você recebeu cumpriu o que esperava? Se tiver qualquer feedback, elogio ou sugestão, eu gosto muito de ouvir. {volta}")


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
