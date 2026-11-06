# Spec — Consulta de Pedidos (`reports/consulta_pedido.py`, menu Diário)

Status: v8 em 06/11/2026 (custo de aquisição com o dia corrente, via API; v7 prévia do custo do clique, v6 no mesmo dia, v1 a v5 em 04/11/2026). Pedido do Hugo: página bem visual, pouco texto, "Desempenho do Produto, mas do pedido", com o seletor no pedido mais recente por padrão e um panorama o mais real possível de como a venda foi.

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
| Envio (opção, transportadora, rastreio, expedição, entrega, previsão) | `tb_logistica_pedido` |
| Cliente (nome, cidade/UF, WhatsApp, nº de pedidos, total consumido) | `tb_cliente` (por `cd_contato`) |
| Mensagem de cashback (W0) e cupom de cashback | `raw_control.vw_contato_cliente` (`tipo_tarefa = 'jornada_w0'`, chave `<cd_contato>|<cd_codigo_interno>`) e `raw_control.cupons_gerados` (`campanha = 'cupom_cashback_posvenda'`) |
| Origem e mídia (já tratadas), UTM, clique, dispositivo do checkout | `tb_atribuicao_pedido` (`ds_origem_venda`, `ds_midia_venda`, `ds_origem_venda_url`, `ds_midia_venda_url`, `fg_origem_recuperada_ga4`, `ds_utm_*`, `ds_gclid`, `ds_origem_checkout_nuvemshop`) |
| URL de entrada inteira e origem informada no pedido | `dbt_dw_stg.stg_nuvemshop_pedido` (`ds_url_entrada` = `landing_url` da Nuvemshop, `ds_origem_pedido` = `order_origin`), casada com `tb_atribuicao_pedido` pela mesma chave do dbt (parte antes do `_` de `cd_pedido_loja` = id ou número da Nuvemshop). A querystring não está em nenhuma tabela `az`: a página lê a staging só para isto |
| Custo de aquisição | `dbt_dw_us_az.tb_gads_clique_campanha` (gclid → campanha) e `tb_gads_campanha_performance` (custo por campanha e dia), via `origem_campanha.carregar_campanhas` |

## Layout (v6)
Estilo do pedido da Nuvemshop (pedido do Hugo): **extrato**, não cards por indicador. Depois da lista de itens com foto, dois blocos de quadros (rótulo e detalhe à esquerda, valor à direita; negativos em vermelho; linhas de total em destaque):
1. **Da venda à margem:** quadros **Pagamento e margem** (um quadro só, do subtotal à margem de contribuição) e **Origem e aquisição**. Não há quadro de margem por item (saiu na v5).
2. **Venda e cliente** (**no fim da página**, depois da margem): quadros **Informações da venda** e **Cliente** (abaixo). São consulta de apoio; o que o pedido rendeu vem primeiro.
Sem gráfico (o "Panorama da venda" saiu na v2).

**Celular (v4):** a página não pode ter rolagem horizontal (testado a 375 e 320 px). As grades de quadros e de itens usam colunas de no mínimo 420/340 px **limitadas a 100% da largura** (`minmax(min(420px,100%),1fr)`), então no celular viram 1 coluna. Nas linhas do extrato, valores curtos (até 18 caracteres) ficam numa linha só; texto longo (meio de pagamento, origem) quebra em vez de alargar o quadro.

