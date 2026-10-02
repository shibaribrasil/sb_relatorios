# Spec — Novidades — Oportunidades (menu Catálogo)

Status: v1 construída em 02/10/2026 (dbt `sb_dw_dbt` branch `feat/novidade-oportunidades`; página `reports/novidades.py`). Escopo e decisões do Hugo: `sb_admin_team/planejamento/Novidades — Relatório de Oportunidades (Escopo, Out 2026).md`. Exploração dos sinais: `sb_admin_team/dados/Novidades — Sinais de Demanda (Exploração, Out 2026).md`.

## Pergunta da página
Das novidades mapeadas na aba `produto_novidade`, quais implementar primeiro, com base em dado? Frameworks: *assortment planning* (category management), priorização estilo RICE/ICE e *payback* do lote.

## Fontes
| Dado | Tabela | Observação |
|---|---|---|
| Lista de novidades, `Implementado?`, links | `stg_novidade_planilha` / `stg_novidade_link` (external table `datalake_drive.drive_produto_novidade`, macro `criar_external_table_novidade`) | a planilha **não é alterada**; leitura por posição (5 primeiras colunas) |
| Papel pretendido, família, termos de busca | `stg_novidade_atributo` (modelo dbt, rascunho do agente) | o Hugo revisa no PR; novidade nova na planilha aparece "Sem mapeamento" |
| Cotação de fornecedor | `raw_compras.cotacao_fornecedor` → `stg_novidade_cotacao` → `tb_novidade_cotacao` | feita no navegador do Hugo, append-only, **sem botão na página** |
| Premissas de margem | `tb_novidade_premissa` (da `tb_pedido`, 12 meses) | desconto, taxa, embalagem, reembolso, frete, imposto realizados |
| Meta de margem | `stg_meta_margem_preco` (cadastro) | papel pretendido × Revenda Nacional |
| Preço, margem, lote, payback | `tb_novidade_preco` | tudo no dbt |
| Vendas da família | `tb_novidade_familia_venda` (da `tb_pedido`, Curadoria) | proxy; repete entre novidades da mesma família |
| Demanda do site | `tb_ga4_busca_interna` (90d), `tb_gsc_consulta_diaria`, `tb_ga4_produto_dia` (30d) | região US, juntados **no pandas** |

## Regras e fórmulas
- **Custo regular à vista**: Sexy = preço de tabela × (1 − 20%), a promoção **nunca** entra (`fg_promocao_observada` só sinaliza); Vip = à vista observado; Gall = Pix. Indisponível ou sem preço não gera custo.
- **Custo de referência** = menor custo regular entre Sexy e Vip; Gall só se nenhuma das duas tem cotação disponível. Link com várias variações usa o menor custo (aviso quando o maior passa de 5% acima).
- **Preço piso** = custo ÷ (fator variável − meta), dividido por (1 − desconto médio), onde fator variável = 1 − taxa − embalagem − reembolso − imposto + resultado de frete (todos % da receita líquida realizados na `tb_pedido` em 12 meses). **Preço sugerido** = piso arredondado para cima terminando em ,90. Teste dbt garante que a MC projetada no preço sugerido nunca fica abaixo da meta.
- **MC projetada** = receita líquida × fator variável − custo; % sobre a receita líquida (mesma definição da margem realizada).
- **Meta**: a do cadastro. O cadastro **não tem linha para Impulso**; por decisão do Hugo (02/10/2026) o preço do Impulso é **custo × 2,5** (`ds_fonte_meta = markup impulso`, `pr_meta_margem` nulo, MC projetada só informativa). Impulso acima de R$ 30 segue com aviso.
- **Lote de teste** = 3 un (5 para Impulso) × custo; **payback** (meses) = lote ÷ (MC unitária × unidades/mês de um produto médio da família). Ordem de grandeza, não previsão.
- **Faixa de preço da família** = P25/mediana/P75 do preço médio vendido por produto (12m, Curadoria). O preço sugerido é **conferido** contra ela (abaixo / dentro / acima), nunca usado como piso.
- **Score 0–100** (pandas, só entre novidades abertas, cotadas e mapeadas): média ponderada de posições percentuais — margem (MC R$/un) 35%, vendas da família 25% (70% pedidos 12m + 30% tendência 90d), demanda do site 20% (média das posições de busca interna, impressões do Google sem marca e carrinhos GA4 da família), capital e payback 20% (lote menor e payback menor). Valor ausente conta como posição 0,5. Faixas: Implementar já ≥ 70 · Boa ≥ 50 · Avaliar ≥ 30 · Deixar. **Escala relativa a esta lista**, não nota absoluta.
- **Confiança**: Alta ≥ 20 pedidos da família em 12 meses; Média ≥ 10; Baixa abaixo. Cotação vencida (> 14 dias) rebaixa um nível.
- **Exceção à regra "score só na az"**: a az (us-east4) e o GA4/Search Console (US) não se juntam em SQL; o score é ranking de apresentação calculado no pandas (mesma exceção do Mapa de Interesse). Nenhum número financeiro é recalculado na página.

## Seções
Resumo do Kimba (texto automático **sem IA**), cartões, 1 Ranking (filtro por faixa), 2 Detalhe de uma novidade (componentes do score, custo por fornecedor com data e link, piso × sugerido, família e demanda), 3 Radar de lacunas, 4 Já implementadas, Última cotação por fornecedor, Regras aplicadas, De quando são os dados.

## Limites (mostrar sempre)
- ~1 pedido por dia: leia faixas, não posições exatas. Vendas e demanda são **da família**, não da novidade.
- GA4 de produto só desde 29/08/2026; Search Console com poucos dias; busca interna com ~200 sessões em 3 meses.
- **Viés de sobrevivência**: só se vendeu o que já foi escolhido; por isso existe o radar de lacunas (busca e Google), que é radar, não recomendação (volumes de 1 a 2 sessões por termo).
- Cotação com mais de 14 dias deve ser reconfirmada antes de comprar. Frete de entrada **não** entra no custo (a conferir se o custo do Bling o inclui).
- **Kimba com IA** exige o ranking dentro do dbt (o Kimba só redige sobre sinais calculados lá) e a chave da Anthropic válida no app; hoje a demanda está em outra região do BigQuery e a chave do Streamlit está inválida. Por isso o resumo é automático (template).

## Pendências
- Hugo revisar `stg_novidade_atributo` (papel pretendido e família de cada novidade) (a meta do Impulso, quando existir no cadastro, passa a valer sozinha no lugar do markup).
- Cotação real das novidades (hoje sem dados de produção).
- Fase 6: ligar novidade implementada ao SKU para medir o desempenho real e recalibrar os pesos depois do 1º ciclo de testes (90 dias).
