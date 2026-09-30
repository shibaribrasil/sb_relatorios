"""Relatório Giro Semanal — Shibari Brasil (camada semanal).

Regras, limiares e definição de cada leitura: ver specs/giro-semanal.md. Cruza, na mesma janela de dias, os três elos do giro:
mídia (Google Ads: custo, cliques, leilão) -> acesso (GA4: sessões, engajamento, funil) -> venda (tb_pedido: pedidos, faturamento, margem),
e escreve a interpretação em cima dos números ("norteadores"): onde o giro está travando e o que olhar primeiro.

Nenhuma regra de negócio nova: números vêm das mesmas fontes de Vendas da Semana, Google Ads e Tráfego (razão = Σ ÷ Σ). Os limiares que
transformam número em alerta são de APRESENTAÇÃO e estão nas constantes abaixo (e no spec). A semana em andamento é o acumulado até o último dia
fechado, comparado aos mesmos dias da semana anterior e à média dessas mesmas janelas nas 4 semanas anteriores.
"""
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from common import semana as sm
from common.design import (
    COLORS, METRIC_COLORS, CATEGORICAL, inject_css, card, render_cards, section_title, note, plotly_layout, insight_card, render_insights, brl, pct,
)
from common.ga4 import carregar_ga4, INICIO_GA4
from reports import google_ads as ga
from reports.origem_campanha import carregar_campanhas
from reports.vendas_margem import carregar_dados as carregar_vendas, _hoje_brt, _somas

SEMANAS_BASE = 4              # média das N semanas anteriores (mesmos dias) como "normal" da loja
SEMANAS_GRAFICO = 8
AMOSTRA_PEDIDOS = 5           # menos pedidos que isso na janela: conversão, ticket, ROAS e margem oscilam demais para virar alerta
MIN_CLIQUES = 30              # menos cliques que isso na janela: leituras de tráfego/rastreio ainda são ruído
MIN_SESSOES = 200
QUEDA_ALERTA, QUEDA_URGENTE = 0.25, 0.40       # variação relativa contra o normal que vira "atenção" / "urgente" (tráfego, conversão)
TOL_LEVE, TOL_FORTE = 0.10, 0.25               # semáforo da tabela elo a elo (pior em 10–25% = âmbar; acima disso = vermelho)
RASTREIO_MIN, RASTREIO_MAX = 0.7, 1.3          # sessões de Google pago ÷ cliques do Ads
PERDA_RANK_ALERTA, PERDA_ORC_ALERTA = 0.50, 0.20
ENG_MIN = 0.50                                 # % de sessões engajadas de Google pago abaixo do qual o tráfego é pouco qualificado
FRETE_GRATIS = 400                             # regra da loja: frete grátis acima de R$ 400
CONCENTRACAO = 0.65                            # parcela dos pedidos vinda de Google pago acima da qual há dependência de um canal
PARIDADE_MIN, PARIDADE_MAX = 0.6, 1.5          # compras do GA4 ÷ pedidos reais (o GA4 dispara na criação do pedido: inclui não pagos/cancelados, então passa de 1)
MUDANCA_ESTRUTURA = pd.Timestamp("2026-08-31")   # Topo Funil pausada e campanhas reestruturadas: semanas anteriores não são "normal" para as de depois
GA4_DEFASAGEM_DIAS = 2                         # o GA4 reprocessa a atribuição dos últimos ~2 dias


# ═══ COMPONENTES E DERIVADOS ═══════════════════════════════════════════════════════════════════════════════════════════════════

def _grupo_origem(fonte, meio):
    f, m = (fonte.lower() if isinstance(fonte, str) else ""), (meio.lower() if isinstance(meio, str) else "")
    if not f and not m:
        return "Sem origem ainda"
    if f == "google" and m == "cpc":
        return "Google pago"
    if f == "google" and m == "organic":
        return "Google orgânico"
    if f == "(direct)":
        return "Direto"
    if f in ("instagram", "facebook", "ig", "fb") or m in ("paid_social", "social"):
        return "Instagram/Meta"
    return "Outros"


def componentes(ctx, ini, fim):
    """Somas brutas da janela [ini, fim] em cada elo. Só soma: as razões saem de `derivados`."""
    a = ga.somas_ads(ctx["perf"], ini, fim)
    gx = sm.entre(ctx["canal"], "dt_data", ini, fim)
    cpc = gx[(gx["ds_canal_fonte"].fillna("").str.lower() == "google") & (gx["ds_canal_meio"].fillna("").str.lower() == "cpc")]
    fx = sm.entre(ctx["funil"], "dt_data", ini, fim).groupby("ds_etapa_funil")["qt_ocorrencias"].sum()
    v = sm.entre(ctx["vendas"], "dt_pedido", ini, fim)
    s = _somas(v)
    gp = ga.google_pago(ctx["vendas"], ini, fim)
    return {"custo": a["custo"], "cliques": a["cliques"], "impr": a["impr"], "compras_ads": a["compras"], "valor_ads": a["valor"],
            "sess": float(gx["qt_sessoes"].sum()), "sess_eng": float(gx["qt_sessoes_engajadas"].sum()),
            "sess_cpc": float(cpc["qt_sessoes"].sum()), "sess_cpc_eng": float(cpc["qt_sessoes_engajadas"].sum()),
            "view_item": float(fx.get("view_item", 0.0)), "add_cart": float(fx.get("add_to_cart", 0.0)), "checkout": float(fx.get("begin_checkout", 0.0)), "purchase_ga4": float(fx.get("purchase", 0.0)),
            "pedidos": float(s["pedidos"]), "fat": s["vl_liquido_item"], "mc": s["vl_margem_contribuicao"],
            "ped_gp": float(gp["pedidos"]), "fat_gp": gp["fat"], "mc_gp": gp["mc"], "novos_gp": float(gp["novos"])}


def _media(comps):
    """Média das componentes de várias janelas de mesmo tamanho: razões da média = razões da soma (Σ ÷ Σ)."""
    return {k: sum(c[k] for c in comps) / len(comps) for k in comps[0]}


def _d(a, b):
    return (a / b) if b else None


