"""Relatório Consulta de Pedidos — Shibari Brasil (camada diária).

Um pedido por vez, do carrinho à margem: itens (com foto) → descontos e frete → valor pago → taxa, envio e imposto →
o que entrou na conta → custo e margem de contribuição → origem, custo de aquisição e margem após aquisição.
Regras e limites: specs/consulta-pedido.md.

Regra de negócio vem pronta do dbt (`tb_pedido`, `tb_atribuicao_pedido`, `tb_produto_gestao`, `tb_gads_*`).
Aqui só se filtra, soma e apresenta; a única conta própria é o custo de aquisição estimado (ver `custo_aquisicao`).
"""
import html
import re

import pandas as pd
import streamlit as st

from common import bigquery as bq
from common.design import (
    COLORS, inject_css, section_title, note, brl, pct,
)
from reports.origem_campanha import GOOGLE_PAGO, carregar_campanhas
from reports.produto_desempenho import _chips, _foto, _in
from reports.vendas_margem import _hoje_brt, _variant_margem

AZ = f"{bq.PROJECT}.dbt_dw_az"
INICIO = "2025-08-01"                      # taxa e frete reais da Nuvemshop só são confiáveis desde ago/2025
INICIO_ADS = pd.Timestamp("2026-06-01")    # o histórico de custo por campanha do Google Ads começa aqui
META_PAGO = ("instagram", "paid_social")   # mídia paga sem custo na base (não há dados do Meta Ads)
JANELA_DIAS = 30                           # janela do custo médio por venda do Google Ads
MAX_PEDIDO_CASHBACK = 3                    # espelha o parâmetro max_pedido_cashback da fila de pós-venda (dbt): cashback só até o 3º pedido
MEIOS = {"credit_card": "Cartão de crédito", "pix": "Pix", "boleto": "Boleto", "wallet": "Carteira digital", "other": "Outro meio"}


# ── carga ────────────────────────────────────────────────────────────────────

def _digitos(v):
    """Id numérico vindo do próprio BigQuery; valida antes de entrar em SQL."""
    v = str(v)
    if not re.fullmatch(r"\d+", v):
        raise ValueError(f"id inválido: {v!r}")
    return v


@st.cache_data(ttl=300)
def carregar_lista():
    """1 linha por pedido válido, do mais recente para o mais antigo (seletor)."""
    client = bq.get_client()
    df = bq.query_df(client, f"""
        SELECT CAST(cd_codigo_interno AS STRING) AS cd, ANY_VALUE(CAST(cd_pedido AS STRING)) AS cd_pedido,
               ANY_VALUE(CAST(cd_pedido_nuvemshop AS STRING)) AS cd_nuvemshop, ANY_VALUE(dt_pedido) AS dt_pedido,
               ANY_VALUE(nm_contato) AS nm_contato, SUM(vl_liquido_item) AS vl_liquido
          FROM `{AZ}.tb_pedido` WHERE fg_pedido_valido AND dt_pedido >= DATE '{INICIO}'
      GROUP BY cd_codigo_interno
    """)
    df["dt_pedido"] = pd.to_datetime(df["dt_pedido"])
    df["vl_liquido"] = pd.to_numeric(df["vl_liquido"]).fillna(0.0)
    df["codigo"] = df["cd_nuvemshop"].where(df["cd_nuvemshop"].notna(), "Bling " + df["cd_pedido"].astype(str))
    # no mesmo dia, o código maior da Nuvemshop é o mais recente
    df["_n"] = pd.to_numeric(df["cd_nuvemshop"], errors="coerce").fillna(0)
    return df.sort_values(["dt_pedido", "_n"], ascending=False).drop(columns="_n").reset_index(drop=True)


@st.cache_data(ttl=300)
def carregar_itens(cd: str):
    """Linhas do pedido (uma por produto) com todas as parcelas da margem de contribuição."""
    client = bq.get_client()
    df = bq.query_df(client, f"""
        SELECT CAST(cd_produto_bling AS STRING) AS sku, nm_produto, ds_variacao, qt_item, fg_brinde,
               vl_receita_bruta_produto, vl_desconto_cupom_rateio, vl_desconto_pagamento_rateio, vl_desconto_promocional_rateio,
               vl_receita_liquida_produto, vl_frete_pago_rateio, vl_frete_real_rateio, vl_taxa_pedido_rateio,
               vl_embalagem_rateio, vl_imposto_rateio, vl_reembolso_rateio, vl_custo_linha, vl_margem_contribuicao,
               ds_incompletude, CAST(cd_pedido AS STRING) AS cd_pedido, CAST(cd_pedido_nuvemshop AS STRING) AS cd_nuvemshop,
               dt_pedido, nm_contato, nr_pedido_cliente, fg_cliente_recorrente, ds_tipo_loja, ds_status_pedido,
               ds_meio_pagamento_nuvemshop, qt_parcelas_nuvemshop, ds_codigo_cupom_nuvemshop, dt_recebimento_pedido,
               ds_bandeira_cartao_nuvemshop, ds_gateway_nuvemshop, ds_opcao_frete_nuvemshop, nm_transportadora_nuvemshop,
               ds_origem_checkout_nuvemshop, dt_pagamento_nuvemshop, qt_dias_recebimento, CAST(cd_contato AS STRING) AS cd_contato
          FROM `{AZ}.tb_pedido` WHERE fg_pedido_valido AND CAST(cd_codigo_interno AS STRING) = '{_digitos(cd)}'
    """)
    for c in ["qt_item", "vl_receita_bruta_produto", "vl_desconto_cupom_rateio", "vl_desconto_pagamento_rateio",
              "vl_desconto_promocional_rateio", "vl_receita_liquida_produto", "vl_frete_pago_rateio", "vl_frete_real_rateio",
              "vl_taxa_pedido_rateio", "vl_embalagem_rateio", "vl_imposto_rateio", "vl_reembolso_rateio", "vl_custo_linha",
              "vl_margem_contribuicao"]:
        df[c] = pd.to_numeric(df[c]).fillna(0.0)
    df["fg_brinde"] = df["fg_brinde"].fillna(False).astype(bool)
    df["dt_pedido"] = pd.to_datetime(df["dt_pedido"])
    df["dt_recebimento_pedido"] = pd.to_datetime(df["dt_recebimento_pedido"])
    df["dt_pagamento_nuvemshop"] = pd.to_datetime(df["dt_pagamento_nuvemshop"])
    return df


