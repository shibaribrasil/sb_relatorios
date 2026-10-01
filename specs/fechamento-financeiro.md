# Spec — Fechamento Financeiro (caixa) — v1 `reports/fechamento_financeiro.py` e v2 `reports/fechamento_financeiro_extrato.py` (camada Mensal) — 01/10/2026

Pergunta: **quanto dinheiro entrou e saiu de fato no mês, e quanto sobrou?** É a visão **financeira** (regime de caixa: entra quando recebe, sai quando paga). Complementa, sem substituir, a visão **gerencial** da página Resultado (DRE), que é por competência.

| | Gerencial — Resultado (DRE) | Financeiro — Fechamento |
|---|---|---|
| Regime | Competência | Caixa |
| Mercadoria | CMV (custo do que foi vendido) | Compras pagas no mês |
| Frete | Frete real de cada pedido | Fatura paga no mês |
| Google Ads | Consumo do mês (do Ads) | Lançamento pago no mês (M+1) |
| Embalagem | Estimada por pedido | Suprimentos pagos |
| Investimento | Fora do resultado (memo) | Saída do mês |

## Decisões do Hugo (01/10/2026)
1. Duas visões, gerencial e financeira; a financeira em **duas versões**, cada uma com link próprio: **v1** (entradas estimadas pelos pedidos, `url_path=fechamento-financeiro`) e **v2** (entradas reais do extrato da conta PJ, `url_path=fechamento-financeiro-extrato`), para identificar as divergências.
2. Ponto de partida = vendas **sem descontos, cancelamentos e reembolsos**, já **líquidas da taxa** (o gateway retém a taxa na venda). Frete pago pelo cliente entra; a fatura de frete sai quando é paga.
3. Data de recebimento e meio de pagamento: **pedidos da loja Nuvemshop vêm da Nuvemshop**; pedidos de outras lojas (marketplace, quando existir) vêm do Bling.
4. Cartão Nubank não é entidade, é forma de pagamento: vale o lançamento do Bling com a data de pagamento.
5. Mês negativo por compra grande **não** se ajusta: é o caixa.

## Fontes (tudo calculado no dbt; a página só filtra, soma e apresenta)
| O quê | Tabela |
|---|---|
| Mês a mês (v1, v2, saídas, diferença) | `dbt_dw_az.tb_caixa_mes` |
| Entradas v1, por pedido | `dbt_dw_az.tb_caixa_entrada_pedido` (de `tb_pedido`: `ds_origem_recebimento`, `ds_meio_recebimento`, `dt_recebimento_pedido`) |
| Saídas, por lançamento | `dbt_dw_az.tb_caixa_saida` (contas a pagar do Bling) |
| Entradas v2, por lançamento do banco | `dbt_dw_az.tb_extrato_conta` (extrato importado pelo `sb_data_pipeline`) |
| Ponte com a visão gerencial | `dbt_dw_az.tb_dre_mes` (CMV, frete real, embalagem) + `dbt_dw_us_az.tb_gads_conta_diario` (Ads, região US, junta no pandas) |

## Regras
**Entradas v1 (estimadas):** pedido válido (pago, não cancelado; reembolso total fora) → faturamento (produtos líquidos + frete pago) − taxa, na **data de recebimento** = data do pagamento do cliente (`paid_at`, ou data da transação paga — mesma data em 100% de pix/cartão) + prazo do gateway × meio (`stg_prazo_recebimento`: Nuvem Pago pix 0, cartão 2, boleto 2; Mercado Pago pix 0, cartão 0, boleto 3 — valores do cadastro do Bling, **prazo do cartão a confirmar**). Reembolso parcial sai no mês em que aconteceu. Recebimento com data futura (cartão pago nos 2 últimos dias) não entra até a data chegar.

**Exceção de fechamento de set/2026 (decisão do Hugo, 01/10/2026):** pedidos feitos até 30/09/2026 cujo recebimento pela regra caiu em outubro (3 pedidos: 2490 boleto, 2500 e 2502 cartão; R$ 656,58) contam como recebidos em 30/09. Do corte em diante vale a data de recebimento. No dbt: `var caixa_corte_excecao` (2026-09-30), `fg_excecao_recebimento`, `dt_recebimento_regra` em `tb_caixa_entrada_pedido`; a `tb_pedido` não muda.

**Saídas (iguais em v1 e v2):** contas a pagar do Bling pela **data de pagamento** = data da baixa (extraída do Bling dia a dia, `raw_bling.contas_pagar_pagamento`); sem baixa, o vencimento (`ds_origem_data = vencimento`). Só até hoje. Tudo entra (mercadoria, frete, mídia, investimento, suprimentos). Duplicado (mesmo mês, fornecedor, valor e linha) conta uma vez — mesma regra da DRE.

**Entradas v2 (reais):** repasses que caíram na conta PJ (Nuvem Pago = instituição "FITS IP"; Mercado Pago = conta do CNPJ da loja) − estornos pagos a clientes pela conta. Aporte de sócio **não** é venda (aparece à parte). Transferência para sócio não é saída aqui: as despesas que ela reembolsa já estão no Bling. Só existe nos meses com extrato importado (hoje: set/2026).

**Resultado de caixa** = entrada líquida − saídas. **Diferença v1 − v2** = entrada líquida estimada − real: positivo quando os pedidos indicam mais dinheiro do que caiu na conta.

## Por que v1 e v2 divergem (o que a página explica)
- **Saldo parado no gateway:** o dinheiro só chega à conta PJ quando há saque/repasse do Nuvem Pago (ex.: R$ 8,9 mil em 30/09).
- **Prazo:** cartão pago no fim do mês cai no mês seguinte.
- **Descontos no saldo do gateway:** fatura de frete ou estornos debitados direto no saldo do Nuvem Pago reduzem o repasse sem passar pelo Bling.
- **Recebimentos fora da loja** ("Pagamentos Personalizados", Pix direto) e entradas não classificadas.

## Limitações (nas notas da tela)
- Saídas desde 08/2026 (primeiro mês com contas confiáveis); extrato só desde 09/2026.
- Gasto não lançado no Bling não existe aqui.
- Prazo de recebimento do cartão no Nuvem Pago é o do cadastro do Bling (2 dias), a confirmar.
- A classificação do extrato é por texto do banco; lançamento novo de tipo desconhecido cai em `outras_entradas`/`pagamento_pix` e deve ser revisado.
- Mês corrente parcial.

## Tela
Demonstrativos com cor por tipo de linha (`design.demonstrativo`: barra = tipo, número = sinal). No fim de cada página, bloco **"Regras aplicadas"** (`design.regras_aplicadas`) com o resumo objetivo das regras daquela versão — manter alinhado com esta spec ao mudar qualquer regra.
