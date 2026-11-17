"""Nutri Saúde Integral — plataforma de avaliação e acompanhamento nutricional (Flask)."""
import json
import os
import secrets
import time
from datetime import date, timedelta
from functools import wraps
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from flask import (Flask, abort, flash, g, jsonify, make_response, redirect,
                   render_template, request, send_from_directory, session, url_for)
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_migrate import Migrate
from markupsafe import Markup, escape
from sqlalchemy import and_, inspect, or_, text
from sqlalchemy.orm import selectinload
from werkzeug.middleware.proxy_fix import ProxyFix

import anamnese_schema as AS
import automacao
import calc
import equacoes as Q
import modulos_clinicos as MC
from models import (Alimento, Anamnese, AuditLog, Avaliacao, AvaliacaoIntegrativa, AvaliacaoTEA,
                    Cardapio, Consent, DataRequest, Documento, ExameColeta, FoodSource,
                    MedidaCaseira, Patient, User, agora, db)
from report import pdf_avaliacao
from security import (hash_senha, init_keys, load_secret, senha_forte, token_consulta,
                      verificar_senha)

POLICY_VERSION = "1.0"
IDLE_SECONDS = 30 * 60
MAX_TENTATIVAS = 5
BLOQUEIO_MIN = 15
INVITE_MAX_AGE = 7 * 24 * 3600
TZ = ZoneInfo("America/Sao_Paulo")

load_dotenv()  # lê o arquivo .env (identidade profissional, pastas de dados e chaves)

app = Flask(__name__, instance_relative_config=True)
# Pastas separadas: dados (banco) e chaves. Em HD portátil, mantenha as chaves em outro local seguro.
DATA_DIR = Path(os.environ.get("NUTRI_DATA_DIR") or app.instance_path).resolve()
KEYS_DIR = Path(os.environ.get("NUTRI_KEYS_DIR") or DATA_DIR).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
KEYS_DIR.mkdir(parents=True, exist_ok=True)
init_keys(KEYS_DIR)
def _db_url():
    u = os.environ.get("DATABASE_URL")
    if not u:
        return "sqlite:///" + str(DATA_DIR / "nutri.db")
    for ant in ("postgres://", "postgresql://"):
        if u.startswith(ant):
            return "postgresql+psycopg://" + u[len(ant):]
    return u


app.config.update(
    SECRET_KEY=load_secret(KEYS_DIR),
    SQLALCHEMY_DATABASE_URI=_db_url(),
    SQLALCHEMY_ENGINE_OPTIONS={"pool_pre_ping": True},
    RATELIMIT_ENABLED=os.environ.get("NUTRI_RATELIMIT", "1") != "0",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("NUTRI_ENV") == "production",
    MAX_CONTENT_LENGTH=7 * 1024 * 1024,
    CLINIC={
        "nome_clinica": os.environ.get("CLINICA_NOME", "Nutri Saúde Integral"),
        "nome_pro": os.environ.get("PRO_NOME", "Nome do Profissional"),
        "titulo_pro": os.environ.get("PRO_TITULO", "Nutricionista"),
        "registro_pro": os.environ.get("PRO_REGISTRO", "CRN 0-00000000"),
        "contato_dpo": os.environ.get("DPO_CONTATO", "informe-o-e-mail-do-encarregado@exemplo.com.br"),
    },
)
if os.environ.get("NUTRI_PROXY") == "1":  # atrás de nginx/Caddy: IP e HTTPS reais
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
db.init_app(app)
migrate = Migrate(app, db)
limiter = Limiter(get_remote_address, app=app, default_limits=[],
                  storage_uri=os.environ.get("RATELIMIT_STORAGE_URI", "memory://"))
DUMMY_HASH = hash_senha("senha-fictícia-para-igualar-tempo")


def _migrar_colunas():
    """Uso local: acrescenta colunas novas (somente aditivo). Em produção use `flask db upgrade`."""
    insp = inspect(db.engine)
    with db.engine.begin() as con:
        for tabela in db.metadata.sorted_tables:
            if not insp.has_table(tabela.name):
                continue
            existentes = {c["name"] for c in insp.get_columns(tabela.name)}
            for col in tabela.columns:
                if col.name not in existentes:
                    tipo = col.type.compile(dialect=db.engine.dialect)
                    con.execute(text(f'ALTER TABLE "{tabela.name}" ADD COLUMN "{col.name}" {tipo}'))


if os.environ.get("NUTRI_ENV") != "production" or os.environ.get("NUTRI_AUTO_CREATE") == "1":
    with app.app_context():
        db.create_all()
        _migrar_colunas()


# ───────────────────────── utilidades ─────────────────────────
def serializer():
    return URLSafeTimedSerializer(app.config["SECRET_KEY"], salt="convite-paciente")


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def audit(acao, entidade=None, entidade_id=None, detalhe=None, user_id=None):
    """Registro de auditoria com hash encadeado (alteração ou remoção de linhas é detectável)."""
    import hashlib
    uid = user_id if user_id is not None else (g.user.id if getattr(g, "user", None) else None)
    ts = agora().replace(microsecond=0)
    ip = (request.remote_addr or "")[:64]
    det = (detalhe or "")[:255]
    ultimo = AuditLog.query.order_by(AuditLog.id.desc()).first()  # autoflush inclui linhas pendentes
    prev = (ultimo.hash if ultimo and ultimo.hash else "0" * 64)
    h = hashlib.sha256(f"{prev}|{ts.isoformat()}|{uid}|{acao}|{entidade}|{entidade_id}|{ip}|{det}".encode()).hexdigest()
    db.session.add(AuditLog(ts=ts, user_id=uid, acao=acao, entidade=entidade, entidade_id=entidade_id,
                            ip=ip, detalhe=det, hash=h))


def num(form, nome, rotulo, lo, hi, obrigatorio=True):
    raw = (form.get(nome) or "").strip().replace(",", ".")
    if not raw:
        if obrigatorio:
            raise ValueError(f"Informe {rotulo}.")
        return None
    try:
        v = float(raw)
    except ValueError:
        raise ValueError(f"{rotulo}: valor inválido.")
    if not lo <= v <= hi:
        raise ValueError(f"{rotulo} deve estar entre {lo:g} e {hi:g}.")
    return v


def tem_consentimento(paciente, tipo):
    ultimo = None
    for c in paciente.consentimentos:
        if c.tipo == tipo:
            ultimo = c
    return bool(ultimo and ultimo.aceito)


