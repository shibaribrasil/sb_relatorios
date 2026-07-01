# Migração — Sistema de Relatórios Shibari Brasil

Documento de planejamento e acompanhamento da migração do fluxo de relatórios de Google Ads para uma arquitetura baseada em DBT + BigQuery + Streamlit Community Cloud.

---

## Contexto e Motivação

O fluxo atual gera relatórios diretamente via Claude (IA), com queries ao BigQuery sendo construídas e executadas pela IA a cada sessão. Isso gera:

- **Alto custo de tokens** — a IA processa grandes volumes de dados brutos
- **Timeouts frequentes** — chamadas longas à API resultam em falhas
- **Falta de automação** — o relatório só existe quando explicitamente pedido
- **Dependência do PC ligado** — o DBT roda via `.bat` local

### Solução definida

```
Google Ads (BQ raw)
      ↓
  DBT (stg → az → rpt)        ← agendado via GitHub Actions
      ↓
Tabelas rpt no BigQuery        ← pequenas, agregadas, prontas
      ↓
  Streamlit Community Cloud    ← lê rpt + chama Claude só para insights
      ↓
     URL fixa do relatório
```

O Claude passa a receber apenas o snapshot das tabelas `rpt` (poucos KB), eliminando timeouts e reduzindo custo de API para centavos por geração.

---

## Decisões de Arquitetura

| Decisão | Definição |
|---|---|
| Janela de tempo padrão | Últimos 30 dias a partir do último dia fechado (`< CURRENT_DATE()`) |
| Camada de relatório | Nova camada `4.rpt` após `3.az` |
| Organização das pastas | Por assunto dentro de cada camada (igual ao padrão existente) |
| Agendamento do DBT | GitHub Actions com cron (substituição ao `.bat` local) |
| Frontend do relatório | Streamlit Community Cloud (deploy via GitHub) |
| Escopo inicial | Apenas Google Ads — ampliar para Vendas/Financeiro/Estoque depois de validar |
| **Dois targets no DBT** | `datalake_google_ads` está em região `US` e o restante em `us-east4`. Solução: target `dev` (us-east4 → `dbt_dw`) para Bling/Drive e target `us` (US → `dbt_dw_us`) para Google Ads. Modelos Google Ads sempre rodam com `--target us` |
| **Chave de serviço GCP** | Arquivo `key-dbt-bq.json` na raiz do projeto (ignorado pelo git via `*.json` no `.gitignore`). No GitHub Actions, será injetado como secret |

---

## Estrutura de Arquivos a Criar

### `models/1.stg/Google Ads/` — Staging Google Ads

Views que limpam as tabelas brutas: convertem micros → BRL, resolvem `_DATA_DATE = _LATEST_DATE`, padronizam nomes de colunas. Todos os modelos incluem `customer_id` como `cd_conta`. Nenhuma lógica de negócio aqui.

> ⚠️ Campos **não disponíveis** neste export BQ (removidos após validação):
> `metrics_search_impression_share` em `CampaignBasicStats` — IS está em `CampaignCrossDeviceStats`.
> `metrics_all_conversions` e `metrics_view_through_conversions` — não exportados para este dataset.

| Arquivo | Fonte no BQ | Responsabilidade |
|---|---|---|
| `stg_gads_campanha.sql` | `ads_Campaign_4241689372` | Estrutura e config das campanhas, filtro `_LATEST_DATE` |
| `stg_gads_campanha_stats.sql` | `ads_CampaignBasicStats_4241689372` | Métricas por campanha/dia, micros → BRL |
| `stg_gads_campanha_conversoes.sql` | `ads_CampaignConversionStats_4241689372` | Conversões por tipo por campanha/dia |
| `stg_gads_campanha_impression_share.sql` | `ads_CampaignCrossDeviceStats_4241689372` | IS completo: search, top, abs top, perda por budget e ranking |
| `stg_gads_conta_stats.sql` | `ads_AccountStats_4241689372` | Métricas consolidadas da conta por dia, micros → BRL |
| `stg_gads_grupo_anuncio.sql` | `ads_AdGroup_4241689372` | Nome e status dos grupos de anúncios, filtro `_LATEST_DATE` |
| `stg_gads_keyword.sql` | `ads_Keyword_4241689372` | Todas as keywords (ativas e negativas) com `fl_keyword_negativa`, quality score, filtro `_LATEST_DATE` |
| `stg_gads_keyword_stats.sql` | `ads_KeywordBasicStats_4241689372` | Performance por keyword/dia, micros → BRL |
| `stg_gads_anuncio.sql` | `ads_Ad_4241689372` | Criativos: RSA (headlines, descriptions, paths), ETA legado, ad strength, status de aprovação, filtro `_LATEST_DATE` |

