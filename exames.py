"""Interpretação de exames laboratoriais — APOIO ao nutricionista, NÃO diagnóstico.

Princípios:
  * O intervalo de referência do LABORATÓRIO informado prevalece sobre qualquer referência genérica.
  * O sistema produz "achados para correlação clínica" e sugere encaminhamento; nunca afirma doença.
  * Cada regra cita fonte e estado de verificação; o conjunto é versionado (REGRAS_VERSAO) e deve ser
    revisado periodicamente (diretrizes mudam).
  * Pontos de corte numéricos só entram quando verificados em fonte; os demais usam o intervalo do laboratório.
Normas: Lei 8.234/1991 art. 4º VIII e Recomendação CFN nº 005/2016 (exames para diagnóstico nutricional e
ajuste da dieta; não para diagnóstico nosológico).
"""
import math

REGRAS_VERSAO = "2026.11"

# chave: (nome, categoria, unidade padrão, {unidade alternativa: multiplicador para a padrão}, casas decimais)
CATALOGO = {
    "hemoglobina": ("Hemoglobina", "Hematológica", "g/dL", {"g/L": 0.1}, 1),
    "hematocrito": ("Hematócrito", "Hematológica", "%", {}, 1),
    "vcm": ("VCM", "Hematológica", "fL", {}, 1),
    "plaquetas": ("Plaquetas", "Hematológica", "mil/mm³", {"10^9/L": 1.0}, 0),
    "glicemia": ("Glicemia de jejum", "Glicêmica", "mg/dL", {"mmol/L": 18.016}, 0),
    "hba1c": ("Hemoglobina glicada (HbA1c)", "Glicêmica", "%", {}, 1),
    "insulina": ("Insulina de jejum", "Glicêmica", "µUI/mL", {"pmol/L": 0.144}, 1),
    "col_total": ("Colesterol total", "Lipídica", "mg/dL", {"mmol/L": 38.67}, 0),
    "hdl": ("HDL-colesterol", "Lipídica", "mg/dL", {"mmol/L": 38.67}, 0),
    "ldl": ("LDL-colesterol", "Lipídica", "mg/dL", {"mmol/L": 38.67}, 0),
    "tg": ("Triglicerídeos", "Lipídica", "mg/dL", {"mmol/L": 88.57}, 0),
    "tgo": ("TGO/AST", "Hepática", "U/L", {}, 0),
    "tgp": ("TGP/ALT", "Hepática", "U/L", {}, 0),
    "ggt": ("GGT", "Hepática", "U/L", {}, 0),
    "ureia": ("Ureia", "Renal", "mg/dL", {"mmol/L": 6.006}, 0),
    "creatinina": ("Creatinina", "Renal", "mg/dL", {"µmol/L": 0.01131}, 2),
    "acido_urico": ("Ácido úrico", "Renal", "mg/dL", {"µmol/L": 0.0168}, 1),
    "tsh": ("TSH", "Hormonal", "µUI/mL", {}, 2),
    "t4l": ("T4 livre", "Hormonal", "ng/dL", {"pmol/L": 0.0777}, 2),
    "ferritina": ("Ferritina", "Nutricional", "ng/mL", {"µg/L": 1.0}, 0),
    "b12": ("Vitamina B12", "Nutricional", "pg/mL", {"pmol/L": 1.355}, 0),
    "folato": ("Folato", "Nutricional", "ng/mL", {"nmol/L": 0.4413}, 1),
    "vitd": ("Vitamina D (25-OH)", "Nutricional", "ng/mL", {"nmol/L": 0.4006}, 0),
    "pcr": ("PCR (proteína C reativa)", "Inflamatória", "mg/L", {"mg/dL": 10.0}, 1),
}
CATEGORIAS = ["Hematológica", "Glicêmica", "Lipídica", "Hepática", "Renal", "Hormonal", "Nutricional", "Inflamatória"]


def unidades(chave):
    nome, cat, padrao, alt, _ = CATALOGO[chave]
    return [padrao] + list(alt)


def para_padrao(chave, valor, unidade):
    """Converte para a unidade padrão. Levanta ValueError se a unidade for desconhecida."""
    _, _, padrao, alt, casas = CATALOGO[chave]
    if unidade in (None, "", padrao):
        return valor
    if unidade not in alt:
        raise ValueError(f"Unidade inválida para {CATALOGO[chave][0]}: {unidade}")
    return valor * alt[unidade]


