import os
import sys
import tempfile
from pathlib import Path

import pytest

_TMP = tempfile.mkdtemp(prefix="wia-test-")
os.environ["WIA_DATA_DIR"] = _TMP
os.environ["WIA_COOKIE_SECURE"] = "false"
os.environ["WIA_DESCRIBE_IMAGES"] = "false"
os.environ["WIA_SECRET_KEY"] = "teste-" + "x" * 40
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config, db  # noqa: E402


@pytest.fixture(autouse=True)
def banco_limpo():
    """Cada teste começa com um banco vazio."""
    if config.DB_PATH.exists():
        with db.session() as conn:
            for t in ["trechos", "documentos", "ocorrencias", "notas", "glossario", "alarmes", "conversas",
                      "uso_api", "ferramentas_log", "usuarios"]:
                conn.execute(f"DELETE FROM {t}")
    db.init_db()
    yield
