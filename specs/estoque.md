# Spec — Estoque & Reposição (v2, 29/09/2026 — escopo aprovado pelo Hugo)

> **Status:** construída em `reports/estoque.py` (menu Semanal, `url_path=estoque`). A v1 (abaixo, sobre `rpt_estoque_produtos`) é histórico e não é mais usada. R7 do plano de KPIs.

Pergunta: **"vou perder venda por ruptura, quanto preciso comprar agora e quanto capital está parado?"**

## Escopo da primeira leva (decisão do Hugo, 29/09)
- **Só produto físico comercial** (revenda e produção própria). **Suprimento e matéria-prima ficam para a segunda leva** (regra: excluir `ds_categoria = '[Interno] Insumos'` — 48 produtos, ~R$ 710 a custo da última compra — e `[Interno] Inativos`; `tb_suprimento_analitico` está vazia hoje). Isso também tira o Óleo de Semente de Uva (5.000 un × custo cadastrado R$ 75,89 = R$ 379 mil, custo evidentemente errado) dos totais.
- Fonte: **`dbt_dw_az`** — não usar mais `rpt_estoque_*`. Regra de negócio fica no dbt; a página filtra, soma e apresenta.

## Fontes (todas us-east4, uma região só)
| Uso | Tabela |
|---|---|
| Risco, cobertura, compra pendente, ABC, ciclo | `tb_estoque_analitico` (1 linha por produto físico) |
| Histórico de posição e ruptura | `tb_estoque_posicao_dia` (desde 02/07/2026), `tb_estoque_ruptura` (dt_inicio, dt_fim, qt_dias_sem_estoque) |
| Giro, cobertura, GMROI, parado por papel × origem | `tb_giro_papel_mes` |
| Orçamento de mercadoria | `tb_orcamento_mercadoria_mes` (orçado, consumido, disponível) |
| Lista de compra | `tb_sugestao_reposicao` + regras da rotina `sugestao-compras-reposicao` (3 blocos) |
| Compras em aberto | `tb_compra` / `tb_agg_compra_produto` |

## Blocos (ordem do maior problema ao menor)
1. **Comprar agora:** produtos de revenda com risco 🆘 Rompido / 🛑 Urgente e venda em 60 dias > 0, ou ✅ Estável abaixo do mínimo com venda; qtd sugerida, custo, fornecedor, **custo acumulado contra o orçamento disponível do mês** (corte pelo caixa é decisão humana). Produtos do ciclo "Descontinuado/Em Saída" não entram em compra.
2. **Produção própria abaixo do mínimo** (agrupada por família), separado da revenda.
3. **Cobertura × lead time por papel** (Core / Complementar / Impulso): dias de cobertura atual e total (com compra pendente) contra o lead time do produto.
4. **Capital em estoque:** valor a custo por papel × origem, giro anualizado, GMROI e estoque parado (`tb_giro_papel_mes`), com aviso de mês parcial.
5. **Rupturas:** produtos com mais dias sem estoque no período e classe ABC deles (`tb_estoque_ruptura`).
6. **Qualidade do cadastro:** sem estoque mínimo, sem lead time, sem origem, "Sem Classificação", sem custo — cada um com contagem e lista, porque o risco depende desses campos.
7. **Girando devagar** (Encalhado, Sobreestoque, Sem Histórico): alerta para descontinuar/baixar mínimo/ação de marketing, **não** é recomendação de compra. Sobreposição com Gestão de Produtos (baixo giro): aqui só o valor de capital; a ação de vitrine fica lá.

## Regras
- Classificação de risco, cobertura, lead time e ABC: **as definidas em `tb_estoque_analitico`** (ver v1 abaixo); custo base = `vl_custo_cadastro` com fallback `vl_custo_ultima_compra`; não recalcular no Streamlit.
- Razões soma ÷ soma; mês corrente parcial rotulado.

