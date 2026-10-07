"""Relatório SAC — Tarefas do Dia — Shibari Brasil (camada diária).

Regras: ver specs/sac.md. Página de trabalho do SAC (hoje: Robson), não de leitura gerencial — cada lista
tem uma tarefa, um link de WhatsApp com a mensagem pronta para aquela situação e um checkbox de "já tratei".
O estado do checkbox vem de `raw_control.sac_tarefas` (common/tarefas.py), uma tabela que o dbt **nunca**
recria — o que foi marcado ou deixado sem marcar continua exatamente assim depois que os dados são
atualizados (o item só entra ou sai da lista conforme a situação real dele mudar; o check em si nunca se perde).

Listas: entregas com problema (tb_logistica_pedido), carrinhos abandonados (tb_carrinho_abandonado) e pedidos
cancelados (tb_pedido_cancelado). Quem entra em cada lista e o tipo de cada situação vêm do dbt; aqui só a
janela de tempo, a ordem e o texto da mensagem (common/mensagens_sac.py).
"""
import pandas as pd
import streamlit as st

from common import bigquery as bq
from common import mensagens_sac as msg
from common.design import inject_css, section_title, note, card, render_cards, brl
from common.logistica import carregar_logistica
from common import cupom as cup
from common.tarefas import carregar_tarefas, salvar_tarefas, TABELA as TAB_TAREFAS, HISTORICO as TAB_HISTORICO
from common.frescor import carregar_frescor, badge_atualizacao, detalhe_atualizacao, alerta_atraso

TABELAS = ("tb_logistica_pedido", "tb_carrinho_abandonado", "tb_pedido_cancelado", "tb_pedido_recompra_entregue")
EXTRATORES = ("nuvemshop_orders", "nuvemshop_fulfillments", "nuvemshop_customers", "bling_orders")

TIPO_ENTREGA_PROBLEMA = "entrega_problema"
TIPO_CARRINHO = "carrinho_abandonado"
TIPO_CANCELADO = "pedido_cancelado"
TIPO_RECONTATO = "recontato_cupom"
TIPO_PROXIMIDADE = "proximidade_pos_entrega"
TIPO_RECOMPRA = "recompra_piloto"  # piloto do Ecossistema de Pós-Venda: último contato dos "Perdido" (spec: specs/sac.md)
CAMPANHA_LISTA_RECOMPRA = "perdido_ultimo_contato"
TAB_LISTA = f"{bq.PROJECT}.raw_control.posvenda_lista"  # lista operacional montada à mão e liberada em ondas (fg_liberado)
LIMITE_CUPONS_POR_CLIQUE = 30  # trava de segurança do botão "Gerar cupons pendentes"
LOTE_DIARIO_RECOMPRA = 20  # clientes sem contato registrado que a lista mantém (Hugo, 07/10/2026): o lote do dia completa até este número
# Proximidade: o registro nasce no dia seguinte à entrega (D+1) e FICA até o Robson tratar — não há prazo para sumir (decisão do Hugo,
# 28/09/2026: fim de semana, folga ou imprevisto não podem fazer o contato desaparecer). PROXIMIDADE_DESDE = 1ª entrega considerada:
# 30 dias antes do lançamento da lista (28/09/2026), para já contatar o que ficou para trás.
PROXIMIDADE_DESDE = pd.Timestamp("2026-08-29")
DIAS_RECONTATO = 7     # dias depois do 1º contato do SAC (marcado "Já tratei") para o recontato com cupom (decisão do Hugo, 28/09)
DIAS_PARADO = 10        # mesmo limite usado no Pulso do Dia (decisão de apresentação)
JANELA_CARRINHO = 15    # dias: carrinho mais velho que isso não vale mais contato
JANELA_CANCELADO = 30   # dias desde o cancelamento
FUSO = "America/Sao_Paulo"

JA_TRATEI, RESOLUCAO, OBS_SAC = "Já tratei", "Resolução", "Observação SAC"  # colunas editáveis; entram em `colunas` na posição desejada
EDITAVEIS = (JA_TRATEI, RESOLUCAO, OBS_SAC)
COL_WHATSAPP = st.column_config.LinkColumn("WhatsApp", display_text="Mensagem", width="small")
COL_OBS = st.column_config.TextColumn("Obs.", width="large")


def _agora():
    return pd.Timestamp.now(tz=FUSO).tz_localize(None)


# --- Cargas ----------------------------------------------------------------------------------------------------

@st.cache_data(ttl=300)
def carregar_carrinhos():
    client = bq.get_client()
    df = bq.query_df(client, f"""
        SELECT cd_carrinho, ts_criacao, nm_cliente, nr_telefone, ds_email_cliente, vl_total_carrinho,
               fg_cliente_recorrente, ds_url_recuperacao
          FROM `{bq.PROJECT}.dbt_dw_az.tb_carrinho_abandonado`
         WHERE NOT fg_recuperado AND NOT fg_teste AND vl_total_carrinho > 0
           AND DATE(ts_criacao) >= DATE_SUB(CURRENT_DATE('{FUSO}'), INTERVAL {JANELA_CARRINHO} DAY)
    """)
    df["ts_criacao"] = pd.to_datetime(df["ts_criacao"])
    df["vl_total_carrinho"] = pd.to_numeric(df["vl_total_carrinho"]).fillna(0.0)
    df["fg_cliente_recorrente"] = df["fg_cliente_recorrente"].fillna(False).astype(bool)
    return df


@st.cache_data(ttl=300)
def carregar_cancelados():
    client = bq.get_client()
    df = bq.query_df(client, f"""
        SELECT cd_pedido_nuvemshop, cd_pedido_loja, dt_pedido, dt_cancelamento, ds_motivo_cancelamento,
               ds_tipo_cancelamento, ds_origem_cancelamento, fg_estorno_a_conferir, ds_meio_pagamento,
               vl_total_pedido, nm_cliente, ds_email_cliente, nr_telefone_cliente, fg_cliente_recorrente, fg_recomprou
          FROM `{bq.PROJECT}.dbt_dw_az.tb_pedido_cancelado`
         WHERE NOT fg_teste
           AND dt_cancelamento >= DATE_SUB(CURRENT_DATE('{FUSO}'), INTERVAL {JANELA_CANCELADO} DAY)
    """)
    df["dt_cancelamento"] = pd.to_datetime(df["dt_cancelamento"])
    df["vl_total_pedido"] = pd.to_numeric(df["vl_total_pedido"]).fillna(0.0)
    for c in ["fg_estorno_a_conferir", "fg_cliente_recorrente", "fg_recomprou"]:
        df[c] = df[c].fillna(False).astype(bool)
    return df