def registrar_consentimento(paciente, tipo, aceito, origem):
    db.session.add(Consent(paciente_id=paciente.id, tipo=tipo, versao=POLICY_VERSION,
                           aceito=aceito, origem=origem))
    db.session.flush()
    db.session.refresh(paciente)


def novo_codigo():
    while True:
        cod = "NSI-" + secrets.token_hex(3).upper()
        if not Patient.query.filter_by(codigo=cod).first():
            return cod


def linha_svg(pontos, titulo):
    """Gráfico de linha em SVG (sem JavaScript externo → compatível com CSP estrita)."""
    if len(pontos) < 2:
        return Markup("")
    w, h, pad = 340, 120, 26
    vals = [v for _, v in pontos]
    lo, hi = min(vals), max(vals)
    if hi == lo:
        hi, lo = hi + 1, lo - 1
    xs = [pad + i * (w - 2 * pad) / (len(pontos) - 1) for i in range(len(pontos))]
    ys = [h - pad - (v - lo) / (hi - lo) * (h - 2 * pad) for v in vals]
    path = " ".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs, ys))
    pts = "".join(
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.5" class="pt"><title>{escape(l)}: {v:.1f}</title></circle>'
        for x, y, (l, v) in zip(xs, ys, pontos))
    rot = (f'<text x="{pad}" y="{h - 6}" class="lbl">{escape(pontos[0][0])}</text>'
           f'<text x="{w - pad}" y="{h - 6}" class="lbl" text-anchor="end">{escape(pontos[-1][0])}</text>'
           f'<text x="{pad}" y="14" class="lbl">{escape(titulo)}: {vals[0]:.1f} → {vals[-1]:.1f}</text>')
    return Markup(f'<svg viewBox="0 0 {w} {h}" class="grafico" role="img" aria-label="{escape(titulo)}">'
                  f'<polyline points="{path}" class="ln" fill="none"/>{pts}{rot}</svg>')


def barras_svg(dados, titulo, destaque=()):
    """Gráfico de barras horizontais em SVG a partir de {rótulo: valor}."""
    if not dados:
        return Markup("")
    itens = list(dados.items())
    mx = max(v for _, v in itens) or 1
    larg, lin = 360, 24
    h = lin * len(itens) + 24
    partes = [f'<text x="4" y="12" class="lbl">{escape(titulo)}</text>']
    for i, (rot, v) in enumerate(itens):
        y = 20 + i * lin
        w = max(2, (larg - 170) * v / mx)
        cls = "barra-alerta" if rot in destaque else "barra-svg"
        partes.append(f'<text x="4" y="{y + 12}" class="lbl">{escape(rot)}</text>'
                      f'<rect x="150" y="{y}" width="{w:.1f}" height="16" rx="3" class="{cls}"/>'
                      f'<text x="{154 + w:.1f}" y="{y + 12}" class="lbl">{v:g}</text>')
    return Markup(f'<svg viewBox="0 0 {larg} {h}" class="grafico" role="img" aria-label="{escape(titulo)}">'
                  + "".join(partes) + "</svg>")


@app.template_filter("br")
def f_br(v, casas=1):
    return "—" if v is None else f"{v:.{casas}f}".replace(".", ",")


@app.template_filter("dmy")
def f_dmy(d):
    return "" if not d else d.strftime("%d/%m/%Y")


@app.template_filter("dt")
def f_dt(d):
    if not d:
        return ""
    from datetime import timezone
    return d.replace(tzinfo=timezone.utc).astimezone(TZ).strftime("%d/%m/%Y %H:%M")


@app.context_processor
def inject():
    return {"csrf_token": csrf_token, "clinic": app.config["CLINIC"], "policy_version": POLICY_VERSION,
            "fatores": calc.FATORES_ATIVIDADE, "Q": Q, "condicoes": calc.CONDICOES, "hoje": date.today()}


# ───────────────────────── sessão, CSRF, cabeçalhos ─────────────────────────
@app.before_request
def carregar_usuario():
    g.user = None
    if request.endpoint == "static":
        return
    uid = session.get("uid")
    if uid:
        if time.time() - session.get("last", 0) > IDLE_SECONDS:
            session.clear()
            flash("Sessão encerrada por inatividade. Entre novamente.", "aviso")
        else:
            u = db.session.get(User, uid)
            if u and u.ativo:
                g.user = u
                session["last"] = time.time()
            else:
                session.clear()
    if request.method == "POST":
        tok = session.get("csrf", "")
        if not tok or not secrets.compare_digest(tok, request.form.get("_csrf", "")):
            abort(400, "Formulário expirado. Volte e tente novamente.")
    if (g.user and g.user.papel == "profissional" and os.environ.get("NUTRI_REQUIRE_2FA") == "1"
            and not g.user.totp_ativo
            and request.endpoint not in ("conta_2fa", "logout", "privacidade", "static", "offline",
                                         "service_worker", "manifest")):
        return redirect(url_for("conta_2fa"))


@app.after_request
def cabecalhos(resp):
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
        "frame-ancestors 'none'; form-action 'self'; base-uri 'self'; object-src 'none'")
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "same-origin"
    resp.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if os.environ.get("NUTRI_ENV") == "production":
        resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    if request.endpoint != "static":
        resp.headers["Cache-Control"] = "no-store"  # dados de saúde não ficam em cache do navegador
    return resp


def exige(papel):
    def deco(f):
        @wraps(f)
        def w(*a, **k):
            if not g.user:
                return redirect(url_for("login"))
            if g.user.papel != papel:
                abort(403)
            return f(*a, **k)
        return w
    return deco


pro = exige("profissional")


def meu_paciente(pid, editavel=False):
    p = db.session.get(Patient, pid)
    if not p or p.owner_id != g.user.id:
        abort(404)
    if editavel and p.anonimizado_em:
        abort(410, "Prontuário anonimizado: somente leitura.")
    return p


def paciente_logado():
    p = Patient.query.filter_by(user_id=g.user.id).first()
    if not p:
        abort(403)
    return p


def portal(f):
    """Portal do paciente: exige login e consentimento vigente para tratamento dos dados."""
    @wraps(f)
    @exige("paciente")
    def w(*a, **k):
        g.paciente = paciente_logado()
        if not tem_consentimento(g.paciente, "tratamento_saude"):
            return redirect(url_for("portal_consentimento"))
        return f(*a, **k)
    return w