@st.cache_data(ttl=900)
def carregar_fotos(skus: tuple):
    """Foto por SKU (a da própria variação; se faltar, a de outra variação da família). Link do Bling é assinado e vence."""
    client = bq.get_client()
    g = bq.query_df(client, f"""
        SELECT CAST(cd_produto_bling AS STRING) AS sku, CAST(cd_produto_bling_familia AS STRING) AS familia, lk_imagem_produto AS foto
          FROM `{AZ}.tb_produto_gestao`
         WHERE CAST(cd_produto_bling_familia AS STRING) IN (
               SELECT CAST(cd_produto_bling_familia AS STRING) FROM `{AZ}.tb_produto_gestao` WHERE CAST(cd_produto_bling AS STRING) IN ({_in(skus)}))
    """)
    por_familia = g.groupby("familia")["foto"].apply(lambda s: _foto(list(s))).to_dict()
    proprio = dict(zip(g["sku"], g["foto"]))
    fam = dict(zip(g["sku"], g["familia"]))
    return {s: (proprio.get(s) if isinstance(proprio.get(s), str) and proprio.get(s) else por_familia.get(fam.get(s))) for s in skus}


@st.cache_data(ttl=900)
def carregar_origem(cd_pedido: str):
    """Origem e mídia do pedido (tb_atribuicao_pedido): URL de entrada, com o clique do Google Ads recuperado pelo GA4."""
    client = bq.get_client()
    df = bq.query_df(client, f"""
        SELECT ds_origem_venda AS origem, ds_midia_venda AS midia, ds_gclid, ds_utm_campaign
          FROM `{AZ}.tb_atribuicao_pedido` WHERE CAST(cd_pedido AS STRING) = '{_digitos(cd_pedido)}' LIMIT 1
    """)
    return df.iloc[0].to_dict() if not df.empty else {"origem": "(sem parametro)", "midia": "(sem parametro)", "ds_gclid": None, "ds_utm_campaign": None}


@st.cache_data(ttl=900)
def carregar_pedidos_google(ini: str, fim: str):
    """Pedidos válidos com origem Google pago entre duas datas ('AAAA-MM-DD'), com a data do pedido: divisor do custo do Ads."""
    for d in (ini, fim):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
            raise ValueError(f"data inválida: {d!r}")
    client = bq.get_client()
    df = bq.query_df(client, f"""
        SELECT CAST(a.cd_pedido AS STRING) AS cd_pedido, a.ds_gclid, p.dt_pedido
          FROM `{AZ}.tb_atribuicao_pedido` AS a
          JOIN (SELECT cd_pedido, ANY_VALUE(dt_pedido) AS dt_pedido FROM `{AZ}.tb_pedido`
                 WHERE fg_pedido_valido AND dt_pedido BETWEEN DATE '{ini}' AND DATE '{fim}' GROUP BY cd_pedido) AS p
            ON CAST(p.cd_pedido AS STRING) = CAST(a.cd_pedido AS STRING)
         WHERE a.ds_origem_venda = '{GOOGLE_PAGO[0]}' AND a.ds_midia_venda = '{GOOGLE_PAGO[1]}'
    """)
    df["dt_pedido"] = pd.to_datetime(df["dt_pedido"])
    return df


@st.cache_data(ttl=900)
def carregar_cupom(codigo: str):
    """Regra do cupom usado no pedido (tb_cupom): tipo, valor, pedido mínimo. Vazio se o cupom não existir mais."""
    if not re.fullmatch(r"[A-Za-z0-9_\-]+", codigo):
        raise ValueError(f"cupom inválido: {codigo!r}")
    client = bq.get_client()
    df = bq.query_df(client, f"""
        SELECT ds_tipo, vl_desconto, vl_pedido_minimo, fl_primeira_compra, dt_fim
          FROM `{AZ}.tb_cupom` WHERE UPPER(ds_codigo) = UPPER('{codigo}') LIMIT 1
    """)
    return df.iloc[0].to_dict() if not df.empty else {}


@st.cache_data(ttl=300)
def carregar_logistica(cd: str):
    """Envio do pedido (tb_logistica_pedido): transportadora, rastreio, datas de expedição e entrega, previsão."""
    client = bq.get_client()
    df = bq.query_df(client, f"""
        SELECT nm_transportadora, ds_opcao_frete, cd_rastreio, ds_url_rastreio, dt_expedicao, dt_entrega,
               dt_estimada_entrega_max, ds_cidade_entrega, ds_estado_entrega
          FROM `{AZ}.tb_logistica_pedido` WHERE CAST(cd_codigo_interno AS STRING) = '{_digitos(cd)}' LIMIT 1
    """)
    return df.iloc[0].to_dict() if not df.empty else {}


@st.cache_data(ttl=300)
def carregar_cliente(cd_contato: str):
    """Cliente (tb_cliente): cadastro e acumulado de compras. Vazio se o pedido não tem contato."""
    if not cd_contato or cd_contato in ("None", "nan"):
        return {}
    client = bq.get_client()
    df = bq.query_df(client, f"""
        SELECT nm_contato, ds_endereco_uf, ds_endereco_municipio, nr_telefone, qt_pedido, vl_total_pedido,
               vl_receita_liquida_produto, dt_prim_pedido, dt_ult_pedido
          FROM `{AZ}.tb_cliente` WHERE cd_contato = '{_digitos(cd_contato)}' LIMIT 1
    """)
    return df.iloc[0].to_dict() if not df.empty else {}