@st.cache_data(ttl=300)
def carregar_recontato():
    """1º contato do SAC já feito (check marcado) em carrinho ou pedido cancelado, com os dados do cliente e se ele comprou desde então.
    `ts_contato` = 1ª vez que o check foi marcado (histórico; se o item é anterior ao histórico, a última atualização)."""
    client = bq.get_client()
    df = bq.query_df(client, f"""
        WITH tarefa AS (
          SELECT tipo_tarefa, chave, ds_resultado, dt_atualizacao
            FROM `{TAB_TAREFAS}`
           WHERE tipo_tarefa IN ('{TIPO_CARRINHO}', '{TIPO_CANCELADO}') AND fg_feito
          QUALIFY ROW_NUMBER() OVER (PARTITION BY tipo_tarefa, chave ORDER BY dt_atualizacao DESC) = 1
        ), contato AS (
          SELECT t.tipo_tarefa, t.chave, t.ds_resultado,
                 COALESCE((SELECT MIN(h.dt_evento) FROM `{TAB_HISTORICO}` h
                            WHERE h.tipo_tarefa = t.tipo_tarefa AND h.chave = t.chave AND h.fg_feito),
                          t.dt_atualizacao) AS ts_contato
            FROM tarefa t
        )
        SELECT c.tipo_tarefa, c.chave, c.ds_resultado AS ds_resolucao_contato, c.ts_contato,
               'carrinho' AS origem, cr.nm_cliente, cr.nr_telefone AS nr_telefone, cr.ds_email_cliente,
               cr.vl_total_carrinho AS vl_total, CAST(NULL AS STRING) AS cd_pedido_loja, cr.ds_url_recuperacao,
               cr.fg_recuperado AS fg_comprou, cr.fg_teste, CAST(NULL AS STRING) AS ds_motivo_cancelamento
          FROM contato c JOIN `{bq.PROJECT}.dbt_dw_az.tb_carrinho_abandonado` cr
            ON c.tipo_tarefa = '{TIPO_CARRINHO}' AND c.chave = CAST(cr.cd_carrinho AS STRING)
        UNION ALL
        SELECT c.tipo_tarefa, c.chave, c.ds_resultado, c.ts_contato,
               'cancelado', pc.nm_cliente, pc.nr_telefone_cliente, pc.ds_email_cliente,
               pc.vl_total_pedido, CAST(pc.cd_pedido_loja AS STRING), CAST(NULL AS STRING),
               pc.fg_recomprou, pc.fg_teste, pc.ds_motivo_cancelamento
          FROM contato c JOIN `{bq.PROJECT}.dbt_dw_az.tb_pedido_cancelado` pc
            ON c.tipo_tarefa = '{TIPO_CANCELADO}' AND c.chave = CAST(pc.cd_pedido_nuvemshop AS STRING)
    """)
    df["ts_contato"] = pd.to_datetime(df["ts_contato"], utc=True).dt.tz_convert(FUSO).dt.tz_localize(None)
    df["vl_total"] = pd.to_numeric(df["vl_total"]).fillna(0.0)
    for c in ["fg_comprou", "fg_teste"]:
        df[c] = df[c].fillna(False).astype(bool)
    return df


def _sql_nao_retomar(az: str) -> str:
    """Bloco `nao_retomar AS (...)` das queries do SAC: (tipo_tarefa, chave, e-mail) de quem tem "Não retomar contato" em QUALQUER lista.
    Precisa de uma CTE `tarefa` (último estado de cada item) antes dele. Uma só definição para a Proximidade e para a Recompra (piloto)
    nunca divergirem sobre quem pediu para não ser contatado.

    O mapeamento tarefa -> e-mail mora na view `raw_control.vw_tarefa_pessoa` (sb_data_pipeline, `sql/raw_control/views_contato_cliente.sql`),
    a MESMA que o motor de pós-venda usa para a supressão (`vw_supressao_contato`): lista nova do SAC = um ramo novo na view, e o SAC e o
    motor passam a enxergar o "Não retomar contato" juntos. Equivalência com a versão anterior (8 JOINs repetidos aqui) conferida por consulta
    em 07/10/2026 para todos os resultados existentes. O parâmetro `az` fica por compatibilidade com quem chama."""
    return f"""nao_retomar AS (
          SELECT t.tipo_tarefa, t.chave, p.email
            FROM tarefa t JOIN `{bq.PROJECT}.raw_control.vw_tarefa_pessoa` p ON p.tipo_tarefa = t.tipo_tarefa AND p.chave = t.chave
           WHERE t.ds_resultado = 'Não retomar contato'
        )"""


@st.cache_data(ttl=300)
def carregar_proximidade():
    """Recompras (2º pedido em diante) com entrega confirmada nos últimos dias, com duas marcas do estado do SAC:
    `fg_nao_retomar` (o cliente tem "Não retomar contato" em QUALQUER lista do SAC, por e-mail) e `fg_contatado_antes` (já teve
    contato de proximidade tratado em OUTRO pedido, exceto "WhatsApp inválido", que não chegou ao cliente)."""
    client = bq.get_client()
    az = f"{bq.PROJECT}.dbt_dw_az"
    df = bq.query_df(client, f"""
        WITH base AS (
          SELECT r.*, LOWER(r.ds_email_cliente) AS email
            FROM `{az}.tb_pedido_recompra_entregue` r
           WHERE NOT r.fg_teste
             AND r.dt_entrega >= DATE '{PROXIMIDADE_DESDE:%Y-%m-%d}'
        ), tarefa AS (
          SELECT tipo_tarefa, chave, ds_resultado, fg_feito, dt_atualizacao
            FROM `{TAB_TAREFAS}`
          QUALIFY ROW_NUMBER() OVER (PARTITION BY tipo_tarefa, chave ORDER BY dt_atualizacao DESC) = 1
        ), {_sql_nao_retomar(az)}, contatado AS (
          SELECT r.cd_contato, r.cd_codigo_interno
            FROM tarefa t JOIN `{az}.tb_pedido_recompra_entregue` r ON t.tipo_tarefa = '{TIPO_PROXIMIDADE}' AND t.chave = r.cd_codigo_interno
           WHERE t.fg_feito AND COALESCE(t.ds_resultado, '') != 'WhatsApp inválido'
        ), atendimento AS (
          -- quando o Robson tratou: 1ª vez que o check foi marcado (histórico); se for anterior ao histórico, a última atualização
          SELECT t.chave,
                 COALESCE((SELECT MIN(h.dt_evento) FROM `{TAB_HISTORICO}` h
                            WHERE h.tipo_tarefa = t.tipo_tarefa AND h.chave = t.chave AND h.fg_feito), t.dt_atualizacao) AS ts_atendimento
            FROM tarefa t WHERE t.tipo_tarefa = '{TIPO_PROXIMIDADE}' AND t.fg_feito
        )
        SELECT b.cd_codigo_interno, b.cd_pedido_loja, b.cd_contato, b.nm_cliente, b.ds_email_cliente, b.nr_telefone_cliente,
               b.dt_entrega, b.nr_pedido_cliente, b.vl_total_pedido, b.nm_produto_principal, b.fg_entrega_atrasada,
               b.qt_dias_atraso_entrega, a.ts_atendimento,
               EXISTS (SELECT 1 FROM nao_retomar n WHERE n.email = b.email
                          AND NOT (n.tipo_tarefa = '{TIPO_PROXIMIDADE}' AND n.chave = b.cd_codigo_interno)) AS fg_nao_retomar,
               EXISTS (SELECT 1 FROM contatado c WHERE c.cd_contato = b.cd_contato
                          AND c.cd_codigo_interno != b.cd_codigo_interno) AS fg_contatado_antes
          FROM base b LEFT JOIN atendimento a ON a.chave = b.cd_codigo_interno
    """)
    df["dt_entrega"] = pd.to_datetime(df["dt_entrega"])
    df["ts_atendimento"] = pd.to_datetime(df["ts_atendimento"], utc=True).dt.tz_convert(FUSO).dt.tz_localize(None)
    df["vl_total_pedido"] = pd.to_numeric(df["vl_total_pedido"]).fillna(0.0)
    for c in ["fg_entrega_atrasada", "fg_nao_retomar", "fg_contatado_antes"]:
        df[c] = df[c].fillna(False).astype(bool)
    return df


