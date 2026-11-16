"""Definições e regras das avaliações integrativa (8 remédios naturais) e nutricional no TEA.

IMPORTANTE: os escores abaixo são TRIAGENS INTERNAS de apoio ao profissional. Não são instrumentos
validados, não diagnosticam TEA nem qualquer doença, e não substituem avaliação multiprofissional.
"""

# ───────── Avaliação integrativa: oito remédios naturais ─────────
PILARES = [
    ("alimentacao", "Alimentação saudável", "Revisar padrão alimentar, priorizar alimentos in natura e regularidade das refeições."),
    ("exercicio", "Exercício", "Definir meta gradual de atividade física semanal, respeitando limitações e liberação médica."),
    ("agua", "Água", "Estabelecer meta de hidratação ao longo do dia e substituir bebidas açucaradas."),
    ("luz_solar", "Luz solar", "Orientar exposição solar segura e em horários adequados; avaliar vitamina D se indicado."),
    ("temperanca", "Temperança", "Trabalhar moderação e equilíbrio (porções, álcool, cafeína, ultraprocessados)."),
    ("ar_puro", "Ar puro", "Estimular ambientes ventilados e momentos ao ar livre; avaliar exposição a fumaça."),
    ("repouso", "Repouso", "Trabalhar higiene do sono e pausas ao longo do dia."),
    ("confianca", "Confiança em Deus / espiritualidade", "Respeitar a escolha do paciente; apenas se ele desejar."),
]
PILAR_SENSIVEL = "confianca"  # convicção religiosa = dado sensível; exige consentimento específico


def coletar_integrativa(form, permite_espiritual):
    notas, obs = {}, {}
    for chave, _rot, _sug in PILARES:
        if chave == PILAR_SENSIVEL and not permite_espiritual:
            continue
        raw = (form.get(f"nota_{chave}") or "").strip()
        if raw == "":
            continue
        try:
            n = int(raw)
        except ValueError:
            raise ValueError(f"Nota inválida em {chave}.")
        if not 0 <= n <= 10:
            raise ValueError("As notas devem estar entre 0 e 10.")
        notas[chave] = n
        o = (form.get(f"obs_{chave}") or "").strip()[:1000]
        if o:
            obs[chave] = o
    if not notas:
        raise ValueError("Informe a nota de pelo menos um pilar.")
    return {"notas": notas, "obs": obs}


def resumo_integrativa(dados):
    notas = dados.get("notas", {})
    media = sum(notas.values()) / len(notas) if notas else None
    baixos = [(c, n) for c, n in notas.items() if n <= 4]
    rotulo = {c: r for c, r, _ in PILARES}
    sug = {c: s for c, _, s in PILARES}
    return {"media": media, "baixos": [(rotulo[c], n, sug[c]) for c, n in sorted(baixos, key=lambda x: x[1])]}


# ───────── TEA: avaliação alimentar e nutricional ─────────
GRUPOS = [("frutas", "Frutas"), ("verduras", "Verduras e legumes"), ("carnes", "Carnes e ovos"),
          ("leguminosas", "Feijões e leguminosas"), ("cereais", "Arroz, massas, pães"),
          ("laticinios", "Leite e derivados"), ("ultraprocessados", "Ultraprocessados / doces")]
RECUSAS = [("textura", "Textura"), ("cor", "Cor"), ("cheiro", "Cheiro"), ("temperatura", "Temperatura"),
           ("marca", "Marca / embalagem específica"), ("mistura", "Alimentos misturados no prato")]
COMPORTAMENTOS = [("ritual", "Rituais ou rotinas rígidas na refeição"), ("recusa_novos", "Recusa total de alimentos novos"),
                  ("mastigacao", "Dificuldade de mastigação/deglutição"), ("engasgo", "Engasgos ou vômitos frequentes"),
                  ("pica", "Ingere itens não alimentares (pica)"), ("refeicao_longa", "Refeições muito longas (> 45 min)"),
                  ("sem_fome", "Pouco sinal de fome/saciedade")]
GI = [("constipacao", "Constipação"), ("diarreia", "Diarreia"), ("refluxo", "Refluxo / regurgitação"),
      ("dor", "Dor ou desconforto abdominal"), ("distensao", "Distensão / gases")]
APOIO = [("fono", "Fonoaudiologia"), ("to", "Terapia ocupacional / integração sensorial"),
         ("psico", "Psicologia / ABA"), ("medico", "Acompanhamento médico (neuropediatria/psiquiatria)")]

FAIXAS_ACEITOS = [("", "Selecione"), ("ate10", "Até 10 alimentos"), ("11a20", "11 a 20 alimentos"),
                  ("21a30", "21 a 30 alimentos"), ("mais30", "Mais de 30 alimentos")]