### `models/3.az/Google Ads/` — Analytics Google Ads

Tabelas com joins e cálculos de métricas derivadas (CTR, CPC, ROAS, CPA, utilização de budget). Sem filtro de janela de tempo — isso fica na `rpt`.

```
3.az/Google Ads/
├── Campanhas/
│   ├── tb_gads_campanha_performance.sql    # join Campaign + Stats → CTR, CPC, ROAS, CPA por campanha
│   ├── tb_gads_campanha_orcamento.sql      # budget, gasto médio diário, % utilização
│   ├── tb_gads_campanha_conversoes.sql     # conversões por tipo (PURCHASE vs micro) por campanha
│   └── tb_gads_campanha_impression_share.sql  # IS agregado por campanha (fonte: stg_campanha_impression_share)
├── Keywords/
│   ├── tb_gads_keyword_quality.sql         # keywords ativas (fl_keyword_negativa=false) + quality score
│   └── tb_gads_keyword_performance.sql     # keyword + gasto + cliques + conversões (join com grupo_anuncio)
├── Anuncios/
│   └── tb_gads_anuncio.sql                 # criativos ativos: RSA/ETA + ad strength + join com campanha e grupo
└── Conta/
    └── tb_gads_conta_diario.sql            # série diária agregada de toda a conta
```

### `models/4.rpt/Google Ads/` — Reporting Layer (nova camada)

Tabelas pequenas e específicas. Aplicam a janela de 30 dias e selecionam exatamente as colunas que o Streamlit vai consumir. O Claude recebe essas tabelas diretamente — sem processar nada adicional.

| Arquivo | Baseada em | O que entrega ao Streamlit |
|---|---|---|
| `rpt_gads_resumo_conta.sql` | `tb_gads_conta_diario` | 1 linha: totais do período (custo, impressões, cliques, conversões, ROAS, CPA) |
| `rpt_gads_performance_campanhas.sql` | `tb_gads_campanha_performance` | 1 linha por campanha: métricas completas do período |
| `rpt_gads_tendencia_diaria.sql` | `tb_gads_conta_diario` | 1 linha por dia: série temporal dos últimos 30 dias |
| `rpt_gads_conversoes_tipo.sql` | `tb_gads_campanha_conversoes` | Conversões separadas: PURCHASE vs micro-conversões |
| `rpt_gads_orcamento.sql` | `tb_gads_campanha_orcamento` | Budget configurado vs gasto médio real + % utilização por campanha |
| `rpt_gads_keywords_top.sql` | `tb_gads_keyword_performance` + `tb_gads_keyword_quality` | Top 20 keywords ativas por gasto com quality score |
| `rpt_gads_impression_share.sql` | `tb_gads_campanha_impression_share` | IS, perda por budget e por ranking por campanha |
| `rpt_gads_anuncios.sql` | `tb_gads_anuncio` | Criativos ativos com ad strength — insumo para análise de copy |

---

## Checklist de Execução

### Fase 1 — DBT: Source e Staging ✅

- [x] Adicionar source `google_ads` no `models/source.yml`
  - Tabelas: `ads_Campaign`, `ads_CampaignBasicStats`, `ads_CampaignConversionStats`, `ads_Keyword`, `ads_KeywordBasicStats`, `ads_AccountStats`, `ads_AdGroup`, `ads_CampaignCrossDeviceStats`, `ads_Ad`
- [x] Criar target `us` no `profiles.yml` (região US para Google Ads)
- [x] Criar dataset `dbt_dw_us` no BQ (região US)
- [x] Criar pasta `models/1.stg/Google Ads/`
- [x] Criar `stg_gads_campanha.sql`
- [x] Criar `stg_gads_campanha_stats.sql`
- [x] Criar `stg_gads_campanha_conversoes.sql`
- [x] Criar `stg_gads_campanha_impression_share.sql` (fonte: `CampaignCrossDeviceStats`)
- [x] Criar `stg_gads_conta_stats.sql`
- [x] Criar `stg_gads_grupo_anuncio.sql`
- [x] Criar `stg_gads_keyword.sql` (inclui negativas com `fl_keyword_negativa`)
- [x] Criar `stg_gads_keyword_stats.sql`
- [x] Criar `stg_gads_anuncio.sql` (RSA, ETA, ad strength, aprovação)
- [x] Rodar `dbt run --select tag:stg,tag:google_ads --target us` — **9/9 passou**