@st.cache_data(ttl=300)
def carregar_recompra_piloto():
    """Piloto de recompra (último contato dos "Perdido"): só o que foi LIBERADO (`fg_liberado`) e tem consentimento conferido
    (`ds_consentimento = 'true'`) em `raw_control.posvenda_lista`, com três marcas do estado atual: `fg_nao_retomar` ("Não retomar
    contato" em QUALQUER lista do SAC, por e-mail; a marcação feita nesta própria lista não conta, para o item continuar visível
    até ser tratado), `fg_comprou_depois` (pedido no DW desde a criação da lista) e `fg_comprou_com_cupom` (pedido com o código do
    cupom gerado para ele). Regras em specs/sac.md."""
    client = bq.get_client()
    az = f"{bq.PROJECT}.dbt_dw_az"
    df = bq.query_df(client, f"""
        WITH tarefa AS (
          SELECT tipo_tarefa, chave, ds_resultado, fg_feito, dt_atualizacao
            FROM `{TAB_TAREFAS}`
          QUALIFY ROW_NUMBER() OVER (PARTITION BY tipo_tarefa, chave ORDER BY dt_atualizacao DESC) = 1
        ), {_sql_nao_retomar(az)}, cupom AS (
          SELECT referencia, codigo
            FROM `{cup.TABELA}` WHERE campanha = '{cup.CAMPANHA_RETORNO_PERDIDO}'
          QUALIFY ROW_NUMBER() OVER (PARTITION BY referencia ORDER BY criado_em DESC) = 1
        )
        SELECT l.chave, l.nm_cliente, l.email, l.nr_telefone, l.dt_ultima_compra, l.vl_total_gasto, l.qt_pedidos, l.onda, l.ordem,
               EXISTS (SELECT 1 FROM nao_retomar n WHERE n.email = LOWER(l.email)
                          AND NOT (n.tipo_tarefa = '{TIPO_RECOMPRA}' AND n.chave = l.chave)) AS fg_nao_retomar,
               COALESCE((SELECT MAX(c.dt_ult_pedido) FROM `{az}.tb_cliente` c WHERE LOWER(c.ds_email) = LOWER(l.email))
                        >= DATE(l.criado_em, '{FUSO}'), FALSE) AS fg_comprou_depois,
               EXISTS (SELECT 1 FROM cupom k JOIN `{az}.tb_pedido` p ON p.ds_codigo_cupom_nuvemshop = k.codigo
                        WHERE k.referencia = l.chave) AS fg_comprou_com_cupom
          FROM `{TAB_LISTA}` l
         WHERE l.campanha = '{CAMPANHA_LISTA_RECOMPRA}' AND l.fg_liberado AND l.ds_consentimento = 'true'
    """)
    df["dt_ultima_compra"] = pd.to_datetime(df["dt_ultima_compra"])
    df["vl_total_gasto"] = pd.to_numeric(df["vl_total_gasto"]).fillna(0.0)
    for c in ["fg_nao_retomar", "fg_comprou_depois", "fg_comprou_com_cupom"]:
        df[c] = df[c].fillna(False).astype(bool)
    return df


COLUNAS_RECOMPRA = ["chave", "nm_cliente", "email", "nr_telefone", "dt_ultima_compra", "vl_total_gasto", "qt_pedidos", "onda", "ordem",
                    "fg_nao_retomar", "fg_comprou_depois", "fg_comprou_com_cupom"]


def carregar_recompra_piloto_seguro():
    """A lista do piloto não deve derrubar o resto da página do SAC se a carga falhar."""
    try:
        return carregar_recompra_piloto()
    except Exception as e:
        st.warning(f"Não consegui montar a lista de recompra (piloto) agora: {e}")
        return None


def _sql_completar_lote(az: str, lote: int) -> str:
    """UPDATE que libera (`fg_liberado`) os próximos clientes da fila para completar `lote` clientes SEM contato registrado.

    Regras (decisão do Hugo, 07/10/2026): todo dia a lista carrega novos clientes que ainda não foram contatados e completa até `lote`
    — se sobraram contatos que o atendente não conseguiu fazer ontem, só entra a diferença. Detalhes:
    - roda no máximo UMA vez por dia (se alguém já foi liberado hoje, não faz nada): tratar tudo de manhã não puxa mais gente no mesmo dia;
    - "sem contato registrado" = liberado, visível (consentimento 'true', sem "Não retomar contato" em outra lista, sem compra depois
      da criação da lista nem com o cupom) e SEM o check "Já tratei" em `sac_tarefas`;
    - a fila é a ordem (`ordem`) dos clientes com `ds_consentimento = 'true'` ainda não liberados: quem não tem consentimento conferido
      nunca entra;
    - linhas de teste (chave que começa com 'teste') ficam fora da conta e da liberação.
    Devolve o SQL; quem chama executa e lê `num_dml_affected_rows`."""
    return f"""
        UPDATE `{TAB_LISTA}` l
           SET fg_liberado = TRUE, dt_liberacao = CURRENT_TIMESTAMP(),
               onda = (SELECT COALESCE(MAX(x.onda), 0) + 1 FROM `{TAB_LISTA}` x WHERE x.campanha = '{CAMPANHA_LISTA_RECOMPRA}' AND NOT STARTS_WITH(x.chave, 'teste'))
         WHERE l.campanha = '{CAMPANHA_LISTA_RECOMPRA}' AND NOT l.fg_liberado AND l.ds_consentimento = 'true'
           AND NOT STARTS_WITH(l.chave, 'teste')
           AND NOT EXISTS (SELECT 1 FROM `{TAB_LISTA}` y WHERE y.campanha = '{CAMPANHA_LISTA_RECOMPRA}' AND NOT STARTS_WITH(y.chave, 'teste')
                              AND DATE(y.dt_liberacao, '{FUSO}') = CURRENT_DATE('{FUSO}'))
           AND l.chave IN (
             WITH tarefa AS (
               SELECT tipo_tarefa, chave, ds_resultado, fg_feito, dt_atualizacao
                 FROM `{TAB_TAREFAS}`
               QUALIFY ROW_NUMBER() OVER (PARTITION BY tipo_tarefa, chave ORDER BY dt_atualizacao DESC) = 1
             ), {_sql_nao_retomar(az)}, cupom AS (
               SELECT referencia, codigo FROM `{cup.TABELA}` WHERE campanha = '{cup.CAMPANHA_RETORNO_PERDIDO}'
               QUALIFY ROW_NUMBER() OVER (PARTITION BY referencia ORDER BY criado_em DESC) = 1
             ), base AS (
               SELECT b.chave, b.ordem, b.fg_liberado,
                      EXISTS (SELECT 1 FROM nao_retomar n WHERE n.email = LOWER(b.email)
                                 AND NOT (n.tipo_tarefa = '{TIPO_RECOMPRA}' AND n.chave = b.chave)) AS fg_nao_retomar,
                      COALESCE((SELECT MAX(c.dt_ult_pedido) FROM `{az}.tb_cliente` c WHERE LOWER(c.ds_email) = LOWER(b.email))
                               >= DATE(b.criado_em, '{FUSO}'), FALSE) AS fg_comprou_depois,
                      EXISTS (SELECT 1 FROM cupom k JOIN `{az}.tb_pedido` p ON p.ds_codigo_cupom_nuvemshop = k.codigo
                               WHERE k.referencia = b.chave) AS fg_comprou_com_cupom,
                      EXISTS (SELECT 1 FROM tarefa t WHERE t.tipo_tarefa = '{TIPO_RECOMPRA}' AND t.chave = b.chave AND t.fg_feito) AS fg_tratado
                 FROM `{TAB_LISTA}` b
                WHERE b.campanha = '{CAMPANHA_LISTA_RECOMPRA}' AND b.ds_consentimento = 'true' AND NOT STARTS_WITH(b.chave, 'teste')
             ), pendentes AS (
               SELECT COUNT(*) AS n FROM base
                WHERE fg_liberado AND NOT fg_tratado AND NOT fg_nao_retomar AND NOT fg_comprou_depois AND NOT fg_comprou_com_cupom
             ), fila AS (
               SELECT chave, ROW_NUMBER() OVER (ORDER BY ordem) AS posicao FROM base
                WHERE NOT fg_liberado AND NOT fg_nao_retomar AND NOT fg_comprou_depois AND NOT fg_comprou_com_cupom
             )
             SELECT f.chave FROM fila f CROSS JOIN pendentes p WHERE f.posicao <= GREATEST(0, {int(lote)} - p.n)
           )
    """


def completar_lote_diario_recompra() -> int:
    """Libera os clientes que faltam para a lista ter `LOTE_DIARIO_RECOMPRA` sem contato registrado (1x por dia). Devolve quantos
    foram liberados. Escreve em `raw_control.posvenda_lista` (a mesma conta que grava os checks do SAC)."""
    client = bq.get_client()
    job = client.query(_sql_completar_lote(f"{bq.PROJECT}.dbt_dw_az", LOTE_DIARIO_RECOMPRA))
    job.result()
    return int(job.num_dml_affected_rows or 0)