## Informações da venda e Cliente (v3; mostrados no fim da página desde a v4)
**Informações da venda:** primeiro o bloco **Origem da venda** (abaixo), depois pagamento (meio e parcelas: "à vista" no cartão de 1x; bandeira e gateway no detalhe), envio (opção e transportadora), rastreio (link para o código), expedição, entrega e mensagem de cashback.
- **Expedição / entrega:** "Expedido em" só com `dt_expedicao`; "Entregue em" só com `dt_entrega` (confirmada). Pedido `EM ABERTO` sem expedição = "Aguardando postagem"; sem expedição e já fora de aberto = "Sem registro de expedição" (rastreio pode não ter sido carregado). **Não** usar `ds_situacao_logistica`/`fg_entregue`: ficam verdadeiros para pedido sem rastreio carregado (achado de 13/10/2026). Sem entrega, mostra a previsão (`dt_estimada_entrega_max`).
- **Mensagem de cashback** = a mensagem W0 da jornada de pós-venda (cashback logo após o pagamento). "Recebeu em dd/mm" quando há registro `jornada_w0` com `fg_alcancou`; "Enviada ..., sem confirmação" quando o registro existe e não alcançou o cliente (ex.: WhatsApp inválido). Sem registro: "Cupom gerado, mensagem ainda não enviada" se já existe cupom; "Sem cashback (4º pedido)" a partir do 4º pedido do cliente (**atalho:** `MAX_PEDIDO_CASHBACK = 3` espelha o parâmetro `max_pedido_cashback` do dbt); senão "Não enviada". O detalhe traz o cupom (código, valor, validade). Se o check-in W1 existir, aparece uma linha a mais. Horários em UTC são convertidos para Brasília.
- **Cliente:** nome, cidade/UF do cadastro (`tb_cliente`; sem cadastro, a cidade da entrega), WhatsApp (link `wa.me`, sem mensagem), pedidos feitos (`qt_pedido`, com 1ª e última compra) e **total consumido** = `vl_total_pedido` (produtos líquidos + frete pago, acumulado; o detalhe mostra os produtos sem frete). Pedido sem cliente vinculado mostra "Cadastro não encontrado".

## Origem da venda (v6; linhas no topo de Informações da venda)
Mostra de onde veio a venda e onde o cliente fechou a compra, sem recalcular nada: o que o DW já tratou (`tb_atribuicao_pedido`) mais a URL de entrada crua da Nuvemshop. Linhas que não têm dado não aparecem.
| Linha | Regra |
|---|---|
| **Origem da venda** | `ds_origem_venda · ds_midia_venda` (a origem **final**). O detalhe traz o significado da mídia (`cpc` = anúncio pago, `organico_srsltid` = orgânico do Google/Merchant Center, `paid_social` = social pago...) e a fonte: "lida da URL de entrada", ou "recuperada pelo GA4" quando `fg_origem_recuperada_ga4` (a URL não trazia origem e o GA4 ligou o comprador a um clique do Google Ads; a leitura só pela URL era "(sem parametro)"). Sem origem: "Direto / sem parâmetro" |
| **Clique de anúncio** | Sinais na URL: `gclid`/`gbraid`/`wbraid`/`gad_source` = Google Ads, `fbclid` = Meta Ads, `srsltid` = Google orgânico (Merchant Center, **não** é anúncio). "Recuperado pelo GA4" quando a origem foi recuperada |
| **Campanha do Google Ads** | 1º o `gad_campaignid` da URL de entrada (vale no mesmo dia, mesmo antes de o Ads ligar o clique); 2º o clique (`gclid` → campanha em `tb_gads_clique_campanha`, ~1 dia de atraso). O nome vem das tabelas do Ads (clique ou performance); id sem nome conhecido aparece como "campanha <id>" |
| **Campanha (UTM)** | `utm_campaign` de `tb_atribuicao_pedido`; no detalhe os outros `utm_*` que existirem |
| **Finalizou a compra em** | `ds_origem_checkout_nuvemshop` (`storefront` da Nuvemshop): `mobile` = Celular, `store` = Computador, `form` = Pedido manual (link / formulário). **Inferência, a confirmar com a Nuvemshop:** a doc do dbt diz "mobile (app)", mas os pedidos `mobile` (58% do total) têm URL de entrada de página da loja, o que um app não gera; os `form` (44 pedidos, nenhum com URL) são criados à mão no painel |
| **Origem informada no pedido** | `order_origin`: texto livre preenchido na criação manual do pedido ("Whatsapp", "Instagram", "Parceria com Influencer"...). Só aparece quando preenchido (quase só em pedido `form`) |
| **Página de entrada** | Caminho da `ds_url_entrada` ("Página inicial" quando é `/`) |
| **URL de entrada (Nuvemshop)** | `ds_url_entrada` inteira, copiável, com o link "abrir página" apontando **para a página sem querystring** (abrir a URL com o `gclid` geraria uma visita falsa com clique de anúncio). Só vira link se for `http(s)`: a URL vem de fora. Sem URL: "não registrada" (pedido manual ou sem sessão rastreável) |

**Limites:** a Nuvemshop grava no máximo **255 caracteres** da URL (máximo observado = 255): numa URL longa o `gclid` pode ter sido cortado (a página avisa). A URL não diferencia direto de orgânico quando não tem parâmetro (limitação herdada do dbt). `gad_campaignid` só existe em clique de Google Ads com auto-tagging.