# ───────────────────────── autenticação ─────────────────────────
@app.route("/")
def index():
    if not g.user:
        return redirect(url_for("login"))
    return redirect(url_for("dashboard" if g.user.papel == "profissional" else "portal_home"))


@app.get("/dashboard")
@pro
def dashboard():
    todos = (Patient.query.filter_by(owner_id=g.user.id)
             .options(selectinload(Patient.avaliacoes), selectinload(Patient.anamneses),
                      selectinload(Patient.integrativas), selectinload(Patient.teas),
                      selectinload(Patient.consentimentos), selectinload(Patient.solicitacoes),
                      selectinload(Patient.exames), selectinload(Patient.cardapios))
             .all())
    pn = automacao.painel(todos)
    abertas = (DataRequest.query.join(Patient)
               .filter(Patient.owner_id == g.user.id, DataRequest.status == "aberta").count())
    graf_imc = barras_svg(pn["dist"], "Classificação do IMC (última avaliação)",
                          destaque=("Obesidade grau I", "Obesidade grau II", "Obesidade grau III", "Baixo peso"))
    graf_prog = barras_svg({"Integrativa": pn["integrativa"], "TEA": pn["tea"],
                            "Sem reavaliação > 30 d": pn["sem_reav"]}, "Acompanhamento", destaque=("Sem reavaliação > 30 d",))
    return render_template("dashboard.html", pn=pn, abertas=abertas, graf_imc=graf_imc, graf_prog=graf_prog)


def falha_login(u, detalhe=None):
    if u:
        u.tentativas = (u.tentativas or 0) + 1
        if u.tentativas >= MAX_TENTATIVAS:
            u.bloqueado_ate = agora() + timedelta(minutes=BLOQUEIO_MIN)
            u.tentativas = 0
    audit("login_falha", "usuario", u.id if u else None, detalhe, user_id=u.id if u else 0)
    db.session.commit()


def concluir_login(u, via="senha"):
    u.tentativas, u.bloqueado_ate = 0, None
    audit("login", "usuario", u.id, via, user_id=u.id)
    db.session.commit()
    session.clear()  # evita fixação de sessão
    session["uid"], session["last"] = u.id, time.time()


@app.route("/login", methods=["GET", "POST"])
@limiter.limit("20 per minute", methods=["POST"])
def login():
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        senha = request.form.get("senha") or ""
        u = User.query.filter_by(email=email).first()
        if u and u.bloqueado_ate and u.bloqueado_ate > agora():
            flash("Acesso temporariamente bloqueado por tentativas excessivas. Tente mais tarde.", "erro")
        else:
            if u and u.ativo:
                ok, rehash = verificar_senha(u.senha_hash, senha)
            else:
                verificar_senha(DUMMY_HASH, senha)  # iguala o tempo de resposta (não revela se o e-mail existe)
                ok, rehash = False, False
            if ok:
                if rehash:
                    u.senha_hash = hash_senha(senha)
                if u.totp_ativo:
                    session.clear()
                    session["pre2fa"], session["pre2fa_ts"] = u.id, time.time()
                    audit("login_senha_ok", "usuario", u.id, user_id=u.id)
                    db.session.commit()
                    return redirect(url_for("login_2fa"))
                concluir_login(u)
                return redirect(url_for("index"))
            falha_login(u)
            flash("E-mail ou senha incorretos.", "erro")
    return render_template("login.html")


@app.post("/logout")
def logout():
    if g.user:
        audit("logout")
        db.session.commit()
    session.clear()
    return redirect(url_for("login"))


@app.route("/convite/<token>", methods=["GET", "POST"])
@limiter.limit("20 per minute", methods=["POST"])
def convite(token):
    try:
        pid = serializer().loads(token, max_age=INVITE_MAX_AGE)["pid"]
    except (BadSignature, SignatureExpired):
        return render_template("erro.html", titulo="Convite inválido ou expirado",
                               msg="Peça um novo convite ao seu nutricionista."), 400
    p = db.session.get(Patient, pid)
    if not p or p.user_id or p.anonimizado_em or not p.email:
        return render_template("erro.html", titulo="Convite já utilizado",
                               msg="Este convite não está mais disponível."), 400
    if request.method == "POST":
        senha, conf = request.form.get("senha", ""), request.form.get("conf", "")
        erro = senha_forte(senha) or (None if senha == conf else "As senhas não coincidem.")
        if not request.form.get("c_saude"):
            erro = "Para usar o portal é necessário autorizar o tratamento dos seus dados de saúde."
        if not erro and User.query.filter_by(email=p.email.lower()).first():
            erro = "Já existe uma conta com este e-mail. Fale com seu nutricionista."
        if erro:
            flash(erro, "erro")
        else:
            u = User(email=p.email.lower(), senha_hash=hash_senha(senha),
                     papel="paciente", nome=p.nome)
            db.session.add(u)
            db.session.flush()
            p.user_id = u.id
            registrar_consentimento(p, "tratamento_saude", True, "portal")
            if request.form.get("c_comunicacao"):
                registrar_consentimento(p, "comunicacao", True, "portal")
            audit("convite_aceito", "paciente", p.id, user_id=u.id)
            db.session.commit()
            flash("Conta criada. Entre com seu e-mail e senha.", "ok")
            return redirect(url_for("login"))
    return render_template("convite.html", p=p)


# ───────────────────────── área do profissional ─────────────────────────
@app.get("/pacientes")
@pro
def pacientes():
    import re
    q = (request.args.get("q") or "").strip()
    try:
        pagina = max(1, int(request.args.get("p", 1)))
    except ValueError:
        pagina = 1
    consulta = Patient.query.filter_by(owner_id=g.user.id)
    if q:
        tokens = [t for t in (token_consulta(w) for w in q.split()) if t]
        cod = re.sub(r"[%_\\]", "", q)
        cond = Patient.codigo.ilike(f"%{cod}%")
        if tokens:
            cond = or_(cond, and_(*[Patient.busca.contains(t) for t in tokens]))
        consulta = consulta.filter(cond)
    pag = (consulta.options(selectinload(Patient.avaliacoes))
           .order_by(Patient.id.desc()).paginate(page=pagina, per_page=25, error_out=False))
    abertas = (DataRequest.query.join(Patient).filter(Patient.owner_id == g.user.id,
                                                      DataRequest.status == "aberta").count())
    return render_template("pacientes.html", pag=pag, lista=pag.items, q=q, abertas=abertas)


