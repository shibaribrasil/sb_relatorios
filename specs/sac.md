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
| `tipo_tarefa` | STRING | Qual lista (hoje só `entrega_problema`). Novas listas usam um novo valor aqui — mesma tabela, sem migração. |
| `chave` | STRING | Identificador estável do item dentro do tipo (hoje: `cd_codigo_interno`, como texto). |
| `fg_feito` | BOOL | Marcado como concluído. |
| `nm_responsavel` | STRING | Existe no schema, mas não é usada — decisão do Hugo (23/set/2026): a página não identifica quem marcou. Fica vazia. |
| `ds_observacao` | STRING | Livre, hoje não usado na tela (campo pronto para o futuro). |
| `dt_criacao` / `dt_atualizacao` | TIMESTAMP | Quando a linha nasceu / foi tocada pela última vez. |

Leitura e escrita: `common/tarefas.py` (`carregar_tarefas(tipo)`, `marcar_tarefa(tipo, chave, feito)` — grava via `MERGE`, parametrizado).

## Seção "Entregas com problema — falar com o cliente"

- **Fonte:** `common/logistica.py` (`tb_logistica_pedido`), o mesmo critério de risco do Pulso do Dia — atrasado (`fg_atrasado_em_aberto`), problema de entrega ativo (`fg_problema_entrega_ativo`) ou parado (em trânsito, sem evento de rastreio há 10+ dias — mesmo limite do Pulso do Dia, `DIAS_PARADO`).
- **Tabela editável** (`st.data_editor`): Pedido, Rastreio, Cliente, Dias sem evento, Motivo e a coluna **Já tratei** (checkbox). Só essa coluna é editável.
- **Sem identificação de quem marca** — não pede nome nem usa login do Streamlit Cloud.
- **Toggle "Mostrar também os já tratados"**: por padrão a lista só mostra pendentes, para o SAC ver o que falta, não o que já foi feito.
- Ao marcar/desmarcar, a página grava no BigQuery e recarrega (`st.rerun()`) para mostrar os contadores atualizados.

## Extensão futura

Para uma nova lista de tarefas do SAC: escolher um `tipo_tarefa` novo, escrever a função que monta o `DataFrame` de itens (com uma coluna `chave` estável) e chamar `_secao_checklist(...)` de novo com esses parâmetros — o resto (persistência, contadores, filtro de pendentes) é reaproveitado.
