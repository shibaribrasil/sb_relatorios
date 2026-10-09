# Spec — Mapa de Interesse (camada semanal)

Status: v1 construída em 28/09/2026 (seção de banners acrescentada em 09/10/2026, ver § Banners e quadrados de categoria) (branch `feat/mapa-de-interesse`; dbt `feat/ga4-produto-interesse`). Decisões do Hugo ("bora seguir") aplicadas como padrão recomendado: score sem margem como âncora do ranking, página só **aponta** desalinhamentos (não sugere trocas), janela 14/30 dias, seções que dependem de histórico ficam para depois.

## Pergunta da página
Cada espaço nobre do site (prateleiras da home) está ocupado pelo produto que mais interessa ao cliente? Conceito de varejo: *space productivity* (venda por metro de gôndola) + matriz BCG adaptada (interesse × conversão).

## Fontes
| Dado | Tabela | Observação |
|---|---|---|
| Prateleira, ordem e posição na home (última captura) | `dbt_dw_stg.stg_nuvemshop_vitrine` (`ds_tipo='home'`) | HTML público, de hora em hora |
| Exposição, foto, estoque, frente | `tb_produto_vitrine` + `tb_produto_gestao` (agregada por produto Nuvemshop) | |
| Visita, carrinho, clique na home/categoria | `dbt_dw_us_az.tb_ga4_produto_dia` (nova) | US; só eventos nativos com `items` |
| Pedidos, unidades, margem de contribuição | `tb_pedido` (válidos, sem brinde) ligado a `tb_produto.cd_produto_nuvemshop` | margem antes de mídia, soma da az |
| Variação → produto | `stg_nuvemshop_variacao_produto` | resolve o `item_id` do GA4 |

## Indicadores
- **Visitas** = sessões com `view_item`; **carrinhos** = sessões com `add_to_cart`; **cliques da home** = sessões com `select_item` na lista Home; soma de sessões por variação (quem viu duas cores conta duas vezes).
- **Pontos** = 1 × visita + 5 × carrinho + 20 × pedido (pesos por raridade no funil; constantes `PESO_*` no código).
- **Score 0–100** = posição percentil dos pontos entre os produtos publicados; 0 pontos = 0 (Sem sinal). Faixas: Sem sinal · Frio (>0) · Morno ≥30 · Quente ≥60 · Estrela ≥85. **Escala relativa ao nosso catálogo** (a distribuição por faixa é parcialmente determinada pelo percentil).
- **Taxa de carrinho suavizada** = (carrinhos + 20 × média da loja) ÷ (visitas + 20).
- **Quadrantes** (só produtos com ≥10 visitas): Estrela = score ≥60 e taxa ≥ média; Vitrine que não fecha = score ≥60 e taxa < média; Joia escondida = score <60 e taxa ≥ média; Cão = resto. Demais = "Poucos dados".
- Cliques e vendas de produto em mais de uma prateleira os **cliques** são divididos igualmente entre elas nas somas por prateleira/posição (o GA4 não informa a prateleira do clique); as unidades vendidas na tabela por prateleira são as do produto inteiro (sem divisão), então produto em duas prateleiras aparece nas duas.

## Seções
1. A home montada (prateleiras em cartões com foto, posição, faixa, score, visitas, cliques da home, vendas, alerta sem estoque).
2. Veredito por prateleira (% dos cliques × % das posições, score médio, frios, quentes, sem estoque).
3. Onde erro e onde acerto: frios na home; acertos (Quente/Estrela na home); quentes fora da home (candidatos a subir).
4. Quadrantes (cards de contagem + tabela filtrável por quadrante: score, visitas, carrinhos, taxa de carrinho e vs média da loja, unidades, margem, margem por visita, estoque).
5. Efeito da posição (cliques médios por posição).
Rodapé: de quando são os dados (frescor) e cobertura.

## Limites (mostrar sempre na tela)
- Eventos com produto no GA4 só existem **desde 29/08/2026**; janela máxima ≈ 30 dias. Cliques da home: ~380 em 30 dias → por produto é amostra pequena; leia faixas, não posições exatas do score.
- **Impressão da home não serve de CTR:** `view_item_list` da home só dispara para ~18 itens distintos (de ~57 posições) e o `item_id` vem como variação. Por isso não há CTR por prateleira.
- `item_id` do GA4 é ID de **variação** na maioria dos eventos e de **produto** no `select_item` da home; a resolução é feita na página (100% dos eventos casaram em 28/09).
- Correlação não é causa: produto forte tende a ser posto na home. Interesse fora da home pode vir de anúncio/busca. Cruzar com Tráfego & Conteúdo antes de trocar.
- **Exceção à regra "score só na az":** dbt (us-east4) e GA4 (US) não se juntam em SQL. O score é ranking de apresentação calculado no pandas; nenhum número financeiro é recalculado.