## A cascata (ordem da página)
1. **O pedido:** itens com foto, quantidade × preço cheio unitário (`vl_receita_bruta_produto ÷ qt_item`) e subtotal; "com desconto R$ X" quando a linha pagou menos. Brinde aparece por último, a R$ 0,00.
2. **Pagamento e margem (um quadro só):** a parte de cima é o pagamento: Itens (preço cheio, sem brinde) − Descontos (= itens − receita líquida de produtos) + Frete cobrado (`vl_frete_pago_rateio`) = **Total pago pelo cliente** (receita líquida de produtos + frete pago). Os descontos aparecem como linhas recuadas, uma por origem (ver "Descontos destrinchados"). Depois, − Taxa de pagamento real (`vl_taxa_pedido_rateio`) = **Total líquido (entrou na conta)** (mesma definição de `tb_caixa_entrada_pedido.vl_recebido`, com a data de `dt_recebimento_pedido`: "cai em" se futura, "caiu em" se passada).
3. **Do caixa à margem (continuação do mesmo quadro):** − Frete real da etiqueta (`vl_frete_real_rateio`) − Embalagem (`vl_embalagem_rateio`) − Imposto (`vl_imposto_rateio`) − Reembolso (`vl_reembolso_rateio`, só se houver) − Custo dos produtos (`vl_custo_linha`, **inclui o custo do brinde**) = **Margem de contribuição** (`vl_margem_contribuicao`, antes de mídia). O selo do quadro mostra a margem % = margem ÷ receita líquida de produtos; semáforo igual ao de Vendas & Margem (verde ≥ 50%, âmbar ≥ 40%).
   - Decisão de ordem: o "Total líquido" vem logo depois da taxa porque é isso que o gateway deposita; etiqueta, embalagem e imposto são custos do pedido que não saem desse depósito.
4. **Origem e aquisição (quadro):** origem e mídia (o detalhe da origem, com campanha, UTM e URL, está em Informações da venda desde a v6); custo de aquisição estimado; **margem após aquisição** = margem de contribuição − custo de aquisição (R$ e %).

**Fechamento:** valor pago − taxa − etiqueta − embalagem − imposto − reembolso − custo = margem de contribuição do dbt (teste `tests/test_consulta_pedido.py`). Se a diferença passar de R$ 0,02 a página avisa. Pedido com `ds_incompletude` (sem custo, sem taxa, sem dados da Nuvemshop) leva aviso: a margem está superestimada.

## Descontos destrinchados (v2)
Uma linha (recuada, em vermelho) por origem, só das linhas que não são brinde (o desconto do brinde, 100% do valor dele, não é desconto ao cliente). A soma das linhas = o desconto total (subtotal − receita líquida de produtos).
- **Cupom** (`vl_desconto_cupom_rateio`): título com o código (`ds_codigo_cupom_nuvemshop`); detalhe com a regra do cupom em `tb_cupom` ("20% de desconto" ou "R$ 5,00 fixo", mais "pedido mín. R$ X" e "1ª compra" quando houver). Cupom apagado na Nuvemshop: mostra o código e "regra do cupom indisponível".
- **Desconto Pix** (`vl_desconto_pagamento_rateio`, o desconto do gateway): rotulado "Pix" quando o meio de pagamento é Pix; senão "Desconto do pagamento".
- **Promoção da loja** (`vl_desconto_promocional_rateio`, promoção automática): só o valor. **O nome da promoção não existe na base**: o extrator da Nuvemshop (`sb_data_pipeline/extractors/nuvemshop_orders.py`) guarda apenas `promotional_discount.total_discount_amount`. Para mostrar o nome é preciso capturar o detalhe das promoções aplicadas no extrator e expô-lo no `tb_pedido` (mudança no `sb_data_pipeline` e no `sb_dw_dbt`, não aqui). Também não dá para dizer em qual item a promoção incidiu: o dbt rateia o valor entre as linhas pela proporção do valor, não pela regra da promoção.
- **Outro desconto:** o que sobrar do desconto total depois de cupom, Pix e promoção (ajuste manual no pedido).

