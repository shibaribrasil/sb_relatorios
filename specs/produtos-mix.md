# Spec — Produtos & Mix (`reports/produtos_mix.py`, camada Mensal)

Pergunta: **o que sustenta a margem, o que vende muito ganhando pouco, e como o mix de categorias está mudando?**

## Fontes e escopo
Mesma base de `vendas_margem` (`carregar_dados()` → `dbt_dw_az.tb_pedido`, pedidos válidos desde ago/2025). **Brindes ficam fora** (o custo deles fica no CMV da página Vendas & Margem). Produto = `nm_produto`; categoria = cadastro do Bling.

## Regras
- **Período:** mês atual, últimos 3 / 6 / 12 meses (meses inteiros até hoje). Comparação com o período anterior de **mesmo tamanho em dias**; só se o período anterior existir na base.
- **Margem de contribuição do produto:** Σ `vl_margem_contribuicao` das linhas (antes de mídia; frete, taxa, reembolso e embalagem já rateados no dbt). Margem % = Σmargem ÷ Σreceita líquida de produtos (nunca média de percentuais).
- **Curva ABC** pela margem de contribuição (R$), decrescente: **A** = produtos que somam até 80% acumulado (o produto que cruza o limite entra na classe em que começa); **B** até 95%; **C** o restante. Limites 80/95 são constantes da página (`CLASSE_A`, `CLASSE_B`); se virarem oficiais, mover para parâmetro do dbt.
- **Dispersão:** volume (unidades, eixo log) × margem %; linha no mínimo institucional (40%); tamanho = receita; cor = classe.
- **Mix por categoria:** participação na receita líquida por mês do período; tabela com % da receita e margem % por categoria.
- **Δ receita:** (receita − receita anterior) ÷ receita anterior; vazio quando o produto não vendeu no período anterior.
- **Limite conhecido:** com ~40 pedidos/mês, produtos individuais oscilam muito; usar 3+ meses. Cruzamento com estoque (classe A sem estoque) depende da página de Estoque refeita. Os modelos `tb_venda_produto_*` (com regra própria, cancelados fora só por status) seguem alimentando o estoque e serão consolidados junto com o Estoque; esta página **não** os usa.