def _ler_paciente(form, p=None):
    nome = (form.get("nome") or "").strip()
    if not nome:
        raise ValueError("Informe o nome completo.")
    sexo = form.get("sexo")
    if sexo not in ("M", "F"):
        raise ValueError("Selecione o sexo utilizado nos cálculos.")
    nasc = None
    if form.get("nascimento"):
        try:
            nasc = date.fromisoformat(form["nascimento"])
        except ValueError:
            raise ValueError("Data de nascimento inválida.")
        if not date(1900, 1, 1) <= nasc <= date.today():
            raise ValueError("Data de nascimento fora do intervalo válido.")
    email = (form.get("email") or "").strip().lower() or None
    if email and ("@" not in email or len(email) > 190):
        raise ValueError("E-mail inválido.")
    resp_nome = (form.get("responsavel_nome") or "").strip()[:190] or None
    if nasc and calc.idade_em(nasc, date.today()) < 18 and not resp_nome:
        raise ValueError("Paciente menor de 18 anos: informe o responsável legal (LGPD art. 14).")
    return {"nome": nome[:190], "sexo": sexo, "nascimento": nasc, "email": email,
            "responsavel_nome": resp_nome,
            "responsavel_contato": (form.get("responsavel_contato") or "").strip()[:190] or None,
            "telefone": (form.get("telefone") or "").strip()[:40] or None,
            "cidade": (form.get("cidade") or "").strip()[:120] or None,
            "profissao": (form.get("profissao") or "").strip()[:120] or None}


@app.route("/pacientes/novo", methods=["GET", "POST"])
@pro
def paciente_novo():
    if request.method == "POST":
        try:
            dados = _ler_paciente(request.form)
        except ValueError as e:
            flash(str(e), "erro")
            return render_template("paciente_form.html", p=None, f=request.form)
        p = Patient(codigo=novo_codigo(), owner_id=g.user.id, **dados)
        db.session.add(p)
        p.atualizar_busca()
        db.session.flush()
        if request.form.get("c_presencial"):
            registrar_consentimento(p, "tratamento_saude", True, "presencial")
        audit("paciente_criado", "paciente", p.id)
        db.session.commit()
        return redirect(url_for("paciente", pid=p.id))
    return render_template("paciente_form.html", p=None, f={})


@app.route("/pacientes/<int:pid>/editar", methods=["GET", "POST"])
@pro
def paciente_editar(pid):
    p = meu_paciente(pid, editavel=True)
    if request.method == "POST":
        try:
            dados = _ler_paciente(request.form, p)
        except ValueError as e:
            flash(str(e), "erro")
            return render_template("paciente_form.html", p=p, f=request.form)
        if p.user_id:
            dados.pop("email")  # e-mail de login só muda por solicitação de correção
        for k, v in dados.items():
            setattr(p, k, v)
        p.atualizar_busca()
        audit("paciente_editado", "paciente", p.id)
        db.session.commit()
        return redirect(url_for("paciente", pid=p.id))
    f = {"nome": p.nome, "sexo": p.sexo, "nascimento": p.nascimento.isoformat() if p.nascimento else "",
         "email": p.email or "", "telefone": p.telefone or "", "cidade": p.cidade or "",
         "profissao": p.profissao or "", "responsavel_nome": p.responsavel_nome or "",
         "responsavel_contato": p.responsavel_contato or ""}
    return render_template("paciente_form.html", p=p, f=f)


@app.get("/pacientes/<int:pid>")
@pro
def paciente(pid):
    p = meu_paciente(pid)
    audit("prontuario_visualizado", "paciente", p.id)
    db.session.commit()
    av = list(reversed(p.avaliacoes))
    graf_peso = linha_svg([(a.data.strftime("%d/%m/%y"), a.peso) for a in av], "Peso (kg)")
    graf_imc = linha_svg([(a.data.strftime("%d/%m/%y"), calc.imc(a.peso, a.altura_cm)) for a in av], "IMC")
    graf_cint = linha_svg([(a.data.strftime("%d/%m/%y"), a.cintura) for a in av], "Cintura (cm)")
    integ_graf = None
    if p.integrativas:
        notas = p.integrativas[0].dados.get("notas", {})
        rot = {c: r.split(" /")[0] for c, r, _ in MC.PILARES}
        integ_graf = barras_svg({rot[c]: n for c, n in notas.items()}, "Oito remédios naturais (0–10)",
                                destaque=tuple(rot[c] for c, n in notas.items() if n <= 4))
    tea_res = None
    if p.teas:
        tea_res = MC.triagem_tea(p.teas[0].dados)
    return render_template("paciente.html", p=p, graficos=[graf_peso, graf_imc, graf_cint],
                           insights=automacao.insights_paciente(p), integ_graf=integ_graf, tea_res=tea_res,
                           ana=p.anamneses[0] if p.anamneses else None,
                           consent={t: tem_consentimento(p, t) for t in ("tratamento_saude", "espiritualidade", "comunicacao")})


@app.post("/pacientes/<int:pid>/convite")
@pro
def paciente_convite(pid):
    p = meu_paciente(pid, editavel=True)
    if p.user_id:
        flash("Este paciente já possui acesso ao portal.", "aviso")
    elif not p.email:
        flash("Cadastre o e-mail do paciente para gerar o convite.", "erro")
    else:
        link = url_for("convite", token=serializer().dumps({"pid": p.id}), _external=True)
        audit("convite_gerado", "paciente", p.id)
        db.session.commit()
        flash(f"Link de convite (válido por 7 dias, uso único). Envie ao paciente: {link}", "ok")
    return redirect(url_for("paciente", pid=p.id))


# ── anamnese (profissional) ──
def _form_anamnese(p, destino, permite_check_espiritual, titulo):
    ana = p.anamneses[0] if p.anamneses else None
    dados = ana.dados if ana else {}
    espiritual_ok = tem_consentimento(p, "espiritualidade")
    return render_template("anamnese.html", p=p, secoes=AS.SECOES, dados=dados, destino=destino,
                           espiritual_ok=espiritual_ok, check_espiritual=permite_check_espiritual,
                           titulo=titulo, sensivel=AS.CAMPO_SENSIVEL)