def _completar_lote_seguro():
    """Roda o lote do dia na 1ª abertura da página (e só 1x por sessão e dia). Falha aqui não derruba o resto da página."""
    hoje = _agora().date()
    if st.session_state.get("lote_recompra_dia") == hoje:
        return
    try:
        liberados = completar_lote_diario_recompra()
    except Exception as e:
        st.warning(f"Não consegui completar o lote do dia da recompra (piloto): {e}")
        return
    st.session_state["lote_recompra_dia"] = hoje
    if liberados:
        carregar_recompra_piloto.clear()


def elegivel_recompra_piloto(df):
    """Quem fica na tela do piloto: liberado e consentido (já filtrado na carga), sem "Não retomar contato" em outra lista, que
    não comprou depois da criação da lista nem com o cupom (comprou = missão cumprida, o item sai sozinho)."""
    if df.empty:
        return df
    return df[~df["fg_nao_retomar"] & ~df["fg_comprou_depois"] & ~df["fg_comprou_com_cupom"]].sort_values("ordem")


def _lista_recompra_piloto(df, cupons, agora=None):
    """Itens do piloto prontos para a tabela: cupom (se já gerado) e link do WhatsApp (só com cupom ainda válido)."""
    agora = agora or _agora()
    el = elegivel_recompra_piloto(df)
    if el.empty:
        return el.assign(cupom=pd.Series(dtype=str), whatsapp=pd.Series(dtype=str), obs=pd.Series(dtype=str))
    el = el.copy()
    mapa = cupons.set_index("referencia") if not cupons.empty else pd.DataFrame(columns=["codigo", "valor", "expira_em"])
    cupom_txt, whatsapp, obs = [], [], []
    for _, r in el.iterrows():
        sem_fone = _sem_whatsapp(r, "nr_telefone", "email")
        if r["chave"] not in mapa.index:
            cupom_txt.append("— (gerar abaixo)"); whatsapp.append(None); obs.append(sem_fone)
            continue
        c = mapa.loc[r["chave"]]
        if c["expira_em"] <= agora:
            cupom_txt.append(f"{c['codigo']} — expirado"); whatsapp.append(None)
            obs.append(" · ".join(t for t in ("cupom expirado em " + c["expira_em"].strftime("%d/%m %H:%M"), sem_fone) if t))
            continue
        cupom_txt.append(f"{c['codigo']} — vence {c['expira_em'].strftime('%d/%m')}")
        whatsapp.append(msg.link_whatsapp(r["nr_telefone"], msg.msg_recompra_ultimo_contato(
            r["nm_cliente"], cup.link_cupom(c["codigo"]), c["valor"], cup.MIN_COMPRA_RETORNO, c["expira_em"])))
        obs.append(sem_fone)
    el["cupom"], el["whatsapp"], el["obs"] = cupom_txt, whatsapp, obs
    el["pedidos_txt"] = el["qt_pedidos"].map(lambda n: f"{int(n)}" if pd.notna(n) else "antes de 11/2023")
    return el


# --- Montagem das listas ---------------------------------------------------------------------------------------

def _sem_whatsapp(r, col_fone, col_email):
    return "" if msg.telefone_whatsapp(r[col_fone]) else f"sem telefone — e-mail: {r[col_email] or '—'}"


def _lista_entregas_problema(logi):
    em_transito = logi[logi["ds_situacao_logistica"] == "em_transito"]
    parado = em_transito["qt_dias_sem_movimento"] >= DIAS_PARADO
    risco = logi[logi["fg_atrasado_em_aberto"] | logi["fg_problema_entrega_ativo"] | (logi["ds_situacao_logistica"] == "em_transito") & parado].copy()

    def eh_parado(r):
        return r["ds_situacao_logistica"] == "em_transito" and r["qt_dias_sem_movimento"] >= DIAS_PARADO

    def motivo(r):
        m = []
        if r["fg_problema_entrega_ativo"]:
            m.append("problema de entrega")
        if r["fg_atrasado_em_aberto"]:
            m.append("atrasado")
        if eh_parado(r):
            m.append("parado")
        return " + ".join(m)

    numero = risco["cd_pedido_loja"].where(risco["cd_pedido_loja"].notna(), risco["cd_pedido_nuvemshop"])
    risco["codigo"] = "#" + numero.astype(str)
    risco["motivo"] = risco.apply(motivo, axis=1)
    risco["chave"] = risco["cd_codigo_interno"].astype(str)
    risco["whatsapp"] = risco.apply(lambda r: msg.link_whatsapp(r["nr_telefone_cliente"], msg.msg_entrega(
        r["nm_cliente"], numero[r.name], r["cd_rastreio"], r["ds_url_rastreio"],
        bool(r["fg_problema_entrega_ativo"]), bool(r["fg_atrasado_em_aberto"]), r["qt_dias_sem_movimento"])), axis=1)
    return risco.sort_values("qt_dias_sem_movimento", ascending=False)


def _tempo_desde(delta):
    horas = delta.total_seconds() / 3600
    if horas < 48:
        return f"{max(int(horas), 0)} h"
    return f"{int(horas // 24)} dias"


def _lista_carrinhos(car):
    if car.empty:
        return car.assign(chave=pd.Series(dtype=str))
    car = car.sort_values(["ts_criacao", "vl_total_carrinho"], ascending=[False, False]).copy()
    agora = _agora()
    idade = agora - car["ts_criacao"]
    car["chave"] = car["cd_carrinho"].astype(str)
    car["abandonado_ha"] = idade.map(_tempo_desde)
    car["cliente_antigo"] = car["fg_cliente_recorrente"].map({True: "Sim", False: "Não"})
    # mesmo telefone em mais de um carrinho = quase sempre a mesma pessoa tentando de novo: 1 mensagem só (no mais recente)
    fone = car["nr_telefone"].map(msg.telefone_whatsapp)
    repetido = fone.notna() & fone.duplicated(keep="first")
    car["whatsapp"] = [
        None if rep else msg.link_whatsapp(r["nr_telefone"], msg.msg_carrinho(r["nm_cliente"], r["fg_cliente_recorrente"], r["ds_url_recuperacao"]))
        for rep, (_, r) in zip(repetido, car.iterrows())
    ]
    car["obs"] = [
        "mesmo telefone de um carrinho acima — não reenviar" if rep else _sem_whatsapp(r, "nr_telefone", "ds_email_cliente")
        for rep, (_, r) in zip(repetido, car.iterrows())
    ]
    return car


def _lista_cancelados(canc):
    if canc.empty:
        return canc.assign(chave=pd.Series(dtype=str))
    # fora: quem já voltou a comprar (a venda foi recuperada) — exceto estorno a conferir, que é pendência financeira;
    # e suspeita de fraude, que não se contata
    canc = canc[(~canc["fg_recomprou"] | canc["fg_estorno_a_conferir"]) & (canc["ds_motivo_cancelamento"] != "fraud")].copy()
    canc = canc.sort_values(["fg_estorno_a_conferir", "dt_cancelamento", "vl_total_pedido"], ascending=[False, False, False])
    canc["chave"] = canc["cd_pedido_nuvemshop"].astype(str)
    canc["codigo"] = "#" + canc["cd_pedido_loja"].astype(str)
    canc["tipo"] = canc["ds_tipo_cancelamento"] + canc["fg_estorno_a_conferir"].map({True: " · pago, sem estorno", False: ""})
    canc["cliente_antigo"] = canc["fg_cliente_recorrente"].map({True: "Sim", False: "Não"})
    canc["whatsapp"] = canc.apply(lambda r: msg.link_whatsapp(r["nr_telefone_cliente"], msg.msg_cancelado(
        r["nm_cliente"], r["cd_pedido_loja"], r["vl_total_pedido"], r["ds_motivo_cancelamento"],
        r["fg_estorno_a_conferir"], r["ds_meio_pagamento"])), axis=1)
    # Obs.: o alerta de estorno (conferir antes de chamar) + aviso de sem telefone, quando houver
    canc["obs"] = canc.apply(lambda r: " · ".join(t for t in (
        msg.acao_cancelado(r["ds_motivo_cancelamento"], True) if r["fg_estorno_a_conferir"] else "",
        _sem_whatsapp(r, "nr_telefone_cliente", "ds_email_cliente")) if t), axis=1)
    return canc


