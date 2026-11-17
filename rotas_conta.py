"""Conta: 2FA (TOTP), troca de senha e redefinição de senha do paciente."""
import hashlib
import json
import time

import click
import segno
from flask import abort, flash, redirect, render_template, request, session, url_for
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from markupsafe import Markup

from app import (app, audit, concluir_login, db, exige, falha_login, limiter, meu_paciente, pro)
from flask import g
from models import Patient, User, agora
from security import (gerar_recovery, hash_recovery, hash_senha, novo_totp_secret, senha_forte,
                      totp_uri, totp_verifica, verificar_senha)

RESET_MAX_AGE = 24 * 3600


def _ser():
    return URLSafeTimedSerializer(app.config["SECRET_KEY"], salt="reset-senha")


def _logado():
    if not g.user:
        return redirect(url_for("login"))
    return None


def _usar_recovery(u, codigo):
    try:
        hashes = json.loads(u.recovery or "[]")
    except ValueError:
        hashes = []
    h = hash_recovery(codigo, app.config["SECRET_KEY"])
    if h in hashes:
        hashes.remove(h)
        u.recovery = json.dumps(hashes)
        return True
    return False


# ───────── segundo fator no login ─────────
@app.route("/login/2fa", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
def login_2fa():
    uid, ts = session.get("pre2fa"), session.get("pre2fa_ts", 0)
    if not uid or time.time() - ts > 300:
        session.clear()
        return redirect(url_for("login"))
    u = db.session.get(User, uid)
    if not u or not u.ativo or not u.totp_ativo:
        session.clear()
        return redirect(url_for("login"))
    if request.method == "POST":
        codigo = (request.form.get("codigo") or "").strip()
        contador = totp_verifica(u.totp_secret, codigo, u.totp_ultimo or 0)
        if contador:
            u.totp_ultimo = contador
            concluir_login(u, "2fa")
            return redirect(url_for("index"))
        if _usar_recovery(u, codigo):
            concluir_login(u, "2fa-recuperacao")
            flash("Você usou um código de recuperação. Gere novos códigos em Conta e segurança.", "aviso")
            return redirect(url_for("index"))
        falha_login(u, "2fa")
        if u.bloqueado_ate and u.bloqueado_ate > agora():
            session.clear()
            flash("Acesso temporariamente bloqueado por tentativas excessivas.", "erro")
            return redirect(url_for("login"))
        flash("Código inválido.", "erro")
    return render_template("login_2fa.html")


# ───────── configurar / desativar 2FA ─────────
@app.route("/conta/2fa", methods=["GET", "POST"])
def conta_2fa():
    if (r := _logado()):
        return r
    u = g.user
    if request.method == "POST":
        acao = request.form.get("acao")
        if acao == "ativar" and not u.totp_ativo and u.totp_secret:
            if totp_verifica(u.totp_secret, request.form.get("codigo", ""), 0):
                codigos = gerar_recovery()
                u.recovery = json.dumps([hash_recovery(c, app.config["SECRET_KEY"]) for c in codigos])
                u.totp_ativo = True
                audit("2fa_ativado", "usuario", u.id)
                db.session.commit()
                return render_template("conta_2fa.html", u=u, ativo=True,
                                       codigos=[f"{c[:5]}-{c[5:]}" for c in codigos], qr=None, segredo=None)
            flash("Código incorreto. Confira o horário do celular e tente de novo.", "erro")
        elif acao == "desativar" and u.totp_ativo:
            ok, _ = verificar_senha(u.senha_hash, request.form.get("senha", ""))
            if ok and totp_verifica(u.totp_secret, request.form.get("codigo", ""), u.totp_ultimo or 0):
                u.totp_ativo, u.totp_secret, u.recovery = False, None, None
                audit("2fa_desativado", "usuario", u.id)
                db.session.commit()
                flash("Verificação em duas etapas desativada.", "ok")
                return redirect(url_for("conta_2fa"))
            flash("Senha ou código incorretos.", "erro")
        elif acao == "novos_codigos" and u.totp_ativo:
            ok, _ = verificar_senha(u.senha_hash, request.form.get("senha", ""))
            if ok:
                codigos = gerar_recovery()
                u.recovery = json.dumps([hash_recovery(c, app.config["SECRET_KEY"]) for c in codigos])
                audit("2fa_novos_codigos", "usuario", u.id)
                db.session.commit()
                return render_template("conta_2fa.html", u=u, ativo=True,
                                       codigos=[f"{c[:5]}-{c[5:]}" for c in codigos], qr=None, segredo=None)
            flash("Senha incorreta.", "erro")
    if not u.totp_ativo:
        if not u.totp_secret:
            u.totp_secret = novo_totp_secret()
            db.session.commit()
        uri = totp_uri(u.totp_secret, u.email, app.config["CLINIC"]["nome_clinica"])
        qr = Markup(segno.make(uri, error="m").svg_inline(scale=5, border=2))
        return render_template("conta_2fa.html", u=u, ativo=False, codigos=None, qr=qr, segredo=u.totp_secret)
    return render_template("conta_2fa.html", u=u, ativo=True, codigos=None, qr=None, segredo=None)


# ───────── troca de senha ─────────
@app.route("/conta/senha", methods=["GET", "POST"])
def conta_senha():
    if (r := _logado()):
        return r
    if request.method == "POST":
        atual, nova, conf = (request.form.get(k, "") for k in ("atual", "nova", "conf"))
        ok, _ = verificar_senha(g.user.senha_hash, atual)
        erro = None if ok else "Senha atual incorreta."
        erro = erro or senha_forte(nova) or (None if nova == conf else "As senhas não coincidem.")
        if erro:
            flash(erro, "erro")
        else:
            g.user.senha_hash = hash_senha(nova)
            audit("senha_alterada", "usuario", g.user.id)
            db.session.commit()
            flash("Senha alterada.", "ok")
            return redirect(url_for("index"))
    return render_template("conta_senha.html")


# ───────── redefinição de senha do paciente (gerada pelo profissional) ─────────
@app.post("/pacientes/<int:pid>/redefinir-senha")
@pro
def paciente_redefinir(pid):
    p = meu_paciente(pid, editavel=True)
    if not p.user_id:
        flash("O paciente ainda não ativou o portal.", "erro")
    else:
        u = p.conta
        token = _ser().dumps({"uid": u.id, "f": hashlib.sha256(u.senha_hash.encode()).hexdigest()[:16]})
        link = url_for("redefinir", token=token, _external=True)
        audit("reset_gerado", "paciente", p.id)
        db.session.commit()
        flash(f"Link para o paciente criar nova senha (válido por 24 h, uso único): {link}", "ok")
    return redirect(url_for("paciente", pid=p.id))


@app.route("/redefinir/<token>", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
def redefinir(token):
    try:
        d = _ser().loads(token, max_age=RESET_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return render_template("erro.html", titulo="Link inválido ou expirado", msg="Peça um novo link ao seu nutricionista."), 400
    u = db.session.get(User, d["uid"])
    if not u or not u.ativo or hashlib.sha256(u.senha_hash.encode()).hexdigest()[:16] != d["f"]:
        return render_template("erro.html", titulo="Link já utilizado", msg="Peça um novo link ao seu nutricionista."), 400
    if request.method == "POST":
        nova, conf = request.form.get("nova", ""), request.form.get("conf", "")
        erro = senha_forte(nova) or (None if nova == conf else "As senhas não coincidem.")
        if erro:
            flash(erro, "erro")
        else:
            u.senha_hash = hash_senha(nova)
            u.tentativas, u.bloqueado_ate = 0, None
            audit("senha_redefinida", "usuario", u.id, user_id=u.id)
            db.session.commit()
            flash("Senha redefinida. Entre com a nova senha.", "ok")
            return redirect(url_for("login"))
    return render_template("redefinir.html")


@app.cli.command("redefinir-senha-profissional")
@click.argument("email")
def redefinir_senha_profissional(email):
    """Redefine a senha de um profissional (acesso ao servidor/HD): flask redefinir-senha-profissional EMAIL"""
    import getpass
    u = User.query.filter_by(email=email.strip().lower(), papel="profissional").first()
    if not u:
        raise SystemExit("Profissional não encontrado.")
    nova = getpass.getpass("Nova senha: ")
    erro = senha_forte(nova)
    if erro:
        raise SystemExit(erro)
    u.senha_hash, u.tentativas, u.bloqueado_ate = hash_senha(nova), 0, None
    db.session.commit()
    print("Senha redefinida.")