## Como ficou (implementação)
- Sem dbt novo: tudo lido de tabelas `az` existentes. Bloco 1 usa a `tb_sugestao_reposicao` como está (quantidade, caixa fechada, pedido mínimo, ciclo de vida e orçamento já vêm de lá); "Cabe no orçamento" = custo acumulado na ordem de prioridade ≤ `vl_disponivel` do mês.
- Exceção assumida à regra "regra só no dbt": o rótulo "Por que não está na lista" (produtos em risco fora da sugestão) é derivado no pandas de campos já existentes (fabricado, ciclo, custo) — só explica a ausência, não decide compra.
- Custo do estoque hoje = `vl_custo_cadastro` com fallback `vl_custo_ultima_compra` (base da v1). Giro/GMROI vêm da `tb_giro_papel_mes` (custo vigente por dia); "Sem papel" fica fora do bloco 4 (insumos/uso e consumo, 2ª leva).
- Rupturas: dias inclusivos na janela; só produtos com venda em 60 dias ou curva A/B; sem estimativa de venda perdida.
- Verificado em 29/09 contra o BigQuery: 11 rompidos, 12 urgentes, estoque a custo R$ 7.838,31 (R$ 4.332,81 em sobreestoque/encalhado), lista de compra 14 produtos = R$ 1.003,79 vs. orçamento disponível R$ 583,28, 30 produtos com ruptura nos últimos 30 dias.

## Pontos abertos (dbt / dados)
- Lacuna "Sem Classificação" (cobertura 30–45 com estoque > mínimo) — decidir regra no dbt.
- 96 de 290 produtos sem estoque mínimo e 17 sem origem/lead time: correção manual no Bling (Melhorias Manuais).
- Vendas perdidas por ruptura (estimativa) exige definição de venda média diária antes da ruptura — propor antes de implementar.
- Segunda leva: suprimento/matéria-prima (`tb_suprimento_analitico` vazia; conferir custo e unidade do Óleo de Semente de Uva no Bling).

---

# Spec v1 — Relatório de Estoque (histórico)

Documenta as regras de negócio e a definição de cada indicador do relatório de Estoque. **Leia antes de alterar qualquer cálculo, filtro ou seção deste relatório.** Se a mudança alterar uma regra de negócio, atualize este arquivo no mesmo commit — não deixe a regra só implícita no SQL ou no app (ver `CLAUDE.md` e o histórico do bug do ROI em `specs/google-ads.md`, motivo dessa regra existir).

## Escopo (v1, 2026-07-02)

Primeira fase do relatório: uma área de **"Problemas"** (quantidade de produtos nas 2 classificações de risco mais urgentes) e uma área de **"Indicadores Gerais"** (custo total do estoque, produtos com compra pendente, valor total em risco). Sem filtro de mês — é o estado atual do estoque (uma foto), não uma série temporal como o relatório de Vendas.

**Fora de escopo desta v1, para uma próxima rodada** (confirmado com o usuário): grid com todas as classificações de risco (não só Rompido/Urgente); tratamento de Suprimento (deliberadamente excluído por enquanto, não "ainda não implementado").

## Fonte de dados

`rpt_estoque_produtos`, dataset **`dbt_dw_rpt`** (região `us-east4`, produzida pelo projeto `sb_dw_dbt`, `models/4.rpt/Estoque/rpt_estoque_produtos.sql`), grão **1 linha por produto**, sem janela de tempo (é um snapshot do estado atual, recalculado a cada `dbt run`, não um histórico).

Lê de `{{ ref('tb_estoque_analitico') }}` (camada `az`, projeto `sb_dw_dbt`) — **tabela já existente**, que por sua vez já implementa a classificação de risco e as fórmulas de cobertura descritas abaixo. `rpt_estoque_produtos` não recalcula nada dessa lógica — só seleciona as colunas relevantes e aplica o filtro de categoria (ver abaixo). A lógica de negócio central deste relatório mora em `tb_estoque_analitico.sql`, não no Streamlit nem na camada `rpt`.

### Regra de negócio: exclusão de categorias internas