@st.cache_data(ttl=300)
def carregar_cashback(cd_contato: str, cd: str):
    """Mensagens da jornada de pós-venda já registradas para o pedido (raw_control.vw_contato_cliente) e o cupom de cashback gerado."""
    chave = f"{_digitos(cd_contato)}|{_digitos(cd)}"
    client = bq.get_client()
    contatos = bq.query_df(client, f"""
        SELECT tipo_tarefa, dt_contato, fg_alcancou, ds_resultado
          FROM `{bq.PROJECT}.raw_control.vw_contato_cliente` WHERE tipo_tarefa LIKE 'jornada%' AND chave = '{chave}'
         ORDER BY dt_contato
    """)
    cupom = bq.query_df(client, f"""
        SELECT codigo, valor, expira_em, cancelado_em
          FROM `{bq.PROJECT}.raw_control.cupons_gerados`
         WHERE campanha = 'cupom_cashback_posvenda' AND referencia = '{chave}' ORDER BY criado_em DESC LIMIT 1
    """)
    return contatos.to_dict("records"), (cupom.iloc[0].to_dict() if not cupom.empty else {})


# ── contas ───────────────────────────────────────────────────────────────────

def _brt(ts):
    """Timestamp (UTC) -> data/hora de Brasília; vazio vira NaT."""
    if ts is None or pd.isna(ts):
        return pd.NaT
    return pd.Timestamp(ts).tz_convert("America/Sao_Paulo") if pd.Timestamp(ts).tzinfo else pd.Timestamp(ts)


def formatar_telefone(nr):
    """(texto, link do WhatsApp) a partir do telefone do cadastro; vazio se não houver número válido."""
    d = re.sub(r"\D", "", str(nr or ""))
    if d.startswith("55") and len(d) > 11:
        d = d[2:]
    if len(d) not in (10, 11):
        return "", ""
    corpo = f"{d[2:7]}-{d[7:]}" if len(d) == 11 else f"{d[2:6]}-{d[6:]}"
    return f"({d[:2]}) {corpo}", f"https://wa.me/55{d}"


def status_envio(log, status_pedido):
    """Expedição e entrega do pedido: {'expedicao': (texto, detalhe, tipo), 'entrega': (...)}.

    Não usa `ds_situacao_logistica`/`fg_entregue`: ficam verdadeiros para pedido sem rastreio carregado (achado de 13/10/2026).
    Expedido = tem `dt_expedicao`; entregue = tem `dt_entrega` (confirmada).
    """
    log = log or {}
    exp, ent, prev = log.get("dt_expedicao"), log.get("dt_entrega"), log.get("dt_estimada_entrega_max")
    if exp is not None and not pd.isna(exp):
        expedicao = (f"Expedido em {pd.Timestamp(exp):%d/%m}", "", "ok")
    elif str(status_pedido).upper() == "EM ABERTO":
        expedicao = ("Aguardando postagem", "pago, ainda não expedido", "warn")
    else:
        expedicao = ("Sem registro de expedição", "o rastreio pode não ter sido carregado", "")
    if ent is not None and not pd.isna(ent):
        entrega = (f"Entregue em {pd.Timestamp(ent):%d/%m}", "", "ok")
    elif prev is not None and not pd.isna(prev):
        entrega = (f"Previsão até {pd.Timestamp(prev):%d/%m}", "", "")
    else:
        entrega = ("—", "", "")
    return {"expedicao": expedicao, "entrega": entrega}


def status_cashback(contatos, cupom, nr_pedido):
    """Mensagem de cashback (W0, logo após o pagamento) deste pedido: (texto, detalhe, tipo). `fg_alcancou` = a mensagem chegou ao cliente."""
    det = ""
    if cupom:
        det = f"cupom {cupom.get('codigo')} · {brl(float(cupom.get('valor') or 0))}"
        if cupom.get("expira_em") is not None and not pd.isna(cupom.get("expira_em")):
            det += f" · vence {_brt(cupom['expira_em']):%d/%m}"
        if cupom.get("cancelado_em") is not None and not pd.isna(cupom.get("cancelado_em")):
            det += " · cancelado"
    w0 = next((c for c in contatos if c.get("tipo_tarefa") == "jornada_w0"), None)
    if w0:
        dia = _brt(w0["dt_contato"])
        if bool(w0.get("fg_alcancou")):
            return f"Recebeu em {dia:%d/%m}", det, "ok"
        motivo = f" ({w0['ds_resultado']})" if isinstance(w0.get("ds_resultado"), str) and w0["ds_resultado"] else ""
        return f"Enviada em {dia:%d/%m}, sem confirmação{motivo}", det, "warn"
    if cupom:
        return "Cupom gerado, mensagem ainda não enviada", det, "warn"
    if nr_pedido is not None and not pd.isna(nr_pedido) and int(nr_pedido) > MAX_PEDIDO_CASHBACK:
        return f"Sem cashback ({int(nr_pedido)}º pedido)", f"o cashback vai até o {MAX_PEDIDO_CASHBACK}º pedido", ""
    return "Não enviada", "sem registro na jornada de pós-venda", ""