@app.route("/pacientes/<int:pid>/anamnese", methods=["GET", "POST"])
@pro
def anamnese_pro(pid):
    p = meu_paciente(pid, editavel=True)
    if request.method == "POST":
        dados = AS.coletar(request.form, tem_consentimento(p, "espiritualidade"))
        db.session.add(Anamnese(paciente_id=p.id, autor_id=g.user.id, origem="profissional",
                                conteudo=json.dumps(dados, ensure_ascii=False), revisada=True))
        audit("anamnese_salva", "paciente", p.id, "profissional")
        db.session.commit()
        flash("Anamnese salva (nova versão registrada).", "ok")
        return redirect(url_for("paciente", pid=p.id))
    return _form_anamnese(p, url_for("anamnese_pro", pid=p.id), False, f"Anamnese — {p.nome}")


@app.post("/pacientes/<int:pid>/anamnese/<int:aid>/revisar")
@pro
def anamnese_revisar(pid, aid):
    p = meu_paciente(pid, editavel=True)
    a = db.session.get(Anamnese, aid)
    if not a or a.paciente_id != p.id:
        abort(404)
    a.revisada = True
    audit("anamnese_revisada", "anamnese", a.id)
    db.session.commit()
    return redirect(url_for("paciente", pid=p.id))


# ── avaliações ──
def _ler_extras(f, paciente, data_av):
    """Dobras, protocolo e circunferência do braço (validados)."""
    ex = {}
    prot = f.get("protocolo") or ""
    dob = {}
    for k, rot in Q.DOBRAS.items():
        v = num(f, f"dob_{k}", f"a dobra {rot.lower()}", 1, 80, obrigatorio=False)
        if v:
            dob[k] = v
    if prot:
        if prot not in Q.PROTOCOLOS:
            raise ValueError("Protocolo de dobras inválido.")
        ex["protocolo"] = prot
        ex["conversao"] = "brozek" if f.get("conversao") == "brozek" else "siri"
        idade = calc.idade_em(paciente.nascimento, data_av)
        if idade is None:
            raise ValueError("Cadastre a data de nascimento para calcular o % de gordura por dobras.")
        Q.gordura_por_dobras(prot, paciente.sexo, idade, dob, ex["conversao"])  # valida (levanta ValueError)
    if dob:
        ex["dobras"] = dob
    cb = num(f, "cb", "a circunferência do braço", 10, 100, obrigatorio=False)
    dct = num(f, "dct", "a dobra tricipital (braço)", 1, 80, obrigatorio=False)
    if cb and dct:
        ex["cb"], ex["dct"] = cb, dct
    return ex


@app.route("/pacientes/<int:pid>/avaliacao/nova", methods=["GET", "POST"])
@pro
def avaliacao_nova(pid):
    p = meu_paciente(pid, editavel=True)
    ultima = p.avaliacoes[0] if p.avaliacoes else None
    if request.method == "POST":
        f = request.form
        try:
            d = date.fromisoformat(f.get("data", ""))
            if d > date.today():
                raise ValueError("A data da avaliação não pode ser futura.")
            fator = f.get("fator")
            if fator not in calc.FATORES_ATIVIDADE:
                raise ValueError("Selecione o fator de atividade.")
            condicao = f.get("condicao", "nenhuma")
            if condicao not in calc.CONDICOES:
                raise ValueError("Condição inválida.")
            equacao = f.get("equacao") or None
            if equacao and equacao not in Q.EQUACOES:
                raise ValueError("Equação inválida.")
            peso_base = f.get("peso_base", "atual")
            if peso_base not in ("atual", "ideal", "ajustado"):
                raise ValueError("Peso de cálculo inválido.")
            inj = f.get("injuria", "nenhum")
            if inj not in Q.FATORES_INJURIA:
                raise ValueError("Fator de injúria inválido.")
            extras = _ler_extras(f, p, d)
            av = Avaliacao(
                paciente_id=p.id, autor_id=g.user.id, data=d, fator=fator, condicao=condicao,
                equacao=equacao, peso_base=peso_base, fator_injuria=Q.FATORES_INJURIA[inj][1],
                fator_valor=num(f, "fator_valor", "o fator de atividade personalizado", 1.0, 2.5, obrigatorio=False),
                extras=json.dumps(extras) if extras else None,
                peso=num(f, "peso", "o peso", 2, 500),
                altura_cm=num(f, "altura_cm", "a altura (cm)", 50, 250),
                cintura=num(f, "cintura", "a circunferência abdominal", 20, 300),
                quadril=num(f, "quadril", "a circunferência do quadril", 20, 300),
                gordura_pct=num(f, "gordura_pct", "o % de gordura", 2, 70, obrigatorio=False),
                ajuste_kcal=int(num(f, "ajuste_kcal", "o ajuste energético", -2000, 2000, obrigatorio=False) or 0),
                pct_carb=num(f, "pct_carb", "% carboidratos", 0, 100),
                pct_prot=num(f, "pct_prot", "% proteínas", 0, 100),
                pct_lip=num(f, "pct_lip", "% lipídios", 0, 100),
                observacoes=(f.get("observacoes") or "").strip()[:4000] or None,
            )
        except ValueError as e:
            flash(str(e), "erro")
            return render_template("avaliacao_form.html", p=p, f=f, ultima=ultima, Q=Q)
        db.session.add(av)
        audit("avaliacao_criada", "paciente", p.id)
        db.session.commit()
        if "tmb" not in calc.avaliar(av, p):
            flash("Avaliação salva, mas a equação escolhida não pôde ser aplicada (veja os avisos).", "aviso")
        return redirect(url_for("avaliacao_ver", pid=p.id, aid=av.id))
    menor = p.menor
    padrao = {"data": date.today().isoformat(), "altura_cm": f"{ultima.altura_cm:g}" if ultima else "",
              "fator": ultima.fator if ultima else "leve", "pct_carb": 50, "pct_prot": 20, "pct_lip": 30,
              "ajuste_kcal": 0, "condicao": "nenhuma", "equacao": "schofield" if menor else Q.PADRAO,
              "peso_base": "atual", "injuria": "nenhum"}
    return render_template("avaliacao_form.html", p=p, f=padrao, ultima=ultima, Q=Q)


def _av(p, aid):
    av = db.session.get(Avaliacao, aid)
    if not av or av.paciente_id != p.id:
        abort(404)
    return av


