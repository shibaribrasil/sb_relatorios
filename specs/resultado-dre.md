# Spec — Resultado (DRE mensal) (`reports/resultado_dre.py`, camada Mensal) — v1, 29/09/2026

Pergunta: **depois de pagar tudo o que a operação custa, sobrou dinheiro no mês? Quanto precisamos vender para empatar?** (KPI 5.6 "Resultado líquido (P&L)"; R3 do plano de KPIs.)

Substitui, para leitura gerencial, a `tb_resultado_final` (visão híbrida de caixa: misturava datas, não usava o custo do produto vendido, ignorava o cartão e tinha meses futuros). A tabela antiga continua no BigQuery, sem página, até ser desligada.

## Decisões do Hugo (29/09/2026)
1. **Regime: competência** (DRE). Visão de caixa (recebido × pago) fica para uma segunda etapa.
2. **Meta/Instagram:** o gasto entra pelo lançamento no Bling (contas a pagar) quando existir; não há outra fonte.
3. **Cartão Nubank fica fora** (o Bling é a fonte única de despesa). Sem lista de classificação do cartão.
4. **Sem limpeza manual no Bling:** o modelo trata os lançamentos duplicados por regra (abaixo).
5. **Histórico a partir de agosto/2026** (`var dre_inicio`), primeiro mês com despesa confiável.

## Fontes
| O quê | Fonte |
|---|---|
| Receita, custo, frete, taxa, embalagem, imposto, reembolso | `dbt_dw_az.tb_pedido` (pedidos válidos) — as mesmas colunas da margem de contribuição; nada é recalculado |
| Despesas | `dbt_dw_az.tb_despesa_dre` (novo; lançamentos de `tb_contas_pagar` classificados por competência) |
| Mês a mês | `dbt_dw_az.tb_dre_mes` (novo; 1 linha por mês, de ago/2026 até o mês corrente) |
| Google Ads | `dbt_dw_us_az.tb_gads_conta_diario` — **região US**: consulta separada, subtraído no pandas (mesma exceção já usada em "margem após mídia") |

## Linhas da DRE (na ordem da tela)
1. **Faturamento** (produtos líquidos + frete pago), por mês do pedido.
2. **Margem de contribuição antes de mídia** = receita líquida de produtos + resultado de frete − CMV − taxa de pagamento − embalagem (estimada R$ 2,50/pedido) − imposto − reembolsos. **Reembolso no mês em que aconteceu** (sem data, cai no mês do pedido). Mesma regra de Vendas & Margem.
3. **Mídia:** Google Ads (do Ads, por mês de consumo) e Meta/outras (contas a pagar, subcategoria "Propaganda e publicidade").
4. **Despesas operacionais** (contas a pagar, por competência): pessoal (pró-labore), ferramentas e tecnologia, adicionais, financeiras, sem categoria.
5. **Resultado operacional** = margem de contribuição − mídia − despesas operacionais. **Antes de imposto** (imposto 0% até haver CNPJ).
6. **Abaixo da linha (memo):** investimentos/melhorias (estante, caixas: capex, não entram no resultado), suprimentos lançados (embalagem real × estimada).

## Regras de classificação (no dbt, `tb_despesa_dre`)
| Subcategoria do Bling | Linha | Entra no resultado? |
|---|---|---|
| Compras de Mercadorias, Compra de Matérias Primas; sem categoria com "pedido de compra" | mercadoria | **Não** — o CMV já está na margem (compra de estoque não é despesa) |
| Fretes e seguros | frete | **Não** — o frete real já está na margem |
| Propaganda e publicidade com "google" | mídia Google | **Não** — o custo vem do Ads (o lançamento no Bling vem 1 mês depois e duplicaria) |
| Propaganda e publicidade (Meta, outras) | mídia Meta / outras | Sim |
| Pró-labore (inclui "Dívida legada") | pessoal | Sim |
| Ferramentas e Tecnologia | ferramentas | Sim |
| Outros (Não Descritos), demais | adicionais | Sim |
| Juros pagos, Taxas pagas | financeiras | Sim |
| Sem categoria (não é compra) | sem categoria | Sim, sinalizado |
| Investimentos/Melhorias | investimento | **Não** (memo) |
| Caixas e Embalagens, Materiais de Envio | suprimentos | **Não** (memo) — a embalagem já entra estimada na margem; soma dupla |

- **Duplicados (regra):** o Bling tem o mesmo gasto lançado duas vezes por mês (um com o nome antigo, um com o novo; ex.: "Bling" e "Plataforma ERP (Bling)", R$ 200). Mesmo mês de competência + mesmo fornecedor + mesmo valor = **1 só conta** (`row_number`, preferindo o Pago). Referência 29/09: 24 grupos, ~R$ 3,1 mil de excedente (jul–out). Os excluídos ficam visíveis em `tb_despesa_dre` (`fg_duplicado_provavel`) e como coluna do mês (`vl_excl_duplicado`).
- **Contas apagadas no Bling (01/10/2026):** a origem real dos "duplicados" eram lançamentos já **excluídos no Bling** que continuavam no BigQuery (o extrator nunca apagava). Desde 01/10 o `tb_contas_pagar` mantém só as contas da última listagem completa (169 lançamentos, R$ 20,3 mil, saíram). Com isso a regra de duplicados não encontra mais nada (ago e set: R$ 0) e fica como rede de segurança.
- **Situação não importa:** competência conta Pago, Atrasado e Em Aberto (despesa incorrida). Meses futuros ficam de fora; o **mês corrente** entra com o que já foi lançado e é rotulado "em andamento" (receita parcial × despesa fixa cheia).
- **Reconciliação:** para cada mês, total de contas a pagar = considerado + excluído por motivo (mercadoria, frete, Google Ads, suprimentos, investimento, duplicado). Teste singular no dbt.

## Limitações (aparecem na nota da tela)
- Só existe despesa lançada por competência de **agosto/2026** em diante; antes disso a tela não mostra resultado.
- Despesa **não lançada no Bling não existe aqui** (cartão Nubank, gasto ainda sem lançamento): o resultado é um **teto**. Ferramentas pagas no cartão que não foram lançadas no Bling ficam de fora.
- Competência do Bling para mídia é o mês do pagamento (ex.: Google Ads "ref julho" pago em agosto); por isso Google vem do Ads.
- Embalagem é estimada; imposto 0%; margem de meses anteriores a ago/2025 é aproximada (fora da janela).
- Com ~40 pedidos/mês, o resultado de um mês oscila com o mix e com o calendário de pagamentos: ler tendência de vários meses.

## Ponto de equilíbrio
Faturamento necessário no mês = (despesas operacionais + mídia) ÷ (margem de contribuição ÷ faturamento). Calculado sobre a margem % do período; mostra também o faturamento realizado contra esse número.
