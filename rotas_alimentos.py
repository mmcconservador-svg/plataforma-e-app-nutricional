"""Rotas: alimentos, medidas caseiras, equivalentes, importação e cardápios."""
import json
import os
import tempfile
from pathlib import Path

import click
from flask import abort, flash, g, jsonify, make_response, redirect, render_template, request, url_for

import alimentos as A
import calc
import referencias as REF
import usda
from app import app, audit, db, meu_paciente, num, portal, pro, _av  # noqa: F401
from models import Alimento, Cardapio, FoodSource, MedidaCaseira, Patient
from report import pdf_cardapio

FONTES_PADRAO = {
    "TACO": "TACO – Tabela Brasileira de Composição de Alimentos (Unicamp/NEPA)",
    "TBCA": "TBCA – Tabela Brasileira de Composição de Alimentos (USP/FoRC)",
    "IBGE-MEDIDAS": "IBGE – Medidas referidas para alimentos consumidos no Brasil",
}


# ───────────────────────── alimentos ─────────────────────────
@app.get("/alimentos")
@pro
def alimentos():
    q = (request.args.get("q") or "").strip()
    achados = A.buscar(q, g.user.id, 60) if q else []
    contagem = dict(db.session.query(Alimento.fonte_id, db.func.count(Alimento.id)).group_by(Alimento.fonte_id).all())
    return render_template("alimentos.html", q=q, achados=achados, fontes=FoodSource.query.all(), contagem=contagem)


@app.get("/alimentos/<int:aid>")
@pro
def alimento(aid):
    a = A.obter(aid, g.user.id) or abort(404)
    eq, erro = None, None
    try:
        gramas = float(request.args.get("g", "100").replace(",", "."))
    except ValueError:
        gramas = 100.0
    criterio = request.args.get("criterio", "carboidrato")
    mesmo_grupo = request.args.get("grupo", "1") == "1"
    if request.args.get("g"):
        try:
            if not 1 <= gramas <= 2000:
                raise ValueError("Informe entre 1 e 2000 g.")
            eq = A.equivalentes(a, gramas, criterio, g.user.id, mesmo_grupo)
        except ValueError as e:
            erro = str(e)
    return render_template("alimento.html", a=a, eq=eq, erro=erro, gramas=gramas, criterio=criterio,
                           mesmo_grupo=mesmo_grupo, criterios=A.CRITERIOS, sugerir=A.sugerir_medida)


def _proprio():
    f = FoodSource.query.filter_by(codigo="PROPRIO").first()
    if not f:
        f = FoodSource(codigo="PROPRIO", nome="Alimentos cadastrados pelo profissional", licenca_confirmada=True)
        db.session.add(f)
        db.session.flush()
    return f


@app.route("/alimentos/novo", methods=["GET", "POST"])
@pro
def alimento_novo():
    if request.method == "POST":
        f = request.form
        try:
            nome = (f.get("nome") or "").strip()
            if not nome:
                raise ValueError("Informe o nome do alimento.")
            vals = {c: num(f, c, rot, 0, 1000, obrigatorio=(c != "fibra_g"))
                    for c, rot in (("energia_kcal", "a energia (kcal)"), ("proteina_g", "as proteínas"),
                                   ("lipidio_g", "os lipídios"), ("carbo_g", "os carboidratos"),
                                   ("fibra_g", "as fibras"))}
            fc = num(f, "fator_correcao", "o fator de correção", 1, 10, obrigatorio=False) or 1.0
            medidas = []
            for linha in (f.get("medidas") or "").splitlines():
                if not linha.strip():
                    continue
                desc, _, gr = linha.partition(";")
                gr = A.parse_num(gr)
                if not desc.strip() or not gr or not 0 < gr <= 5000:
                    raise ValueError(f"Medida inválida: “{linha.strip()}” (use: descrição;gramas).")
                medidas.append((desc.strip()[:120], gr))
        except ValueError as e:
            flash(str(e), "erro")
            return render_template("alimento_form.html", f=f)
        k, p_, l_, c_ = (vals[x] for x in ("energia_kcal", "proteina_g", "lipidio_g", "carbo_g"))
        if k > 20 and abs(k - (4 * p_ + 4 * c_ + 9 * l_)) / k > 0.30:
            flash("Atenção: a energia informada difere mais de 30% de 4×P + 4×C + 9×L. Confira os valores.", "aviso")
        a = Alimento(fonte_id=_proprio().id, owner_id=g.user.id, nome=nome[:300], nome_norm=A.normalizar(nome)[:300],
                     grupo=(f.get("grupo") or "").strip()[:120] or None, fator_correcao=fc, **vals)
        for d, gr in medidas:
            a.medidas.append(MedidaCaseira(descricao=d, gramas=gr, fonte="PROPRIO"))
        db.session.add(a)
        audit("alimento_proprio_criado", "alimento", None, nome[:80])
        db.session.commit()
        return redirect(url_for("alimento", aid=a.id))
    return render_template("alimento_form.html", f={})


