# Spec — Mapa de Interesse (camada semanal)

Status: v1 construída em 28/09/2026 (branch `feat/mapa-de-interesse`; dbt `feat/ga4-produto-interesse`). Decisões do Hugo ("bora seguir") aplicadas como padrão recomendado: score sem margem como âncora do ranking, página só **aponta** desalinhamentos (não sugere trocas), janela 14/30 dias, seções que dependem de histórico ficam para depois.

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
- Cliques e vendas de produto em mais de uma prateleira são **divididos igualmente** entre elas nas somas por prateleira/posição (o GA4 não informa a prateleira do clique).

## Seções
1. A home montada (prateleiras em cartões com foto, posição, faixa, score, visitas, cliques da home, vendas, alerta sem estoque).
2. Veredito por prateleira (% dos cliques × % das posições, score médio, frios, quentes, sem estoque).
3. Onde erro e onde acerto: frios na home; acertos (Quente/Estrela na home); quentes fora da home (candidatos a subir).
4. Quadrantes (dispersão visitas × taxa de carrinho; bolha = margem; cor = exposição).
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
