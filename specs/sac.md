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
- **Cupom `SEGUNDACHANCE`** (código único `SEGUNDACHANCE`+4 caracteres; 20%, uso único, 48 horas): criado pelo botão **Gerar cupom** (Cloud Function `nuvemshop-criar-cupom`, preset `cupom_recuperacao_venda_whatsapp`) — a validade de 48h começa no clique, por isso a mensagem só sai com cupom já gerado. 1 cupom por item (referência `<tipo_tarefa>:<chave>`; repetir o clique devolve o mesmo cupom). Estado em `raw_control.cupons_gerados`. Cupom vencido: sem link, aviso em Obs. (a última tentativa já foi usada).
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
