"""Relatório Google Ads — Shibari Brasil.

Regras de negócio e definição de cada indicador: ver specs/google-ads.md.
Não altere cálculo/filtro sem antes ler (e, se preciso, atualizar) esse spec.
"""
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from common import bigquery as bq
from common import claude
from common.design import (
    PLUM, SCARLET, TAUPE, SKIN, OK, OK_BG, WARN, WARN_BG, BAD, GRID,
    inject_css, card, render_cards, section_title, bench_row, note,
    roas_variant, util_variant, qs_variant, style_color, plotly_layout, nome_curto,
    insight_card, render_insights, opportunity_card, render_opportunities,
)

# Classificação de conversão por relevância de negócio — específica do Google
# Ads (segments_conversion_action_category), por isso mora aqui e não em
# common/design.py. Ver specs/google-ads.md.
TIPO_CONVERSAO = {
    "PURCHASE": ("Primária", "ok"),
    "ADD_TO_CART": ("Secundária", "warn"),
    "BEGIN_CHECKOUT": ("Secundária", "warn"),
}


def tipo_conversao(categoria):
    return TIPO_CONVERSAO.get(categoria, ("Micro", "muted"))

DATASET = "dbt_dw_us_rpt"

TABELAS = {
    "resumo_conta":          "rpt_gads_resumo_conta",
    "performance_campanhas": "rpt_gads_performance_campanhas",
    "tendencia_diaria":      "rpt_gads_tendencia_diaria",
    "conversoes_tipo":       "rpt_gads_conversoes_tipo",
    "orcamento":             "rpt_gads_orcamento",
    "keywords_top":          "rpt_gads_keywords_top",
    "impression_share":      "rpt_gads_impression_share",
    "anuncios":              "rpt_gads_anuncios",
}


@st.cache_data(ttl=3600)
def carregar_dados():
    client = bq.get_client()
    return bq.query_tables(client, DATASET, TABELAS)


ACOES_PATH = Path(__file__).resolve().parent.parent / "content" / "acoes-google-ads.md"


@st.cache_data(ttl=3600)
def carregar_acoes():
    """Lê e faz parsing simples de content/acoes-google-ads.md — cada
    entrada começa num cabeçalho '### DD/MM/AAAA — Título'. Não estrutura o
    corpo (já é markdown rico, com tabelas e subseções) — cada entrada guarda
    o corpo bruto pra renderizar direto com st.markdown(). Entradas mais
    recentes primeiro (mesma ordem do arquivo — ver content/acoes-google-ads.md).
    """
    texto = ACOES_PATH.read_text(encoding="utf-8")
    _, _, registro = texto.partition("## Registro de Ações")
    entradas = []
    for bloco in re.split(r"\n### ", registro)[1:]:
        cabecalho, _, corpo = bloco.partition("\n")
        data_str, _, titulo = cabecalho.partition(" — ")
        status_match = re.search(r"\*\*Status(?:\s+geral)?:?\*\*\s*(.+)", corpo)
        entradas.append({
            "data": data_str.strip(),
            "titulo": titulo.strip(),
            "corpo": corpo.strip().rstrip("-").strip(),
            "status": status_match.group(1).strip() if status_match else None,
        })
    return entradas


# Palavras genéricas demais pra contar como sinal de correlação (aparecem no
# nome de quase toda campanha desta conta) — sem isso, "shibari"/"brasil"
# combinaria qualquer ação com qualquer campanha.
_STOPWORDS_CORRELACAO = {"de", "da", "do", "e", "a", "o", "os", "as", "shibari", "brasil"}

# Nº mínimo de palavras do nome da campanha que precisam aparecer no texto da
# ação pra considerar correlacionada — 1 palavra sozinha ("compra", "shibari")
# gera falso positivo entre campanhas parecidas; 2+ desambiguou bem em teste
# com dado real (ver specs/google-ads.md).
LIMIAR_PALAVRAS_CORRELACAO = 2


def _palavras_significativas(texto):
    palavras = re.findall(r"[\wÀ-ú/]+", texto.lower())
    return {p for p in palavras if p not in _STOPWORDS_CORRELACAO and len(p) > 1}


def detectar_acoes_avaliaveis(acoes, dados):
    """Decide quais ações do log já têm o que avaliar — regra fixa em
    Python, sem IA. Só entram ações com status Executado ou Monitorando
    (Planejado ainda não tem resultado pra medir) dentre as mais recentes, e
    só quando pelo menos uma campanha atual correlaciona pelo nome (ver
    LIMIAR_PALAVRAS_CORRELACAO) — sem correlação, não há número real pra
    ancorar a avaliação da IA, então a ação é descartada aqui, não enviada
    à Claude API. Correlação é heurística por sobreposição de palavras do
    nome da campanha — documentada e com limitação conhecida em
    specs/google-ads.md (pode errar se o nome da campanha mudou desde o
    registro da ação).
    """
    df_camp = dados["performance_campanhas"]
    df_orc = dados["orcamento"]
    palavras_por_campanha = {
        nome: _palavras_significativas(nome) for nome in df_camp["nm_campanha"].unique()
    }

    avaliaveis = []
    for acao in acoes[:LIMITE_ACOES_RECENTES]:
        status = (acao["status"] or "").lower()
        if "executado" not in status and "monitorando" not in status:
            continue

        texto_acao = f'{acao["titulo"]} {acao["corpo"]}'.lower()
        campanhas_correlacionadas = []
        for nome, palavras in palavras_por_campanha.items():
            score = sum(1 for p in palavras if p in texto_acao)
            if score < LIMIAR_PALAVRAS_CORRELACAO:
                continue
            camp_row = df_camp[df_camp["nm_campanha"] == nome].iloc[0]
            orc_row = df_orc[df_orc["nm_campanha"] == nome]
            campanhas_correlacionadas.append({
                "campanha": nome,
                "custo_total": float(camp_row["vl_custo_total"]),
                "roas": float(camp_row["vl_roas"]),
                "utilizacao_orcamento": float(orc_row.iloc[0]["pct_utilizacao_media"]) if not orc_row.empty else None,
            })

        if not campanhas_correlacionadas:
            continue
        avaliaveis.append({
            "data": acao["data"],
            "titulo": acao["titulo"],
            "status": acao["status"],
            "resumo_acao": acao["corpo"][:800],  # cap — evita mandar markdown gigante (tabelas grandes) pro prompt
            "campanhas_correlacionadas": campanhas_correlacionadas,
        })
    return avaliaveis


# Reaproveita o mesmo componente visual do Diagnóstico Executivo
# (insight_card) — mapeia veredito pra severidade em vez de criar um card
# novo só pra isso.
VEREDITO_SEVERIDADE = {"surtiu_efeito": "ok", "nao_surtiu_efeito": "bad", "cedo_para_avaliar": "warn"}
VEREDITO_LABEL = {"surtiu_efeito": "Resultado positivo", "nao_surtiu_efeito": "Sem resultado", "cedo_para_avaliar": "Cedo para avaliar"}

