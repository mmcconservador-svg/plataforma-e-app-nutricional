"""Equações de gasto energético, fatores de atividade/injúria e composição corporal.

Cada equação traz a fonte e o estado de verificação:
  verificada  = coeficientes conferidos em fonte primária/secundária consultada durante o desenvolvimento
  literatura  = fórmula clássica amplamente publicada; conferir na fonte antes de uso clínico
Equações que não foram verificadas e não são universais (Henry/Oxford, Owen, Ireton-Jones, Chumlea) NÃO estão incluídas.
Resultados são ESTIMATIVAS de apoio ao nutricionista.
"""
import math

# ───────────────────────── fatores de atividade ─────────────────────────
# chave: (rótulo, multiplicador, grupo, fonte)
FATORES_ATIVIDADE = {
    # clássicos (Harris-Benedict/Katch-McArdle, muito usados na prática)
    "sedentario": ("Sedentário (1,2)", 1.2, "Clássicos", "Harris-Benedict (convenção)"),
    "leve": ("Levemente ativo (1,375)", 1.375, "Clássicos", "Harris-Benedict (convenção)"),
    "moderado": ("Moderadamente ativo (1,55)", 1.55, "Clássicos", "Harris-Benedict (convenção)"),
    "intenso": ("Muito ativo (1,725)", 1.725, "Clássicos", "Harris-Benedict (convenção)"),
    "muito_intenso": ("Extremamente ativo (1,9)", 1.9, "Clássicos", "Harris-Benedict (convenção)"),
    # FAO/WHO/UNU 2004 (faixas de PAL; valor = ponto médio, ajustável em "fator personalizado")
    "fao_sedentario": ("FAO/OMS 2004 — sedentário/leve (1,40–1,69; usa 1,55)", 1.55, "FAO/OMS 2004", "FAO/WHO/UNU 2004"),
    "fao_ativo": ("FAO/OMS 2004 — ativo/moderado (1,70–1,99; usa 1,85)", 1.85, "FAO/OMS 2004", "FAO/WHO/UNU 2004"),
    "fao_vigoroso": ("FAO/OMS 2004 — vigoroso (2,00–2,40; usa 2,20)", 2.20, "FAO/OMS 2004", "FAO/WHO/UNU 2004"),
    # paciente hospitalizado/restrito ao leito
    "leito": ("Acamado (1,2)", 1.2, "Clínico", "tabela de nutrição clínica"),
    "leito_movel": ("Acamado com mobilidade (1,25)", 1.25, "Clínico", "tabela de nutrição clínica"),
    "ambulante": ("Ambulante (1,3)", 1.3, "Clínico", "tabela de nutrição clínica"),
}

# Fatores de injúria/estresse (aplicados sobre TMB × atividade). Faixas variam entre fontes: confirme e justifique.
FATORES_INJURIA = {
    "nenhum": ("Sem injúria / paciente não complicado (1,0)", 1.0),
    "pos_op_cancer": ("Pós-operatório de câncer (1,1)", 1.1),
    "fratura": ("Fratura (1,2)", 1.2),
    "sepse": ("Sepse (1,3)", 1.3),
    "peritonite": ("Peritonite (1,4)", 1.4),
    "multitrauma_reab": ("Multitrauma em reabilitação (1,5)", 1.5),
    "multitrauma_sepse": ("Multitrauma com sepse (1,6)", 1.6),
    "queim_30_50": ("Queimadura 30–50% da SC (1,7)", 1.7),
    "queim_50_70": ("Queimadura 50–70% da SC (1,8)", 1.8),
    "queim_70_90": ("Queimadura 70–90% da SC (2,0)", 2.0),
}

# Categorias de atividade das DRI/IOM 2005 (faixas de PAL)
DRI_PA = (("sedentario", 1.0, 1.4), ("pouco_ativo", 1.4, 1.6), ("ativo", 1.6, 1.9), ("muito_ativo", 1.9, 99))
DRI_PA_ROTULO = {"sedentario": "sedentário", "pouco_ativo": "pouco ativo", "ativo": "ativo", "muito_ativo": "muito ativo"}


