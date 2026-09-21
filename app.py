"""Entrypoint multi-página — Shibari Brasil.

Este é o "Main file path" configurado no Streamlit Cloud (não dá para trocar
pela interface depois do deploy, então o entrypoint fica sendo sempre
app.py). Registra um relatório por página via st.navigation(). Ver CLAUDE.md
e MIGRACAO-RELATORIOS.md (Fase 9) para a arquitetura. Novo relatório = novo
arquivo em reports/ + entrada aqui.
"""
import streamlit as st

from reports import canais_unit_economics, clientes, estoque, google_ads, produtos_mix, pulso_dia, site_funil, vendas_margem, vendas_semana

st.set_page_config(
    page_title="Relatórios — Shibari Brasil",
    page_icon="📊",
    layout="wide",
)

# Menu agrupado por cadência (ver planejamento de relatórios): cada relatório
# entra na camada em que a decisão acontece.
pages = {
    "Diário": [
        st.Page(pulso_dia.render, title="Pulso do Dia", icon="⚡", url_path="dia", default=True),
    ],
    "Mensal": [
        st.Page(vendas_margem.render, title="Vendas & Margem", icon="🛒", url_path="vendas"),
        st.Page(clientes.render, title="Clientes & Coorte", icon="👥", url_path="clientes"),
        st.Page(produtos_mix.render, title="Produtos & Mix", icon="🧩", url_path="produtos"),
        st.Page(canais_unit_economics.render, title="Canais & Unit Economics", icon="🧮", url_path="canais"),
    ],
    "Semanal": [
        st.Page(vendas_semana.render, title="Vendas da Semana", icon="📅", url_path="semana"),
        st.Page(site_funil.render, title="Site & Funil", icon="🔻", url_path="funil"),
        st.Page(estoque.render, title="Estoque", icon="📦", url_path="estoque"),
    ],
    "Marketing": [
        st.Page(google_ads.render, title="Google Ads", icon="📊", url_path="google-ads"),
    ],
}

pg = st.navigation(pages)
pg.run()
