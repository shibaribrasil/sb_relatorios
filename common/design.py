"""Tema visual e componentes de UI compartilhados por todos os relatórios.

Todas as cores vêm de `common/theme.py` (design system de dados, separado da
identidade da marca). Nenhuma cor deve ser escrita à mão fora de theme.py.
Os nomes legados (PLUM, SCARLET, TAUPE...) continuam exportados como aliases
da paleta nova para não quebrar as páginas antigas (Estoque, Google Ads).
"""
import re

import streamlit as st

from common import theme  # noqa: F401 — registra o template Plotly "ecommerce" como padrão
from common.theme import COLORS, CATEGORICAL, METRIC_COLORS, kpi_delta_color  # noqa: F401

# ── Aliases legados (páginas antigas) → paleta nova ─────────────────────────
PLUM    = COLORS["primary"]
PLUM_DK = COLORS["primary_dark"]
SCARLET = COLORS["accent"]
TAUPE   = COLORS["text_secondary"]
SKIN    = COLORS["primary_light"]
BG      = COLORS["bg_secondary"]
OK      = COLORS["success"]
OK_BG   = COLORS["success"]
WARN    = COLORS["warning"]
WARN_BG = COLORS["warning"]
BAD     = COLORS["danger"]
BORDER  = COLORS["border"]
GRID    = COLORS["grid"]
CATEGORICAL_PALETTE = CATEGORICAL
MUTED   = COLORS["text_muted"]

C = COLORS

