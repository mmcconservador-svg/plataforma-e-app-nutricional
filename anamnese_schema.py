"""Definição das 16 abas da anamnese + módulo de saúde integrativa.
Cada campo: (nome, rótulo, tipo[, opções]). Tipos: texto, area, escolha."""

SIM_NAO = ["", "Sim", "Não"]

SECOES = [
    ("saude", "1. Histórico de saúde", [
        ("doencas", "Doenças diagnosticadas", "area"),
        ("cirurgias", "Cirurgias / internações", "area"),
        ("exames_recentes", "Exames recentes relevantes", "area"),
    ]),
    ("familiar", "2. Histórico familiar", [
        ("familiar", "Doenças na família (diabetes, hipertensão, câncer, etc.)", "area"),
    ]),
    ("medicamentos", "3. Medicamentos e suplementos", [
        ("medicamentos", "Medicamentos em uso (nome, dose, horário)", "area"),
        ("suplementos", "Suplementos / fitoterápicos em uso", "area"),
    ]),
    ("hist_alimentar", "4. Histórico alimentar", [
        ("refeicoes_dia", "Número de refeições por dia", "texto"),
        ("faz_refeicoes_fora", "Come fora de casa com frequência?", "escolha", SIM_NAO),
        ("quem_prepara", "Quem prepara as refeições?", "texto"),
        ("dietas_anteriores", "Dietas anteriores e resultado", "area"),
    ]),
    ("recordatorio", "5. Recordatório de 24 horas", [
        ("cafe", "Café da manhã (horário e alimentos)", "area"),
        ("lanche_manha", "Lanche da manhã", "area"),
        ("almoco", "Almoço", "area"),
        ("lanche_tarde", "Lanche da tarde", "area"),
        ("jantar", "Jantar", "area"),
        ("ceia", "Ceia", "area"),
    ]),
    ("frequencia", "6. Frequência alimentar semanal", [
        ("frutas", "Frutas (vezes/semana)", "texto"),
        ("verduras", "Verduras e legumes (vezes/semana)", "texto"),
        ("leguminosas", "Feijões e leguminosas (vezes/semana)", "texto"),
        ("carnes", "Carnes / ovos (vezes/semana)", "texto"),
        ("ultraprocessados", "Ultraprocessados e doces (vezes/semana)", "texto"),
    ]),
    ("preferencias", "7. Preferências e aversões", [
        ("gosta", "Alimentos que gosta", "area"),
        ("nao_gosta", "Alimentos que não gosta / evita", "area"),
        ("restricoes", "Restrições (religiosas, éticas, vegetarianismo, etc.)", "area"),
    ]),
    ("alergias", "8. Alergias e intolerâncias", [
        ("alergias", "Alergias alimentares", "area"),
        ("intolerancias", "Intolerâncias (lactose, glúten, etc.)", "area"),
    ]),
    ("intestino", "9. Funcionamento intestinal", [
        ("frequencia", "Evacuações por semana", "texto"),
        ("bristol", "Escala de Bristol predominante", "escolha",
         ["", "Tipo 1", "Tipo 2", "Tipo 3", "Tipo 4", "Tipo 5", "Tipo 6", "Tipo 7"]),
        ("sintomas", "Sintomas (distensão, dor, refluxo, etc.)", "area"),
    ]),
    ("hidratacao", "10. Hidratação", [
        ("agua", "Água por dia (litros, aproximado)", "texto"),
        ("outras_bebidas", "Outras bebidas (café, refrigerante, álcool)", "area"),
    ]),
    ("sono", "11. Sono", [
        ("horas", "Horas de sono por noite", "texto"),
        ("qualidade", "Qualidade do sono", "escolha", ["", "Boa", "Regular", "Ruim"]),
        ("queixas", "Queixas (insônia, despertares, etc.)", "area"),
    ]),
    ("atividade", "12. Atividade física", [
        ("tipo", "Tipo de atividade", "texto"),
        ("frequencia", "Frequência semanal e duração", "texto"),
    ]),
    ("emocional", "13. Estresse e saúde emocional", [
        ("estresse", "Nível de estresse (0 a 10)", "texto"),
        ("emocional", "Relação emocional com a comida / observações", "area"),
    ]),
    ("hormonal", "14. Saúde hormonal e metabólica", [
        ("hormonal", "Tireoide, ciclo menstrual, menopausa, resistência à insulina, etc.", "area"),
    ]),
    ("rotina", "15. Hábitos de vida e rotina", [
        ("profissao_rotina", "Rotina de trabalho e horários", "area"),
        ("tabagismo", "Tabagismo", "escolha", ["", "Não fuma", "Ex-fumante", "Fuma"]),
        ("alcool", "Consumo de álcool", "texto"),
    ]),
    ("objetivos", "16. Objetivos nutricionais", [
        ("objetivo", "Objetivo principal", "area"),
        ("expectativas", "Expectativas e prazo", "area"),
    ]),
    ("integrativa", "Saúde integrativa — oito remédios naturais (opcional)", [
        ("alimentacao", "Alimentação saudável", "area"),
        ("exercicio", "Exercício", "area"),
        ("agua", "Água", "area"),
        ("luz_solar", "Luz solar", "area"),
        ("temperanca", "Temperança", "area"),
        ("ar_puro", "Ar puro", "area"),
        ("repouso", "Repouso", "area"),
        # Dado sobre convicção religiosa/espiritual = dado sensível (LGPD art. 5º, II):
        # só é coletado com consentimento específico e destacado.
        ("confianca", "Confiança em Deus / espiritualidade", "area"),
    ]),
]

CAMPO_SENSIVEL = ("integrativa", "confianca")


def coletar(form, permite_espiritual):
    """Extrai respostas do formulário. Descarta o campo espiritual sem consentimento."""
    dados = {}
    for chave, _titulo, campos in SECOES:
        sec = {}
        for c in campos:
            nome, tipo = c[0], c[2]
            valor = (form.get(f"{chave}__{nome}") or "").strip()[:4000]
            if tipo == "escolha" and valor not in c[3]:
                valor = ""
            if (chave, nome) == CAMPO_SENSIVEL and not permite_espiritual:
                valor = ""
            if valor:
                sec[nome] = valor
        if sec:
            dados[chave] = sec
    return dados