@app.get("/pacientes/<int:pid>/avaliacao/<int:aid>")
@pro
def avaliacao_ver(pid, aid):
    p = meu_paciente(pid)
    av = _av(p, aid)
    audit("avaliacao_visualizada", "avaliacao", av.id)
    db.session.commit()
    return render_template("avaliacao.html", p=p, av=av, r=calc.avaliar(av, p), portal=False)


@app.post("/pacientes/<int:pid>/avaliacao/<int:aid>/liberar")
@pro
def avaliacao_liberar(pid, aid):
    p = meu_paciente(pid, editavel=True)
    av = _av(p, aid)
    av.liberada = not av.liberada
    audit("avaliacao_liberada" if av.liberada else "avaliacao_ocultada", "avaliacao", av.id)
    db.session.commit()
    flash("Avaliação liberada para o paciente." if av.liberada else "Avaliação oculta do paciente.", "ok")
    return redirect(url_for("avaliacao_ver", pid=p.id, aid=av.id))


def _pdf_response(p, av):
    corpo = pdf_avaliacao(p, av, calc.avaliar(av, p), app.config["CLINIC"])
    resp = make_response(corpo)
    resp.headers["Content-Type"] = "application/pdf"
    resp.headers["Content-Disposition"] = f'inline; filename="avaliacao-{p.codigo}-{av.data.isoformat()}.pdf"'
    return resp


@app.get("/pacientes/<int:pid>/avaliacao/<int:aid>/pdf")
@pro
def avaliacao_pdf(pid, aid):
    p = meu_paciente(pid)
    av = _av(p, aid)
    audit("avaliacao_pdf", "avaliacao", av.id)
    db.session.commit()
    return _pdf_response(p, av)


# ── avaliação integrativa (oito remédios naturais) ──
@app.route("/pacientes/<int:pid>/integrativa/nova", methods=["GET", "POST"])
@pro
def integrativa_nova(pid):
    p = meu_paciente(pid, editavel=True)
    espiritual_ok = tem_consentimento(p, "espiritualidade")
    ultima = p.integrativas[0].dados if p.integrativas else {"notas": {}, "obs": {}}
    if request.method == "POST":
        try:
            d = date.fromisoformat(request.form.get("data", ""))
            if d > date.today():
                raise ValueError("A data não pode ser futura.")
            dados = MC.coletar_integrativa(request.form, espiritual_ok)
        except ValueError as e:
            flash(str(e), "erro")
        else:
            novo = AvaliacaoIntegrativa(paciente_id=p.id, autor_id=g.user.id, data=d,
                                        conteudo=json.dumps(dados, ensure_ascii=False))
            db.session.add(novo)
            audit("integrativa_criada", "paciente", p.id)
            db.session.commit()
            return redirect(url_for("integrativa_ver", pid=p.id, aid=novo.id))
    return render_template("integrativa_form.html", p=p, pilares=MC.PILARES, ultima=ultima,
                           espiritual_ok=espiritual_ok, sensivel=MC.PILAR_SENSIVEL)


@app.get("/pacientes/<int:pid>/integrativa/<int:aid>")
@pro
def integrativa_ver(pid, aid):
    p = meu_paciente(pid)
    a = db.session.get(AvaliacaoIntegrativa, aid)
    if not a or a.paciente_id != p.id:
        abort(404)
    audit("integrativa_visualizada", "integrativa", a.id)
    db.session.commit()
    dados = a.dados
    rot = {c: r.split(" /")[0] for c, r, _ in MC.PILARES}
    notas = dados.get("notas", {})
    graf = barras_svg({rot[c]: n for c, n in notas.items()}, "Notas por pilar (0–10)",
                      destaque=tuple(rot[c] for c, n in notas.items() if n <= 4))
    # comparação com a avaliação anterior
    ant = next((x for x in p.integrativas if x.id < a.id), None)
    comp = {}
    if ant:
        for c, n in notas.items():
            if c in ant.dados.get("notas", {}):
                comp[c] = n - ant.dados["notas"][c]
    return render_template("integrativa.html", p=p, a=a, dados=dados, graf=graf, comp=comp,
                           res=MC.resumo_integrativa(dados), pilares=MC.PILARES)


# ── TEA: avaliação alimentar e nutricional ──
@app.route("/pacientes/<int:pid>/tea/nova", methods=["GET", "POST"])
@pro
def tea_nova(pid):
    p = meu_paciente(pid, editavel=True)
    ult = p.teas[0].dados if p.teas else {}
    if request.method == "POST":
        try:
            d = date.fromisoformat(request.form.get("data", ""))
            if d > date.today():
                raise ValueError("A data não pode ser futura.")
            dados = MC.coletar_tea(request.form)
        except ValueError as e:
            flash(str(e), "erro")
        else:
            novo = AvaliacaoTEA(paciente_id=p.id, autor_id=g.user.id, data=d,
                                conteudo=json.dumps(dados, ensure_ascii=False))
            db.session.add(novo)
            audit("tea_criada", "paciente", p.id)
            db.session.commit()
            return redirect(url_for("tea_ver", pid=p.id, aid=novo.id))
    return render_template("tea_form.html", p=p, ult=ult, M=MC)


@app.get("/pacientes/<int:pid>/tea/<int:aid>")
@pro
def tea_ver(pid, aid):
    p = meu_paciente(pid)
    a = db.session.get(AvaliacaoTEA, aid)
    if not a or a.paciente_id != p.id:
        abort(404)
    audit("tea_visualizada", "tea", a.id)
    db.session.commit()
    nivel, atencao = MC.triagem_tea(a.dados)
    return render_template("tea.html", p=p, a=a, d=a.dados, nivel=nivel, atencao=atencao, M=MC)