def categoria_dri(fa):
    for nome, lo, hi in DRI_PA:
        if lo <= fa < hi:
            return nome
    return "muito_ativo"


# ───────────────────────── pesos de referência ─────────────────────────
def peso_ideal(altura_cm, imc_alvo=22.0):
    h = altura_cm / 100
    return imc_alvo * h * h


def peso_ajustado(peso, ideal, fator=0.25):
    """Peso ajustado para obesidade: PI + 0,25 × (peso atual − PI)."""
    return ideal + fator * (peso - ideal)


# ───────────────────────── TMB / GET ─────────────────────────
class Entrada:
    """Dados para as equações. mlg = massa livre de gordura (kg) ou None."""
    def __init__(self, sexo, peso, altura_cm, idade, mlg=None, fa=1.2, imc=None):
        self.sexo, self.peso, self.altura_cm, self.idade = sexo, peso, altura_cm, idade
        self.mlg, self.fa = mlg, fa
        self.imc = imc if imc is not None else peso / ((altura_cm / 100) ** 2)

    @property
    def h_m(self):
        return self.altura_cm / 100


def _mifflin(e):
    b = 10 * e.peso + 6.25 * e.altura_cm - 5 * e.idade
    return b + 5 if e.sexo == "M" else b - 161


def _hb1919(e):
    if e.sexo == "M":
        return 66.4730 + 13.7516 * e.peso + 5.0033 * e.altura_cm - 6.7550 * e.idade
    return 655.0955 + 9.5634 * e.peso + 1.8496 * e.altura_cm - 4.6756 * e.idade


def _hb1984(e):
    if e.sexo == "M":
        return 88.362 + 13.397 * e.peso + 4.799 * e.altura_cm - 5.677 * e.idade
    return 447.593 + 9.247 * e.peso + 3.098 * e.altura_cm - 4.330 * e.idade


def _katch(e):
    return 370 + 21.6 * e.mlg


def _cunningham(e):
    return 500 + 22 * e.mlg


def _tinsley_peso(e):
    return 24.8 * e.peso + 10


def _tinsley_mlg(e):
    return 25.9 * e.mlg + 284


# Schofield (FAO/WHO/UNU 1985), kcal/dia, peso em kg. (limite inferior, limite superior, a, b)
SCHOFIELD = {
    "M": [(0, 3, 59.512, -30.4), (3, 10, 22.706, 504.3), (10, 18, 17.686, 658.2),
          (18, 30, 15.057, 692.2), (30, 60, 11.472, 873.1), (60, 200, 11.711, 587.7)],
    "F": [(0, 3, 58.317, -31.1), (3, 10, 20.315, 485.9), (10, 18, 13.384, 692.6),
          (18, 30, 14.818, 486.6), (30, 60, 8.126, 845.6), (60, 200, 9.082, 658.5)],
}


def _schofield(e):
    for lo, hi, a, b in SCHOFIELD[e.sexo]:
        if lo <= e.idade < hi:
            return a * e.peso + b
    raise ValueError("Idade fora do intervalo.")


def _eer_dri(e):
    """EER (IOM/DRI 2005) para adultos ≥ 19 anos: já inclui a atividade (retorna o GET)."""
    pa = categoria_dri(e.fa)
    idx = ("sedentario", "pouco_ativo", "ativo", "muito_ativo").index(pa)
    h, w, a = e.h_m, e.peso, e.idade
    if e.imc > 25:
        if e.sexo == "M":
            return 1085.6 - 10.08 * a + (1.00, 1.12, 1.29, 1.59)[idx] * (13.7 * w + 416 * h)
        return 447.6 - 7.95 * a + (1.00, 1.16, 1.27, 1.44)[idx] * (11.4 * w + 619 * h)
    if e.sexo == "M":
        return 661.8 - 9.53 * a + (1.00, 1.11, 1.25, 1.48)[idx] * (15.91 * w + 539.6 * h)
    return 354.1 - 6.91 * a + (1.00, 1.12, 1.27, 1.45)[idx] * (9.36 * w + 726 * h)


