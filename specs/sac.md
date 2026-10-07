# Spec — SAC — Tarefas do Dia (`reports/sac.py`, camada Diária)

Página de **trabalho**, não de leitura gerencial: dá tarefas ao SAC (hoje: Robson) e deixa ele marcar o que já fez. Pensada para crescer com outras listas de tarefas, não só entregas com problema.

## Por que uma tabela fora do dbt

O estado de "já tratei" **precisa sobreviver** às atualizações de dados (o job do dbt roda de hora em hora e recria as tabelas `az`/`stg`). Por isso o check não pode morar em nenhuma tabela do dbt. Ele vive em `raw_control.sac_tarefas` — um dataset e uma tabela criados à mão (fora do projeto `sb_dw_dbt`), que **nenhum model referencia**. O `dbt run` nunca a toca: cria, dropa ou recria só o que está no grafo do dbt.

Cada carga da página faz um `LEFT JOIN`, pela chave estável do item (hoje `cd_codigo_interno`, o código interno do pedido no Bling), entre os dados novos vindos do dbt e o estado salvo em `sac_tarefas`. Consequência prática:
- O que foi marcado continua marcado depois que os dados atualizarem.
- O que não foi marcado continua sem marca.
- Se o item sair da lista (por ex., a entrega deixou de ter problema), ele simplesmente não aparece mais — mas a linha da tarefa continua na tabela, sem ser apagada, para o caso de ele voltar a aparecer um dia.

## Schema de `raw_control.sac_tarefas`

| Coluna | Tipo | O que é |
|---|---|---|
| `tipo_tarefa` | STRING | Qual lista: `entrega_problema`, `carrinho_abandonado`, `pedido_cancelado`. Novas listas usam um novo valor aqui — mesma tabela, sem migração. |
| `chave` | STRING | Identificador estável do item dentro do tipo, como texto: `cd_codigo_interno` (entregas), `cd_carrinho` (carrinhos), `cd_pedido_nuvemshop` (cancelados). |
| `fg_feito` | BOOL | Marcado como concluído. |
| `nm_responsavel` | STRING | Existe no schema, mas não é usada — decisão do Hugo (23/set/2026): a página não identifica quem marcou. Fica vazia. |
| `ds_resultado` | STRING | Resolução do contato escolhida na coluna **Resolução** (opções por lista em `common/mensagens_sac.py`, `RESULTADOS`). Estado atual — pode ser trocado ou limpo a qualquer momento. Adicionada em 28/09/2026. |
| `ds_observacao` | STRING | Livre, hoje não usado na tela (campo pronto para o futuro). |
| `dt_criacao` / `dt_atualizacao` | TIMESTAMP | Quando a linha nasceu / foi tocada pela última vez. |

Leitura e escrita: `common/tarefas.py` (`carregar_tarefas(tipo)`, `salvar_tarefas(tipo, [(chave, feito, resultado), ...])` — grava check e resultado de vários itens de uma vez num único `MERGE` parametrizado e acrescenta 1 linha por item em `raw_control.sac_tarefas_historico`; `carregar_tarefas` mantém a linha mais recente por chave).

**Histórico — `raw_control.sac_tarefas_historico`** (append-only, fora do dbt, criada em 28/09/2026): `tipo_tarefa`, `chave`, `fg_feito`, `ds_resultado`, `dt_evento` — 1 linha a cada gravação. Serve para ver a evolução de um contato (ex.: "Vai pensar" → "Comprou") e medir conversão do SAC por resultado.

## Ordem da página (decisão do Hugo, 28/09/2026)

1. Carrinhos abandonados · 2. Pedidos cancelados · 3. Entregas com problema. Em todas, o link de WhatsApp aparece com o texto **"Mensagem"** (coluna estreita) e as colunas editáveis são **Já tratei** e **Resolução**, nessa ordem, logo após o WhatsApp. "Obs." é uma coluna larga (texto completo), só em carrinhos e cancelados.

## Seção "Entregas com problema — falar com o cliente"