RESULTADO_ACOES_SCHEMA = {
    "type": "object",
    "properties": {
        "resultados": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "indice": {"type": "integer", "description": "mesmo índice da ação na lista recebida"},
                    "titulo": {"type": "string"},
                    "avaliacao": {"type": "string"},
                    "veredito": {"type": "string", "enum": ["surtiu_efeito", "nao_surtiu_efeito", "cedo_para_avaliar"]},
                },
                "required": ["indice", "titulo", "avaliacao", "veredito"],
            },
        },
    },
    "required": ["resultados"],
}

RESULTADO_ACOES_SYSTEM_PROMPT = (
    "Você é analista de performance de Google Ads da Shibari Brasil. Você recebe ações JÁ "
    "TOMADAS na conta (extraídas de um log manual) junto com os números ATUAIS das campanhas "
    "correlacionadas — a correlação e os números já vêm prontos, não reavalie nem invente "
    "outra campanha. Sua tarefa: para cada ação, escrever uma avaliação de até 70 palavras "
    "dizendo se o resultado esperado (descrito na ação) parece estar se confirmando nos "
    "números atuais, citando os números reais fornecidos. Se os dados não permitirem concluir "
    "com confiança (ex.: ação muito recente, período curto), diga isso explicitamente em vez "
    "de forçar uma conclusão — use veredito 'cedo_para_avaliar' nesses casos. Nunca invente "
    "número que não esteja nos dados fornecidos. Responda em português do Brasil, tom direto "
    "e profissional."
)


def _serializar_acoes_avaliaveis(acoes_avaliaveis):
    linhas = []
    for i, a in enumerate(acoes_avaliaveis):
        linhas.append(
            f'{i}. data={a["data"]} | titulo="{a["titulo"]}" | status={a["status"]} | '
            f'resumo_da_acao="{a["resumo_acao"]}" | campanhas_atuais={a["campanhas_correlacionadas"]}'
        )
    return "\n".join(linhas)


@st.cache_data(ttl=90000)  # mesma lógica de gerar_diagnostico()/gerar_oportunidades() — 1x por dia (BRT)
def gerar_resultado_acoes(acoes_avaliaveis, data_referencia):
    """Transforma as ações avaliáveis em texto (avaliação + veredito) via
    Claude API. Quais ações entram e com quais números já vêm decididos por
    detectar_acoes_avaliaveis() — aqui a IA só redige (ver specs/google-ads.md).
    """
    if not acoes_avaliaveis:
        return []

    client = claude.get_client()
    user = "Ações tomadas e números atuais das campanhas correlacionadas:\n" + _serializar_acoes_avaliaveis(acoes_avaliaveis)
    max_tokens = min(8192, max(1500, len(acoes_avaliaveis) * 350))
    resultado, usage = claude.gerar_texto_estruturado(
        client, RESULTADO_ACOES_SYSTEM_PROMPT, user, RESULTADO_ACOES_SCHEMA, max_tokens=max_tokens,
    )

    textos_por_indice = {item["indice"]: item for item in resultado.get("resultados", [])}
    finais = []
    for i, acao in enumerate(acoes_avaliaveis):
        texto = textos_por_indice.get(i)
        if texto is None:
            continue
        finais.append({
            **acao,
            "avaliacao": texto["avaliacao"],
            "veredito": texto["veredito"],
        })
    return finais


# Limite de sinais por categoria no Diagnóstico Executivo — evita que uma
# conta com muitas campanhas/keywords no mesmo problema gere uma lista
# enorme; prioriza sempre os de maior impacto financeiro dentro da categoria.
LIMITE_SINAIS_POR_CATEGORIA = 3

# Perda de Impression Share considerada relevante o suficiente para virar
# alerta. Não vem de specs/google-ads.md (o spec só documenta a distinção
# budget vs. ranking, sem limiar numérico) — limiar novo, introduzido aqui e
# documentado no spec junto com esta função.
LIMIAR_PERDA_IMPRESSION_SHARE = 0.15

# Ordem de exibição no Diagnóstico Executivo: urgente primeiro, positivo por
# último — mesma leitura do HTML de referência (🔴 → 🟡 → 🟢).
ORDEM_SEVERIDADE = {"bad": 0, "warn": 1, "ok": 2}


