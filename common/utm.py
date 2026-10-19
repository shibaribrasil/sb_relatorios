"""UTM dos links que o SAC manda ao cliente por WhatsApp (plano de rastreamento de 19/10/2026, frente 3).

Convenção do Manual de Rastreamento: minúsculas, sem acento, sem espaço (separador `_`). Todo link de WhatsApp gerado aqui leva
`utm_source=whatsapp`, `utm_medium=messaging` e um `utm_campaign` que diz DE QUAL mensagem veio (tabela `CAMPANHA_UTM`). Sem isso o pedido
cai em "(sem parametro)" na `tb_atribuicao_pedido` e o clique vira "Direct" no GA4.

Testado em 19/10/2026: o redirecionamento de `/discount/<código>` da Nuvemshop MANTÉM os parâmetros; o link de recuperação de carrinho
(`/checkout/v3/proxy/<carrinho>/<hash>`) responde 200 sem redirecionar e o checkout lê os parâmetros (vão em `location.queries`) e carrega o GTM.
"""
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

UTM_SOURCE = "whatsapp"
UTM_MEDIUM = "messaging"

# campanha da fila do SAC (`ds_campanha` do dbt / tipo do SAC) -> utm_campaign
CAMPANHA_UTM = {
    "jornada_w0": "posvenda_w0",            # inclui a W0 combinada (check-in + cashback) da onda de lançamento
    "jornada_w1": "posvenda_w1",
    "jornada_w2": "posvenda_w2",
    "jornada_w2_final": "posvenda_w2",      # lembrete final do 3º pedido: mesma W2 na leitura
    "esgotamento_maturacao": "posvenda_esgotamento",
    "esgotamento_dormente": "posvenda_esgotamento",
    "recompra_perdido": "posvenda_perdido",  # piloto do último contato dos Perdido (crédito de retorno)
    "recuperacao": "recuperacao",           # carrinho abandonado e recontato com cupom
}


def normaliza(valor: str) -> str:
    """Minúsculas, sem acento, só letras, números e `_` (qualquer outra sequência vira um `_`)."""
    sem_acento = unicodedata.normalize("NFKD", str(valor)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", sem_acento.lower()).strip("_")


def utm_campaign(campanha: str) -> str:
    """`utm_campaign` da mensagem. Levanta KeyError para campanha desconhecida (melhor quebrar no teste do que mandar link sem rastreio)."""
    return CAMPANHA_UTM[campanha]


def com_utm(url, campanha: str):
    """`url` com os UTM da mensagem `campanha` (chave de `CAMPANHA_UTM`). Mantém o que já houver na query e substitui os `utm_*` próprios
    (idempotente). Devolve `url` como veio se não for texto (None/NaN: carrinho sem link)."""
    if not isinstance(url, str) or not url.strip():
        return url
    partes = urlsplit(url.strip())
    utm = {"utm_source": UTM_SOURCE, "utm_medium": UTM_MEDIUM, "utm_campaign": normaliza(utm_campaign(campanha))}
    resto = [(k, v) for k, v in parse_qsl(partes.query, keep_blank_values=True) if k not in utm]
    return urlunsplit(partes._replace(query=urlencode(resto + list(utm.items()), safe="_")))
