"""Cadastro de produtos — chamada à Cloud Function `cadastrador` (sb_data_pipeline, `cadastrador/edicao.py`).

A regra (o que pode mudar, validação, ordem Notion -> Bling -> Nuvemshop, trava de simulação) mora na function; aqui só se
pede a ação e se mostra o resultado. A function é autenticada (OIDC): o Streamlit usa a mesma service account do BigQuery
(st.secrets["gcp"]) para gerar o token, e essa conta precisa de `run.invoker` nela.

Ações: buscar, ler, simular (não grava), aplicar (grava; exige o `hash` da simulação e o nome do operador).
Erros: `CadastradorErro(status, mensagem)`; status 409 = a simulação venceu (o produto ou a edição mudou): simular de novo.
"""
import requests
import streamlit as st
from google.auth.transport.requests import Request
from google.oauth2 import service_account

URL_FUNCTION = "https://cadastrador-jpt6bmdtaa-uk.a.run.app"
TIMEOUTS = {"buscar": 60, "ler": 90, "simular": 120, "aplicar": 290}


class CadastradorErro(Exception):
    def __init__(self, status, mensagem):
        super().__init__(mensagem)
        self.status = status
        self.mensagem = mensagem


def _token() -> str:
    creds = service_account.IDTokenCredentials.from_service_account_info(dict(st.secrets["gcp"]), target_audience=URL_FUNCTION)
    creds.refresh(Request())
    return creds.token


def _chamar(acao: str, **params):
    resp = requests.post(URL_FUNCTION, timeout=TIMEOUTS[acao], headers={"Authorization": f"Bearer {_token()}"},
                         json={"acao": acao, **params})
    try:
        corpo = resp.json()
    except ValueError:
        corpo = {"result": resp.text[:300]}
    if resp.status_code != 200:
        raise CadastradorErro(resp.status_code, str(corpo.get("result", corpo)).removeprefix("erro: "))
    return corpo["dados"]


def buscar(termo: str) -> list[dict]:
    """Páginas do catálogo (Notion) que casam com o nome ou o SKU: page_id, produto, sku, status, id_bling, id_nuvemshop."""
    return _chamar("buscar", termo=termo)


def ler(page_id: str) -> dict:
    """Estado do produto nos três canais, o que é editável (`notion`), a comparação e as vitrines que existem na loja."""
    return _chamar("ler", page_id=page_id)


def simular(page_id: str, edicao: dict) -> dict:
    """O que mudaria em cada canal. Não grava. Devolve erros (bloqueiam), avisos, passos, manual, hash e pode_aplicar."""
    return _chamar("simular", page_id=page_id, edicao=edicao)


def aplicar(page_id: str, edicao: dict, hash_: str, operador: str) -> dict:
    """Grava a edição simulada. Levanta CadastradorErro(409) se algo mudou desde a simulação."""
    return _chamar("aplicar", page_id=page_id, edicao=edicao, hash=hash_, operador=operador)
