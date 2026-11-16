"""Motor de cálculos nutricionais (apoio ao profissional; não substitui a conduta clínica)."""

FATORES_ATIVIDADE = {
    "sedentario": ("Sedentário (1,2)", 1.2),
    "leve": ("Levemente ativo (1,375)", 1.375),
    "moderado": ("Moderadamente ativo (1,55)", 1.55),
    "intenso": ("Muito ativo (1,725)", 1.725),
    "muito_intenso": ("Extremamente ativo (1,9)", 1.9),
}

CONDICOES = {
    "nenhuma": "Nenhuma condição especial",
    "gestante": "Gestante / lactante",
    "atleta": "Atleta / alto rendimento",
    "clinica": "Condição clínica específica (renal, oncológica, etc.)",
}


def idade_em(nascimento, ref):
    if not nascimento:
        return None
    anos = ref.year - nascimento.year
    if (ref.month, ref.day) < (nascimento.month, nascimento.day):
        anos -= 1
    return anos


def imc(peso, altura_cm):
    h = altura_cm / 100.0
    return peso / (h * h)


def classificar_imc(valor, idade):
    if idade is not None and idade < 18:
        return "Criança/adolescente: avaliar por percentil/escore-z (curvas OMS); classificação adulta não se aplica"
    if idade is not None and idade >= 60:  # Lipschitz
        if valor < 22:
            return "Baixo peso (idoso)"
        if valor <= 27:
            return "Eutrofia (idoso)"
        return "Sobrepeso (idoso)"
    if valor < 18.5:
        return "Baixo peso"
    if valor < 25:
        return "Eutrofia"
    if valor < 30:
        return "Sobrepeso"
    if valor < 35:
        return "Obesidade grau I"
    if valor < 40:
        return "Obesidade grau II"
    return "Obesidade grau III"


def risco_cintura(sexo, cintura):
    lim = (94, 102) if sexo == "M" else (80, 88)
    if cintura >= lim[1]:
        return "Risco muito aumentado"
    if cintura >= lim[0]:
        return "Risco aumentado"
    return "Sem risco aumentado"


def risco_rcq(sexo, rcq):
    lim = 0.90 if sexo == "M" else 0.85
    return "Risco aumentado" if rcq > lim else "Sem risco aumentado"


def risco_rce(rce):
    return "Risco aumentado (≥ 0,5)" if rce >= 0.5 else "Sem risco aumentado"


def tmb_mifflin(sexo, peso, altura_cm, idade):
    base = 10 * peso + 6.25 * altura_cm - 5 * idade
    return base + 5 if sexo == "M" else base - 161


def avaliar(av, paciente):
    """Calcula todos os indicadores de uma avaliação. Retorna dict pronto para exibição."""
    idade = idade_em(paciente.nascimento, av.data)
    r = {"idade": idade, "avisos": []}
    r["imc"] = imc(av.peso, av.altura_cm)
    r["imc_class"] = classificar_imc(r["imc"], idade)
    r["cintura_risco"] = risco_cintura(paciente.sexo, av.cintura)
    r["rcq"] = av.cintura / av.quadril
    r["rcq_risco"] = risco_rcq(paciente.sexo, r["rcq"])
    r["rce"] = av.cintura / av.altura_cm
    r["rce_risco"] = risco_rce(r["rce"])
    if av.gordura_pct is not None:
        r["massa_gorda"] = av.peso * av.gordura_pct / 100
        r["massa_magra"] = av.peso - r["massa_gorda"]
    if idade is None:
        r["avisos"].append("Data de nascimento ausente: TMB/GET/VET não calculados.")
        return r
    r["tmb"] = tmb_mifflin(paciente.sexo, av.peso, av.altura_cm, idade)
    r["fa"] = FATORES_ATIVIDADE[av.fator][1]
    r["get_kcal"] = r["tmb"] * r["fa"]
    r["vet"] = r["get_kcal"] + av.ajuste_kcal
    soma = av.pct_carb + av.pct_prot + av.pct_lip
    r["macros"] = {
        "carb_g": r["vet"] * av.pct_carb / 100 / 4,
        "prot_g": r["vet"] * av.pct_prot / 100 / 4,
        "lip_g": r["vet"] * av.pct_lip / 100 / 9,
        "soma_pct": soma,
    }
    r["prot_g_kg"] = r["macros"]["prot_g"] / av.peso
    r["agua_ml"] = 35 * av.peso
    r["fibra_g"] = 14 * r["vet"] / 1000  # DRI/IA: 14 g por 1.000 kcal
    if idade < 18:
        r["avisos"].append("Menor de 18 anos: a equação de Mifflin-St Jeor não é validada para crianças e adolescentes; use equações/DRI pediátricas.")
    if idade >= 60:
        r["avisos"].append("Idoso: estimativas de gasto energético têm maior erro; considere composição corporal e estado clínico.")
    if av.condicao == "gestante":
        r["avisos"].append("Gestação/lactação: use as necessidades energéticas e de nutrientes específicas das DRI; este cálculo não se aplica.")
    elif av.condicao == "atleta":
        r["avisos"].append("Atleta: a equação pode subestimar o gasto; considere equação baseada em massa magra e carga de treino.")
    elif av.condicao == "clinica":
        r["avisos"].append("Condição clínica específica: ajuste meta energética e de macronutrientes ao quadro e aos exames.")
    if r["imc"] >= 30:
        r["avisos"].append("IMC ≥ 30: avalie a necessidade de usar peso ajustado/ideal na estimativa energética.")
    if abs(soma - 100) > 0.5:
        r["avisos"].append(f"A soma dos percentuais de macronutrientes é {soma:.0f}% (deveria ser 100%).")
    return r
