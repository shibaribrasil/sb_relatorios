# Spec — Vendas & Margem de Contribuição

Define as regras de negócio e a fórmula de cada indicador de venda e margem dos relatórios novos. **Leia antes de criar ou alterar qualquer página que mostre faturamento, margem ou custo.** Se a mudança alterar uma regra, atualize este arquivo no mesmo commit. Substitui `specs/vendas.md` (relatório antigo, baseado em `rpt_*`, que será desativado).

> Status: **v1 — 19/set/2026.** Definições validadas contra o BigQuery; página "Vendas & Margem" construída e testada localmente contra uma cópia de validação da `tb_pedido` (ainda depende do deploy da Fase 1 do dbt em produção). Aprovadas pelo Hugo em 19/set: pedido `EM ABERTO` conta como venda (aguarda postagem); histórico de Ads começa em 15/06/2026 (data da integração). As demais decisões (D1, D3–D6) seguem as recomendações do levantamento da Fase 1.

## Fonte de dados

**`dbt_dw_az.tb_pedido`** (projeto `sb_dw_dbt`, `models/3.az/Pedidos/`), grão **1 linha por item de pedido** (`cd_codigo_interno` + `cd_produto_bling`). Nenhuma tabela `rpt_*` é usada. Toda regra de negócio mora nesta tabela, com testes; o app só lê, filtra e soma.

Atualização: a cada hora, das 7h às 23h (BRT). A coluna `ts_load` traz o carimbo da carga (para "atualizado em").

## Regras de negócio

### R1 — O que é uma venda (`fg_pedido_valido`)
Filtrar **sempre** por `fg_pedido_valido = TRUE`. Nenhuma página filtra status por conta própria.

| Situação | Conta? |
|---|---|
| `ATENDIDO` (já postado), pagamento `paid` | Sim |
| `EM ABERTO` (pago, aguardando postagem), pagamento `paid` | **Sim** |
| `ATENDIDO` com pagamento `partially_refunded` | Sim (com o reembolso descontado, R3) |
| `CANCELADO` (qualquer pagamento: pendente, anulado, reembolsado) | Não |
| Pagamento `pending`, `voided`, `refunded` | Não |

`EM ABERTO` = "pago, esperando ser postado"; é receita do dia, e "pedidos a postar" = pedidos válidos com status `EM ABERTO`.

### R2 — Data da venda
`dt_pedido` (data do pedido no Bling). Confirmado igual à data da Nuvemshop em horário de Brasília. Fuso das páginas: BRT.

### R3 — Reembolso
Reconhecido no mês do **pedido** (a API da Nuvemshop não tem data do reembolso). Rateado pela linha em `vl_reembolso_rateio`. A taxa do gateway **não** é devolvida no reembolso (mantida como custo).

### R4 — Embalagem e imposto
Parâmetros com vigência em `stg_parametro_operacional` (não em seed): embalagem **R$ 2,50 por pedido**; imposto **0%** (sem CNPJ). Ao mudar, adicionar linha nova com a data de início — nunca editar a antiga.

### R5 — Brindes
Sem tratamento especial: brinde com preço e custo entra como produto comum.

### R6 — Mídia
Não é por pedido. A margem por linha é **antes de mídia**. "Margem após mídia" só existe em agregado (dia/mês) subtraindo o gasto de Google Ads (`tb_gads_conta_diario`, dataset `dbt_dw_us_az`, **histórico desde 15/06/2026** — data da integração; períodos anteriores não têm mídia e não devem mostrar margem após mídia nem CAC).

## Cascata da margem de contribuição

Todos os componentes `*_rateio` são **aditivos por linha**.

| # | Componente | Coluna |
|---|---|---|
| 1 | Receita bruta de produtos | `vl_total_item` |
| 2 | (−) Desconto | `vl_desconto_rateio` (origens: `vl_desconto_cupom_rateio`, `vl_desconto_pagamento_rateio`, `vl_desconto_promocional_rateio`) |
| 3 | **= Receita líquida de produtos** | `vl_receita_liquida_produto` |
| 4 | (+) Frete pago pelo cliente | `vl_frete_pago_rateio` |
| 5 | (−) Frete real (etiqueta Nuvem Envio) | `vl_frete_real_rateio` |
| — | = Resultado de frete (4 − 5) | `vl_resultado_frete` (negativo = frete subsidiado) |
| 6 | (−) Custo do produto | `vl_custo_linha` |
| 7 | (−) Taxa de pagamento (real) | `vl_taxa_pedido_rateio` |
| 8 | (−) Reembolso | `vl_reembolso_rateio` |
| 9 | (−) Embalagem | `vl_embalagem_rateio` |
| 10 | (−) Imposto | `vl_imposto_rateio` |
| 11 | **= Margem de contribuição (antes de mídia)** | `vl_margem_contribuicao` |

`vl_margem_contribuicao = vl_receita_liquida_produto + vl_resultado_frete − vl_custo_linha − vl_taxa_pedido_rateio − vl_reembolso_rateio − vl_embalagem_rateio − vl_imposto_rateio`

**Faturamento** (o que o cliente pagou) = `vl_liquido_item` = `vl_receita_liquida_produto + vl_frete_pago_rateio`.

### Margem em %
`Σ vl_margem_contribuicao ÷ Σ vl_receita_liquida_produto` (denominador = receita líquida de produtos, sem frete). **Nunca** média de percentuais de linha ou de dia; **nunca** calcular % com poucos pedidos como KPI de decisão (ver "Volume" abaixo).