def detectar_sinais(dados):
    """Decide quais alertas entram no Diagnóstico Executivo — regra fixa em
    Python, sem IA (ver specs/google-ads.md, seção "Diagnóstico Executivo:
    detecção de sinais"). A Claude API só escreve o texto a partir da lista
    que esta função devolve; não decide severidade nem quais campanhas
    aparecem.

    Retorna uma lista de dicts: {categoria, severidade, campanha, numeros}.
    """
    sinais = []

    df_camp = dados["performance_campanhas"]
    df_camp_ativa = df_camp[df_camp["vl_custo_total"] > 0]

    # ROAS — campanhas no prejuízo (todas; normalmente são poucas) e o
    # melhor performer do período (só 1, como destaque positivo).
    for _, row in df_camp_ativa[df_camp_ativa["vl_roas"].apply(roas_variant) == "bad"].iterrows():
        sinais.append({
            "categoria": "roas", "severidade": "bad", "campanha": row["nm_campanha"],
            "numeros": {
                "custo": float(row["vl_custo_total"]), "receita": float(row["vl_conversoes_total"]),
                "roas": float(row["vl_roas"]), "cpa": float(row["vl_cpa"]) if pd.notna(row["vl_cpa"]) else None,
            },
        })
    melhor = df_camp_ativa.sort_values("vl_roas", ascending=False).head(1)
    if len(melhor) and roas_variant(melhor.iloc[0]["vl_roas"]) == "ok":
        row = melhor.iloc[0]
        sinais.append({
            "categoria": "roas", "severidade": "ok", "campanha": row["nm_campanha"],
            "numeros": {
                "custo": float(row["vl_custo_total"]), "receita": float(row["vl_conversoes_total"]),
                "roas": float(row["vl_roas"]),
            },
        })

    # Orçamento — limitada (≥100%, perdendo oportunidade) e subutilizada
    # (<70%, capada nas de maior budget diário, mais relevante realocar).
    df_orc = dados["orcamento"]
    for _, row in df_orc[df_orc["pct_utilizacao_media"] >= 1.0].iterrows():
        sinais.append({
            "categoria": "orcamento", "severidade": "bad", "campanha": row["nm_campanha"],
            "numeros": {
                "orcamento_diario": float(row["vl_orcamento_diario"]),
                "gasto_medio_diario": float(row["vl_gasto_medio_diario"]),
                "utilizacao": float(row["pct_utilizacao_media"]),
            },
        })
    subutilizadas = df_orc[df_orc["pct_utilizacao_media"] < 0.7] \
        .sort_values("vl_orcamento_diario", ascending=False).head(LIMITE_SINAIS_POR_CATEGORIA)
    for _, row in subutilizadas.iterrows():
        sinais.append({
            "categoria": "orcamento", "severidade": "warn", "campanha": row["nm_campanha"],
            "numeros": {
                "orcamento_diario": float(row["vl_orcamento_diario"]),
                "gasto_medio_diario": float(row["vl_gasto_medio_diario"]),
                "utilizacao": float(row["pct_utilizacao_media"]),
            },
        })

    # Quality Score crítico (<5, meta é ≥7 — ver spec) — capado nas keywords
    # de maior custo entre as críticas, prioridade de ação por impacto.
    df_kw = dados["keywords_top"]
    criticas = df_kw[df_kw["nr_quality_score"] < 5] \
        .sort_values("vl_custo_total", ascending=False).head(LIMITE_SINAIS_POR_CATEGORIA)
    for _, row in criticas.iterrows():
        sinais.append({
            "categoria": "quality_score", "severidade": "bad", "campanha": row["nm_campanha"],
            "numeros": {
                "keyword": row["ds_keyword"], "quality_score": int(row["nr_quality_score"]),
                "custo": float(row["vl_custo_total"]), "cpc": float(row["vl_cpc"]),
            },
        })

    # Impression Share — perda por budget e perda por ranking são
    # diagnósticos opostos (ver spec); capados nas campanhas com maior perda
    # de cada tipo, acima do limiar definido nesta função.
    df_is = dados["impression_share"]
    top_budget = df_is[df_is["pct_perda_budget"] >= LIMIAR_PERDA_IMPRESSION_SHARE] \
        .sort_values("pct_perda_budget", ascending=False).head(LIMITE_SINAIS_POR_CATEGORIA)
    for _, row in top_budget.iterrows():
        sinais.append({
            "categoria": "impression_share_budget", "severidade": "warn", "campanha": row["nm_campanha"],
            "numeros": {
                "impression_share": float(row["pct_impression_share"]),
                "perda_budget": float(row["pct_perda_budget"]),
            },
        })
    top_ranking = df_is[df_is["pct_perda_ranking"] >= LIMIAR_PERDA_IMPRESSION_SHARE] \
        .sort_values("pct_perda_ranking", ascending=False).head(LIMITE_SINAIS_POR_CATEGORIA)
    for _, row in top_ranking.iterrows():
        sinais.append({
            "categoria": "impression_share_ranking", "severidade": "warn", "campanha": row["nm_campanha"],
            "numeros": {
                "impression_share": float(row["pct_impression_share"]),
                "perda_ranking": float(row["pct_perda_ranking"]),
            },
        })

    return sorted(sinais, key=lambda s: ORDEM_SEVERIDADE[s["severidade"]])


LABEL_CATEGORIA_SINAL = {
    "roas": "ROAS",
    "orcamento": "Orçamento (budget vs. gasto)",
    "quality_score": "Quality Score de keyword",
    "impression_share_budget": "Impression Share perdido por orçamento",
    "impression_share_ranking": "Impression Share perdido por ranking (Quality Score/lance)",
}

DIAGNOSTICO_SCHEMA = {
    "type": "object",
    "properties": {
        "insights": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "indice": {"type": "integer", "description": "mesmo índice do sinal na lista recebida"},
                    "titulo": {"type": "string"},
                    "corpo": {"type": "string"},
                    "acoes": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 4},
                },
                "required": ["indice", "titulo", "corpo", "acoes"],
            },
        },
    },
    "required": ["insights"],
}

DIAGNOSTICO_SYSTEM_PROMPT = (
    "Você é analista de performance de Google Ads da Shibari Brasil. Você recebe uma "
    "lista de sinais JÁ DETECTADOS E CLASSIFICADOS por um sistema de regras — não "
    "reavalie, não mude e não invente severidade, categoria ou números diferentes dos "
    "fornecidos. Sua única tarefa é, para cada sinal, escrever: um título curto (até 15 "
    "palavras), um corpo de até 60 palavras explicando o contexto e citando os números "
    "exatos do sinal (nunca invente número que não esteja nos dados fornecidos), e de 3 a "
    "4 ações recomendadas, concretas e específicas ao Google Ads (ex.: pausar campanha, "
    "realocar orçamento, revisar keyword) — não genéricas. Responda em português do "
    "Brasil, tom direto e profissional."
)


def _serializar_sinais(sinais):
    linhas = []
    for i, s in enumerate(sinais):
        categoria = LABEL_CATEGORIA_SINAL.get(s["categoria"], s["categoria"])
        linhas.append(f'{i}. severidade={s["severidade"]} | categoria={categoria} | '
                       f'campanha="{s["campanha"]}" | numeros={s["numeros"]}')
    return "\n".join(linhas)


BRT = timezone(timedelta(hours=-3))  # Brasil não observa horário de verão desde 2019 — offset fixo


def _data_referencia_brt():
    """Chave de cache do diagnóstico — muda só quando o dia (BRT) muda, não
    por hora. Gera insights só na primeira abertura do relatório em cada
    dia; se ninguém abrir, não gasta token nenhum (a função só executa
    quando o Streamlit renderiza a página)."""
    return datetime.now(BRT).date().isoformat()


@st.cache_data(ttl=90000)  # ~25h — rede de segurança; a chave por dia (abaixo) já é quem decide quando regenerar
def gerar_diagnostico(sinais, data_referencia):
    """Transforma os sinais de detectar_sinais() em texto (título, corpo, ações)
    via Claude API. A severidade e quais sinais existem já vêm decididos —
    aqui a IA só redige, nunca reavalia (ver specs/google-ads.md).

    `data_referencia` (ver _data_referencia_brt) é só uma chave de cache: o
    Streamlit já derruba o cache quando `sinais` muda, mas aqui queremos o
    oposto — mesmo que o dado subjacente mude durante o dia (atualização de
    hora em hora), o diagnóstico fica congelado na primeira geração do dia
    até o dia seguinte, para não gerar de novo a cada abertura do relatório.
    """
    if not sinais:
        return []

    client = claude.get_client()
    user = "Sinais detectados nos últimos 30 dias:\n" + _serializar_sinais(sinais)
    # ~300 tokens por sinal (título + corpo + ações + overhead do JSON) —
    # sem isso, com muitos sinais a resposta trunca em max_tokens e o
    # tool_use volta com input vazio (visto na prática com 15 sinais e o
    # default de 1500).
    max_tokens = min(8192, max(1500, len(sinais) * 300))
    resultado, usage = claude.gerar_texto_estruturado(
        client, DIAGNOSTICO_SYSTEM_PROMPT, user, DIAGNOSTICO_SCHEMA, max_tokens=max_tokens,
    )

    textos_por_indice = {item["indice"]: item for item in resultado.get("insights", [])}
    diagnosticos = []
    for i, sinal in enumerate(sinais):
        texto = textos_por_indice.get(i)
        if texto is None:
            continue
        diagnosticos.append({
            **sinal,
            "titulo": texto["titulo"],
            "corpo": texto["corpo"],
            "acoes": texto["acoes"],
        })
    return diagnosticos


