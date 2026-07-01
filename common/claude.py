"""Client Anthropic (Claude API) compartilhado por todos os relatórios.

Espelha o padrão de common/bigquery.py: client construído a partir de
st.secrets, sem cache de client aqui (cache de resultado fica por conta de
cada relatório — ver reports/google_ads.py).
"""
import anthropic
import streamlit as st

MODEL_HAIKU = "claude-haiku-4-5-20251001"


def get_client():
    return anthropic.Anthropic(api_key=st.secrets["anthropic"]["api_key"])


def gerar_texto_estruturado(client, system, user, schema, model=MODEL_HAIKU, max_tokens=1500):
    """Chama a Claude API forçando o retorno no formato de `schema` (JSON Schema),
    via tool-use forçado — evita parsing manual de texto livre. Devolve
    (dict validado pelo schema, usage da chamada).
    """
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
        tools=[{
            "name": "responder",
            "description": "Devolve a resposta estruturada no formato pedido.",
            "input_schema": schema,
        }],
        tool_choice={"type": "tool", "name": "responder"},
    )
    for bloco in resp.content:
        if bloco.type == "tool_use":
            return bloco.input, resp.usage
    raise ValueError("Claude não retornou tool_use apesar de tool_choice forçado")
