# Spec — Gestão de Produtos (`reports/gestao_produtos.py`, camada Diária)

Página de **trabalho** para movimentar o catálogo: cada seção termina numa ação direta no Bling ou na Nuvemshop (publicar ou ocultar, repor, trocar a oferta, dar visibilidade, corrigir o cadastro). As seções seguem a ordem em que a ação é mais urgente: primeiro o que o cliente está vendo errado hoje, depois as decisões de ciclo de vida, por último a higiene de cadastro.

Criada em 28/09/2026 (pedido do Hugo). Unidade de leitura: **produto como aparece na loja** (família = produto pai do Bling, ou o próprio SKU quando simples); algumas listas descem ao SKU (variação), porque a correção é na variação.

## Fontes

| Fonte | Região | Uso |
|---|---|---|
| `dbt_dw_az.tb_produto_gestao` | us-east4 | Foto atual de cada SKU vendável: visibilidade no site, estoque Bling × Nuvemshop, classificação, giro 30/60/90 dias, custo e preço, lacunas de cadastro. **Toda regra (o que é "visível", "tem estoque", alerta de vitrine, oferta sem estoque) está nesse modelo.** |
| `dbt_dw_az.tb_estoque_movimento_dia` | us-east4 | Zerou, voltou, subiu ou baixou, por SKU × dia (desde 02/07/2026). |
| `dbt_dw_az.tb_produto_teste` | us-east4 | Produtos em teste: prazo, critério, status recomendado (critérios aprovados em 25/09/2026, Notion "Classificação de Produtos — Conceitos" §4.4). |
| `dbt_dw_az.tb_produto_ciclo_monitor` | us-east4 | Em Saída e Dificuldade de Reposição. |
| `dbt_dw_us_az.tb_ga4_pagina_diaria` | **US** | Visualizações da página do produto (`/produtos/<handle>/`) nos últimos 30 dias fechados. Cruzada no pandas pelo `ds_url_produto` (handle da Nuvemshop). |

Visibilidade: a Nuvemshop tem **dois interruptores** — produto publicado (`published`) e variação visível (`variants.visible`). O produto só aparece para o cliente quando os dois estão ligados. As colunas `visible`, `stock_management` e `image_count` entraram na extração em 28/09/2026 (`sb_data_pipeline`, `extractors/nuvemshop_products.py`) e o histórico foi recarregado no mesmo dia.

## Filtro

**Frente** (Todas, Shibari, Curadoria). Vale para todas as seções que partem da `tb_produto_gestao`. Teste e ciclo de vida (seções 3 e 4) valem só para a Curadoria, onde a classificação foi definida.

## Seções

### 0. Onde agir hoje (cards)
Contagens de cada fila das seções abaixo: famílias visíveis sem estoque, SKUs com estoque fora do site, ofertas sem estoque, testes a decidir, SKUs que zeraram nos últimos 7 dias e produtos com estoque sem custo.

### 1. Site × estoque
- **Visível sem nenhum estoque** (família visível e estoque da família = 0): ocultar ou repor. Mostra compra pendente, visitas em 30 dias e peças vendidas em 90 dias, para decidir entre ocultar e repor rápido.
- **Com estoque e fora do site** (SKU com estoque no Bling e: fora da Nuvemshop, produto não publicado ou variação oculta): publicar ou cadastrar. É venda parada por falha de cadastro.
- **Estoque diferente entre Bling e Nuvemshop** (só variações com controle de estoque ligado): sincronizar.
- Expansor: **variações esgotadas visíveis** dentro de famílias com estoque (baixa prioridade: o site mostra a opção como esgotada) e variações **vendendo sem controle de estoque** e sem estoque no Bling.

### 2. O que mudou no estoque (últimos 7 dias)
Gráfico diário por tipo de movimento e tabela com a situação no site e a ação sugerida:
- Zerou e continua visível → ocultar ou repor.
- Voltou e não está visível → publicar.
- Voltou e está visível → divulgar a volta (Instagram, destaque).
- Subiu (entrada) → conferir a publicação e o preço.

A foto diária pode faltar em algum dia; nesse caso o movimento compara com a última foto disponível.

### 3. Produtos em teste
Tabela da `tb_produto_teste` com prazo, dias até o prazo, pedidos, payback e status recomendado. Destaques:
- **Teste a vencer:** prazo nos próximos **30 dias** (decisão do Hugo, 28/09/2026).
- **Critério atingido antes do prazo:** decidir já.
- **Saíram do teste nos últimos 90 dias:** Graduou (virou Ativo) ou Saiu.

O status é recomendação; a decisão é humana e a troca de ciclo é manual no Bling.

