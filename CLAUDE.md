# sb_relatorios

App Streamlit que exibe relatórios de marketing da Shibari Brasil, lendo dados já prontos das tabelas `rpt` no BigQuery (produzidas pelo projeto `sb_dw_dbt`). Deploy no Streamlit Community Cloud, com deploy automático a cada push na branch `main`.

## Arquitetura

Um único link fixo do Streamlit, com várias páginas internas — uma por relatório:

```
sb_relatorios/
  app.py                 # entrypoint ("Main file path" do Streamlit Cloud): registra as páginas via st.navigation(), agrupadas por cadência
  common/
    theme.py             # design system de dados (cores, template Plotly) — fonte única de cor; separado da identidade da marca
    design.py            # CSS e componentes (card, section_title, note, brl, pct) sobre o theme.py — compartilhado por todo relatório
    bigquery.py           # client BigQuery e helpers de query compartilhados
  reports/
    google_ads.py         # lógica e layout do relatório de Google Ads
    vendas_margem.py      # Vendas & Margem de Contribuição (lê dbt_dw_az.tb_pedido; sem tabela rpt)
    estoque.py
    (futuro) pulso do dia, clientes, produtos, marketing
  specs/
    google-ads.md          # regras de negócio e especificação de indicadores do relatório
  content/
    acoes-google-ads.md    # log manual de ações tomadas na conta — editado pelo usuário, formatado por IA
```

## Log de ações tomadas (`content/`)

Cada relatório pode ter um `content/acoes-<relatorio>.md` — log manual de ações tomadas na conta (ex.: "pausei campanha X", "ajustei orçamento de Y"), editado pelo usuário. Fluxo: o usuário passa notas informais sobre uma ação e pede pra formatar como entrada nova, seguindo o template documentado no topo do próprio arquivo. Esse log alimenta seções do relatório que mostram as últimas ações e avaliam se surtiram resultado — ver `specs/<relatorio>.md` para os detalhes de cada relatório.

Cada novo relatório é uma página nova em `reports/`, registrada em `app.py`. Regra de negócio mora na camada `az` do `sb_dw_dbt` (validada, com teste); o Streamlit só filtra, soma e apresenta — nunca recalcula margem, lucro ou taxa. Cores só vêm de `common/theme.py`. Código de tema/estilo/conexão BigQuery deve morar em `common/`, nunca duplicado por relatório.

## Regra: especificação de negócio antes de código

Todo relatório tem um arquivo em `specs/<nome-do-relatorio>.md` documentando, por indicador: fonte de dados (tabela `rpt`), fórmula, **regra de negócio** (ex.: "ROI/ROAS/CPA consideram só conversões de categoria `PURCHASE`, não a soma de todas as conversões do Google Ads"), benchmark de referência e limitações conhecidas.

**Antes de adicionar ou alterar qualquer indicador de um relatório, leia o spec correspondente.** Se a mudança envolve uma regra de negócio nova ou revista, atualize o spec no mesmo commit da mudança de código — nunca deixe a regra só implícita no SQL ou no app.

Essa regra existe porque um bug real já aconteceu por causa disso: o relatório de Google Ads somava receita de TODAS as conversões (incluindo Page View), inflando ROI/ROAS em ~16x, porque a regra "só Purchase conta como retorno" nunca tinha sido escrita em lugar nenhum — só existia na cabeça de quem revisava o número manualmente. Ver `specs/google-ads.md` e `MIGRACAO-RELATORIOS.md` (Fase 8) para o histórico.

## Dependências e versionamento

`requirements.txt` fixa versões **exatas** (`==`), não faixas (`>=`). O Streamlit Community Cloud resolve dependências a cada deploy; sem pin exato, uma versão nova do pandas/streamlit pode quebrar o app sem nenhuma mudança de código (já aconteceu — `pandas` removeu `Styler.applymap` entre versões). Ao atualizar uma dependência, atualize a versão exata no `requirements.txt` deliberadamente, não deixe subir sozinho.

## Repositório relacionado

- **`sb_dw_dbt`** — projeto dbt que gera as tabelas `rpt` consumidas aqui (staging → analytics → reporting). Mudança de regra de negócio que afeta cálculo (ex.: o que conta como conversão) mora lá, na camada `az`/`rpt`; o Streamlit só lê o resultado já pronto.
