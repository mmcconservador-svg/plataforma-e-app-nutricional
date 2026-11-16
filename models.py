import json
from datetime import datetime, timezone

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.types import Text, TypeDecorator

from security import decrypt, encrypt

db = SQLAlchemy()


def agora():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class EncryptedText(TypeDecorator):
    """Texto criptografado em repouso (Fernet/AES)."""
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return None if value is None else encrypt(value)

    def process_result_value(self, value, dialect):
        return None if value is None else decrypt(value)


class User(db.Model):
    __tablename__ = "usuarios"
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(190), unique=True, nullable=False, index=True)
    senha_hash = db.Column(db.String(255), nullable=False)
    papel = db.Column(db.String(20), nullable=False)  # profissional | paciente
    nome = db.Column(db.String(190), nullable=False)
    ativo = db.Column(db.Boolean, default=True, nullable=False)
    tentativas = db.Column(db.Integer, default=0, nullable=False)
    bloqueado_ate = db.Column(db.DateTime)
    criado_em = db.Column(db.DateTime, default=agora)


class Patient(db.Model):
    __tablename__ = "pacientes"
    id = db.Column(db.Integer, primary_key=True)
    codigo = db.Column(db.String(20), unique=True, nullable=False)
    owner_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), unique=True)
    nome = db.Column(EncryptedText, nullable=False)
    nascimento = db.Column(db.Date)
    sexo = db.Column(db.String(1), nullable=False)  # M | F (sexo usado nas equações)
    telefone = db.Column(EncryptedText)
    email = db.Column(EncryptedText)
    cidade = db.Column(EncryptedText)
    profissao = db.Column(EncryptedText)
    # Menores de 18 anos (LGPD art. 14): consentimento e contato via responsável legal.
    responsavel_nome = db.Column(EncryptedText)
    responsavel_contato = db.Column(EncryptedText)
    criado_em = db.Column(db.DateTime, default=agora)
    anonimizado_em = db.Column(db.DateTime)

    conta = db.relationship("User", foreign_keys=[user_id])
    anamneses = db.relationship("Anamnese", backref="paciente", order_by="Anamnese.id.desc()")
    avaliacoes = db.relationship("Avaliacao", backref="paciente", order_by="Avaliacao.data.desc(), Avaliacao.id.desc()")
    consentimentos = db.relationship("Consent", backref="paciente", order_by="Consent.id")
    solicitacoes = db.relationship("DataRequest", backref="paciente", order_by="DataRequest.id.desc()")
    integrativas = db.relationship("AvaliacaoIntegrativa", backref="paciente",
                                   order_by="AvaliacaoIntegrativa.data.desc(), AvaliacaoIntegrativa.id.desc()")
    teas = db.relationship("AvaliacaoTEA", backref="paciente",
                           order_by="AvaliacaoTEA.data.desc(), AvaliacaoTEA.id.desc()")

    @property
    def idade(self):
        from datetime import date
        from calc import idade_em
        return idade_em(self.nascimento, date.today())

    @property
    def menor(self):
        return self.idade is not None and self.idade < 18


class Consent(db.Model):
    """Registro imutável de consentimentos: revogar = nova linha com aceito=False."""
    __tablename__ = "consentimentos"
    id = db.Column(db.Integer, primary_key=True)
    paciente_id = db.Column(db.Integer, db.ForeignKey("pacientes.id"), nullable=False, index=True)
    tipo = db.Column(db.String(40), nullable=False)  # tratamento_saude | espiritualidade | comunicacao
    versao = db.Column(db.String(10), nullable=False)
    aceito = db.Column(db.Boolean, nullable=False)
    origem = db.Column(db.String(20), nullable=False)  # portal | presencial
    data = db.Column(db.DateTime, default=agora)


class Anamnese(db.Model):
    """Cada gravação cria uma nova versão (histórico preservado)."""
    __tablename__ = "anamneses"
    id = db.Column(db.Integer, primary_key=True)
    paciente_id = db.Column(db.Integer, db.ForeignKey("pacientes.id"), nullable=False, index=True)
    autor_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=False)
    origem = db.Column(db.String(20), nullable=False)  # paciente | profissional
    conteudo = db.Column(EncryptedText, nullable=False)  # JSON
    revisada = db.Column(db.Boolean, default=False, nullable=False)
    criado_em = db.Column(db.DateTime, default=agora)

    @property
    def dados(self):
        try:
            return json.loads(self.conteudo)
        except (ValueError, TypeError):
            return {}


