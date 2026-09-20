"""Entrypoint multi-página — Shibari Brasil.

Este é o "Main file path" configurado no Streamlit Cloud (não dá para trocar
pela interface depois do deploy, então o entrypoint fica sendo sempre
app.py). Registra um relatório por página via st.navigation(). Ver CLAUDE.md
e MIGRACAO-RELATORIOS.md (Fase 9) para a arquitetura. Novo relatório = novo
arquivo em reports/ + entrada aqui.
"""
import streamlit as st

from reports import estoque, google_ads, vendas_margem

st.set_page_config(
    page_title="Relatórios — Shibari Brasil",
    page_icon="📊",
    layout="wide",
)

# Menu agrupado por cadência (ver planejamento de relatórios): cada relatório
# entra na camada em que a decisão acontece.
pages = {
    "Mensal": [
        st.Page(vendas_margem.render, title="Vendas & Margem", icon="🛒", url_path="vendas", default=True),
    ],
    "Semanal": [
        st.Page(estoque.render, title="Estoque", icon="📦", url_path="estoque"),
    ],
    "Marketing": [
        st.Page(google_ads.render, title="Google Ads", icon="📊", url_path="google-ads"),
    ],
}

pg = st.navigation(pages)
pg.run()