def derivados(c):
    return {
        "custo": c["custo"], "cliques": c["cliques"], "cpc": _d(c["custo"], c["cliques"]),
        "sess": c["sess"], "sess_cpc": c["sess_cpc"], "rastreio": _d(c["sess_cpc"], c["cliques"]),
        "eng": _d(c["sess_eng"], c["sess"]), "eng_cpc": _d(c["sess_cpc_eng"], c["sess_cpc"]),
        "view_sess": _d(c["view_item"], c["sess"]), "cart_view": _d(c["add_cart"], c["view_item"]),
        "chk_cart": _d(c["checkout"], c["add_cart"]), "ped_chk": _d(c["pedidos"], c["checkout"]),
        "pedidos": c["pedidos"], "conv": _d(c["pedidos"], c["sess"]), "ticket": _d(c["fat"], c["pedidos"]),
        "fat": c["fat"], "mc": c["mc"], "mc_pos": c["mc"] - c["custo"],
        "roas": _d(c["fat_gp"], c["custo"]), "mc_custo": _d(c["mc_gp"], c["custo"]), "mer": _d(c["fat"], c["custo"]),
        "ped_gp": c["ped_gp"], "custo_ped": _d(c["custo"], c["ped_gp"]), "mc_ped_gp": _d(c["mc_gp"], c["ped_gp"]),
        "part_gp": _d(c["ped_gp"], c["pedidos"]), "paridade": _d(c["purchase_ga4"], c["pedidos"]),
    }


def _rel(cur, ref):
    if cur is None or ref is None or ref == 0:
        return None
    return (cur - ref) / abs(ref)


def _f_var(r):
    return "—" if r is None else f"{'+' if r >= 0 else '−'}{abs(r) * 100:.0f}%"


f_brl = lambda v: brl(v, 0) if v is not None else "—"
f_brl2 = lambda v: brl(v) if v is not None else "—"
f_int = lambda v: f"{v:.0f}" if v is not None else "—"
f_pct = lambda v: pct(v, 1)
f_pct0 = lambda v: pct(v, 0)
f_x = lambda v: (f"{v:.1f}×".replace(".", ",") if v is not None else "—")
f_num2 = lambda v: (f"{v:.2f}".replace(".", ",") if v is not None else "—")


# ═══ TABELA ELO A ELO ══════════════════════════════════════════════════════════════════════════════════════════════════════════

# (bloco, rótulo, chave, formatador, melhor: "up" | "down" | None, guarda: função(componentes) -> bool que diz se há amostra para o semáforo)
LINHAS = [
    ("1 · Mídia", "Investimento Google Ads", "custo", f_brl, None, lambda c: True),
    ("1 · Mídia", "Cliques no Ads", "cliques", f_int, "up", lambda c: c["cliques"] >= MIN_CLIQUES),
    ("1 · Mídia", "CPC médio", "cpc", f_brl2, "down", lambda c: c["cliques"] >= MIN_CLIQUES),
    ("2 · Acesso", "Sessões do site (todas as origens)", "sess", f_int, "up", lambda c: c["sess"] >= MIN_SESSOES),
    ("2 · Acesso", "Sessões de Google pago", "sess_cpc", f_int, "up", lambda c: c["sess_cpc"] >= MIN_CLIQUES),
    ("2 · Acesso", "Sessões de Google pago ÷ cliques do Ads", "rastreio", f_num2, None, lambda c: True),
    ("2 · Acesso", "Sessões engajadas (site)", "eng", f_pct0, "up", lambda c: c["sess"] >= MIN_SESSOES),
    ("3 · Comportamento", "Produtos vistos por sessão", "view_sess", f_num2, "up", lambda c: c["sess"] >= MIN_SESSOES),
    ("3 · Comportamento", "Carrinho ÷ produto visto", "cart_view", f_pct, "up", lambda c: c["view_item"] >= 100),
    ("3 · Comportamento", "Checkout iniciado ÷ carrinho", "chk_cart", f_pct0, "up", lambda c: c["add_cart"] >= 15),
    ("3 · Comportamento", "Pedido ÷ checkout iniciado", "ped_chk", f_pct0, "up", lambda c: c["checkout"] >= 10),
    ("4 · Venda", "Pedidos", "pedidos", f_int, "up", lambda c: c["pedidos"] >= AMOSTRA_PEDIDOS),
    ("4 · Venda", "Taxa de conversão (pedidos ÷ sessões)", "conv", f_pct, "up", lambda c: c["pedidos"] >= AMOSTRA_PEDIDOS and c["sess"] >= MIN_SESSOES),
    ("4 · Venda", "Ticket médio", "ticket", f_brl, "up", lambda c: c["pedidos"] >= AMOSTRA_PEDIDOS),
    ("4 · Venda", "Faturamento", "fat", f_brl, "up", lambda c: c["pedidos"] >= AMOSTRA_PEDIDOS),
    ("5 · Retorno", "Margem de contribuição (antes de mídia)", "mc", f_brl, "up", lambda c: c["pedidos"] >= AMOSTRA_PEDIDOS),
    ("5 · Retorno", "Margem após mídia", "mc_pos", f_brl, "up", lambda c: c["pedidos"] >= AMOSTRA_PEDIDOS),
    ("5 · Retorno", "ROAS real do Google pago", "roas", f_x, "up", lambda c: c["ped_gp"] >= 3),
    ("5 · Retorno", "MER (faturamento total ÷ investimento)", "mer", f_x, "up", lambda c: c["pedidos"] >= AMOSTRA_PEDIDOS),
]


def _semaforo(cur, ref, melhor, amostra_ok):
    if melhor is None:
        return "—"
    if not amostra_ok:
        return "⚠ amostra pequena"
    r = _rel(cur, ref)
    if r is None:
        return "—"
    pior = -r if melhor == "up" else r
    if pior >= TOL_FORTE:
        return "🔴 pior"
    if pior >= TOL_LEVE:
        return "🟡 pior"
    if pior <= -TOL_LEVE:
        return "🟢 melhor"
    return "➖ estável"


def tabela_elos(c, ca, cb):
    C, A = derivados(c), derivados(ca)
    B = derivados(cb) if cb else None
    linhas = []
    for bloco, nome, chave, fmt, melhor, guarda in LINHAS:
        ref = B[chave] if B else A[chave]
        linhas.append({
            "Elo": bloco, "Indicador": nome, "Semana": fmt(C[chave]), "Semana anterior": fmt(A[chave]), f"Normal ({SEMANAS_BASE} sem.)": fmt(B[chave]) if B else "—",
            "Δ vs. anterior": _f_var(_rel(C[chave], A[chave])), "Δ vs. normal": _f_var(_rel(C[chave], B[chave])) if B else "—",
            "Leitura": _semaforo(C[chave], ref, melhor, guarda(c)),
        })
    return pd.DataFrame(linhas)


# ═══ NORTEADORES (leitura em regras) ═══════════════════════════════════════════════════════════════════════════════════════════

CADEIA = ["Rastreamento", "Investimento", "Tráfego", "Site", "Venda", "Retorno", "Leilão"]   # ordem em que o giro trava: primeiro o que invalida a leitura, depois de cima para baixo; o leilão (condição estrutural) por último