## Custo de aquisição (única conta própria da página)
Só o Google Ads tem custo na base. Regra, por origem/mídia do pedido:
- **google / cpc**, mês ≥ jun/2026, nesta ordem:
  1. **Campanha do clique identificada** (gclid → campanha em `tb_gads_clique_campanha`): custo da campanha no mês do pedido ÷ pedidos Google pago do mês atribuídos a ela. É um **custo médio por pedido da campanha no mês**, não o custo do clique daquele cliente.
  2. **Senão, custo médio por venda do Google Ads nos últimos 30 dias** (v4): custo total do Ads ÷ pedidos Google pago (com ou sem clique identificável) na janela de 30 dias que termina no dia do pedido, ou no **último dia com custo lançado** se o pedido for mais novo que o custo (pedidos depois desse dia não entram no divisor, para custo e pedidos cobrirem o mesmo período). Vale para: clique que o Google Ads ainda não ligou à campanha, pedido Google pago sem gclid e mês sem custo lançado (ex.: dia 1). O detalhe da linha diz "estimativa" e o motivo.
- **instagram / paid_social:** "—" (o custo do Meta Ads não está na base).
- **Qualquer outra origem** (direto, orgânico, e-mail, Instagram orgânico): R$ 0,00, "sem mídia paga rastreada".
- Google pago antes de jun/2026: "—" (a margem após aquisição também fica "—" e a cascata termina na margem de contribuição).

**Defasagem dos dados (v8, 06/11/2026):** origem e mídia do pedido (`tb_atribuicao_pedido`) existem no mesmo dia, porque vêm da URL de entrada gravada no pedido. Até 04/11 atrasavam o vínculo **clique → campanha** e o **custo** (a transferência do Google Ads traz só o dia anterior, ~11:35 BRT). Desde 06/11 os dois chegam de hora em hora pela Google Ads API (`google-ads-cliques-hourly`): o custo por campanha vem de `tb_gads_campanha_custo_dia` (US; a transferência quando já carregou o dia, senão a API) e o clique de `stg_gads_clique`. O pedido do dia usa a campanha do clique e o custo do mês até agora; quando o mês inclui custo de hoje, a linha avisa "inclui custo provisório de hoje" (o Google pode ajustar). `tb_gads_campanha_performance` (com conversão, usada em outras páginas) segue só com a transferência. Cache de 5 min na página.

- Limites: a média de 30 dias é uma estimativa (cada campanha custa diferente) e o pedido sem gclid pode nem ter vindo do anúncio; o custo por campanha do mês corrente muda ao longo dos dias; o divisor da média conta todos os pedidos Google pago, não só os com clique identificável; o Instagram pago e qualquer outro canal pago futuro ficam sem custo até entrar na base.

## Prévia do custo do clique (v7, 06/11/2026)
Pedido do Hugo: ver o custo do pedido quase em tempo real para medir margem pedido a pedido. No quadro **Origem e aquisição**, para pedido google / cpc com `gclid`, duas linhas a mais: **Custo do clique (prévia)** e **Margem após o clique (prévia)** (= margem de contribuição − custo do clique).
- **Fonte:** `dbt_dw_az.tb_gads_clique_custo` (publicada de US por `scripts/publicar_us_para_useast4.py`), ligada ao pedido por `tb_atribuicao_pedido.ds_gclid`. Sem conta própria: o dbt traz o valor pronto.
- **O que é:** o Google não informa custo por clique. A prévia é o **CPC médio do dia do clique** da palavra-chave (texto + correspondência no grupo) ou, sem palavra (Shopping), do grupo de anúncios. O detalhe da linha diz qual e de que dia.
- **Atualização:** extração de cliques e custo de hora em hora (`google-ads-cliques-hourly`, `:50`) e dbt na hora cheia; cache da página de 5 min. O pedido de hoje já tem prévia em até ~2 h.
- **Limites:** é **piso** do custo de aquisição (só o clique que comprou, sem os que não compraram: o custo de aquisição acima, que reparte o custo da campanha pelos pedidos, é o mais próximo do CAC). É média do dia, não o custo exato do clique. O dia corrente é parcial e o Google pode ajustar o custo depois (cliques inválidos). Instagram pago e demais canais seguem sem prévia.

## Limites (resumo)
- ~1 pedido/dia: a margem de um pedido isolado não é tendência.
- Embalagem é estimativa por pedido (parâmetro com vigência em `stg_parametro_operacional`; pedido antigo usa o valor da época).
- Pedido do Mercado Livre: taxa e frete vêm das tarifas que o Bling traz (`stg_pedido_marketplace`); amostra de 1 pedido (31/10/2026).