def resumo_pedido(it):
    """Parcelas do pedido, da cascata. Fecha com a margem de contribuição do dbt (campo `folga` = diferença, deve ser ~0)."""
    bruto = float(it.loc[~it["fg_brinde"], "vl_receita_bruta_produto"].sum())
    rec = float(it["vl_receita_liquida_produto"].sum())
    s = {c: float(it[c].sum()) for c in ["vl_frete_pago_rateio", "vl_frete_real_rateio", "vl_taxa_pedido_rateio", "vl_embalagem_rateio",
                                         "vl_imposto_rateio", "vl_reembolso_rateio", "vl_custo_linha", "vl_margem_contribuicao",
                                         "vl_desconto_cupom_rateio", "vl_desconto_pagamento_rateio", "vl_desconto_promocional_rateio"]}
    nb = ~it["fg_brinde"]  # o desconto promocional do brinde (100% do valor dele) não é desconto que o cliente recebeu
    pago = rec + s["vl_frete_pago_rateio"]
    r = {
        "bruto": bruto, "rec": rec, "desconto": bruto - rec, "frete": s["vl_frete_pago_rateio"], "pago": pago,
        "taxa": s["vl_taxa_pedido_rateio"], "frete_real": s["vl_frete_real_rateio"], "embalagem": s["vl_embalagem_rateio"],
        "imposto": s["vl_imposto_rateio"], "reembolso": s["vl_reembolso_rateio"], "custo": s["vl_custo_linha"],
        "custo_brinde": float(it.loc[it["fg_brinde"], "vl_custo_linha"].sum()), "margem": s["vl_margem_contribuicao"],
        "cupom": float(it.loc[nb, "vl_desconto_cupom_rateio"].sum()), "pgto": float(it.loc[nb, "vl_desconto_pagamento_rateio"].sum()),
        "promo": float(it.loc[nb, "vl_desconto_promocional_rateio"].sum()),
    }
    r["entrou"] = pago - r["taxa"]
    r["margem_pct"] = (r["margem"] / rec) if rec else None
    r["folga"] = pago - r["taxa"] - r["frete_real"] - r["embalagem"] - r["imposto"] - r["reembolso"] - r["custo"] - r["margem"]
    return r


def detalhe_descontos(r, codigo_cupom, cupom, meio_pagamento):
    """Descontos que o cliente recebeu, um item por origem: (título, valor, detalhe). Soma = r['desconto'].

    Cupom: código e regra vêm de tb_cupom. Pix/pagamento: é o desconto do gateway. Promoção: a Nuvemshop só entrega o
    valor total (o nome da promoção não chega à base — ver specs/consulta-pedido.md). Resto = ajuste manual.
    """
    itens = []
    codigo = (codigo_cupom or "").strip() if isinstance(codigo_cupom, str) else ""
    if codigo or r["cupom"] > 0.005:
        regra = ""
        if cupom:
            valor = float(cupom.get("vl_desconto") or 0)
            regra = f"{valor:g}% de desconto" if cupom.get("ds_tipo") == "percentage" else f"{brl(valor)} fixo"
            minimo = cupom.get("vl_pedido_minimo")
            if minimo is not None and not pd.isna(minimo) and float(minimo) > 0:
                regra += f" · pedido mín. {brl(float(minimo))}"
            if cupom.get("fl_primeira_compra"):
                regra += " · 1ª compra"
        itens.append((f"Cupom {codigo}" if codigo else "Cupom", r["cupom"], regra or "regra do cupom indisponível"))
    if r["pgto"] > 0.005:
        pix = str(meio_pagamento).lower() == "pix"
        itens.append(("Desconto Pix" if pix else "Desconto do pagamento", r["pgto"],
                      "por pagar no Pix" if pix else f"desconto do meio de pagamento ({meio_pagamento})"))
    if r["promo"] > 0.005:
        itens.append(("Promoção da loja", r["promo"], "promoção automática · nome não vem da Nuvemshop"))
    resto = r["desconto"] - r["cupom"] - r["pgto"] - r["promo"]
    if resto > 0.005:
        itens.append(("Outro desconto", resto, "ajuste manual no pedido"))
    return itens


def custo_aquisicao(origem, midia, gclid, dia, pedidos_google, ponte, perf):
    """Custo de mídia paga atribuído ao pedido: (valor | None, explicação curta). None = não dá para saber.

    Só o Google Ads tem custo na base. Pedido Google pago:
    1. campanha do clique já identificada (gclid → campanha): custo da campanha no mês do pedido ÷ pedidos do mês dela;
    2. senão (clique ainda não ligado à campanha, o Ads leva ~1 dia; sem gclid; mês sem custo): custo MÉDIO POR VENDA do
       Google Ads nos últimos 30 dias com custo lançado (custo total ÷ pedidos Google pago do mesmo período).
    shortcut: o divisor conta todos os pedidos Google pago (com ou sem clique identificável); se o Ads passar a mandar
    conversão por pedido, trocar por ela.
    """
    if (origem, midia) == GOOGLE_PAGO:
        dia = pd.Timestamp(dia).normalize()
        mes = dia.to_period("M").to_timestamp()
        if mes < INICIO_ADS:
            return None, "sem histórico de custo do Google Ads antes de jun/2026"
        if perf.empty:
            return None, "sem custo do Google Ads lançado"
        ponte = ponte.drop_duplicates("cd_gclid").set_index("cd_gclid")
        ped = pedidos_google.assign(cd_campanha=pedidos_google["ds_gclid"].map(ponte["cd_campanha"]))
        no_mes = perf[perf["dt_data"].dt.to_period("M").dt.to_timestamp() == mes]
        camp = ponte["cd_campanha"].get(gclid) if gclid else None
        if camp is not None and pd.notna(camp) and not no_mes.empty:
            ped_mes = ped[ped["dt_pedido"].dt.to_period("M").dt.to_timestamp() == mes]
            n = int((ped_mes["cd_campanha"] == camp).sum())
            if n:
                custo = float(no_mes.loc[no_mes["cd_campanha"] == camp, "vl_custo"].sum())
                return custo / n, f"campanha {ponte['nm_campanha'].get(gclid)}: {brl(custo)} no mês ÷ {n} {'pedido' if n == 1 else 'pedidos'}"
        # custo médio por venda do Google Ads: janela móvel, só até o último dia com custo lançado
        fim = min(dia, perf["dt_data"].max())
        ini = max(fim - pd.Timedelta(days=JANELA_DIAS - 1), INICIO_ADS)
        custo = float(perf.loc[(perf["dt_data"] >= ini) & (perf["dt_data"] <= fim), "vl_custo"].sum())
        n = int(((ped["dt_pedido"] >= ini) & (ped["dt_pedido"] <= fim)).sum())
        if not n:
            return None, "sem pedidos Google pago na janela de custo"
        if not gclid:
            motivo = "pedido sem clique de anúncio identificado"
        elif camp is None or pd.isna(camp):
            motivo = "campanha do clique ainda não identificada (o Google Ads leva ~1 dia)"
        else:
            motivo = "sem custo da campanha no mês"
        return custo / n, f"estimativa: custo médio por venda do Google Ads de {ini:%d/%m} a {fim:%d/%m} ({brl(custo)} ÷ {n} pedidos) · {motivo}"
    if (origem, midia) == META_PAGO:
        return None, "mídia paga do Instagram: o custo do Meta Ads não está na base"
    return 0.0, "sem mídia paga rastreada"


