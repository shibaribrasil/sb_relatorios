"""Tema visual e componentes de UI compartilhados por todos os relatórios.

Extraído de app.py (relatório Google Ads) na Fase 9 da migração — ver
MIGRACAO-RELATORIOS.md e CLAUDE.md. Qualquer novo relatório deve reusar isto
em vez de duplicar CSS/cores/helpers.
"""
import re

import streamlit as st

# ── Design System — mesmo tema do relatório HTML (sb_marketing_team/relatorios) ──
PLUM    = "#5b1e4b"
PLUM_DK = "#1c0c18"
SCARLET = "#d10f2f"
TAUPE   = "#8c6f68"
SKIN    = "#e6cfc3"
BG      = "#f4ece7"
OK      = "#1e7a4f"
OK_BG   = "#4caf82"
WARN    = "#8a6010"
WARN_BG = "#c9963a"
BAD     = "#d10f2f"
BORDER  = "rgba(91,30,75,0.18)"
GRID    = "rgba(91,30,75,0.10)"

# Paleta categórica para gráficos de pizza/participação (identidade, não
# magnitude) — 8 tons fixos, ordem validada (CVD ΔE mínimo entre vizinhos
# 24.2, claridade OKLCH 0.43–0.77, croma ≥ 0.10 no fundo branco dos
# gráficos). Nunca cicle além de 8 fatias — dobre o resto em "Outros"
# (usa MUTED) em vez de gerar uma 9ª cor.
CATEGORICAL_PALETTE = ["#2a78d6", "#1baf7a", "#eda100", "#008300", "#4a3aa7", "#e34948", "#e87ba4", "#eb6834"]
MUTED = "#898781"