# chave: dict(nome, tipo ("tmb"|"get"), func, idade_min, idade_max, requer_mlg, fonte, status, nota)
EQUACOES = {
    "mifflin": dict(
        nome="Mifflin-St Jeor (1990)", tipo="tmb", func=_mifflin, idade_min=18, idade_max=120, mlg=False,
        fonte="Mifflin et al., Am J Clin Nutr 1990", status="verificada",
        nota="Padrão para adultos; boa acurácia em sobrepeso/obesidade. Não validada em crianças, gestantes ou atletas."),
    "hb1919": dict(
        nome="Harris-Benedict original (1919)", tipo="tmb", func=_hb1919, idade_min=18, idade_max=120, mlg=False,
        fonte="Harris & Benedict, 1919", status="literatura",
        nota="Tende a superestimar o gasto em populações atuais."),
    "hb1984": dict(
        nome="Harris-Benedict revisada (Roza & Shizgal, 1984)", tipo="tmb", func=_hb1984, idade_min=18, idade_max=120,
        mlg=False, fonte="Roza & Shizgal, Am J Clin Nutr 1984", status="literatura",
        nota="Revisão dos coeficientes originais; ainda pode superestimar."),
    "schofield": dict(
        nome="Schofield / FAO-OMS-UNU (1985) — só peso", tipo="tmb", func=_schofield, idade_min=0, idade_max=120,
        mlg=False, fonte="FAO/WHO/UNU 1985 (Schofield), Tabela 5.2 — fao.org", status="verificada",
        nota="Única equação desta lista com faixas pediátricas; erro maior em obesos e idosos."),
    "eer_dri": dict(
        nome="EER — DRI/IOM 2005 (adultos ≥ 19 anos)", tipo="get", func=_eer_dri, idade_min=19, idade_max=120, mlg=False,
        fonte="IOM, Dietary Reference Intakes 2005 (eq. de manutenção de peso)", status="verificada",
        nota="Já inclui o nível de atividade (categorias DRI derivadas do fator escolhido); não multiplica por FA."),
    "katch": dict(
        nome="Katch-McArdle (370 + 21,6 × MLG)", tipo="tmb", func=_katch, idade_min=14, idade_max=120, mlg=True,
        fonte="McArdle, Katch & Katch", status="literatura",
        nota="Exige massa livre de gordura confiável (dobras, bioimpedância, DXA)."),
    "cunningham": dict(
        nome="Cunningham (500 + 22 × MLG)", tipo="tmb", func=_cunningham, idade_min=14, idade_max=120, mlg=True,
        fonte="Cunningham, Am J Clin Nutr 1980", status="literatura",
        nota="Indicada para pessoas ativas/atletas com MLG conhecida."),
    "tinsley_peso": dict(
        nome="Tinsley (2019) — 24,8 × peso + 10", tipo="tmb", func=_tinsley_peso, idade_min=18, idade_max=120, mlg=False,
        fonte="Tinsley et al., J Int Soc Sports Nutr 2019 (PMID 30240568)", status="verificada",
        nota="Desenvolvida em atletas de físico musculoso; não generalizar para a população geral."),
    "tinsley_mlg": dict(
        nome="Tinsley (2019) — 25,9 × MLG + 284", tipo="tmb", func=_tinsley_mlg, idade_min=18, idade_max=120, mlg=True,
        fonte="Tinsley et al., J Int Soc Sports Nutr 2019 (PMID 30240568)", status="verificada",
        nota="Atletas de físico musculoso; exige MLG."),
}
PADRAO = "mifflin"


def aplicavel(chave, e):
    """(ok, motivo). Não bloqueia por julgamento clínico — só por impossibilidade matemática/limite documentado."""
    q = EQUACOES[chave]
    if q["mlg"] and not e.mlg:
        return False, "requer massa livre de gordura (informe %G ou dobras)"
    if not q["idade_min"] <= e.idade <= q["idade_max"]:
        return False, f"idade fora da faixa da equação ({q['idade_min']}–{q['idade_max']} anos)"
    return True, ""