CSS = f"""
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  .stApp {{ background: {C['bg_secondary']}; font-family: 'Inter', 'Segoe UI', sans-serif; color: {C['text']}; }}
  .block-container {{ padding-top: 1.5rem; max-width: 1280px; }}
  h1, h2, h3 {{ font-family: 'Inter', 'Segoe UI', sans-serif !important; }}

  [data-testid="stPlotlyChart"] {{ border-radius: 10px; overflow: hidden; }}

  .report-header {{
    background: {C['bg']}; border: 1px solid {C['border']}; border-left: 4px solid {C['primary']};
    padding: 18px 26px; border-radius: 10px; margin-bottom: 18px;
    display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px;
  }}
  .report-brand {{ font-size: 10px; font-weight: 600; letter-spacing: 0.16em; text-transform: uppercase; color: {C['text_muted']}; margin-bottom: 4px; }}
  .report-title {{ font-size: 22px; font-weight: 700; color: {C['text']}; }}
  .report-title span {{ color: {C['accent']}; }}
  .report-meta {{ color: {C['text_secondary']}; font-size: 12px; margin-top: 3px; }}
  .report-badge {{
    background: {C['primary_light']}; border: 1px solid {C['border']}; border-radius: 6px;
    padding: 8px 16px; font-size: 12px; color: {C['text_secondary']}; text-align: right; line-height: 1.8;
  }}
  .report-badge strong {{ color: {C['text']}; }}

  .section-title {{
    display: flex; align-items: center; gap: 10px; font-size: 11px; font-weight: 700;
    letter-spacing: 0.12em; text-transform: uppercase; color: {C['text_secondary']};
    margin: 30px 0 14px 0; padding-bottom: 8px; border-bottom: 1px solid {C['border']};
  }}
  .section-title::before {{ content: ''; display: block; width: 3px; height: 14px; background: {C['primary']}; border-radius: 2px; }}

  .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 12px; margin-bottom: 6px; }}
  .card {{ background: {C['bg']}; border: 1px solid {C['border']}; border-radius: 10px; padding: 16px 18px; position: relative; box-shadow: 0 1px 3px rgba(30,41,59,0.06); }}
  .card::before {{ content: ''; position: absolute; top: 0; left: 0; right: 0; height: 3px; border-radius: 10px 10px 0 0; }}
  .card.c-neutral::before {{ background: {C['primary']}; }}
  .card.c-ok::before {{ background: {C['success']}; }}
  .card.c-warn::before {{ background: {C['warning']}; }}
  .card.c-bad::before {{ background: {C['danger']}; }}
  .c-label {{ font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.07em; color: {C['text_secondary']}; margin-bottom: 8px; }}
  .c-value {{ font-size: 24px; font-weight: 700; line-height: 1.1; color: {C['text']}; white-space: nowrap; }}
  .card.c-ok .c-value {{ color: {C['success']}; }}
  .card.c-warn .c-value {{ color: {C['warning']}; }}
  .card.c-bad .c-value {{ color: {C['danger']}; }}
  .c-sub {{ font-size: 11px; color: {C['text_secondary']}; margin-top: 6px; }}
  .c-ref {{ font-size: 10px; color: {C['text_muted']}; margin-top: 2px; }}
  .c-delta {{ font-size: 11px; font-weight: 600; margin-top: 6px; }}

  .bench-row {{ display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 10px; }}
  .bench {{ background: {C['primary_light']}; border: 1px solid {C['border']}; border-radius: 5px; padding: 3px 10px; font-size: 10px; color: {C['text_secondary']}; font-weight: 500; }}
  .bench strong {{ color: {C['text']}; }}

  .note {{
    font-size: 12px; color: {C['text_secondary']}; line-height: 1.6; margin-top: 6px;
    background: {C['bg']}; border: 1px solid {C['border']}; border-radius: 8px; padding: 10px 14px;
  }}
  .note strong {{ color: {C['text']}; }}
  .note.n-warn {{ border-left: 3px solid {C['warning']}; background: {C['warning_bg']}; color: {C['text']}; }}

  .tag {{ display: inline-block; padding: 2px 9px; border-radius: 4px; font-size: 10px; font-weight: 700; }}
  .t-ok    {{ background: {C['success_bg']}; color: {C['success']}; }}
  .t-warn  {{ background: {C['warning_bg']}; color: {C['warning']}; }}
  .t-bad   {{ background: {C['danger_bg']};  color: {C['danger']}; }}
  .t-muted {{ background: {C['bg_secondary']}; color: {C['text_secondary']}; }}

  .insights {{ display: flex; flex-direction: column; gap: 12px; margin-bottom: 6px; }}
  .insight {{
    background: {C['bg']}; border: 1px solid {C['border']}; border-left: 3px solid {C['text_muted']};
    border-radius: 0 8px 8px 0; padding: 16px 20px; box-shadow: 0 1px 3px rgba(30,41,59,0.06);
    display: flex; gap: 12px;
  }}
  .insight.i-bad  {{ border-left-color: {C['danger']}; }}
  .insight.i-warn {{ border-left-color: {C['warning']}; }}
  .insight.i-ok   {{ border-left-color: {C['success']}; }}
  .insight-icon {{
    width: 28px; height: 28px; border-radius: 6px; flex-shrink: 0;
    display: flex; align-items: center; justify-content: center; font-size: 13px;
  }}
  .insight.i-bad  .insight-icon {{ background: {C['danger_bg']}; }}
  .insight.i-warn .insight-icon {{ background: {C['warning_bg']}; }}
  .insight.i-ok   .insight-icon {{ background: {C['success_bg']}; }}
  .insight-content {{ flex: 1; min-width: 0; }}
  .insight-label {{ font-size: 9px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em; color: {C['text_secondary']}; margin-bottom: 4px; }}
  .insight-title {{ font-size: 14px; font-weight: 600; color: {C['text']}; margin-bottom: 6px; }}
  .insight-body {{ font-size: 12px; color: {C['text_secondary']}; line-height: 1.6; margin-bottom: 10px; }}
  .insight-action {{ background: {C['bg_secondary']}; border: 1px solid {C['border']}; border-radius: 6px; padding: 10px 14px; }}
  .insight-action-lbl {{ font-size: 9px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.07em; color: {C['text_secondary']}; margin-bottom: 6px; }}
  .insight-action ul {{ margin: 0; padding-left: 0; list-style: none; }}
  .insight-action li {{ font-size: 11px; color: {C['text']}; margin-bottom: 4px; padding-left: 14px; position: relative; }}
  .insight-action li:last-child {{ margin-bottom: 0; }}
  .insight-action li::before {{ content: '→'; color: {C['primary']}; position: absolute; left: 0; font-weight: 700; }}

  .opportunities {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 16px; margin-bottom: 6px; }}
  .opp-card {{
    background: {C['bg']}; border: 1px solid {C['border']}; border-top: 2px solid {C['primary']};
    border-radius: 0 0 10px 10px; padding: 20px 22px; box-shadow: 0 1px 3px rgba(30,41,59,0.05);
  }}
  .opp-title {{ font-size: 14px; font-weight: 600; color: {C['text']}; margin-bottom: 6px; }}
  .opp-desc {{ font-size: 12px; color: {C['text_secondary']}; line-height: 1.6; margin-bottom: 10px; }}
  .opp-gain {{
    display: inline-block; background: {C['success_bg']}; border: 1px solid {C['success']};
    color: {C['success']}; font-size: 11px; font-weight: 600; padding: 4px 10px; border-radius: 5px; margin-bottom: 10px;
  }}
  .opp-where {{ font-size: 11px; color: {C['text_secondary']}; margin-bottom: 10px; }}
  .opp-where strong {{ color: {C['text']}; }}
  .opp-how-label {{ font-size: 9px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.07em; color: {C['text_secondary']}; margin-bottom: 6px; }}
  .opp-how {{ margin: 0; padding-left: 18px; font-size: 11px; color: {C['text']}; }}
  .opp-how li {{ margin-bottom: 4px; }}
  .opp-how li:last-child {{ margin-bottom: 0; }}
</style>
"""