**`rpt_estoque_produtos` exclui `ds_categoria in ('[Interno] Suprimentos', '[Interno] Inativos')`.**

- `[Interno] Suprimentos` (44 produtos) — pedido explícito do usuário: "não vamos trabalhar com suprimento no momento". Existe uma tabela separada pra isso, `tb_suprimento_analitico` (mesmo projeto), não usada por este relatório.
- `[Interno] Inativos` (2 produtos) — encontrado durante a investigação e confirmado com o usuário: produto inativo não deveria gerar alerta de estoque, mesmo raciocínio de excluir Suprimentos.

Todos os indicadores deste relatório herdam essa exclusão (é aplicada uma vez, na fonte).

## Regra de negócio: Classificação de Risco

Calculada em `tb_estoque_analitico.sql` (não no Streamlit), coluna `ds_classificacao_risco`. As letras no início de cada rótulo (`a.`, `b.`, `c.`...) existem **de propósito** — ordenar por essa string ordena automaticamente pela severidade, sem precisar de uma coluna de ordenação separada.

```sql
case
    when qt_pecas_sessenta_dias = 0 and qt_estoque_atual = 0
        then 'g. ⚫ Sem Histórico'
    when qt_pecas_sessenta_dias = 0 and qt_estoque_atual > 0
        then 'f. 💤 Encalhado'
    when qt_pecas_sessenta_dias > 0 and qt_estoque_atual = 0
        then 'a. 🆘 Estoque Rompido'
    when qt_estoque_atual > 0 and qt_cobertura_total < 30
        then 'b. 🛑 Urgente'
    when qt_estoque_atual > 0 and qt_estoque_atual <= qt_estoque_minimo and qt_cobertura_total < 45
        then 'c. ⚠️ Atenção'
    when qt_estoque_atual > 0 and qt_cobertura_total >= 45 and qt_cobertura_total <= 60
        then 'd. ✅ Estável'
    when qt_estoque_atual > 0 and qt_cobertura_total > 60
        then 'e. 💠 Sobreestoque'
    else 'h. Sem Classificação'
end
```

**Nota sobre a ordem das condições:** repare que "Urgente" (`cobertura_total < 30`) é checada **antes** de "Atenção". Isso é proposital — como `case when` para na primeira condição verdadeira, ao chegar em "Atenção" já se sabe que `cobertura_total >= 30` (senão teria caído em "Urgente"), então a condição de "Atenção" não precisa repetir esse limite inferior. Funcionalmente equivalente a exigir `cobertura_total >= 30 and < 45` ali. **Não reordene essas duas condições** sem entender essa dependência.

**Lacuna conhecida (existe desde antes deste relatório, não é um bug introduzido aqui):** um produto com `qt_estoque_atual > qt_estoque_minimo` **e** `cobertura_total` entre 30 e 45 não se encaixa em nenhuma condição além do `else` — não é "Atenção" (exige estoque ≤ mínimo), não é "Estável" (exige cobertura ≥ 45), não é "Urgente" (exige cobertura < 30). Cai em "h. Sem Classificação". Na distribuição real de hoje isso não aparece (0 produtos em "h."), mas pode aparecer no futuro — documentado aqui pra não ser confundido com um bug do relatório quando acontecer.

### Fórmulas de apoio (calculadas em `tb_estoque_analitico.sql`)

| Campo | Fórmula |
|---|---|
| Cobertura (dias) | `qt_estoque_atual ÷ (qt_pecas_sessenta_dias ÷ 60)` — `qt_cobertura_atual` |
| Compra Pendente por Dia | `qt_item_compra_pendente ÷ (qt_pecas_sessenta_dias ÷ 60)`, 0 se não houver compra pendente — `qt_compra_pendente_dia` |
| Cobertura Total (dias) | `Cobertura + Compra Pendente por Dia − Lead Time` — `qt_cobertura_total` |
| Lead Time (dias) | **campo cadastrado por produto** (`qt_lead_time`), não recalculado — ver decisão abaixo |

### Regra de negócio: Lead Time usa o campo cadastrado, não uma fórmula por origem