### Fase 2 — DBT: Camada Analytics (az) ✅

- [x] Criar pasta `models/3.az/Google Ads/Campanhas/`
- [x] Criar `tb_gads_campanha_performance.sql` (join campanha + stats → CTR, CPC, ROAS, CPA)
- [x] Criar `tb_gads_campanha_orcamento.sql` (budget vs gasto médio + % utilização)
- [x] Criar `tb_gads_campanha_conversoes.sql` (PURCHASE vs micro-conversões por campanha)
- [x] Criar `tb_gads_campanha_impression_share.sql` (IS agregado — fonte: `stg_campanha_impression_share`)
- [x] Criar pasta `models/3.az/Google Ads/Keywords/`
- [x] Criar `tb_gads_keyword_quality.sql` (filtrar `fl_keyword_negativa = false`)
- [x] Criar `tb_gads_keyword_performance.sql` (join com `stg_gads_grupo_anuncio` para nome do grupo)
- [x] Criar pasta `models/3.az/Google Ads/Anuncios/`
- [x] Criar `tb_gads_anuncio.sql` (join anuncio + campanha + grupo → criativos contextualizados)
- [x] Criar pasta `models/3.az/Google Ads/Conta/`
- [x] Criar `tb_gads_conta_diario.sql` (série diária agregada da conta)
- [x] Rodar `dbt run --select tag:az,tag:google_ads --target us` — **8/8 passou**

### Fase 3 — DBT: Camada Reporting (rpt) ✅

- [x] Adicionar configuração da camada `4.rpt` no `dbt_project.yml`
  - Materialização: `table`, schema: `rpt`, tag: `rpt`
- [x] Criar pasta `models/4.rpt/Google Ads/`
- [x] Criar `rpt_gads_resumo_conta.sql`
- [x] Criar `rpt_gads_performance_campanhas.sql`
- [x] Criar `rpt_gads_tendencia_diaria.sql`
- [x] Criar `rpt_gads_conversoes_tipo.sql`
- [x] Criar `rpt_gads_orcamento.sql`
- [x] Criar `rpt_gads_keywords_top.sql`
- [x] Criar `rpt_gads_impression_share.sql`
- [x] Criar `rpt_gads_anuncios.sql`
- [x] Rodar `dbt run --select tag:rpt --target us` — **8/8 passou**
- [ ] Inspecionar tabelas no BQ e validar dados das tabelas `rpt`

### Fase 4 — GitHub Actions (substituição ao .bat) ✅

- [x] Subir projeto `sb_dw_dbt` para repositório GitHub (privado)
- [x] Adicionar chave `key-dbt-bq.json` como secret no GitHub (`GCP_SERVICE_ACCOUNT_KEY`)
- [x] Adaptar `profiles.yml` para caminho relativo (funciona local e no Actions)
- [x] Criar `.github/workflows/dbt_run.yml` com cron horário 08h-23h BRT
  - Rodar modelos Bling/Drive com `--target dev`
  - Rodar modelos Google Ads com `--target us`
- [x] Testar dispatch manual do workflow no GitHub
- [ ] Confirmar tabelas `rpt` atualizadas no BQ após o run

### Fase 5 — Streamlit (relatório com URL fixa) ✅

- [x] Criar repositório `sb_relatorios` no GitHub
- [x] Criar `app.py` — app Streamlit que:
  - [x] Lê as 8 tabelas `rpt` do BQ via `google-cloud-bigquery`
  - [x] Monta os gráficos (Plotly)
  - [ ] Chama Claude API com o snapshot das tabelas `rpt` — **ainda não implementado**
  - [ ] Exibe insights gerados pela IA junto aos gráficos — **ainda não implementado**
- [x] Criar `requirements.txt` com dependências
- [x] Criar `secrets.toml` local (não comitado) para credenciais de dev
- [x] Deploy no Streamlit Community Cloud conectado ao repositório
- [x] Configurar secrets no Streamlit Cloud (GCP key)
- [x] Validar URL fixa do relatório funcionando end-to-end

> A camada de dados está completa e em produção. Os insights via Claude API (badge "Diagnóstico Executivo" / "Oportunidades" no modelo HTML de referência) ficam para uma fase futura — ver Fase 6.

### Fase 6 — Design Visual do Relatório ✅

Referência de design: `sb_marketing_team/relatorios/diagnostico-google-ads/relatorio-diagnostico.html` — relatório HTML feito para o time de marketing, com tema claro (plum/scarlet/skin sobre fundo creme, fontes Playfair Display + Montserrat), seções numeradas, cards de KPI, benchmarks acima dos gráficos e notas explicativas dos indicadores logo abaixo de cada gráfico/tabela.

