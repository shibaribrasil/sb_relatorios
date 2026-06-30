# sb_relatorios

Relatórios de marketing da Shibari Brasil com URL fixa, gerados automaticamente via Streamlit + Claude AI.

## O que é

App Streamlit que lê as tabelas `rpt` do BigQuery (atualizadas de hora em hora via GitHub Actions) e gera um relatório de Google Ads com:

- Visão geral da conta (custo, cliques, conversões, ROAS, CPA)
- Performance por campanha
- Tendência diária dos últimos 30 dias
- Análise de orçamento vs gasto real
- Top 20 keywords por gasto + quality score
- Impression share por campanha
- Criativos ativos com ad strength
- Insights gerados automaticamente pela Claude AI

## Arquitetura

```
BigQuery (dbt_dw_us_rpt)
        ↓
   Streamlit App          ← lê tabelas rpt (poucos KB)
        ↓
   Claude API             ← recebe snapshot das tabelas, devolve insights
        ↓
   URL fixa do relatório
```

## Dependências

- `streamlit`
- `google-cloud-bigquery`
- `plotly`
- `anthropic`
- `pandas`

## Configuração local

Crie o arquivo `.streamlit/secrets.toml` (não comitar):

```toml
[gcp]
project_id = "igneous-sandbox-381622"
# cole o conteúdo do key-dbt-bq.json aqui como string JSON

[anthropic]
api_key = "sk-..."
```

## Deploy

App hospedado no [Streamlit Community Cloud](https://streamlit.io/cloud), conectado a este repositório. Secrets configurados diretamente no painel do Streamlit Cloud.