### 4. Em Saída e Dificuldade de Reposição
Tabela da `tb_produto_ciclo_monitor`: estoque, ritmo de venda, dias para zerar, status e ação sugerida (Em Saída); dias em ruptura e venda perdida estimada (Dificuldade de Reposição).

### 5. Ofertas (Cashing)
- Produtos com **Tipo de oferta** preenchido (campo do Bling; ofertas de order bump e upsell no Cashing), com estoque, visibilidade e giro. **Oferta sem estoque = trocar a oferta no Cashing agora.**
- **Candidatos a oferta:** visíveis, com estoque, sem oferta, papel Impulso ou Complementar e preço "por" até **R$ 60** (`PRECO_MAX_OFERTA`), ordenados pelo estoque a custo. É um filtro de apresentação: o teto de preço do papel Impulso (~R$ 35) ainda está em definição.
- Em 28/09/2026 o campo está sendo preenchido pelo time (só 1 produto preenchido); a seção fica vazia até lá.

### 6. Empurrãozinho — baixo giro
Famílias **visíveis e com estoque** que giram devagar: sem venda em 90 dias, ou risco de estoque "Encalhado" ou "Sobreestoque" (`tb_estoque_analitico`). Mostra o estoque parado a custo e separa pelo diagnóstico de visitas (30 dias):
- **Pouca visita** (abaixo da mediana das páginas de produto com visita no período) → dar visibilidade: destaque no site, Instagram, entrar como oferta.
- **Visitam e não compram** (na mediana ou acima) → revisar preço, fotos e descrição.

### 7. Procura sem estoque
Famílias **sem estoque** com visitas em 30 dias ou venda em 90 dias, ordenadas por visitas. É a fila de reposição pela demanda (mostra fornecedor e compra pendente).

### 8. Cadastro
Um SKU por linha, com filtro por tipo de problema: sem custo (a margem sai superestimada; prioridade quando tem estoque ou está visível), sem papel, sem ciclo (Curadoria), sem peso (afeta o frete), preço cheio da Nuvemshop diferente do Bling, sem imagem na Nuvemshop, fora da Nuvemshop.

## Limitações
- Visitas vêm do GA4 (histórico desde 01/07/2026; os últimos 1–2 dias ficam de fora). Página de produto com handle alterado perde o histórico anterior à troca.
- Estoque de kits (composição) no Bling depende do cadastro do kit.
- Com ~1 pedido/dia, "sem venda em 90 dias" é o sinal robusto; 30 dias oscila muito.
- A classificação (papel, ciclo, oferta) está em preenchimento: o SKU herda do pai quando o próprio campo está vazio.

## Atualização dos dados (28/09/2026)
Cabeçalho mostra **"Atualizado em"** = horário da tabela mais antiga que a página lê (`common/frescor.py`, a partir de `dbt_dw_az.__TABLES__`), e o expansor "De quando são os dados desta página" lista a última extração com sucesso de cada fonte (`raw_control.pipeline_runs`) e a data do GA4. Todas as extrações usadas aqui rodam de hora em hora entre :20 e :45 e o dbt na hora cheia (7h–23h), então as seções saem da mesma rodada. Alerta na página se o dbt passar de 90 minutos sem rodar dentro da janela.

## Vitrine como agravante (28/09/2026, pedido do Hugo)
A vitrine **não é uma seção à parte**: ela pesa os problemas das seções existentes. Fonte: `tb_produto_vitrine` (dbt), que chega à página pelas colunas de exposição da `tb_produto_gestao`.

- **Nível de exposição** (regra no dbt): 3 **Home** (alguma prateleira da home) › 2 **Vitrine** (categoria de vitrine visível — Liquidação, Seleção Prazer, Primeiras Cordas, campanhas — ou sugerido em "Produtos similares"/"Para comprar com esse produto" na página de outro produto) › 1 **Só categoria** › 0 **Fora do site**.
- **Coluna "Exposição"** em todas as listas (ex.: "Home · Os Mais Queridos (3º)", "Vitrine · sugerido em 7 páginas").
- **Ordenação:** o mais exposto primeiro (visível sem estoque, divergência de estoque, movimentos, Em Saída, procura sem estoque, cadastro). **Exceção — empurrãozinho:** o menos exposto primeiro, porque o primeiro empurrão é expor.
- **Ações que mudam com a exposição:** zerou e está na home → "Tirar da home ou repor já"; voltou e está só na categoria → "Divulgar a volta e colocar em vitrine"; empurrãozinho → "Sem vitrine" (expor) › "Em vitrine e pouca visita" (divulgar fora do site) › "Visitam e não compram" (página/preço).
- **Conflitos ciclo × vitrine** (`ds_alerta_exposicao`, dbt): Em Saída/Descontinuado na home; Descontinuado em vitrine; Em Teste só na categoria (entra como alerta na seção de teste).
- **Cadastro:** novos problemas — sem SEO, fotos sem texto alternativo, sem GTIN na Nuvemshop — e a lista de **categorias da loja visíveis e vazias** (`tb_categoria_loja`).
- Card "Visíveis sem estoque" separa quantos estão na home / em vitrine / só na categoria; card novo "Conflitos de vitrine".