def norteadores(ctx, w, c, ca, cb, lc, lca, camp):
    """Lista de achados {sev, cat, titulo, corpo, acoes}. Só compara números já calculados com o normal da loja; o texto é o raciocínio de um analista
    (separar rastreio × tráfego × leilão × site × venda × retorno) e sempre diz o que olhar primeiro. Amostra pequena não gera alerta."""
    C, A = derivados(c), derivados(ca)
    B = derivados(cb) if cb else None
    ref, ref_nome = (B, f"o normal das {SEMANAS_BASE} semanas anteriores") if B else (A, "a semana anterior")
    cr = cb if cb else ca
    n, achados = w["n"], []
    recente = (ctx["hoje"] - w["fim"]).days <= GA4_DEFASAGEM_DIAS
    add = lambda sev, cat, titulo, corpo, acoes: achados.append({"sev": sev, "cat": cat, "titulo": titulo, "corpo": corpo, "acoes": acoes})

    # 1. RASTREAMENTO — se a medição está quebrada, o resto da leitura não vale
    probs = []
    if c["cliques"] >= MIN_CLIQUES and C["rastreio"] is not None:
        r = C["rastreio"]
        if r < RASTREIO_MIN:
            probs.append((("bad" if r < 0.5 and not recente else "warn"), f"o GA4 registrou só {f_num2(r)} sessão de Google pago por clique do Ads ({f_int(c['sess_cpc'])} sessões para {f_int(c['cliques'])} cliques): cliques estão se perdendo antes do site (auto-tagging/gclid, consentimento, página lenta) ou a atribuição do GA4 ainda não fechou"))
        elif r > RASTREIO_MAX:
            probs.append(("warn", f"o GA4 registrou {f_num2(r)} sessão de Google pago por clique ({f_int(c['sess_cpc'])} sessões para {f_int(c['cliques'])} cliques): há sessões marcadas como Google pago sem clique correspondente (UTM manual em link orgânico, recarregamento de página)"))
    if c["pedidos"] >= AMOSTRA_PEDIDOS and C["paridade"] is not None:
        p = C["paridade"]
        if p < PARIDADE_MIN:
            probs.append(("warn", f"o GA4 registrou só {f_int(c['purchase_ga4'])} compras para {f_int(c['pedidos'])} pedidos reais: a tag de compra está perdendo pedidos"))
        elif p > PARIDADE_MAX:
            probs.append(("warn", f"o GA4 registrou {f_int(c['purchase_ga4'])} compras para {f_int(c['pedidos'])} pedidos reais: bem mais do que o normal de pedidos criados e não pagos/cancelados, pode haver compra contada em duplicidade"))
    if probs:
        sev = "bad" if any(s == "bad" for s, _ in probs) else "warn"
        add(sev, "Rastreamento", "A medição do giro está com ruído", "; ".join(t for _, t in probs).capitalize() + "." + (" Os últimos dias do GA4 ainda são reprocessados pelo Google: confirme em 2 dias antes de mexer em qualquer coisa." if recente else ""),
            ["Conferir no GA4 (Relatórios → Aquisição) se as sessões de google / cpc aparecem por dia e com o nome das campanhas.",
             "No Google Ads, checar se o auto-tagging está ligado e se os links finais preservam o gclid (testar uma URL de anúncio com curl/aba anônima).",
             "Só depois de fechar o rastreio ler conversão e ROAS desta semana."])
    elif c["cliques"] >= MIN_CLIQUES:
        add("ok", "Rastreamento", "Medição consistente", f"{f_int(c['sess_cpc'])} sessões de Google pago para {f_int(c['cliques'])} cliques do Ads (razão {f_num2(C['rastreio'])}, saudável entre {f_num2(RASTREIO_MIN)} e {f_num2(RASTREIO_MAX)})"
            + (f"; compras do GA4 ({f_int(c['purchase_ga4'])}) na mesma ordem dos pedidos reais ({f_int(c['pedidos'])})" if c["pedidos"] >= AMOSTRA_PEDIDOS else "") + ".", ["Sem ação: os números das próximas linhas são confiáveis."])

    # 2a. INVESTIMENTO — gasto acima do normal
    dco0 = _rel(C["custo"], ref["custo"])
    if dco0 is not None and dco0 >= 0.40 and c["custo"] >= 60 and cr["custo"] > 0:
        sub = ""
        if camp is not None and not camp.empty:
            top = camp.assign(delta=camp["custo"] - camp["custo_ant"]).sort_values("delta", ascending=False).iloc[0]
            if top["delta"] > 0:
                sub = f" O maior aumento foi de <strong>{top['campanha']}</strong> ({brl(top['custo'], 0)} contra {brl(top['custo_ant'], 0)} na semana anterior)."
        retorno_ok = c["ped_gp"] >= 3 and C["mc_custo"] is not None and C["mc_custo"] >= 1
        add("ok" if retorno_ok else "warn", "Investimento", f"Investimento {_f_var(dco0)} vs. {ref_nome}",
            f"{brl(c['custo'], 0)} na janela contra {brl(cr['custo'], 0)} de referência ({brl(_d(c['custo'], n), 0)} por dia)." + sub +
            (" O retorno acompanhou." if retorno_ok else " O retorno ainda não apareceu na base: gasto acelerado sem venda correspondente é o principal risco de caixa."),
            ["Conferir no Google Ads se é pico pontual (o Google pode gastar até ~2× o orçamento num dia e compensar depois) ou campanha nova/lance mais alto.",
             "Se o pico vem de campanha nova, ler só depois de 7 dias (fase de aprendizado) e definir antes o critério de corte."])

    # 2. TRÁFEGO — volume de cliques e o porquê
    if cr["cliques"] >= MIN_CLIQUES and c["cliques"] >= 0:
        dcl, dco, dcp = _rel(C["cliques"], ref["cliques"]), _rel(C["custo"], ref["custo"]), _rel(C["cpc"], ref["cpc"])
        if dcl is not None and dcl <= -QUEDA_ALERTA:
            if dco is not None and dco <= -0.15 and (dcp is None or abs(dcp) < 0.15):
                causa = "O investimento caiu junto e o CPC ficou parecido: o tráfego caiu por <strong>menos verba/menos campanhas no ar</strong>, não por perda de competitividade."
                acoes = ["Conferir no Google Ads (Histórico de alterações) se alguma campanha foi pausada ou teve o orçamento reduzido.",
                         "Se a queda foi intencional (reestruturação), tratar como novo patamar e comparar só as próximas semanas entre si."]
            elif dcp is not None and dcp >= 0.15:
                causa = "O CPC subiu: cada clique ficou mais caro, sinal de <strong>mais concorrência no leilão ou anúncio/palavra perdendo relevância</strong>."
                acoes = ["Abrir Leilão (abaixo) e ver se a perda por classificação subiu nas campanhas de Pesquisa.",
                         "Rever a qualidade dos anúncios (força RSA) e as palavras que mais gastam."]
            else:
                causa = "Investimento e CPC não explicam sozinhos a queda: pode ser <strong>demanda menor na semana</strong> (sazonalidade, dia da semana) ou impressões perdidas."
                acoes = ["Comparar as impressões por dia com o normal e olhar a sazonalidade (véspera de feriado, fim de mês).", "Ver quais campanhas perderam impressão na tabela por campanha da página Google Ads."]
            add("bad" if dcl <= -QUEDA_URGENTE else "warn", "Tráfego", f"Cliques do Ads {_f_var(dcl)} vs. {ref_nome}",
                f"{f_int(c['cliques'])} cliques na janela contra {f_int(ref['cliques'])} de referência; investimento {_f_var(dco)} e CPC {_f_var(dcp)}. " + causa, acoes)
        elif dcl is not None and dcl >= QUEDA_ALERTA:
            add("ok", "Tráfego", f"Cliques do Ads {_f_var(dcl)} vs. {ref_nome}", f"{f_int(c['cliques'])} cliques contra {f_int(ref['cliques'])} de referência (investimento {_f_var(dco)}, CPC {_f_var(dcp)}). "
                "Mais tráfego só é bom se virar venda: confira Site e Retorno abaixo.", ["Se a conversão acompanhar, manter; se não, o crescimento veio de clique de baixa qualidade."])

    # 3. LEILÃO — o que limita o alcance na Pesquisa
    if lc:
        txt = f"Nas campanhas de Pesquisa ganhamos {pct(lc['is_'], 0)} das impressões possíveis; perdemos {pct(lc['perda_rank'], 0)} por classificação e {pct(lc['perda_orc'], 0)} por orçamento" \
              + (f" (semana anterior: {pct(lca['perda_rank'], 0)} e {pct(lca['perda_orc'], 0)})" if lca else "") + ". "
        if lc["perda_rank"] >= PERDA_RANK_ALERTA and lc["perda_rank"] >= lc["perda_orc"]:
            add("warn", "Leilão", "O alcance da Pesquisa é limitado por classificação, não por verba",
                txt + "Mais orçamento não compra mais impressão aqui: o Google está nos colocando atrás de outros anunciantes (lance, qualidade do anúncio ou relevância da palavra).",
                ["Priorizar qualidade do anúncio (títulos/descrições, extensões) e relevância da página final antes de subir o lance.", "Subir lance/meta de ROAS só com uma mudança por vez e ler o efeito em 7–10 dias (PDCA)."])
        elif lc["perda_orc"] >= PERDA_ORC_ALERTA:
            add("warn", "Leilão", "Impressões sendo perdidas por orçamento", txt + "As campanhas afetadas poderiam ter mais cliques com o lance atual.",
                ["Ver na tabela por campanha (página Google Ads) quais têm perda por orçamento alta e retorno bom (ROAS real ≥ 3×) e mover verba para elas."])

    # 4. CAMPANHA CONSUMINDO SEM RETORNO VISÍVEL
    if camp is not None and not camp.empty and C["custo"] > 0:
        pior = camp[(camp["custo"] >= 50) & (camp["custo"] >= 0.3 * C["custo"]) & (camp["compras"] == 0) & (camp["pedidos"] == 0)]
        for _, r in pior.iterrows():
            shop = r["tipo"] in ("Shopping", "PMax")
            add("warn", "Retorno", f"{r['campanha']}: {brl(r['custo'], 0)} sem compra nem pedido ligado",
                f"Consumiu {pct(r['custo'] / C['custo'], 0)} do investimento da janela ({f_int(r['cliques'])} cliques) sem compra creditada pelo Ads e sem pedido ligado pelo gclid." +
                (" Atenção: no Shopping a atribuição é incerta (pedidos com gclid válido não aparecem na tabela de cliques), então isto é um piso de retorno, não um veredito." if shop else ""),
                ["Conferir se a campanha teve compras tardias (o Ads credita o clique no dia do clique, e a compra pode chegar dias depois).",
                 ("Não cortar o orçamento por este número: cruzar com Compras (GA4) na tabela de Canais & Unit Economics e reavaliar na revisão de sexta." if shop else "Revisar palavras/termos que gastam sem vender antes de pausar a campanha.")])

    # 5. SITE — qualidade do tráfego e conversão
    if c["sess_cpc"] >= MIN_CLIQUES and C["eng_cpc"] is not None:
        queda_eng = (ref["eng_cpc"] - C["eng_cpc"]) if ref["eng_cpc"] is not None else 0
        if C["eng_cpc"] < ENG_MIN or queda_eng >= 0.08:
            add("warn", "Site", "Tráfego de Google pago pouco engajado",
                f"Só {pct(C['eng_cpc'], 0)} das sessões de Google pago foram engajadas" + (f" (referência: {pct(ref['eng_cpc'], 0)})" if ref["eng_cpc"] is not None else "") + ": o visitante sai sem interagir. Costuma indicar palavra ampla demais, anúncio prometendo o que a página não entrega ou página lenta.",
                ["Olhar as palavras de correspondência ampla que mais gastam (página Google Ads → Palavras-chave) e os termos de pesquisa no painel.", "Conferir se a página de destino de cada grupo é a categoria certa e carrega rápido no celular."])
    if cr["sess"] >= MIN_SESSOES and c["pedidos"] >= AMOSTRA_PEDIDOS and C["conv"] is not None:
        dconv = _rel(C["conv"], ref["conv"])
        passos = [("cart_view", "de ver um produto para colocar no carrinho", ["Revisar preço, fotos e descrição das páginas de produto mais vistas (Mapa de Interesse).", "Checar se o produto mais visitado tem estoque e variação disponível."]),
                  ("chk_cart", "do carrinho para o início do checkout", ["Testar o cálculo de frete/prazo no carrinho: frete surpresa é o principal motivo de abandono nesta etapa.", "Ver se a barra de frete grátis (a partir de R$ 400) está clara."]),
                  ("ped_chk", "do checkout para o pedido pago", ["Testar Pix e cartão de ponta a ponta; conferir se algum meio de pagamento está falhando.", "Olhar carrinhos abandonados no SAC: quem chegou ao checkout e não pagou é a recuperação mais barata."])]
        pior_passo, pior_queda = None, 0
        for chave, desc, acoes in passos:
            d = _rel(C[chave], ref[chave])
            base_ok = {"cart_view": cr["view_item"] >= 100, "chk_cart": cr["add_cart"] >= 15, "ped_chk": cr["checkout"] >= 10}[chave]
            if d is not None and base_ok and d < pior_queda:
                pior_passo, pior_queda = (chave, desc, acoes), d
        if pior_passo and pior_passo[0] == "ped_chk" and c["purchase_ga4"] > c["pedidos"]:
            pior_passo = (pior_passo[0], pior_passo[1], [f"O GA4 registrou {f_int(c['purchase_ga4'])} compras para {f_int(c['pedidos'])} pedidos válidos: a diferença são pedidos criados e não confirmados (Pix não pago, cancelados). Ver a lista de Cancelados no SAC."] + pior_passo[2])
        if dconv is not None and dconv <= -QUEDA_ALERTA:
            corpo = f"Conversão de {pct(C['conv'], 2)} (pedidos ÷ sessões) contra {pct(ref['conv'], 2)} de referência: {f_int(c['sess'])} sessões viraram {f_int(c['pedidos'])} pedidos. "
            acoes = ["Confirmar primeiro se é a semana (poucos pedidos) ou padrão: olhar a tabela semana a semana abaixo."]
            if pior_passo and pior_queda <= -0.2:
                corpo += f"O passo que mais piorou foi <strong>{pior_passo[1]}</strong> (ficou {abs(pior_queda) * 100:.0f}% abaixo da referência)."
                acoes = pior_passo[2] + acoes
            add("bad" if dconv <= -QUEDA_URGENTE else "warn", "Site", f"Sessões não estão virando pedido ({_f_var(dconv)})", corpo, acoes)
        elif pior_passo and pior_queda <= -0.3:
            add("warn", "Site", f"O funil perdeu força {pior_passo[1]}", f"Esse passo do funil ficou {abs(pior_queda) * 100:.0f}% abaixo d{'o normal' if B else 'a semana anterior'}, embora a conversão total ainda esteja em linha.", pior_passo[2])
        elif dconv is not None and dconv >= QUEDA_ALERTA:
            add("ok", "Site", f"Conversão subiu ({_f_var(dconv)})", f"{pct(C['conv'], 2)} das sessões viraram pedido contra {pct(ref['conv'], 2)} de referência.", ["Descobrir o que mudou (campanha, oferta, cupom) para repetir."])

    # 6. VENDA — ticket e dependência de canal
    if c["pedidos"] >= AMOSTRA_PEDIDOS and cr["pedidos"] > 0:
        dt = _rel(C["ticket"], ref["ticket"])
        if dt is not None and dt <= -0.15:
            add("warn", "Venda", f"Ticket médio {_f_var(dt)}", f"{brl(C['ticket'], 0)} contra {brl(ref['ticket'], 0)} de referência. Ticket menor com o mesmo tráfego derruba a margem por pedido: mix mais barato ou menos pedidos chegando ao frete grátis (R$ {FRETE_GRATIS}).",
                ["Ver Produtos da semana (Vendas da Semana) para achar o que puxou o ticket para baixo.", "Avaliar oferta de kit/complemento na página do produto (sem venda cruzada na descrição do cadastro; aqui é vitrine/carrinho)."])
        elif dt is not None and dt >= 0.15:
            add("ok", "Venda", f"Ticket médio {_f_var(dt)}", f"{brl(C['ticket'], 0)} contra {brl(ref['ticket'], 0)} de referência.", ["Manter o mix que puxou o ticket; conferir se a margem % acompanhou."])
        if C["part_gp"] is not None and C["part_gp"] >= CONCENTRACAO:
            add("warn", "Venda", "Vendas concentradas em Google pago", f"{pct(C['part_gp'], 0)} dos pedidos da janela vieram de Google pago: se a mídia parar, a venda para. Framework de concentração de canal: a saída é fortalecer recompra e busca orgânica.",
                ["Olhar recompra (Clientes & Coorte) e Instagram/orgânico como segunda perna do faturamento."])

    # 7. RETORNO — a mídia se pagou?
    if C["custo"] > 0:
        gasto = f"Investimos {brl(C['custo'], 0)}"
        if c["ped_gp"] == 0:
            if n >= 4 or c["custo"] >= 100:
                add("warn" if n < 7 else "bad", "Retorno", "Nenhum pedido real de Google pago na janela", f"{gasto} em {f_int(c['cliques'])} cliques e nenhum pedido da nossa base tem origem google / cpc. Em janela curta isso ainda pode ser atraso de compra (o clique de hoje vende em dias) ou pedido sem gclid.",
                    ["Conferir se houve compra tardia nos próximos dias antes de concluir.", "Olhar Compras (Ads) e Compras (GA4) por campanha: se o Ads credita compra e a base não, o problema é atribuição, não a campanha."])
        elif c["ped_gp"] >= 3:
            mc_c = C["mc_custo"]
            base_txt = f"{gasto}; {f_int(c['ped_gp'])} pedidos reais de Google pago (custo de {brl(C['custo_ped'], 0)} por pedido, que deixam em média {brl(C['mc_ped_gp'], 0)} de margem de contribuição cada)"
            if mc_c < 0.6:
                add("bad", "Retorno", f"A mídia não se pagou: {f_x(mc_c)} de margem por R$ 1 investido", base_txt + ". Cada pedido custou mais do que deixou de margem.",
                    ["Ver por campanha onde está o custo sem pedido e onde está o retorno; concentrar verba onde ROAS real ≥ 3×.", "Não subir orçamento antes de recuperar o retorno; ler 2 semanas seguidas antes de decidir corte."])
            elif mc_c < 1:
                add("warn", "Retorno", f"A mídia quase não se pagou: {f_x(mc_c)} de margem por R$ 1 investido", base_txt + ".", ["Comparar com as 4 semanas anteriores na tabela: se é queda isolada, aguardar; se é padrão, revisar palavras e campanhas."])
            else:
                add("ok", "Retorno", f"A mídia se pagou: {f_x(mc_c)} de margem por R$ 1 investido", base_txt + f". ROAS real de {f_x(C['roas'])}.", ["Se o alcance é limitado por classificação (Leilão), há espaço para ganhar impressão com qualidade de anúncio antes de subir a verba."])
    if c["pedidos"] >= AMOSTRA_PEDIDOS and C["mc_pos"] < 0:
        add("bad", "Retorno", "Margem após mídia negativa", f"A margem de contribuição de todos os pedidos ({brl(C['mc'], 0)}) não cobre o investimento em Google Ads ({brl(C['custo'], 0)}).", ["Rever investimento × faturamento total (MER) e priorizar as campanhas de melhor ROAS real."])
    infl = _d(c["valor_ads"], c["fat_gp"])
    if infl is not None and infl > 1.3 and c["valor_ads"] > 200:
        add("warn", "Retorno", "O painel do Ads credita mais venda do que a base confirma", f"O Ads reporta {brl(c['valor_ads'], 0)} de valor de conversão contra {brl(c['fat_gp'], 0)} de faturamento real de Google pago ({f_x(infl)}). Decidir lance por ROAS do painel superestima o retorno.",
            ["Usar o ROAS real desta página para decisão; o do painel só para comparar campanhas entre si."])
    return achados


