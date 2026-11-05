# Spec — Biblioteca (camada de consulta)

Página de análise e busca do catálogo da biblioteca de shibari, BDSM, fetiches e sexualidade da Shibari Brasil. Responde duas perguntas: **o que temos e o que não temos** (cobertura por assunto) e **onde está o que preciso** (busca de fontes e de verbetes do dicionário). Criada em 04/11/2026 a pedido do Hugo.

## Fonte de dados

Dataset `raw_biblioteca` (`us-east4`), carregado por `sb_data_pipeline/scripts/carregar_biblioteca.py` a partir do repositório `sb_cerebro` (catálogo, triagem e dicionário). **Não é dbt**: a regra de negócio mora nas views do dataset (`sql/raw_biblioteca/views.sql` no `sb_data_pipeline`) e nos arquivos do `sb_cerebro`; a página só filtra, soma e apresenta. A carga é manual (sob demanda), quando o catálogo muda; a página mostra a data da última carga.

| Tabela ou view | O que é | Chave |
|---|---|---|
| `vw_cobertura_tema` | uma linha por tema: fontes, escopo, confiança, idioma, guias práticos, tutoriais | `tema` |
| `vw_cobertura_colecao` | coleção × confiabilidade: fontes, escopo, palavras | `colecao, confiabilidade` |
| `vw_cobertura_dicionario` | universo × tipo: verbetes, uma fonte, 2+ fontes, divergência | `universo, tipo` |
| `vw_busca_fontes` | fonte ativa com tema, escopo, tipo de conteúdo e `prioridade_busca` | `id` |
| `verbetes`, `verbete_fonte` | o dicionário e a definição de cada fonte | `universo, slug` |

## Conceitos (definidos com o Hugo)

- **Frente:** `shibari`, `bdsm-fetiches`, `sexualidade` e `demais-materiais` (avisos, publicidade, assuntos soltos). Vem do tema.
- **Escopo (por material, nunca por coleção):** `nucleo` (shibari, BDSM, fetiches), `adjacente` (sexualidade), `fora-de-foco` (acessório). Todo material de `demais-materiais` é `fora-de-foco`.
- **Prioridade de busca:** 1 núcleo, 2 adjacente, 3 fora de foco, 4 demais materiais (**só em última hipótese**; fora da busca por padrão).
- **Fonte confiável:** confiabilidade `primaria`, `academica` ou `praticante`. `comunidade` reflete o meio, não prova. `comercial` é material de loja ou marca (só o editorial entra).
- O **tema, o escopo e o tipo de conteúdo são propostas da triagem por IA** (título, abertura e intertítulos), ainda sem conferência do Hugo. A página não os trata como verdade, e o catálogo guarda a correção pontual.

## Indicadores

### Cobertura por tema
- **Fontes do tema** = contagem de fontes cujo tema principal é o tema (`fonte_tema.principal`).
- **Situação do tema** (regra de apresentação; limites ajustáveis no topo de `reports/biblioteca.py`):
  - `Sem fonte`: 0 fontes.
  - `Sem fonte confiável`: há fontes, mas nenhuma é confiável.
  - `Pouca fonte confiável`: 1 ou 2 fontes confiáveis (`POUCA_FONTE_CONFIAVEL = 3`).
  - `Coberto`: 3 ou mais fontes confiáveis.
  - `demais-materiais` não entra na régua (não é referência).
- **Lacuna** é o tema `Sem fonte` ou `Sem fonte confiável`. É sinal de onde buscar material novo, não juízo de qualidade.

### Coleções
Fontes por coleção e escopo, e palavras. Mostra de onde vem o material e que parte dele é núcleo.

### Dicionário
Verbetes por universo e tipo; verbetes com **uma fonte só** (menor lastro), com **2 ou mais** e com **divergência** entre fontes. Um verbete é um termo; cada fonte tem a sua definição, sem fusão.

### Busca
- **Fontes:** filtros por texto (sem acento), frente, tema, escopo, idioma, confiabilidade e tipo de conteúdo. Ordem: `prioridade_busca`, depois confiabilidade (confiáveis primeiro), depois tamanho. `demais-materiais` só entra se o campo "Incluir demais materiais" estiver marcado.
- **Verbetes:** busca por nome ou nome alternativo; ao escolher um verbete, mostra o resumo de cada fonte.

## O que a página não diz

Não diz que a fonte está certa, nem que cobre bem o assunto: diz quantas fontes há, de que tipo e de que confiança. Fonte comercial conta como fonte, mas pesa pouco na régua de lacuna. Resumos do dicionário são `rascunho` (IA), não conferidos.