# Limite de oportunidades por categoria — mesmo motivo do limite de sinais:
# prioriza sempre o maior impacto (custo) dentro da categoria.
LIMITE_OPORTUNIDADES_POR_CATEGORIA = 3

# Meta de utilização de orçamento (ver Seção 2 do spec) e meta de ROAS (Seção
# 1) reaproveitadas aqui — mesmos limiares, não valores novos.
META_UTILIZACAO_ORCAMENTO = 0.7
META_ROAS = 3.0

# Quantas entradas do log de ações mostrar na seção "Últimas Ações Tomadas"
# — as mais recentes primeiro (mesma ordem do arquivo).
LIMITE_ACOES_RECENTES = 3


def detectar_oportunidades(dados):
    """Decide quais oportunidades entram — regra fixa em Python, sem IA,
    mesmo espírito de detectar_sinais() (ver specs/google-ads.md). Baseado em
    gaps do manual hab-google-ads (sb_marketing_team) detectáveis com o dado
    já disponível hoje — não é "problema" nos dados, é ausência de uma
    prática recomendada.
    """
    oportunidades = []

    # Correspondência Exata — keyword de maior custo sem NENHUMA variante
    # EXACT entre as top keywords (Cap. 4.1 do manual: "termos críticos com
    # alto CPC" merecem controle via correspondência exata).
    df_kw = dados["keywords_top"]
    por_keyword = df_kw.groupby("ds_keyword").agg(
        custo_total=("vl_custo_total", "sum"),
        correspondencias=("ds_correspondencia", lambda s: set(s)),
    ).reset_index()
    sem_exata = por_keyword[~por_keyword["correspondencias"].apply(lambda s: "EXACT" in s)]
    sem_exata = sem_exata.sort_values("custo_total", ascending=False).head(LIMITE_OPORTUNIDADES_POR_CATEGORIA)
    for _, row in sem_exata.iterrows():
        oportunidades.append({
            "categoria": "correspondencia_exata",
            "campanha": None,
            "numeros": {"keyword": row["ds_keyword"], "custo_total": float(row["custo_total"])},
        })

    # Ad Strength — RSA abaixo de "Bom" (Cap. 5.1: meta mínima é "Bom"; de
    # "Pobre" pra "Excelente" o manual cita em média +15% de cliques e
    # conversões). UNSPECIFIED = dado insuficiente, não é sinal de problema.
    # nm_anuncio vem sempre vazio nesta tabela (RSA não tem nome editável) —
    # referencia por grupo de anúncio, não por nome do anúncio. Descarta
    # linhas sem nm_campanha (falha de join já conhecida em rpt_gads_anuncios
    # — poucas linhas órfãs, não é o fan-out de cd_keyword já corrigido).
    df_ads = dados["anuncios"]
    fracos = df_ads[df_ads["ds_forca_anuncio"].isin(["AVERAGE", "POOR"]) & df_ads["nm_campanha"].notna()]
    fracos = fracos.drop_duplicates(subset=["cd_campanha", "cd_grupo_anuncio"])
    fracos = fracos.head(LIMITE_OPORTUNIDADES_POR_CATEGORIA)
    for _, row in fracos.iterrows():
        oportunidades.append({
            "categoria": "ad_strength",
            "campanha": row["nm_campanha"],
            "numeros": {"grupo_anuncio": row["nm_grupo_anuncio"], "forca_atual": row["ds_forca_anuncio"]},
        })

    # Customer Match — campanha de remarketing com ROAS ≥ meta e orçamento
    # subutilizado (Cap. 4.4/7.6: escalar audiência a partir de campanha já
    # comprovadamente eficiente). Só promove quando as DUAS condições valem
    # — se fosse só "orçamento subutilizado" duplicaria o alerta genérico já
    # existente em detectar_sinais() (que sugere reduzir orçamento, não
    # expandir — evita mensagem contraditória no mesmo relatório).
    # Detecção de "é remarketing" é heurística por nome da campanha — o
    # Google Ads não expõe um campo estrutural de tipo remarketing separado
    # de Search/Display neste export.
    df_camp = dados["performance_campanhas"]
    df_orc = dados["orcamento"]
    remarketing = df_camp[
        df_camp["nm_campanha"].str.contains("remarketing", case=False, na=False)
        & (df_camp["vl_roas"] >= META_ROAS)
    ]
    for _, row in remarketing.iterrows():
        orc_row = df_orc[df_orc["nm_campanha"] == row["nm_campanha"]]
        if orc_row.empty or orc_row.iloc[0]["pct_utilizacao_media"] >= META_UTILIZACAO_ORCAMENTO:
            continue
        oportunidades.append({
            "categoria": "customer_match",
            "campanha": row["nm_campanha"],
            "numeros": {
                "roas": float(row["vl_roas"]),
                "utilizacao_orcamento": float(orc_row.iloc[0]["pct_utilizacao_media"]),
                "custo_total": float(row["vl_custo_total"]),
            },
        })

    return oportunidades


OPORTUNIDADE_SCHEMA = {
    "type": "object",
    "properties": {
        "oportunidades": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "indice": {"type": "integer", "description": "mesmo índice do gap na lista recebida"},
                    "titulo": {"type": "string"},
                    "descricao": {"type": "string"},
                    "ganho_esperado": {"type": "string"},
                    "onde_aplicar": {"type": "string"},
                    "como_aplicar": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 4},
                },
                "required": ["indice", "titulo", "descricao", "ganho_esperado", "onde_aplicar", "como_aplicar"],
            },
        },
    },
    "required": ["oportunidades"],
}

OPORTUNIDADE_SYSTEM_PROMPT = (
    "Você é consultor de Google Ads da Shibari Brasil, com acesso a um manual interno de boas práticas "
    "(hab-google-ads). Você recebe uma lista de GAPS JÁ DETECTADOS por um sistema de regras — não "
    "reavalie, não invente novos gaps nem números diferentes dos fornecidos. Para cada gap, escreva uma "
    "recomendação prática citando o número real do gap. Use estritamente estas referências do manual, sem "
    "inventar outras:\n"
    "- categoria=correspondencia_exata (Cap. 4.1): keyword de alto custo/CPC crítico deve ganhar uma "
    "variante de correspondência Exata pra dar controle sobre o termo, sem abandonar a correspondência "
    "ampla existente.\n"
    "- categoria=ad_strength (Cap. 5.1): meta é Ad Strength mínimo \"Bom\"; o manual indica que subir de "
    "\"Ruim\" pra \"Excelente\" gera em média +15% de cliques e conversões — cite esse +15% só nesta "
    "categoria. Ação prática: adicionar mais variações de título e descrição no RSA.\n"
    "- categoria=customer_match (Cap. 4.4/7.6): campanha de remarketing com ROAS alto e orçamento "
    "subutilizado é boa candidata a Customer Match — subir lista de e-mails de clientes pra ampliar a "
    "audiência sem perder a eficiência já comprovada.\n"
    "Em 'ganho_esperado', só cite número de impacto (%, R$) quando a categoria já fornecer essa referência "
    "(o +15% do ad_strength); nas outras categorias escreva qualitativamente (ex.: 'mais controle de custo "
    "por clique'), nunca invente percentual. Responda em português do Brasil, tom direto e profissional."
)


