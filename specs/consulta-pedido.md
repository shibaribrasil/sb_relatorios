# Spec — Consulta de Pedidos (`reports/consulta_pedido.py`, menu Diário)

Status: v2 em 04/11/2026 (v1 no mesmo dia). Pedido do Hugo: página bem visual, pouco texto, "Desempenho do Produto, mas do pedido", com o seletor no pedido mais recente por padrão e um panorama o mais real possível de como a venda foi.

## Pergunta da página
Como foi esta venda, do que o cliente comprou até o que sobrou depois de taxa, envio, imposto, custo do produto e custo de aquisição?

## Unidade de análise
- **Um pedido válido** (`fg_pedido_valido`: pago, não cancelado), de ago/2025 em diante (taxa e frete reais da Nuvemshop só são confiáveis desde então). Pedido cancelado não aparece.
- Seletor ordenado do mais recente (data, depois maior código da Nuvemshop) para o mais antigo; **vem com o mais recente selecionado**. Código exibido = o da Nuvemshop; pedido sem ele (ex.: Mercado Livre) aparece como "Bling nnnn".

## Fontes (az, us-east4; Google Ads na região US, junta no pandas)
| Seção | Tabela |
|---|---|
| Itens, descontos, frete, taxa, embalagem, imposto, reembolso, custo, margem, cliente, meio de pagamento, data de recebimento | `tb_pedido` (linhas do pedido) |
| Foto | `tb_produto_gestao.lk_imagem_produto` (SKU; se faltar, outra variação da família). Link do Bling é assinado e vence: se quebrar, a imagem some |
| Regra do cupom (tipo, valor, pedido mínimo) | `tb_cupom` (por `ds_codigo`) |
| Origem e mídia | `tb_atribuicao_pedido` (`ds_origem_venda`, `ds_midia_venda`, `ds_gclid`, `ds_utm_campaign`) |
| Custo de aquisição | `dbt_dw_us_az.tb_gads_clique_campanha` (gclid → campanha) e `tb_gads_campanha_performance` (custo por campanha e dia), via `origem_campanha.carregar_campanhas` |

## A cascata (ordem da página)
1. **O pedido:** itens com foto, quantidade × preço cheio unitário (`vl_receita_bruta_produto ÷ qt_item`) e subtotal; "com desconto R$ X" quando a linha pagou menos. Brinde aparece por último, a R$ 0,00.
2. **Do preço ao valor pago:** Itens (preço cheio, sem brinde) − Descontos (= itens − receita líquida de produtos) + Frete cobrado (`vl_frete_pago_rateio`) = **Valor pago** (receita líquida de produtos + frete pago). Logo abaixo, **Descontos que o cliente recebeu**, um card por origem (ver "Descontos destrinchados").
3. **Do valor pago ao caixa:** − Taxa de pagamento real (`vl_taxa_pedido_rateio`) = **Entrou na conta** (mesma definição de `tb_caixa_entrada_pedido.vl_recebido`, com a data de `dt_recebimento_pedido`: "cai em" se futura, "caiu em" se passada). Depois, ainda do valor pago: − Frete real da etiqueta (`vl_frete_real_rateio`) − Embalagem (`vl_embalagem_rateio`) − Imposto (`vl_imposto_rateio`) − Reembolso (`vl_reembolso_rateio`, só se houver).
   - Decisão de ordem: "Entrou na conta" vem logo depois da taxa porque é isso que o gateway deposita; etiqueta, embalagem e imposto são custos do pedido que não saem desse depósito.
4. **Custo e margem:** − Custo dos produtos (`vl_custo_linha`, **inclui o custo do brinde**) = **Margem de contribuição** (`vl_margem_contribuicao`, antes de mídia); % = margem ÷ receita líquida de produtos; semáforo igual ao de Vendas & Margem (verde ≥ 50%, âmbar ≥ 40%).
5. **Origem e custo de aquisição:** origem, mídia e campanha (UTM); custo de aquisição estimado; **margem após aquisição** = margem de contribuição − custo de aquisição (R$ e %).