- **Fonte:** `common/logistica.py` (`tb_logistica_pedido`), o mesmo critério de risco do Pulso do Dia — atrasado (`fg_atrasado_em_aberto`), problema de entrega ativo (`fg_problema_entrega_ativo`) ou parado (em trânsito, sem evento de rastreio há 10+ dias — mesmo limite do Pulso do Dia, `DIAS_PARADO`).
- **Tabela editável** (`st.data_editor`): Pedido (número do pedido de venda na Nuvemshop, `#cd_pedido_loja`), Cliente, Motivo, Rastreio, Dias sem evento, **WhatsApp**, **Já tratei**, **Resolução**. Só Já tratei e Resolução são editáveis.
- **Mensagem por motivo** (prioridade: problema de entrega > atrasado > parado): problema de entrega pede confirmação de endereço/recebedor; atrasado e parado avisam que estamos acompanhando e perguntam se já recebeu. Inclui o código e o link de rastreio quando existem. Telefone: `nr_telefone_cliente` (cadastro Nuvemshop, na `tb_logistica_pedido`).
- **Sem identificação de quem marca** — não pede nome nem usa login do Streamlit Cloud.
- **Toggle "Mostrar também os já tratados"**: por padrão a lista só mostra pendentes, para o SAC ver o que falta, não o que já foi feito.
- Marcar/desmarcar e escolher a Resolução **não gravam na hora**: as trocas ficam na tela (o usuário pode mudar de ideia) e o botão **"Salvar alterações (N)"** grava tudo de uma vez e recarrega (`st.rerun()`) para atualizar os contadores. Só o estado salvo entra no histórico. Mudar o filtro "Mostrar também os já tratados" ou a recarga dos dados antes de salvar descarta as trocas não salvas. (Decisão do Hugo, 28/09/2026; também evita gravações concorrentes, que duplicaram uma chave.)

## Coluna "Resolução" (todas as listas)

Lista de opções (`SelectboxColumn`) editável, logo depois de "Já tratei" (que vem logo após o WhatsApp). Chamava-se "Resultado" até 28/09/2026; o campo gravado continua `ds_resultado`. Independente do check: o atendente pode registrar "Vai pensar" sem marcar como tratado (para voltar depois) ou trocar o resultado quando o cliente responder. Para mudar o resultado de um item já tratado, ative "Mostrar também os já tratados". Cada troca grava o estado atual em `sac_tarefas.ds_resultado` e uma linha no histórico.

| Lista (`tipo_tarefa`) | Opções |
|---|---|
| Carrinhos (`carrinho_abandonado`) | Comprou · Vai pensar · Sem interesse — preço/frete · Sem interesse — outro motivo · Sem resposta · Telefone inválido / sem WhatsApp |
| Entregas (`entrega_problema`) | Cliente já recebeu · Aguardando transportadora · Endereço corrigido / nova tentativa · Reenvio feito · Reembolsado · Reclamação aberta na transportadora · Sem resposta |
| Cancelados (`pedido_cancelado`) | Refez o pedido · Vai pensar · Estorno confirmado · Estorno pendente — resolver · Desistiu — preço/frete · Desistiu — prazo de entrega · Desistiu — outro motivo · Sem resposta |

Mudar opções: **acrescentar** é seguro; renomear/remover faz o valor antigo gravado aparecer vazio na tela (continua na tabela e no histórico).

## Link de WhatsApp (todas as listas)

Coluna **WhatsApp** (`LinkColumn`, texto "Mensagem") com `https://wa.me/<telefone>?text=<mensagem>` — abre a conversa com a mensagem **já escrita para aquela situação**; o atendente revisa e envia (nada é enviado sozinho). Textos em `common/mensagens_sac.py` (assinatura: "Robson, da Shibari Brasil" — constantes `ATENDENTE` e `LOJA`).

**Padrão das mensagens (decisão do Hugo, 07/10/2026 — vale para TODA mensagem de WhatsApp a cliente, as atuais e as novas):** (1) **rodapé obrigatório** no fim: "Se preferir não receber mais mensagens, é só responder *SAIR*." (`RODAPE_SAIR`; cada `msg_*` fecha com `_fecha` e `link_whatsapp` garante o rodapé, sem duplicar; quem responde SAIR vira "Não retomar contato"); (2) **negrito do WhatsApp** (`*texto*`, `_neg`) nas partes importantes: cupom/código, vantagem oferecida (% ou crédito, sticker, Pix), prazo/validade, valor mínimo e a pergunta ou ação principal; nome e saudação sem negrito, nunca colado em link. (3) **quebra de linha**: blocos curtos separados por linha em branco (saudação, contexto, pergunta/oferta, link sozinho na linha, rodapé), nunca parágrafo corrido (`_p`; `_sem_emoji` preserva as quebras). Vale para todo registro ainda não tratado, porque o texto é gerado na hora a partir do código. A regra completa está no `CLAUDE.md` do projeto e no cabeçalho de `common/mensagens_sac.py`.