class Avaliacao(db.Model):
    __tablename__ = "avaliacoes"
    id = db.Column(db.Integer, primary_key=True)
    paciente_id = db.Column(db.Integer, db.ForeignKey("pacientes.id"), nullable=False, index=True)
    autor_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=False)
    data = db.Column(db.Date, nullable=False)
    peso = db.Column(db.Float, nullable=False)
    altura_cm = db.Column(db.Float, nullable=False)
    cintura = db.Column(db.Float, nullable=False)
    quadril = db.Column(db.Float, nullable=False)
    gordura_pct = db.Column(db.Float)
    fator = db.Column(db.String(20), nullable=False)
    ajuste_kcal = db.Column(db.Integer, default=0, nullable=False)
    condicao = db.Column(db.String(20), default="nenhuma", nullable=False)
    pct_carb = db.Column(db.Float, default=50, nullable=False)
    pct_prot = db.Column(db.Float, default=20, nullable=False)
    pct_lip = db.Column(db.Float, default=30, nullable=False)
    observacoes = db.Column(EncryptedText)
    liberada = db.Column(db.Boolean, default=False, nullable=False)  # só o profissional libera ao paciente
    criado_em = db.Column(db.DateTime, default=agora)


class _JsonMixin:
    @property
    def dados(self):
        try:
            return json.loads(self.conteudo)
        except (ValueError, TypeError):
            return {}


class AvaliacaoIntegrativa(_JsonMixin, db.Model):
    """Pontuação (0-10) dos oito remédios naturais. Conteúdo criptografado."""
    __tablename__ = "avaliacoes_integrativas"
    id = db.Column(db.Integer, primary_key=True)
    paciente_id = db.Column(db.Integer, db.ForeignKey("pacientes.id"), nullable=False, index=True)
    autor_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=False)
    data = db.Column(db.Date, nullable=False)
    conteudo = db.Column(EncryptedText, nullable=False)  # {"notas": {...}, "obs": {...}}
    criado_em = db.Column(db.DateTime, default=agora)


class AvaliacaoTEA(_JsonMixin, db.Model):
    """Avaliação nutricional/alimentar no Transtorno do Espectro Autista (não é instrumento diagnóstico)."""
    __tablename__ = "avaliacoes_tea"
    id = db.Column(db.Integer, primary_key=True)
    paciente_id = db.Column(db.Integer, db.ForeignKey("pacientes.id"), nullable=False, index=True)
    autor_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=False)
    data = db.Column(db.Date, nullable=False)
    conteudo = db.Column(EncryptedText, nullable=False)
    criado_em = db.Column(db.DateTime, default=agora)


class DataRequest(db.Model):
    """Requisições do titular (LGPD art. 18)."""
    __tablename__ = "solicitacoes_titular"
    id = db.Column(db.Integer, primary_key=True)
    paciente_id = db.Column(db.Integer, db.ForeignKey("pacientes.id"), nullable=False, index=True)
    tipo = db.Column(db.String(30), nullable=False)  # correcao | exclusao | informacao | revogacao
    mensagem = db.Column(EncryptedText)
    status = db.Column(db.String(20), default="aberta", nullable=False)  # aberta | atendida | negada
    resposta = db.Column(EncryptedText)
    criado_em = db.Column(db.DateTime, default=agora)
    resolvido_em = db.Column(db.DateTime)


class AuditLog(db.Model):
    __tablename__ = "auditoria"
    id = db.Column(db.Integer, primary_key=True)
    ts = db.Column(db.DateTime, default=agora, index=True)
    user_id = db.Column(db.Integer)
    acao = db.Column(db.String(60), nullable=False)
    entidade = db.Column(db.String(40))
    entidade_id = db.Column(db.Integer)
    ip = db.Column(db.String(64))
    detalhe = db.Column(db.String(255))
