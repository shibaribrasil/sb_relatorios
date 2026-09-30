"""Cadastro de Produtos — edição de produto existente (descrição, SEO, tags e vitrines) — Shibari Brasil.

Regras: ver specs/cadastro-produtos.md. Página de trabalho (não de leitura gerencial): a regra mora na function `cadastrador`
(sb_data_pipeline); aqui o operador busca o produto, edita, SIMULA (vê o que muda em cada canal) e só então APLICA.

Sem login (decisão do Hugo, 29/09/2026): quem tem o link opera. Por isso o nome de quem opera é obrigatório para aplicar e
vai para a auditoria (`raw_control.cadastrador_edicoes`). Quem opera aprova o próprio passo; não há aprovação de terceiros.
"""
import streamlit as st

from common import cadastrador as cad
from common.design import inject_css, section_title, note

LIMITE_TITULO_SEO = 65
IDEAL_DESC_SEO = (140, 160)
NIVEL_ICONE = {"OK": "✅", "INFO": "ℹ️", "DIVERGE": "⚠️"}
CANAL_ROTULO = {"notion": "Notion (página)", "bling": "Bling", "nuvemshop": "Nuvemshop"}
CAMPO_ROTULO = {"descricao": "Descrição", "titulo_seo": "Título SEO", "descricao_seo": "Descrição SEO", "tags": "Tags", "vitrines": "Vitrines"}
STATUS_ICONE = {"ATUALIZADO": "✅ gravado e conferido", "SEM_MUDANCA": "➖ já estava igual", "ERRO": "❌ erro",
                "ERRO_VERIFICACAO": "❌ gravou, mas a releitura difere"}

AJUDA_FORMATO = """
**Como escrever a descrição** (texto simples com poucas marcas):

- `## Título de seção` para os títulos (Benefícios, Especificações técnicas, Cuidados...)
- Um parágrafo por bloco, separado por linha em branco. Linha seguinte sem linha em branco vira quebra de linha.
- `- item` para lista com marcadores e `1. item` para lista numerada
- `**negrito**`, `_itálico_`, `[texto do link](https://...)`, `---` para divisor
- Emoji só no bloco fixo do fim (postagem, rastreio, WhatsApp)

O sistema converte para a Nuvemshop (com formatação) e para o Bling (só negrito, itálico e listas; **sem emoji**, porque o Bling grava "?").
"""


def _estado():
    ss = st.session_state
    ss.setdefault("cad_operador", "")
    ss.setdefault("cad_achados", None)
    ss.setdefault("cad_dados", None)
    ss.setdefault("cad_plano", None)
    ss.setdefault("cad_resultado", None)
    return ss


def _limpa_produto():
    for k in ("cad_dados", "cad_plano", "cad_resultado"):
        st.session_state[k] = None


def _erro_api(e: cad.CadastradorErro):
    if e.status == 409:
        st.warning(f"{e.mensagem}")
        st.session_state["cad_plano"] = None
    else:
        st.error(f"Não foi possível concluir ({e.status}): {e.mensagem}")


def _secao_busca(ss):
    section_title("1. Produto")
    with st.form("cad_busca", border=False):
        c1, c2 = st.columns([4, 1])
        termo = c1.text_input("Nome ou SKU", placeholder="ex.: Coleira Luxo Dourado ou 100249", label_visibility="collapsed")
        buscou = c2.form_submit_button("Buscar", use_container_width=True)
    if buscou and termo.strip():
        try:
            with st.spinner("Buscando no catálogo..."):
                ss["cad_achados"] = cad.buscar(termo.strip())
            _limpa_produto()
        except cad.CadastradorErro as e:
            _erro_api(e)
    achados = ss["cad_achados"]
    if achados is None:
        note("Busque pelo nome ou pelo SKU. O produto precisa ter página no catálogo do Notion (o Notion é a verdade das descrições).")
        return
    if not achados:
        st.info("Nenhuma página do catálogo casa com essa busca.")
        return
    opcoes = {f'{a["produto"]} · SKU {a["sku"] or "—"} · {a["status"] or "sem status"}': a for a in achados}
    escolha = st.selectbox("Produtos encontrados", list(opcoes), label_visibility="collapsed")
    if st.button("Carregar produto"):
        try:
            with st.spinner("Lendo o Notion, o Bling e a Nuvemshop..."):
                ss["cad_dados"] = cad.ler(opcoes[escolha]["page_id"])
            ss["cad_plano"] = ss["cad_resultado"] = None
        except cad.CadastradorErro as e:
            _erro_api(e)