**Regras de texto (decisão do Hugo, 28/09/2026 — valem para toda mensagem nova):** (1) **sem emoji** — não renderiza quando a mensagem é enviada pelo link; `link_whatsapp` ainda remove qualquer emoji que escape (`_sem_emoji`); (2) **nome da loja sempre completo, "Shibari Brasil"**, nunca só "Shibari". Telefone: só dígitos, com DDI 55. Sem telefone → link vazio e a coluna **Obs.** mostra o e-mail.

## Seção "Carrinhos abandonados — recuperar a venda"

- **Fonte:** `dbt_dw_az.tb_carrinho_abandonado` — `NOT fg_recuperado AND NOT fg_teste AND vl_total_carrinho > 0`, abandonados nos últimos **15 dias** (`JANELA_CARRINHO`, por `ts_criacao`, horário de Brasília). `fg_teste` (macro `eh_contato_teste` no dbt) = mesma regra de teste da rotina de recuperação.
- **Colunas:** Cliente, Valor, Já é cliente?, Abandonado há (horas até 48 h, depois dias), WhatsApp, Já tratei, Resolução, Obs. (larga). Ordem: mais recente primeiro, depois maior valor.
- **Sem coluna de prioridade** (removida em 28/09/2026, pedido do Hugo): a ordem já é do mais recente para o mais antigo.
- **Mensagens** = os dois textos da antiga rotina (cliente recorrente / cliente novo), sem emoji e com "Shibari Brasil", com `ds_url_recuperacao` (link que reabre o carrinho).
- **Mesmo telefone em mais de um carrinho:** link só no mais recente; os outros aparecem com "não reenviar".
- Card extra: valor somado dos carrinhos pendentes.
- **Frescor:** extração `nuvemshop_customers` (clientes + carrinhos) passou de 2×/dia para **de hora em hora, 08:25–22:25** (Cloud Scheduler `nuvemshop-customers-2x`, 25/09/2026); o dbt roda a cada hora no minuto 0 → o carrinho aparece aqui em até ~1h35 depois de abandonado.
- **Relação com a rotina agendada** (`recuperacao-carrinho-abandonado`, lista no Notion 10h30 seg–sex): as duas usam o mesmo dado e os mesmos textos; o check desta página **não** conversa com o `controle-notificados.json` da rotina.

## Seção "Pedidos cancelados — entender e recuperar"