O usuário descreveu inicialmente o Lead Time como uma regra fixa por origem do produto (`Revenda Nacional`/`Produção Própria` = 15 dias, `Revenda Importada` = 25 dias, qualquer outra = 25 dias). Ao comparar com o dado real, essa regra bate para 3 das 4 origens cadastradas — mas `Uso e Consumo` (15 produtos) tem Lead Time real = 15 dias, não 25 como a regra "senão" sugeriria.

**Decisão (confirmada com o usuário): usar `qt_lead_time` direto do cadastro do produto**, não recalcular pela fórmula de origem. Motivo: o campo cadastrado já reflete a realidade de todas as origens — inclusive origens futuras que a fórmula fixa não preveria corretamente. Se o cadastro de Lead Time por produto ficar desatualizado, é um problema de dado a corrigir na fonte (Bling), não uma regra a reimplementar aqui.

## Seção "Problemas"

**v1: só as 2 classificações mais urgentes.** Tabela (grid) "Quantidade de Produtos por Classificação de Risco", filtrada em `ds_classificacao_risco in ('a. 🆘 Estoque Rompido', 'b. 🛑 Urgente')`, ordenada pela própria label (ordem alfabética = ordem de severidade, graças ao prefixo de letra). Colunas: Classificação de Risco, Quantidade de Produtos.

**Decisão de escopo (confirmada com o usuário):** o grid com **todas** as classificações fica para uma próxima rodada — não é uma limitação técnica, é sequenciamento deliberado ("essa área de problemas vamos ter só isso inicialmente, depois vamos preencher com mais coisas"). A função que monta a tabela já está desenhada pra aceitar remover esse filtro depois sem mudar a estrutura.

Distribuição completa de referência (2026-07-02, 231 produtos relevantes, já sem Suprimento/Inativos): a. Estoque Rompido = 25 · b. Urgente = 3 · c. Atenção = 0 · d. Estável = 4 · e. Sobreestoque = 10 · f. Encalhado = 47 · g. Sem Histórico = 142 · h. Sem Classificação = 0.

## Seção "Indicadores Gerais"

| Indicador | Fórmula |
|---|---|
| Custo Total dos Produtos de Estoque | soma de `vl_custo_cadastro × qt_estoque_atual`, todos os produtos relevantes |
| Produtos com Estoque Pendente | contagem de produtos com `qt_item_compra_pendente > 0` |
| Valor Total em Risco | soma de `vl_custo_cadastro × qt_estoque_atual`, só produtos "a. Estoque Rompido" + "b. Urgente" |

**"Valor Total em Risco" foi sugerido pela IA** (o usuário pediu uma sugestão de indicador) — complementa a contagem de produtos em risco da seção "Problemas" com a magnitude em R$ envolvida. Referência: hoje equivale a R$ 263,96, contra R$ 5.205,86 de custo total do estoque (ou seja, ~5% do valor do estoque está nas duas classificações mais urgentes).

**Regra de negócio: base de custo = `vl_custo_cadastro`, não `vl_custo_ultima_compra`.** Escolhida por completude de dado: 51 dos 231 produtos relevantes não têm `vl_custo_cadastro` preenchido, contra 130 sem `vl_custo_ultima_compra` — `vl_custo_cadastro` cobre mais produtos. Mesma preferência (cadastro como base, com fallback quando necessário) já usada no relatório de Vendas (`tb_pedido.sql`, projeto `sb_dw_dbt`).

## Limitações conhecidas

- Produtos sem `vl_custo_cadastro` (51 de 231) contribuem R$ 0 pros indicadores de custo — não ficam de fora da contagem de produtos, mas não somam valor. Pode subestimar o Custo Total e o Valor em Risco.
- A classificação de risco não filtra por fornecedor nem por canal de venda — é o produto consolidado.
- Ver "Lacuna conhecida" acima (produtos que caem em "h. Sem Classificação" por causa do intervalo não coberto entre as regras de Atenção/Estável/Urgente).