## Pendente (depende de histórico da vitrine, coletado desde 28/09/2026)
- Efeito antes/depois de entrar na home (único jeito de separar causa de correlação) e fadiga de vitrine (dias na home × conversão): revisitar com 4–6 semanas de capturas.
- Margem por visita como métrica-âncora: a coluna `margem_por_visita` já é calculada, mas o score não a usa. Decidir com o Hugo depois de ver a página em uso.
- Frente 2 (mapa de calor do site): Microsoft Clarity (heatmap de clique/scroll grátis) — requer instalar o script no tema da loja; GA4 não tem clique por coordenada.


## Banners e quadrados de categoria (seção 6 e home montada, 09/10/2026)

Pergunta: cada arte da home (carrossel e quadrados de categoria) ocupa a posição certa e leva o cliente adiante? Mesmo conceito do mapa de produtos (*space productivity*, interesse × conversão), com a arte no lugar do produto.

### Fontes
| Dado | Tabela | Observação |
|---|---|---|
| Ordem de todos os blocos, banners, link e arte (última captura) | `dbt_dw_stg.stg_nuvemshop_vitrine_bloco` (fonte `raw_nuvemshop.storefront_home_blocos`) | HTML público, de hora em hora, mesma captura da vitrine de produtos. `nr_ordem_bloco` conta carrossel, quadrados e prateleiras; `cd_arte` = arquivo da imagem sem sufixo de tamanho |
| Data de entrada de cada arte | mesma stg, 1ª captura de cada `cd_arte` | |
| Cliques e funil por banner | `dbt_dw_us_az.tb_ga4_promocao_dia` | evento `select_promotion` da tag GTM v32 (publicada em 09/10/2026); funil na MESMA sessão, depois do clique; `ar_transacoes` = `transaction_id` = `cd_pedido_loja` |
| Sessões que viram a home | `dbt_dw_us_az.tb_ga4_home_sessao_dia` | denominador do CTR |
| Margem de contribuição dos pedidos pós-clique | `dbt_dw_az.tb_pedido` (válidos, sem brinde), por `cd_pedido_loja` | antes de mídia |

### Indicadores (mesmos parâmetros do mapa de produtos)
- **Cliques** = sessões que clicaram na arte (análogo de "visitas"); **carrinhos** = dessas, as que adicionaram ao carrinho depois; **pedidos** = pedidos feitos na mesma sessão depois do clique.
- **Pontos** = 1 × clique + 5 × carrinho + 20 × pedido (mesmos `PESO_*`). **Score 0–100** = posição percentil dos pontos entre os banners da home (no máximo 10 posições); 0 pontos = Sem sinal. Faixas e cores iguais às dos produtos.
- **CTR** = sessões que clicaram ÷ sessões que viram a home. Vira possível aqui (o mapa de produtos não tem CTR porque a impressão da home só cobre ~18 itens).
- **Taxa de carrinho suavizada** e **quadrantes** (≥ 10 cliques): mesma fórmula e mesmos nomes (Estrela, Vitrine que não fecha, Joia escondida, Cão).
- **Janela por arte:** começa no maior entre o início da janela, o 1º dia COMPLETO da tag (10/10/2026) e a entrada da arte (1ª captura); vale para o numerador e o denominador do CTR. Métricas são **por arte**, não por slot: se a ordem do carrossel girar, a arte leva o histórico dela e a tabela de efeito da posição mostra cliques por arte × slot. Cliques de outra arte que já ocupou o slot aparecem em "cliques de outras artes".
- **Receita GA4** = o que o GA4 enxergou (piso); **margem** = soma da `tb_pedido` dos mesmos `transaction_id`.
- **Home montada:** blocos na ordem do site (carrossel, quadrados, prateleiras), cartões de banner de 176 px (o de produto tem 138 px), arte mobile no carrossel.

### Limites (mostrar sempre na tela)
- Só existem dados desde 10/10/2026 (1º dia completo da tag). Antes não há clique por banner.
- Volume pequeno: ~40 sessões por dia na home e dezenas de cliques de banner por mês. Poucos banners passam de 10 cliques nas primeiras semanas (ficam em "Poucos dados"). Leia faixas, não posições exatas do score, e só com 2 a 4 semanas.
- Sessão que clica em dois banners conta nos dois; o pedido não é atribuído a um só banner. Atribuição é piso (consentimento, bloqueadores).
- Os quadrados ficam abaixo da dobra: o CTR deles subestima quem os viu.
- Viés de posição no carrossel (o slide 1 aparece primeiro): o teste limpo é girar a ordem no meio da janela.
- Eventos do modo Visualizar/Tag Assistant (`debug_mode`) são excluídos no dbt.
- **Exceção à regra "score só na az"** (igual aos produtos): GA4 (US) e az do Bling (us-east4) não se juntam em SQL; o score e a taxa suavizada são calculados no pandas, como apresentação.
- Se a captura dos banners não estiver implantada (extrator novo), a seção 1 mostra só as prateleiras e a seção 6 não aparece, com aviso.