def elegivel_recontato(df, agora=None, dias=DIAS_RECONTATO):
    """Regra do recontato com cupom (decisão do Hugo, 28/09/2026): o 1º contato do SAC foi feito (check marcado) há pelo menos
    `dias` dias; o cliente NÃO comprou desde então (carrinho recuperado / pedido refeito); a Resolução do 1º contato não é
    uma que impeça nova abordagem (msg.RESOLUCOES_SEM_RECONTATO; vazia não impede); não é teste nem suspeita de fraude.
    Um cliente com mais de um item elegível entra uma vez só (o contato mais recente)."""
    if df.empty:
        return df.assign(_pessoa=pd.Series(dtype=str))
    agora = agora or _agora()
    ok = (
        (df["ts_contato"] <= agora - pd.Timedelta(days=dias))
        & ~df["fg_comprou"] & ~df["fg_teste"]
        & ~df["ds_resolucao_contato"].isin(msg.RESOLUCOES_SEM_RECONTATO)
        & (df["ds_motivo_cancelamento"] != "fraud")
    )
    out = df[ok].copy()
    fone = out["nr_telefone"].map(msg.telefone_whatsapp)
    pessoa = fone.where(fone.notna(), out["ds_email_cliente"].fillna(out["chave"]))
    return out.assign(_pessoa=pessoa).sort_values("ts_contato", ascending=False).drop_duplicates("_pessoa", keep="first")


def _lista_recontato(df, cupons, agora=None):
    """Itens elegíveis + cupom (se já gerado) + link do WhatsApp (só com cupom ainda válido)."""
    agora = agora or _agora()
    el = elegivel_recontato(df, agora)
    if el.empty:
        return el.assign(referencia=pd.Series(dtype=str), cupom=pd.Series(dtype=str))
    el = el.sort_values("ts_contato").copy()  # o contato mais antigo primeiro: é o que está mais frio
    el["referencia"] = el["tipo_tarefa"] + ":" + el["chave"]
    el["chave"] = el["referencia"]  # a chave do checklist do recontato é a própria referência do cupom
    el["contato_em"] = el["ts_contato"].dt.normalize()
    el["origem_txt"] = el.apply(lambda r: "Carrinho" if r["origem"] == "carrinho" else f"Pedido #{r['cd_pedido_loja']}", axis=1)
    mapa = cupons.set_index("referencia") if not cupons.empty else pd.DataFrame(columns=["codigo", "valor", "expira_em"])
    cupom_txt, whatsapp, obs = [], [], []
    for _, r in el.iterrows():
        sem_fone = _sem_whatsapp(r, "nr_telefone", "ds_email_cliente")
        if r["referencia"] not in mapa.index:
            cupom_txt.append("— (gerar abaixo)"); whatsapp.append(None); obs.append(sem_fone)
            continue
        c = mapa.loc[r["referencia"]]
        if c["expira_em"] <= agora:
            cupom_txt.append(f"{c['codigo']} — expirado"); whatsapp.append(None)
            obs.append(" · ".join(t for t in ("cupom expirado em " + c["expira_em"].strftime("%d/%m %H:%M") + " — a última tentativa já foi usada", sem_fone) if t))
            continue
        cupom_txt.append(f"{c['codigo']} — vence {c['expira_em'].strftime('%d/%m %H:%M')}")
        whatsapp.append(msg.link_whatsapp(r["nr_telefone"], msg.msg_recontato_cupom(
            r["nm_cliente"], r["origem"], r["cd_pedido_loja"], c["codigo"], c["valor"], c["expira_em"], r["ds_url_recuperacao"])))
        obs.append(sem_fone)
    el["cupom"], el["whatsapp"], el["obs"] = cupom_txt, whatsapp, obs
    return el


def elegivel_proximidade(df, hoje=None):
    """Regra da lista Proximidade (decisão do Hugo, 28/09/2026): recompra (a base do dbt já garante 2º pedido em diante, entrega
    confirmada e sem reembolso) entregue a partir de PROXIMIDADE_DESDE, de D-1 em diante. **Sem prazo para sair**: o registro existe
    desde o dia seguinte à entrega e continua até o atendente tratar (o "Já tratei" marca o atendimento; o histórico fica). Fora:
    cliente com "Não retomar contato" em qualquer lista do SAC e cliente que já teve contato de proximidade em outro pedido.
    Cliente com mais de um pedido pendente entra uma vez (o mais recente); ao tratar esse, o outro sai (já foi contatado)."""
    if df.empty:
        return df
    hoje = (hoje or _agora()).normalize()
    dias = (hoje - df["dt_entrega"]).dt.days
    ok = (dias >= 1) & (df["dt_entrega"] >= PROXIMIDADE_DESDE) & ~df["fg_nao_retomar"] & ~df["fg_contatado_antes"]
    out = df[ok].assign(dias_entrega=dias[ok])
    return out.sort_values(["dt_entrega", "cd_codigo_interno"], ascending=False).drop_duplicates("cd_contato", keep="first")


def _lista_proximidade(df, hoje=None):
    """Itens da Proximidade prontos para a tabela: mensagem (com variante de atraso) e avisos."""
    el = elegivel_proximidade(df, hoje)
    if el.empty:
        return el.assign(chave=pd.Series(dtype=str))
    el = el.sort_values("dt_entrega").copy()  # o mais antigo primeiro: é o que está esperando há mais tempo
    el["chave"] = el["cd_codigo_interno"].astype(str)
    el["codigo"] = "#" + el["cd_pedido_loja"].astype(str)
    el["na_lista_desde"] = el["dt_entrega"] + pd.Timedelta(days=1)  # o registro nasce no dia seguinte à entrega
    el["atendido_em"] = el["ts_atendimento"].dt.normalize()
    el["compra_txt"] = el["nr_pedido_cliente"].map(lambda n: f"{int(n)}ª compra")
    el["whatsapp"] = el.apply(lambda r: msg.link_whatsapp(r["nr_telefone_cliente"], msg.msg_proximidade(
        r["nm_cliente"], r["cd_pedido_loja"], r["nm_produto_principal"], r["nr_pedido_cliente"], r["dias_entrega"],
        bool(r["fg_entrega_atrasada"]))), axis=1)
    el["obs"] = el.apply(lambda r: " · ".join(t for t in (
        f"entrega atrasou {int(r['qt_dias_atraso_entrega'])} dia(s) — a mensagem já pede desculpa"
        if r["fg_entrega_atrasada"] and pd.notna(r["qt_dias_atraso_entrega"]) else
        ("entrega fora do prazo — a mensagem já pede desculpa" if r["fg_entrega_atrasada"] else ""),
        _sem_whatsapp(r, "nr_telefone_cliente", "ds_email_cliente")) if t), axis=1)
    return el


# --- Seção genérica com check persistente ---------------------------------------------------------------------