**Visual:** sem gráfico; a página é feita de cards na ordem acima (o gráfico de cascata "Panorama da venda" da v1 saiu a pedido do Hugo, 04/11/2026).

**Fechamento:** valor pago − taxa − etiqueta − embalagem − imposto − reembolso − custo = margem de contribuição do dbt (teste `tests/test_consulta_pedido.py`). Se a diferença passar de R$ 0,02 a página avisa. Pedido com `ds_incompletude` (sem custo, sem taxa, sem dados da Nuvemshop) leva aviso: a margem está superestimada.

## Descontos destrinchados (v2)
Um card por origem, só das linhas que não são brinde (o desconto do brinde, 100% do valor dele, não é desconto ao cliente). A soma dos cards = o card "(−) Descontos".
- **Cupom** (`vl_desconto_cupom_rateio`): título com o código (`ds_codigo_cupom_nuvemshop`); detalhe com a regra do cupom em `tb_cupom` ("20% de desconto" ou "R$ 5,00 fixo", mais "pedido mín. R$ X" e "1ª compra" quando houver). Cupom apagado na Nuvemshop: mostra o código e "regra do cupom indisponível".
- **Desconto Pix** (`vl_desconto_pagamento_rateio`, o desconto do gateway): rotulado "Pix" quando o meio de pagamento é Pix; senão "Desconto do pagamento".
- **Promoção da loja** (`vl_desconto_promocional_rateio`, promoção automática): só o valor. **O nome da promoção não existe na base**: o extrator da Nuvemshop (`sb_data_pipeline/extractors/nuvemshop_orders.py`) guarda apenas `promotional_discount.total_discount_amount`. Para mostrar o nome é preciso capturar o detalhe das promoções aplicadas no extrator e expô-lo no `tb_pedido` (mudança no `sb_data_pipeline` e no `sb_dw_dbt`, não aqui). Também não dá para dizer em qual item a promoção incidiu: o dbt rateia o valor entre as linhas pela proporção do valor, não pela regra da promoção.
- **Outro desconto:** o que sobrar do desconto total depois de cupom, Pix e promoção (ajuste manual no pedido).

## Custo de aquisição (única conta própria da página)
Só o Google Ads tem custo na base. Regra, por origem/mídia do pedido:
- **google / cpc**, mês ≥ jun/2026: custo da **campanha do clique** (gclid → campanha) no mês do pedido ÷ pedidos válidos do mês atribuídos a essa campanha. Sem campanha identificada: custo de **todo o Ads** no mês ÷ pedidos Google pago do mês. É um **custo médio por pedido da campanha no mês**, não o custo do clique daquele cliente.
- **instagram / paid_social:** "—" (o custo do Meta Ads não está na base).
- **Qualquer outra origem** (direto, orgânico, e-mail, Instagram orgânico): R$ 0,00, "sem mídia paga rastreada".
- Google pago antes de jun/2026 ou mês sem custo lançado: "—" (a margem após aquisição também fica "—" e a cascata termina na margem de contribuição).
- Limites: o divisor conta só pedidos com clique identificável (piso), então o custo por pedido é um teto; o custo do Ads chega com 1–2 dias de atraso, então o mês corrente muda ao longo dos dias; o Instagram pago e qualquer outro canal pago futuro ficam sem custo até entrar na base.

## Limites (resumo)
- ~1 pedido/dia: a margem de um pedido isolado não é tendência.
- Embalagem é estimativa por pedido (parâmetro com vigência em `stg_parametro_operacional`; pedido antigo usa o valor da época).
- Pedido do Mercado Livre: taxa e frete vêm das tarifas que o Bling traz (`stg_pedido_marketplace`); amostra de 1 pedido (31/10/2026).
