# Spec (rascunho) — Mapa de Interesse (segunda página de produto)

Status: ideia em discussão com o Hugo (28/09/2026). Nada implementado. Escrever spec final e conferir o GA4 antes de qualquer código.

## Ideia central: eficiência da vitrine
Conceito: *merchandising / space productivity* (varejo: vendas por metro quadrado de gôndola). Aqui a gôndola são as prateleiras da home, categorias de vitrine e blocos de sugestão. Pergunta da página: cada espaço nobre do site está ocupado pelo produto que mais converte esse espaço em margem?
Métricas: funil exposição → visita → carrinho → compra; RPV (receita por visita) e, de preferência, **margem por visita** (não empurrar produto que vende mas não paga a conta).

## Quadrantes (interesse × conversão), adaptação da matriz BCG
- Muita visita + conversão alta = **Estrela** (proteger estoque, manter na home)
- Muita visita + conversão baixa = **Vitrine que não fecha** (preço, foto, descrição, frete, variação)
- Pouca visita + conversão alta = **Joia escondida** (candidata a subir para a home)
- Pouca visita + conversão baixa = **Cão** (sair da vitrine / Em Saída)

## Seções previstas
1. Mapa de interesse: dispersão visitas × conversão, bolha = margem, cor = nível de exposição (home/vitrine/categoria/fora).
2. Desalinhamentos: exposição alta com desempenho baixo, e o inverso (a parte mais acionável).
3. Funil por prateleira da home (a prateleira inteira rende?).
4. Efeito da posição na prateleira (posição 1 vs 6).
5. Fadiga de vitrine: dias na home × evolução da conversão (precisa de histórico).
6. Efeito da exposição, antes e depois de entrar na home (precisa de histórico; único jeito honesto de separar causa de correlação).
7. Concentração ABC/Pareto da margem × onde está a exposição.

## Limites dos dados
- Histórico de vitrine só existe desde 28/09/2026: seções 5 e 6 só ficam confiáveis após semanas de captura; 1–4 e 7 dá para fazer já (foto atual + giro 30/60/90).
- Correlação não é causa: produto que vende bem tende a ser posto na home.
- A confirmar: se o GA4 entrega `view_item_list`/`select_item` (cliques por prateleira) e `add_to_cart` por produto. A auditoria do GA4 já apontou problemas de eventos. Sem eles, funil = visita → compra.
- Visita não é só vitrine (anúncio, busca): separar por origem quando o dado permitir.

## Decisões em aberto (Hugo)
1. Métrica-âncora: margem por visita (recomendado) ou receita por visita.
2. A página só aponta desalinhamentos ou sugere "trocar X por Y na prateleira Z"?
3. Janela: 30, 60 (sugerido como padrão, com alternância) ou 90 dias.
4. Começar por 1–4 e 7 e deixar 5–6 para quando o histórico acumular?
