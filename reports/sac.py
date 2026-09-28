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
from common.tarefas import carregar_tarefas, salvar_tarefas
from common.frescor import carregar_frescor, badge_atualizacao, detalhe_atualizacao, alerta_atraso

TABELAS = ("tb_logistica_pedido", "tb_carrinho_abandonado", "tb_pedido_cancelado")
EXTRATORES = ("nuvemshop_orders", "nuvemshop_fulfillments", "nuvemshop_customers", "bling_orders")

TIPO_ENTREGA_PROBLEMA = "entrega_problema"
TIPO_CARRINHO = "carrinho_abandonado"
TIPO_CANCELADO = "pedido_cancelado"
DIAS_PARADO = 10        # mesmo limite usado no Pulso do Dia (decisão de apresentação)
JANELA_CARRINHO = 15    # dias: carrinho mais velho que isso não vale mais contato
JANELA_CANCELADO = 30   # dias desde o cancelamento
FUSO = "America/Sao_Paulo"

JA_TRATEI, RESOLUCAO = "Já tratei", "Resolução"  # colunas editáveis; entram em `colunas` na posição desejada
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


# --- Seção genérica com check persistente ---------------------------------------------------------------------

def _secao_checklist(titulo, tipo_tarefa, itens, colunas, nota, column_config=None, cards_extra=None):
    """`itens` já vem com uma coluna `chave` (str). `colunas` = dict nome exibido → coluna em `itens`, NA ORDEM de exibição;
    as colunas editáveis entram com as chaves JA_TRATEI e RESOLUCAO (valor None) onde devem aparecer. "Resolução" = opções
    de msg.RESULTADOS[tipo_tarefa] (gravada em sac_tarefas.ds_resultado); ao mudar ela ou o check, grava o estado da linha
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
    pendentes, concluidos = int((~feito_atual).sum()), int(feito_atual.sum())
    render_cards([
        card("Pendentes", f"{pendentes}", "ainda sem contato registrado", variant="bad" if pendentes else "ok"),
        card("Já tratados", f"{concluidos}", "marcados nesta lista"),
        *(cards_extra(itens[~feito_atual.values]) if cards_extra else []),
    ])
    mostrar_feitos = st.toggle("Mostrar também os já tratados", value=False, key=f"toggle_{tipo_tarefa}")
    base = itens.assign(**{JA_TRATEI: feito_atual.values, RESOLUCAO: resultado_atual.values})
    if not mostrar_feitos:
        base = base[~base[JA_TRATEI]]
    if base.empty:
        note("Tudo tratado por aqui. Ative \"Mostrar também os já tratados\" para conferir ou mudar uma resolução.")
        return
    tabela = pd.DataFrame({nome: base[nome if nome in (JA_TRATEI, RESOLUCAO) else coluna].values for nome, coluna in colunas.items()})
    chaves = base["chave"].to_numpy()  # fora da tabela exibida — usada só para gravar a mudança na chave certa
    # o editor guarda as trocas na tela; só vão para o BigQuery no botão "Salvar alterações" (versão na key = zera o editor após salvar)
    versao = st.session_state.get(f"versao_{tipo_tarefa}", 0)
    editado = st.data_editor(
        tabela, hide_index=True, use_container_width=True, key=f"editor_{tipo_tarefa}_{versao}",
        disabled=[c for c in tabela.columns if c not in (JA_TRATEI, RESOLUCAO)],
        column_config={
            **(column_config or {}),
            RESOLUCAO: st.column_config.SelectboxColumn(options=opcoes, width="medium", required=False,
                                                        help="Como terminou o contato. Pode trocar depois quantas vezes precisar."),
            JA_TRATEI: st.column_config.CheckboxColumn(width=90),
        },
    )
    # comparação por posição (não por índice): data_editor mantém a ordem das linhas, não reordena/filtra sozinho
    def _norm(serie):
        return serie.astype(object).where(serie.notna(), None).to_numpy()
    mudou = (editado[JA_TRATEI].to_numpy() != tabela[JA_TRATEI].to_numpy()) | (_norm(editado[RESOLUCAO]) != _norm(tabela[RESOLUCAO]))
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
                (chave, bool(feito), resultado)
                for chave, feito, resultado in zip(chaves[mudou], editado[JA_TRATEI].to_numpy()[mudou], _norm(editado[RESOLUCAO])[mudou])
            ])
        st.session_state[f"versao_{tipo_tarefa}"] = versao + 1
        st.rerun()
    note(nota)


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
                  "de enviar</b>; nada sai sozinho. Registre a <b>Resolução</b> (pode trocar depois) e marque \"Já tratei\".")
    valor = st.column_config.NumberColumn(format="R$ %.2f")

    _secao_checklist(
        "Carrinhos abandonados — recuperar a venda",
        TIPO_CARRINHO, _lista_carrinhos(car),
        colunas={
            "Cliente": "nm_cliente", "Valor": "vl_total_carrinho", "Já é cliente?": "cliente_antigo",
            "Abandonado há": "abandonado_ha", "WhatsApp": "whatsapp", JA_TRATEI: None, RESOLUCAO: None, "Obs.": "obs",
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
        TIPO_CANCELADO, _lista_cancelados(canc),
        colunas={
            "Pedido": "codigo", "Cliente": "nm_cliente", "Valor": "vl_total_pedido", "Já é cliente?": "cliente_antigo",
            "Cancelado em": "dt_cancelamento", "Tipo": "tipo", "WhatsApp": "whatsapp", JA_TRATEI: None, RESOLUCAO: None, "Obs.": "obs",
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
        "Entregas com problema — falar com o cliente",
        TIPO_ENTREGA_PROBLEMA, _lista_entregas_problema(logi),
        colunas={
            "Pedido": "codigo", "Cliente": "nm_cliente", "Motivo": "motivo", "Rastreio": "cd_rastreio",
            "Dias sem evento": "qt_dias_sem_movimento", "WhatsApp": "whatsapp", JA_TRATEI: None, RESOLUCAO: None,
        },
        column_config={"WhatsApp": COL_WHATSAPP},
        nota="Mesmo critério do Pulso do Dia (\"Entregas em risco\"): pedido atrasado, com problema de entrega ativo (devolução/tentativa "
             f"falha) ou parado {DIAS_PARADO}+ dias sem nenhum evento de rastreio. A mensagem muda conforme o motivo (problema de entrega "
             "tem prioridade sobre atraso, que tem prioridade sobre parado). " + aviso_link + " A marcação fica salva à parte dos dados e "
             "não some quando a base atualizar de novo; se o pedido sair da situação de risco (por exemplo, foi entregue), ele some da "
             "lista, mas o registro de que você tratou continua guardado.",
    )

    if fr:
        detalhe_atualizacao(fr)