CSS = f"""
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Montserrat:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  .stApp {{ background: {BG}; font-family: 'Montserrat', sans-serif; }}
  .block-container {{ padding-top: 1.5rem; max-width: 1280px; }}
  h1, h2, h3 {{ font-family: 'Montserrat', sans-serif !important; }}

  [data-testid="stPlotlyChart"] {{ border-radius: 10px; overflow: hidden; }}

  .report-header {{
    background: {PLUM_DK}; border-bottom: 3px solid {SCARLET};
    padding: 22px 32px; border-radius: 10px; margin-bottom: 18px;
    display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px;
  }}
  .report-brand {{ font-size: 10px; font-weight: 600; letter-spacing: 0.16em; text-transform: uppercase; color: {TAUPE}; margin-bottom: 4px; }}
  .report-title {{ font-family: 'Montserrat', sans-serif; font-size: 22px; font-weight: 700; color: {BG}; }}
  .report-title span {{ color: {SCARLET}; }}
  .report-meta {{ color: {TAUPE}; font-size: 12px; margin-top: 3px; }}
  .report-badge {{
    background: rgba(244,236,231,0.08); border: 1px solid rgba(244,236,231,0.15); border-radius: 6px;
    padding: 8px 16px; font-size: 12px; color: {SKIN}; text-align: right; line-height: 1.8;
  }}
  .report-badge strong {{ color: {BG}; }}

  .section-title {{
    display: flex; align-items: center; gap: 10px; font-size: 11px; font-weight: 700;
    letter-spacing: 0.12em; text-transform: uppercase; color: {TAUPE};
    margin: 30px 0 14px 0; padding-bottom: 8px; border-bottom: 1px solid {BORDER};
  }}
  .section-title::before {{ content: ''; display: block; width: 3px; height: 14px; background: {SCARLET}; border-radius: 2px; }}

  .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px; margin-bottom: 6px; }}
  .card {{ background: #fff; border: 1px solid {BORDER}; border-radius: 10px; padding: 16px 18px; position: relative; box-shadow: 0 1px 4px rgba(91,30,75,0.06); }}
  .card::before {{ content: ''; position: absolute; top: 0; left: 0; right: 0; height: 3px; border-radius: 10px 10px 0 0; }}
  .card.c-neutral::before {{ background: {TAUPE}; }}
  .card.c-ok::before {{ background: {OK_BG}; }}
  .card.c-warn::before {{ background: {WARN_BG}; }}
  .card.c-bad::before {{ background: {BAD}; }}
  .c-label {{ font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.07em; color: {TAUPE}; margin-bottom: 8px; }}
  .c-value {{ font-size: 26px; font-weight: 700; font-family: 'Montserrat', sans-serif; line-height: 1; }}
  .card.c-neutral .c-value {{ color: #141419; }}
  .card.c-ok .c-value {{ color: {OK}; }}
  .card.c-warn .c-value {{ color: {WARN}; }}
  .card.c-bad .c-value {{ color: {BAD}; }}
  .c-sub {{ font-size: 11px; color: {TAUPE}; margin-top: 6px; }}
  .c-ref {{ font-size: 10px; color: {TAUPE}; margin-top: 2px; opacity: 0.7; }}

  .bench-row {{ display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 10px; }}
  .bench {{ background: rgba(91,30,75,0.06); border: 1px solid {BORDER}; border-radius: 5px; padding: 3px 10px; font-size: 10px; color: {TAUPE}; font-weight: 500; }}
  .bench strong {{ color: #141419; }}

  .note {{
    font-size: 12px; color: {TAUPE}; line-height: 1.6; margin-top: 6px;
    background: rgba(91,30,75,0.04); border: 1px solid {BORDER}; border-radius: 8px; padding: 10px 14px;
  }}
  .note strong {{ color: #141419; }}

  .tag {{ display: inline-block; padding: 2px 9px; border-radius: 4px; font-size: 10px; font-weight: 700; }}
  .t-ok    {{ background: rgba(76,175,130,0.14);  color: #1a6b40; }}
  .t-warn  {{ background: rgba(201,150,58,0.18);  color: #7a5208; }}
  .t-bad   {{ background: rgba(209,15,47,0.12);   color: {BAD}; }}
  .t-muted {{ background: rgba(140,111,104,0.14); color: #5e3e3a; }}

  .insights {{ display: flex; flex-direction: column; gap: 12px; margin-bottom: 6px; }}
  .insight {{
    background: #fff; border: 1px solid {BORDER}; border-left: 3px solid {TAUPE};
    border-radius: 0 8px 8px 0; padding: 16px 20px; box-shadow: 0 1px 3px rgba(91,30,75,0.06);
    display: flex; gap: 12px;
  }}
  .insight.i-bad  {{ border-left-color: {BAD}; }}
  .insight.i-warn {{ border-left-color: {WARN_BG}; }}
  .insight.i-ok   {{ border-left-color: {OK_BG}; }}
  .insight-icon {{
    width: 28px; height: 28px; border-radius: 6px; flex-shrink: 0;
    display: flex; align-items: center; justify-content: center; font-size: 13px;
  }}
  .insight.i-bad  .insight-icon {{ background: rgba(209,15,47,0.10); }}
  .insight.i-warn .insight-icon {{ background: rgba(201,150,58,0.14); }}
  .insight.i-ok   .insight-icon {{ background: rgba(76,175,130,0.14); }}
  .insight-content {{ flex: 1; min-width: 0; }}
  .insight-label {{ font-size: 9px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em; color: {TAUPE}; margin-bottom: 4px; }}
  .insight-title {{ font-family: 'Montserrat', sans-serif; font-size: 14px; font-weight: 600; color: #141419; margin-bottom: 6px; }}
  .insight-body {{ font-size: 12px; color: {TAUPE}; line-height: 1.6; margin-bottom: 10px; }}
  .insight-action {{ background: rgba(91,30,75,0.04); border: 1px solid {BORDER}; border-radius: 6px; padding: 10px 14px; }}
  .insight-action-lbl {{ font-size: 9px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.07em; color: {TAUPE}; margin-bottom: 6px; }}
  .insight-action ul {{ margin: 0; padding-left: 0; list-style: none; }}
  .insight-action li {{ font-size: 11px; color: #141419; margin-bottom: 4px; padding-left: 14px; position: relative; }}
  .insight-action li:last-child {{ margin-bottom: 0; }}
  .insight-action li::before {{ content: '→'; color: {SCARLET}; position: absolute; left: 0; font-weight: 700; }}

  .opportunities {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 16px; margin-bottom: 6px; }}
  .opp-card {{
    background: #fff; border: 1px solid {BORDER}; border-top: 2px solid {PLUM};
    border-radius: 0 0 10px 10px; padding: 20px 22px; box-shadow: 0 1px 3px rgba(91,30,75,0.05);
  }}
  .opp-title {{ font-family: 'Montserrat', sans-serif; font-size: 14px; font-weight: 600; color: #141419; margin-bottom: 6px; }}
  .opp-desc {{ font-size: 12px; color: {TAUPE}; line-height: 1.6; margin-bottom: 10px; }}
  .opp-gain {{
    display: inline-block; background: rgba(76,175,130,0.08); border: 1px solid rgba(76,175,130,0.25);
    color: {OK}; font-size: 11px; font-weight: 600; padding: 4px 10px; border-radius: 5px; margin-bottom: 10px;
  }}
  .opp-where {{ font-size: 11px; color: {TAUPE}; margin-bottom: 10px; }}
  .opp-where strong {{ color: #141419; }}
  .opp-how-label {{ font-size: 9px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.07em; color: {TAUPE}; margin-bottom: 6px; }}
  .opp-how {{ margin: 0; padding-left: 18px; font-size: 11px; color: #141419; }}
  .opp-how li {{ margin-bottom: 4px; }}
  .opp-how li:last-child {{ margin-bottom: 0; }}
</style>
"""

_INSIGHT_ICONE = {"bad": "🔴", "warn": "🟡", "ok": "🟢"}
_INSIGHT_ROTULO = {"bad": "Urgente", "warn": "Atenção", "ok": "Positivo"}


def inject_css():
    st.html(CSS)


def card(label, value, sub="", ref="", variant="neutral"):
    ref_html = f'<div class="c-ref">{ref}</div>' if ref else ""
    return f"""<div class="card c-{variant}">
        <div class="c-label">{label}</div>
        <div class="c-value">{value}</div>
        <div class="c-sub">{sub}</div>
        {ref_html}
    </div>"""


