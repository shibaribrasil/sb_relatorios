# Spec — Instagram (camada semanal)

Status: v1 proposta em 07/10/2026 (branch `feat/pagina-instagram`). Fontes no ar desde 07/10/2026: extrator `sb_data_pipeline` (function `instagram`, 3x/dia, PR #17) e modelos dbt `sb_dw_dbt#32`. Skill de apoio: `especialista-instagram` (as mesmas consultas ficam em `references/consultas-bigquery.md`).

## Perguntas da página
1. **Como foi a semana?** O Instagram está trazendo gente nova, visita ao perfil e pedido, ou só views de anúncio?
2. **O que funcionou?** Qual peça, formato e tema entregam envio, salvamento e seguidor por alcance?
3. **O anúncio vale pagar?** Quanto o Meta gastou por peça e quanto virou pedido **real** (não o que o Meta atribui)?
4. **Quem é a audiência, o que perguntam e como estão os pares?** Público, horário, perguntas sem resposta, concorrentes.

## Fontes (todas na az, `dbt_dw_az`, região us-east4; nada de Google Ads/GA4, então não há join cross-region)
| Dado | Tabela | Observação |
|---|---|---|
| Conta por dia, split seguidor × não seguidor, formato e anúncio | `tb_instagram_conta_dia` | quebras só desde 10/04/2026 (`fg_tem_quebra`) |
| Funil dia a dia até o pedido | `tb_instagram_funil_dia` | pedidos ATENDIDOS com origem Instagram; margem antes de mídia |
| Peças, taxas por alcance, impulsionamento | `tb_instagram_midia` | último snapshot; Stories fora |
| Curva da peça | `tb_instagram_midia_curva` | só peças com 2+ snapshots |
| Anúncios | `tb_instagram_anuncio` | gasto, funil do Meta e pedido real (UTM) |
| Gasto por posição e por público | `tb_meta_ads_distribuicao_dia` | duas visões do mesmo gasto: não somar entre si |
| Público, horário | `tb_instagram_publico`, `tb_instagram_horario_online` | |
| Concorrentes | `tb_instagram_concorrente` | inclui a própria conta (`fg_propria`) |
| Atendimento, UGC | `tb_instagram_comentario`, `tb_instagram_marcacao` | LGPD: usernames e texto de terceiros, uso interno |

## Indicadores (regra de negócio mora no dbt; aqui só soma e razão soma/soma)
- **Views orgânicas** = views − views de anúncio (`qt_views_organicas`; só nos dias com quebra). **% de não seguidores** = Σ alcance de não seguidores ÷ Σ (alcance de seguidores + não seguidores) (razão de somas, nunca média de razões).
- **Alcance somado por dia NÃO é alcance único**: usado só para variação semana a semana, sempre rotulado "soma dos dias".
- **Pedidos Instagram** = orgânico (social) + pago (paid_social) com UTM. **É um piso**: a maior parte das vendas chega sem parâmetro (Notion "Rastreamento de Links"). Receita e margem **antes de mídia**; "após Meta" só aparece rotulada e desconta apenas o gasto do Meta.
- **Peça**: taxas por alcance (`pct_envio`, `pct_salvamento`, `pct_interacao`) e `qt_seguidores_por_mil_views` vêm prontas do dbt; percentil `pct_rank_*_formato` é dentro do mesmo formato. A mediana por formato é calculada aqui sobre as taxas prontas (apresentação, sem regra nova).
- **Anúncio**: `vl_roas_real` = receita dos pedidos com `utm_content` = id do anúncio ÷ gasto (**piso**); `vl_roas_meta` = valor de compra atribuído pelo Meta ÷ gasto (janela 7 dias clique / 1 dia view; **não é pedido real**). A diferença entre os dois é o dado mais importante da aba.
- **Semana** = segunda a domingo, cortada no último dia fechado e comparada aos mesmos dias da semana anterior (`common/semana.py`). Dia em andamento nunca entra.

## Seções (4 abas, uma por pergunta)
1. **Semana:** cards (views, views orgânicas, % não seguidores, visitas ao perfil, toques no link + cliques no site, seguidores novos quando existir, pedidos Instagram e receita, gasto Meta) com variação; tendência de 12 semanas (views orgânicas × anúncio; visitas ao perfil e pedidos); dia a dia da semana.
2. **Peças:** filtros (período de publicação, formato, orgânica/impulsionada, alcance mínimo); cards com medianas; mix por formato; tabela de peças com taxas, percentil e link; detalhe e curva de uma peça.
3. **Anúncios:** cards (gasto, pedidos reais, receita real, ROAS real × ROAS Meta, % do gasto ligado a uma peça); tabela por anúncio; gasto por posição e por público (90 dias).
4. **Audiência e atendimento:** idade e gênero; heatmap de seguidores online por dia × hora; concorrentes (com a própria conta); perguntas sem resposta da loja; posts de terceiros que nos marcaram.
Rodapé: "De quando são os dados" (frescor: extração do Instagram 3x/dia, dbt de hora em hora).

## Limites (mostrar sempre na tela)
- **Peça impulsionada tem views e alcance com o pago**; compare orgânicas entre si e impulsionadas entre si.
- Quebras por seguidor/formato/anúncio só existem desde **10/04/2026**; seguidores novos por dia só nos **últimos 30 dias**; seguidores no fim do dia, desde 07/10/2026.
- **Dia da métrica no fuso da Meta** (a verificar contra o Painel); os 1–2 últimos dias podem estar incompletos.
- Skip rate, retenção detalhada dos Reels e Status da conta (elegibilidade) **não vêm pela API**: ficam no Painel do Instagram.
- Horário online: histórico curto (desde 04/10/2026); veja `qt_dias_amostra`. Concorrentes: amostra das ~25 últimas peças; só dado público de contas profissionais.
- Pedido por UTM é piso; com ~1 pedido por dia o semanal oscila: leia 4+ semanas. Correlação não é causalidade (views altos e pedido no mesmo dia não provam causa).
- Comentários: heurística de pergunta (texto com "?"); resposta é sempre humana, a página só lista.

## Fora desta versão
Cruzar peça postada com a pauta do marketing (de-para código C-XXX ↔ post ainda não existe), termos de risco por peça, Reels com skip rate (precisa do Painel), alerta de queda de alcance.