def _serializar_oportunidades(oportunidades):
    linhas = []
    for i, o in enumerate(oportunidades):
        campanha = f' | campanha="{o["campanha"]}"' if o["campanha"] else ""
        linhas.append(f'{i}. categoria={o["categoria"]}{campanha} | numeros={o["numeros"]}')
    return "\n".join(linhas)


@st.cache_data(ttl=90000)  # mesma lógica de gerar_diagnostico() — 1x por dia (BRT), não por hora
def gerar_oportunidades(oportunidades, data_referencia):
    """Transforma os gaps de detectar_oportunidades() em texto (título,
    descrição, ganho esperado, onde/como aplicar) via Claude API. Quais
    gaps existem já vêm decididos — aqui a IA só redige, citando o capítulo
    do manual hab-google-ads (ver specs/google-ads.md).
    """
    if not oportunidades:
        return []

    client = claude.get_client()
    user = "Gaps detectados na conta:\n" + _serializar_oportunidades(oportunidades)
    max_tokens = min(8192, max(1500, len(oportunidades) * 350))
    resultado, usage = claude.gerar_texto_estruturado(
        client, OPORTUNIDADE_SYSTEM_PROMPT, user, OPORTUNIDADE_SCHEMA, max_tokens=max_tokens,
    )

    textos_por_indice = {item["indice"]: item for item in resultado.get("oportunidades", [])}
    finais = []
    for i, o in enumerate(oportunidades):
        texto = textos_por_indice.get(i)
        if texto is None:
            continue
        finais.append({
            **o,
            "titulo": texto["titulo"],
            "descricao": texto["descricao"],
            "ganho_esperado": texto["ganho_esperado"],
            "onde_aplicar": texto["onde_aplicar"],
            "como_aplicar": texto["como_aplicar"],
        })
    return finais


