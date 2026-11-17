"""Valores Diários de Referência (VDR) da ANVISA — população adulta, alimentos em geral.

Fonte: Instrução Normativa IN nº 75/2020 (Anvisa), Anexo II, em apoio à RDC nº 429/2020.
Valores conferidos no texto da IN publicado pela Anvisa durante o desenvolvimento; confirme no
Diário Oficial da União antes de uso regulatório. Valores específicos por faixa etária/gestação
(Anexo VIII) NÃO estão incluídos por não terem sido integralmente verificados.
"""

VD_FONTE = "Anvisa — IN 75/2020, Anexo II (adultos; alimentos em geral)"

# macros: chave do cálculo do cardápio -> (rótulo, valor, unidade)
VD_MACROS = {
    "kcal": ("Valor energético", 2000, "kcal"),
    "c": ("Carboidratos", 300, "g"),
    "p": ("Proteínas", 50, "g"),
    "l": ("Gorduras totais", 65, "g"),
    "f": ("Fibras alimentares", 25, "g"),
}

# micronutrientes: (rótulo, VDR, unidade, [chaves possíveis em Alimento.micros])
VD_MICROS = [
    ("Gorduras saturadas", 20, "g", ["saturados_g", "acidos_graxos_saturados_g"]),
    ("Gorduras trans", 2, "g", ["trans_g", "acidos_graxos_trans_g"]),
    ("Colesterol", 300, "mg", ["colesterol_mg"]),
    ("Sódio", 2000, "mg", ["sodio_mg"]),
    ("Vitamina A (RAE)", 800, "mcg", ["rae_mcg", "vitamina_a_rae_mcg"]),
    ("Vitamina D", 15, "mcg", ["vitamina_d_mcg"]),
    ("Vitamina E", 15, "mg", ["vitamina_e_mg", "alfa_tocoferol_mg"]),
    ("Vitamina K", 120, "mcg", ["vitamina_k_mcg"]),
    ("Vitamina C", 100, "mg", ["vitamina_c_mg"]),
    ("Tiamina (B1)", 1.2, "mg", ["tiamina_mg"]),
    ("Riboflavina (B2)", 1.2, "mg", ["riboflavina_mg"]),
    ("Niacina (B3)*", 15, "mg", ["niacina_mg"]),
    ("Vitamina B6", 1.3, "mg", ["piridoxina_mg", "vitamina_b6_mg"]),
    ("Folato (DFE)", 400, "mcg", ["folato_dfe_mcg"]),
    ("Vitamina B12", 2.4, "mcg", ["vitamina_b12_mcg", "cobalamina_mcg"]),
    ("Cálcio", 1000, "mg", ["calcio_mg"]),
    ("Cobre", 900, "mcg", ["cobre_mcg"]),
    ("Ferro", 14, "mg", ["ferro_mg"]),
    ("Fósforo", 700, "mg", ["fosforo_mg"]),
    ("Iodo", 150, "mcg", ["iodo_mcg"]),
    ("Magnésio", 420, "mg", ["magnesio_mg"]),
    ("Manganês", 3, "mg", ["manganes_mg"]),
    ("Potássio", 3500, "mg", ["potassio_mg"]),
    ("Selênio", 60, "mcg", ["selenio_mcg"]),
    ("Zinco", 11, "mg", ["zinco_mg"]),
]
# Notas de unidade: algumas tabelas trazem cobre/zinco em mg (TACO: cobre em mg) — convertidas na leitura.
CONVERSOES = {"cobre_mg": ("cobre_mcg", 1000.0)}

VD_NOTA = ("%VD = quantidade ÷ VDR de referência para adultos. *Niacina: tabelas costumam trazer mg de niacina, "
           "não equivalentes de niacina (NE). Totais subestimam quando algum alimento não tem o dado na tabela de origem.")

CHAVES_MICROS = {k for _r, _v, _u, ks in VD_MICROS for k in ks} | set(CONVERSOES)


def acumular_micros(micros, fator, acc, cobertura):
    """Soma micronutrientes de um item (fator = gramas/100) em `acc`; `cobertura` conta itens com/sem dado."""
    visto = set()
    for k, v in micros.items():
        if k in CONVERSOES:
            k, mult = CONVERSOES[k][0], CONVERSOES[k][1]
            v = v * mult
        if k in CHAVES_MICROS and isinstance(v, (int, float)):
            acc[k] = acc.get(k, 0.0) + v * fator
            visto.add(k)
    for k in visto:
        cobertura[k] = cobertura.get(k, 0) + 1


def linhas_vd(macros, micros, divisor=1.0, n_itens=0, cobertura=None):
    """Tabela de %VD. macros: dict kcal/c/p/l/f; micros: dict acumulado. divisor = nº de dias (média diária)."""
    cobertura = cobertura or {}
    saida = []
    for k, (rot, vd, un) in VD_MACROS.items():
        v = macros.get(k, 0.0) / divisor
        saida.append({"rotulo": rot, "valor": v, "unidade": un, "vd": vd, "pct": v / vd * 100, "parcial": False})
    for rot, vd, un, chaves in VD_MICROS:
        chave = next((c for c in chaves if c in micros), None)
        if chave is None:
            continue
        v = micros[chave] / divisor
        parcial = bool(n_itens) and cobertura.get(chave, 0) < n_itens
        saida.append({"rotulo": rot, "valor": v, "unidade": un, "vd": vd, "pct": v / vd * 100, "parcial": parcial})
    return saida
