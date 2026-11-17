"""Motor de cálculos nutricionais (apoio ao profissional; não substitui a conduta clínica)."""

import equacoes as Q

FATORES_ATIVIDADE = Q.FATORES_ATIVIDADE

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
    extras = av.extras_dict
    r["imc"] = imc(av.peso, av.altura_cm)
    r["imc_class"] = classificar_imc(r["imc"], idade)
    r["cintura_risco"] = risco_cintura(paciente.sexo, av.cintura)
    r["rcq"] = av.cintura / av.quadril
    r["rcq_risco"] = risco_rcq(paciente.sexo, r["rcq"])
    r["rce"] = av.cintura / av.altura_cm
    r["rce_risco"] = risco_rce(r["rce"])

    # composição corporal: %G informado ou calculado por dobras
    gordura = av.gordura_pct
    if extras.get("protocolo") and extras.get("dobras") and idade is not None:
        try:
            d = Q.gordura_por_dobras(extras["protocolo"], paciente.sexo, idade, extras["dobras"],
                                     extras.get("conversao", "siri"))
            d["protocolo_nome"] = Q.PROTOCOLOS[extras["protocolo"]]["nome"]
            r["dobras"] = d
            r["avisos"] += d["avisos"]
            if gordura is None:
                gordura = d["pct"]
        except ValueError as e:
            r["avisos"].append(str(e))
    r["gordura_pct"] = gordura
    if gordura is not None:
        r["massa_gorda"] = av.peso * gordura / 100
        r["massa_magra"] = av.peso - r["massa_gorda"]
    if extras.get("cb") and extras.get("dct"):
        cmb = Q.circ_muscular_braco(extras["cb"], extras["dct"])
        r["braco"] = {"cb": extras["cb"], "dct": extras["dct"], "cmb": cmb,
                      "amb": Q.area_muscular_braco(cmb),
                      "ambc": Q.area_muscular_braco_corrigida(cmb, paciente.sexo)}

    if idade is None:
        r["avisos"].append("Data de nascimento ausente: TMB/GET/VET não calculados.")
        return r

    # peso de referência
    ideal = Q.peso_ideal(av.altura_cm)
    base = av.peso_base or "atual"
    peso_calc = {"atual": av.peso, "ideal": ideal, "ajustado": Q.peso_ajustado(av.peso, ideal)}.get(base, av.peso)
    r.update(peso_ideal=ideal, peso_ajustado=Q.peso_ajustado(av.peso, ideal), peso_base=base, peso_calc=peso_calc)

    fa = av.fator_valor or Q.FATORES_ATIVIDADE[av.fator][1]
    fi = av.fator_injuria or 1.0
    mlg = r.get("massa_magra")
    ent = Q.Entrada(paciente.sexo, peso_calc, av.altura_cm, idade, mlg=mlg, fa=fa, imc=r["imc"])
    chave = av.equacao or ("schofield" if idade < 18 else Q.PADRAO)
    if chave not in Q.EQUACOES:
        chave = Q.PADRAO
    eq = Q.EQUACOES[chave]
    r["equacao"] = {"chave": chave, **{k: eq[k] for k in ("nome", "fonte", "status", "nota")}}
    r["comparacao"] = Q.comparar_todas(ent, fi)
    ok, motivo = Q.aplicavel(chave, ent)
    if not ok:
        r["avisos"].append(f"Equação “{eq['nome']}” não aplicável: {motivo}. TMB/GET/VET não calculados.")
        return r
    res = Q.calcular_equacao(chave, ent, fi)
    r["fator_injuria"] = fi
    r["fa"] = fa
    r["tmb"] = res["tmb"]
    r["get_kcal"] = res["get"]
    r["vet"] = res["get"] + av.ajuste_kcal
    if eq["tipo"] == "get":
        r["fa_dri"] = Q.DRI_PA_ROTULO[Q.categoria_dri(fa)]
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
    if eq["status"] == "literatura":
        r["avisos"].append("Equação publicada na literatura clássica: confira os coeficientes na fonte citada antes do uso clínico.")
    if base != "atual":
        r["avisos"].append(f"TMB calculada com peso {base} ({peso_calc:.1f} kg).")
    elif r["imc"] >= 30:
        r["avisos"].append("IMC ≥ 30: avalie usar peso ajustado/ideal na estimativa energética (campo “Peso usado no cálculo”).")
    if idade < 18 and chave != "schofield":
        r["avisos"].append("Menor de 18 anos: use equações/DRI pediátricas; esta equação não é validada para crianças e adolescentes.")
    if idade >= 60:
        r["avisos"].append("Idoso: estimativas de gasto energético têm maior erro; considere composição corporal e estado clínico.")
    if av.condicao == "gestante":
        r["avisos"].append("Gestação/lactação: use as necessidades energéticas e de nutrientes específicas das DRI; este cálculo não se aplica.")
    elif av.condicao == "atleta" and chave not in ("tinsley_peso", "tinsley_mlg", "cunningham", "katch"):
        r["avisos"].append("Atleta: a equação pode subestimar o gasto; considere equação baseada em massa livre de gordura e carga de treino.")
    elif av.condicao == "clinica":
        r["avisos"].append("Condição clínica específica: ajuste meta energética e de macronutrientes ao quadro e aos exames.")
    if abs(soma - 100) > 0.5:
        r["avisos"].append(f"A soma dos percentuais de macronutrientes é {soma:.0f}% (deveria ser 100%).")
    return r
