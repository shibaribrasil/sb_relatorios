# Spec — Giro Semanal (`reports/giro_semanal.py`, camada Semanal)

Relaciona, **na mesma janela de dias**, os três elos do giro da loja — **mídia** (Google Ads: custo, cliques, leilão) → **acesso** (GA4: sessões, engajamento, funil) → **venda** (pedidos, ticket, faturamento, margem) — e escreve a interpretação em cima dos números ("norteadores"): onde olhar primeiro e o que fazer. Não cria regra de negócio: números vêm das mesmas fontes de Vendas da Semana (`tb_pedido`), Google Ads (`tb_gads_*`, ver `google-ads.md`) e Tráfego/GA4 (`tb_ga4_canal_diario`, `tb_ga4_funil`). Razão = Σ ÷ Σ.

## Janela
- Semana de segunda a domingo; padrão = **semana em andamento acumulada até o último dia fechado de TODAS as fontes** (`min(ontem, último dia do Ads, último dia do GA4)`). Hoje nunca entra.
- Três referências na mesma janela de dias: **semana atual**, **semana anterior** (mesmos dias) e o **normal** = média das mesmas janelas nas 4 semanas anteriores (só janelas com Ads e GA4; histórico do GA4 desde 01/07/2026, então o normal das primeiras semanas tem menos de 4 janelas). Razões do normal = razão das médias (Σ ÷ Σ).
- Aviso quando o normal atravessa a reestruturação do Ads de 31/08/2026: para investimento, cliques e CPC usar a semana anterior como referência principal (até ~meados de outubro).

## Estrutura da tela
1. **Leitura da semana** — a cadeia em uma linha (investimento → cliques → sessões → pedidos → faturamento → margem, antes e após mídia) e o **ponto de atenção principal** (primeiro achado urgente, depois de atenção, na ordem da cadeia) com a primeira ação sugerida. Sem gargalo: diz que não há.
2. **Cards**: faturamento, pedidos, sessões, conversão (pedidos ÷ sessões), investimento, margem após mídia (Google Ads).
3. **Norteadores** — cartões de diagnóstico (🔴 urgente · 🟡 atenção · 🟢 positivo), cada um com o achado, a explicação e a **ação recomendada**.
4. **O giro elo a elo** — tabela mídia → acesso → comportamento → venda → retorno com semana, anterior, normal, Δs e semáforo da leitura.
5. **Os elos se movem juntos?** — índice por dia (100 = média das semanas mostradas) de investimento, cliques, sessões, pedidos e faturamento; funil do site (janela × normal); sessões por dia por origem (Google pago, Google orgânico, direto, Instagram/Meta, outros, sem origem ainda).
6. **Semana a semana** — 8 semanas com todos os elos.

## Regras dos norteadores (constantes em `giro_semanal.py`; são de apresentação, não de negócio)
Ordem = caminho do giro: **Rastreamento → Investimento → Tráfego → Site → Venda → Retorno → Leilão** (rastreio primeiro porque medição quebrada invalida o resto; leilão por último por ser condição estrutural).

| Achado | Dispara quando | Severidade |
|---|---|---|
| Rastreamento | sessões de Google pago ÷ cliques do Ads fora de 0,7–1,3 (com ≥ 30 cliques); compras GA4 ÷ pedidos fora de 0,6–1,5 (com ≥ 5 pedidos; o GA4 dispara na criação do pedido, por isso passa de 1) | urgente só se razão < 0,5 e a janela não é dos últimos 2 dias (o GA4 reprocessa); senão atenção. Sem problema → "medição consistente" |
| Investimento | custo ≥ 40% acima da referência e ≥ R$ 60; cita a campanha de maior aumento | atenção; positivo se ≥ 3 pedidos reais de Google pago e margem ÷ custo ≥ 1× |
| Tráfego | cliques −25% (atenção) / −40% (urgente) ou +25% (positivo) vs. referência, com ≥ 30 cliques; separa a causa: menos verba (custo caiu, CPC estável) × CPC subiu (concorrência/rank) × outra (demanda) | conforme |
| Site | % de sessões engajadas de Google pago < 50% ou queda ≥ 8 p.p.; conversão (pedidos ÷ sessões) −25%/−40%; passo do funil que mais piorou (carrinho ÷ visto, checkout ÷ carrinho, pedido ÷ checkout) com ≥ 100/15/10 na base | conforme; exige ≥ 5 pedidos e ≥ 200 sessões |
| Venda | ticket ±15% vs. referência; ≥ 65% dos pedidos vindos de Google pago (concentração de canal) | atenção/positivo; exige ≥ 5 pedidos |
| Retorno | 0 pedido real de Google pago com ≥ 4 dias ou ≥ R$ 100 gastos; margem ÷ custo < 0,6 (urgente) / < 1 (atenção) / ≥ 1 (positivo), com ≥ 3 pedidos de Google pago; margem após mídia negativa; Ads reporta > 1,3× o faturamento real; campanha com ≥ 30% do custo e ≥ R$ 50 sem compra nem pedido ligado | conforme (no Shopping/PMax o texto avisa que a atribuição é incerta e que não se deve cortar orçamento só por isso) |
| Leilão | perda por classificação ≥ 50% e ≥ perda por orçamento → "limitado por classificação"; perda por orçamento ≥ 20% → "perdendo por orçamento" | atenção |

**Amostra:** nada de conversão, ticket ou retorno vira alerta com menos de 5 pedidos na janela; tráfego/rastreio pedem ≥ 30 cliques; a tabela mostra "amostra pequena" no lugar do semáforo. Com ~10 pedidos por semana, os norteadores são **hipóteses sobre onde olhar primeiro**, não sentença; confirmar a tendência em 2 semanas seguidas antes de mexer em campanha (PDCA, uma mudança por vez).

## Semáforo da tabela elo a elo
Compara a semana com o normal (ou com a anterior, se não há normal): 🟢 melhor em ≥ 10%, ➖ estável (±10%), 🟡 pior em 10–25%, 🔴 pior em mais de 25%. Indicadores sem "melhor" definido (investimento, sessões ÷ cliques) ficam sem semáforo. Melhor = maior, exceto CPC (menor).

## Definições
- **Sessões de Google pago**: GA4 fonte google / meio cpc. **Sessões do site**: todas as origens. Funil = sessões únicas do GA4 que chegaram em cada etapa (produto visto, carrinho, checkout) do site inteiro; o último degrau usa **pedidos reais** da base. *Pedido ÷ checkout* é aproximação (o checkout pode virar pedido em outro dia).
- **Pedidos de Google pago**: origem google / mídia cpc pela URL de entrada. **ROAS real** = faturamento desses pedidos ÷ custo. **MER** = faturamento total ÷ custo. **Margem após mídia** = margem de contribuição de todos os pedidos − custo do Google Ads (Meta/Instagram pago não tem custo na base).
- **Conversão** = pedidos ÷ sessões do site. **Ticket** = faturamento ÷ pedidos.

## Limitações
- GA4: atribuição dos últimos ~2 dias ainda é reprocessada; sessões "sem origem ainda" nos últimos dias.
- Ads: compras creditadas aos últimos dias sobem nos dias seguintes; em janela de 1–2 dias, retorno e conversão ainda não dizem nada.
- Atribuição por campanha do Shopping é incerta (ver `canais-unit-economics.md`).
- Sem coluna de origem no funil do dbt: o funil é do site todo, não só do Google pago.