def _ordenar(achados):
    peso = {"bad": 0, "warn": 1, "ok": 2}
    return sorted(achados, key=lambda a: (peso[a["sev"]], CADEIA.index(a["cat"]) if a["cat"] in CADEIA else 99))


def leitura_da_semana(achados):
    """Frase-título: primeiro gargalo (bad, depois warn) na ordem do giro; se não há, diz que não há."""
    for sev in ("bad", "warn"):
        for cat in CADEIA:
            for a in achados:
                if a["sev"] == sev and a["cat"] == cat:
                    outros = [x["cat"] for x in achados if x["sev"] in ("bad", "warn") and x is not a]
                    return sev, a, sorted(set(outros), key=lambda k: CADEIA.index(k) if k in CADEIA else 99)
    return "ok", None, []


# ═══ GRÁFICOS ══════════════════════════════════════════════════════════════════════════════════════════════════════════════════

def _serie_semanas(ctx, seg, fechado):
    """Componentes de cada uma das últimas semanas até `seg` (a última cortada no último dia fechado)."""
    linhas = []
    for s in reversed([x for x in sm.semanas(max(ga.INICIO_ADS, INICIO_GA4), fechado) if x <= seg][:SEMANAS_GRAFICO]):
        j = sm.janela(s, fechado)
        linhas.append({"seg": s, "n": j["n"], "parcial": j["parcial"], "fim": j["fim"], "c": componentes(ctx, j["ini"], j["fim"])})
    return linhas


