"""Lista da fila do fluxo de pós-venda no SAC (B065; revisão de 14/10/2026): quem aparece, cupom, texto e link do WhatsApp. Dados sintéticos, sem BigQuery."""
from datetime import datetime
from urllib.parse import unquote

import pandas as pd

from reports import sac

AGORA = datetime(2026, 10, 20, 10, 0)
BASE = dict(dt_lote=pd.Timestamp("2026-10-19"), dt_criacao=pd.Timestamp("2026-10-19 08:00"), email="a@x.com", nr_telefone="+5521999990000",
            dt_ultima_compra=pd.Timestamp("2026-10-01"), vl_total_gasto=200.0, qt_pedidos=1, nm_produto="Corda de juta", itens_pedido="Corda de juta",
            vl_pedido_base=228.0, ds_faixa_valor="B", nr_pedido=1, dt_pedido=pd.Timestamp("2026-10-18"), dt_entrega=pd.NaT, dt_limite=pd.NaT,
            fg_atrasada=False, qt_validade_cashback_dias=45, qt_meta_diaria=35, fg_substituiu=False,
            fg_nao_retomar=False, fg_comprou_depois=False, fg_comprou_com_cupom=False, fg_na_fila=True)
VAZIO = pd.DataFrame(columns=["referencia", "codigo", "valor", "valido_de", "expira_em"])


def _df():
    return pd.DataFrame([
        {**BASE, "ds_campanha": "jornada_w0", "chave": "1|11", "nm_cliente": "Ana Souza"},
        {**BASE, "ds_campanha": "jornada_w0", "chave": "2|22", "nm_cliente": "Bia", "fg_nao_retomar": True},
        {**BASE, "ds_campanha": "jornada_w0", "chave": "3|33", "nm_cliente": "Caio", "fg_comprou_depois": True},
        {**BASE, "ds_campanha": "jornada_w0", "chave": "4|44", "nm_cliente": "Davi", "fg_na_fila": False},
        {**BASE, "ds_campanha": "jornada_w0", "chave": "5|55", "nm_cliente": "Edu", "fg_na_fila": False},
        {**BASE, "ds_campanha": "jornada_w0", "chave": "6|66", "nm_cliente": "Fabi", "dt_pedido": pd.Timestamp("2026-10-10"), "fg_atrasada": True},
        {**BASE, "ds_campanha": "esgotamento_dormente", "chave": "7", "nm_cliente": "Gil", "fg_na_fila": False, "nr_pedido": float("nan"), "dt_pedido": pd.NaT},
    ])


def _cupons():
    return pd.DataFrame([
        {"referencia": "1|11", "codigo": "CASHBACKPOSCOMPRAAB12", "valor": 22.8, "valido_de": AGORA, "expira_em": datetime(2026, 12, 4)},
        {"referencia": "5|55", "codigo": "CASHBACKPOSCOMPRAXX99", "valor": 22.8, "valido_de": AGORA, "expira_em": datetime(2026, 10, 1)},
    ])


def _texto(lista, chave):
    return unquote(lista.set_index("chave").loc[chave, "whatsapp"])


def test_quem_aparece_na_w0_e_a_ordem():
    w0 = sac._lista_fila(_df(), "jornada_w0", _cupons(), feitos={"5|55"}, agora=AGORA)
    # fora: "Não retomar contato" (2), comprou de novo (3), item que saiu da fila e não foi tratado (4); fica o já tratado (5); o pedido mais antigo vem primeiro
    assert list(w0["chave"]) == ["6|66", "1|11", "5|55"]


def test_w0_cupom_link_e_aviso_de_atraso():
    w0 = sac._lista_fila(_df(), "jornada_w0", _cupons(), feitos={"5|55"}, agora=AGORA).set_index("chave")
    assert w0.loc["1|11", "cupom"].startswith("CASHBACKPOSCOMPRAAB12 — vence 04/12")
    link = w0.loc["1|11", "whatsapp"]
    assert link.startswith("https://wa.me/5521999990000?text=") and "%0A%0A" in link and "CASHBACKPOSCOMPRAAB12" in link and "SAIR" in link
    assert w0.loc["5|55", "cupom"].endswith("expirado") and pd.isna(w0.loc["5|55", "whatsapp"])  # cupom vencido: sem link de mensagem
    assert w0.loc["6|66", "cupom"].startswith("— (gerar") and "W0 atrasada" in w0.loc["6|66", "obs"]
    assert w0.loc["6|66", "cashback_previsto"] == 22.8 and w0.loc["1|11", "pedido_nr_txt"] == "1º"