def _marcados(form, prefixo, itens):
    return [k for k, _ in itens if form.get(f"{prefixo}_{k}")]


def coletar_tea(form):
    def txt(n, m=2000):
        return (form.get(n) or "").strip()[:m]

    faixa = form.get("faixa_aceitos", "")
    if faixa not in dict(FAIXAS_ACEITOS) or not faixa:
        raise ValueError("Informe a quantidade aproximada de alimentos aceitos.")
    dados = {
        "informante": txt("informante", 120),
        "faixa_aceitos": faixa,
        "grupos_aceitos": _marcados(form, "grupo", GRUPOS),
        "recusas": _marcados(form, "recusa", RECUSAS),
        "comportamentos": _marcados(form, "comp", COMPORTAMENTOS),
        "gi": _marcados(form, "gi", GI),
        "bristol": txt("bristol", 20),
        "sono": txt("sono", 400),
        "medicamentos": txt("medicamentos"),
        "suplementos": txt("suplementos"),
        "dietas_restritivas": txt("dietas_restritivas"),
        "apoio": _marcados(form, "apoio", APOIO),
        "alimentos_aceitos": txt("alimentos_aceitos", 3000),
        "alimentos_recusados": txt("alimentos_recusados", 3000),
        "sensorial": txt("sensorial"),
        "obs": txt("obs", 4000),
    }
    for campo in ("zimc", "zestatura"):
        raw = (form.get(campo) or "").strip().replace(",", ".")
        if raw:
            try:
                v = float(raw)
            except ValueError:
                raise ValueError("Escore-z inválido.")
            if not -6 <= v <= 6:
                raise ValueError("Escore-z deve estar entre -6 e +6.")
            dados[campo] = v
    return dados


def triagem_tea(d):
    """Retorna (nivel, pontos_de_atencao). Nível: baixo | moderado | alto (triagem interna)."""
    pts, atencao = 0, []
    faixa = d.get("faixa_aceitos")
    if faixa == "ate10":
        pts += 3
        atencao.append("Repertório muito restrito (até 10 alimentos): risco de ingestão inadequada de nutrientes.")
    elif faixa == "11a20":
        pts += 2
        atencao.append("Repertório restrito (11–20 alimentos).")
    elif faixa == "21a30":
        pts += 1
    n_grupos = len(d.get("grupos_aceitos", []))
    sem_saudavel = [r for k, r in GRUPOS if k in ("frutas", "verduras", "carnes", "leguminosas") and k not in d.get("grupos_aceitos", [])]
    if sem_saudavel:
        pts += 1 if len(sem_saudavel) < 3 else 2
        atencao.append("Grupos não aceitos: " + ", ".join(sem_saudavel) + " — avaliar adequação de proteínas, fibras, ferro, zinco, cálcio e vitaminas.")
    if n_grupos <= 3:
        pts += 1
    comp = d.get("comportamentos", [])
    if "mastigacao" in comp or "engasgo" in comp:
        pts += 2
        atencao.append("Dificuldade de mastigação/deglutição ou engasgos: sugerir avaliação fonoaudiológica e médica.")
    if "pica" in comp:
        pts += 2
        atencao.append("Pica relatada: comunicar equipe médica (avaliar deficiências, como ferro/zinco, e segurança).")
    gi = d.get("gi", [])
    if gi:
        pts += 1
        atencao.append("Sintomas gastrointestinais (" + ", ".join(r for k, r in GI if k in gi) + "): considerar avaliação médica e ajuste de fibras/líquidos; não atribuir a dietas sem avaliação.")
    if d.get("dietas_restritivas"):
        atencao.append("Dieta restritiva informada: a evidência para dietas de exclusão (ex.: sem glúten/caseína) é limitada; verificar adequação nutricional e supervisão profissional.")
    if d.get("medicamentos"):
        atencao.append("Medicamentos em uso: verificar efeitos sobre apetite, peso e glicemia/lipídios e possíveis interações com suplementos.")
    z = d.get("zimc")
    if z is not None and (z <= -2 or z >= 2):
        pts += 2
        atencao.append(f"Escore-z de IMC/idade = {z:.1f} (fora de ±2): priorizar avaliação do estado nutricional e acompanhamento mais próximo.")
    ze = d.get("zestatura")
    if ze is not None and ze <= -2:
        pts += 1
        atencao.append(f"Escore-z de estatura/idade = {ze:.1f} (≤ −2): investigar adequação de crescimento.")
    if "ultraprocessados" in d.get("grupos_aceitos", []) and n_grupos <= 3:
        atencao.append("Repertório concentrado em ultraprocessados: planejar introdução gradual e estruturada de novos alimentos (dessensibilização), em conjunto com a equipe terapêutica.")
    nivel = "alto" if pts >= 6 else "moderado" if pts >= 3 else "baixo"
    return nivel, atencao
