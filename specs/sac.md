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

Leitura e escrita: `common/tarefas.py` (`carregar_tarefas(tipo)`, `salvar_tarefa(tipo, chave, feito, resultado)` — grava check e resultado juntos via `MERGE` parametrizado e acrescenta 1 linha em `raw_control.sac_tarefas_historico`).

**Histórico — `raw_control.sac_tarefas_historico`** (append-only, fora do dbt, criada em 28/09/2026): `tipo_tarefa`, `chave`, `fg_feito`, `ds_resultado`, `dt_evento` — 1 linha a cada gravação. Serve para ver a evolução de um contato (ex.: "Vai pensar" → "Comprou") e medir conversão do SAC por resultado.

## Ordem da página (decisão do Hugo, 28/09/2026)

1. Carrinhos abandonados · 2. Pedidos cancelados · 3. Entregas com problema. Em todas, o link de WhatsApp aparece com o texto **"Mensagem"** (coluna estreita) e as colunas editáveis são **Já tratei** e **Resolução**, nessa ordem, logo após o WhatsApp. "Obs." é uma coluna larga (texto completo), só em carrinhos e cancelados.

## Seção "Entregas com problema — falar com o cliente"

- **Fonte:** `common/logistica.py` (`tb_logistica_pedido`), o mesmo critério de risco do Pulso do Dia — atrasado (`fg_atrasado_em_aberto`), problema de entrega ativo (`fg_problema_entrega_ativo`) ou parado (em trânsito, sem evento de rastreio há 10+ dias — mesmo limite do Pulso do Dia, `DIAS_PARADO`).
- **Tabela editável** (`st.data_editor`): Pedido (número do pedido de venda na Nuvemshop, `#cd_pedido_loja`), Cliente, Motivo, Rastreio, Dias sem evento, **WhatsApp**, **Já tratei**, **Resolução**. Só Já tratei e Resolução são editáveis.
- **Mensagem por motivo** (prioridade: problema de entrega > atrasado > parado): problema de entrega pede confirmação de endereço/recebedor; atrasado e parado avisam que estamos acompanhando e perguntam se já recebeu. Inclui o código e o link de rastreio quando existem. Telefone: `nr_telefone_cliente` (cadastro Nuvemshop, na `tb_logistica_pedido`).
- **Sem identificação de quem marca** — não pede nome nem usa login do Streamlit Cloud.
- **Toggle "Mostrar também os já tratados"**: por padrão a lista só mostra pendentes, para o SAC ver o que falta, não o que já foi feito.
- Ao marcar/desmarcar, a página grava no BigQuery e recarrega (`st.rerun()`) para mostrar os contadores atualizados.

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