def _secao_canais(d):
    section_title("2. Situação nos três canais")
    bl, ns = d["bling"], d["nuvemshop"]
    partes = [f'**{d["produto"]}**', f'Notion: {d["status"] or "sem status"}']
    partes.append(f'Bling: {"situação " + bl["situacao"] + ", formato " + bl["formato"] if bl else "sem ID Bling"}')
    partes.append(f'Nuvemshop: {("publicado" if ns["publicado"] else "oculto") if ns else "não integrado"}')
    st.markdown(" · ".join(partes))
    for c in d["comparacao"]:
        st.markdown(f'{NIVEL_ICONE.get(c["nivel"], "•")} {c["texto"]}')
    if bl and bl["formato"] == "V":
        note("Produto com variação: o Bling do <strong>pai</strong> nunca é gravado pelo sistema (duplicaria as imagens das variações). "
             "As variações são atualizadas e o HTML do pai aparece no final para você colar no Bling.", "warn")


def _campos_edicao(d):
    """Widgets de edição; devolve a edição (só o que mudou em relação ao Notion) e o texto novo de cada campo."""
    n, pid = d["notion"], d["page_id"]
    section_title("3. Editar")
    st.caption("Os campos começam com o valor atual da página do Notion. Só o que você mudar será enviado.")
    aba_desc, aba_seo, aba_tags = st.tabs(["Descrição", "SEO", "Tags e vitrines"])
    with aba_desc:
        with st.expander("Como escrever"):
            st.markdown(AJUDA_FORMATO)
        desc = st.text_area("Descrição", value=n["descricao_md"], height=520, key=f"cad_desc_{pid}", label_visibility="collapsed")
    with aba_seo:
        titulo = st.text_input("Título SEO", value=n["titulo_seo"] or "", key=f"cad_tit_{pid}")
        st.caption(f"{len(titulo)} caracteres (máx. {LIMITE_TITULO_SEO}) · aparece no Google e na aba do navegador")
        desc_seo = st.text_area("Descrição SEO", value=n["descricao_seo"] or "", height=110, key=f"cad_dseo_{pid}")
        st.caption(f"{len(desc_seo)} caracteres (ideal {IDEAL_DESC_SEO[0]} a {IDEAL_DESC_SEO[1]}) · o texto que convida ao clique no Google")
    with aba_tags:
        tags = st.text_input("Tags (separadas por vírgula)", value=n["tags"] or "", key=f"cad_tags_{pid}")
        st.caption("A Nuvemshop reordena as tags e tira os acentos delas; isso é normal.")
        atuais = [v for v in n["vitrines"] if v in d["vitrines_da_loja"]]
        vit = st.multiselect("Vitrines (categorias da loja)", d["vitrines_da_loja"], default=atuais, key=f"cad_vit_{pid}")
        fora = [v for v in n["vitrines"] if v not in d["vitrines_da_loja"]]
        if fora:
            st.warning("A página cita vitrines que não existem na loja: " + ", ".join(fora) + ". Escolha as corretas acima.")
    edicao = {}
    if desc.strip() != (n["descricao_md"] or "").strip():
        edicao["descricao_md"] = desc
    if titulo.strip() != (n["titulo_seo"] or "").strip():
        edicao["titulo_seo"] = titulo
    if desc_seo.strip() != (n["descricao_seo"] or "").strip():
        edicao["descricao_seo"] = desc_seo
    if tags.strip() != (n["tags"] or "").strip():
        edicao["tags"] = tags
    if vit != n["vitrines"]:
        edicao["vitrines"] = vit
    return edicao


def _mostra_plano(plano):
    for e in plano["erros"]:
        st.error(e)
    for a in plano["avisos"]:
        st.warning(a)
    for x in plano.get("notas", []):
        st.caption("ℹ️ " + x)
    mudam = [p for p in plano["passos"] if p["muda"]]
    if not mudam:
        st.info("Nada muda em nenhum canal: o que você escreveu já é o que está publicado.")
    for canal in ("notion", "bling", "nuvemshop"):
        do_canal = [p for p in plano["passos"] if p["canal"] == canal]
        if not do_canal:
            continue
        st.markdown(f"**{CANAL_ROTULO[canal]}**")
        for p in do_canal:
            rotulo = CAMPO_ROTULO.get(p["campo"], p["campo"]) + (f' ({p["alvo"]})' if p.get("alvo") else "")
            if not p["muda"]:
                st.caption(f"➖ {rotulo}: já está igual")
                continue
            with st.expander(f"✏️ {rotulo}: vai mudar", expanded=p["campo"] != "descricao"):
                c1, c2 = st.columns(2)
                c1.caption("Antes")
                c1.code(p["antes"] or "(vazio)", language=None, wrap_lines=True)
                c2.caption("Depois")
                c2.code(p["depois"] or "(vazio)", language=None, wrap_lines=True)
    for m in plano.get("manual", []):
        st.warning(f'**Passo manual no {m["canal"].title()} ({m["alvo"]}).** {m["instrucao"]}')
        st.code(m["html"], language="html", wrap_lines=True)


