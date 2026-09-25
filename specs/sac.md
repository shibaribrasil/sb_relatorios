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
| `ds_observacao` | STRING | Livre, hoje não usado na tela (campo pronto para o futuro). |
| `dt_criacao` / `dt_atualizacao` | TIMESTAMP | Quando a linha nasceu / foi tocada pela última vez. |

Leitura e escrita: `common/tarefas.py` (`carregar_tarefas(tipo)`, `marcar_tarefa(tipo, chave, feito)` — grava via `MERGE`, parametrizado).

## Seção "Entregas com problema — falar com o cliente"

- **Fonte:** `common/logistica.py` (`tb_logistica_pedido`), o mesmo critério de risco do Pulso do Dia — atrasado (`fg_atrasado_em_aberto`), problema de entrega ativo (`fg_problema_entrega_ativo`) ou parado (em trânsito, sem evento de rastreio há 10+ dias — mesmo limite do Pulso do Dia, `DIAS_PARADO`).
- **Tabela editável** (`st.data_editor`): Pedido (número que o cliente vê, `#cd_pedido_loja`), Cliente, Motivo, **WhatsApp**, Rastreio, Dias sem evento e a coluna **Já tratei** (checkbox). Só essa coluna é editável.
- **Mensagem por motivo** (prioridade: problema de entrega > atrasado > parado): problema de entrega pede confirmação de endereço/recebedor; atrasado e parado avisam que estamos acompanhando e perguntam se já recebeu. Inclui o código e o link de rastreio quando existem. Telefone: `nr_telefone_cliente` (cadastro Nuvemshop, na `tb_logistica_pedido`).
- **Sem identificação de quem marca** — não pede nome nem usa login do Streamlit Cloud.
- **Toggle "Mostrar também os já tratados"**: por padrão a lista só mostra pendentes, para o SAC ver o que falta, não o que já foi feito.
- Ao marcar/desmarcar, a página grava no BigQuery e recarrega (`st.rerun()`) para mostrar os contadores atualizados.

## Link de WhatsApp (todas as listas)

Coluna **WhatsApp** (`LinkColumn`, texto "Chamar no WhatsApp") com `https://wa.me/<telefone>?text=<mensagem>` — abre a conversa com a mensagem **já escrita para aquela situação**; o atendente revisa e envia (nada é enviado sozinho). Textos em `common/mensagens_sac.py` (assinatura: "Robson, da Shibari" — constante `ATENDENTE`). Telefone: só dígitos, com DDI 55. Sem telefone → link vazio e a coluna **Obs.** mostra o e-mail.

## Seção "Carrinhos abandonados — recuperar a venda"

- **Fonte:** `dbt_dw_az.tb_carrinho_abandonado` — `NOT fg_recuperado AND NOT fg_teste AND vl_total_carrinho > 0`, abandonados nos últimos **15 dias** (`JANELA_CARRINHO`, por `ts_criacao`, horário de Brasília). `fg_teste` (macro `eh_contato_teste` no dbt) = mesma regra de teste da rotina de recuperação.
- **Colunas:** Prioridade, Cliente, WhatsApp, Obs., Abandonado há (horas até 48 h, depois dias), Valor, Já é cliente?, Já tratei. Ordem: mais recente primeiro, depois maior valor.
- **Prioridade** (mesma régua da rotina): 🔴 abandonado há até 1 dia **ou** valor ≥ R$ 300; senão 🟡.
- **Mensagens** = os dois textos da rotina (cliente recorrente / cliente novo), com `ds_url_recuperacao` (link que reabre o carrinho).
- **Mesmo telefone em mais de um carrinho:** link só no mais recente; os outros aparecem com "não reenviar".
- Card extra: valor somado dos carrinhos pendentes.
- **Frescor:** extração `nuvemshop_customers` (clientes + carrinhos) passou de 2×/dia para **de hora em hora, 08:25–22:25** (Cloud Scheduler `nuvemshop-customers-2x`, 25/09/2026); o dbt roda a cada hora no minuto 0 → o carrinho aparece aqui em até ~1h35 depois de abandonado.
- **Relação com a rotina agendada** (`recuperacao-carrinho-abandonado`, lista no Notion 10h30 seg–sex): as duas usam o mesmo dado e os mesmos textos; o check desta página **não** conversa com o `controle-notificados.json` da rotina.

## Seção "Pedidos cancelados — entender e recuperar"

- **Fonte:** `dbt_dw_az.tb_pedido_cancelado` (1 linha por pedido Nuvemshop cancelado), `NOT fg_teste`, cancelados nos últimos **30 dias** (`JANELA_CANCELADO`).
- **Tipo** (`ds_tipo_cancelamento`, do `cancel_reason` da Nuvemshop): *Pagamento não concluído (automático)* / *Pagamento expirado* — o sistema cancelou; *Cliente desistiu*, *Sem estoque*, *Outro motivo*, *Reembolso* — **alguém da loja cancelou** e escolheu o motivo (o cliente não cancela sozinho pela loja virtual; "cliente desistiu" é o que a loja registrou). `ds_origem_cancelamento` agrupa em automatico / loja / sem_registro.
- **"· pago, sem estorno"** (`fg_estorno_a_conferir`): cancelado com o pagamento ainda "paid" na Nuvemshop — possível estorno não feito. Vai para o topo, com a ação "Conferir estorno antes de chamar".
- **Sai da lista:** quem voltou a comprar (`fg_recomprou`) — exceto estorno a conferir; e suspeita de fraude (não se contata).
- **Colunas:** Pedido (#número), Cliente, O que fazer, WhatsApp, Obs., Tipo, Cancelado em, Valor, Já é cliente?, Já tratei.
- **Mensagem por tipo:** automático → pergunta se teve dificuldade com o Pix/boleto/cartão e oferece refazer; cliente desistiu → pergunta o motivo; sem estoque → desculpas + alternativa; estorno → confirma se o dinheiro voltou; outros → pergunta genérica sobre pendência.

## Extensão futura

Para uma nova lista de tarefas do SAC: escolher um `tipo_tarefa` novo, escrever a função que monta o `DataFrame` de itens (com uma coluna `chave` estável) e chamar `_secao_checklist(...)` de novo com esses parâmetros — o resto (persistência, contadores, filtro de pendentes) é reaproveitado.
