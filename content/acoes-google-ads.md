# Ações e Considerações — Google Ads

Registro de decisões e ações tomadas a partir dos relatórios de diagnóstico. Usado como insumo nos próximos relatórios para contextualizar mudanças e medir resultados (ver seções "Últimas Ações Tomadas" e "Resultado das Últimas Ações" em `reports/google_ads.py`).

Migrado de `sb_marketing_team/relatorios/diagnostico-google-ads/acoes-e-consideracoes.md` em 2026-07-01 — este arquivo passa a ser a fonte única daqui pra frente.

---

## Como registrar uma ação nova

Editado manualmente. Fluxo: anote informalmente (bloco de notas, mensagem) o que foi feito e peça pra formatar — a nova entrada entra no **topo** do "Registro de Ações" abaixo (mais recente primeiro), seguindo este formato:

```
### [DD/MM/AAAA] — [Título curto da ação]
**Observação no relatório:** o que chamou atenção ou gerou a decisão
**Ação:** o que foi feito (ou será feito)
**Resultado esperado:** o que se espera observar na próxima análise
**Status:** Planejado | Executado | Monitorando | Concluído
```

Para ações mais complexas (várias campanhas, várias mudanças), pode-se detalhar em subseções (`####`) e tabelas dentro da mesma entrada — ver exemplo abaixo. O único campo obrigatório e padronizado é o **Status**, porque `detectar_acoes_avaliaveis()` em `reports/google_ads.py` filtra por ele (só `Executado`/`Monitorando` entram na avaliação de resultado — `Planejado` ainda não tem o que avaliar).

---

## Registro de Ações

---

### 25/06/2026 — Ajustes pós-relatório de diagnóstico (24/06/2026)

**Relatório de origem:** `relatorio-2026-06-24.html` (histórico, gerado em `sb_marketing_team` antes da migração para este projeto)

#### Campanhas — Orçamentos e Status

| Campanha | Ação |
|---|---|
| Compra Pesquisa 22/10/2023 | **Pausada** |
| Topo de Funil | Orçamento ajustado para **R$ 7,00/dia** |
| Remarketing | Orçamento ajustado para **R$ 10,62/dia** |
| Compra Direta | Orçamento ajustado para **R$ 16,24/dia** |
| Google Shopping | Orçamento ajustado para **R$ 13,59/dia** |

#### Campanha: Topo de Funil — Palavras-chave negativas adicionadas

Correspondência exata adicionada como negativas:
`[comprar]` · `[loja]` · `[comprar corda]` · `[loja shibari]` · `[loja bdsm]`

#### Configurações globais

- **Padrão de conversão** alterado para **baseado em dados** (todas as campanhas)

#### Campanha: Compra Direta — Mudanças estruturais

- **Metas de conversão** expandidas: antes apenas `Compras`; agora inclui também `Adicionar ao carrinho` e `Iniciar finalização de compra`
- **AI Max** ativado
- **Correspondência exata** adicionada para:
  `[shibari brasil]` · `[comprar corda shibari]` · `[corda de juta para shibari]`

---

#### Observações e Diagnósticos

**Add to Cart não estava mapeado como meta de conversão**
O evento de "adicionar ao carrinho" estava sendo coletado corretamente via Tag Manager (confirmado em teste), mas não estava vinculado como meta de conversão em nenhuma campanha. Corrigido: evento adicionado como meta na campanha Compra Direta.

**Customer Match e ampliação de público do remarketing**
Não foi possível configurar ainda. Pendência para próxima sessão.

**Status geral:** Executado (exceto Customer Match — pendente)
