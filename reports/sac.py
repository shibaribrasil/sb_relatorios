"""Relatório SAC — Tarefas do Dia — Shibari Brasil (camada diária).

Regras: ver specs/sac.md. Página de trabalho do SAC (hoje: Robson), não de leitura gerencial — cada lista
tem uma tarefa e um checkbox de "já tratei". O estado do checkbox vem de `raw_control.sac_tarefas`
(common/tarefas.py), uma tabela que o dbt **nunca** recria — o que foi marcado ou deixado sem marcar
continua exatamente assim depois que os dados são atualizados (o item só entra ou sai da lista conforme
a situação real dele mudar; o check em si nunca se perde).
"""
import pandas as pd
import streamlit as st

from common.design import inject_css, section_title, note, card, render_cards
from common.logistica import carregar_logistica
from common.tarefas import carregar_tarefas, marcar_tarefa

TIPO_ENTREGA_PROBLEMA = "entrega_problema"
DIAS_PARADO = 10  # mesmo limite usado no Pulso do Dia (common/logistica não define isso, é decisão de apresentação)


def _lista_entregas_problema(logi):
    em_transito = logi[logi["ds_situacao_logistica"] == "em_transito"]
    parado = em_transito["qt_dias_sem_movimento"] >= DIAS_PARADO
    risco = logi[logi["fg_atrasado_em_aberto"] | logi["fg_problema_entrega_ativo"] | (logi["ds_situacao_logistica"] == "em_transito") & parado].copy()

    def motivo(r):
        m = []
        if r["fg_problema_entrega_ativo"]:
            m.append("problema de entrega")
        if r["fg_atrasado_em_aberto"]:
            m.append("atrasado")
        if r["ds_situacao_logistica"] == "em_transito" and r["qt_dias_sem_movimento"] >= DIAS_PARADO:
            m.append("parado")
        return " + ".join(m)

    risco["codigo"] = risco["cd_pedido_nuvemshop"].where(risco["cd_pedido_nuvemshop"].notna(), "Bling " + risco["cd_pedido"].astype(str))
    risco["motivo"] = risco.apply(motivo, axis=1)
    risco["chave"] = risco["cd_codigo_interno"].astype(str)
    return risco.sort_values("qt_dias_sem_movimento", ascending=False)


def _secao_checklist(titulo, tipo_tarefa, itens, colunas_extra, nota):
    """`itens` já vem com uma coluna `chave` (str) e as colunas citadas em `colunas_extra` (dict nome exibido → coluna
    em `itens`). Renderiza um data_editor com checkbox "Já tratei"; ao mudar, grava no BigQuery e reexecuta."""
    section_title(titulo)
    if itens.empty:
        note(nota + " Nenhum item na lista agora — nada pendente.")
        return
    tarefas = carregar_tarefas(tipo_tarefa).set_index("chave")
    feito_atual = itens["chave"].map(lambda c: bool(tarefas.loc[c, "fg_feito"]) if c in tarefas.index else False)
    pendentes, concluidos = int((~feito_atual).sum()), int(feito_atual.sum())
    render_cards([
        card("Pendentes", f"{pendentes}", "ainda sem contato registrado", variant="bad" if pendentes else "ok"),
        card("Já tratados", f"{concluidos}", "marcados nesta lista"),
    ])
    mostrar_feitos = st.toggle("Mostrar também os já tratados", value=False, key=f"toggle_{tipo_tarefa}")
    base = itens.assign(**{"Já tratei": feito_atual.values})
    if not mostrar_feitos:
        base = base[~base["Já tratei"]]
    if base.empty:
        note("Tudo tratado por aqui. Ative \"Mostrar também os já tratados\" para conferir.")
        return
    tabela = pd.DataFrame({nome: base[coluna].values for nome, coluna in colunas_extra.items()})
    tabela["Já tratei"] = base["Já tratei"].values
    chaves = base["chave"].to_numpy()  # fora da tabela exibida — usada só para gravar a mudança na chave certa
    editado = st.data_editor(
        tabela, hide_index=True, use_container_width=True, key=f"editor_{tipo_tarefa}",
        disabled=[c for c in tabela.columns if c != "Já tratei"],
        column_config={"Já tratei": st.column_config.CheckboxColumn(width=90)},
    )
    # comparação por posição (não por índice): data_editor mantém a ordem das linhas, não reordena/filtra sozinho
    mudou = editado["Já tratei"].to_numpy() != tabela["Já tratei"].to_numpy()
    if mudou.any():
        for chave, novo_valor in zip(chaves[mudou], editado["Já tratei"].to_numpy()[mudou]):
            marcar_tarefa(tipo_tarefa, chave, bool(novo_valor))
        st.rerun()
    note(nota)


def render():
    inject_css()
    st.html("""
    <div class="report-header">
      <div>
        <div class="report-brand">shibari brasil · camada diária</div>
        <div class="report-title">SAC <span>—</span> Tarefas do Dia</div>
        <div class="report-meta">Lista de trabalho com check de execução — o que fica marcado (ou sem marcar) permanece entre as atualizações de dados</div>
      </div>
    </div>
    """)

    with st.spinner("Carregando dados..."):
        try:
            logi = carregar_logistica()
        except Exception as e:
            st.error(f"Erro ao carregar dados do BigQuery: {e}")
            return

    entregas = _lista_entregas_problema(logi)
    _secao_checklist(
        "Entregas com problema — falar com o cliente",
        TIPO_ENTREGA_PROBLEMA, entregas,
        colunas_extra={
            "Pedido": "codigo", "Rastreio": "cd_rastreio", "Cliente": "nm_cliente",
            "Dias sem evento": "qt_dias_sem_movimento", "Motivo": "motivo",
        },
        nota="Mesmo critério do Pulso do Dia (\"Entregas em risco\"): pedido atrasado, com problema de entrega ativo (devolução/tentativa "
             f"falha) ou parado {DIAS_PARADO}+ dias sem nenhum evento de rastreio. Marque \"Já tratei\" depois de contatar o cliente — a "
             "marcação fica salva à parte dos dados e não some quando a base atualizar de novo; se o pedido sair da situação de risco "
             "(por exemplo, foi entregue), ele some da lista, mas o registro de que você tratou continua guardado.",
    )
