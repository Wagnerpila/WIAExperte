"""Usuários, senhas (scrypt) e proteção simples contra força bruta no login."""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import threading
import time
from collections import defaultdict

from fastapi import HTTPException, Request

from . import config, db

log = logging.getLogger("wia.auth")
_N, _R, _P = 2 ** 14, 8, 1


def hash_senha(senha: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(senha.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${h.hex()}"


def conferir_senha(senha: str, armazenado: str) -> bool:
    try:
        _, n, r, p, salt, h = armazenado.split("$")
        calc = hashlib.scrypt(senha.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p),
                              dklen=len(bytes.fromhex(h)))
        return hmac.compare_digest(calc.hex(), h)
    except (ValueError, TypeError):
        return False


SENHA_MINIMA = 8


def criar_usuario(username: str, senha: str, papel: str = "tecnico") -> None:
    if not username.strip():
        raise ValueError("informe o nome do usuário")
    if len(senha) < SENHA_MINIMA:
        raise ValueError(f"a senha precisa ter pelo menos {SENHA_MINIMA} caracteres")
    with db.session() as conn:
        conn.execute("INSERT INTO usuarios (username, hash, papel, criado_em) VALUES (?,?,?,?)"
                     " ON CONFLICT(username) DO UPDATE SET hash=excluded.hash, papel=excluded.papel",
                     (username.strip().lower(), hash_senha(senha), papel, db.agora()))


def garantir_admin_do_ambiente() -> None:
    """Cria ou atualiza o administrador definido em WIA_ADMIN_USUARIO / WIA_ADMIN_SENHA.

    Roda a cada inicialização: trocar a senha no Ambiente do Easypanel e implantar de novo
    redefine a senha desse usuário.
    """
    usuario = config.ADMIN_USUARIO.strip()
    senha = config.ADMIN_SENHA
    if not usuario and not senha:
        return
    try:
        criar_usuario(usuario, senha, "admin")
        log.info("administrador '%s' configurado pelo ambiente", usuario.lower())
    except ValueError as e:
        log.error("WIA_ADMIN_USUARIO/WIA_ADMIN_SENHA inválidos: %s", e)


def autenticar(username: str, senha: str) -> dict | None:
    with db.session() as conn:
        row = conn.execute("SELECT username, hash, papel FROM usuarios WHERE username = ?",
                           (username.strip().lower(),)).fetchone()
    if row and conferir_senha(senha, row["hash"]):
        return {"username": row["username"], "papel": row["papel"]}
    conferir_senha(senha, hash_senha("x" * 12)) if not row else None  # tempo parecido com usuário inexistente
    return None


# Limite de tentativas: 5 falhas em 10 minutos bloqueiam o IP por 10 minutos.
_falhas: dict[str, list[float]] = defaultdict(list)
_lock = threading.Lock()


def bloqueado(ip: str) -> bool:
    with _lock:
        agora = time.time()
        _falhas[ip] = [t for t in _falhas[ip] if agora - t < 600]
        return len(_falhas[ip]) >= 5


def registrar_falha(ip: str) -> None:
    with _lock:
        _falhas[ip].append(time.time())


def usuario_atual(request: Request) -> dict:
    u = request.session.get("usuario")
    if not u:
        raise HTTPException(status_code=401, detail="não autenticado")
    return u


def carregar_secret() -> str:
    if config.SECRET_KEY:
        return config.SECRET_KEY
    arq = config.DATA_DIR / "secret.key"
    if arq.is_file():
        return arq.read_text().strip()
    arq.parent.mkdir(parents=True, exist_ok=True)
    chave = secrets.token_urlsafe(48)
    arq.write_text(chave)
    return chave