def _secao_checklist(titulo, tipo_tarefa, itens, colunas, nota, column_config=None, cards_extra=None):
    """`itens` já vem com uma coluna `chave` (str). `colunas` = dict nome exibido → coluna em `itens`, NA ORDEM de exibição;
    as colunas editáveis entram com as chaves JA_TRATEI, RESOLUCAO e OBS_SAC (valor None) onde devem aparecer. "Resolução" = opções
    de msg.RESULTADOS[tipo_tarefa] (gravada em sac_tarefas.ds_resultado); "Observação SAC" = texto livre (ds_observacao, a atual
    sobrescreve a anterior). As trocas ficam na tela e o botão "Salvar alterações" grava o estado das linhas alteradas
    no BigQuery (+ histórico) e reexecuta. `column_config` (por nome exibido) formata as demais colunas."""
    section_title(titulo)
    if itens.empty:
        note(nota + " Nenhum item na lista agora — nada pendente.")
        return
    tarefas = carregar_tarefas(tipo_tarefa).set_index("chave")
    feito_atual = itens["chave"].map(lambda c: bool(tarefas.loc[c, "fg_feito"]) if c in tarefas.index else False)
    opcoes = msg.RESULTADOS[tipo_tarefa]
    resultado_atual = itens["chave"].map(
        lambda c: tarefas.loc[c, "ds_resultado"] if c in tarefas.index and tarefas.loc[c, "ds_resultado"] in opcoes else None)
    obs_atual = itens["chave"].map(lambda c: (tarefas.loc[c, "ds_observacao"] or "") if c in tarefas.index else "")
    pendentes, concluidos = int((~feito_atual).sum()), int(feito_atual.sum())
    render_cards([
        card("Pendentes", f"{pendentes}", "ainda sem contato registrado", variant="bad" if pendentes else "ok"),
        card("Já tratados", f"{concluidos}", "marcados nesta lista"),
        *(cards_extra(itens[~feito_atual.values]) if cards_extra else []),
    ])
    mostrar_feitos = st.toggle("Mostrar também os já tratados", value=False, key=f"toggle_{tipo_tarefa}")
    base = itens.assign(**{JA_TRATEI: feito_atual.values, RESOLUCAO: resultado_atual.values, OBS_SAC: obs_atual.values})
    if not mostrar_feitos:
        base = base[~base[JA_TRATEI]]
    if base.empty:
        note("Tudo tratado por aqui. Ative \"Mostrar também os já tratados\" para conferir ou mudar uma resolução.")
        return
    tabela = pd.DataFrame({nome: base[nome if nome in EDITAVEIS else coluna].values for nome, coluna in colunas.items()})
    chaves = base["chave"].to_numpy()  # fora da tabela exibida — usada só para gravar a mudança na chave certa
    # o editor guarda as trocas na tela; só vão para o BigQuery no botão "Salvar alterações" (versão na key = zera o editor após salvar)
    versao = st.session_state.get(f"versao_{tipo_tarefa}", 0)
    editado = st.data_editor(
        tabela, hide_index=True, use_container_width=True, key=f"editor_{tipo_tarefa}_{versao}",
        disabled=[c for c in tabela.columns if c not in EDITAVEIS],
        column_config={
            **(column_config or {}),
            RESOLUCAO: st.column_config.SelectboxColumn(options=opcoes, width="medium", required=False,
                                                        help="Como terminou o contato. Pode trocar depois quantas vezes precisar."),
            OBS_SAC: st.column_config.TextColumn(width="large", max_chars=500,
                                                 help="Texto livre do SAC sobre este contato. Salva junto com o resto; o texto atual sobrescreve o anterior."),
            JA_TRATEI: st.column_config.CheckboxColumn(width=90),
        },
    )
    # comparação por posição (não por índice): data_editor mantém a ordem das linhas, não reordena/filtra sozinho
    def _norm(serie):
        return serie.astype(object).where(serie.notna(), None).to_numpy()
    def _texto(serie):
        return serie.map(lambda v: v.strip() if isinstance(v, str) else "").to_numpy()
    mudou = ((editado[JA_TRATEI].to_numpy() != tabela[JA_TRATEI].to_numpy()) | (_norm(editado[RESOLUCAO]) != _norm(tabela[RESOLUCAO]))
             | (_texto(editado[OBS_SAC]) != _texto(tabela[OBS_SAC])))
    n_mudou = int(mudou.sum())
    col_botao, col_aviso = st.columns([1, 4], vertical_alignment="center")
    salvar = col_botao.button(f"Salvar alterações ({n_mudou})" if n_mudou else "Salvar alterações",
                              type="primary", disabled=not n_mudou, key=f"salvar_{tipo_tarefa}")
    if n_mudou:
        col_aviso.caption("Alterações ainda não salvas — só valem depois de clicar em Salvar. "
                          "Mudar o filtro \"Mostrar também os já tratados\" antes de salvar descarta as trocas.")
    if salvar:
        with st.spinner("Salvando..."):
            salvar_tarefas(tipo_tarefa, [
                (chave, bool(feito), resultado, obs)
                for chave, feito, resultado, obs in zip(chaves[mudou], editado[JA_TRATEI].to_numpy()[mudou],
                                                        _norm(editado[RESOLUCAO])[mudou], _texto(editado[OBS_SAC])[mudou])
            ])
        st.session_state[f"versao_{tipo_tarefa}"] = versao + 1
        st.rerun()
    note(nota)


GRUPOS = {  # visual por grupo de listas: cor da faixa, fundo suave e propósito (uma linha)
    "recuperacao": ("Recuperação de venda", "Trazer de volta quem quase comprou", "#0284C7", "#E0F2FE"),
    "problemas": ("Problemas", "Resolver antes que vire reclamação", "#D97706", "#FEF3C7"),
    "proximidade": ("Proximidade", "Cuidar de quem já é cliente — relacionamento, sem venda", "#16A34A", "#DCFCE7"),
    "recompra": ("Recompra (piloto)", "Convidar para voltar quem já comprou, com um crédito pessoal", "#7C3AED", "#EDE9FE"),
}


def _pendentes(tipo_tarefa, itens):
    """Quantos itens da lista ainda não têm o check marcado (mesma conta dos cards da seção)."""
    if itens.empty:
        return 0
    try:
        feitos = set(carregar_tarefas(tipo_tarefa).query("fg_feito")["chave"])
    except Exception:
        return 0
    return int((~itens["chave"].isin(feitos)).sum())


def _faixa_grupo(grupo, pendentes):
    """Faixa colorida que abre cada grupo de listas do SAC, com o total de pendentes do grupo."""
    titulo, proposito, cor, fundo = GRUPOS[grupo]
    chip = (f'<span style="background:{cor};color:#fff;border-radius:999px;padding:2px 12px;font-size:12px;font-weight:700">'
            f'{pendentes} pendente{"s" if pendentes != 1 else ""}</span>') if pendentes else (
            '<span style="color:#475569;font-size:12px;font-weight:600">nada pendente</span>')
    st.html(f'''
    <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;margin:38px 0 6px 0;
                padding:12px 18px;background:{fundo};border-left:6px solid {cor};border-radius:8px">
      <div>
        <div style="font-size:15px;font-weight:800;letter-spacing:.08em;text-transform:uppercase;color:{cor}">{titulo}</div>
        <div style="font-size:13px;color:#475569;margin-top:2px">{proposito}</div>
      </div>
      {chip}
    </div>
    ''')


def carregar_proximidade_seguro():
    """A lista de Proximidade não deve derrubar o resto da página do SAC se a carga falhar."""
    try:
        return carregar_proximidade()
    except Exception as e:
        st.warning(f"Não consegui montar a lista de Proximidade agora: {e}")
        return None


def carregar_recontato_seguro():
    """A lista de recontato não deve derrubar o resto da página do SAC se a carga falhar."""
    try:
        return carregar_recontato()
    except Exception as e:
        st.warning(f"Não consegui montar a lista de recontato com cupom agora: {e}")
        return pd.DataFrame(columns=["tipo_tarefa", "chave", "ds_resolucao_contato", "ts_contato", "origem", "nm_cliente", "nr_telefone",
                                     "ds_email_cliente", "vl_total", "cd_pedido_loja", "ds_url_recuperacao", "fg_comprou", "fg_teste",
                                     "ds_motivo_cancelamento"])