def _mostra_resultado(res):
    if res["ok"]:
        st.success("Edição aplicada e conferida nos canais.")
    else:
        st.error(f'A aplicação parou no canal **{res["parou_em"]}**. Os canais seguintes NÃO foram tocados. Veja abaixo o que já foi gravado.')
    for r in res["resultado"]:
        alvo = f' ({r["alvo"]})' if r.get("alvo") else ""
        linha = f'{CANAL_ROTULO.get(r["canal"], r["canal"])} · {CAMPO_ROTULO.get(r["campo"], r["campo"])}{alvo}: {STATUS_ICONE.get(r["status"], r["status"])}'
        st.markdown("- " + linha + (f' — {r["erro"]}' if r.get("erro") else ""))
    for m in res.get("manual", []):
        st.warning(f'**Falta o passo manual no {m["canal"].title()} ({m["alvo"]}).** {m["instrucao"]}')
        st.code(m["html"], language="html", wrap_lines=True)
    for a in res.get("avisos_auditoria", []):
        st.caption("⚠️ " + a)
    st.caption(f'Registro da operação: {res["run_id"]}')


def render():
    inject_css()
    ss = _estado()
    st.html("""
    <div class="report-header">
      <div>
        <div class="report-brand">shibari brasil · catálogo</div>
        <div class="report-title">Cadastro de Produtos <span>—</span> Edição</div>
        <div class="report-meta">Edite descrição, SEO, tags e vitrines de um produto que já existe. O texto vai para o Notion, depois para o Bling e por fim para a Nuvemshop.</div>
      </div>
    </div>""")

    ss["cad_operador"] = st.text_input("Quem está operando", value=ss["cad_operador"], placeholder="seu nome (fica registrado em cada gravação)")
    _secao_busca(ss)
    d = ss["cad_dados"]
    if not d:
        return
    _secao_canais(d)
    edicao = _campos_edicao(d)

    section_title("4. Simular")
    if not edicao:
        st.caption("Mude algum campo acima para poder simular.")
    if st.button("Simular (não grava nada)", disabled=not edicao, type="primary"):
        try:
            with st.spinner("Comparando com o Notion, o Bling e a Nuvemshop..."):
                ss["cad_plano"] = {"plano": cad.simular(d["page_id"], edicao), "edicao": edicao, "page_id": d["page_id"]}
            ss["cad_resultado"] = None
        except cad.CadastradorErro as e:
            _erro_api(e)
    p = ss["cad_plano"]
    if p and p["page_id"] == d["page_id"]:
        plano = p["plano"]
        _mostra_plano(plano)
        vencida = p["edicao"] != edicao
        if vencida:
            st.warning("Você mudou algum campo depois de simular. Simule de novo antes de aplicar.")
        section_title("5. Aplicar")
        conferi = st.checkbox("Conferi o que vai mudar em cada canal", key=f'cad_conferi_{plano["hash"]}')
        falta = []
        if not ss["cad_operador"].strip():
            falta.append("informe quem está operando")
        if plano["erros"]:
            falta.append("corrija os erros acima")
        if not plano["pode_aplicar"] and not plano["erros"]:
            falta.append("não há nada a gravar")
        if not conferi:
            falta.append("marque que conferiu")
        if vencida:
            falta.append("simule de novo")
        if falta:
            st.caption("Para aplicar: " + "; ".join(falta) + ".")
        if st.button("Aplicar edição", disabled=bool(falta), type="primary"):
            try:
                with st.spinner("Gravando no Notion, no Bling e na Nuvemshop (pode levar até 1 minuto)..."):
                    ss["cad_resultado"] = cad.aplicar(d["page_id"], p["edicao"], plano["hash"], ss["cad_operador"].strip())
                ss["cad_plano"] = None
                ss["cad_dados"] = cad.ler(d["page_id"])
                st.rerun()
            except cad.CadastradorErro as e:
                _erro_api(e)
    if ss["cad_resultado"]:
        section_title("Resultado")
        _mostra_resultado(ss["cad_resultado"])
