"""Configuração lida de variáveis de ambiente (arquivo .env opcional)."""
from __future__ import annotations

import os
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    """Carrega KEY=VALUE de um .env simples, sem sobrescrever variáveis já definidas."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


BASE_DIR = Path(__file__).resolve().parent.parent
_load_dotenv(BASE_DIR / ".env")


def _bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "sim", "yes", "on"}


DATA_DIR = Path(os.environ.get("WIA_DATA_DIR", BASE_DIR / "data")).resolve()
FILES_DIR = DATA_DIR / "arquivos"
DB_PATH = DATA_DIR / "wia.db"
PROMPT_PATH = Path(os.environ.get("WIA_PROMPT_PATH", BASE_DIR / "prompts" / "system_prompt.md"))

MODEL = os.environ.get("WIA_MODEL", "claude-opus-5-5")
EFFORT = os.environ.get("WIA_EFFORT", "high")
MAX_TOKENS = int(os.environ.get("WIA_MAX_TOKENS", "16000"))
MAX_TOOL_ITERATIONS = int(os.environ.get("WIA_MAX_TOOL_ITERATIONS", "15"))
USE_FALLBACKS = _bool("WIA_USE_FALLBACKS", True)
DESCRIBE_IMAGES = _bool("WIA_DESCRIBE_IMAGES", True)

SECRET_KEY = os.environ.get("WIA_SECRET_KEY", "")
# Administrador criado/atualizado a cada inicialização (configurável pelo Ambiente do Easypanel).
ADMIN_USUARIO = os.environ.get("WIA_ADMIN_USUARIO", "")
ADMIN_SENHA = os.environ.get("WIA_ADMIN_SENHA", "")
COOKIE_SECURE = _bool("WIA_COOKIE_SECURE", True)
MAX_UPLOAD_MB = int(os.environ.get("WIA_MAX_UPLOAD_MB", "200"))

# Recursos por modelo: o parâmetro `effort` não existe nos modelos mais antigos, e o
# fallback do servidor (`fallbacks: "default"`) só existe em alguns modelos atuais.
_SEM_EFFORT = ("claude-haiku-4-5", "claude-sonnet-4-5", "claude-3")
_COM_FALLBACK = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}


def suporta_effort(modelo: str) -> bool:
    return not modelo.startswith(_SEM_EFFORT)


def suporta_fallback(modelo: str) -> bool:
    return modelo in _COM_FALLBACK


# Preço em US$ por milhão de tokens (entrada, saída), só para a estimativa de custo no painel.
_PRECOS = {
    "claude-fable-5-1": (10.0, 50.0), "claude-opus-5-5": (4.0, 20.0), "claude-opus-5": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0), "claude-sonnet-5-5": (2.0, 10.0), "claude-sonnet-5": (2.0, 10.0),
    "claude-sonnet-4-6": (3.0, 15.0), "claude-haiku-5-5": (0.10, 0.50), "claude-haiku-4-5": (1.0, 5.0),
}
_pin, _pout = _PRECOS.get(MODEL, (4.0, 20.0))
PRICE_INPUT = float(os.environ.get("WIA_PRICE_INPUT") or _pin)
PRICE_OUTPUT = float(os.environ.get("WIA_PRICE_OUTPUT") or _pout)
PRICE_CACHE_READ = float(os.environ.get("WIA_PRICE_CACHE_READ") or PRICE_INPUT * 0.1)
PRICE_CACHE_WRITE = float(os.environ.get("WIA_PRICE_CACHE_WRITE") or PRICE_INPUT * 1.25)