def _grafico_indice(serie):
    x = [s["seg"].strftime("%d/%m") + ("*" if s["parcial"] else "") for s in serie]
    itens = [("Investimento por dia", "custo", COLORS["text_secondary"]), ("Cliques por dia", "cliques", METRIC_COLORS["pedidos"]), ("Sessões por dia", "sess", METRIC_COLORS["sessoes"]),
             ("Pedidos por dia", "pedidos", METRIC_COLORS["conversao"]), ("Faturamento por dia", "fat", METRIC_COLORS["receita"])]
    fig = go.Figure()
    for nome, k, cor in itens:
        v = [s["c"][k] / s["n"] for s in serie]
        m = sum(v) / len(v) if v else 0
        if not m:
            continue
        fig.add_trace(go.Scatter(x=x, y=[a / m * 100 for a in v], name=nome, mode="lines+markers", line=dict(color=cor, width=3 if k in ("fat", "custo") else 2),
                                 hovertemplate="semana de %{x}<br>" + nome + ": %{y:.0f} (100 = média do período)<extra></extra>"))
    plotly_layout(fig, height=320, hovermode="x unified", xaxis=dict(type="category", dtick=1, gridcolor=COLORS["grid"]), yaxis=dict(gridcolor=COLORS["grid"], rangemode="tozero"))
    fig.add_hline(y=100, line_dash="dot", line_color=COLORS["text_muted"])
    return fig


