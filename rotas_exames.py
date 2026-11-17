"""Rotas de exames laboratoriais e anexos (PDF criptografado em disco)."""
import json
import secrets
from datetime import date
from io import BytesIO

from flask import abort, flash, g, redirect, render_template, request, send_file, url_for

import calc
import exames as EX
from app import DATA_DIR, app, audit, db, linha_svg, meu_paciente, pro
from models import Documento, ExameColeta
from security import decrypt_bytes, encrypt_bytes

PASTA = DATA_DIR / "uploads"
MAX_PDF = 5 * 1024 * 1024


def _guardar_pdf(arq, paciente, exame):
    dados = arq.read(MAX_PDF + 1)
    if len(dados) > MAX_PDF:
        raise ValueError("O PDF deve ter até 5 MB.")
    if not dados.startswith(b"%PDF-"):
        raise ValueError("O arquivo não é um PDF válido.")
    PASTA.mkdir(parents=True, exist_ok=True)
    nome_disco = secrets.token_hex(16) + ".bin"
    (PASTA / nome_disco).write_bytes(encrypt_bytes(dados))
    doc = Documento(paciente_id=paciente.id, exame_id=exame.id, autor_id=g.user.id,
                    nome=(arq.filename or "exame.pdf")[:120], arquivo=nome_disco, tamanho=len(dados))
    db.session.add(doc)
    return doc


def _exame(p, eid):
    e = db.session.get(ExameColeta, eid)
    if not e or e.paciente_id != p.id:
        abort(404)
    return e


def _meta_ldl(e):
    try:
        return json.loads(e.resultados).get("meta_ldl")
    except (ValueError, TypeError):
        return None


@app.route("/pacientes/<int:pid>/exames/novo", methods=["GET", "POST"])
@pro
def exame_novo(pid):
    p = meu_paciente(pid, editavel=True)
    if request.method == "POST":
        f = request.form
        try:
            d = date.fromisoformat(f.get("data_coleta", ""))
            if d > date.today():
                raise ValueError("A data da coleta não pode ser futura.")
            itens = EX.montar_itens(f)
            meta = None
            if (f.get("meta_ldl") or "").strip():
                meta = float(f["meta_ldl"].replace(",", "."))
                if not 20 <= meta <= 300:
                    raise ValueError("Meta de LDL deve estar entre 20 e 300 mg/dL.")
            arq = request.files.get("pdf")
            tem_pdf = bool(arq and arq.filename)
        except ValueError as e:
            flash(str(e) if "could not convert" not in str(e) else "Meta de LDL inválida.", "erro")
            return render_template("exame_form.html", p=p, f=f, EX=EX)
        ex = ExameColeta(paciente_id=p.id, autor_id=g.user.id, data_coleta=d,
                         laboratorio=(f.get("laboratorio") or "").strip()[:120] or None,
                         resultados=json.dumps({"itens": itens, "meta_ldl": meta}),
                         observacoes=(f.get("observacoes") or "").strip()[:4000] or None,
                         regras_versao=EX.REGRAS_VERSAO)
        db.session.add(ex)
        db.session.flush()
        try:
            if tem_pdf:
                _guardar_pdf(arq, p, ex)
        except ValueError as e:
            db.session.rollback()
            flash(str(e), "erro")
            return render_template("exame_form.html", p=p, f=f, EX=EX)
        audit("exame_criado", "paciente", p.id)
        db.session.commit()
        return redirect(url_for("exame_ver", pid=p.id, eid=ex.id))
    return render_template("exame_form.html", p=p, f={"data_coleta": date.today().isoformat()}, EX=EX)


@app.get("/pacientes/<int:pid>/exames/<int:eid>")
@pro
def exame_ver(pid, eid):
    p = meu_paciente(pid)
    e = _exame(p, eid)
    audit("exame_visualizado", "exame", e.id)
    db.session.commit()
    idade = calc.idade_em(p.nascimento, e.data_coleta)
    linhas, calculados, achados = EX.avaliar(e.itens, idade, p.sexo, _meta_ldl(e))
    # evolução: mesmos exames em outras datas
    serie = {}
    for outro in reversed(p.exames):
        for k, it in outro.itens.items():
            if k in linhas and it.get("v") is not None:
                serie.setdefault(k, []).append((outro.data_coleta.strftime("%d/%m/%y"), it["v"]))
    graficos = [(EX.CATALOGO[k][0], linha_svg(pts, f"{EX.CATALOGO[k][0]} ({EX.CATALOGO[k][2]})"))
                for k, pts in serie.items() if len(pts) >= 2]
    por_cat = {c: [l for l in linhas.values() if l["categoria"] == c] for c in EX.CATEGORIAS}
    return render_template("exame.html", p=p, e=e, idade=idade, por_cat={c: v for c, v in por_cat.items() if v},
                           calculados=calculados, achados=achados, graficos=graficos, EX=EX)


@app.post("/pacientes/<int:pid>/exames/<int:eid>/documento")
@pro
def exame_documento(pid, eid):
    p = meu_paciente(pid, editavel=True)
    e = _exame(p, eid)
    arq = request.files.get("pdf")
    try:
        if not arq or not arq.filename:
            raise ValueError("Selecione um PDF.")
        _guardar_pdf(arq, p, e)
        audit("documento_anexado", "exame", e.id)
        db.session.commit()
        flash("PDF anexado (guardado criptografado).", "ok")
    except ValueError as err:
        db.session.rollback()
        flash(str(err), "erro")
    return redirect(url_for("exame_ver", pid=p.id, eid=e.id))


@app.get("/pacientes/<int:pid>/documentos/<int:did>")
@pro
def documento_baixar(pid, did):
    p = meu_paciente(pid)
    d = db.session.get(Documento, did)
    if not d or d.paciente_id != p.id:
        abort(404)
    caminho = PASTA / d.arquivo
    if not caminho.is_file() or caminho.resolve().parent != PASTA.resolve():
        abort(404)
    conteudo = decrypt_bytes(caminho.read_bytes())
    audit("documento_baixado", "documento", d.id)
    db.session.commit()
    resp = send_file(BytesIO(conteudo), mimetype="application/pdf", as_attachment=False, download_name=f"exame-{p.codigo}-{d.id}.pdf")
    resp.headers["Content-Security-Policy"] = "sandbox; default-src 'none'"
    return resp