**Levantamento — dados do HTML que já existem nas tabelas `rpt` (sem precisar de IA):**

| Seção do HTML | Coberto pelas tabelas `rpt`? |
|---|---|
| 1 — Saúde Financeira (custo, receita, ROI, ROAS, CPA) | ✅ `rpt_gads_resumo_conta` já tem `vl_conversoes_total` (receita) — só não estava sendo exibido no app |
| 2 — Performance por Campanha (tabela completa) | ✅ `rpt_gads_performance_campanhas` tem todos os campos (faltava exibir receita e conv. rate) |
| 3 — Tendência diária + Conversões por tipo | ✅ `rpt_gads_tendencia_diaria` já tem `qt_cliques` para o duplo eixo; `rpt_gads_conversoes_tipo` cobre o quadro de eventos |
| 4 — Keywords / Quality Score | ✅ `rpt_gads_keywords_top` |
| 6 — Orçamento (budget vs. gasto, utilização) | ✅ `rpt_gads_orcamento` — status (Limitado/Normal/Subutilizado) calculado por regra simples (>=100%/70-99%/<70%), sem IA |
| Impression Share (budget loss vs. ranking loss) | ✅ `rpt_gads_impression_share` — aliás mais completo que o HTML de referência, que marcava IS como indisponível |
| Anúncios ativos / Ad Strength | ✅ `rpt_gads_anuncios` |
| Diagnóstico Executivo + Oportunidades (insights) | ❌ Gerado por IA no HTML — fica para a Fase de insights (Claude API), fora do escopo de design |
| Landing Page por intenção de busca | ⚠️ Requer classificar keyword por intenção (curadoria manual ou IA) — não é extraível direto das tabelas `rpt` atuais; não implementado |
| Orçamento recomendado + projeção 30 dias | ⚠️ Era cálculo manual/IA no HTML, não regra fixa — fica para a fase de insights |

**Checklist:**

- [x] Redesenhar `sb_relatorios/app.py` com o design system do HTML (cores, fontes, cards, notas)
- [x] Reorganizar em 7 seções numeradas (Saúde Financeira, Performance, Tendência+Conversões, Orçamento, Keywords, Impression Share, Anúncios)
- [x] Adicionar nota explicativa com benchmark abaixo de cada gráfico/tabela
- [x] Exibir Receita e ROI (já existiam em `rpt_gads_resumo_conta`, só não eram mostrados)
- [x] Colorir ROAS/Utilização/Quality Score por faixa (regra fixa, sem IA)
- [ ] Validar visualmente no Streamlit Cloud após o deploy desta versão
- [ ] Seção "Landing Page por intenção de busca" — decidir se vale curar manualmente ou esperar a fase de insights
- [ ] Seção "Diagnóstico Executivo" / "Oportunidades" — depende da Fase 7 (Claude API)

### Fase 7 — Insights via Claude API

- [ ] Implementar chamada à Claude API com o snapshot das tabelas `rpt`
- [ ] Exibir insights gerados pela IA (seção "Diagnóstico Executivo" / "Oportunidades", no estilo do HTML de referência)
- [ ] Configurar secret da Anthropic API key no Streamlit Cloud
- [ ] Medir custo de tokens da chamada no novo fluxo

### Fase 8 — Validação e Ajustes

- [x] Corrigido: Receita/ROI/ROAS/CPA somavam TODAS as conversões (Purchase + Page View + Add to Cart + Begin Checkout), inflando o retorno em ~16x. `tb_gads_conta_diario` e `tb_gads_campanha_performance` agora filtram só `ds_categoria_conversao = 'PURCHASE'` (fonte: `stg_gads_campanha_conversoes`)
- [ ] Comparar dados do Streamlit com relatório atual gerado pelo Claude
- [ ] Validar que a janela de 30 dias está correta em todas as tabelas `rpt`
- [ ] Ajustar prompts de insight se necessário
- [ ] Documentar URL final do relatório

### Fase 9 — Arquitetura Multi-página + Especificação de Regras de Negócio

O bug do ROI/ROAS (Fase 8) mostrou o risco de regra de negócio não documentada — a categoria PURCHASE só estava "na cabeça" de quem revisou o número, não em nenhum lugar do projeto. Diretriz do projeto: um único link fixo do Streamlit, com várias páginas internas (uma por relatório: Google Ads, futuramente Vendas/Financeiro/Estoque), e uma forma explícita de registrar/consultar regras de negócio e especificação de cada relatório antes de mexer no código.