def render_cards(cards_html):
    st.html(f'<div class="cards">{"".join(cards_html)}</div>')


def insight_card(severidade, categoria_label, titulo, corpo, acoes):
    """Card do Diagnóstico Executivo. `severidade` (bad/warn/ok) e os dados
    vêm de detectar_sinais() — este helper só formata, não decide nada."""
    itens = "".join(f"<li>{a}</li>" for a in acoes)
    return f"""<div class="insight i-{severidade}">
        <div class="insight-icon">{_INSIGHT_ICONE[severidade]}</div>
        <div class="insight-content">
            <div class="insight-label">{_INSIGHT_ROTULO[severidade]} · {categoria_label}</div>
            <div class="insight-title">{titulo}</div>
            <div class="insight-body">{corpo}</div>
            <div class="insight-action">
                <div class="insight-action-lbl">Ação recomendada</div>
                <ul>{itens}</ul>
            </div>
        </div>
    </div>"""


def render_insights(insights_html):
    st.html(f'<div class="insights">{"".join(insights_html)}</div>')


def opportunity_card(titulo, descricao, ganho_esperado, onde_aplicar, como_aplicar):
    """Card de Oportunidades. Gap e categoria vêm de detectar_oportunidades()
    — este helper só formata, não decide nada."""
    passos = "".join(f"<li>{p}</li>" for p in como_aplicar)
    return f"""<div class="opp-card">
        <div class="opp-title">{titulo}</div>
        <div class="opp-desc">{descricao}</div>
        <div class="opp-gain">📈 {ganho_esperado}</div>
        <div class="opp-where"><strong>Onde aplicar:</strong> {onde_aplicar}</div>
        <div class="opp-how-label">Como aplicar</div>
        <ol class="opp-how">{passos}</ol>
    </div>"""


def render_opportunities(cards_html):
    st.html(f'<div class="opportunities">{"".join(cards_html)}</div>')


def section_title(text):
    st.html(f'<div class="section-title">{text}</div>')


def bench_row(items):
    spans = "".join(f'<div class="bench">{label}: <strong>{value}</strong></div>' for label, value in items)
    st.html(f'<div class="bench-row">{spans}</div>')


def note(html):
    st.html(f'<div class="note">{html}</div>')


def tag(text, variant):
    return f'<span class="tag t-{variant}">{text}</span>'


def nome_curto(nome, max_len=30):
    """Encurta nome de campanha para rótulo de gráfico (o nome completo
    continua nas tabelas e no hover). Remove sufixo de data/mês entre
    parênteses (ex.: "(Set-25)"), sufixo de data no fim (ex.: "- 22/10/2023")
    e a palavra "Shibari" (redundante — o relatório inteiro já é da conta).
    """
    s = nome
    s = re.sub(r"\s*\([^)]{2,10}\)\s*$", "", s)
    s = re.sub(r"\s*[-–]\s*\d{1,2}/\d{1,2}/\d{2,4}\s*$", "", s)
    s = re.sub(r"\bshibari\b", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s*[-–]\s*[-–]\s*", " – ", s)
    s = re.sub(r"^\s*[-–]\s*|\s*[-–]\s*$", "", s)
    s = re.sub(r"\s{2,}", " ", s).strip()
    if not s:
        return nome
    if len(s) > max_len:
        s = s[:max_len - 1].rstrip() + "…"
    return s


def roas_variant(v):
    if v >= 3:
        return "ok"
    if v >= 2:
        return "warn"
    return "bad"


def util_variant(pct):
    if pct >= 1.0:
        return "bad"
    if pct < 0.7:
        return "muted"
    return "warn"


def qs_variant(qs):
    if qs >= 9:
        return "ok"
    if qs >= 7:
        return "warn"
    return "bad"


def style_color(styler, func, subset):
    # pandas >=2.1 renamed Styler.applymap to .map and later removed applymap entirely —
    # try both so this works regardless of which pandas version Streamlit Cloud installs.
    if hasattr(styler, "map"):
        return styler.map(func, subset=subset)
    return styler.applymap(func, subset=subset)


def plotly_layout(fig, **kwargs):
    layout = dict(
        plot_bgcolor="white", paper_bgcolor="white",
        font=dict(family="Montserrat, sans-serif", color=TAUPE, size=12),
        margin=dict(l=10, r=10, t=10, b=10),
        xaxis=dict(gridcolor=GRID), yaxis=dict(gridcolor=GRID),
        legend=dict(orientation="h", y=1.12, font=dict(size=11)),
    )
    layout.update(kwargs)
    fig.update_layout(**layout)
    # cantos arredondados nas barras de todo relatório — aplicado aqui (função
    # compartilhada) em vez de em cada add_bar(), pra valer pra qualquer gráfico
    # de barra que passe por plotly_layout()
    fig.update_traces(marker_cornerradius=4, selector=dict(type="bar"))
    return fig
