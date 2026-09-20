"""
Design system de cores dos relatórios de e-commerce.
Uso:
    from theme import COLORS, METRIC_COLORS, CATEGORICAL, kpi_delta_color
    import theme  # registra o template Plotly "ecommerce" como padrão
"""
import plotly.graph_objects as go
import plotly.io as pio

# ---------------------------------------------------------------------------
# 1. Cores de interface
# ---------------------------------------------------------------------------
COLORS = {
    # Marca
    "primary": "#2B4C7E",        # azul-marinho: identidade, métrica principal
    "primary_dark": "#1B3558",
    "primary_light": "#EAF1FB",  # fundos de destaque suaves
    "accent": "#F28C28",         # laranja: chamar atenção, meta, comparação

    # Neutros
    "text": "#1E293B",
    "text_secondary": "#475569",
    "text_muted": "#94A3B8",     # legendas, rótulos de eixo
    "border": "#E2E8F0",
    "grid": "#EEF2F6",           # linhas de grade dos gráficos
    "bg": "#FFFFFF",
    "bg_secondary": "#F4F6F9",

    # Semânticas (status / variação)
    "success": "#16A34A",
    "success_bg": "#DCFCE7",
    "warning": "#D97706",
    "warning_bg": "#FEF3C7",
    "danger": "#DC2626",
    "danger_bg": "#FEE2E2",
    "info": "#0284C7",
    "info_bg": "#E0F2FE",
}

# ---------------------------------------------------------------------------
# 2. Cores de dados
# ---------------------------------------------------------------------------
# Categórica: para canais, categorias, marketplaces. Use na ordem.
CATEGORICAL = [
    "#2B4C7E",  # azul-marinho
    "#F28C28",  # laranja
    "#3BA99C",  # verde-água
    "#8E6BBF",  # roxo
    "#E4577A",  # rosa
    "#7CA84F",  # verde-oliva
    "#D4A72C",  # mostarda
    "#5B8DB8",  # azul-claro
]

# Sequencial: intensidade (heatmaps, mapas de vendas por estado)
SEQUENTIAL = ["#EAF1FB", "#D0E0F4", "#AFC8E8", "#8AAED9",
              "#6592C6", "#4677B0", "#2B4C7E", "#1B3558"]

# Divergente: variação vs. período anterior/meta (laranja = abaixo, azul = acima)
DIVERGING = ["#B45309", "#F28C28", "#FCD9B0", "#F1F5F9",
             "#BFD3EE", "#5B8DB8", "#2B4C7E"]

# Cor fixa por métrica — a mesma métrica tem sempre a mesma cor em todos os relatórios
METRIC_COLORS = {
    "receita": "#2B4C7E",
    "pedidos": "#5B8DB8",
    "ticket_medio": "#8E6BBF",
    "conversao": "#3BA99C",
    "sessoes": "#94A3B8",
    "meta": "#F28C28",
    "devolucoes": "#DC2626",
    "cancelamentos": "#E4577A",
    # adicionadas em 19/set/2026 (relatório Vendas & Margem)
    "margem_contribuicao": "#7CA84F",
    "margem_pct": "#D4A72C",
}

# Cor fixa por canal de venda (ajuste para os canais reais da empresa)
CHANNEL_COLORS = {
    "Site": CATEGORICAL[0],
    "App": CATEGORICAL[2],
    "Marketplace": CATEGORICAL[1],
    "Loja física": CATEGORICAL[3],
    "Outros": "#94A3B8",
}


def kpi_delta_color(delta: float, higher_is_better: bool = True) -> str:
    """Verde se a variação é boa, vermelho se é ruim, cinza se neutra."""
    if delta == 0:
        return COLORS["text_muted"]
    good = (delta > 0) == higher_is_better
    return COLORS["success"] if good else COLORS["danger"]


# ---------------------------------------------------------------------------
# 3. Template Plotly
# ---------------------------------------------------------------------------
pio.templates["ecommerce"] = go.layout.Template(
    layout=dict(
        font=dict(family="Inter, Segoe UI, sans-serif", size=13, color=COLORS["text"]),
        title=dict(font=dict(size=16, color=COLORS["text"]), x=0, xanchor="left"),
        colorway=CATEGORICAL,
        colorscale=dict(
            sequential=[[i / (len(SEQUENTIAL) - 1), c] for i, c in enumerate(SEQUENTIAL)],
            diverging=[[i / (len(DIVERGING) - 1), c] for i, c in enumerate(DIVERGING)],
        ),
        paper_bgcolor=COLORS["bg"],
        plot_bgcolor=COLORS["bg"],
        xaxis=dict(gridcolor=COLORS["grid"], linecolor=COLORS["border"],
                   tickfont=dict(color=COLORS["text_secondary"]), zeroline=False),
        yaxis=dict(gridcolor=COLORS["grid"], linecolor=COLORS["border"],
                   tickfont=dict(color=COLORS["text_secondary"]), zeroline=False),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0,
                    font=dict(color=COLORS["text_secondary"])),
        hoverlabel=dict(bgcolor=COLORS["bg"], bordercolor=COLORS["border"],
                        font=dict(color=COLORS["text"])),
        margin=dict(l=40, r=20, t=60, b=40),
    )
)
pio.templates.default = "ecommerce"
