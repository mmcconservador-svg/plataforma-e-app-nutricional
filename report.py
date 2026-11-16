"""Relatório PDF da avaliação nutricional (ReportLab)."""
import io
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

VERDE = colors.HexColor("#2f6f4f")


def _t(s):
    """Texto seguro para o PDF (fonte padrão não possui ≥/≤)."""
    return escape(str(s)).replace("≥", "&gt;=").replace("≤", "&lt;=")


def _br(v, d=1):
    return f"{v:.{d}f}".replace(".", ",")


def pdf_avaliacao(paciente, av, r, clinic):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm,
                            topMargin=1.8 * cm, bottomMargin=1.8 * cm,
                            title=f"Avaliação nutricional {paciente.codigo}", author=clinic["nome_pro"])
    ss = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=ss["Title"], textColor=VERDE, fontSize=18, spaceAfter=2)
    sub = ParagraphStyle("sub", parent=ss["Normal"], textColor=colors.grey, alignment=1, fontSize=9)
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], textColor=VERDE, fontSize=12, spaceBefore=10)
    normal = ParagraphStyle("n", parent=ss["Normal"], fontSize=10, leading=14)
    small = ParagraphStyle("s", parent=ss["Normal"], fontSize=8, textColor=colors.grey, leading=11)

    el = [
        Paragraph(_t(clinic["nome_clinica"]), h1),
        Paragraph(_t(f"{clinic['nome_pro']} — {clinic['titulo_pro']} — {clinic['registro_pro']}"), sub),
        Spacer(1, 12),
        Paragraph("Relatório de avaliação nutricional", h2),
        Paragraph(_t(f"Paciente: {paciente.nome} &nbsp;|&nbsp; Código: {paciente.codigo}").replace("&amp;nbsp;", "&nbsp;"), normal),
        Paragraph(_t(f"Data da avaliação: {av.data.strftime('%d/%m/%Y')}"
                     + (f" | Idade: {r['idade']} anos" if r.get('idade') is not None else "")), normal),
    ]

    def tabela(linhas, larguras=(7 * cm, 5 * cm, 5 * cm)):
        t = Table(linhas, colWidths=larguras, hAlign="LEFT")
        t.setStyle(TableStyle([
            ("FONTSIZE", (0, 0), (-1, -1), 9.5),
            ("BACKGROUND", (0, 0), (-1, 0), VERDE), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.3, colors.lightgrey),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f7f4")]),
            ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        return t

    el.append(Paragraph("Antropometria", h2))
    linhas = [["Indicador", "Resultado", "Classificação"],
              ["Peso", f"{_br(av.peso)} kg", ""],
              ["Altura", f"{_br(av.altura_cm, 0)} cm", ""],
              ["IMC", f"{_br(r['imc'])} kg/m²", _t(r["imc_class"])[:60]],
              ["Circunferência abdominal", f"{_br(av.cintura)} cm", _t(r["cintura_risco"])],
              ["Relação cintura/quadril", _br(r["rcq"], 2), _t(r["rcq_risco"])],
              ["Relação cintura/estatura", _br(r["rce"], 2), _t(r["rce_risco"])]]
    if "massa_gorda" in r:
        linhas += [["% de gordura", f"{_br(av.gordura_pct)} %", ""],
                   ["Massa gorda / magra", f"{_br(r['massa_gorda'])} kg / {_br(r['massa_magra'])} kg", ""]]
    # Paragraph para quebra de linha em células longas
    linhas = [[Paragraph(str(c), normal) if i else c for c in row] for i, row in enumerate(linhas)]
    el.append(tabela(linhas))

    if "tmb" in r:
        el.append(Paragraph("Estimativas energéticas e metas", h2))
        m = r["macros"]
        linhas = [["Item", "Valor"],
                  ["TMB (Mifflin-St Jeor)", f"{_br(r['tmb'], 0)} kcal"],
                  ["Fator de atividade", _br(r["fa"], 3)],
                  ["GET", f"{_br(r['get_kcal'], 0)} kcal"],
                  ["Ajuste energético", f"{av.ajuste_kcal:+d} kcal"],
                  ["VET (meta energética)", f"{_br(r['vet'], 0)} kcal"],
                  ["Carboidratos", f"{_br(av.pct_carb, 0)} % = {_br(m['carb_g'], 0)} g"],
                  ["Proteínas", f"{_br(av.pct_prot, 0)} % = {_br(m['prot_g'], 0)} g ({_br(r['prot_g_kg'], 2)} g/kg)"],
                  ["Lipídios", f"{_br(av.pct_lip, 0)} % = {_br(m['lip_g'], 0)} g"],
                  ["Fibras (referência 14 g/1000 kcal)", f"{_br(r['fibra_g'], 0)} g"],
                  ["Necessidade hídrica estimada (35 ml/kg)", f"{_br(r['agua_ml'] / 1000, 1)} L"]]
        el.append(tabela(linhas, (9 * cm, 8 * cm)))

    if r["avisos"]:
        el.append(Paragraph("Observações e limitações", h2))
        for a in r["avisos"]:
            el.append(Paragraph("• " + _t(a), normal))
    if av.observacoes:
        el.append(Paragraph("Observações do profissional", h2))
        el.append(Paragraph(_t(av.observacoes).replace("\n", "<br/>"), normal))

    el += [Spacer(1, 28),
           Paragraph("_______________________________________", normal),
           Paragraph(_t(f"{clinic['nome_pro']} — {clinic['titulo_pro']} — {clinic['registro_pro']}"), normal),
           Spacer(1, 10),
           Paragraph("Os valores são estimativas de apoio e só têm validade após revisão e aprovação do "
                     "nutricionista responsável. Documento contém dados pessoais sensíveis (LGPD): "
                     "compartilhe apenas com quem tem necessidade.", small)]
    doc.build(el)
    return buf.getvalue()