- **Fonte:** `dbt_dw_az.tb_pedido_cancelado` (1 linha por pedido Nuvemshop cancelado), `NOT fg_teste`, cancelados nos últimos **30 dias** (`JANELA_CANCELADO`).
- **Tipo** (`ds_tipo_cancelamento`, do `cancel_reason` da Nuvemshop): *Pagamento não concluído (automático)* / *Pagamento expirado* — o sistema cancelou; *Cliente desistiu*, *Sem estoque*, *Outro motivo*, *Reembolso* — **alguém da loja cancelou** e escolheu o motivo (o cliente não cancela sozinho pela loja virtual; "cliente desistiu" é o que a loja registrou). `ds_origem_cancelamento` agrupa em automatico / loja / sem_registro.
- **"· pago, sem estorno"** (`fg_estorno_a_conferir`): cancelado com o pagamento ainda "paid" na Nuvemshop — possível estorno não feito. Vai para o topo, com "Conferir estorno antes de chamar" na coluna Obs.
- **Sai da lista:** quem voltou a comprar (`fg_recomprou`) — exceto estorno a conferir; e suspeita de fraude (não se contata).
- **Colunas:** Pedido (#número do pedido de venda na Nuvemshop), Cliente, Valor, Já é cliente?, Cancelado em, Tipo, WhatsApp, Já tratei, Resolução, Obs. (larga). A antiga coluna "O que fazer" saiu (28/09/2026); o alerta crítico dela — "Conferir estorno antes de chamar" — foi para **Obs.**, junto do aviso de sem telefone.
- **Mensagem por tipo:** automático → pergunta se teve dificuldade com o Pix/boleto/cartão e oferece refazer; cliente desistiu → pergunta o motivo; sem estoque → desculpas + alternativa; estorno → confirma se o dinheiro voltou; outros → pergunta genérica sobre pendência.

## Extensão futura

Para uma nova lista de tarefas do SAC: escolher um `tipo_tarefa` novo, escrever a função que monta o `DataFrame` de itens (com uma coluna `chave` estável) e chamar `_secao_checklist(...)` de novo com esses parâmetros — o resto (persistência, contadores, filtro de pendentes) é reaproveitado.

## Atualização dos dados (28/09/2026)
Cabeçalho mostra **"Atualizado em"** (tabela mais antiga entre `tb_logistica_pedido`, `tb_carrinho_abandonado`, `tb_pedido_cancelado`) e um expansor com a última extração de pedidos (Bling e Nuvemshop), rastreio e clientes/carrinhos. Todas rodam de hora em hora entre :20 e :45 (rastreio passou de 3×/dia para de hora em hora) e o dbt na hora cheia, 7h–23h. Código em `common/frescor.py`.

## Observação SAC e novas resoluções (28/09/2026, pedido do Hugo)
- **Observação SAC** (coluna editável, texto livre até 500 caracteres) em todas as listas: grava em `raw_control.sac_tarefas.ds_observacao` junto com o check e a Resolução, no mesmo botão "Salvar alterações". Mostra sempre a observação atual; ao salvar um texto novo, ele **sobrescreve** o anterior (o anterior continua no histórico). Não confundir com a coluna **Obs.**, que é aviso automático do sistema (sem telefone, estorno a conferir, telefone repetido).
- `raw_control.sac_tarefas_historico` ganhou a coluna `ds_observacao` (ALTER TABLE manual, 28/09/2026).
- Novas opções de Resolução em **todas** as listas: "WhatsApp inválido" e "Não retomar contato". No carrinho, "Telefone inválido / sem WhatsApp" continua na lista (há valores gravados com ele); as duas fazem o mesmo papel.

## Recontato com cupom — última tentativa (28/09/2026, pedido do Hugo)
Lista nova (tipo de tarefa `recontato_cupom`), paralela a carrinhos e cancelados. Regras:
- **Quem entra:** item de `carrinho_abandonado` ou `pedido_cancelado` cujo 1º contato foi marcado "Já tratei" há **7 dias ou mais** (`DIAS_RECONTATO`; data = 1ª vez que o check foi marcado, em `sac_tarefas_historico`; se o item é anterior ao histórico, a última atualização). Sem teto de idade por decisão (o item fica até ser tratado ou o cliente comprar).
- **Não pode ter comprado:** carrinho `fg_recuperado` / pedido `fg_recomprou` (dbt) — some sozinho quando o cliente compra.
- **Resolução do 1º contato** não pode estar em `msg.RESOLUCOES_SEM_RECONTATO`: "Não retomar contato", "WhatsApp inválido", "Telefone inválido / sem WhatsApp", "Comprou", "Refez o pedido", "Estorno confirmado", "Estorno pendente — resolver". Resolução vazia não impede. Fora também: teste e cancelamento por fraude.
- **1 linha por cliente** (telefone, senão e-mail): vale o contato mais recente.
- **Cupom `SEGUNDACHANCE`** (código único `SEGUNDACHANCE`+4 caracteres; 20%, uso único, 48 horas): criado pelo botão **Gerar cupom** (Cloud Function `nuvemshop-criar-cupom`, preset `cupom_recuperacao_venda_whatsapp`) — a validade de 48h começa no clique, por isso a mensagem só sai com cupom já gerado. 1 cupom por item (referência `<tipo_tarefa>:<chave>`; repetir o clique devolve o mesmo cupom). Estado em `raw_control.cupons_gerados`. Cupom vencido: sem link, aviso em Obs. (a última tentativa já foi usada). **Negativa antes de gerar (Hugo, 07/10/2026):** se o Robson registrar na Resolução da própria repescagem "Sem interesse", "WhatsApp inválido" ou "Não retomar contato" (`msg.RESOLUCOES_SEM_CUPOM`) e salvar, o cliente sai da lista "Gerar cupom" na próxima execução (a Resolução fica gravada com a chave = referência do recontato). "Vai pensar" e "Sem resposta" não cancelam a geração. A Resolução do 1º contato (carrinho/cancelado) segue como antes: "Sem interesse" e "Desistiu" continuam entrando na repescagem.
- Mensagem: `msg.msg_recontato_cupom` (sem emoji, "Shibari Brasil", escassez de 48h com data e hora de vencimento). Resoluções do recontato: `RESULTADOS["recontato_cupom"]`.
- A carga (`carregar_recontato`) vem de `raw_control` + `az` (mesma região); falha nela não derruba o resto da página.
- Cliente que compra com o cupom: cruzar `cupons_gerados.codigo` com `orders.coupon_code` (medição de conversão — ainda não construída).

## Grupos visuais e lista Proximidade (28/09/2026, pedido do Hugo)
- **Três grupos, cada um com faixa colorida** (propósito em uma linha + total de pendentes do grupo): **Recuperação de venda** (azul: carrinhos, cancelados, recontato com cupom), **Problemas** (âmbar: entregas com problema) e **Proximidade** (verde). Ordem: Recuperação → Problemas → Proximidade.
- **Lista "Pós-entrega — como foi a experiência?"** (tipo de tarefa `proximidade_pos_entrega`): contato amistoso, **sem venda e sem cupom**, com a recompra entregue. Fonte: `tb_pedido_recompra_entregue` (dbt: pedido válido, `nr_pedido_cliente` >= 2, entrega confirmada com data, sem reembolso/cancelamento; entrega presumida fica de fora).
- **O registro não some por tempo** (decisão do Hugo, 28/09/2026): nasce no dia seguinte à entrega ("Na lista desde" = D+1) e fica até o Robson tratar; fim de semana, folga ou imprevisto não fazem o contato desaparecer. Ao marcar "Já tratei" grava-se "Atendido em" (1ª vez que o check foi marcado, em `sac_tarefas_historico`) e o registro continua consultável em "Mostrar também os já tratados". Mais antigo primeiro. Só entram entregas a partir de `PROXIMIDADE_DESDE` (29/08/2026 = 30 dias antes do lançamento, para já contatar o que ficou para trás).
- **Uma vez por cliente:** fora quem já teve contato de proximidade tratado em OUTRO pedido (exceto "WhatsApp inválido", que não chegou ao cliente) e quem tem **"Não retomar contato" em qualquer lista** do SAC (cruzado por e-mail: carrinho, cancelado, entrega, recontato e a própria proximidade em outro pedido). Cliente com 2 pedidos na janela entra uma vez (o mais recente).
- **Mensagem** (`msg.msg_proximidade`): pergunta como foi a experiência, se o produto cumpriu o esperado, se há feedback; cita pedido e produto principal; "chegou ontem" (1 dia), "chegou há N dias" (2 e 3) e "chegou faz alguns dias" (acima disso, registro que ficou aguardando); variante que **reconhece o atraso e pede desculpa** quando `fg_entrega_atrasada`; agradece a 2ª compra ("voltar a comprar") ou as seguintes ("continuar comprando"). Sem emoji, "Shibari Brasil".
- **Resoluções:** "Respondeu — satisfeito", "Respondeu — com feedback", "Reclamação — abrir tratativa", "Sem resposta", "WhatsApp inválido", "Não retomar contato". O texto do feedback vai na **Observação SAC**.

## Recompra — último contato dos "Perdido" (piloto, 07/10/2026, pedido do Hugo)
Lista nova de **WhatsApp**, parte do Ecossistema de Pós-Venda (`sb_admin_team/planejamento/Ecossistema de Pós-Venda.md` e `Plano de Ação — Ecossistema de Pós-Venda.md`). É o **piloto do WhatsApp**, e sai dos clientes "Perdido" (última compra há mais de 365 dias) de propósito: é a base de menor valor futuro, então um erro de texto, de link ou de cupom custa o mínimo e protege a base "Dormente". Roda **sem esperar o motor**: a lista é montada à mão e liberada em ondas.

**Esta lista é uma exceção à regra "regra de negócio mora no dbt"**, por ser operacional e temporária: a seleção é uma decisão do sócio e do analista, gravada em tabela de controle; o Streamlit só mostra o que foi liberado. Quando o motor (`tb_cliente_ciclo`) existir, a seleção passa a ser calculada lá e esta tabela vira a fila do dia.

- **Fonte:** `raw_control.posvenda_lista` (não gerenciada pelo dbt, mesma natureza de `sac_tarefas` e `cupons_gerados`). Uma linha por (`campanha`, cliente). Colunas: `campanha` (`perdido_ultimo_contato`), `chave` (id do cliente na Nuvemshop, texto; é a chave do checklist e a referência do cupom), `id_nuvemshop`, `email`, `nm_cliente`, `nr_telefone`, `dt_ultima_compra`, `vl_total_gasto`, `qt_pedidos` (do DW; nulo para quem comprou só antes de nov/2023), `ordem`, `onda`, `fg_liberado`, `dt_liberacao` (quando o cliente foi liberado), `ds_consentimento` (`pendente`, `true` ou `false`), `dt_verificacao_consentimento`, `ds_origem`, `criado_em`.
- **Quem entra na tabela (carga de 07/10/2026):** contatos **ativos** no Perfit (fora descadastrado, rebote e spam), cuja **última compra na Nuvemshop** (data que o Perfit traz da Nuvemshop, que cobre antes do DW) é de mais de 365 dias, com telefone de tamanho válido e sem "Não retomar contato" no SAC. Total na carga: 1.030.
- **Estados de `ds_consentimento` (07/10/2026):** `true` = aceite de marketing na Nuvemshop (`raw_nuvemshop.customers.accepts_marketing`, que o extrator horário traz desde 07/10/2026) **com data individual** (no cadastro ou em outra data própria); `em_massa` = aceite marcado, mas com a data do **lote de 30/06/2026** (1.388 clientes na loja inteira, todos "aceita") ou de 01 a 02/10/2026: **não é opt-in individual verificável**, então não entra até o Hugo explicar o que foi esse evento (a decisão é reversível com um UPDATE); `false` = não aceita; `pausado` = consentimento individual conferido, segurado até o pedido de teste (cupom + Pix + link) dar certo (para retomar: `UPDATE ... SET ds_consentimento = 'true' WHERE ds_consentimento = 'pausado'`); `pendente` = ainda não conferido. Na carga de 07/10/2026, dos 1.030 "Perdido": 248 individuais (`true` ou `pausado`), 781 `em_massa`, 1 `false`.
- **Quem aparece na tela:** só `fg_liberado` **e** `ds_consentimento = 'true'`. A liberação é automática, em **lote diário** (abaixo); nunca entra quem não tem consentimento conferido.
- **Lote diário (decisão do Hugo, 07/10/2026):** a lista mantém **20 clientes sem contato registrado** (`LOTE_DIARIO_RECOMPRA`). Na 1ª abertura da página em cada dia, `completar_lote_diario_recompra` libera os próximos da fila (ordem por `ordem`, só `ds_consentimento = 'true'`) para completar 20: se sobraram 7 sem o check "Já tratei", entram 13. "Sem contato registrado" = liberado, visível (sem "Não retomar contato" em outra lista, sem compra depois da criação da lista nem com o cupom) e sem o check em `sac_tarefas`. Roda **no máximo uma vez por dia** (`dt_liberacao`), então tratar tudo de manhã não puxa mais gente na mesma tarde; sem cliente consentido na fila, não libera nada. Linhas de teste (`chave` começando com `teste`) ficam fora da conta e da liberação. Cada liberação grava `dt_liberacao` e incrementa `onda` (número do lote). **Para pausar o piloto:** `UPDATE raw_control.posvenda_lista SET ds_consentimento = 'pausado' WHERE campanha = 'perdido_ultimo_contato' AND NOT fg_liberado` (a fila deixa de ser consumida; os já liberados continuam). **Consentimento:** o campo `tn_accepts_marketing` do Perfit está **defasado** (12 de 17 clientes conferidos divergem da Nuvemshop), então o consentimento vale só depois de conferido na Nuvemshop (`accepts_marketing` do cliente) e gravado com data. Onda 1: 20 clientes, todos conferidos em 07/10/2026.
- **Some da tela:** cliente com "Não retomar contato" em **qualquer** lista do SAC (cruzado por e-mail; inclui "SAIR" respondido pelo cliente, que o atendente registra como "Não retomar contato"); cliente que **comprou com o cupom**; cliente que **comprou depois** da criação da lista (`tb_cliente.dt_ult_pedido >= data de criação`). O item tratado continua visível em "Mostrar também os já tratados".
- **Cupom (`RETORNOPERDIDO` + 4 caracteres):** preset `cupom_retorno_perdido` na Cloud Function `nuvemshop-criar-cupom`: **R$ 20 fixo, uso único, validade de 21 dias, **combina com outros descontos** (decisão do Hugo, 07/10/2026: o desconto de 3% do Pix só vale junto se o cupom combinar), valor mínimo de compra de R$ 120** (confirmado pelo Hugo em 07/10/2026; o mesmo valor está em `MIN_COMPRA_RETORNO`, em `common/cupom.py`, porque entra no texto). Criado pelo botão **Gerar cupons pendentes**; a validade começa no clique, então gere na hora de enviar. 1 cupom por cliente (referência = `chave`; repetir o clique devolve o mesmo cupom). Estado em `raw_control.cupons_gerados`. **A function precisa de redeploy** para conhecer o preset novo.
- **Link:** `https://shibaribrasil.com.br/discount/<código>` aplica o cupom ao entrar na loja (documentação da Nuvemshop). **Ainda não testado nesta loja**: a primeira mensagem do piloto é o teste (conferir com carrinho vazio e com item, valor mínimo e uso único).
- **Mensagem:** `msg.msg_recompra_ultimo_contato`: pergunta humana sobre a prática, crédito de R$ 20 pessoal "para curtir nossas novidades" com validade e valor mínimo (R$ 120, confirmado pelo Hugo em 07/10/2026), link que aplica o cupom, lembrete do **sticker exclusivo** e de **3% de desconto pagando no Pix**, e saída por "SAIR". Partes importantes em *negrito* do WhatsApp (asteriscos colados, sem espaço por dentro; pedido do Hugo em 07/10/2026): o crédito de R$ 20, a validade, o mínimo de compra, o sticker exclusivo, os 3% no Pix e a palavra SAIR. Sem emoji, "Shibari Brasil", sem urgência artificial (a validade é a real). **Texto aprovado pelo Hugo em 07/10/2026.** **Pendência antes da onda 1:** o cupom é criado com `combina_com_outros = True` (decisão do Hugo, 07/10/2026), porque o único pedido histórico com um cupom que não combina (SEGUNDACHANCE) pagou no Pix sem o desconto de pagamento. Conferir num pedido de teste (cupom + Pix) que o desconto do Pix aparece.
- **Resoluções** (`RESULTADOS["recompra_piloto"]`): "Comprou com o cupom", "Respondeu — quer ver produtos", "Respondeu — sem interesse", "Sem resposta", "WhatsApp inválido", "Não retomar contato". Quem responder SAIR vira "Não retomar contato".
- **Medição:** cupom usado = `tb_pedido.ds_codigo_cupom_nuvemshop` igual ao código gerado (a lista mostra "Comprou com o cupom" em Obs.). Comparação com a taxa histórica de recompra do mesmo recorte; ~10% de cada onda pode ficar sem contato como comparação (decisão do Hugo).
- **Gatilho de parada:** mais de 3% de respostas "SAIR" em uma onda, bloqueios percebidos ou aviso do WhatsApp: o Robson para e o Hugo revisa o texto antes da onda seguinte.
- **Quem faz o quê:** a seleção e a liberação são do sócio e do analista; o envio é do Robson, na página SAC; nada sai sozinho.

### Carga da tabela (SQL usado em 07/10/2026)
A exportação do Perfit ("Contatos da Nuvemshop") foi carregada em `raw_control.perfit_export_20261007` (colunas mínimas: e-mail, nome, estado, qualidade, último envio, última atividade, estágio, aceite do Perfit, total gasto e última compra; **sem CPF**, sobrenome, gênero, aniversário nem interesses). A tabela da lista foi criada por:

```sql
-- Lista operacional do pós-venda (piloto "último contato" dos Perdido). Criada em 07/10/2026 a partir da exportação do Perfit.
-- Perdido = última compra na Nuvemshop há mais de 365 dias (data que o Perfit traz da Nuvemshop; o DW só começa em nov/2023).
-- Entram só contatos ATIVOS no Perfit (fora descadastrado, rebote e spam), com telefone de tamanho válido e sem "Não retomar contato" no SAC.
-- Consentimento nasce 'pendente': é conferido na Nuvemshop (accepts_marketing) antes de liberar cada cliente.
CREATE OR REPLACE TABLE `igneous-sandbox-381622.raw_control.posvenda_lista` AS
WITH p AS (
  SELECT LOWER(TRIM(email)) AS email, estado, SAFE.PARSE_DATE('%Y-%m-%d', ultima_compra) AS dt_ultima_compra,
         SAFE_CAST(total_gastado AS FLOAT64) AS vl_total_gasto
    FROM `igneous-sandbox-381622.raw_control.perfit_export_20261007`
), n AS (
  SELECT LOWER(TRIM(email)) AS email, ANY_VALUE(id) AS id_nuvemshop, ANY_VALUE(name) AS nm_cliente, ANY_VALUE(phone) AS nr_telefone
    FROM `igneous-sandbox-381622.raw_nuvemshop.customers` GROUP BY 1
), c AS (
  SELECT LOWER(TRIM(ds_email)) AS email, ANY_VALUE(qt_pedido) AS qt_pedidos
    FROM `igneous-sandbox-381622.dbt_dw_az.tb_cliente` GROUP BY 1
), tarefa AS (
  SELECT tipo_tarefa, chave, ds_resultado
    FROM `igneous-sandbox-381622.raw_control.sac_tarefas`
  QUALIFY ROW_NUMBER() OVER (PARTITION BY tipo_tarefa, chave ORDER BY dt_atualizacao DESC) = 1
), nao_retomar AS (
  SELECT LOWER(cr.ds_email_cliente) AS email FROM tarefa t JOIN `igneous-sandbox-381622.dbt_dw_az.tb_carrinho_abandonado` cr
    ON t.tipo_tarefa = 'carrinho_abandonado' AND t.chave = CAST(cr.cd_carrinho AS STRING) WHERE t.ds_resultado = 'Não retomar contato'
  UNION ALL
  SELECT LOWER(pc.ds_email_cliente) FROM tarefa t JOIN `igneous-sandbox-381622.dbt_dw_az.tb_pedido_cancelado` pc
    ON t.tipo_tarefa = 'pedido_cancelado' AND t.chave = CAST(pc.cd_pedido_nuvemshop AS STRING) WHERE t.ds_resultado = 'Não retomar contato'
  UNION ALL
  SELECT LOWER(lp.ds_email_cliente) FROM tarefa t JOIN `igneous-sandbox-381622.dbt_dw_az.tb_logistica_pedido` lp
    ON t.tipo_tarefa = 'entrega_problema' AND t.chave = lp.cd_codigo_interno WHERE t.ds_resultado = 'Não retomar contato'
  UNION ALL
  SELECT LOWER(r.ds_email_cliente) FROM tarefa t JOIN `igneous-sandbox-381622.dbt_dw_az.tb_pedido_recompra_entregue` r
    ON t.tipo_tarefa = 'proximidade_pos_entrega' AND t.chave = r.cd_codigo_interno WHERE t.ds_resultado = 'Não retomar contato'
)
SELECT
  'perdido_ultimo_contato' AS campanha,
  CAST(n.id_nuvemshop AS STRING) AS chave,
  n.id_nuvemshop,
  p.email,
  n.nm_cliente,
  n.nr_telefone,
  p.dt_ultima_compra,
  p.vl_total_gasto,
  c.qt_pedidos,
  ROW_NUMBER() OVER (ORDER BY p.dt_ultima_compra DESC, p.vl_total_gasto DESC) AS ordem,
  CAST(NULL AS INT64) AS onda,
  FALSE AS fg_liberado,
  'pendente' AS ds_consentimento,
  CAST(NULL AS TIMESTAMP) AS dt_verificacao_consentimento,
  'perfit_export_20261007' AS ds_origem,
  CURRENT_TIMESTAMP() AS criado_em
FROM p
JOIN n USING (email)
LEFT JOIN c USING (email)
WHERE p.estado = 'ACTIVE'
  AND DATE_DIFF(CURRENT_DATE('America/Sao_Paulo'), p.dt_ultima_compra, DAY) > 365
  AND LENGTH(REGEXP_REPLACE(IFNULL(n.nr_telefone, ''), r'\D', '')) BETWEEN 10 AND 13
  AND p.email NOT IN (SELECT email FROM nao_retomar WHERE email IS NOT NULL);
```

Para liberar uma onda: `UPDATE raw_control.posvenda_lista SET fg_liberado = TRUE WHERE campanha = 'perdido_ultimo_contato' AND onda = 1 AND ds_consentimento = 'true';`
Para conferir o consentimento de novos clientes: consultar a Nuvemshop (`accepts_marketing`) e gravar `ds_consentimento` e `dt_verificacao_consentimento` antes de marcar `onda`.

### Convenção de prefixos de cupom (padrão de 07/10/2026)
O **prefixo identifica a ação** que gerou o cupom, para medir as vendas de cada ação sem cruzar tabelas: no painel da Nuvemshop, em `tb_pedido.ds_codigo_cupom_nuvemshop` (`LIKE 'RETORNOPERDIDO%'`) e em `raw_control.cupons_gerados.campanha`. Regras (também no código da function, com validação no import): MAIÚSCULAS e dígitos, palavra legível da ação, **um prefixo por ação**, e **nenhum prefixo é começo de outro**. Em uso: `SEGUNDACHANCE` (recuperação de venda) e `RETORNOPERDIDO` (crédito de retorno, piloto dos "Perdido"). Reservados: `RETORNOJANELA`, `RETORNODORMENTE`, `RETORNOREATIVACAO`. Código universal fora da function: `EXPLORAR20` (Área VIP).
Medição por ação: view `raw_control.vw_vendas_cupom_acao` (uma linha por cupom gerado, com o pedido, a data, o valor, o desconto de cupom, o desconto de pagamento e a margem de contribuição quando houve compra).

## Supressão "Não retomar contato" (07/10/2026)

O bloco `nao_retomar` das consultas de Proximidade, Recompra (piloto) e do lote diário lê `raw_control.vw_tarefa_pessoa` (sb_data_pipeline, `sql/raw_control/views_contato_cliente.sql`) em vez de repetir 8 JOINs. A mesma view alimenta o motor de pós-venda (`vw_supressao_contato`). Lista nova do SAC = 1 ramo novo na view. Comportamento idêntico ao anterior.