def test_w0_do_1o_pedido_e_boas_vindas_com_10_por_cento():
    t = _texto(sac._lista_fila(_df(), "jornada_w0", _cupons(), feitos=set(), agora=AGORA), "1|11")
    assert "Obrigado pela sua compra!" in t and "*cashback de R$ 22,80*" in t and "(10% do seu pedido)" in t
    assert "Vale *até 04/12*" in t and "Quando o seu pedido chegar" in t and "substitui" not in t


def test_w0_do_2o_e_3o_pedido_e_avisa_a_substituicao():
    df = _df()
    df.loc[df["chave"] == "1|11", ["nr_pedido", "fg_substituiu"]] = [2, True]
    t2 = _texto(sac._lista_fila(df, "jornada_w0", _cupons(), feitos=set(), agora=AGORA), "1|11")
    assert "Que bom ver você por aqui de novo" in t2 and "um novo *cashback" in t2 and "Ele substitui o cashback anterior" in t2
    df.loc[df["chave"] == "1|11", ["nr_pedido", "fg_substituiu"]] = [3, False]
    t3 = _texto(sac._lista_fila(df, "jornada_w0", _cupons(), feitos=set(), agora=AGORA), "1|11")
    assert "Como você já é de casa, deixei um prazo maior: vale *até 04/12*" in t3 and "substitui" not in t3


def test_w0_com_pedido_ja_entregue_vira_a_mensagem_combinada():
    df = _df()
    df.loc[df["chave"] == "1|11", "dt_entrega"] = pd.Timestamp("2026-10-19")
    t = _texto(sac._lista_fila(df, "jornada_w0", _cupons(), feitos=set(), agora=AGORA), "1|11")
    assert "Vi que o seu pedido chegou" in t and "*cashback de R$ 22,80*" in t and "Obrigado pela sua compra" not in t


def test_w0_sem_percentual_quando_piso_ou_teto():
    df = _df()
    df.loc[df["chave"] == "1|11", "vl_pedido_base"] = 30.0
    cupons = pd.DataFrame([{"referencia": "1|11", "codigo": "CASHBACKPOSCOMPRAAB12", "valor": 5.0, "valido_de": AGORA, "expira_em": datetime(2026, 12, 4)}])
    t = _texto(sac._lista_fila(df, "jornada_w0", cupons, feitos=set(), agora=AGORA), "1|11")
    assert "*cashback de R$ 5,00*" in t and "10%" not in t


def test_w1_check_in_com_lembrete_e_sem_lembrete_no_4o_pedido():
    base = _df().assign(ds_campanha="jornada_w1")
    t = _texto(sac._lista_fila(base, "jornada_w1", _cupons(), feitos=set(), agora=AGORA), "1|11")
    assert "A corda *era como você esperava?*" in t and "E só para lembrar: o seu *cashback de R$ 22,80* continua valendo *até 04/12*" in t
    assert "CASHBACKPOSCOMPRAAB12" in t and "Corda de juta" not in t
    base.loc[base["chave"] == "1|11", "nr_pedido"] = 4
    t4 = _texto(sac._lista_fila(base, "jornada_w1", _cupons(), feitos=set(), agora=AGORA), "1|11")
    assert "E só para lembrar" not in t4 and "*era como você esperava?*" in t4


def test_w2_nao_gera_cupom_e_exige_o_da_w0():
    w2 = sac._lista_fila(_df().assign(ds_campanha="jornada_w2"), "jornada_w2", _cupons(), feitos=set(), agora=AGORA).set_index("chave")
    assert w2.loc["6|66", "cupom"].startswith("— (cashback da W0 n")
    assert w2.loc["1|11", "cupom"].startswith("CASHBACKPOSCOMPRAAB12")
    assert "continua valendo *até 04/12*" in unquote(w2.loc["1|11", "whatsapp"])