## Regras de agregação (o que pode e o que não pode somar)

- **Somar:** somente colunas `*_rateio`, `vl_total_item`, `vl_liquido_item`, `vl_receita_liquida_produto`, `vl_resultado_frete`, `vl_custo_linha`, `vl_margem_contribuicao`, `qt_item`.
- **Não somar** (repetem em cada linha do pedido): `vl_total_pedido`, `vl_taxa_pedido`, `vl_frete_pago`, `vl_frete_real`, `vl_reembolso`, `vl_desconto_cupom`, `vl_desconto_pagamento`, `vl_desconto_promocional`, `vl_total_pedido_nuvemshop`. Para contar pedidos: `COUNT(DISTINCT cd_codigo_interno)`.
- **Colunas legadas:** `vl_custo_pedido` (= `vl_custo_linha`) e `vl_frete_rateio` (≈ `vl_frete_pago_rateio`) só existem até os modelos derivados antigos serem reconstruídos; **não usar em página nova**.

## Cobertura e qualidade

- **`fg_margem_incompleta`** = custo ausente, taxa nula ou pedido sem dado Nuvemshop. Linhas assim têm margem superestimada. Toda página mostra o % de linhas incompletas do período ("cobertura") e não esconde o número.
- **Histórico confiável a partir de ago/2025** (taxa real e frete real da Nuvemshop): de lá para cá 0–5 linhas incompletas por mês. Antes disso, muitas linhas são incompletas — não mostrar margem de contribuição de períodos anteriores a 08/2025 sem aviso.
- Testes que garantem estas regras (`sb_dw_dbt/tests/`): `tb_pedido__grao_unico`, `tb_pedido__margem_identidade`, `tb_pedido__rateio_fecha_pedido`, `tb_pedido__pedido_valido`, `tb_pedido__margem_plausivel`, `tb_pedido__custo_plausivel`, `tb_pedido__valores_consistentes`; avisos (`warn`): `identidade_desconto` e `reconciliacao_nuvemshop` (2 e 5 pedidos conhecidos).

## Volume: por que taxa não vale por dia (regra de apresentação)
Com ~24–40 pedidos/mês (≈1 por dia), uma margem % de um único dia ou semana não significa nada. Diário mostra valores e contagens (R$, pedidos, a postar); semanal usa janela móvel de 4 semanas para qualquer razão; razões e North Star ficam no mensal.

## Referência de valores (validação, pedidos válidos, antes de mídia)

| Mês | Pedidos | Receita líq. produtos | Contribuição | % |
|---|---|---|---|---|
| Mai/26 | 35 | 5.961 | 3.878 | 65,1% |
| Jun/26 | 34 | 5.088 | 3.264 | 64,1% |
| Jul/26 | 39 | 6.341 | 4.051 | 63,9% |
| Ago/26 | 23 | 4.652 | 2.993 | 64,4% |
| Set/26 (até 19/09) | 34 | 6.835 | 4.390 | 64,2% |

Jan–Abr/26 ficam entre 57,9% e 62,6% (frete subsidiado em pedidos ≥ R$ 400 e reembolsos).

## Limitações conhecidas
- Reembolso sem data própria (usa o mês do pedido).
- Taxa de pagamento em pedidos sem transação Nuvemshop usa a estimativa tabelada do Bling (só histórico anterior a meados de 2025).
- Embalagem é estimativa fixa por pedido, não medida.
- Imposto é zero enquanto a loja não tiver CNPJ; muda quando formalizar.
- Um pedido pode ter mais de uma origem de desconto (cupom + PIX); `ds_origem_desconto` traz só a principal — usar as colunas `*_rateio` para a decomposição.

## Página "Vendas & Margem" (`reports/vendas_margem.py`, camada Mensal)

- **Filtro:** meses (default: mês corrente). Histórico a partir de ago/2025.
- **Cards (7):** Faturamento, Receita líq. de produtos, Margem de contribuição (R$), Margem %, Pedidos, Ticket médio (faturamento ÷ pedidos), Resultado de frete.
- **Semáforo da Margem %:** verde ≥ 50% · âmbar ≥ 40% (mínimo institucional do Manual de Precificação) · vermelho < 40%. Constantes `MARGEM_OK` / `MARGEM_MIN`.
- **Comparação:** só com **um** mês selecionado. Mês corrente (parcial) compara com o **mesmo intervalo de dias** do mês anterior; mês fechado, com o mês anterior inteiro. Variações sempre com sinal (+/−) além da cor; margem em pontos percentuais (p.p.).
- **Cobertura:** aviso quando há linhas com `fg_margem_incompleta` no período.
- **Gráficos:** cascata (soma do período; passos zerados omitidos), evolução mensal desde ago/2025 (não segue o filtro), margem por categoria (top 8 + "Outras").
- **Tabelas:** por produto e por pedido. **Sem nome de cliente** (decisão de privacidade: a URL do app é fixa; a tabela do relatório antigo expunha nome e pedido).
- **Cache:** 15 min (`ttl=900`); os dados são atualizados de hora em hora.
- **Desenvolvimento:** variáveis de ambiente `SB_DATASET_PEDIDO` / `SB_TABELA_PEDIDO` apontam para uma cópia de validação; em produção lê `dbt_dw_az.tb_pedido`.