def render():
    inject_css()

    with st.spinner("Carregando dados do BigQuery..."):
        try:
            dados = carregar_dados()
            r = dados["resumo_conta"].iloc[0]
            periodo_ini = pd.to_datetime(r["dt_inicio_periodo"]).strftime("%d/%m/%Y")
            periodo_fim = pd.to_datetime(r["dt_fim_periodo"]).strftime("%d/%m/%Y")
            receita = r["vl_conversoes_total"]
            custo = r["vl_custo_total"]
            roi = (receita - custo) / custo if custo else 0

            # cada gráfico/tabela ordena pela sua própria métrica principal
            # (maior pro menor) — por isso são views separadas, não uma só.
            df_camp = dados["performance_campanhas"]
            n_campanhas = df_camp["nm_campanha"].nunique()
            n_dias = (pd.to_datetime(r["dt_fim_periodo"]) - pd.to_datetime(r["dt_inicio_periodo"])).days + 1
            retorno_liquido = receita - custo
            n_campanhas_com_compra = int((df_camp["qt_conversoes_total"] > 0).sum())
            cpa_validos = df_camp["vl_cpa"].dropna()

            df_roas = df_camp.sort_values("vl_roas", ascending=False)
            nomes_roas = [nome_curto(n) for n in df_roas["nm_campanha"]]

            df_receita = df_camp.sort_values("vl_conversoes_total", ascending=False)
            nomes_receita = [nome_curto(n) for n in df_receita["nm_campanha"]]

            df_investido = df_camp.sort_values("vl_custo_total", ascending=False)
            nomes_investido = [nome_curto(n) for n in df_investido["nm_campanha"]]

            # Custo do mês calendário atual (BRT) — diferente da janela de 30
            # dias corridos usada no resto do relatório (ver specs/google-ads.md).
            df_tend_mes = dados["tendencia_diaria"].copy()
            df_tend_mes["dt_data"] = pd.to_datetime(df_tend_mes["dt_data"])
            hoje_brt = datetime.now(BRT).date()
            filtro_mes_atual = (df_tend_mes["dt_data"].dt.year == hoje_brt.year) & (df_tend_mes["dt_data"].dt.month == hoje_brt.month)
            custo_mes_atual = df_tend_mes.loc[filtro_mes_atual, "vl_custo"].sum()
            n_dias_mes_atual = int(filtro_mes_atual.sum())

            st.html(f"""
            <div class="report-header">
              <div>
                <div class="report-brand">shibari brasil · tráfego pago</div>
                <div class="report-title">Google Ads <span>—</span> Relatório</div>
                <div class="report-meta">Dados via BigQuery · {bq.PROJECT}</div>
              </div>
              <div class="report-badge">
                Período: <strong>{periodo_ini} → {periodo_fim}</strong><br>
                Atualizado de hora em hora
              </div>
            </div>
            """)

            # ═══ DIAGNÓSTICO EXECUTIVO ═══
            # Try/except isolado do try/except geral desta função: se a
            # Claude API falhar, o resto do relatório (100% dado, sem
            # dependência de IA) continua funcionando normalmente.
            try:
                sinais = detectar_sinais(dados)
                diagnosticos = gerar_diagnostico(sinais, _data_referencia_brt())
            except Exception:
                diagnosticos = None
            if diagnosticos is not None:
                section_title("Diagnóstico Executivo")
                if diagnosticos:
                    render_insights([
                        insight_card(
                            d["severidade"], LABEL_CATEGORIA_SINAL.get(d["categoria"], d["categoria"]),
                            d["titulo"], d["corpo"], d["acoes"],
                        )
                        for d in diagnosticos
                    ])
                else:
                    note("Nenhum alerta relevante detectado nos últimos 30 dias pelas regras atuais (ROAS, orçamento, Quality Score, Impression Share).")
            else:
                note("Diagnóstico Executivo indisponível no momento — o restante do relatório não é afetado.")

            # ═══ OPORTUNIDADES ═══
            # Mesmo padrão de try/except isolado do Diagnóstico Executivo.
            try:
                gaps = detectar_oportunidades(dados)
                oportunidades = gerar_oportunidades(gaps, _data_referencia_brt())
            except Exception:
                oportunidades = None
            if oportunidades is not None:
                section_title("Oportunidades")
                if oportunidades:
                    render_opportunities([
                        opportunity_card(
                            o["titulo"], o["descricao"], o["ganho_esperado"], o["onde_aplicar"], o["como_aplicar"],
                        )
                        for o in oportunidades
                    ])
                else:
                    note("Nenhuma oportunidade relevante detectada pelas regras atuais (correspondência exata, Ad Strength, Customer Match).")
            else:
                note("Oportunidades indisponível no momento — o restante do relatório não é afetado.")

            # ═══ 1 — VISÃO GERAL DA CONTA ═══
            section_title("1 — Visão Geral da Conta")
            render_cards([
                card("Custo Total", f"R$ {custo:,.2f}", f"{n_campanhas} campanhas · {n_dias} dias", variant="neutral"),
                card("Custo — Mês Atual", f"R$ {custo_mes_atual:,.2f}",
                     f"{n_dias_mes_atual} dias registrados" if n_dias_mes_atual else "sem dados ainda este mês",
                     "mês calendário (BRT) — não é a janela de 30 dias corridos do resto do relatório", variant="neutral"),
                card("Receita Gerada", f"R$ {receita:,.2f}", f"{r['qt_conversoes_total']:,.0f} compras confirmadas", variant="neutral"),
                card("ROI", f"{roi*100:.0f}%", f"R$ {retorno_liquido:,.2f} de retorno líquido",
                     "meta: > 100% · (receita − gasto) ÷ gasto", variant="ok" if roi >= 1 else ("warn" if roi >= 0 else "bad")),
                card("ROAS Médio", f"{r['vl_roas']:.2f}×", f"{n_campanhas_com_compra} de {n_campanhas} campanhas com compra",
                     "meta: 3–5× · mínimo: 2×", variant=roas_variant(r["vl_roas"])),
                card("CPA Médio", f"R$ {r['vl_cpa']:,.2f}",
                     f"variação: R$ {cpa_validos.min():,.2f} → R$ {cpa_validos.max():,.2f}" if len(cpa_validos) else "sem compras no período",
                     "definir meta de CPA por produto", variant="neutral"),
            ])
            render_cards([
                card("Impressões", f"{int(r['qt_impressoes_total']):,}", variant="neutral"),
                card("Cliques", f"{int(r['qt_cliques_total']):,}", variant="neutral"),
                card("CTR", f"{r['pct_ctr']*100:.2f}%", "benchmark: 2–6%+", variant="neutral"),
                card("CPC Médio", f"R$ {r['vl_cpc']:,.2f}", variant="neutral"),
            ])
            note("<strong>Como ler:</strong> ROI considera só o gasto de mídia — não inclui custo do produto. "
                 "ROAS abaixo de 2× indica campanha no prejuízo considerando margem; entre 2× e 3× está na zona de atenção; acima de 3× está saudável.")

            # ═══ 2 — ORÇAMENTO ═══
            section_title("2 — Orçamento: Budget vs. Gasto Médio Diário")
            with st.container(border=True):
                df_orc = dados["orcamento"].sort_values("vl_orcamento_diario", ascending=False)
                nomes_orc = [nome_curto(n) for n in df_orc["nm_campanha"]]
                fig = go.Figure()
                # ordenado do maior pro menor de cima pra baixo: dado já vem
                # decrescente, e o eixo precisa ser invertido (ver nota em
                # ROAS/Investido sobre a ordem de desenho do Plotly).
                fig.add_bar(name="Budget diário", y=nomes_orc, x=df_orc["vl_orcamento_diario"], orientation="h", marker_color=SKIN,
                            customdata=df_orc["nm_campanha"], hovertemplate="<b>%{customdata}</b><br>Budget diário: R$ %{x:,.2f}<extra></extra>")
                fig.add_bar(name="Gasto médio diário", y=nomes_orc, x=df_orc["vl_gasto_medio_diario"], orientation="h", marker_color=PLUM,
                            customdata=df_orc["nm_campanha"], hovertemplate="<b>%{customdata}</b><br>Gasto médio: R$ %{x:,.2f}<extra></extra>")
                plotly_layout(fig, barmode="group", height=300, xaxis=dict(gridcolor=GRID, tickprefix="R$"),
                              yaxis=dict(gridcolor=GRID, autorange="reversed"))
                st.plotly_chart(fig, use_container_width=True)

                tabela_orc = pd.DataFrame({
                    "Campanha": df_orc["nm_campanha"],
                    "Status": df_orc["ds_status_campanha"],
                    "Budget diário": df_orc["vl_orcamento_diario"].apply(lambda v: f"R$ {v:,.2f}"),
                    "Gasto médio": df_orc["vl_gasto_medio_diario"].apply(lambda v: f"R$ {v:,.2f}"),
                    "Utilização": df_orc["pct_utilizacao_media"],
                    "Gasto total": df_orc["vl_gasto_total"].apply(lambda v: f"R$ {v:,.2f}"),
                })
                styled_orc = style_color(
                    tabela_orc.style,
                    lambda v: f"color: {BAD}; font-weight:700" if v >= 1.0 else (f"color: {WARN}; font-weight:700" if v >= 0.7 else f"color: {TAUPE}"),
                    subset=["Utilização"]
                ).format({"Utilização": "{:.0%}"})
                st.dataframe(styled_orc, hide_index=True, use_container_width=True)
                note("<strong>Como ler:</strong> Utilização ≥ 100% = campanha <strong>limitada por orçamento</strong> — está perdendo impressões/cliques que poderiam converter; "
                     "70–99% é normal; abaixo de 70% = orçamento subutilizado (pode ter espaço para aumentar lance ou indicar audiência pequena).")

            # ═══ 3 — PERFORMANCE POR CAMPANHA ═══
            section_title("3 — Performance por Campanha")
            col1, col2 = st.columns(2)

            with col1:
                with st.container(border=True):
                    st.html('<div class="c-label" style="margin-bottom:10px">ROAS por Campanha</div>')
                    bench_row([("Meta", "3–5×"), ("Mínimo", "2×"), ("Crítico", "< 2×")])
                    fig = go.Figure()
                    cores = [{"ok": OK_BG, "warn": WARN_BG, "bad": BAD}[roas_variant(v)] for v in df_roas["vl_roas"]]
                    fig.add_bar(y=nomes_roas, x=df_roas["vl_roas"], orientation="h", marker_color=cores,
                                text=[f"{v:.2f}×" for v in df_roas["vl_roas"]], textposition="outside",
                                customdata=df_roas["nm_campanha"],
                                hovertemplate="<b>%{customdata}</b><br>ROAS: %{x:.2f}×<extra></extra>")
                    plotly_layout(fig, showlegend=False, height=300, xaxis=dict(gridcolor=GRID, ticksuffix="×"),
                                  yaxis=dict(gridcolor=GRID, autorange="reversed"))
                    st.plotly_chart(fig, use_container_width=True)
                    note("Barras vermelhas (ROAS abaixo de 2×) estão perdendo dinheiro considerando margem mínima.")

            with col2:
                with st.container(border=True):
                    st.html('<div class="c-label" style="margin-bottom:10px">Investido vs. Receita por Campanha</div>')
                    bench_row([("Barras iguais", "= ROAS 1× (empate)")])
                    fig = go.Figure()
                    # Plotly desenha o primeiro trace embaixo e o segundo em cima
                    # num grupo de barra horizontal — Receita entra primeiro para
                    # que Investido apareça por cima (ordem de leitura: Investido, Receita).
                    fig.add_bar(name="Receita", y=nomes_investido, x=df_investido["vl_conversoes_total"], orientation="h", marker_color=SCARLET,
                                customdata=df_investido["nm_campanha"], hovertemplate="<b>%{customdata}</b><br>Receita: R$ %{x:,.2f}<extra></extra>")
                    fig.add_bar(name="Investido", y=nomes_investido, x=df_investido["vl_custo_total"], orientation="h", marker_color=PLUM,
                                customdata=df_investido["nm_campanha"], hovertemplate="<b>%{customdata}</b><br>Investido: R$ %{x:,.2f}<extra></extra>")
                    plotly_layout(fig, barmode="group", height=300, xaxis=dict(gridcolor=GRID, tickprefix="R$"),
                                  yaxis=dict(gridcolor=GRID, autorange="reversed"),
                                  legend=dict(orientation="h", y=1.12, font=dict(size=11), traceorder="reversed"))
                    st.plotly_chart(fig, use_container_width=True)
                    note("Quando a barra de receita (vermelha) é menor que a de investido (roxa), a campanha está no prejuízo no período.")

            with st.container(border=True):
                df_tab = df_receita.copy()
                df_tab["pct_conv_rate"] = df_tab["qt_conversoes_total"] / df_tab["qt_cliques_total"]
                tabela = pd.DataFrame({
                    "Campanha": df_tab["nm_campanha"],
                    "Tipo": df_tab["ds_tipo_canal"],
                    "Status": df_tab["ds_status_campanha"],
                    "Investido": df_tab["vl_custo_total"].apply(lambda v: f"R$ {v:,.2f}"),
                    "Impr.": df_tab["qt_impressoes_total"].apply(lambda v: f"{int(v):,}"),
                    "Cliques": df_tab["qt_cliques_total"].apply(lambda v: f"{int(v):,}"),
                    "CTR": df_tab["pct_ctr"].apply(lambda v: f"{v*100:.2f}%"),
                    "CPC": df_tab["vl_cpc"].apply(lambda v: f"R$ {v:,.2f}"),
                    "Conv. Rate": df_tab["pct_conv_rate"].apply(lambda v: f"{v*100:.2f}%" if v == v else "—"),
                    "Conversões": df_tab["qt_conversoes_total"].apply(lambda v: f"{v:.1f}"),
                    "CPA": df_tab["vl_cpa"].apply(lambda v: f"R$ {v:,.2f}" if v == v else "—"),
                    "Receita": df_tab["vl_conversoes_total"].apply(lambda v: f"R$ {v:,.2f}"),
                    "ROAS": df_tab["vl_roas"],
                })
                styled = style_color(
                    tabela.style,
                    lambda v: f"color: {OK}; font-weight:700" if v >= 3 else (f"color: {WARN}; font-weight:700" if v >= 2 else f"color: {BAD}; font-weight:700"),
                    subset=["ROAS"]
                ).format({"ROAS": "{:.2f}×"})
                st.dataframe(styled, hide_index=True, use_container_width=True)
                note("<strong>Benchmarks:</strong> CTR Search &gt;2% aceitável / &gt;6% bom · Conv. Rate e-commerce &gt;1% mínimo / &gt;3% bom · "
                     "ROAS mínimo 2× / meta 3–5×.")

            # ═══ 4 — TENDÊNCIA DIÁRIA ═══
            section_title("4 — Tendência Diária")
            with st.container(border=True):
                st.html('<div class="c-label" style="margin-bottom:10px">Gasto diário e Cliques — últimos 30 dias</div>')
                df_tend = dados["tendencia_diaria"].sort_values("dt_data")
                fig = go.Figure()
                fig.add_trace(go.Scatter(x=df_tend["dt_data"], y=df_tend["vl_custo"], name="Gasto (R$)",
                                          line=dict(color=SCARLET, width=2.5, shape="spline"), fill="tozeroy",
                                          fillcolor="rgba(209,15,47,0.06)", yaxis="y"))
                fig.add_trace(go.Scatter(x=df_tend["dt_data"], y=df_tend["qt_cliques"], name="Cliques",
                                          line=dict(color=PLUM, width=2, dash="dash"), yaxis="y2"))
                plotly_layout(fig, height=300, hovermode="x unified",
                              yaxis=dict(gridcolor=GRID, tickprefix="R$", title=None),
                              yaxis2=dict(overlaying="y", side="right", showgrid=False, title=None))
                st.plotly_chart(fig, use_container_width=True)
                note("Gasto subindo com cliques estáveis = CPC subindo (leilão mais competitivo). Cliques caindo com gasto constante geralmente indica perda de impression share.")

            # ═══ 5 — CONVERSÕES POR TIPO ═══
            section_title("5 — Conversões por Tipo")
            with st.container(border=True):
                col3, col4 = st.columns([2, 3])
                df_conv = dados["conversoes_tipo"].sort_values("qt_conversoes_total", ascending=False)
                with col3:
                    fig = go.Figure(go.Pie(labels=df_conv["nm_acao_conversao"], values=df_conv["qt_conversoes_total"],
                                            hole=0.5, marker_colors=[PLUM, SCARLET, WARN_BG, TAUPE, SKIN],
                                            textinfo="percent"))
                    plotly_layout(fig, height=220, showlegend=True, legend=dict(orientation="v", y=0.5, font=dict(size=10)))
                    st.plotly_chart(fig, use_container_width=True)
                with col4:
                    variante_cor = {"ok": OK, "warn": WARN, "muted": TAUPE}
                    variante_por_tipo = {"Primária": "ok", "Secundária": "warn", "Micro": "muted"}
                    tabela_conv = pd.DataFrame({
                        "Ação": df_conv["nm_acao_conversao"],
                        "Tipo": [tipo_conversao(c)[0] for c in df_conv["ds_categoria_conversao"]],
                        "Qtd": df_conv["qt_conversoes_total"].apply(lambda v: f"{v:.1f}"),
                        "Valor": df_conv["vl_conversoes_total"].apply(lambda v: f"R$ {v:,.2f}"),
                    })
                    styled_conv = style_color(
                        tabela_conv.style,
                        lambda t: f"color: {variante_cor[variante_por_tipo[t]]}; font-weight:700",
                        subset=["Tipo"]
                    )
                    st.dataframe(styled_conv, hide_index=True, use_container_width=True)
                note("<strong>Primária</strong> = compra real. <strong>Secundária</strong> = etapa do funil (carrinho/checkout). "
                     "<strong>Micro</strong> = ex. visualização de página — não é receita real e não entra no cálculo de ROAS.")

            # ═══ 6 — DIAGNÓSTICO DE LEILÃO E CRIATIVOS ═══
            section_title("6 — Diagnóstico de Leilão e Criativos")
            with st.container(border=True):
                st.html('<div class="c-label" style="margin-bottom:10px">Top Keywords por Gasto</div>')
                bench_row([("QS meta", "≥ 7"), ("Aceitável", "5–6"), ("Crítico", "< 5"), ("Perfeito", "QS 10")])
                df_kw = dados["keywords_top"].sort_values("nr_quality_score", ascending=False)
                tabela_kw = pd.DataFrame({
                    "Keyword": [f"{k} ★" if qs == 10 else k for k, qs in zip(df_kw["ds_keyword"], df_kw["nr_quality_score"])],
                    "Correspondência": df_kw["ds_correspondencia"],
                    "Campanha": df_kw["nm_campanha"],
                    "Grupo": df_kw["nm_grupo_anuncio"],
                    "Custo": df_kw["vl_custo_total"].apply(lambda v: f"R$ {v:,.2f}"),
                    "Cliques": df_kw["qt_cliques_total"].apply(lambda v: f"{int(v):,}"),
                    "CTR": df_kw["pct_ctr"].apply(lambda v: f"{v*100:.2f}%"),
                    "CPC": df_kw["vl_cpc"].apply(lambda v: f"R$ {v:,.2f}"),
                    "Conversões": df_kw["qt_conversoes_total"].apply(lambda v: f"{v:.1f}"),
                    "CPA": df_kw["vl_cpa"].apply(lambda v: f"R$ {v:,.2f}" if v == v else "—"),
                    "QS": df_kw["nr_quality_score"],
                    "CTR Previsto": df_kw["ds_ctr_previsto"],
                    "Relevância": df_kw["ds_relevancia_anuncio"],
                    "Exp. LP": df_kw["ds_experiencia_lp"],
                })
                styled_kw = style_color(
                    tabela_kw.style,
                    lambda v: f"color: {OK}; font-weight:700" if v >= 9 else (f"color: {WARN}; font-weight:700" if v >= 7 else f"color: {BAD}; font-weight:700"),
                    subset=["QS"]
                ).apply(
                    lambda row: ["background-color: rgba(76,175,130,0.08)"] * len(row) if row["QS"] == 10 else [""] * len(row),
                    axis=1
                )
                st.dataframe(styled_kw, hide_index=True, use_container_width=True)
                note("★ = Quality Score perfeito (10). QS abaixo de 5 normalmente eleva o CPC e reduz a posição no leilão — priorize melhorar anúncio/landing page dessas keywords antes de aumentar lance.")

            with st.container(border=True):
                st.html('<div class="c-label" style="margin-bottom:10px">Impression Share por Campanha</div>')
                df_is = dados["impression_share"].sort_values("pct_impression_share", ascending=False)
                fig = go.Figure()
                fig.add_bar(name="IS conquistado", y=df_is["nm_campanha"], x=df_is["pct_impression_share"], orientation="h", marker_color=PLUM)
                fig.add_bar(name="Perda por budget", y=df_is["nm_campanha"], x=df_is["pct_perda_budget"], orientation="h", marker_color="#e0b0ff")
                fig.add_bar(name="Perda por ranking", y=df_is["nm_campanha"], x=df_is["pct_perda_ranking"], orientation="h", marker_color="#f5e6ff")
                plotly_layout(fig, barmode="stack", height=300, xaxis=dict(gridcolor=GRID, tickformat=".0%"))
                st.plotly_chart(fig, use_container_width=True)
                note("<strong>Perda por budget</strong> se resolve aumentando orçamento. <strong>Perda por ranking</strong> se resolve melhorando Quality Score ou lance — são diagnósticos opostos, não confundir.")

            with st.container(border=True):
                st.html('<div class="c-label" style="margin-bottom:10px">Anúncios Ativos</div>')
                df_ads = dados["anuncios"]
                st.dataframe(
                    df_ads[["nm_campanha", "nm_grupo_anuncio", "ds_tipo_anuncio", "nm_anuncio",
                            "ds_forca_anuncio", "ds_status_aprovacao", "ds_url_final"]]
                    .rename(columns={
                        "nm_campanha": "Campanha", "nm_grupo_anuncio": "Grupo", "ds_tipo_anuncio": "Tipo",
                        "nm_anuncio": "Nome", "ds_forca_anuncio": "Força", "ds_status_aprovacao": "Aprovação",
                        "ds_url_final": "URL",
                    }),
                    hide_index=True, use_container_width=True
                )
                note("Anúncios com força \"Ruim\" ou \"Regular\" perdem posição no leilão — adicionar mais variações de título/descrição costuma elevar para \"Boa\"/\"Excelente\".")

            # ═══ ÚLTIMAS AÇÕES TOMADAS ═══
            # Try/except isolado — lê um arquivo local (content/acoes-google-ads.md),
            # não depende de BigQuery nem da Claude API; se faltar ou vier
            # malformado, o resto do relatório não é afetado.
            try:
                acoes = carregar_acoes()
            except Exception:
                acoes = None
            if acoes:
                section_title("Últimas Ações Tomadas")
                for a in acoes[:LIMITE_ACOES_RECENTES]:
                    with st.container(border=True):
                        status_html = f' <span class="tag t-muted">{a["status"]}</span>' if a["status"] else ""
                        st.html(f'<div class="c-label" style="margin-bottom:10px">{a["data"]} — {a["titulo"]}{status_html}</div>')
                        st.markdown(a["corpo"])

            # ═══ RESULTADO DAS ÚLTIMAS AÇÕES ═══
            # Try/except isolado, mesmo padrão do Diagnóstico Executivo e
            # Oportunidades. Depende de `acoes` ter carregado com sucesso.
            try:
                if acoes:
                    acoes_avaliaveis = detectar_acoes_avaliaveis(acoes, dados)
                    resultados_acoes = gerar_resultado_acoes(acoes_avaliaveis, _data_referencia_brt())
                else:
                    resultados_acoes = []
            except Exception:
                resultados_acoes = None
            if resultados_acoes is not None:
                section_title("Resultado das Últimas Ações")
                if resultados_acoes:
                    render_insights([
                        insight_card(
                            VEREDITO_SEVERIDADE.get(r["veredito"], "warn"), VEREDITO_LABEL.get(r["veredito"], r["veredito"]),
                            r["titulo"], r["avaliacao"], ["Ver detalhes completos em \"Últimas Ações Tomadas\", acima"],
                        )
                        for r in resultados_acoes
                    ])
                else:
                    note("Nenhuma ação recente com dado suficiente pra avaliar resultado (ver \"Últimas Ações Tomadas\" acima).")
            else:
                note("Resultado das Últimas Ações indisponível no momento — o restante do relatório não é afetado.")

        except Exception as e:
            st.error(f"Erro ao carregar dados: {e}")