def calcular_equacao(chave, e, fator_injuria=1.0):
    """Retorna dict(tmb|None, get, fa_usado) para a equação escolhida."""
    q = EQUACOES[chave]
    v = q["func"](e)
    if q["tipo"] == "get":
        return {"tmb": None, "get": v * fator_injuria, "fa_usado": None}
    return {"tmb": v, "get": v * e.fa * fator_injuria, "fa_usado": e.fa}


def comparar_todas(e, fator_injuria=1.0):
    saida = []
    for k, q in EQUACOES.items():
        ok, motivo = aplicavel(k, e)
        item = {"chave": k, "nome": q["nome"], "status": q["status"], "ok": ok, "motivo": motivo}
        if ok:
            r = calcular_equacao(k, e, fator_injuria)
        item.update({"tmb": r["tmb"], "get_kcal": r["get"], "fa_usado": r["fa_usado"]})
        saida.append(item)
    return saida


# ───────────────────────── composição corporal ─────────────────────────
def siri(dc):
    return 495 / dc - 450


def brozek(dc):
    return 457 / dc - 414.2


def faulkner(soma4):
    """%G = Σ(tríceps, subescapular, supra-ilíaca, abdominal) × 0,153 + 5,783 (retorna %G direto)."""
    return soma4 * 0.153 + 5.783


# protocolo: (nome, sexos, dobras exigidas, fonte, status)
DOBRAS = {
    "tr": "Tríceps", "bi": "Bíceps", "se": "Subescapular", "si": "Supra-ilíaca", "ab": "Abdominal",
    "pe": "Peitoral", "ax": "Axilar média", "co": "Coxa (medial)", "pm": "Perna medial",
}
PROTOCOLOS = {
    "pollock3": dict(nome="Jackson & Pollock — 3 dobras", status="verificada",
                     fonte="Jackson & Pollock 1978 (homens); Jackson, Pollock & Ward 1980 (mulheres)",
                     sitios={"M": ("pe", "ab", "co"), "F": ("tr", "si", "co")}, idade=(18, 61)),
    "pollock7": dict(nome="Jackson & Pollock — 7 dobras", status="verificada",
                     fonte="Jackson & Pollock 1978; Jackson, Pollock & Ward 1980",
                     sitios={"M": ("pe", "ax", "tr", "se", "ab", "si", "co"), "F": ("pe", "ax", "tr", "se", "ab", "si", "co")},
                     idade=(18, 61)),
    "durnin": dict(nome="Durnin & Womersley — 4 dobras", status="verificada",
                   fonte="Durnin & Womersley, Br J Nutr 1974",
                   sitios={"M": ("bi", "tr", "se", "si"), "F": ("bi", "tr", "se", "si")}, idade=(16, 120)),
    "guedes": dict(nome="Guedes — 3 dobras (brasileiros, jovens)", status="verificada",
                   fonte="Guedes, 1985/1991 (equações citadas em Rev Bras Cineantropom Desempenho Hum 2008)",
                   sitios={"M": ("tr", "si", "ab"), "F": ("se", "si", "co")}, idade=(17, 28)),
    "petroski": dict(nome="Petroski — 4 dobras (brasileiros)", status="verificada",
                     fonte="Petroski, 1995 (equações citadas em Rev Bras Cineantropom Desempenho Hum 2008)",
                     sitios={"M": ("tr", "se", "si", "pm"), "F": ("ax", "si", "co", "pm")}, idade=(18, 66)),
    "faulkner": dict(nome="Faulkner — 4 dobras (%G direto)", status="literatura",
                     fonte="Faulkner, 1968", sitios={"M": ("tr", "se", "si", "ab"), "F": ("tr", "se", "si", "ab")},
                     idade=(18, 120)),
}

