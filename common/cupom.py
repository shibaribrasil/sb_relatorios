"""Cupons de desconto gerados pelo SAC — chamada à Cloud Function `nuvemshop-criar-cupom` e leitura do que já foi gerado.

A regra de "que cupom" (prefixo, %, validade, uso único) mora na function (sb_data_pipeline, `writers/nuvemshop_cupom.py`,
um PRESET por campanha); aqui só se pede a campanha e se mostra o resultado. A function é autenticada (OIDC): o Streamlit
usa a mesma service account do BigQuery (st.secrets["gcp"]) para gerar o token, e essa conta precisa de `run.invoker` nela.

Estado: `raw_control.cupons_gerados` (append-only, escrito pela function; fora do dbt). 1 cupom por (campanha, referência) —
pedir de novo para a mesma referência devolve o mesmo cupom.
"""
import pandas as pd
import requests
import streamlit as st
from google.auth.transport.requests import Request
from google.oauth2 import service_account

from common import bigquery as bq

URL_FUNCTION = "https://nuvemshop-criar-cupom-jpt6bmdtaa-uk.a.run.app"
CAMPANHA_RECUPERACAO_WHATSAPP = "cupom_recuperacao_venda_whatsapp"
CAMPANHA_CASHBACK = "cupom_cashback_posvenda"  # cashback da jornada (W1/W2): 10% do pedido, em reais, por cliente; 30 dias (preset na function)
# Cashback = PCT_CASHBACK do valor líquido de produtos do pedido que gerou o contato (Hugo, 13/10/2026), com piso e teto de segurança. Os mesmos
# limites estão no preset da function (sb_data_pipeline, `writers/nuvemshop_cupom.py`: valor_min e valor_max); a function recusa fora deles.
PCT_CASHBACK = 0.10
CASHBACK_MINIMO = 5.00
CASHBACK_MAXIMO = 50.00
CAMPANHA_RETORNO_MATURACAO = "cupom_retorno_maturacao"  # crédito do esgotamento em maturação (R$ 20, 21 dias)
CAMPANHA_RETORNO_DORMENTE = "cupom_retorno_dormente"  # crédito do esgotamento dormente (R$ 20, 21 dias)
CAMPANHA_RETORNO_PERDIDO = "cupom_retorno_perdido"  # crédito de retorno de R$ 20 (preset novo na function; 07/10/2026)
# Valor mínimo de compra do crédito de retorno: mesmo valor do preset `cupom_retorno_perdido` (sb_data_pipeline,
# `writers/nuvemshop_cupom.py`). Entra no texto da mensagem. R$ 120, confirmado pelo Hugo em 07/10/2026 — mude nos dois lugares.
MIN_COMPRA_RETORNO = 120


def valor_cashback(base) -> float | None:
    """Cashback de um cliente: PCT_CASHBACK (10%) do valor do pedido `base` (receita líquida de produtos, sem frete), arredondado PARA BAIXO em
    centavos (nunca passa de 10%), com piso CASHBACK_MINIMO e teto CASHBACK_MAXIMO. None se não houver base (pedido sem valor)."""
    try:
        b = float(base)
    except (TypeError, ValueError):
        return None
    if b != b or b <= 0:  # NaN ou sem valor
        return None
    bruto = int(b * PCT_CASHBACK * 100 + 1e-9) / 100  # floor em centavos
    return round(min(max(bruto, CASHBACK_MINIMO), CASHBACK_MAXIMO), 2)
LINK_LOJA = "https://shibaribrasil.com.br"
TABELA = f"{bq.PROJECT}.raw_control.cupons_gerados"


def link_cupom(codigo: str) -> str:
    """Link da loja que aplica o cupom sozinho ao entrar (formato `/discount/<código>` da Nuvemshop). Ainda não testado nesta loja:
    a 1ª mensagem do piloto é o teste (carrinho vazio e com item, valor mínimo, uso único)."""
    return f"{LINK_LOJA}/discount/{codigo}"


def _token() -> str:
    creds = service_account.IDTokenCredentials.from_service_account_info(dict(st.secrets["gcp"]), target_audience=URL_FUNCTION)
    creds.refresh(Request())
    return creds.token


def gerar_cupom(campanha: str, referencia: str, solicitante: str = "sac", params: dict | None = None) -> dict:
    """Pede o cupom à function. Devolve {codigo, tipo, valor, expira_em, ja_existia, ...}; levanta RuntimeError com a
    mensagem da function se ela recusar ou falhar."""
    resp = requests.post(
        URL_FUNCTION, timeout=60, headers={"Authorization": f"Bearer {_token()}"},
        json={"campanha": campanha, "referencia": referencia, "solicitante": solicitante, **({"params": params} if params else {})},
    )
    if resp.status_code != 200:
        try:
            detalhe = resp.json().get("result", resp.text)
        except ValueError:
            detalhe = resp.text
        raise RuntimeError(f"Não foi possível gerar o cupom ({resp.status_code}): {detalhe}")
    carregar_cupons.clear()
    return resp.json()


@st.cache_data(ttl=30)
def carregar_cupons(campanha: str) -> pd.DataFrame:
    """Cupons já gerados da campanha: referencia, codigo, valor, valido_de, expira_em (naive, horário de Brasília)."""
    client = bq.get_client()
    try:
        df = bq.query_df(client, f"""
            SELECT referencia, codigo, valor, valido_de, expira_em
              FROM `{TABELA}` WHERE campanha = '{campanha}'
            QUALIFY ROW_NUMBER() OVER (PARTITION BY referencia ORDER BY criado_em DESC) = 1
        """)
    except Exception as e:  # tabela só nasce no 1º cupom gerado
        if "Not found" in str(e):
            return pd.DataFrame(columns=["referencia", "codigo", "valor", "valido_de", "expira_em"])
        raise
    for c in ("valido_de", "expira_em"):
        df[c] = pd.to_datetime(df[c], utc=True).dt.tz_convert("America/Sao_Paulo").dt.tz_localize(None)
    return df


def cashback_e_10_por_cento(valor, base) -> bool:
    """Verdadeiro se o cashback `valor` é exatamente PCT_CASHBACK do pedido `base` (sem o piso ou o teto de segurança). A mensagem só diz
    "10% do valor do seu pedido" nesse caso; com piso ou teto aplicado, o texto omite o percentual (para não afirmar o que não é verdade)."""
    try:
        v = float(valor)
    except (TypeError, ValueError):
        return False
    if v != v:
        return False
    b = valor_cashback(base)
    if b is None:
        return False
    return abs(v - b) < 0.005 and CASHBACK_MINIMO < b < CASHBACK_MAXIMO

