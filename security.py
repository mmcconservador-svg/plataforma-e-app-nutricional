"""Criptografia, senhas, 2FA e índice cego (LGPD art. 46)."""
import hashlib
import hmac
import os
import re
import secrets
import time
import unicodedata
from pathlib import Path

import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from werkzeug.security import check_password_hash

_fernet = None
_blind = b""
_ph = PasswordHasher()


def _arquivo(path, gerar):
    p = Path(path)
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(gerar())
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass
    return p.read_text().strip()


def init_keys(keys_dir):
    """Chaves de campo (NUTRI_FIELD_KEYS, várias = rotação) e de índice cego (NUTRI_BLIND_KEY) ou arquivos em keys_dir."""
    global _fernet, _blind
    raw = os.environ.get("NUTRI_FIELD_KEYS", "").strip() or _arquivo(
        Path(keys_dir) / "field.key", lambda: Fernet.generate_key().decode())
    _fernet = MultiFernet([Fernet(k.strip().encode()) for k in raw.split(",") if k.strip()])
    _blind = (os.environ.get("NUTRI_BLIND_KEY", "").strip() or _arquivo(
        Path(keys_dir) / "blind.key", lambda: secrets.token_hex(32))).encode()


def load_secret(keys_dir):
    return os.environ.get("SECRET_KEY") or _arquivo(Path(keys_dir) / "secret.key", lambda: secrets.token_hex(32))


# ───────── campos e arquivos criptografados ─────────
def encrypt(texto):
    return _fernet.encrypt(texto.encode()).decode()


def decrypt(token):
    try:
        return _fernet.decrypt(token.encode()).decode()
    except InvalidToken:
        return "[dado ilegível: chave incorreta]"


def encrypt_bytes(dados):
    return _fernet.encrypt(dados)


def decrypt_bytes(dados):
    return _fernet.decrypt(dados)


# ───────── senhas (Argon2id; migra hashes antigos automaticamente) ─────────
def hash_senha(senha):
    return _ph.hash(senha)


def verificar_senha(hash_guardado, senha):
    """Retorna (ok, precisa_rehash)."""
    if hash_guardado.startswith("$argon2"):
        try:
            _ph.verify(hash_guardado, senha)
            return True, _ph.check_needs_rehash(hash_guardado)
        except (VerificationError, InvalidHashError):
            return False, False
    ok = check_password_hash(hash_guardado, senha)
    return ok, ok


def senha_forte(senha):
    """Retorna mensagem de erro ou None."""
    if len(senha) < 10:
        return "A senha deve ter pelo menos 10 caracteres."
    if not re.search(r"[A-Za-z]", senha) or not re.search(r"\d", senha):
        return "A senha deve conter letras e números."
    if senha.lower() in {"1234567890", "senha12345", "password123"}:
        return "Senha muito comum."
    return None


# ───────── 2FA (TOTP) ─────────
def novo_totp_secret():
    return pyotp.random_base32()


def totp_uri(secret, conta, emissor):
    return pyotp.TOTP(secret).provisioning_uri(name=conta, issuer_name=emissor)


def totp_verifica(secret, codigo, ultimo_contador=0):
    """Retorna o contador aceito (para impedir reutilização) ou None."""
    codigo = re.sub(r"\D", "", codigo or "")
    if len(codigo) != 6:
        return None
    t = pyotp.TOTP(secret)
    agora = int(time.time()) // 30
    for off in (-1, 0, 1):
        c = agora + off
        if c > ultimo_contador and hmac.compare_digest(t.at(c * 30), codigo):
            return c
    return None


def gerar_recovery(n=8):
    return [secrets.token_hex(5) for _ in range(n)]


def hash_recovery(codigo, chave):
    limpo = re.sub(r"[^0-9a-f]", "", (codigo or "").lower())
    return hashlib.sha256((chave + limpo).encode()).hexdigest()


# ───────── índice cego para busca de pacientes por nome ─────────
def normalizar(texto):
    t = unicodedata.normalize("NFKD", texto or "")
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", t)).strip()


def _h(parte):
    return hmac.new(_blind, parte.encode(), hashlib.sha256).hexdigest()[:16]


def tokens_busca(nome, minimo=3, maximo=12):
    """HMAC de todos os prefixos (3..12 letras) de cada palavra: permite buscar por início de nome sem guardá-lo em claro."""
    out = set()
    for palavra in normalizar(nome).split():
        palavra = palavra[:maximo]
        for n in range(minimo, len(palavra) + 1):
            out.add(_h(palavra[:n]))
    return " ".join(sorted(out))


def token_consulta(palavra, minimo=3, maximo=12):
    p = normalizar(palavra)[:maximo]
    return _h(p) if len(p) >= minimo else None
