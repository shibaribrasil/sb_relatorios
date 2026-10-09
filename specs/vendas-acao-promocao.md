# Spec — Vendas com ação ou promoção (seção de Vendas & Margem, camada Mensal)

Criada em 07/10/2026 (pedido do Hugo). Código: `reports/vendas_acao.py` (`secao_acao`), chamada em `reports/vendas_margem.py` logo abaixo do "Perfil dos pedidos".

## Pergunta que responde
Quais vendas usaram alguma ação ou promoção, quanto isso custou em desconto e como ficou a margem, com destaque para as ações mapeadas do SAC e do pós-venda (cupons com prefixo de ação)?

## O que conta como ação
- **Conta:** cupom de qualquer tipo, promoção automática da loja (desconto promocional real) e desconto manual.
- **Não conta:** o brinde (sticker) e o desconto de meio de pagamento (Pix). Pedido só com isso é "Sem ação".
- A promoção real é o `vl_desconto_promocional_rateio` das linhas que **não** são brinde (o campo `promotional_discount` da Nuvemshop mistura promoção e desconto do sticker; o `tb_pedido` já separa os dois).
- Pedido com cupom e promoção entra no grupo do cupom.

## Fontes (toda regra no dbt)
| Fonte | Região | Uso |
|---|---|---|
| `dbt_dw_az.tb_pedido_acao` | us-east4 | 1 linha por pedido válido: ação, grupo, flags, descontos (cupom, promoção real, Pix), faturamento, receita e margem de contribuição. |
| `dbt_dw_stg.stg_acao_cupom` | us-east4 | Mapa prefixo/padrão do cupom → ação e grupo (`fg_sac_marketing`). **Cupom novo de ação entra aqui.** |
| `raw_control.vw_vendas_cupom_acao` | us-east4 | Cupons gerados pela function `nuvemshop-criar-cupom` e se foram usados (conversão). Cupons de teste (`referencia` começando com `teste`) ficam de fora. |

## Mapeamento atual (`stg_acao_cupom`)
| Padrão | Ação | Grupo |
|---|---|---|
| `SEGUNDACHANCE…` | Recontato com cupom (SAC) | SAC e pós-venda |
| `RETORNOPERDIDO…`, `RETORNOMATURACAO…`, `RETORNODORMENTE…` | Recompra — crédito de retorno | SAC e pós-venda |
| `CASHBACKPOSCOMPRA…` | Cashback pós-compra | SAC e pós-venda |
| `EXPLORAR20` | Cartão impresso / Área VIP | SAC e pós-venda |
| `PRIMEIRODATE` | Cupom permanente de entrada (R$ 5, sem prazo) | Cupom permanente |
| `REC`/`RCA` + 1 dígito + 6 caracteres | Recuperação automática de carrinho (Nuvemshop) | Recuperação automática |
| `DRAFT-ORDER-…` | Pedido manual (rascunho do admin) | Pedido manual |
| qualquer outro | Cupom sem ação mapeada | Cupom sem ação mapeada |

Cupom que não casa com nenhum padrão aparece no expansor "Cupons sem ação mapeada" para o Hugo classificar. Os cupons de campanhas antigas de marketing (`SEMANACLIENTE`, `CORDASECUIDADOS`, `VOLTEI12`…) ficam nesse grupo até o Hugo confirmar a que ação pertencem; **não foram classificados por inferência**.

## Blocos da tela
1. **Cards gerais** (meses selecionados, desde ago/2025): pedidos com ação (e % do total), faturamento com ação, desconto concedido (e % da receita bruta desses pedidos), margem de contribuição com ação × sem ação (diferença em p.p.), ticket médio com × sem ação.
2. **Ações mapeadas do SAC e do pós-venda:** pedidos e faturamento com cupom de ação, cupons gerados no mês e quantos foram usados (conversão), margem dessas vendas; tabela por campanha (cupons gerados pela function, pedidos com o prefixo, conversão = pedidos ÷ gerados, faturamento, margem e desconto), na mesma base do card
2b. **Recuperação de carrinho abandonado e de pedido cancelado** (09/10/2026, pedido do Hugo): cards (carrinhos recuperados, pedidos refeitos, com contato do SAC antes, margem) e lista dos pedidos. Regra no dbt (`tb_pedido_recuperacao`): carrinho recuperado = mesmo e-mail faz pedido válido em até 15 dias da criação do carrinho; pedido refeito = mesmo cliente faz outro pedido válido em até 30 dias do cancelamento (as janelas das listas do SAC). Contato do SAC antes = o item de origem tem "Já tratei" e o 1º contato foi feito até a data do pedido (`raw_control.sac_tarefas` e histórico); telefone/WhatsApp inválido não conta. Com contato, o pedido vira ação do SAC e pós-venda ("Carrinho recuperado após contato do SAC" / "Pedido refeito após contato do SAC", destaque âmbar), exceto quando o cupom é de uma ação mapeada (o cupom manda no rótulo); sem contato, só aparece neste bloco. Ligação por e-mail/cliente, não por id de checkout; o SAC começou em 28/09/2026, então antes disso toda recuperação é "sem contato".
3. **Vendas por ação:** pedidos, % dos pedidos, faturamento, ticket, desconto, desconto ÷ receita bruta, margem R$ e %; linhas das ações mapeadas em destaque âmbar; linha de referência "Sem ação".
4. **Pedidos com ação no período:** tabela com data, pedido, cliente, ação, cupom, descontos, faturamento, margem %, pagamento, novo/recorrente; filtro por grupo ("Só SAC e pós-venda" incluso).
5. **Cupons sem ação mapeada** (expansor).

## Regras e limitações
- Faturamento = produtos líquidos + frete pago (mesma base de meta/ticket); margem de contribuição **antes de mídia**, razão soma ÷ soma; reembolso já rateado no pedido.
- **Volume pequeno** nas ações do SAC (jul–out/2026: poucos pedidos): ler como sinal, não como taxa. A conversão conta o cupom gerado no mês que foi usado em compra, mesmo depois.
- `PRIMEIRODATE` é permanente (R$ 5, mínimo R$ 49,99): domina os pedidos com ação e **não mede eficiência de campanha** (viés de seleção: usa quem conhece o código).
- Cupom recriado à mão na Nuvemshop (ex.: `SEGUNDACHANCECUEZ262`) aparece nas vendas por ação, mas **não** nos "cupons gerados" (não passou pela function).
- Preço promocional de cadastro (preço "por" menor que o "de") **não** é desconto no pedido e não entra aqui; só a promoção que a Nuvemshop registra como desconto promocional.
- Histórico desde 01/08/2025 (custo confiável); pedidos manuais (rascunho) têm valor zero.
