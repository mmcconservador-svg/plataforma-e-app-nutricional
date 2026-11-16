"""Criptografia de campos sensíveis (LGPD art. 46) e utilidades de segurança."""
import os
import re
import secrets
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

_fernet = None


def init_keys(instance_path):
    """Carrega as chaves de NUTRI_FIELD_KEYS (várias separadas por vírgula = rotação) ou do arquivo instance/field.key."""
    global _fernet
    raw = os.environ.get("NUTRI_FIELD_KEYS", "").strip()
    if not raw:
        p = Path(instance_path) / "field.key"
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(Fernet.generate_key())
            os.chmod(p, 0o600)
        raw = p.read_text().strip()
    _fernet = MultiFernet([Fernet(k.strip().encode()) for k in raw.split(",") if k.strip()])


def load_secret(instance_path):
    env = os.environ.get("SECRET_KEY")
    if env:
        return env
    p = Path(instance_path) / "secret.key"
    if not p.exists():
        p.write_text(secrets.token_hex(32))
        os.chmod(p, 0o600)
    return p.read_text().strip()


def encrypt(texto):
    return _fernet.encrypt(texto.encode()).decode()


def decrypt(token):
    try:
        return _fernet.decrypt(token.encode()).decode()
    except InvalidToken:
        return "[dado ilegível: chave incorreta]"


def senha_forte(senha):
    """Retorna mensagem de erro ou None."""
    if len(senha) < 10:
        return "A senha deve ter pelo menos 10 caracteres."
    if not re.search(r"[A-Za-z]", senha) or not re.search(r"\d", senha):
        return "A senha deve conter letras e números."
    if senha.lower() in {"1234567890", "senha12345", "password123"}:
        return "Senha muito comum."
    return None
