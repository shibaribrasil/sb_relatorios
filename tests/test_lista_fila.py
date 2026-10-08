"""Lista da fila do fluxo de pós-venda no SAC (B065 etapa 4): quem aparece, cupom e link do WhatsApp. Dados sintéticos, sem BigQuery."""
from datetime import datetime

import pandas as pd

from reports import sac

AGORA = datetime(2026, 10, 20, 10, 0)
BASE = dict(dt_lote=pd.Timestamp("2026-10-19"), dt_criacao=pd.Timestamp("2026-10-19 08:00"), email="a@x.com", nr_telefone="+5521999990000",
            dt_ultima_compra=pd.Timestamp("2026-10-01"), vl_total_gasto=200.0, qt_pedidos=1, nm_produto="Corda de juta", ds_faixa_valor="B",
            fg_nao_retomar=False, fg_comprou_depois=False, fg_comprou_com_cupom=False, fg_na_fila=True)
VAZIO = pd.DataFrame(columns=["referencia", "codigo", "valor", "valido_de", "expira_em"])


def _df():
    return pd.DataFrame([
        {**BASE, "ds_campanha": "jornada_w1", "chave": "1", "nm_cliente": "Ana Souza"},
        {**BASE, "ds_campanha": "jornada_w1", "chave": "2", "nm_cliente": "Bia", "fg_nao_retomar": True},
        {**BASE, "ds_campanha": "jornada_w1", "chave": "3", "nm_cliente": "Caio", "fg_comprou_depois": True},
        {**BASE, "ds_campanha": "jornada_w1", "chave": "4", "nm_cliente": "Davi", "fg_na_fila": False},
        {**BASE, "ds_campanha": "jornada_w1", "chave": "5", "nm_cliente": "Edu", "fg_na_fila": False},
        {**BASE, "ds_campanha": "jornada_w1", "chave": "6", "nm_cliente": "Fabi"},
        {**BASE, "ds_campanha": "esgotamento_dormente", "chave": "7", "nm_cliente": "Gil", "fg_na_fila": False},
    ])


def _cupons():
    return pd.DataFrame([
        {"referencia": "1", "codigo": "CASHBACKPOSCOMPRAAB12", "valor": 20.0, "valido_de": AGORA, "expira_em": datetime(2026, 11, 18)},
        {"referencia": "5", "codigo": "CASHBACKPOSCOMPRAXX99", "valor": 20.0, "valido_de": AGORA, "expira_em": datetime(2026, 10, 1)},
    ])


def test_quem_aparece_na_w1():
    w1 = sac._lista_fila(_df(), "jornada_w1", _cupons(), feitos={"5"}, agora=AGORA)
    # fora: "Não retomar contato" (2), comprou depois (3), W1 envelhecida e não tratada (4); fica a envelhecida já tratada (5)
    assert list(w1["chave"]) == ["1", "5", "6"]


def test_cupom_e_link_do_whatsapp():
    w1 = sac._lista_fila(_df(), "jornada_w1", _cupons(), feitos={"5"}, agora=AGORA).set_index("chave")
    assert w1.loc["1", "cupom"].startswith("CASHBACKPOSCOMPRAAB12 — vence 18/11")
    link = w1.loc["1", "whatsapp"]
    assert link.startswith("https://wa.me/5521999990000?text=") and "%0A%0A" in link and "CASHBACKPOSCOMPRAAB12" in link and "SAIR" in link
    assert w1.loc["5", "cupom"].endswith("expirado") and pd.isna(w1.loc["5", "whatsapp"])  # cupom vencido: sem link de mensagem
    assert w1.loc["6", "cupom"].startswith("— (gerar")                                      # ainda sem cupom


def test_w2_nao_gera_cupom_e_exige_o_da_w1():
    w2 = sac._lista_fila(_df().assign(ds_campanha="jornada_w2"), "jornada_w2", _cupons(), feitos=set(), agora=AGORA).set_index("chave")
    assert w2.loc["6", "cupom"].startswith("— (cashback da W1 n")
    assert w2.loc["1", "cupom"].startswith("CASHBACKPOSCOMPRAAB12")


def test_esgotamento_nao_depende_de_estar_na_fila_do_dbt():
    esg = sac._lista_fila(_df(), "esgotamento_dormente", VAZIO, feitos=set(), agora=AGORA)
    assert list(esg["chave"]) == ["7"]


def test_sql_do_lote_nao_tem_limite_na_jornada_e_tem_no_esgotamento():
    j, e = sac._sql_liberar_jornada(), sac._sql_completar_esgotamento()
    assert "qt_limite_esgotamento_dia" not in j and "QUALIFY" not in j
    assert "qt_limite_esgotamento_dia" in e and "QUALIFY ROW_NUMBER()" in e and "l2.dt_lote = CURRENT_DATE" in e
