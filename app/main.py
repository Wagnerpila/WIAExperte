"""API web e páginas do Assistente de Automação e Instrumentação."""
from __future__ import annotations

import json
import re
import shutil
import time
import uuid
import logging
import tempfile
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware

from . import agent, auth, config, db, ingest, painel

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

STATIC = Path(__file__).parent / "static"

db.init_db()
auth.garantir_admin_do_ambiente()
app = FastAPI(title="WIA Expert", docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(SessionMiddleware, secret_key=auth.carregar_secret(), session_cookie="wia_sessao",
                   max_age=12 * 3600, same_site="strict", https_only=config.COOKIE_SECURE)


@app.middleware("http")
async def cabecalhos_seguranca(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    return resp


app.mount("/static", StaticFiles(directory=STATIC), name="static")

Usuario = Depends(auth.usuario_atual)


def salvar_upload(up: UploadFile) -> Path:
    """Grava o upload em arquivo temporário respeitando o limite de tamanho."""
    limite = config.MAX_UPLOAD_MB * 1024 * 1024
    tmp = tempfile.NamedTemporaryFile(delete=False, dir=config.DATA_DIR, suffix=Path(up.filename or "").suffix)
    total = 0
    with tmp:
        while bloco := up.file.read(1 << 20):
            total += len(bloco)
            if total > limite:
                tmp.close()
                Path(tmp.name).unlink(missing_ok=True)
                raise HTTPException(413, f"arquivo {up.filename} maior que {config.MAX_UPLOAD_MB} MB")
            tmp.write(bloco)
    return Path(tmp.name)


# --------------------------------------------------------------------------- envio em partes
# Arquivos grandes (ex.: .zip de 300 MB do GreenDocs) sobem em pedaços: cada requisição fica
# pequena para o proxy (Traefik) e, se a conexão cair, o navegador continua do ponto onde parou.

ENVIOS_DIR = config.DATA_DIR / "envios"
PARTE_MAX = 16 * 1024 * 1024
ENVIO_VALIDADE_S = 24 * 3600


class NovoEnvio(BaseModel):
    nome: str
    tamanho: int


def _envio(envio_id: str, u: dict) -> tuple[Path, dict]:
    if not re.fullmatch(r"[0-9a-f]{32}", envio_id):
        raise HTTPException(404)
    meta_p = ENVIOS_DIR / f"{envio_id}.json"
    if not meta_p.is_file():
        raise HTTPException(404, "envio não encontrado ou expirado")
    meta = json.loads(meta_p.read_text(encoding="utf-8"))
    if meta["usuario"] != u["username"]:
        raise HTTPException(404)
    return ENVIOS_DIR / f"{envio_id}.part", meta


def _limpar_envios_velhos() -> None:
    agora = time.time()
    for p in ENVIOS_DIR.glob("*"):
        try:
            if agora - p.stat().st_mtime > ENVIO_VALIDADE_S:
                p.unlink()
        except OSError:
            pass


@app.post("/api/envios")
def criar_envio(dados: NovoEnvio, u: dict = Usuario):
    limite = config.MAX_UPLOAD_MB * 1024 * 1024
    if dados.tamanho <= 0:
        raise HTTPException(400, "arquivo vazio")
    if dados.tamanho > limite:
        raise HTTPException(413, f"arquivo {dados.nome} maior que {config.MAX_UPLOAD_MB} MB "
                                 "(ajuste WIA_MAX_UPLOAD_MB no Ambiente)")
    ENVIOS_DIR.mkdir(parents=True, exist_ok=True)
    _limpar_envios_velhos()
    livre = shutil.disk_usage(ENVIOS_DIR).free
    if livre < dados.tamanho * 3 + 512 * 1024 * 1024:  # parte + cópia na base + extração de zip
        raise HTTPException(507, f"pouco espaço em disco no servidor ({livre // 1024 ** 2} MB livres)")
    envio_id = uuid.uuid4().hex
    (ENVIOS_DIR / f"{envio_id}.json").write_text(json.dumps(
        {"usuario": u["username"], "nome": Path(dados.nome).name or "arquivo", "tamanho": dados.tamanho}),
        encoding="utf-8")
    (ENVIOS_DIR / f"{envio_id}.part").touch()
    return {"id": envio_id, "parte_max": PARTE_MAX}


@app.get("/api/envios/{envio_id}")
def ver_envio(envio_id: str, u: dict = Usuario):
    part, meta = _envio(envio_id, u)
    return {"recebido": part.stat().st_size, "tamanho": meta["tamanho"]}


@app.put("/api/envios/{envio_id}")
async def enviar_parte(envio_id: str, request: Request, offset: int, u: dict = Usuario):
    part, meta = _envio(envio_id, u)
    atual = part.stat().st_size
    if offset != atual:  # parte repetida ou fora de ordem: o cliente retoma de "recebido"
        return JSONResponse({"recebido": atual, "tamanho": meta["tamanho"]}, status_code=409)
    recebido = 0
    with part.open("ab") as f:
        async for bloco in request.stream():
            recebido += len(bloco)
            if recebido > PARTE_MAX or atual + recebido > meta["tamanho"]:
                f.truncate(atual)
                raise HTTPException(413, "parte maior que o permitido")
            f.write(bloco)
    return {"recebido": atual + recebido, "tamanho": meta["tamanho"]}


@app.post("/api/envios/{envio_id}/concluir")
def concluir_envio(envio_id: str, background: BackgroundTasks, tipo: str = Form("auto"),
                   plataforma: str = Form(""), area: str = Form(""), equipamento: str = Form(""),
                   observacao: str = Form(""), u: dict = Usuario):
    part, meta = _envio(envio_id, u)
    if part.stat().st_size != meta["tamanho"]:
        raise HTTPException(409, f"envio incompleto: {part.stat().st_size} de {meta['tamanho']} bytes")
    doc_id = ingest.registrar_arquivo(part, meta["nome"], tipo=tipo, enviado_por=u["username"],
                                      plataforma=plataforma or None, area=area or None,
                                      equipamento=equipamento or None, observacao=observacao or None,
                                      mover=True)
    (ENVIOS_DIR / f"{envio_id}.json").unlink(missing_ok=True)
    background.add_task(ingest.processar_documento, doc_id)
    return {"ids": [doc_id]}


# --------------------------------------------------------------------------- páginas e sessão

@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/saude")
def saude():
    return {"ok": True}


class Login(BaseModel):
    usuario: str
    senha: str


@app.post("/api/login")
def login(dados: Login, request: Request):
    ip = request.client.host if request.client else "?"
    if auth.bloqueado(ip):
        raise HTTPException(429, "muitas tentativas; aguarde 10 minutos")
    u = auth.autenticar(dados.usuario, dados.senha)
    if not u:
        auth.registrar_falha(ip)
        raise HTTPException(401, "usuário ou senha inválidos")
    request.session.clear()
    request.session["usuario"] = u
    return u


@app.post("/api/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@app.get("/api/me")
def me(u: dict = Usuario):
    return u | {"modelo": config.MODEL}


# --------------------------------------------------------------------------- conversas

def _conversa_do_usuario(conn, conversa_id: int, u: dict):
    row = conn.execute("SELECT * FROM conversas WHERE id = ?", (conversa_id,)).fetchone()
    if not row or row["usuario"] != u["username"]:
        raise HTTPException(404, "conversa não encontrada")
    return row


@app.get("/api/conversas")
def listar_conversas(u: dict = Usuario):
    with db.session() as conn:
        rows = conn.execute("SELECT id, titulo, criada_em, atualizada_em FROM conversas WHERE usuario = ?"
                            " ORDER BY atualizada_em DESC LIMIT 200", (u["username"],)).fetchall()
    return [dict(r) for r in rows]


@app.post("/api/conversas")
def nova_conversa(u: dict = Usuario):
    with db.session() as conn:
        cur = conn.execute("INSERT INTO conversas (usuario, titulo, criada_em, atualizada_em) VALUES (?,?,?,?)",
                           (u["username"], "Nova conversa", db.agora(), db.agora()))
        return {"id": cur.lastrowid, "titulo": "Nova conversa"}


@app.get("/api/conversas/{conversa_id}")
def ver_conversa(conversa_id: int, u: dict = Usuario):
    with db.session() as conn:
        c = _conversa_do_usuario(conn, conversa_id, u)
        msgs = conn.execute("SELECT papel, texto, ferramentas_json, anexos_json, criada_em FROM mensagens_tela"
                            " WHERE conversa_id = ? ORDER BY id", (conversa_id,)).fetchall()
    return {"id": c["id"], "titulo": c["titulo"], "mensagens": [
        {"papel": m["papel"], "texto": m["texto"], "criada_em": m["criada_em"],
         "ferramentas": json.loads(m["ferramentas_json"] or "[]"), "anexos": json.loads(m["anexos_json"] or "[]")}
        for m in msgs]}


@app.delete("/api/conversas/{conversa_id}")
def apagar_conversa(conversa_id: int, u: dict = Usuario):
    with db.session() as conn:
        _conversa_do_usuario(conn, conversa_id, u)
        conn.execute("DELETE FROM conversas WHERE id = ?", (conversa_id,))
    return {"ok": True}


@app.post("/api/conversas/{conversa_id}/mensagens")
def enviar_mensagem(conversa_id: int, background: BackgroundTasks, texto: str = Form(""),
                    fotos: list[UploadFile] = File(default=[]), u: dict = Usuario):
    texto = texto.strip()
    fotos = [f for f in fotos if f.filename]
    if not texto and not fotos:
        raise HTTPException(400, "mensagem vazia")
    with db.session() as conn:
        c = _conversa_do_usuario(conn, conversa_id, u)
    caminhos, anexos = [], []
    for f in fotos[:6]:
        if Path(f.filename).suffix.lower() not in ingest.EXT_IMAGEM:
            raise HTTPException(400, f"{f.filename}: no chat só fotos (jpg, png, webp). Outros arquivos vão na aba Base.")
        tmp = salvar_upload(f)
        doc_id = ingest.registrar_arquivo(tmp, f.filename, tipo="foto", enviado_por=u["username"],
                                          observacao=texto[:300] or None, mover=True)
        with db.session() as conn:
            caminhos.append(Path(conn.execute("SELECT caminho FROM documentos WHERE id=?", (doc_id,)).fetchone()[0]))
        anexos.append({"doc_id": doc_id, "nome": f.filename})
        background.add_task(ingest.processar_documento, doc_id)
    with db.session() as conn:
        conn.execute("INSERT INTO mensagens_tela (conversa_id, papel, texto, anexos_json, criada_em) VALUES (?,?,?,?,?)",
                     (conversa_id, "user", texto, json.dumps(anexos, ensure_ascii=False), db.agora()))
        if c["titulo"] == "Nova conversa":
            titulo = (texto or "Foto enviada").splitlines()[0][:70]
            conn.execute("UPDATE conversas SET titulo = ? WHERE id = ?", (titulo, conversa_id))
    resposta = agent.responder(conversa_id, u["username"], texto or "(veja as fotos anexadas)", caminhos)
    return resposta


# --------------------------------------------------------------------------- base de conhecimento

@app.get("/api/documentos")
def listar_documentos(tipo: str | None = None, filtro: str | None = None, u: dict = Usuario):
    sql = ("SELECT id, nome, tipo, plataforma, area, equipamento, enviado_por, enviado_em, status, erro,"
           " observacao, tamanho, n_trechos, pai_id, substr(sha256,1,12) sha FROM documentos"
           " WHERE tipo NOT IN ('nota','ocorrencia')")
    p: list = []
    if tipo:
        sql += " AND tipo = ?"
        p.append(tipo)
    if filtro:
        f = f"%{filtro}%"
        sql += " AND (nome LIKE ? OR equipamento LIKE ? OR area LIKE ? OR plataforma LIKE ?)"
        p += [f] * 4
    with db.session() as conn:
        return [dict(r) for r in conn.execute(sql + " ORDER BY id DESC LIMIT 1000", p)]


@app.post("/api/documentos")
def enviar_documentos(background: BackgroundTasks, arquivos: list[UploadFile] = File(...),
                      tipo: str = Form("auto"), plataforma: str = Form(""), area: str = Form(""),
                      equipamento: str = Form(""), observacao: str = Form(""), u: dict = Usuario):
    ids = []
    for up in arquivos:
        if not up.filename:
            continue
        tmp = salvar_upload(up)
        doc_id = ingest.registrar_arquivo(tmp, up.filename, tipo=tipo, enviado_por=u["username"],
                                          plataforma=plataforma or None, area=area or None,
                                          equipamento=equipamento or None, observacao=observacao or None,
                                          mover=True)
        background.add_task(ingest.processar_documento, doc_id)
        ids.append(doc_id)
    return {"ids": ids}


@app.get("/api/documentos/{doc_id}")
def ver_documento(doc_id: int, u: dict = Usuario):
    with db.session() as conn:
        d = conn.execute("SELECT * FROM documentos WHERE id = ?", (doc_id,)).fetchone()
        if not d:
            raise HTTPException(404)
        trechos = conn.execute("SELECT ordem, local, ts, texto FROM trechos WHERE doc_id = ? ORDER BY ordem LIMIT 30",
                               (doc_id,)).fetchall()
    d = dict(d)
    d.pop("caminho", None)
    return d | {"trechos": [dict(t) for t in trechos]}


@app.get("/api/documentos/{doc_id}/arquivo")
def baixar_documento(doc_id: int, u: dict = Usuario):
    with db.session() as conn:
        d = conn.execute("SELECT nome, caminho FROM documentos WHERE id = ?", (doc_id,)).fetchone()
    if not d or not d["caminho"] or not Path(d["caminho"]).is_file():
        raise HTTPException(404)
    return FileResponse(d["caminho"], filename=d["nome"])


@app.post("/api/documentos/{doc_id}/reprocessar")
def reprocessar(doc_id: int, background: BackgroundTasks, tipo: str | None = Form(None), u: dict = Usuario):
    with db.session() as conn:
        if not conn.execute("SELECT 1 FROM documentos WHERE id = ?", (doc_id,)).fetchone():
            raise HTTPException(404)
        conn.execute("UPDATE documentos SET status='processando', erro=NULL"
                     + (", tipo=?" if tipo in ingest.TIPOS else "") + " WHERE id = ?",
                     ([tipo, doc_id] if tipo in ingest.TIPOS else [doc_id]))
    background.add_task(ingest.processar_documento, doc_id)
    return {"ok": True}


@app.get("/api/duplicados")
def ver_duplicados(u: dict = Usuario):
    with db.session() as conn:
        return ingest.resumo_duplicados(conn)


@app.post("/api/duplicados/apagar")
def apagar_duplicados(u: dict = Usuario):
    return ingest.apagar_duplicados()


@app.delete("/api/documentos/{doc_id}")
def apagar_documento(doc_id: int, u: dict = Usuario):
    with db.session() as conn:
        rows = conn.execute("SELECT caminho FROM documentos WHERE id = ? OR pai_id = ?", (doc_id, doc_id)).fetchall()
        conn.execute("DELETE FROM documentos WHERE id = ?", (doc_id,))
    for r in rows:
        if r["caminho"]:
            Path(r["caminho"]).unlink(missing_ok=True)
    return {"ok": True}


# --------------------------------------------------------------------------- registros

class Ocorrencia(BaseModel):
    data_hora: str
    equipamento: str
    sintoma: str
    tag: str | None = None
    area: str | None = None
    plataforma: str | None = None
    causa_raiz: str | None = None
    categoria_causa: str | None = None
    acao: str | None = None
    blocos_parametros: str | None = None
    tempo_parada_min: float | None = None
    licao: str | None = None
    fontes: str | None = None


@app.get("/api/ocorrencias")
def listar_ocorrencias(u: dict = Usuario):
    with db.session() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM ocorrencias ORDER BY data_hora DESC LIMIT 1000")]


@app.post("/api/ocorrencias")
def criar_ocorrencia(o: Ocorrencia, u: dict = Usuario):
    if not ingest.parse_dt(o.data_hora):
        raise HTTPException(400, "data_hora inválida (use AAAA-MM-DDTHH:MM)")
    with db.session() as conn:
        return {"id": ingest.criar_ocorrencia(conn, o.model_dump(), autor=u["username"], origem="manual")}


@app.delete("/api/ocorrencias/{oc_id}")
def apagar_ocorrencia(oc_id: int, u: dict = Usuario):
    with db.session() as conn:
        r = conn.execute("SELECT doc_id FROM ocorrencias WHERE id = ?", (oc_id,)).fetchone()
        conn.execute("DELETE FROM ocorrencias WHERE id = ?", (oc_id,))
        if r and r["doc_id"]:
            conn.execute("DELETE FROM documentos WHERE id = ?", (r["doc_id"],))
    return {"ok": True}


@app.get("/api/notas")
def listar_notas(u: dict = Usuario):
    with db.session() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM notas ORDER BY id DESC LIMIT 500")]


class Nota(BaseModel):
    alvo: str
    texto: str
    fonte: str | None = None


@app.post("/api/notas")
def criar_nota(n: Nota, u: dict = Usuario):
    with db.session() as conn:
        return {"id": ingest.criar_nota(conn, n.alvo, n.texto, n.fonte, u["username"], "manual")}


@app.get("/api/glossario")
def listar_glossario(u: dict = Usuario):
    with db.session() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM glossario ORDER BY termo")]


class Termo(BaseModel):
    termo: str
    significado: str


@app.post("/api/glossario")
def salvar_termo(t: Termo, u: dict = Usuario):
    with db.session() as conn:
        conn.execute("INSERT INTO glossario (termo, significado, autor, atualizado_em) VALUES (?,?,?,?)"
                     " ON CONFLICT(termo) DO UPDATE SET significado=excluded.significado, autor=excluded.autor,"
                     " atualizado_em=excluded.atualizado_em", (t.termo.strip(), t.significado, u["username"], db.agora()))
    return {"ok": True}


# --------------------------------------------------------------------------- painel

@app.get("/api/painel")
def ver_painel(dias: int | None = 90, u: dict = Usuario):
    if dias is not None and dias <= 0:
        dias = None
    with db.session() as conn:
        return painel.montar(conn, dias)


@app.exception_handler(ValueError)
def erro_valor(request: Request, exc: ValueError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})