def classificar(valor, mn, mx):
    """'baixo' | 'normal' | 'alto' | None (sem intervalo)."""
    if valor is None:
        return None
    if mn is not None and valor < mn:
        return "baixo"
    if mx is not None and valor > mx:
        return "alto"
    if mn is None and mx is None:
        return None
    return "normal"


# ───────────────────────── valores calculados ─────────────────────────
def egfr_ckdepi_2021(creat_mg_dl, idade, sexo):
    """CKD-EPI 2021 (sem raça), mL/min/1,73 m²."""
    k = 0.7 if sexo == "F" else 0.9
    a = -0.241 if sexo == "F" else -0.302
    r = creat_mg_dl / k
    v = 142 * (min(r, 1) ** a) * (max(r, 1) ** -1.200) * (0.9938 ** idade)
    return v * 1.012 if sexo == "F" else v


def estagio_tfg(v):
    for lim, rot in ((90, "G1 (≥ 90)"), (60, "G2 (60–89)"), (45, "G3a (45–59)"), (30, "G3b (30–44)"), (15, "G4 (15–29)")):
        if v >= lim:
            return rot
    return "G5 (< 15)"


def calculados(v, idade, sexo):
    """v: {chave: valor na unidade padrão}. Retorna dict de itens derivados."""
    out = {}
    if v.get("col_total") and v.get("hdl"):
        out["nao_hdl"] = {"nome": "Colesterol não-HDL", "valor": v["col_total"] - v["hdl"], "unidade": "mg/dL",
                          "formula": "colesterol total − HDL"}
    if not v.get("ldl") and v.get("col_total") and v.get("hdl") and v.get("tg") and v["tg"] < 400:
        out["ldl_friedewald"] = {"nome": "LDL estimado (Friedewald)", "valor": v["col_total"] - v["hdl"] - v["tg"] / 5,
                                 "unidade": "mg/dL", "formula": "CT − HDL − TG/5 (válido para TG < 400 mg/dL)"}
    if v.get("glicemia") and v.get("insulina"):
        out["homa_ir"] = {"nome": "HOMA-IR", "valor": v["glicemia"] * v["insulina"] / 405, "unidade": "",
                          "formula": "glicemia (mg/dL) × insulina (µUI/mL) ÷ 405"}
    if v.get("creatinina") and idade is not None and idade >= 18:
        t = egfr_ckdepi_2021(v["creatinina"], idade, sexo)
        out["egfr"] = {"nome": "TFG estimada (CKD-EPI 2021)", "valor": t, "unidade": "mL/min/1,73 m²",
                       "formula": "CKD-EPI 2021 sem raça (adultos)", "extra": estagio_tfg(t)}
    return out


# ───────────────────────── regras / achados ─────────────────────────
def _a(nivel, regra, titulo, texto, implicacao="", encaminhar=False, fonte="", status="verificada"):
    return {"nivel": nivel, "regra": regra, "titulo": titulo, "texto": texto, "implicacao": implicacao,
            "encaminhar": encaminhar, "fonte": fonte, "status": status}


FONTE_SBD = "SBD — Diretriz (adendo de jul/2024): glicemia jejum, HbA1c e TTGO"


