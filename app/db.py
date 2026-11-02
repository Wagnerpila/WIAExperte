"""Banco SQLite: base de conhecimento (com busca FTS5), conversas, registros e uso."""
from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from . import config

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS usuarios (
    username   TEXT PRIMARY KEY,
    hash       TEXT NOT NULL,
    papel      TEXT NOT NULL DEFAULT 'tecnico',
    criado_em  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documentos (
    id           INTEGER PRIMARY KEY,
    nome         TEXT NOT NULL,
    tipo         TEXT NOT NULL,
    plataforma   TEXT,
    area         TEXT,
    equipamento  TEXT,
    caminho      TEXT,
    sha256       TEXT,
    tamanho      INTEGER,
    enviado_por  TEXT,
    enviado_em   TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'processando',
    erro         TEXT,
    n_trechos    INTEGER NOT NULL DEFAULT 0,
    observacao   TEXT,
    pai_id       INTEGER REFERENCES documentos(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_doc_nome ON documentos(nome);
CREATE INDEX IF NOT EXISTS ix_doc_sha ON documentos(sha256);

CREATE TABLE IF NOT EXISTS trechos (
    id      INTEGER PRIMARY KEY,
    doc_id  INTEGER NOT NULL REFERENCES documentos(id) ON DELETE CASCADE,
    ordem   INTEGER NOT NULL,
    texto   TEXT NOT NULL,
    local   TEXT,
    ts      TEXT
);
CREATE INDEX IF NOT EXISTS ix_trecho_doc ON trechos(doc_id, ordem);
CREATE INDEX IF NOT EXISTS ix_trecho_ts ON trechos(ts);

CREATE VIRTUAL TABLE IF NOT EXISTS trechos_fts USING fts5(
    texto, content='trechos', content_rowid='id',
    tokenize="unicode61 remove_diacritics 2 tokenchars '_'"
);
CREATE TRIGGER IF NOT EXISTS trechos_ai AFTER INSERT ON trechos BEGIN
    INSERT INTO trechos_fts(rowid, texto) VALUES (new.id, new.texto);
END;
CREATE TRIGGER IF NOT EXISTS trechos_ad AFTER DELETE ON trechos BEGIN
    INSERT INTO trechos_fts(trechos_fts, rowid, texto) VALUES ('delete', old.id, old.texto);
END;

CREATE TABLE IF NOT EXISTS conversas (
    id             INTEGER PRIMARY KEY,
    usuario        TEXT NOT NULL,
    titulo         TEXT NOT NULL,
    criada_em      TEXT NOT NULL,
    atualizada_em  TEXT NOT NULL
);

-- Histórico exatamente como enviado à API (append-only: nunca editar linhas antigas).
CREATE TABLE IF NOT EXISTS mensagens_api (
    id            INTEGER PRIMARY KEY,
    conversa_id   INTEGER NOT NULL REFERENCES conversas(id) ON DELETE CASCADE,
    papel         TEXT NOT NULL,
    conteudo_json TEXT NOT NULL,
    criada_em     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_msgapi_conv ON mensagens_api(conversa_id, id);

-- O que a interface mostra (texto final + resumo das ferramentas usadas).
CREATE TABLE IF NOT EXISTS mensagens_tela (
    id               INTEGER PRIMARY KEY,
    conversa_id      INTEGER NOT NULL REFERENCES conversas(id) ON DELETE CASCADE,
    papel            TEXT NOT NULL,
    texto            TEXT NOT NULL,
    ferramentas_json TEXT,
    anexos_json      TEXT,
    criada_em        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_msgtela_conv ON mensagens_tela(conversa_id, id);

CREATE TABLE IF NOT EXISTS uso_api (
    id             INTEGER PRIMARY KEY,
    conversa_id    INTEGER,
    usuario        TEXT,
    ts             TEXT NOT NULL,
    modelo         TEXT,
    finalidade     TEXT NOT NULL DEFAULT 'chat',
    input_tokens   INTEGER DEFAULT 0,
    output_tokens  INTEGER DEFAULT 0,
    cache_read     INTEGER DEFAULT 0,
    cache_write    INTEGER DEFAULT 0,
    segundos       REAL DEFAULT 0,
    stop_reason    TEXT
);
CREATE INDEX IF NOT EXISTS ix_uso_ts ON uso_api(ts);

CREATE TABLE IF NOT EXISTS ferramentas_log (
    id           INTEGER PRIMARY KEY,
    conversa_id  INTEGER,
    ts           TEXT NOT NULL,
    nome         TEXT NOT NULL,
    entrada_json TEXT,
    ok           INTEGER NOT NULL DEFAULT 1,
    n_resultados INTEGER
);
CREATE INDEX IF NOT EXISTS ix_ferr_ts ON ferramentas_log(ts);

CREATE TABLE IF NOT EXISTS notas (
    id         INTEGER PRIMARY KEY,
    alvo       TEXT NOT NULL,
    texto      TEXT NOT NULL,
    fonte      TEXT,
    autor      TEXT,
    origem     TEXT NOT NULL DEFAULT 'manual',
    doc_id     INTEGER REFERENCES documentos(id) ON DELETE SET NULL,
    criada_em  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ocorrencias (
    id                INTEGER PRIMARY KEY,
    data_hora         TEXT NOT NULL,
    equipamento       TEXT NOT NULL,
    tag               TEXT,
    area              TEXT,
    plataforma        TEXT,
    sintoma           TEXT NOT NULL,
    causa_raiz        TEXT,
    categoria_causa   TEXT,
    acao              TEXT,
    blocos_parametros TEXT,
    tempo_parada_min  REAL,
    licao             TEXT,
    fontes            TEXT,
    autor             TEXT,
    origem            TEXT NOT NULL DEFAULT 'manual',
    doc_id            INTEGER REFERENCES documentos(id) ON DELETE SET NULL,
    criada_em         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_oc_data ON ocorrencias(data_hora);
CREATE INDEX IF NOT EXISTS ix_oc_eq ON ocorrencias(equipamento);

CREATE TABLE IF NOT EXISTS glossario (
    termo          TEXT PRIMARY KEY COLLATE NOCASE,
    significado    TEXT NOT NULL,
    autor          TEXT,
    atualizado_em  TEXT NOT NULL
);

-- Eventos de alarme importados de CSV (para os indicadores ISA-18.2 / EEMUA 191).
CREATE TABLE IF NOT EXISTS alarmes (
    id          INTEGER PRIMARY KEY,
    ts          TEXT NOT NULL,
    tag         TEXT NOT NULL,
    mensagem    TEXT,
    prioridade  TEXT,
    estado      TEXT,
    area        TEXT,
    doc_id      INTEGER REFERENCES documentos(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_al_ts ON alarmes(ts);
CREATE INDEX IF NOT EXISTS ix_al_tag ON alarmes(tag, ts);

-- Documentos vistos em buscas no GreenDocs (e o que já foi baixado para a base).
CREATE TABLE IF NOT EXISTS greendocs_itens (
    nome             TEXT PRIMARY KEY,
    revisao          TEXT,
    descricao        TEXT,
    link             TEXT NOT NULL,
    visto_em         TEXT NOT NULL,
    revisao_baixada  TEXT,
    doc_ids          TEXT,
    baixado_em       TEXT
);
"""


def agora() -> str:
    """Horário atual em UTC, ISO 8601."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect(path: Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path or config.DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def session(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    conn = connect(path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(path: Path | None = None) -> None:
    p = path or config.DB_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    config.FILES_DIR.mkdir(parents=True, exist_ok=True)
    with session(p) as conn:
        conn.executescript(SCHEMA)
        from . import enderecos  # import tardio: enderecos usa db
        enderecos.garantir_tabelas(conn)


_TOKEN_RE = re.compile(r"[\w]+", re.UNICODE)


def fts_query(texto: str) -> str:
    """Converte texto livre em consulta FTS5 segura.

    Cada termo vira uma string entre aspas (sem operadores do usuário) e os
    termos são unidos por OR; o ranking BM25 prioriza trechos com mais termos.
    Termos com hífen (tags como FIC-101) também entram como frase exata.
    """
    termos: list[str] = []
    for bruto in texto.split():
        partes = _TOKEN_RE.findall(bruto)
        if not partes:
            continue
        if len(partes) > 1:
            termos.append('"' + " ".join(partes) + '"')
        termos.extend(f'"{p}"' for p in partes)
    vistos: list[str] = []
    for t in termos:
        if t.lower() not in (v.lower() for v in vistos):
            vistos.append(t)
    return " OR ".join(vistos[:40])