# ── apresentação ─────────────────────────────────────────────────────────────

def _cod(codigo):
    """Código da Nuvemshop leva #; pedido só do Bling (ex.: Mercado Livre) aparece como 'Bling nnnn'."""
    codigo = str(codigo)
    return codigo if codigo.startswith("Bling") else f"#{codigo}"


def _itens_html(it, fotos):
    cards = []
    for _, r in it.sort_values("fg_brinde", kind="stable").iterrows():
        foto = fotos.get(r["sku"])
        img = (f'<img src="{html.escape(foto)}" style="width:84px;height:84px;object-fit:cover;border-radius:8px;flex:none" onerror="this.style.display=\'none\'">'
               if foto else f'<div style="width:84px;height:84px;border-radius:8px;background:{COLORS["bg_secondary"]};flex:none"></div>')
        var = "" if str(r["ds_variacao"]).lower().startswith("sem var") or pd.isna(r["ds_variacao"]) else f' · {html.escape(str(r["ds_variacao"]))}'
        qt = int(r["qt_item"])
        if r["fg_brinde"]:
            linha, valor = f"{qt} × brinde", "R$ 0,00"
        else:
            unit = r["vl_receita_bruta_produto"] / qt if qt else 0.0
            linha = f"{qt} × {brl(unit)}"
            valor = brl(r["vl_receita_bruta_produto"])
            if r["vl_receita_liquida_produto"] < r["vl_receita_bruta_produto"] - 0.005:
                linha += f" · com desconto {brl(r['vl_receita_liquida_produto'])}"
        cards.append(f"""<div style="display:flex;gap:12px;align-items:center;border:1px solid {COLORS['border']};border-radius:10px;padding:10px;background:{COLORS['bg']}">
          {img}
          <div style="min-width:0;flex:1">
            <div style="font-size:14px;font-weight:700;color:{COLORS['primary_dark']};line-height:1.25">{html.escape(str(r['nm_produto']))}</div>
            <div style="font-size:12px;color:{COLORS['text_secondary']};margin:2px 0 6px">{var.lstrip(' ·') or '&nbsp;'}</div>
            <div style="font-size:12px;color:{COLORS['text_muted']}">{linha}</div>
          </div>
          <div style="font-size:16px;font-weight:800;color:{COLORS['text']};white-space:nowrap">{valor}</div>
        </div>""")
    st.html(f'<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(min(340px,100%),1fr));gap:12px;margin:6px 0 4px">{"".join(cards)}</div>')


# grade dos quadros: colunas de ≥ 420px, mas nunca mais largas que a tela (no celular vira 1 coluna sem rolagem lateral)
GRADE = "display:grid;grid-template-columns:repeat(auto-fit,minmax(min(420px,100%),1fr));gap:16px;align-items:start;margin-top:6px"

_TIPOS = {  # tipo da linha -> (fundo, cor do valor, negrito)
    "": ("", COLORS["text"], False), "neg": ("", COLORS["danger"], False), "total": (COLORS["bg_secondary"], COLORS["text"], True),
    "ok": (COLORS["success_bg"], COLORS["success"], True), "warn": (COLORS["warning_bg"], COLORS["warning"], True),
    "bad": (COLORS["danger_bg"], COLORS["danger"], True),
}


def _linha(rotulo, valor, detalhe="", tipo="", recuo=False, valor_html=None):
    """Uma linha do extrato: rótulo (e detalhe pequeno) à esquerda, valor à direita. Tipos: neg (vermelho), total, ok, warn, bad."""
    fundo, cor, forte = _TIPOS[tipo]
    peso = 700 if forte else 400
    pad = "9px 16px 9px 34px" if recuo else "10px 16px"
    det = f'<div style="font-size:11px;color:{COLORS["text_muted"]};margin-top:1px;font-weight:400">{html.escape(detalhe)}</div>' if detalhe else ""
    bg = f"background:{fundo};" if fundo else ""
    rot_cor = COLORS["text_secondary"] if recuo else COLORS["text"]
    curto = valor_html is None and len(valor) <= 18   # número/valor curto fica numa linha só; texto longo quebra em vez de alargar a tela
    nowrap = "white-space:nowrap;flex:none;" if curto else "min-width:0;max-width:58%;overflow-wrap:anywhere;"
    return (f'<div style="display:flex;justify-content:space-between;align-items:baseline;gap:14px;padding:{pad};border-top:1px solid {COLORS["border"]};{bg}">'
            f'<div style="font-size:{12.5 if recuo else 13.5}px;color:{rot_cor};font-weight:{peso};min-width:0;flex:1 1 0;overflow-wrap:anywhere">{html.escape(rotulo)}{det}</div>'
            f'<div style="font-size:13.5px;color:{cor};font-weight:{peso};{nowrap}text-align:right">{valor_html if valor_html is not None else html.escape(valor)}</div></div>')