**Abordagem decidida** (discutida em conversa em 2026-07-01): multi-página via `st.navigation()`/`st.Page()` (API nativa do Streamlit ≥1.36, já coberta pela versão fixada `1.58.0`) + especificação de negócio em Markdown por relatório (`specs/<relatorio>.md`). Estrutura alvo:

```
sb_relatorios/
  app.py                  # entrypoint (nome fixo — é o "Main file path" do Streamlit Cloud, não dá pra trocar depois do deploy): registra as páginas via st.navigation()
  common/
    design.py            # tema, CSS, cards, section_title, note, style_color
    bigquery.py           # client BigQuery e helpers de query compartilhados
  reports/
    google_ads.py         # o que antes era o conteúdo inteiro do app.py
  specs/
    google-ads.md          # regras de negócio e especificação de indicadores
```

Documentado também em `CLAUDE.md` (raiz do projeto) como convenção permanente.

**Passo a passo** (executar devagar, um passo por vez, validando antes de seguir para o próximo):

- [x] 1. Criar `CLAUDE.md` documentando a arquitetura e a regra "spec antes de código"
- [x] 2. Detalhar este passo a passo aqui no `MIGRACAO-RELATORIOS.md`
- [x] 3. Criar `specs/google-ads.md` documentando os indicadores do relatório atual (fonte, fórmula, regra de negócio — incluindo a regra PURCHASE-only —, benchmark, limitações conhecidas), a partir do que já está implementado em `app.py`
- [x] 4. Criar `common/design.py` extraindo do `app.py` atual: constantes de cor, CSS, `card()`, `render_cards()`, `section_title()`, `bench_row()`, `note()`, `tag()`, `style_color()`, `plotly_layout()` — sem mudar comportamento
- [x] 5. Criar `common/bigquery.py` extraindo `carregar_dados()`/setup do client — sem mudar comportamento
- [x] 6. Criar `reports/google_ads.py` movendo a lógica de renderização do relatório para uma função de página, usando `common/design.py` e `common/bigquery.py`
- [x] 7. Criar `streamlit_app.py` com `st.navigation()` registrando a página de Google Ads
- [x] 8. Testar localmente (AppTest com dados simulados, como nas correções da Fase 8) antes de considerar pronto
- [x] 9. **Ajuste de rota:** o Streamlit Cloud não permite trocar o "Main file path" pela interface depois do deploy. Em vez de usar `streamlit_app.py` como arquivo separado, o conteúdo dele foi movido para dentro do `app.py` (que já é o entrypoint configurado) — `streamlit_app.py` foi removido. Nenhuma configuração do Streamlit Cloud precisou ser alterada. Testado com dados reais do BigQuery via `AppTest.from_file("app.py")` — OK
- [ ] 10. Deploy e validação visual no Streamlit Cloud
- [ ] 11. ~~Remover o `app.py` antigo~~ — não se aplica mais (ver passo 9); o `app.py` antigo (monolítico) foi substituído pelo novo entrypoint no mesmo passo

---

## Ampliações Futuras (pós-validação Google Ads)

Seguir o mesmo padrão de 3 camadas para cada novo escopo:

- [ ] **Vendas** — `rpt_vendas_resumo_mes`, `rpt_vendas_tendencia_diaria`, `rpt_vendas_top_produtos`
- [ ] **Financeiro** — `rpt_financeiro_resultado_mes`, `rpt_financeiro_meta_atingimento`
- [ ] **Estoque** — `rpt_estoque_alertas` (apenas Rompido + Urgente + Atenção)

---

## Referências

- **Projeto BQ:** `igneous-sandbox-381622`
- **Dataset Google Ads:** `datalake_google_ads`
- **ID da conta Google Ads:** `4241689372` (sufixo de todas as tabelas)
- **Schema DBT target (Bling/Drive):** `dbt_dw` em `us-east4` (schemas: `dbt_dw_stg`, `dbt_dw_az`)
- **Schema DBT target (Google Ads):** `dbt_dw_us` em `US` (schemas: `dbt_dw_us_stg`, `dbt_dw_us_az`, `dbt_dw_us_rpt`)
- **Queries originais:** `sb_marketing_team/relatorios/diagnostico-google-ads/queries.sql`
- **Referência de design do relatório:** `sb_marketing_team/relatorios/diagnostico-google-ads/relatorio-diagnostico.html`
- **Repositório do projeto DBT:** [`shibaribrasil/sb_dw_dbt`](https://github.com/shibaribrasil/sb_dw_dbt) (staging → analytics → reporting)