# ── exportação / anonimização ──
def exportar(p):
    iso = lambda d: d.isoformat() if d else None
    return {
        "gerado_em": iso(agora()), "codigo": p.codigo,
        "dados_cadastrais": {"nome": p.nome, "nascimento": iso(p.nascimento), "sexo": p.sexo,
                             "telefone": p.telefone, "email": p.email, "cidade": p.cidade,
                             "profissao": p.profissao},
        "anamneses": [{"data": iso(a.criado_em), "origem": a.origem, "revisada": a.revisada,
                       "conteudo": a.dados} for a in reversed(p.anamneses)],
        "avaliacoes": [{"data": iso(a.data), "peso_kg": a.peso, "altura_cm": a.altura_cm,
                        "cintura_cm": a.cintura, "quadril_cm": a.quadril, "gordura_pct": a.gordura_pct,
                        "fator_atividade": a.fator, "ajuste_kcal": a.ajuste_kcal,
                        "observacoes": a.observacoes} for a in reversed(p.avaliacoes)],
        "responsavel_legal": {"nome": p.responsavel_nome, "contato": p.responsavel_contato},
        "avaliacoes_integrativas": [{"data": iso(i.data), **i.dados} for i in reversed(p.integrativas)],
        "avaliacoes_tea": [{"data": iso(t.data), **t.dados} for t in reversed(p.teas)],
        "cardapios": [{"titulo": c.titulo, "data": iso(c.criado_em), "metas": c.metas_dict,
                       "estrutura": c.estrutura} for c in reversed(p.cardapios)],
        "exames": [{"data_coleta": iso(e.data_coleta), "laboratorio": e.laboratorio, "itens": e.itens,
                    "observacoes": e.observacoes} for e in reversed(p.exames)],
        "documentos": [{"nome": d.nome, "data": iso(d.criado_em), "tamanho_bytes": d.tamanho} for d in p.documentos],
        "consentimentos": [{"tipo": c.tipo, "aceito": c.aceito, "versao": c.versao, "origem": c.origem,
                            "data": iso(c.data)} for c in p.consentimentos],
        "solicitacoes": [{"tipo": s.tipo, "status": s.status, "data": iso(s.criado_em)} for s in p.solicitacoes],
    }


def _json_download(dados, nome):
    resp = make_response(json.dumps(dados, ensure_ascii=False, indent=2))
    resp.headers["Content-Type"] = "application/json; charset=utf-8"
    resp.headers["Content-Disposition"] = f'attachment; filename="{nome}"'
    return resp


@app.get("/pacientes/<int:pid>/exportar.json")
@pro
def exportar_pro(pid):
    p = meu_paciente(pid)
    audit("exportacao", "paciente", p.id, "profissional")
    db.session.commit()
    return _json_download(exportar(p), f"prontuario-{p.codigo}.json")


def _apagar_arquivo(nome):
    try:
        (DATA_DIR / "uploads" / nome).unlink()
    except OSError:
        pass


def anonimizar(p):
    """Elimina dados identificáveis e conteúdo de anamnese; mantém apenas medidas sem identificação."""
    p.nome = f"Paciente anonimizado {p.codigo}"
    p.telefone = p.email = p.cidade = p.profissao = None
    p.nascimento = p.nascimento.replace(month=1, day=1) if p.nascimento else None  # só o ano
    for a in p.anamneses:
        a.conteudo = "{}"
    for av in p.avaliacoes:
        av.observacoes = None
    p.responsavel_nome = p.responsavel_contato = None
    p.busca = None
    for c in p.cardapios:
        c.conteudo, c.metas, c.titulo = '{"dias": []}', None, "removido"
    for e in p.exames:
        e.resultados, e.observacoes = '{"itens": {}}', None
    for d in p.documentos:
        _apagar_arquivo(d.arquivo)
        d.nome = "removido"
    for i in p.integrativas:
        i.conteudo = '{"notas": {}, "obs": {}}'
    for t in p.teas:
        t.conteudo = "{}"
    for s in p.solicitacoes:
        s.mensagem = None
    if p.conta:
        p.conta.ativo = False
        p.conta.email = f"anonimizado-{p.codigo.lower()}@invalido.local"
        p.conta.nome = "Anonimizado"
    p.anonimizado_em = agora()


@app.post("/pacientes/<int:pid>/anonimizar")
@pro
def paciente_anonimizar(pid):
    p = meu_paciente(pid, editavel=True)
    if request.form.get("confirma") != p.codigo:
        flash("Digite o código do paciente para confirmar a anonimização.", "erro")
        return redirect(url_for("paciente", pid=p.id))
    anonimizar(p)
    audit("paciente_anonimizado", "paciente", p.id)
    db.session.commit()
    flash("Dados identificáveis eliminados. Esta ação é irreversível.", "ok")
    return redirect(url_for("paciente", pid=p.id))


# ── solicitações do titular ──
@app.get("/solicitacoes")
@pro
def solicitacoes():
    lista = (DataRequest.query.join(Patient).filter(Patient.owner_id == g.user.id)
             .order_by(DataRequest.status, DataRequest.id.desc()).all())
    return render_template("solicitacoes.html", lista=lista)


@app.post("/solicitacoes/<int:rid>")
@pro
def solicitacao_resolver(rid):
    s = db.session.get(DataRequest, rid)
    if not s or s.paciente.owner_id != g.user.id or s.status != "aberta":
        abort(404)
    acao = request.form.get("acao")
    resposta = (request.form.get("resposta") or "").strip()[:2000]
    if acao == "negar" and not resposta:
        flash("Ao negar, registre a justificativa (ex.: obrigação legal de guarda do prontuário).", "erro")
        return redirect(url_for("solicitacoes"))
    s.status = "atendida" if acao == "atender" else "negada"
    s.resposta = resposta or None
    s.resolvido_em = agora()
    if acao == "atender" and s.tipo == "exclusao":
        anonimizar(s.paciente)
    audit("solicitacao_" + s.status, "solicitacao", s.id, s.tipo)
    db.session.commit()
    return redirect(url_for("solicitacoes"))


@app.get("/auditoria")
@pro
def auditoria():
    logs = AuditLog.query.order_by(AuditLog.id.desc()).limit(300).all()
    return render_template("auditoria.html", logs=logs)


# ───────────────────────── portal do paciente ─────────────────────────
@app.get("/portal")
@portal
def portal_home():
    p = g.paciente
    return render_template("portal.html", p=p, avs=[a for a in p.avaliacoes if a.liberada],
                           ana=p.anamneses[0] if p.anamneses else None)


@app.route("/portal/consentimento", methods=["GET", "POST"])
@exige("paciente")
def portal_consentimento():
    p = paciente_logado()
    if request.method == "POST":
        if request.form.get("c_saude"):
            registrar_consentimento(p, "tratamento_saude", True, "portal")
            audit("consentimento_aceito", "paciente", p.id)
            db.session.commit()
            return redirect(url_for("portal_home"))
        flash("Sem esta autorização não é possível usar o portal.", "erro")
    return render_template("consentimento.html", p=p)