Limitações: prateleiras da home atualizam de hora em hora; sugestões (similares/complementares) e ranking "mais vendidos" 1×/dia. Histórico da vitrine desde 28/09/2026. Leitura depende dos marcadores do tema da loja (`data-store`): se o tema mudar, a extração falha com erro em vez de gravar vitrine vazia.


## Ordem por urgência e leitura clara (28/09/2026, pedido do Hugo) — substitui a ordem das seções acima
**Ordem das seções = do maior problema para o menor:**
1. **Estoque diferente entre Bling e Nuvemshop** (primeiro: pode vender o que não tem). Coluna "Diferença (Nuvemshop − Bling)" e "Risco"; Nuvemshop maior que o Bling vem antes.
2. **Com estoque e fora do site.** Não entra o SKU que é **componente de kit/composição visível no site** (`fg_componente_kit_visivel`, dbt) — é vendido dentro do kit; esses ficam num expansor. Se o único kit que usa o item está oculto, continua no alerta com o kit indicado.
3. **Visíveis no site sem estoque.**
4. **Ofertas no Cashing** — oferta sem estoque primeiro (a vitrine mais cara de errar).
5. O que mudou no estoque (7 dias) · 6. Procura sem estoque · 7. Produtos em teste · 8. Em Saída e Dificuldade de Reposição · 9. Empurrãozinho · 10. Cadastro.
11. **De quando são os dados desta página** — no fim (o aviso de dado atrasado continua no topo). Mesmo padrão no SAC.

**Prioridade dentro das seções = exposição no site:** prateleira da home › oferta no Cashing › categoria de vitrine / sugestão em outras páginas › só na categoria › fora do site (`prioridade` = home×100 + oferta×10 + nível). Exceção: empurrãozinho (menos exposto primeiro).

**Home é a informação principal:** coluna própria "Prateleira da home" (prateleira · posição, +N se estiver em mais de uma), logo depois do nome, e **linhas de produto na home destacadas** (fundo âmbar, negrito) em todas as tabelas. O resto vai em "Outra exposição no site" (oferta no Cashing, categoria de vitrine, "sugerido em N páginas de produto").

**Rótulos sempre dizem onde e quando:** "Visível no site", "Estoque no Bling", "Estoque na Nuvemshop", "Unidades vendidas (90 dias)", "Visitas à página (30 dias)", "Compra pendente (un.)", "Ciclo de vida", "Preço cheio na Nuvemshop" etc.

## Resumo executivo do Kimba (28/09/2026, pedido do Hugo)
Caixa no topo da página: resumo do dia escrito pelo **Kimba** (persona de IA do projeto), para poupar minutos de leitura do relatório inteiro.
- **Quem decide o que é preocupante é o dbt** (`tb_gestao_sinal`, 1 linha por sinal x produto, com gravidade). A IA só redige a partir dessa lista curta; não analisa a base.
- **Sinais (gravidade):** 1 produto na home sem estoque · 2 Nuvemshop com mais estoque que o Bling · 3 oferta do Cashing sem estoque · 4 Em Saída/Descontinuado exposto · 5 com estoque e fora do site (exceto componente de kit visível) · 5 Nuvemshop com menos estoque que o Bling · 6 estoque zerou (hoje/ontem) · 7 teste vencendo (prazo em até 7 dias ou critério atingido) · 9 estoque voltou (informativo).
- **Fluxo:** função `resumo-executivo` (sb_data_pipeline), 1x/dia às 07:30 -> `raw_ia.resumo_executivo` (histórico) -> `dbt_dw_az.tb_resumo_executivo` (última do dia) -> esta página só lê. O Streamlit não chama IA.
- **Controles:** validação em código (todo número e nome de produto do texto precisa existir nos sinais; sem emoji; tamanho máximo); se a IA falhar ou reprovar, grava resumo por **template** com os mesmos sinais (`ds_origem = template`, rótulo "sem IA" na tela). A função falha se o dbt estiver com mais de 4h de atraso (não resume dado velho). "Mudou desde ontem" = comparação, em código, com os sinais do último dia anterior.
- **Limitação:** a validação garante que números e produtos vieram dos sinais, não que a redação seja perfeita; a tela avisa para conferir nas seções.
- **Seção 8:** as tabelas Em Saída e Dificuldade de Reposição ficam uma embaixo da outra (28/09/2026).