def regras(v, linhas, calc, idade, sexo, meta_ldl=None):
    """v: valores padrão; linhas: {chave: {status,...}}; calc: derivados. Retorna lista de achados."""
    f = []
    g, a1c = v.get("glicemia"), v.get("hba1c")
    # glicemia (SBD 2024)
    if g is not None:
        if g >= 126:
            f.append(_a("alerta", "SBD-GLI-01", "Glicemia de jejum no patamar de critério para diabetes",
                        f"Glicemia de jejum {g:.0f} mg/dL (≥ 126 mg/dL). Compatível com critério diagnóstico para diabetes mellitus; "
                        "exige confirmação por repetição/outro critério e avaliação médica.",
                        "Orientar plano alimentar com controle de carboidratos conforme conduta médica; não classificar como diagnóstico.",
                        True, FONTE_SBD))
        elif g >= 100:
            f.append(_a("atencao", "SBD-GLI-02", "Glicemia de jejum na faixa de pré-diabetes",
                        f"Glicemia de jejum {g:.0f} mg/dL (100–125 mg/dL).", "Considerar estratégias de qualidade de carboidratos, fibras, peso e atividade; repetir conforme orientação médica.",
                        True, FONTE_SBD))
    if a1c is not None:
        if a1c >= 6.5:
            f.append(_a("alerta", "SBD-A1C-01", "HbA1c no patamar de critério para diabetes",
                        f"HbA1c {a1c:.1f}% (≥ 6,5%). Compatível com critério diagnóstico; confirmar e encaminhar.",
                        "Condutas nutricionais devem seguir a orientação médica.", True, FONTE_SBD))
        elif a1c >= 5.7:
            f.append(_a("atencao", "SBD-A1C-02", "HbA1c na faixa de pré-diabetes",
                        f"HbA1c {a1c:.1f}% (5,7–6,4%).", "Reforçar plano alimentar e acompanhamento. Anemias e hemoglobinopatias podem alterar a HbA1c.",
                        True, FONTE_SBD))
    if g is not None and a1c is not None and ((g >= 126) != (a1c >= 6.5)):
        f.append(_a("info", "SBD-DISC-01", "Glicemia e HbA1c discordantes",
                    "Um dos marcadores está no patamar de diabetes e o outro não. Interpretação e eventual TTGO são médicos.",
                    "", True, FONTE_SBD))
    if "homa_ir" in calc:
        f.append(_a("info", "HOMA-01", f"HOMA-IR calculado: {calc['homa_ir']['valor']:.2f}",
                    "Pontos de corte variam com população, método de insulina e referência; interprete com o valor de referência do laboratório/serviço.",
                    "", False, "Matthews et al., Diabetologia 1985 (fórmula)", "literatura"))

    # lipídios: sem pontos de corte numéricos próprios; usa laboratório e meta definida pelo médico
    for chave in ("col_total", "ldl", "tg", "hdl"):
        ln = linhas.get(chave)
        if ln and ln["status"] == "alto" and chave != "hdl":
            f.append(_a("atencao", f"LIP-{chave.upper()}", f"{CATALOGO[chave][0]} acima do intervalo do laboratório",
                        f"{ln['valor']:.0f} mg/dL (referência informada: {_fmt_ref(ln)}).",
                        "Avaliar padrão alimentar (gorduras saturadas/trans, açúcares, álcool, fibras), peso e atividade física; metas por categoria de risco são definidas pelo médico.",
                        True, "Intervalo do laboratório; Diretriz Brasileira de Dislipidemias 2025 (SBC) para metas por risco", "verificada"))
        if ln and ln["status"] == "baixo" and chave == "hdl":
            f.append(_a("info", "LIP-HDL", "HDL abaixo do intervalo do laboratório",
                        f"{ln['valor']:.0f} mg/dL (referência: {_fmt_ref(ln)}).", "Considerar atividade física, qualidade das gorduras e cessação do tabagismo.",
                        False, "Intervalo do laboratório"))
    if meta_ldl:
        ldl = v.get("ldl") or (calc.get("ldl_friedewald") or {}).get("valor")
        if ldl is not None:
            if ldl > meta_ldl:
                f.append(_a("atencao", "LIP-META", f"LDL acima da meta definida pelo médico ({meta_ldl:.0f} mg/dL)",
                            f"LDL {ldl:.0f} mg/dL.", "Reforçar conduta dietética e comunicar a equipe médica.", True,
                            "Meta informada pelo profissional (categoria de risco definida pelo médico)"))
            else:
                f.append(_a("info", "LIP-META", f"LDL dentro da meta definida pelo médico ({meta_ldl:.0f} mg/dL)", f"LDL {ldl:.0f} mg/dL.", "", False,
                            "Meta informada pelo profissional"))
    if v.get("tg") and v["tg"] >= 500:
        f.append(_a("alerta", "LIP-TG-GRAVE", "Triglicerídeos muito elevados", f"Triglicerídeos {v['tg']:.0f} mg/dL.",
                    "Valores muito altos estão associados a risco de pancreatite: encaminhar para avaliação médica e orientar restrição de açúcares/álcool conforme conduta médica.",
                    True, "Prática clínica/diretrizes de dislipidemia (conferir limiar na diretriz vigente)", "literatura"))

    # anemia / ferro
    hb, fer, pcr = v.get("hemoglobina"), v.get("ferritina"), v.get("pcr")
    if hb is not None and "hemoglobina" not in linhas or (linhas.get("hemoglobina") and linhas["hemoglobina"]["status"] is None):
        lim = 12.0 if sexo == "F" else 13.0
        if hb is not None and hb < lim:
            f.append(_a("atencao", "OMS-HB-01", "Hemoglobina abaixo do ponto de corte da OMS para anemia",
                        f"Hemoglobina {hb:.1f} g/dL (< {lim:.1f} g/dL; adultos não gestantes). Sem intervalo do laboratório informado.",
                        "Investigar ferro, B12, folato e causa clínica.", True, "OMS 2011 — concentrações de hemoglobina para anemia", "literatura"))
    elif hb is not None and linhas["hemoglobina"]["status"] == "baixo":
        f.append(_a("atencao", "HB-LAB", "Hemoglobina abaixo do intervalo do laboratório", f"{hb:.1f} g/dL (referência: {_fmt_ref(linhas['hemoglobina'])}).",
                    "Correlacionar com VCM, ferritina, B12 e folato; origem da anemia é determinação médica.", True, "Intervalo do laboratório"))
    ln = linhas.get("ferritina")
    if ln and ln["status"] == "baixo":
        f.append(_a("atencao", "FER-BAIXA", "Ferritina abaixo do intervalo do laboratório",
                    f"{fer:.0f} ng/mL (referência: {_fmt_ref(ln)}). Compatível com depleção de ferro.",
                    "Avaliar ingestão e biodisponibilidade de ferro (heme/não heme, vitamina C); causa da perda deve ser investigada pelo médico.",
                    True, "Intervalo do laboratório; interpretação de ferritina com marcadores inflamatórios"))
    if ln and ln["status"] == "alto" and pcr is not None and linhas.get("pcr", {}).get("status") == "alto":
        f.append(_a("info", "FER-INFL", "Ferritina elevada com PCR elevada",
                    "A ferritina é reagente de fase aguda e pode estar elevada por inflamação; não interpretar isoladamente como sobrecarga de ferro.",
                    "Correlacionar com saturação de transferrina e quadro clínico.", True, "Interpretação de ferritina com inflamação"))
    elif ln and ln["status"] == "normal" and linhas.get("pcr", {}).get("status") == "alto":
        f.append(_a("info", "FER-MASC", "Ferritina normal com PCR elevada",
                    "Na inflamação, a ferritina pode mascarar uma deficiência de ferro.", "Considerar saturação de transferrina e hemograma.",
                    False, "Interpretação de ferritina com inflamação"))
    if linhas.get("b12", {}).get("status") == "baixo":
        f.append(_a("atencao", "B12-BAIXA", "Vitamina B12 abaixo do intervalo do laboratório",
                    f"{v['b12']:.0f} pg/mL (referência: {_fmt_ref(linhas['b12'])}).",
                    "Avaliar dieta (especialmente vegetarianos/veganos, idosos), uso de metformina/IBP e absorção; encaminhar.", True, "Intervalo do laboratório"))
    if linhas.get("vitd", {}).get("status") == "baixo":
        f.append(_a("atencao", "VITD-BAIXA", "Vitamina D abaixo do intervalo do laboratório",
                    f"{v['vitd']:.0f} ng/mL (referência: {_fmt_ref(linhas['vitd'])}).",
                    "Avaliar exposição solar segura e ingestão; suplementação conforme prescrição/protocolo e metas por grupo de risco (consultar posicionamento SBPC/ML-SBEM vigente).",
                    False, "Intervalo do laboratório"))
    if linhas.get("folato", {}).get("status") == "baixo":
        f.append(_a("atencao", "FOL-BAIXO", "Folato abaixo do intervalo do laboratório", f"{v['folato']:.1f} ng/mL.",
                    "Avaliar ingestão de folatos/ácido fólico e outros fatores (álcool, medicamentos).", False, "Intervalo do laboratório"))

    # fígado, rim, tireoide, ácido úrico: laboratório
    hep = [CATALOGO[k][0] for k in ("tgo", "tgp", "ggt") if linhas.get(k, {}).get("status") == "alto"]
    if hep:
        f.append(_a("atencao", "HEP-LAB", "Enzimas hepáticas acima do intervalo do laboratório", ", ".join(hep) + ".",
                    "Rever álcool, medicamentos/suplementos/fitoterápicos, excesso de peso e padrão alimentar; causa é determinação médica.",
                    True, "Intervalo do laboratório"))
    if "egfr" in calc:
        t = calc["egfr"]["valor"]
        if t < 60:
            f.append(_a("alerta", "REN-TFG", f"TFG estimada reduzida ({t:.0f} mL/min/1,73 m²)",
                        f"Estágio {calc['egfr']['extra']}. A definição de doença renal crônica exige persistência por > 3 meses e avaliação de albuminúria.",
                        "Antes de ajustar proteína, potássio, fósforo e suplementos/fitoterápicos, alinhar conduta com o médico (nefrologia).",
                        True, "KDIGO 2012/2024 (estadiamento); CKD-EPI 2021", "verificada"))
        elif t < 90:
            f.append(_a("info", "REN-TFG-LEVE", f"TFG estimada {t:.0f} mL/min/1,73 m²", "Levemente reduzida; só tem significado clínico com outros marcadores de lesão renal.",
                        "", False, "KDIGO; CKD-EPI 2021"))
    if linhas.get("tsh", {}).get("status") in ("alto", "baixo"):
        f.append(_a("atencao", "TSH-LAB", "TSH fora do intervalo do laboratório",
                    f"TSH {v['tsh']:.2f} µUI/mL (referência: {_fmt_ref(linhas['tsh'])}).",
                    "Interferem na avaliação do gasto energético e do peso; encaminhar para endocrinologia. Não ajustar conduta nutricional presumindo causa tireoidiana.",
                    True, "Intervalo do laboratório"))
    if linhas.get("acido_urico", {}).get("status") == "alto":
        f.append(_a("info", "URICO-LAB", "Ácido úrico acima do intervalo do laboratório", f"{v['acido_urico']:.1f} mg/dL.",
                    "Rever álcool (especialmente cerveja), frutose/bebidas açucaradas, hidratação e peso.", False, "Intervalo do laboratório"))
    if idade is not None and idade >= 0 and not f:
        f.append(_a("info", "SEM-ACHADOS", "Nenhum achado automático para correlação",
                    "Isso não exclui alterações: a leitura clínica integral é do profissional.", "", False, "—"))
    ordem = {"alerta": 0, "atencao": 1, "info": 2}
    f.sort(key=lambda x: ordem[x["nivel"]])
    return f


