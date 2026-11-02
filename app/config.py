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
COOKIE_SECURE = _bool("WIA_COOKIE_SECURE", True)
MAX_UPLOAD_MB = int(os.environ.get("WIA_MAX_UPLOAD_MB", "200"))

# Preço em US$ por milhão de tokens, só para a estimativa de custo no painel.
PRICE_INPUT = float(os.environ.get("WIA_PRICE_INPUT", "4.0"))
PRICE_OUTPUT = float(os.environ.get("WIA_PRICE_OUTPUT", "20.0"))
PRICE_CACHE_READ = float(os.environ.get("WIA_PRICE_CACHE_READ", "0.20"))
PRICE_CACHE_WRITE = float(os.environ.get("WIA_PRICE_CACHE_WRITE", "5.0"))
