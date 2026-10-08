# Spec — Desempenho do Produto (menu Catálogo)

Status: v1 construída em 08/10/2026 (branches `feat/produto-desempenho` no `sb_relatorios` e no `sb_dw_dbt`). Pedido do Hugo (08/10/2026): tela gráfica de análise completa de um produto; filtros em cascata Categoria → Subcategoria → Produto; sem meta de margem nesta versão; análise de estoque/ruptura por último (é a menos importante); só interessam as trocas de custo **seguidas** de troca de preço.

## Pergunta da página
Como este produto está indo (vendas, preço, margem), com quem ele é comprado, como o cliente reagiu quando o custo subiu e o preço foi ajustado, e quanto ele fica sem estoque (e quanto isso pode ter custado)?

## Unidade de análise
- **Produto = família** (`cd_produto_bling_familia` da `tb_produto_gestao`: o pai do Bling, ou o próprio SKU quando simples) — como o produto aparece na loja. As **variações** são SKUs da família e aparecem em tabela própria.
- Só entra SKU vendável hoje (a `tb_produto_gestao`): produto descontinuado e já apagado do Bling não aparece no seletor.
- Pedido válido, **sem brinde**, sempre. Margem = margem de contribuição da `tb_pedido`, **antes de mídia**.

## Fontes (todas na `az`, us-east4, exceto GA4)
| Seção | Tabela | Observação |
|---|---|---|
| Catálogo, filtros, foto, estoque, vitrine, cadastro | `tb_produto_gestao` | foto = link do Bling (assinado, vence): vale o de maior `Expires` entre os SKUs; se quebrar, a imagem some (mesmo padrão do Mapa de Interesse) |
| Preço, custo e margem ATUAIS | `tb_produto_margem_atual` (nova) | parâmetros da tabela de precificação; ver regra abaixo |
| Vendas por dia | `tb_produto_venda_dia` (nova) | fecha com a `tb_pedido` (teste `tb_produto_venda_dia__reconcilia_pedido`) |
| Perfil de compra | `tb_produto_cesta` (nova) + `tb_pedido` | cesta no histórico inteiro (amostra pequena) |
| Origem das vendas, ação nos pedidos | `tb_atribuicao_pedido`, `tb_pedido_acao` | por `cd_pedido` / `cd_codigo_interno` |
| Interesse (visitas/carrinhos) | `dbt_dw_us_az.tb_ga4_produto_dia` | região US: junta no pandas; só desde 29/08/2026 |
| Histórico de preço e vendas por faixa | `tb_produto_historico_preco` (nova) | desde 17/07/2025 |
| Trocas de custo seguidas de troca de preço | `tb_produto_troca_custo_preco` (nova) | ver regra abaixo |
| Estoque, rupturas, venda perdida | `tb_produto_estoque_resumo`, `tb_produto_ruptura_impacto` (novas), `tb_estoque_posicao_dia` | fotos diárias só desde 02/07/2026 |