@app.post("/alimentos/<int:aid>/medidas")
@pro
def alimento_medida(aid):
    a = A.obter(aid, g.user.id) or abort(404)
    desc = (request.form.get("descricao") or "").strip()[:120]
    gr = A.parse_num(request.form.get("gramas"))
    if not desc or not gr or not 0 < gr <= 5000:
        flash("Informe a descrição e as gramas (0–5000).", "erro")
    else:
        a.medidas.append(MedidaCaseira(descricao=desc, gramas=gr, fonte="PROPRIO"))
        audit("medida_criada", "alimento", a.id, desc[:80])
        db.session.commit()
    return redirect(url_for("alimento", aid=a.id))


@app.post("/alimentos/medidas/<int:mid>/remover")
@pro
def alimento_medida_remover(mid):
    m = db.session.get(MedidaCaseira, mid) or abort(404)
    if m.fonte != "PROPRIO":
        abort(403)
    aid = m.alimento_id
    db.session.delete(m)
    audit("medida_removida", "alimento", aid)
    db.session.commit()
    return redirect(url_for("alimento", aid=aid))


# ───────────────────────── importação (tabelas oficiais) ─────────────────────────
@app.route("/alimentos/importar", methods=["GET", "POST"])
@pro
def alimentos_importar():
    resultado = None
    if request.method == "POST":
        arq = request.files.get("arquivo")
        codigo = (request.form.get("fonte") or "").strip().upper()
        tipo = request.form.get("tipo")
        if not request.form.get("licenca"):
            flash("Confirme que você tem licença/autorização para usar esta tabela.", "erro")
        elif not arq or not arq.filename:
            flash("Selecione o arquivo (.csv ou .xlsx).", "erro")
        elif tipo not in ("composicao", "medidas", "fatores") or (tipo != "fatores" and (not codigo or len(codigo) > 30)):
            flash("Informe a fonte e o tipo de arquivo.", "erro")
        else:
            ext = Path(arq.filename).suffix.lower()
            if ext not in (".csv", ".tsv", ".txt", ".xlsx", ".xlsm"):
                flash("Formato não suportado. Use .csv, .tsv ou .xlsx.", "erro")
            else:
                fd, tmp = tempfile.mkstemp(suffix=ext)
                os.close(fd)
                try:
                    arq.save(tmp)
                    versao = (request.form.get("versao") or "").strip()[:40]
                    if tipo == "fatores":
                        resultado = A.importar_fatores(tmp, g.user.id)
                    elif tipo == "composicao":
                        resultado = A.importar_tabela(tmp, codigo, FONTES_PADRAO.get(codigo, codigo), versao, g.user.id)
                    else:
                        resultado = A.importar_medidas(tmp, codigo, FONTES_PADRAO.get(codigo, codigo), versao, g.user.id,
                                                       fonte_alimentos=(request.form.get("fonte_alimentos") or "").strip().upper() or None)
                    audit("alimentos_importados", "fonte", None, f"{codigo} {tipo}")
                    db.session.commit()
                    flash("Importação concluída.", "ok")
                except Exception as e:  # arquivo malformado
                    db.session.rollback()
                    flash(f"Não foi possível importar: {e}", "erro")
                finally:
                    try:
                        os.unlink(tmp)
                    except OSError:
                        pass
    return render_template("alimentos_importar.html", resultado=resultado, fontes=FONTES_PADRAO)


@app.cli.command("importar-alimentos")
@click.argument("arquivo")
@click.option("--fonte", required=True, help="Código da fonte, ex.: TACO, TBCA")
@click.option("--versao", default="")
@click.option("--medidas", is_flag=True, help="O arquivo é de medidas caseiras (não de composição)")
@click.option("--fonte-alimentos", default=None, help="Ao importar medidas: limitar a uma fonte de alimentos")
@click.option("--confirmo-licenca", is_flag=True, help="Confirmo que tenho licença para usar esta tabela")
def importar_cli(arquivo, fonte, versao, medidas, fonte_alimentos, confirmo_licenca):
    """Importa TACO/TBCA/IBGE de um arquivo .csv/.xlsx."""
    if not confirmo_licenca:
        raise SystemExit("Informe --confirmo-licenca após verificar a licença de uso da tabela.")
    fonte = fonte.upper()
    if medidas:
        st = A.importar_medidas(arquivo, fonte, FONTES_PADRAO.get(fonte, fonte), versao, fonte_alimentos=fonte_alimentos)
    else:
        st = A.importar_tabela(arquivo, fonte, FONTES_PADRAO.get(fonte, fonte), versao)
    print(json.dumps(st, ensure_ascii=False, indent=2))


