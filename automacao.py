"""Automação inteligente baseada em regras transparentes.

- Não usa IA generativa e não envia dados a terceiros.
- Gera apenas ALERTAS e SUGESTÕES para o profissional; nada chega ao paciente sem validação.
- Não diagnostica: aponta pontos que merecem avaliação clínica.
"""
from datetime import date, timedelta

import calc
import modulos_clinicos as MC

DIAS_REAVALIACAO = 30
PRAZO_LGPD_DIAS = 15
ORDEM = {"atencao": 0, "aviso": 1, "info": 2}


def insights_paciente(p, hoje=None):
    hoje = hoje or date.today()
    out = []
    if p.anonimizado_em:
        return out

    consent = [c for c in p.consentimentos if c.tipo == "tratamento_saude"]
    if not consent or not consent[-1].aceito:
        out.append(("aviso", "Consentimento para tratamento de dados de saúde não registrado."))
    if p.menor and not p.responsavel_nome:
        out.append(("atencao", "Paciente menor de 18 anos sem responsável legal cadastrado (LGPD art. 14)."))

    if p.anamneses and not p.anamneses[0].revisada:
        out.append(("aviso", "Anamnese enviada pelo paciente aguardando sua revisão."))
    if not p.anamneses:
        out.append(("info", "Anamnese ainda não preenchida."))

    avs = p.avaliacoes
    if not avs:
        out.append(("info", "Nenhuma avaliação antropométrica registrada."))
    else:
        ult = avs[0]
        dias = (hoje - ult.data).days
        if dias > DIAS_REAVALIACAO:
            out.append(("aviso", f"Última avaliação há {dias} dias: sugerida reavaliação (rotina de {DIAS_REAVALIACAO} dias)."))
        r = calc.avaliar(ult, p)
        if r["imc"] >= 30 and not (r["idade"] is not None and r["idade"] < 18):
            out.append(("aviso", f"IMC {r['imc']:.1f}: avaliar necessidade de metas de redução de risco cardiometabólico."))
        elif r["imc"] < 18.5 and not (r["idade"] is not None and r["idade"] < 18):
            out.append(("aviso", f"IMC {r['imc']:.1f} (baixo peso): avaliar ingestão e causas."))
        if r["cintura_risco"] == "Risco muito aumentado":
            out.append(("aviso", "Circunferência abdominal com risco muito aumentado."))
        if abs(r.get("macros", {}).get("soma_pct", 100) - 100) > 0.5:
            out.append(("atencao", "Soma dos percentuais de macronutrientes diferente de 100% na última avaliação."))
        if len(avs) >= 2:
            ant = avs[1]
            dd = (ult.data - ant.data).days
            if ant.peso:
                var = (ult.peso - ant.peso) / ant.peso * 100
                if dd <= 45 and abs(var) >= 5:
                    out.append(("aviso", f"Variação de peso de {var:+.1f}% em {dd} dias: confirmar medidas e investigar causas."))
        if not ult.liberada:
            out.append(("info", "Última avaliação ainda não liberada ao paciente (aguardando sua validação)."))

    if p.integrativas:
        res = MC.resumo_integrativa(p.integrativas[0].dados)
        for rot, nota, sug in res["baixos"][:3]:
            out.append(("info", f"Integrativa — {rot} com nota {nota}/10. Sugestão: {sug}"))
        if len(p.integrativas) >= 2:
            a, b = MC.resumo_integrativa(p.integrativas[0].dados)["media"], MC.resumo_integrativa(p.integrativas[1].dados)["media"]
            if a is not None and b is not None and a - b <= -1.5:
                out.append(("aviso", "Média integrativa caiu mais de 1,5 ponto desde a avaliação anterior."))

    if p.teas:
        nivel, pts = MC.triagem_tea(p.teas[0].dados)
        if nivel == "alto":
            out.append(("atencao", "TEA: triagem alimentar com nível de atenção ALTO — considere plano estruturado e articulação com a equipe multiprofissional."))
        elif nivel == "moderado":
            out.append(("aviso", "TEA: triagem alimentar com nível de atenção moderado."))
        if (hoje - p.teas[0].data).days > 90:
            out.append(("info", "Avaliação alimentar TEA com mais de 90 dias: reavaliar."))

    for s in p.solicitacoes:
        if s.status == "aberta":
            idade = (hoje - s.criado_em.date()).days
            nivel = "atencao" if idade >= PRAZO_LGPD_DIAS - 3 else "aviso"
            out.append((nivel, f"Solicitação LGPD ({s.tipo}) aberta há {idade} dia(s); prazo recomendado de {PRAZO_LGPD_DIAS} dias."))
    out.sort(key=lambda x: ORDEM[x[0]])
    return out


def painel(pacientes, hoje=None):
    hoje = hoje or date.today()
    ativos = [p for p in pacientes if not p.anonimizado_em]
    inicio_mes = hoje.replace(day=1)
    dist, tarefas = {}, []
    aval_mes = sem_reav = anam_pend = 0
    tea_n = integ_n = 0
    for p in ativos:
        for a in p.avaliacoes:
            if a.data >= inicio_mes:
                aval_mes += 1
        if p.avaliacoes:
            r = calc.avaliar(p.avaliacoes[0], p)
            cat = r["imc_class"].split(" (")[0].split(":")[0]
            cat = "Criança/adolescente" if cat.startswith("Criança") else cat
            dist[cat] = dist.get(cat, 0) + 1
            if (hoje - p.avaliacoes[0].data).days > DIAS_REAVALIACAO:
                sem_reav += 1
        else:
            sem_reav += 1
        if p.anamneses and not p.anamneses[0].revisada:
            anam_pend += 1
        tea_n += 1 if p.teas else 0
        integ_n += 1 if p.integrativas else 0
        for nivel, txt in insights_paciente(p, hoje):
            if nivel != "info":
                tarefas.append((ORDEM[nivel], nivel, p, txt))
    tarefas.sort(key=lambda t: t[0])
    return {
        "ativos": len(ativos), "aval_mes": aval_mes, "sem_reav": sem_reav, "anam_pend": anam_pend,
        "tea": tea_n, "integrativa": integ_n, "dist": dist,
        "tarefas": [(n, p, t) for _, n, p, t in tarefas[:20]],
    }