def test_lembrete_final_diz_quantos_dias_faltam():
    f = sac._lista_fila(_df().assign(ds_campanha="jornada_w2_final"), "jornada_w2_final", _cupons(), feitos=set(), agora=datetime(2026, 11, 27, 10, 0))
    t = _texto(f, "1|11")
    assert "vence em *7 dias*, no dia *04/12*" in t


def test_esgotamento_nao_depende_de_estar_na_fila_do_dbt():
    esg = sac._lista_fila(_df(), "esgotamento_dormente", VAZIO, feitos=set(), agora=AGORA)
    assert list(esg["chave"]) == ["7"]


def test_sql_da_jornada_nao_tem_limite_e_o_esgotamento_segue_a_meta_diaria():
    j, e = sac._sql_liberar_jornada(), sac._sql_completar_esgotamento()
    assert "ds_chave" in j and "QUALIFY" not in j
    assert "GREATEST(0, 10 - p.n)" in e and "nr_posicao_esgotamento" in e and "STARTS_WITH(ds_campanha, 'jornada')" in e
    assert "QUALIFY ROW_NUMBER()" in e and "l2.dt_lote = CURRENT_DATE" in e and "qt_limite_esgotamento_dia" not in e


def test_backlog_puxa_na_ordem_de_prioridade_e_limita_a_quantidade():
    sql = sac._sql_puxar_backlog(10)
    assert "nr_posicao_esgotamento) <= 10" in sql and "NOT EXISTS" in sql
    assert "<= 50" in sac._sql_puxar_backlog(500) and "<= 1" in sac._sql_puxar_backlog(0)


def test_cashback_previsto_e_10_por_cento_do_pedido_com_piso_e_teto():
    from common import cupom as cup
    assert cup.valor_cashback(228.0) == 22.8
    assert cup.valor_cashback(144.99) == 14.49          # arredonda PARA BAIXO em centavos (nunca passa de 10%)
    assert cup.valor_cashback(30) == 5.0                # piso
    assert cup.valor_cashback(900) == 50.0              # teto
    assert cup.valor_cashback(None) is None and cup.valor_cashback(0) is None and cup.valor_cashback(float("nan")) is None


def test_percentual_so_aparece_quando_e_verdade():
    from common import cupom as cup
    assert cup.cashback_e_10_por_cento(22.8, 228.0)
    assert not cup.cashback_e_10_por_cento(5.0, 30.0)     # piso aplicado: não é 10%
    assert not cup.cashback_e_10_por_cento(50.0, 900.0)   # teto aplicado
    assert not cup.cashback_e_10_por_cento(20.0, 228.0)   # valor diferente do calculado


def test_previa_mostra_mensagem_com_cupom_de_mentira_e_nao_grava():
    """Pré-visualização (fila desligada): item sem cupom ganha link de mensagem com código PREVIA e a célula do cupom continua "gerar abaixo"."""
    df = _df().assign(ds_campanha="jornada_w0")
    w0 = sac._lista_fila(df, "jornada_w0", VAZIO, feitos=set(), agora=AGORA, previa=True).set_index("chave")
    assert w0.loc["1|11", "cupom"].startswith("— (gerar") and "prévia" in w0.loc["1|11", "cupom"]
    t = unquote(w0.loc["1|11", "whatsapp"])
    assert "PREVIA" in t and "*cashback de R$ 22,80*" in t
    normal = sac._lista_fila(df, "jornada_w0", VAZIO, feitos=set(), agora=AGORA).set_index("chave")
    assert pd.isna(normal.loc["1|11", "whatsapp"])          # fora da prévia continua sem link até gerar o cupom de verdade


def test_esgotamento_mostra_10_por_padrao_e_a_previa_corta_em_10():
    assert sac.ESGOTAMENTO_PADRAO == 10 and sac.PASSO_PUXAR == 10
    assert "SELECT 10 AS n" in sac._sql_corte_esgotamento()


def test_grupos_na_ordem_do_hugo_e_perdidos_fora_da_pagina():
    import inspect
    src = inspect.getsource(sac.render)
    ordem = [src.index(x) for x in ("_render_posvenda(", 'key="grupo_recuperacao"', 'key="grupo_problemas"', "_render_esgotamento(")]
    assert ordem == sorted(ordem)
    assert "carregar_recompra_piloto_seguro" not in src and "_completar_lote_seguro()" not in src.replace("_completar_lote_fila_seguro()", "")