# ───────────────────────── API (JSON) para o editor ─────────────────────────
@app.get("/api/alimentos")
@pro
def api_alimentos():
    return jsonify([A.serializar(a) for a in A.buscar(request.args.get("q", ""), g.user.id, 15)])


@app.get("/api/equivalentes")
@pro
def api_equivalentes():
    a = A.obter(request.args.get("alimento", type=int) or 0, g.user.id) or abort(404)
    try:
        gramas = float(request.args.get("gramas", "100"))
        if not 1 <= gramas <= 5000:
            raise ValueError("Quantidade fora do intervalo.")
        eq = A.equivalentes(a, gramas, request.args.get("criterio", "carboidrato"), g.user.id,
                            request.args.get("grupo", "1") == "1", limite=8)
    except ValueError as e:
        return jsonify({"erro": str(e)}), 400
    itens = []
    for x in eq["itens"]:
        s = A.serializar(x["alimento"])
        s.update({"gramas": x["gramas"], "sugestao": x["medida"], "dif_kcal": round(x["dif"]["kcal"], 1)})
        itens.append(s)
    return jsonify({"itens": itens})


# ───────────────────────── cardápios ─────────────────────────
def _metas_sugeridas(p):
    if not p.avaliacoes:
        return {}
    r = calc.avaliar(p.avaliacoes[0], p)
    if "vet" not in r:
        return {}
    m = r["macros"]
    return {"kcal": round(r["vet"]), "carb_g": round(m["carb_g"]), "prot_g": round(m["prot_g"]), "lip_g": round(m["lip_g"])}


def _ler_metas(form):
    return {k: num(form, k, rot, 0, 20000, obrigatorio=False)
            for k, rot in (("kcal", "a meta de energia"), ("carb_g", "a meta de carboidratos"),
                           ("prot_g", "a meta de proteínas"), ("lip_g", "a meta de lipídios"))}


@app.route("/pacientes/<int:pid>/cardapio/novo", methods=["GET", "POST"])
@pro
def cardapio_novo(pid):
    p = meu_paciente(pid, editavel=True)
    base = None
    if request.args.get("base", type=int):
        base = db.session.get(Cardapio, request.args.get("base", type=int))
        if not base or base.paciente_id != p.id:
            abort(404)
    if request.method == "POST":
        f = request.form
        try:
            titulo = (f.get("titulo") or "").strip()[:160]
            if not titulo:
                raise ValueError("Informe o título do cardápio.")
            est = A.validar_estrutura(json.loads(f.get("estrutura") or "{}"), g.user.id)
            metas = {k: v for k, v in _ler_metas(f).items() if v}
        except (ValueError, json.JSONDecodeError) as e:
            flash(str(e) if isinstance(e, ValueError) and not isinstance(e, json.JSONDecodeError) else "Cardápio inválido.", "erro")
            try:
                estado = json.loads((f.get("estado") or "")[:2_000_000])
            except ValueError:
                estado = {"dias": []}
            return render_template("cardapio_editor.html", p=p, titulo=f.get("titulo", ""), metas=dict(f),
                                   estado=estado)
        novo = Cardapio(paciente_id=p.id, autor_id=g.user.id, titulo=titulo, metas=json.dumps(metas),
                        conteudo=json.dumps(est), versao_de=base.id if base else None)
        db.session.add(novo)
        audit("cardapio_criado", "paciente", p.id)
        db.session.commit()
        return redirect(url_for("cardapio_ver", pid=p.id, cid=novo.id))
    if base:
        estado, metas, titulo = A.para_editor(A.calcular(base.estrutura)), base.metas_dict, base.titulo
    else:
        estado, metas, titulo = {"dias": []}, _metas_sugeridas(p), f"Cardápio de {p.nome.split()[0]}"
    return render_template("cardapio_editor.html", p=p, titulo=titulo, metas=metas, estado=estado)


def _cardapio(p, cid):
    c = db.session.get(Cardapio, cid)
    if not c or c.paciente_id != p.id:
        abort(404)
    return c


def _contexto_cardapio(c):
    ce = A.calcular(c.estrutura)
    metas = c.metas_dict
    return {"c": c, "ce": ce, "metas": metas,
            "comp_dias": [A.comparar(d["total"], metas) for d in ce["dias"]],
            "comp_media": A.comparar(ce["media"], metas),
            "compras": A.lista_compras(ce), "qtd": A.texto_quantidade,
            "vd_fonte": REF.VD_FONTE, "vd_nota": REF.VD_NOTA}


@app.get("/pacientes/<int:pid>/cardapio/<int:cid>")
@pro
def cardapio_ver(pid, cid):
    p = meu_paciente(pid)
    c = _cardapio(p, cid)
    audit("cardapio_visualizado", "cardapio", c.id)
    db.session.commit()
    return render_template("cardapio.html", p=p, portal=False, **_contexto_cardapio(c))