@app.route("/portal/anamnese", methods=["GET", "POST"])
@portal
def portal_anamnese():
    p = g.paciente
    if request.method == "POST":
        marcou = bool(request.form.get("c_espiritual"))
        if marcou and not tem_consentimento(p, "espiritualidade"):
            registrar_consentimento(p, "espiritualidade", True, "portal")
        dados = AS.coletar(request.form, tem_consentimento(p, "espiritualidade"))
        db.session.add(Anamnese(paciente_id=p.id, autor_id=g.user.id, origem="paciente",
                                conteudo=json.dumps(dados, ensure_ascii=False), revisada=False))
        audit("anamnese_salva", "paciente", p.id, "paciente")
        db.session.commit()
        flash("Anamnese enviada. Seu nutricionista irá revisá-la.", "ok")
        return redirect(url_for("portal_home"))
    return _form_anamnese(p, url_for("portal_anamnese"), True, "Minha anamnese")


@app.get("/portal/avaliacao/<int:aid>")
@portal
def portal_avaliacao(aid):
    av = _av(g.paciente, aid)
    if not av.liberada:
        abort(404)
    return render_template("avaliacao.html", p=g.paciente, av=av, r=calc.avaliar(av, g.paciente), portal=True)


@app.get("/portal/avaliacao/<int:aid>/pdf")
@portal
def portal_avaliacao_pdf(aid):
    av = _av(g.paciente, aid)
    if not av.liberada:
        abort(404)
    audit("avaliacao_pdf", "avaliacao", av.id, "paciente")
    db.session.commit()
    return _pdf_response(g.paciente, av)


@app.get("/portal/meus-dados")
@portal
def portal_meus_dados():
    p = g.paciente
    return render_template("meus_dados.html", p=p,
                           consent={t: tem_consentimento(p, t) for t in ("tratamento_saude", "espiritualidade", "comunicacao")})


@app.get("/portal/exportar.json")
@portal
def portal_exportar():
    audit("exportacao", "paciente", g.paciente.id, "titular")
    db.session.commit()
    return _json_download(exportar(g.paciente), f"meus-dados-{g.paciente.codigo}.json")


def _redigir_espiritual(p):
    """Remove o campo espiritual de TODAS as versões da anamnese (revogação do consentimento)."""
    sec, campo = AS.CAMPO_SENSIVEL
    for a in p.anamneses:
        d = a.dados
        if sec in d and campo in d[sec]:
            del d[sec][campo]
            if not d[sec]:
                del d[sec]
            a.conteudo = json.dumps(d, ensure_ascii=False)


@app.post("/portal/consentimento/<tipo>/revogar")
@portal
def portal_revogar(tipo):
    p = g.paciente
    if tipo not in ("espiritualidade", "comunicacao"):
        abort(404)
    if tem_consentimento(p, tipo):
        registrar_consentimento(p, tipo, False, "portal")
        if tipo == "espiritualidade":
            _redigir_espiritual(p)
        audit("consentimento_revogado", "paciente", p.id, tipo)
        db.session.commit()
        flash("Consentimento revogado." + (" Os dados espirituais já registrados foram apagados."
                                           if tipo == "espiritualidade" else ""), "ok")
    return redirect(url_for("portal_meus_dados"))


@app.post("/portal/solicitar")
@portal
def portal_solicitar():
    p = g.paciente
    tipo = request.form.get("tipo")
    if tipo not in ("correcao", "exclusao", "informacao", "revogacao"):
        abort(400)
    msg = (request.form.get("mensagem") or "").strip()[:2000] or None
    db.session.add(DataRequest(paciente_id=p.id, tipo=tipo, mensagem=msg))
    audit("solicitacao_titular", "paciente", p.id, tipo)
    db.session.commit()
    flash("Solicitação registrada. O profissional responderá em até 15 dias.", "ok")
    return redirect(url_for("portal_meus_dados"))


# ───────────────────────── páginas públicas / PWA ─────────────────────────
@app.get("/privacidade")
def privacidade():
    return render_template("privacidade.html")


@app.get("/offline")
def offline():
    return render_template("offline.html")


@app.get("/sw.js")
def service_worker():
    resp = send_from_directory(app.static_folder, "sw.js", mimetype="application/javascript")
    resp.headers["Service-Worker-Allowed"] = "/"
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.get("/manifest.json")
def manifest():
    return send_from_directory(app.static_folder, "manifest.json", mimetype="application/manifest+json")


@app.errorhandler(400)
@app.errorhandler(403)
@app.errorhandler(404)
@app.errorhandler(410)
def erro_http(e):
    return render_template("erro.html", titulo=f"Erro {e.code}", msg=getattr(e, "description", "")), e.code


# ───────────────────────── linha de comando ─────────────────────────
@app.cli.command("criar-profissional")
def criar_profissional():
    """Cria o usuário profissional: flask criar-profissional"""
    import getpass
    nome = input("Nome: ").strip()
    email = input("E-mail: ").strip().lower()
    senha = getpass.getpass("Senha (mín. 10, letras e números): ")
    erro = senha_forte(senha)
    if erro:
        raise SystemExit(erro)
    if User.query.filter_by(email=email).first():
        raise SystemExit("E-mail já cadastrado.")
    db.session.add(User(email=email, nome=nome, papel="profissional", senha_hash=hash_senha(senha)))
    db.session.commit()
    print("Profissional criado.")


@app.cli.command("garantir-profissional")
def garantir_profissional():
    """Cria o primeiro usuário profissional somente se ainda não existir nenhum."""
    import getpass
    if User.query.filter_by(papel="profissional").first():
        print("Profissional já cadastrado.")
        return
    print("Primeiro acesso: cadastre o usuário profissional.")
    nome = input("Nome: ").strip()
    email = input("E-mail (será o seu login): ").strip().lower()
    while True:
        senha = getpass.getpass("Senha (mín. 10 caracteres, letras e números): ")
        erro = senha_forte(senha)
        if erro:
            print(erro)
        elif senha != getpass.getpass("Repita a senha: "):
            print("As senhas não coincidem.")
        else:
            break
    db.session.add(User(email=email, nome=nome, papel="profissional", senha_hash=hash_senha(senha)))
    db.session.commit()
    print("Profissional criado.")


# Módulos de rotas (importados ao final para usar os utilitários definidos acima)
import rotas_alimentos  # noqa: E402,F401
import rotas_conta  # noqa: E402,F401
import rotas_exames  # noqa: E402,F401