def _grafico_funil(c, cb, n_janela):
    etapas = [("Produto visto", "view_item"), ("Carrinho", "add_cart"), ("Checkout iniciado", "checkout"), ("Pedido (real)", "pedidos")]
    y = [e[0] for e in etapas]
    fig = go.Figure()
    if cb:
        fig.add_bar(y=y, x=[cb[k] for _, k in etapas], name=f"Normal ({SEMANAS_BASE} sem.)", orientation="h", marker_color=COLORS["text_muted"], hovertemplate="%{y}: %{x:.0f}<extra>normal</extra>")
    fig.add_bar(y=y, x=[c[k] for _, k in etapas], name="Semana", orientation="h", marker_color=METRIC_COLORS["receita"], hovertemplate="%{y}: %{x:.0f}<extra>semana</extra>")
    plotly_layout(fig, height=280, barmode="group", yaxis=dict(autorange="reversed"), xaxis=dict(gridcolor=COLORS["grid"]))
    return fig


def _grafico_origem(ctx, serie):
    canal = ctx["canal"].copy()
    canal["grupo"] = [_grupo_origem(f, m) for f, m in zip(canal["ds_canal_fonte"], canal["ds_canal_meio"])]
    ordem = ["Google pago", "Google orgânico", "Direto", "Instagram/Meta", "Outros", "Sem origem ainda"]
    cores = {"Google pago": CATEGORICAL[0], "Google orgânico": CATEGORICAL[2], "Direto": CATEGORICAL[1], "Instagram/Meta": CATEGORICAL[4], "Outros": CATEGORICAL[3], "Sem origem ainda": COLORS["text_muted"]}
    x = [s["seg"].strftime("%d/%m") + ("*" if s["parcial"] else "") for s in serie]
    fig = go.Figure()
    for g in ordem:
        ys = []
        for s in serie:
            j = sm.janela(s["seg"], s["fim"])
            v = sm.entre(canal[canal["grupo"] == g], "dt_data", j["ini"], j["fim"])["qt_sessoes"].sum()
            ys.append(v / s["n"])
        if sum(ys) > 0:
            fig.add_bar(x=x, y=ys, name=g, marker_color=cores[g], hovertemplate="semana de %{x}<br>" + g + ": %{y:.0f} sessões por dia<extra></extra>")
    plotly_layout(fig, height=300, barmode="stack", xaxis=dict(type="category", dtick=1, gridcolor=COLORS["grid"]), yaxis=dict(gridcolor=COLORS["grid"]))
    return fig


# ═══ TELA ══════════════════════════════════════════════════════════════════════════════════════════════════════════════════════

