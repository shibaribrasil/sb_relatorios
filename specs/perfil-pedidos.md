# Spec — Perfil dos pedidos (`reports/perfil_pedidos.py`)

Bloco único, reutilizado nas três camadas de vendas: **Pulso do Dia** (últimos 7 dias fechados vs 7 anteriores), **Vendas da Semana** (semana vs anterior) e **Vendas & Margem** (mês vs anterior, só com 1 mês selecionado). Sem regra de negócio nova: agrega colunas que a `tb_pedido` já entrega.

## Unidade
1 linha por pedido válido (`pedidos_de_linhas` converte as linhas da `tb_pedido`). Brinde não conta como item nem como frente.

## Indicadores
| Indicador | Definição |
|---|---|
| Ticket médio | faturamento (produtos líq. + frete pago) ÷ pedidos — igual ao card de cada página |
| Ticket de produtos | receita líq. de produtos ÷ pedidos (sem frete) |
| Valor médio por item | receita líq. de produtos ÷ itens vendidos (sem brindes) |
| Itens por pedido | itens ÷ pedidos com produto; mostra também mediana e % de pedidos com 1 item |
| Só Shibari / Só Curadoria / Misto | classificação do **pedido** pelas frentes dos itens (`ds_frente`); % de pedidos + ticket de cada grupo |
| Clientes recorrentes | % dos pedidos com `fg_cliente_recorrente` |
| Frete grátis / frete médio pago | pedidos com frete pago = 0; média de `vl_frete_pago_rateio` por pedido |
| Pedidos com desconto / desconto médio | pedidos com desconto ≠ 0; descontos ÷ receita bruta de produtos |
| Distribuições | pedidos por qtd. de itens (1, 2, 3, 4+) e por faixa de ticket (até 100, 100–150, 150–200, 200–300, 300+) |
| Tabela por tipo de pedido | pedidos, %, ticket, itens/pedido e % da receita líq. de cada grupo |

## Decisões
- Pedido só-brinde (sem item vendido) fica fora da classificação de mix e das médias de itens.
- Diferença para "Shibari × Curadoria" (mensal): lá a receita é dividida **por linha** (pedido misto soma nas duas frentes); aqui o pedido cai em **um grupo só**.
- Na camada diária o perfil usa 7 dias fechados (um dia isolado tem ~1 pedido, sem significância).
- Deltas seguem o padrão das páginas (`_delta`: % relativo para valores, p.p. para percentuais).

## Dados de validação (set/2026, 46 pedidos)
Ticket R$ 227,70 · 2,5 itens/pedido · valor por item R$ 82 · 52% só Shibari, 11% só Curadoria, 37% mistos (misto: ticket R$ 301, 50% da receita).
