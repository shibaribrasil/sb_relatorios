"""Frescor dos dados de uma página: quando o dbt recriou as tabelas lidas e quando cada extração rodou com sucesso.

Fluxo (desde 28/09/2026): extrações das fontes que alimentam as páginas diárias rodam de hora em hora entre :30 e :45,
e o dbt (`sb-dbt-run`) roda na hora cheia, 7h–23h. Assim, dentro de uma página, todas as tabelas vêm da mesma rodada.
A página mostra o horário da tabela mais ANTIGA que ela lê (o dado nunca é mais novo que isso) e, num expansor,
o horário de cada fonte. GA4 é diário por natureza (transferência do Google) e aparece à parte.
"""
import pandas as pd
import streamlit as st

from common import bigquery as bq
from common.design import note

FUSO = "America/Sao_Paulo"
ATRASO_ALERTA_MIN = 90   # dbt sem rodar há mais que isso (dentro da janela 7h–23h) = alerta na página

ROTULOS_EXTRACAO = {
    "bling_products": "Produtos e estoque (Bling)",
    "bling_orders": "Pedidos (Bling)",
    "bling_purchases": "Pedidos de compra (Bling)",
    "nuvemshop_products": "Produtos e visibilidade (Nuvemshop)",
    "nuvemshop_orders": "Pedidos (Nuvemshop)",
    "nuvemshop_fulfillments": "Rastreio de envio (Nuvemshop)",
    "nuvemshop_customers": "Clientes e carrinhos (Nuvemshop)",
}


@st.cache_data(ttl=300)
def carregar_frescor(tabelas_az: tuple, extratores: tuple, com_ga4: bool = False):
    client = bq.get_client()
    lista_t = ", ".join(f"'{t}'" for t in tabelas_az)
    lista_e = ", ".join(f"'{e}'" for e in extratores)
    tab = bq.query_df(client, f"""
        SELECT table_id AS tabela, DATETIME(TIMESTAMP_MILLIS(last_modified_time), '{FUSO}') AS dt_atualizacao
          FROM `{bq.PROJECT}.dbt_dw_az.__TABLES__` WHERE table_id IN ({lista_t})
    """)
    ext = bq.query_df(client, f"""
        SELECT extractor AS extrator, DATETIME(MAX(finished_at), '{FUSO}') AS dt_atualizacao
          FROM `{bq.PROJECT}.raw_control.pipeline_runs`
         WHERE extractor IN ({lista_e}) AND status = 'success'
           AND started_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY)
         GROUP BY 1
    """)
    ga4 = None
    if com_ga4:
        g = bq.query_df(client, f"SELECT MAX(dt_data) AS dt FROM `{bq.PROJECT}.dbt_dw_us_az.tb_ga4_pagina_diaria`")
        ga4 = pd.to_datetime(g["dt"].iloc[0]) if not g.empty else None
    tab["dt_atualizacao"] = pd.to_datetime(tab["dt_atualizacao"])
    ext["dt_atualizacao"] = pd.to_datetime(ext["dt_atualizacao"])
    return {"tabelas": tab, "extracoes": ext, "ga4": ga4}


def _fmt(ts):
    return ts.strftime("%d/%m %H:%M") if ts is not None and pd.notna(ts) else "—"


def badge_atualizacao(fr):
    """HTML do selo do cabeçalho: horário da tabela mais antiga que a página lê."""
    ts = fr["tabelas"]["dt_atualizacao"].min() if not fr["tabelas"].empty else None
    return f'<div class="report-badge">Atualizado em: <strong>{_fmt(ts)}</strong></div>'


def detalhe_atualizacao(fr):
    """Expansor com o horário de cada fonte + alerta se o dbt atrasou."""
    tab, ext = fr["tabelas"], fr["extracoes"]
    agora = pd.Timestamp.now(tz=FUSO).tz_localize(None)
    ts = tab["dt_atualizacao"].min() if not tab.empty else None
    if ts is not None and pd.notna(ts) and 7 <= agora.hour <= 23 and (agora - ts).total_seconds() / 60 > ATRASO_ALERTA_MIN:
        note(f"<strong>Dados atrasados:</strong> a última atualização foi em {_fmt(ts)}. O normal é a cada hora cheia, das 7h às 23h — "
             "confira o job <code>sb-dbt-run</code> e as extrações.", variant="warn")
    with st.expander("De quando são os dados desta página"):
        linhas = [{"Fonte": ROTULOS_EXTRACAO.get(r.extrator, r.extrator), "Etapa": "Extração", "Última execução": r.dt_atualizacao}
                  for r in ext.sort_values("extrator").itertuples()]
        linhas += [{"Fonte": r.tabela, "Etapa": "Tabela (dbt)", "Última execução": r.dt_atualizacao}
                   for r in tab.sort_values("tabela").itertuples()]
        st.dataframe(pd.DataFrame(linhas), hide_index=True, use_container_width=True,
                     column_config={"Última execução": st.column_config.DatetimeColumn(format="DD/MM HH:mm")})
        extra = f" Visitas do site (GA4) vão até {fr['ga4'].strftime('%d/%m')} — o Google entrega 1× por dia, com 1–2 dias de atraso." \
            if fr.get("ga4") is not None else ""
        st.caption("As extrações rodam de hora em hora entre :30 e :45 e o dbt recria as tabelas na hora cheia (7h–23h): o horário do "
                   "cabeçalho é o da tabela mais antiga desta página." + extra)