def _form_gerar_cupom(recontato):
    """Gera o cupom (function) para 1 cliente da lista que ainda não tem cupom. A validade de 48h conta a partir do clique."""
    sem_cupom = recontato[recontato["cupom"].str.startswith("—")] if not recontato.empty else recontato
    if not sem_cupom.empty:
        # O Robson pode registrar uma negativa na Resolução da própria repescagem ("Sem interesse", "WhatsApp inválido", "Não retomar contato")
        # ANTES de gerar o cupom: decidiu não gerar. Depois de salvar, o cliente sai da lista de geração (a Resolução fica gravada em
        # sac_tarefas com a chave = referência do recontato). O restante da tabela segue como está.
        resolucao = carregar_tarefas(TIPO_RECONTATO).set_index("chave")["ds_resultado"] if not sem_cupom.empty else pd.Series(dtype=str)
        negativa = sem_cupom["chave"].map(lambda c: resolucao.get(c) in msg.RESOLUCOES_SEM_CUPOM)
        sem_cupom = sem_cupom[~negativa]
    if sem_cupom.empty:
        return
    rotulos = {r["referencia"]: f"{r['nm_cliente']} — {r['origem_txt']} — {brl(r['vl_total'])}" for _, r in sem_cupom.iterrows()}
    with st.container(border=True):
        st.markdown("**Gerar cupom SEGUNDACHANCE (20% · uso único · 48 horas)**")
        st.caption("O prazo de 48h começa quando você clicar. Gere só na hora de mandar a mensagem; depois o link \"Mensagem\" da tabela já traz o código.")
        col1, col2 = st.columns([3, 1], vertical_alignment="bottom")
        ref = col1.selectbox("Cliente", list(rotulos), format_func=rotulos.get, key="cupom_cliente")
        if col2.button("Gerar cupom", type="primary", key="cupom_gerar"):
            try:
                with st.spinner("Criando o cupom na Nuvemshop..."):
                    cup.gerar_cupom(cup.CAMPANHA_RECUPERACAO_WHATSAPP, ref)
            except Exception as e:
                st.error(str(e))
            else:
                st.rerun()


def _form_gerar_cupons_recompra(lista):
    """Gera o crédito de retorno (function) para os clientes do piloto que ainda não têm cupom. A validade (21 dias) conta a partir
    do clique: gere na hora de mandar as mensagens. Repetir o clique não duplica (1 cupom por cliente)."""
    pendentes = lista[lista["cupom"].str.startswith("—")] if not lista.empty else lista
    if pendentes.empty:
        return
    with st.container(border=True):
        st.markdown(f"**Gerar o crédito de retorno ({brl(20)} · uso único · 21 dias · mínimo {brl(cup.MIN_COMPRA_RETORNO)})**")
        st.caption("A validade de 21 dias começa quando você clicar. Gere só na hora de mandar as mensagens; depois o link \"Mensagem\" "
                   "da tabela já traz o código e o link que aplica o crédito no carrinho.")
        lote = pendentes.head(LIMITE_CUPONS_POR_CLIQUE)
        if st.button(f"Gerar cupons pendentes ({len(lote)})", type="primary", key="cupom_recompra_gerar"):
            erros = []
            with st.spinner("Criando os cupons na Nuvemshop..."):
                for ref in lote["chave"]:
                    try:
                        cup.gerar_cupom(cup.CAMPANHA_RETORNO_PERDIDO, ref, solicitante="sac_recompra_piloto")
                    except Exception as e:
                        erros.append(f"{ref}: {e}")
            if erros:
                st.error("Alguns cupons não foram criados:\n\n" + "\n".join(erros[:5]))
            else:
                st.rerun()