@app.post("/pacientes/<int:pid>/cardapio/<int:cid>/liberar")
@pro
def cardapio_liberar(pid, cid):
    p = meu_paciente(pid, editavel=True)
    c = _cardapio(p, cid)
    c.liberado = not c.liberado
    audit("cardapio_liberado" if c.liberado else "cardapio_ocultado", "cardapio", c.id)
    db.session.commit()
    flash("Cardápio liberado ao paciente." if c.liberado else "Cardápio oculto do paciente.", "ok")
    return redirect(url_for("cardapio_ver", pid=p.id, cid=c.id))


def _pdf_cardapio(p, c):
    ctx = _contexto_cardapio(c)
    resp = make_response(pdf_cardapio(p, c, ctx, app.config["CLINIC"]))
    resp.headers["Content-Type"] = "application/pdf"
    resp.headers["Content-Disposition"] = f'inline; filename="cardapio-{p.codigo}-{c.id}.pdf"'
    return resp


@app.get("/pacientes/<int:pid>/cardapio/<int:cid>/pdf")
@pro
def cardapio_pdf(pid, cid):
    p = meu_paciente(pid)
    c = _cardapio(p, cid)
    audit("cardapio_pdf", "cardapio", c.id)
    db.session.commit()
    return _pdf_cardapio(p, c)


# ── portal do paciente ──
@app.get("/portal/cardapios")
@portal
def portal_cardapios():
    return render_template("portal_cardapios.html", p=g.paciente, lista=[c for c in g.paciente.cardapios if c.liberado])


def _meu_cardapio(cid):
    c = db.session.get(Cardapio, cid)
    if not c or c.paciente_id != g.paciente.id or not c.liberado:
        abort(404)
    return c


@app.get("/portal/cardapio/<int:cid>")
@portal
def portal_cardapio(cid):
    c = _meu_cardapio(cid)
    audit("cardapio_visualizado", "cardapio", c.id, "paciente")
    db.session.commit()
    return render_template("cardapio.html", p=g.paciente, portal=True, **_contexto_cardapio(c))


@app.get("/portal/cardapio/<int:cid>/pdf")
@portal
def portal_cardapio_pdf(cid):
    return _pdf_cardapio(g.paciente, _meu_cardapio(cid))


# ───────────────────────── fatores de correção / cocção ─────────────────────────
@app.post("/alimentos/<int:aid>/fatores")
@pro
def alimento_fatores(aid):
    a = A.obter(aid, g.user.id) or abort(404)
    try:
        fc = num(request.form, "fator_correcao", "o fator de correção", 1, 10, obrigatorio=False)
        fcc = num(request.form, "fator_coccao", "o fator de cocção", 0.1, 10, obrigatorio=False)
    except ValueError as e:
        flash(str(e), "erro")
        return redirect(url_for("alimento", aid=a.id))
    a.fator_correcao = fc or 1.0
    a.fator_coccao = fcc
    a.metodo_preparo = (request.form.get("metodo_preparo") or "").strip()[:60] or None
    audit("fatores_alimento", "alimento", a.id)
    db.session.commit()
    flash("Fatores atualizados.", "ok")
    return redirect(url_for("alimento", aid=a.id))


@app.cli.command("importar-fatores")
@click.argument("arquivo")
def importar_fatores_cli(arquivo):
    """Importa fatores de correção/cocção (CSV/XLSX): alimento|codigo, fator de correção, fator de cocção, método."""
    print(json.dumps(A.importar_fatores(arquivo), ensure_ascii=False, indent=2))


# ───────────────────────── USDA FoodData Central ─────────────────────────
@app.route("/alimentos/usda", methods=["GET", "POST"])
@pro
def alimentos_usda():
    q = (request.values.get("q") or "").strip()
    achados, erro = [], None
    if request.method == "POST" and request.form.get("fdc_id"):
        try:
            a = usda.importar_api(int(request.form["fdc_id"]), g.user.id)
            audit("usda_importado", "alimento", a.id, a.nome[:80])
            db.session.commit()
            return redirect(url_for("alimento", aid=a.id))
        except (ValueError, KeyError) as e:
            db.session.rollback()
            erro = str(e)
    elif q:
        try:
            achados = usda.buscar_api(q)
        except ValueError as e:
            erro = str(e)
    return render_template("alimentos_usda.html", q=q, achados=achados, erro=erro)


@app.cli.command("importar-usda")
@click.argument("pasta")
def importar_usda_cli(pasta):
    """Importa o pacote CSV do USDA FoodData Central (SR Legacy/Foundation) já extraído em PASTA."""
    print(json.dumps(usda.importar_csv(pasta), ensure_ascii=False, indent=2))