def _painel(titulo, linhas, selo=None):
    """Quadro do extrato (título + selo opcional no cabeçalho + linhas), no estilo do pedido da Nuvemshop."""
    chip = (f'<span style="background:{selo[1]};color:{selo[2]};border-radius:999px;padding:2px 10px;font-size:11px;font-weight:700;white-space:nowrap">{html.escape(selo[0])}</span>'
            if selo else "")
    return (f'<div style="border:1px solid {COLORS["border"]};border-radius:12px;overflow:hidden;background:{COLORS["bg"]}">'
            f'<div style="display:flex;justify-content:space-between;align-items:center;gap:10px;padding:14px 16px">'
            f'<div style="font-size:16px;font-weight:800;color:{COLORS["primary_dark"]}">{html.escape(titulo)}</div>{chip}</div>{"".join(linhas)}</div>')


def _txt(v):
    return str(v).strip() if isinstance(v, str) and v.strip() else ""


def render():
    inject_css()
    st.html("""
    <div class="report-header"><div>
      <div class="report-brand">shibari brasil · camada diária</div>
      <div class="report-title">Consulta de <span>Pedidos</span></div>
      <div class="report-meta">Um pedido por vez, do carrinho à margem · pedidos válidos (pagos, não cancelados)</div>
    </div></div>""")
    try:
        lista = carregar_lista()
    except Exception as e:  # noqa: BLE001
        st.error(f"Erro ao carregar os pedidos do BigQuery: {e}")
        return
    if lista.empty:
        st.info("Sem pedidos válidos no período.")
        return

    rot = {r.cd: f"{_cod(r.codigo)} · {r.dt_pedido:%d/%m/%Y} · {r.nm_contato or '—'} · {brl(r.vl_liquido)}" for r in lista.itertuples()}
    cd = st.selectbox("Pedido", list(rot), format_func=rot.get, help="O mais recente vem selecionado. Digite o código ou o nome do cliente para buscar.")
    try:
        it = carregar_itens(cd)
        fotos = carregar_fotos(tuple(sorted(it["sku"].dropna().unique())))
        org = carregar_origem(it["cd_pedido"].iloc[0])
    except Exception as e:  # noqa: BLE001
        st.error(f"Erro ao carregar o pedido: {e}")
        return
    r = resumo_pedido(it)
    h = it.iloc[0]
    hoje = pd.Timestamp(_hoje_brt())

    azul, cinza = (COLORS["primary_light"], COLORS["primary_dark"]), (COLORS["bg_secondary"], COLORS["text_secondary"])
    meio = MEIOS.get(h["ds_meio_pagamento_nuvemshop"], str(h["ds_meio_pagamento_nuvemshop"]) if pd.notna(h["ds_meio_pagamento_nuvemshop"]) else "Meio não informado")
    parc = int(h["qt_parcelas_nuvemshop"]) if pd.notna(h["qt_parcelas_nuvemshop"]) else 0
    recorrente = bool(h["fg_cliente_recorrente"]) if pd.notna(h["fg_cliente_recorrente"]) else False
    n_ped = f" · {int(h['nr_pedido_cliente'])}º pedido" if pd.notna(h["nr_pedido_cliente"]) else ""
    st.html(f"""<div style="display:flex;gap:18px;align-items:baseline;flex-wrap:wrap;margin:4px 0 6px">
      <div style="font-size:24px;font-weight:800;color:{COLORS['primary_dark']}">Pedido {html.escape(_cod(h['cd_nuvemshop'] if pd.notna(h['cd_nuvemshop']) else 'Bling ' + str(h['cd_pedido'])))}</div>
      <div style="font-size:13px;color:{COLORS['text_secondary']}">{h['dt_pedido']:%d/%m/%Y} · {html.escape(str(h['nm_contato'] or '—'))}</div></div>
      {_chips([
          (str(h['ds_tipo_loja']) if pd.notna(h['ds_tipo_loja']) else "", *azul),
          (str(h['ds_status_pedido']).title() if pd.notna(h['ds_status_pedido']) else "", *cinza),
          (f"{meio}{f' {parc}x' if parc > 1 else ''}", *cinza),
          (("Recompra" if recorrente else "1ª compra") + n_ped, *cinza),
          (f"Cupom {h['ds_codigo_cupom_nuvemshop']}" if pd.notna(h['ds_codigo_cupom_nuvemshop']) and str(h['ds_codigo_cupom_nuvemshop']).strip() else "", *azul),
      ])}""")

    if it["ds_incompletude"].notna().any():
        faltas = " + ".join(sorted({t.strip() for v in it["ds_incompletude"].dropna() for t in v.split("+")}))
        note(f"<strong>Dado faltante neste pedido:</strong> {html.escape(faltas)}. A margem está superestimada.", variant="warn")
    if abs(r["folga"]) > 0.02:
        note(f"A cascata não fecha com a margem do dbt (diferença de {brl(r['folga'])}); confira o pedido.", variant="warn")

    # ═══ O PEDIDO ═══
    section_title("O pedido")
    _itens_html(it, fotos)

    # ═══ DADOS DA VENDA E DO CLIENTE (montados aqui, mostrados no fim da página) ═══
    bandeira_txt = _txt(h["ds_bandeira_cartao_nuvemshop"]).title()
    if bandeira_txt.lower() == meio.lower():  # Pix vem com "bandeira" pix
        bandeira_txt = ""
    try:
        log = carregar_logistica(cd)
    except Exception:  # noqa: BLE001
        log = {}
    try:
        cliente = carregar_cliente(str(h["cd_contato"]))
    except Exception:  # noqa: BLE001
        cliente = {}
    try:
        contatos, cupom_cb = carregar_cashback(str(h["cd_contato"]), cd) if pd.notna(h["cd_contato"]) else ([], {})
        cb_ok = pd.notna(h["cd_contato"])
    except Exception:  # noqa: BLE001
        contatos, cupom_cb, cb_ok = [], {}, False
    env = status_envio(log, h["ds_status_pedido"])
    parcelas = f"{parc}x" if parc > 1 else ("à vista" if h["ds_meio_pagamento_nuvemshop"] == "credit_card" else "")
    rastreio, url = _txt(log.get("cd_rastreio")), _txt(log.get("ds_url_rastreio"))
    link_rastreio = (f'<a href="{html.escape(url)}" target="_blank" rel="noopener" style="color:{COLORS["primary"]};font-weight:600">{html.escape(rastreio)}</a>'
                     if rastreio and url else None)
    cb = status_cashback(contatos, cupom_cb, h["nr_pedido_cliente"]) if cb_ok else ("Indisponível", "não foi possível ler o registro de mensagens", "")
    w1 = next((c for c in contatos if c.get("tipo_tarefa") == "jornada_w1"), None)
    LV = [_linha("Pagamento", " · ".join(x for x in [meio, parcelas] if x) or "—", " · ".join(x for x in [bandeira_txt, _txt(h["ds_gateway_nuvemshop"])] if x)),
          _linha("Envio", _txt(log.get("ds_opcao_frete")) or _txt(h["ds_opcao_frete_nuvemshop"]) or "—", _txt(log.get("nm_transportadora")) or _txt(h["nm_transportadora_nuvemshop"])),
          _linha("Rastreio", rastreio or "ainda sem código", valor_html=link_rastreio),
          _linha("Expedição", env["expedicao"][0], env["expedicao"][1], env["expedicao"][2]),
          _linha("Entrega", env["entrega"][0], env["entrega"][1], env["entrega"][2]),
          _linha("Mensagem de cashback", cb[0], cb[1], cb[2])]
    if w1:
        LV.append(_linha("Check-in pós-entrega", f"{'Recebeu' if bool(w1.get('fg_alcancou')) else 'Enviado'} em {_brt(w1['dt_contato']):%d/%m}", "", "ok" if bool(w1.get("fg_alcancou")) else "warn"))
    tel, wa = formatar_telefone(cliente.get("nr_telefone"))
    cidade, uf = _txt(cliente.get("ds_endereco_municipio")) or _txt(log.get("ds_cidade_entrega")), _txt(cliente.get("ds_endereco_uf"))
    local = " / ".join(x for x in [cidade, uf] if x) or "—"
    qt_ped = int(cliente["qt_pedido"]) if pd.notna(cliente.get("qt_pedido")) else None
    datas = " · ".join(x for x in [f"1ª compra em {pd.Timestamp(cliente['dt_prim_pedido']):%d/%m/%Y}" if pd.notna(cliente.get("dt_prim_pedido")) else "",
                                   f"última em {pd.Timestamp(cliente['dt_ult_pedido']):%d/%m/%Y}" if pd.notna(cliente.get("dt_ult_pedido")) else ""] if x)
    link_wa = (f'<a href="{html.escape(wa)}" target="_blank" rel="noopener" style="color:{COLORS["primary"]};font-weight:600">{html.escape(tel)}</a>' if wa else None)
    LC = [_linha("Nome", _txt(cliente.get("nm_contato")) or str(h["nm_contato"] or "—")),
          _linha("Cidade / UF", local),
          _linha("WhatsApp", tel or "—", valor_html=link_wa),
          _linha("Pedidos feitos", "—" if qt_ped is None else f"{qt_ped} pedido{'s' if qt_ped != 1 else ''}", datas)]
    if pd.notna(cliente.get("vl_total_pedido")):
        LC.append(_linha("Total consumido", brl(float(cliente["vl_total_pedido"])), f"produtos {brl(float(cliente['vl_receita_liquida_produto']))}, sem frete", "total"))
    if not cliente:
        LC.append(_linha("Cadastro", "não encontrado", "pedido sem cliente vinculado na base"))
    selo_c = ("Recompra", COLORS["info_bg"], COLORS["info"]) if recorrente else ("1ª compra", COLORS["success_bg"], COLORS["success"])

    # ═══ DA VENDA À MARGEM (extrato) ═══
    section_title("Da venda à margem")
    cupom_cod = h["ds_codigo_cupom_nuvemshop"] if pd.notna(h["ds_codigo_cupom_nuvemshop"]) else ""
    try:
        cupom_info = carregar_cupom(str(cupom_cod).strip()) if str(cupom_cod).strip() else {}
    except Exception:  # noqa: BLE001
        cupom_info = {}
    descontos = detalhe_descontos(r, cupom_cod, cupom_info, h["ds_meio_pagamento_nuvemshop"])

    # origem e custo de aquisição
    dia = pd.Timestamp(h["dt_pedido"]).normalize()
    aq, expl = None, ""
    try:
        if (org["origem"], org["midia"]) == GOOGLE_PAGO:  # divisor: pedidos Google pago do mês e dos últimos 30 dias
            ini = max(INICIO_ADS, min(dia.to_period("M").to_timestamp(), dia - pd.Timedelta(days=JANELA_DIAS - 1)))
            google = carregar_pedidos_google(f"{ini:%Y-%m-%d}", f"{dia:%Y-%m-%d}")
        else:
            google = pd.DataFrame(columns=["cd_pedido", "ds_gclid", "dt_pedido"])
        camp = carregar_campanhas()
        aq, expl = custo_aquisicao(org["origem"], org["midia"], org["ds_gclid"], dia, google, camp["ponte"], camp["perf"])
    except Exception as e:  # noqa: BLE001
        expl = f"custo do Google Ads indisponível ({e})"
    pos = None if aq is None else r["margem"] - aq
    pos_pct = (pos / r["rec"]) if pos is not None and r["rec"] else None
    sem_origem = str(org["origem"]).startswith("(sem")
    campanha = _txt(org.get("ds_utm_campaign"))

    # 1) Pagamento: do preço dos itens ao que entrou na conta
    receb, pagto = h["dt_recebimento_pedido"], h["dt_pagamento_nuvemshop"]
    quando = "" if pd.isna(receb) else (f"cai em {receb:%d/%m}" if receb > hoje else f"caiu em {receb:%d/%m}")
    prazo = f" · pago em {pagto:%d/%m}" if pd.notna(pagto) else ""
    if pd.notna(h["qt_dias_recebimento"]) and pd.notna(pagto) and int(h["qt_dias_recebimento"]) > 0:
        n_dias = int(h["qt_dias_recebimento"])
        prazo += f" (+{n_dias} dia{'s' if n_dias > 1 else ''})"
    bandeira = bandeira_txt
    pagamento_txt = " · ".join(x for x in [meio, bandeira, f"{parc}x" if parc > 1 else "", _txt(h["ds_gateway_nuvemshop"])] if x)
    frete_det = _txt(log.get("ds_opcao_frete")) or _txt(h["ds_opcao_frete_nuvemshop"])
    un = int(it.loc[~it["fg_brinde"], "qt_item"].sum())
    L1 = [_linha(f"Subtotal ({un} un. a preço cheio)", brl(r["bruto"]))]
    L1 += [_linha(t, f"− {brl(v)}", d, "neg", recuo=True) for t, v, d in descontos]
    if descontos:
        L1.append(_linha("Receita líquida de produtos", brl(r["rec"]), "subtotal − descontos", "total"))
    L1.append(_linha("Frete cobrado", brl(r["frete"]) if r["frete"] > 0.005 else "Grátis", frete_det))
    L1.append(_linha("Total pago pelo cliente", brl(r["pago"]), pagamento_txt, "total"))
    L1.append(_linha("Taxa de pagamento", f"− {brl(r['taxa'])}", f"{pct(r['taxa'] / r['pago']) if r['pago'] else '—'} do valor pago · taxa real do gateway", "neg"))
    L1.append(_linha("Total líquido (entrou na conta)", brl(r["entrou"]), (quando + prazo).strip(" ·") if quando else "valor pago − taxa", "ok"))

    # 2) Do caixa à margem de contribuição (continua o mesmo quadro)
    res_frete = r["frete"] - r["frete_real"]
    var_m = _variant_margem(r["margem_pct"])
    tipo_m = "total" if var_m == "neutral" else var_m
    cores_m = {"ok": (COLORS["success_bg"], COLORS["success"]), "warn": (COLORS["warning_bg"], COLORS["warning"]), "bad": (COLORS["danger_bg"], COLORS["danger"])}
    if abs(res_frete) < 0.005:
        frete_res = "frete empatado"
    else:
        frete_res = f"sobrou {brl(res_frete)}" if res_frete > 0 else f"a loja bancou {brl(-res_frete)}"
    L2 = [_linha("Frete (etiqueta)", f"− {brl(r['frete_real'])}", f"cliente pagou {brl(r['frete'])} · {frete_res}", "neg"),
          _linha("Embalagem", f"− {brl(r['embalagem'])}", "estimativa por pedido", "neg"),
          _linha("Imposto", f"− {brl(r['imposto'])}", "sobre o valor já com desconto", "neg")]
    if r["reembolso"] > 0.005:
        L2.append(_linha("Reembolso", f"− {brl(r['reembolso'])}", "estornado ao cliente", "neg"))
    L2.append(_linha("Custo dos produtos", f"− {brl(r['custo'])}", f"inclui {brl(r['custo_brinde'])} de brinde" if r["custo_brinde"] > 0.005 else "CMV do pedido", "neg"))
    L2.append(_linha("Margem de contribuição", brl(r["margem"]), f"{pct(r['margem_pct'])} da receita líquida · antes de mídia", tipo_m))
    selo2 = (pct(r["margem_pct"]), *cores_m[var_m]) if var_m in cores_m else None

    # 3) Origem e custo de aquisição
    checkout = {"store": "Site", "mobile": "Mobile", "form": "Link direto"}.get(_txt(h["ds_origem_checkout_nuvemshop"]), _txt(h["ds_origem_checkout_nuvemshop"]))
    L4 = [_linha("Origem", "Direto / sem parâmetro" if sem_origem else str(org["origem"]), "sem UTM nem clique de anúncio" if sem_origem else ""),
          _linha("Mídia", "—" if sem_origem else str(org["midia"]))]
    if campanha:
        L4.append(_linha("Campanha (UTM)", campanha))
    if checkout:
        L4.append(_linha("Compra feita em", checkout))
    L4.append(_linha("Margem de contribuição", brl(r["margem"]), "", "total"))
    L4.append(_linha("Custo de aquisição", f"− {brl(aq)}" if aq is not None else "—", expl, "neg" if aq else ""))
    var_p = _variant_margem(pos_pct) if pos is not None else "neutral"
    L4.append(_linha("Margem após aquisição", brl(pos) if pos is not None else "—",
                     f"{pct(pos_pct)} da receita líquida" if pos is not None else "sem custo de aquisição estimado", "total" if var_p == "neutral" else var_p))
    selo4 = (pct(pos_pct), *cores_m[var_p]) if var_p in cores_m else None

    st.html(f'<div style="{GRADE}">' + _painel("Pagamento e margem", L1 + L2, selo2) + _painel("Origem e aquisição", L4, selo4) + "</div>")

    # ═══ VENDA E CLIENTE (fim da página) ═══
    section_title("Venda e cliente")
    st.html(f'<div style="{GRADE}">' + _painel("Informações da venda", LV) + _painel("Cliente", LC, selo_c) + "</div>")