def _fmt_ref(ln):
    mn, mx = ln.get("min"), ln.get("max")
    un = ln.get("unidade", "")
    if mn is not None and mx is not None:
        return f"{mn:g}–{mx:g} {un}"
    if mx is not None:
        return f"até {mx:g} {un}"
    if mn is not None:
        return f"a partir de {mn:g} {un}"
    return "não informada"


def avaliar(itens, idade, sexo, meta_ldl=None):
    """itens: {chave: {"v","u","min","max"}} já na unidade padrão. Retorna (linhas, calculados, achados)."""
    v = {k: it["v"] for k, it in itens.items() if it.get("v") is not None}
    linhas = {}
    for k, it in itens.items():
        if k not in CATALOGO or it.get("v") is None:
            continue
        linhas[k] = {"chave": k, "nome": CATALOGO[k][0], "categoria": CATALOGO[k][1], "valor": it["v"],
                     "unidade": CATALOGO[k][2], "min": it.get("min"), "max": it.get("max"),
                     "status": classificar(it["v"], it.get("min"), it.get("max")), "casas": CATALOGO[k][4]}
    calc = calculados(v, idade, sexo)
    return linhas, calc, regras(v, linhas, calc, idade, sexo, meta_ldl)


def montar_itens(form):
    """Lê o formulário -> {chave: {v,u,min,max,v0,u0}} na unidade padrão. Levanta ValueError."""
    itens = {}
    for k, (nome, _cat, padrao, alt, _c) in CATALOGO.items():
        bruto = (form.get(f"v_{k}") or "").strip().replace(",", ".")
        if not bruto:
            continue
        try:
            val = float(bruto)
        except ValueError:
            raise ValueError(f"{nome}: valor inválido.")
        if not math.isfinite(val) or val < 0 or val > 1_000_000:
            raise ValueError(f"{nome}: valor fora do intervalo.")
        un = form.get(f"u_{k}") or padrao
        if un != padrao and un not in alt:
            raise ValueError(f"{nome}: unidade inválida.")
        item = {"v": para_padrao(k, val, un), "v0": val, "u0": un}
        for lado in ("min", "max"):
            r = (form.get(f"{lado}_{k}") or "").strip().replace(",", ".")
            if r:
                try:
                    item[lado] = para_padrao(k, float(r), un)
                except ValueError:
                    raise ValueError(f"{nome}: referência {lado} inválida.")
        if item.get("min") is not None and item.get("max") is not None and item["min"] > item["max"]:
            raise ValueError(f"{nome}: referência mínima maior que a máxima.")
        itens[k] = item
    if not itens:
        raise ValueError("Informe pelo menos um resultado.")
    return itens