def render():
    inject_css()
    try:
        fr = carregar_frescor(TABELAS, EXTRATORES)
    except Exception:
        fr = None
    st.html(f"""
    <div class="report-header">
      <div>
        <div class="report-brand">shibari brasil · camada diária</div>
        <div class="report-title">SAC <span>—</span> Tarefas do Dia</div>
        <div class="report-meta">Lista de trabalho com mensagem pronta e check de execução — o que fica marcado (ou sem marcar) permanece entre as atualizações de dados</div>
      </div>
      {badge_atualizacao(fr) if fr else ""}
    </div>
    """)
    if fr:
        alerta_atraso(fr)

    with st.spinner("Carregando dados..."):
        try:
            logi = carregar_logistica()
            car = carregar_carrinhos()
            canc = carregar_cancelados()
        except Exception as e:
            st.error(f"Erro ao carregar dados do BigQuery: {e}")
            return

    aviso_link = ("O link \"Mensagem\" abre a conversa no WhatsApp com o texto já escrito para aquela situação — <b>revise antes "
                  "de enviar</b>; nada sai sozinho. Registre a <b>Resolução</b> e, se quiser, a <b>Observação SAC</b> (pode trocar depois), marque \"Já tratei\" e clique em <b>Salvar alterações</b>.")
    valor = st.column_config.NumberColumn(format="R$ %.2f")

    lst_car, lst_canc, lst_ent = _lista_carrinhos(car), _lista_cancelados(canc), _lista_entregas_problema(logi)
    recontato = _lista_recontato(carregar_recontato_seguro(), cup.carregar_cupons(cup.CAMPANHA_RECUPERACAO_WHATSAPP))
    df_prox = carregar_proximidade_seguro()
    lst_prox = _lista_proximidade(df_prox) if df_prox is not None else None

    _faixa_grupo("recuperacao", _pendentes(TIPO_CARRINHO, lst_car) + _pendentes(TIPO_CANCELADO, lst_canc) + _pendentes(TIPO_RECONTATO, recontato))
    _secao_checklist(
        "Carrinhos abandonados — recuperar a venda",
        TIPO_CARRINHO, lst_car,
        colunas={
            "Cliente": "nm_cliente", "Valor": "vl_total_carrinho", "Já é cliente?": "cliente_antigo",
            "Abandonado há": "abandonado_ha", "WhatsApp": "whatsapp", JA_TRATEI: None, RESOLUCAO: None, OBS_SAC: None, "Obs.": "obs",
        },
        column_config={"WhatsApp": COL_WHATSAPP, "Valor": valor, "Obs.": COL_OBS},
        cards_extra=lambda pend: [card("Valor em carrinhos pendentes", brl(pend["vl_total_carrinho"].sum()), "soma dos carrinhos sem check")],
        nota=f"Carrinhos da Nuvemshop dos últimos {JANELA_CARRINHO} dias que não viraram pedido (o mesmo e-mail não comprou depois), sem "
             "contatos de teste e com valor acima de zero. Do mais recente para o mais antigo. Mensagens iguais às da antiga rotina de "
             "recuperação: uma para quem já é cliente, outra para cliente novo, as duas com o link que reabre o carrinho preenchido. "
             "Mesmo telefone em dois carrinhos = uma mensagem só (ver Obs.). A lista atualiza de hora em hora (extração da Nuvemshop + "
             "dbt); quando o cliente compra, o carrinho sai sozinho. " + aviso_link,
    )

    _secao_checklist(
        "Pedidos cancelados — entender e recuperar",
        TIPO_CANCELADO, lst_canc,
        colunas={
            "Pedido": "codigo", "Cliente": "nm_cliente", "Valor": "vl_total_pedido", "Já é cliente?": "cliente_antigo",
            "Cancelado em": "dt_cancelamento", "Tipo": "tipo", "WhatsApp": "whatsapp", JA_TRATEI: None, RESOLUCAO: None, OBS_SAC: None, "Obs.": "obs",
        },
        column_config={
            "WhatsApp": COL_WHATSAPP, "Valor": valor, "Obs.": COL_OBS,
            "Cancelado em": st.column_config.DateColumn(format="DD/MM/YYYY"),
        },
        nota=f"Pedidos da Nuvemshop cancelados nos últimos {JANELA_CANCELADO} dias. O tipo vem do motivo gravado na Nuvemshop: "
             "<b>automático</b> = o sistema cancelou porque o pagamento não foi concluído (Pix/boleto expirado) — é a melhor chance de "
             "recuperar a venda; os demais (\"cliente desistiu\", \"sem estoque\", \"outro motivo\") foram cancelados <b>por alguém "
             "da loja</b>, que escolheu o motivo — o cliente não cancela sozinho pela loja virtual. \"Pago, sem estorno\" = cancelado "
             "com o pagamento ainda como pago na Nuvemshop: confira se o dinheiro foi devolvido antes de chamar (aparece no topo, com o "
             "aviso em Obs.). Sai da lista quem já voltou a comprar (exceto estorno a conferir). " + aviso_link,
    )

    _secao_checklist(
        "Recontato com cupom — última tentativa",
        TIPO_RECONTATO, recontato,
        colunas={
            "Cliente": "nm_cliente", "Origem": "origem_txt", "Valor": "vl_total", "1º contato em": "contato_em",
            "Resolução do 1º contato": "ds_resolucao_contato", "Cupom": "cupom", "WhatsApp": "whatsapp",
            JA_TRATEI: None, RESOLUCAO: None, OBS_SAC: None, "Obs.": "obs",
        },
        column_config={
            "WhatsApp": COL_WHATSAPP, "Valor": valor, "Obs.": COL_OBS,
            "1º contato em": st.column_config.DateColumn(format="DD/MM/YYYY"),
        },
        nota=f"Última tentativa de vender para quem o SAC já contatou (carrinho abandonado ou pedido cancelado). Entra aqui quem teve o "
             f"1º contato marcado como tratado há <b>{DIAS_RECONTATO} dias ou mais</b>, <b>não comprou</b> desde então e cuja Resolução do "
             "1º contato não impede nova abordagem (fora: \"Não retomar contato\", \"WhatsApp inválido\", \"Comprou\", \"Refez o "
             "pedido\", estorno). Cada cliente aparece uma vez. O cupom <b>SEGUNDACHANCE</b> (20%, uso único, <b>48 horas</b>) só é "
             "criado quando você clica em \"Gerar cupom\" abaixo — a validade começa nesse momento, então gere na hora de enviar. "
             "Depois de gerado, o link \"Mensagem\" já leva o código e o prazo. " + aviso_link,
    )
    _form_gerar_cupom(recontato)

    _faixa_grupo("problemas", _pendentes(TIPO_ENTREGA_PROBLEMA, lst_ent))
    _secao_checklist(
        "Entregas com problema — falar com o cliente",
        TIPO_ENTREGA_PROBLEMA, lst_ent,
        colunas={
            "Pedido": "codigo", "Cliente": "nm_cliente", "Motivo": "motivo", "Rastreio": "cd_rastreio",
            "Dias sem evento": "qt_dias_sem_movimento", "WhatsApp": "whatsapp", JA_TRATEI: None, RESOLUCAO: None, OBS_SAC: None,
        },
        column_config={"WhatsApp": COL_WHATSAPP},
        nota="Mesmo critério do Pulso do Dia (\"Entregas em risco\"): pedido atrasado, com problema de entrega ativo (devolução/tentativa "
             f"falha) ou parado {DIAS_PARADO}+ dias sem nenhum evento de rastreio. A mensagem muda conforme o motivo (problema de entrega "
             "tem prioridade sobre atraso, que tem prioridade sobre parado). " + aviso_link + " A marcação fica salva à parte dos dados e "
             "não some quando a base atualizar de novo; se o pedido sair da situação de risco (por exemplo, foi entregue), ele some da "
             "lista, mas o registro de que você tratou continua guardado.",
    )

    if lst_prox is not None:
        _faixa_grupo("proximidade", _pendentes(TIPO_PROXIMIDADE, lst_prox))
        _secao_checklist(
            "Pós-entrega — como foi a experiência?",
            TIPO_PROXIMIDADE, lst_prox,
            colunas={
                "Cliente": "nm_cliente", "Pedido": "codigo", "Compra": "compra_txt", "Produto": "nm_produto_principal",
                "Valor": "vl_total_pedido", "Entregue em": "dt_entrega", "Na lista desde": "na_lista_desde", "Atendido em": "atendido_em",
                "WhatsApp": "whatsapp",
                JA_TRATEI: None, RESOLUCAO: None, OBS_SAC: None, "Obs.": "obs",
            },
            column_config={
                "WhatsApp": COL_WHATSAPP, "Valor": valor, "Obs.": COL_OBS,
                "Entregue em": st.column_config.DateColumn(format="DD/MM/YYYY"),
                "Na lista desde": st.column_config.DateColumn(format="DD/MM/YYYY"),
                "Atendido em": st.column_config.DateColumn(format="DD/MM/YYYY"),
            },
            nota="Contato amistoso, sem venda e sem cupom: pergunta como foi a experiência, se o produto cumpriu o que o cliente esperava "
                 "e se ele tem algum feedback. Entra quem fez a <b>2ª compra ou mais</b> e teve a entrega confirmada, "
                 "a partir do dia seguinte à entrega (\"Na lista desde\"). <b>O registro não some por tempo</b>: continua aqui até você "
                 "tratar (fim de semana e folga não fazem o contato desaparecer); ao marcar \"Já tratei\" fica registrada a data em "
                 "\"Atendido em\" (ative \"Mostrar também os já tratados\" para ver o histórico). O mais antigo aparece primeiro. "
                 "Cada cliente recebe esse contato <b>uma vez só</b>: se já foi contatado em outro pedido, ou tem \"Não retomar contato\" "
                 "em qualquer lista do SAC, não aparece. Entrega que atrasou leva uma mensagem que reconhece o atraso (ver Obs.). "
                 "O que o cliente responder vai na <b>Observação SAC</b>; se for reclamação, escolha \"Reclamação — abrir tratativa\". "
                 + aviso_link,
        )

    _completar_lote_seguro()
    df_rec = carregar_recompra_piloto_seguro()
    if df_rec is not None and not df_rec.empty:
        lst_rec = _lista_recompra_piloto(df_rec, cup.carregar_cupons(cup.CAMPANHA_RETORNO_PERDIDO))
        _faixa_grupo("recompra", _pendentes(TIPO_RECOMPRA, lst_rec))
        _secao_checklist(
            "Último contato — voltar a explorar (piloto)",
            TIPO_RECOMPRA, lst_rec,
            colunas={
                "Cliente": "nm_cliente", "Última compra": "dt_ultima_compra", "Pedidos": "pedidos_txt", "Total gasto": "vl_total_gasto",
                "Cupom": "cupom", "WhatsApp": "whatsapp", JA_TRATEI: None, RESOLUCAO: None, OBS_SAC: None, "Obs.": "obs",
            },
            column_config={
                "WhatsApp": COL_WHATSAPP, "Total gasto": valor, "Obs.": COL_OBS,
                "Última compra": st.column_config.DateColumn(format="DD/MM/YYYY"),
            },
            nota="Piloto do Ecossistema de Pós-Venda: contato humano para quem não compra há mais de 1 ano e <b>já aceitou receber "
                 "mensagens</b> (consentimento conferido na Nuvemshop). <b>Todo dia</b>, na 1ª abertura da página, a lista é completada até "
                 f"<b>{LOTE_DIARIO_RECOMPRA} clientes sem contato registrado</b>: quem sobrou de ontem continua aqui e só entra a diferença. Cada cliente leva um <b>crédito de retorno pessoal</b> (uso único, 21 dias, com valor mínimo de compra): clique em "
                 "\"Gerar cupons pendentes\" e depois use o link \"Mensagem\" de cada linha — o crédito já entra aplicado no carrinho. "
                 "Quem responder <b>SAIR</b> ou pedir para não receber: marque a Resolução \"Não retomar contato\" (vale para todas as listas). "
                 "Quem comprar (com o cupom ou não) sai sozinho da lista. <b>Pare e avise o Hugo</b> se mais de 3% pedirem para sair, se o "
                 "link do crédito não funcionar ou se o WhatsApp mostrar qualquer aviso. " + aviso_link,
        )
        _form_gerar_cupons_recompra(lst_rec)

    if fr:
        detalhe_atualizacao(fr)