## Indicadores
- **Vendas**: unidades e receita líquida de produtos. *Mês atual* = dia 1 até hoje (dia em andamento incluído, rotulado, comparado com o mesmo intervalo do mês anterior); *30 dias* e *90 dias* = últimos 30/90 dias **fechados** (até ontem). **Mês de pico** = mês com mais unidades no histórico (desde ago/2025, que é o histórico confiável de custo e taxa) e a quantidade vendida nele.
- **Preço atual** = preço "por" da Nuvemshop; senão "de"; senão o cadastro do Bling (mesma regra da planilha de precificação). **Custo atual** = custo da última compra/produção; senão o cadastrado. Em família com variações, mostra a média ponderada pelas unidades dos últimos 90 dias (se não vendeu, média simples) e a faixa mín–máx.
- **Margem de contribuição atual (esperada)** = `tb_produto_margem_atual`: preço × (1 − desconto padrão) − custo − taxa Pix × preço − materiais de envio (R$ 2,50). Dois cenários: *tabela* (sem desconto de Pix) e *Pix* (com o desconto fixo de 3%). Sem custo cadastrado → "sem custo", nunca 100%. **Não é a margem realizada**: a realizada (margem de contribuição ÷ receita líquida, 90 dias, da `tb_pedido`) já inclui cupom, frete e mix de pagamento e é mostrada ao lado. Sem semáforo (sem meta de margem nesta versão).
- **Perfil de compra**: % dos pedidos com o produto que têm **só ele** (uma única família no pedido; linha de catálogo antigo sem dimensão conta como outra família); companheiros = famílias nos mesmos pedidos, com % dos pedidos do produto e **lift** (> 1 compram juntos mais que o acaso). Lift só é mostrado com ≥ 3 pedidos juntos.
- **Ação hoje**: tipo de oferta do Bling (Cashing) e promoção ativa na Nuvemshop (preço "por" menor que "de"). **Ação nos pedidos**: distribuição do grupo de ação (cupom permanente, promoção automática, sem ação…) nos pedidos com o produto.
- **Retorno do cliente**: % dos pedidos com o produto que foram a 1ª compra do cliente (porta de entrada) e % dos clientes que voltaram a comprar em até 90 dias (só pedidos com mais de 90 dias), comparado com a média da loja.
- **Troca de custo seguida de troca de preço**: para cada troca de preço (SKU × dia) olha os 60 dias anteriores (e depois da troca de preço anterior); se o custo teve variação **líquida** ≥ 1% (a tela filtra por um piso ajustável, padrão 5%), a troca entra. Mostra custo anterior → novo, preço anterior → novo, **repasse** (variação % do preço ÷ variação % do custo), margem de lista antes/depois (preço − custo, sem taxa nem embalagem) e **unidades por dia** no período do preço anterior e no do preço novo. Troca de custo sem troca de preço depois fica de fora (pedido do Hugo).
- **Vendas por faixa de preço**: cada período em que o preço de venda ficou igual: dias, unidades, unidades por dia, preço médio praticado, dias sem estoque conhecidos.
- **Estoque**: % do tempo sem estoque, número e duração média das rupturas, rupturas em curso; **venda perdida estimada** = unidades/dia dos 90 dias antes da ruptura (sem os dias já sem estoque) × dias da ruptura, **só com base suficiente** (≥ 3 unidades e ≥ 30 dias de base). Sem base → "sem base para estimar".

## Limites (mostrar na tela)
- ~1 pedido/dia: período curto não é tendência; as comparações de preço são **indicativas**, não elasticidade (a base antes/depois tem poucas unidades e outras coisas mudam junto: cupom, vitrine, estoque, sazonalidade).
- Histórico de preço/custo desde 17/07/2025; o custo oscila no cadastro (sobe e volta) — por isso a variação líquida e o piso.
- Estoque do Bling desde 02/07/2026: rupturas que começam na 1ª foto podem ter começado antes; o Bling zerado não impede a Nuvemshop de vender se ela não controla estoque (há venda durante ruptura).
- Interesse (GA4) só desde 29/08/2026; visitas somam sessões por variação.
- Margem esperada usa R$ 2,50 de embalagem **por unidade** (a regra do negócio é por pedido): superestima o custo de embalagem em pedido com vários itens.
- Pedidos de antes de set/2024 apontam para um catálogo antigo do Bling e não entram no perfil por família (ver armadilha 18b).

## Seções (ordem da página)
1. Cabeçalho do produto (foto, nome, categoria › subcategoria, ciclo, papel, frente, exposição, ação hoje, estoque).
2. Números (mês atual, 30d, 90d, pico, preço, custo, margem esperada e realizada).
3. Evolução (unidades e receita por dia/semana/mês, com as trocas de preço marcadas).
4. Variações (tabela por SKU).
5. Perfil de compra (sozinho, companheiros, ação nos pedidos, retorno do cliente).
6. Origem e interesse (canal das vendas; visitas → carrinhos → pedidos; vitrine e cadastro).
7. Preço e custo (trocas de custo seguidas de troca de preço; vendas por faixa de preço).
8. Estoque (tempo em estoque e sem estoque; rupturas; venda perdida estimada) — por último.