# Durnin & Womersley: (idade_min, idade_max): {sexo: (c, m)}
_DW = [((0, 17), {"M": (1.1533, 0.0643), "F": (1.1369, 0.0598)}),
       ((17, 20), {"M": (1.1620, 0.0630), "F": (1.1549, 0.0678)}),
       ((20, 30), {"M": (1.1631, 0.0632), "F": (1.1599, 0.0717)}),
       ((30, 40), {"M": (1.1422, 0.0544), "F": (1.1423, 0.0632)}),
       ((40, 50), {"M": (1.1620, 0.0700), "F": (1.1333, 0.0612)}),
       ((50, 200), {"M": (1.1715, 0.0779), "F": (1.1339, 0.0645)})]


def _densidade(protocolo, sexo, idade, d):
    s = sum(d[k] for k in PROTOCOLOS[protocolo]["sitios"][sexo])
    if protocolo == "pollock3":
        if sexo == "M":
            return 1.109380 - 0.0008267 * s + 0.0000016 * s * s - 0.0002574 * idade
        return 1.0994921 - 0.0009929 * s + 0.0000023 * s * s - 0.0001392 * idade
    if protocolo == "pollock7":
        if sexo == "M":
            return 1.112 - 0.00043499 * s + 0.00000055 * s * s - 0.00028826 * idade
        return 1.097 - 0.00046971 * s + 0.00000056 * s * s - 0.00012828 * idade
    if protocolo == "durnin":
        for (lo, hi), c in _DW:
            if lo <= idade < hi:
                a, b = c[sexo]
                return a - b * math.log10(s)
    if protocolo == "guedes":
        if sexo == "M":
            return 1.1714 - 0.0671 * math.log10(s)
        return 1.1665 - 0.0706 * math.log10(s)
    if protocolo == "petroski":
        if sexo == "M":
            return 1.107269 - 0.0008120 * s + 0.0000021 * s * s - 0.0004176 * idade
        return 1.19547130 - 0.07513507 * math.log10(s) - 0.00041072 * idade
    raise ValueError("Protocolo inválido.")


def gordura_por_dobras(protocolo, sexo, idade, dobras, conversao="siri"):
    """dobras: dict chave->mm. Retorna dict(pct, densidade|None, soma, avisos)."""
    if protocolo not in PROTOCOLOS:
        raise ValueError("Protocolo de dobras inválido.")
    p = PROTOCOLOS[protocolo]
    faltam = [DOBRAS[k] for k in p["sitios"][sexo] if not dobras.get(k)]
    if faltam:
        raise ValueError("Informe as dobras: " + ", ".join(faltam) + ".")
    soma = sum(dobras[k] for k in p["sitios"][sexo])
    avisos = []
    lo, hi = p["idade"]
    if not lo <= idade <= hi:
        avisos.append(f"Idade fora da faixa de validade do protocolo ({lo}–{hi} anos): interprete com cautela.")
    if protocolo == "faulkner":
        return {"pct": faulkner(soma), "densidade": None, "soma": soma, "avisos": avisos}
    dc = _densidade(protocolo, sexo, idade, dobras)
    if not 0.9 < dc < 1.2:
        raise ValueError("Densidade corporal fora do intervalo plausível: confira as dobras.")
    pct = siri(dc) if conversao == "siri" else brozek(dc)
    return {"pct": pct, "densidade": dc, "soma": soma, "avisos": avisos}


# ───────────────────────── braço ─────────────────────────
def circ_muscular_braco(cb_cm, dct_mm):
    """CMB (cm) = CB − π × DCT(cm)."""
    return cb_cm - math.pi * dct_mm / 10


def area_muscular_braco(cmb_cm):
    return cmb_cm ** 2 / (4 * math.pi)


def area_muscular_braco_corrigida(cmb_cm, sexo):
    """AMBc (Heymsfield et al. 1982): −10 cm² (homens), −6,5 cm² (mulheres)."""
    return area_muscular_braco(cmb_cm) - (10 if sexo == "M" else 6.5)