_INSIGHT_ICONE = {"bad": "🔴", "warn": "🟡", "ok": "🟢"}
_INSIGHT_ROTULO = {"bad": "Urgente", "warn": "Atenção", "ok": "Positivo"}


def inject_css():
    st.html(CSS)


def card(label, value, sub="", ref="", variant="neutral", delta="", delta_color=""):
    """`delta` = texto da variação (ex.: "+12,3% vs. mês anterior"), `delta_color`
    = cor já resolvida (use kpi_delta_color). Sempre com sinal no texto — a cor
    nunca é o único sinal (daltonismo)."""
    ref_html = f'<div class="c-ref">{ref}</div>' if ref else ""
    delta_html = f'<div class="c-delta" style="color:{delta_color}">{delta}</div>' if delta else ""
    return f"""<div class="card c-{variant}">
        <div class="c-label">{label}</div>
        <div class="c-value">{value}</div>
        <div class="c-sub">{sub}</div>
        {delta_html}
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


def note(html, variant=""):
    cls = f"note n-{variant}" if variant else "note"
    st.html(f'<div class="{cls}">{html}</div>')


def tag(text, variant):
    return f'<span class="tag t-{variant}">{text}</span>'


def brl(v, casas=2):
    """Formata em reais no padrão brasileiro: R$ 1.234,56."""
    if v is None or v != v:
        return "—"
    s = f"{abs(v):,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{'−' if v < 0 else ''}R$ {s}"


def pct(v, casas=1):
    """Formata razão (0,642) como 64,2%."""
    if v is None or v != v:
        return "—"
    return f"{v * 100:.{casas}f}".replace(".", ",") + "%"


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


def util_variant(pct_):
    if pct_ >= 1.0:
        return "bad"
    if pct_ < 0.7:
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
    """Aplica ajustes comuns por cima do template `ecommerce` (theme.py)."""
    layout = dict(margin=dict(l=10, r=10, t=10, b=10), legend=dict(orientation="h", y=1.12, font=dict(size=11)))
    layout.update(kwargs)
    fig.update_layout(**layout)
    fig.update_traces(marker_cornerradius=4, selector=dict(type="bar"))
    return fig
