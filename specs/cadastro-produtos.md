# Spec — Cadastro de Produtos (edição)

Página: `reports/cadastro_produtos.py` (menu "Catálogo", `url_path=cadastro`). Cliente: `common/cadastrador.py`.
Regra e gravação: Cloud Function `cadastrador` (`sb_data_pipeline`, `cadastrador/edicao.py`). Esta página **não recalcula nem valida nada**: pede e mostra.
Plano completo: `planejamento/Plano — Cadastrador como Aplicação (Streamlit + Function).md` (Fase B).

## O que a página faz
Edita um produto **que já existe** (tem página no catálogo do Notion): descrição, título SEO, descrição SEO, tags e vitrines.
Não cria produto (Fase C), não muda dado cadastral (vem da planilha `cadastro_produto` → Bling) nem preço.

## Fluxo
1. **Buscar** por nome ou SKU e carregar o produto (`buscar`, `ler`).
2. **Situação nos três canais**: comparação Notion × Bling × Nuvemshop.
3. **Editar**: campos começam com o valor atual do Notion; só o que mudou é enviado. Descrição em Markdown simples (ver `cadastrador/texto.py`).
4. **Simular** (`simular`, não grava): mostra antes/depois por canal, erros (bloqueiam), avisos (o operador decide) e devolve um `hash`.
5. **Aplicar** (`aplicar`): exige o nome do operador, a caixa "Conferi" e o `hash` da simulação. Se o produto ou a edição mudou desde a simulação, a function recusa (409) e a tela pede nova simulação.

## Regras de negócio (todas na function)
- **A página do Notion é a verdade.** Ordem de gravação: Notion, Bling, Nuvemshop. Bling falhou: a Nuvemshop não recebe. Notion falhou: nada mais é gravado.
- **Produto com variação:** o pai do Bling nunca recebe PATCH (duplica as imagens das variações). Cada variação é atualizada e o HTML do pai aparece na tela para colar à mão.
- **Bling não grava emoji** (vira "?"): a versão Bling sai sem emoji. A Nuvemshop mantém.
- **Erros que bloqueiam:** descrição vazia, HTML escrito como texto, título/descrição SEO ou tags vazios, vitrine que não existe na loja.
- **Avisos** (não bloqueiam): lista negra antirrobô, travessão como conector, frase acima de 25 palavras, frase fixa "Postagem em até 2 dias úteis", CTA fixo, emoji fora do bloco operacional, seção "Por que comprar" (política inexistente, venda cruzada, mais de 5 itens), título SEO acima de 65 e descrição SEO fora de 140–160 caracteres.
- Tags: a Nuvemshop reordena e tira acentos; a comparação é como conjunto.

## Operação e auditoria
- **Sem login** (Hugo, 29/09/2026): quem tem o link opera. O nome de quem opera é obrigatório para aplicar.
- **Quem opera aprova o próprio passo.** Não há aprovação de terceiros.
- Cada gravação vai para `raw_control.cadastrador_edicoes` (append-only, criada pela function): `run_id`, `operador`, `produto`, `page_id`, `hash`, `canal`, `campo`, `status`, `antes`, `depois`, `erro`, `processed_at`. O valor anterior fica em `antes` (dá para desfazer editando de volta).
- A function roda com 1 instância e 1 requisição por vez (token do Bling de uso único e limite de requisições).

## Limitações
- Não há desfazer automático: para voltar, edite o texto de volta (o `antes` está na auditoria).
- A descrição do produto de variação no **pai** do Bling é manual.
- Sem verificação de identidade: o nome do operador é declarado, não autenticado.
