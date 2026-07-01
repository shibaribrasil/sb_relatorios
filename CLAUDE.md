# sb_relatorios

App Streamlit que exibe relatórios de marketing da Shibari Brasil, lendo dados já prontos das tabelas `rpt` no BigQuery (produzidas pelo projeto `sb_dw_dbt`). Deploy no Streamlit Community Cloud, com deploy automático a cada push na branch `main`.

## Arquitetura

Um único link fixo do Streamlit, com várias páginas internas — uma por relatório:

```
sb_relatorios/
  streamlit_app.py       # entrypoint: registra as páginas via st.navigation()
  common/
    design.py            # tema, CSS, cards, section_title, note, style_color — compartilhado por todo relatório
    bigquery.py           # client BigQuery e helpers de query compartilhados
  reports/
    google_ads.py         # lógica e layout do relatório de Google Ads
    (futuro) vendas.py, financeiro.py, estoque.py
  specs/
    google-ads.md          # regras de negócio e especificação de indicadores do relatório
  content/
    acoes-google-ads.md    # log manual de ações tomadas na conta — editado pelo usuário, formatado por IA
  MIGRACAO-RELATORIOS.md   # histórico e checklist da migração
```

## Log de ações tomadas (`content/`)

Cada relatório pode ter um `content/acoes-<relatorio>.md` — log manual de ações tomadas na conta (ex.: "pausei campanha X", "ajustei orçamento de Y"), editado pelo usuário. Fluxo: o usuário passa notas informais sobre uma ação e pede pra formatar como entrada nova, seguindo o template documentado no topo do próprio arquivo. Esse log alimenta seções do relatório que mostram as últimas ações e avaliam se surtiram resultado — ver `specs/<relatorio>.md` para os detalhes de cada relatório.

Cada novo relatório é uma página nova em `reports/`, registrada em `streamlit_app.py`. Código de tema/estilo/conexão BigQuery deve morar em `common/`, nunca duplicado por relatório.

## Regra: especificação de negócio antes de código

Todo relatório tem um arquivo em `specs/<nome-do-relatorio>.md` documentando, por indicador: fonte de dados (tabela `rpt`), fórmula, **regra de negócio** (ex.: "ROI/ROAS/CPA consideram só conversões de categoria `PURCHASE`, não a soma de todas as conversões do Google Ads"), benchmark de referência e limitações conhecidas.

**Antes de adicionar ou alterar qualquer indicador de um relatório, leia o spec correspondente.** Se a mudança envolve uma regra de negócio nova ou revista, atualize o spec no mesmo commit da mudança de código — nunca deixe a regra só implícita no SQL ou no app.

Essa regra existe porque um bug real já aconteceu por causa disso: o relatório de Google Ads somava receita de TODAS as conversões (incluindo Page View), inflando ROI/ROAS em ~16x, porque a regra "só Purchase conta como retorno" nunca tinha sido escrita em lugar nenhum — só existia na cabeça de quem revisava o número manualmente. Ver `specs/google-ads.md` e `MIGRACAO-RELATORIOS.md` (Fase 8) para o histórico.

## Dependências e versionamento

`requirements.txt` fixa versões **exatas** (`==`), não faixas (`>=`). O Streamlit Community Cloud resolve dependências a cada deploy; sem pin exato, uma versão nova do pandas/streamlit pode quebrar o app sem nenhuma mudança de código (já aconteceu — `pandas` removeu `Styler.applymap` entre versões). Ao atualizar uma dependência, atualize a versão exata no `requirements.txt` deliberadamente, não deixe subir sozinho.

## Repositório relacionado

- **`sb_dw_dbt`** — projeto dbt que gera as tabelas `rpt` consumidas aqui (staging → analytics → reporting). Mudança de regra de negócio que afeta cálculo (ex.: o que conta como conversão) mora lá, na camada `az`/`rpt`; o Streamlit só lê o resultado já pronto.