def render():
    inject_css()
    with st.spinner("Carregando dados do BigQuery..."):
        try:
            ads = ga.carregar_ads()
            g4 = carregar_ga4()
            dv = carregar_vendas()
            dcamp = carregar_campanhas()
            gcpc = ga.carregar_ga4_cpc()
        except Exception as e:
            st.error(f"Erro ao carregar dados do BigQuery: {e}")
            return
    perf, lei, vendas = ads["perf"], ads["lei"], dv["vendas"]
    hoje = pd.Timestamp(_hoje_brt())
    fechado = min(hoje - pd.Timedelta(days=1), perf["dt_data"].max(), g4["canal"]["dt_data"].max())  # último dia fechado em TODAS as fontes
    ctx = {"perf": perf, "lei": lei, "vendas": vendas, "canal": g4["canal"], "funil": g4["funil"], "hoje": hoje}
    st.html(f"""
    <div class="report-header">
      <div>
        <div class="report-brand">shibari brasil · camada semanal</div>
        <div class="report-title">Giro <span>Semanal</span></div>
        <div class="report-meta">Mídia (Google Ads) → acesso (GA4) → venda (pedidos): o mesmo período nos três elos, com leitura e norteadores · semana de segunda a domingo</div>
      </div>
      <div class="report-badge">
        Hoje: <strong>{hoje.strftime("%d/%m/%Y")}</strong><br>
        Dados fechados até: <strong>{fechado.strftime("%d/%m")}</strong>
      </div>
    </div>
    """)

    inicio = max(ga.INICIO_ADS, INICIO_GA4)
    semanas = sm.semanas(inicio, fechado)
    atual = semanas[0]
    seg = pd.Timestamp(st.selectbox("Semana", options=semanas, index=0, format_func=lambda s: sm.rotulo(s, fechado if s == atual else None) + (" (em andamento, acumulado)" if s == atual and sm.janela(s, fechado)["parcial"] else "")))
    w = sm.janela(seg, fechado)
    rot_ant = f"semana anterior{' (mesmos dias)' if w['parcial'] else ''}"

    c = componentes(ctx, w["ini"], w["fim"])
    ca = componentes(ctx, w["ant_ini"], w["ant_fim"])
    base = []
    for k in range(1, SEMANAS_BASE + 1):
        ini, fim = sm.janela_k(seg, w["n"], k)
        if ini >= inicio:                     # só janelas com Ads e GA4 disponíveis
            base.append(componentes(ctx, ini, fim))
    cb = _media(base) if base else None
    C = derivados(c)

    # ═══ CABEÇALHO DA LEITURA ═══
    section_title(f"Semana {sm.rotulo(seg, w['fim'])}" + (f" — acumulado de {w['n']} dia{'s' if w['n'] > 1 else ''} (parcial)" if w["parcial"] else ""))
    if w["parcial"]:
        note(f"<strong>Semana em andamento:</strong> acumulado de {w['ini'].strftime('%d/%m')} a {w['fim'].strftime('%d/%m')} ({w['n']} de 7 dias), comparado aos mesmos dias da semana anterior e ao normal dessas mesmas janelas nas {len(base)} semanas antes. "
             "Hoje não entra (o custo do Ads e o GA4 fecham com 1 dia de atraso). "
             + ("Com poucos dias, tráfego e leilão já dizem algo; conversão, ticket e retorno ainda não — o relatório não emite alerta com menos de "
                f"{AMOSTRA_PEDIDOS} pedidos." if w["n"] < 5 else ""), variant="warn" if w["n"] < 3 else "")

    if base and any(sm.janela_k(seg, w["n"], k)[0] < MUDANCA_ESTRUTURA for k in range(1, len(base) + 1)) and seg >= MUDANCA_ESTRUTURA:
        note("<strong>O “normal” desta semana atravessa a reestruturação de 31/08</strong> (Topo Funil pausada, campanhas novas): as semanas de agosto tinham mais investimento e mais cliques do que a estrutura atual. "
             "Para investimento, cliques e CPC, use a <em>semana anterior</em> como referência principal até o normal se formar só com semanas da estrutura nova (~5 semanas depois, meados de outubro).", variant="warn")
    achados = _ordenar(norteadores(ctx, w, c, ca, cb, ga.leilao_conta(perf, lei, w["ini"], w["fim"]), ga.leilao_conta(perf, lei, w["ant_ini"], w["ant_fim"]),
                                   ga._campanhas_janela(perf, lei, vendas, dcamp["ponte"], gcpc, w["ini"], w["fim"], w["ant_ini"], w["ant_fim"])[0]))
    sev, principal, outros = leitura_da_semana(achados)
    linha = (f"Investimos <strong>{brl(c['custo'], 0)}</strong> → <strong>{f_int(c['cliques'])}</strong> cliques → <strong>{f_int(c['sess'])}</strong> sessões no site "
             f"({f_int(c['sess_cpc'])} de Google pago) → <strong>{f_int(c['pedidos'])}</strong> pedidos ({f_pct(C['conv'])} das sessões) → <strong>{brl(c['fat'], 0)}</strong> de faturamento "
             f"e <strong>{brl(c['mc'], 0)}</strong> de margem de contribuição (<strong>{brl(C['mc_pos'], 0)}</strong> após a mídia).")
    if principal:
        titulo_leit = f"Onde olhar primeiro: {principal['cat'].lower()}"
        corpo_leit = f"{linha}<br><br><strong>{principal['titulo']}.</strong> {principal['acoes'][0]}" + (f"<br><span style='color:{COLORS['text_secondary']}'>Também pedem atenção: {', '.join(k.lower() for k in outros)}.</span>" if outros else "")
        note(f"<strong>{titulo_leit}</strong><br>{corpo_leit}", variant="warn" if sev == "warn" else "")
    else:
        note(f"<strong>Sem gargalo claro nesta janela.</strong><br>{linha}")

    t1, c1 = ga.variacao(c["fat"], ca["fat"], rot_ant, fmt=f_brl)
    t2, c2 = ga.variacao(c["pedidos"], ca["pedidos"], rot_ant, fmt=f_int)
    t3, c3 = ga.variacao(c["sess"], ca["sess"], rot_ant, fmt=f_int)
    t4, c4 = ga.variacao(C["conv"], derivados(ca)["conv"], rot_ant, tipo="pp")
    t5, c5 = ga.variacao(c["custo"], ca["custo"], rot_ant, fmt=f_brl, neutro=True)
    t6, c6 = ga.variacao(C["mc_pos"], derivados(ca)["mc_pos"], rot_ant, fmt=f_brl)
    render_cards([
        card("Faturamento", brl(c["fat"]), "todas as origens", delta=t1, delta_color=c1),
        card("Pedidos", f_int(c["pedidos"]), f"{f_int(c['ped_gp'])} de Google pago", delta=t2, delta_color=c2, variant="warn" if c["pedidos"] < AMOSTRA_PEDIDOS else "neutral",
             ref=f"amostra pequena (< {AMOSTRA_PEDIDOS})" if c["pedidos"] < AMOSTRA_PEDIDOS else ""),
        card("Sessões no site", f_int(c["sess"]), f"{f_int(c['sess_cpc'])} vindas de Google pago", delta=t3, delta_color=c3),
        card("Conversão", f_pct(C["conv"]), "pedidos ÷ sessões", delta=t4, delta_color=c4),
        card("Investimento Google Ads", brl(c["custo"]), f"{brl(_d(c['custo'], w['n']))} por dia", delta=t5, delta_color=c5),
        card("Margem após mídia", brl(C["mc_pos"]), "margem de contribuição − Google Ads", delta=t6, delta_color=c6, variant="ok" if C["mc_pos"] > 0 else "bad"),
    ])

    # ═══ NORTEADORES ═══
    section_title("Norteadores da semana")
    if achados:
        render_insights([insight_card(a["sev"], a["cat"], a["titulo"], a["corpo"], a["acoes"]) for a in achados])
    else:
        st.info("Sem volume suficiente na janela para emitir leituras (poucos cliques, sessões e pedidos).")
    note("Os norteadores comparam a janela com <strong>o normal</strong> (média das mesmas janelas nas 4 semanas anteriores) e só emitem alerta quando há amostra: "
         f"{MIN_CLIQUES}+ cliques para tráfego e rastreio, {MIN_SESSOES}+ sessões e {AMOSTRA_PEDIDOS}+ pedidos para conversão, ticket e retorno. A ordem segue o caminho do giro (rastreamento → investimento → tráfego → site → venda → retorno; o leilão, por ser uma condição estrutural, vem por último) "
         "porque um elo quebrado antes invalida a leitura dos seguintes: se o rastreio está ruim, não conclua nada sobre conversão. São hipóteses ordenadas para <em>onde olhar primeiro</em>, não sentença: "
         "com ~10 pedidos por semana, confirme a tendência em 2 semanas seguidas antes de mexer em campanha.")

    # ═══ ELO A ELO ═══
    section_title("O giro elo a elo")
    tab = tabela_elos(c, ca, cb)
    st.dataframe(tab, hide_index=True, use_container_width=True, height=36 + 35 * len(tab),
                 column_config={"Elo": st.column_config.TextColumn(width=130), "Indicador": st.column_config.TextColumn(width=280), "Semana": st.column_config.TextColumn(width=90),
                                "Semana anterior": st.column_config.TextColumn(width=120), f"Normal ({SEMANAS_BASE} sem.)": st.column_config.TextColumn(width=110), "Δ vs. anterior": st.column_config.TextColumn(width=110),
                                "Δ vs. normal": st.column_config.TextColumn(width=100), "Leitura": st.column_config.TextColumn(width=130)})
    note(f"<strong>Como ler:</strong> a tabela segue o caminho do dinheiro — mídia → acesso → comportamento no site → venda → retorno. <em>Normal</em> = média das mesmas janelas nas {len(base)} semanas anteriores "
         "(razões = soma ÷ soma). <em>Leitura</em> compara a semana com o normal (ou com a semana anterior se ainda não há histórico): 🟢 melhor, ➖ estável (±10%), 🟡 pior em 10–25%, 🔴 pior em mais de 25%; "
         "“amostra pequena” = pouco volume no denominador para o semáforo valer. Funil (produto visto, carrinho, checkout) é do <strong>site inteiro</strong>, não só do Google pago, e conta sessões únicas que chegaram em cada etapa. "
         "<em>Pedido ÷ checkout</em> divide pedidos reais por checkouts iniciados no GA4: aproximação (um checkout pode virar pedido em outro dia).")

    # ═══ GRÁFICOS ═══
    serie = _serie_semanas(ctx, seg, fechado)
    section_title("Os elos se movem juntos?")
    if serie:
        st.html('<div class="c-label" style="margin:0 0 10px">Índice por dia (100 = média das semanas mostradas)</div>')
        with st.container(border=True):
            st.plotly_chart(_grafico_indice(serie), use_container_width=True)
        note("Se o investimento sobe e cliques sobem mas <strong>sessões, pedidos e faturamento não acompanham</strong>, o gargalo está depois do clique (rastreio ou site). Se cliques caem sem o investimento cair, o gargalo é o leilão (CPC). "
             "Se tudo sobe junto e o faturamento não, é o retorno (ticket, margem). Volumes <strong>por dia</strong>: a semana em andamento (*) fica comparável às fechadas. Pedidos por dia oscilam muito (1 pedido muda o índice em dezenas de pontos): olhe a direção de várias semanas.")
    col1, col2 = st.columns(2)
    with col1:
        st.html('<div class="c-label" style="margin:14px 0 10px">Funil do site na janela × normal</div>')
        with st.container(border=True):
            st.plotly_chart(_grafico_funil(c, cb, w["n"]), use_container_width=True)
    with col2:
        st.html('<div class="c-label" style="margin:14px 0 10px">Sessões por dia, por origem</div>')
        with st.container(border=True):
            if serie:
                st.plotly_chart(_grafico_origem(ctx, serie), use_container_width=True)
    note("Funil = sessões únicas do GA4 do site todo que chegaram em cada etapa (produto visto, carrinho, checkout) e pedidos reais da base no último degrau. Origem de sessão vem do GA4; “Sem origem ainda” são sessões dos últimos dias que o Google ainda não classificou "
         "(por isso não leia origem dos últimos ~2 dias como definitiva).")

    # ═══ SEMANA A SEMANA ═══
    section_title(f"Semana a semana (últimas {SEMANAS_GRAFICO})")
    linhas = []
    for s in serie:
        d, cc = derivados(s["c"]), s["c"]
        linhas.append({"Semana": sm.rotulo(s["seg"], s["fim"]), "Dias": s["n"], "Investimento": cc["custo"], "Cliques": cc["cliques"], "Sessões": cc["sess"], "Sess. Google pago": cc["sess_cpc"],
                       "Conversão": d["conv"], "Pedidos": cc["pedidos"], "Ticket": d["ticket"], "Faturamento": cc["fat"], "Margem contrib.": cc["mc"], "Margem após mídia": d["mc_pos"],
                       "ROAS real (Google pago)": d["roas"], "MER": d["mer"]})
    if linhas:
        st.dataframe(pd.DataFrame(linhas).iloc[::-1], hide_index=True, use_container_width=True,
                     column_config={"Semana": st.column_config.TextColumn(width=170), "Dias": st.column_config.NumberColumn(format="%d", width=55), "Investimento": st.column_config.NumberColumn(format="R$ %.0f", width=100),
                                    "Cliques": st.column_config.NumberColumn(format="%.0f", width=75), "Sessões": st.column_config.NumberColumn(format="%.0f", width=80), "Sess. Google pago": st.column_config.NumberColumn(format="%.0f", width=125),
                                    "Conversão": st.column_config.NumberColumn(format="percent", width=90), "Pedidos": st.column_config.NumberColumn(format="%.0f", width=80), "Ticket": st.column_config.NumberColumn(format="R$ %.0f", width=80),
                                    "Faturamento": st.column_config.NumberColumn(format="R$ %.0f", width=110), "Margem contrib.": st.column_config.NumberColumn(format="R$ %.0f", width=120),
                                    "Margem após mídia": st.column_config.NumberColumn(format="R$ %.0f", width=135), "ROAS real (Google pago)": st.column_config.NumberColumn(format="%.1f×", width=160), "MER": st.column_config.NumberColumn(format="%.1f×", width=70)})
    note("Totais da semana (na semana em andamento, o acumulado dos dias fechados). Fontes: Google Ads (<code>tb_gads_*</code>), GA4 (<code>tb_ga4_canal_diario</code>, <code>tb_ga4_funil</code>) e <code>tb_pedido</code>. "
         "Margem de contribuição é <strong>antes de mídia</strong>; “após mídia” subtrai só o Google Ads (Meta/Instagram pago não tem custo na base). O histórico de GA4 começa em 01/07/2026, por isso o normal das semanas iniciais tem menos de 4 janelas.")
