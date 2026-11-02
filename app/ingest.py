"""Ingestão de arquivos na base: identificação de tipo, extração de texto, trechos e importações CSV."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import re
import shutil
import unicodedata
import zipfile
from datetime import datetime
from pathlib import Path

from . import config, db, leitores

log = logging.getLogger("wia.ingest")

TIPOS = ["backup", "log", "foto", "audio", "video", "whatsapp", "documentacao",
         "alarmes", "ocorrencias", "nota", "ocorrencia", "outro"]

EXT_TEXTO = {".txt", ".md", ".log", ".csv", ".tsv", ".json", ".xml", ".scl", ".awl", ".stl",
             ".st", ".udt", ".db", ".l5x", ".l5k", ".xef", ".xdb", ".ini", ".cfg", ".html",
             ".htm", ".sdf", ".asc", ".prn", ".lst", ".src", ".exp", ".iec", ".pou"}
EXT_IMAGEM = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
EXT_AUDIO = {".ogg", ".opus", ".mp3", ".m4a", ".wav", ".aac", ".amr"}
EXT_VIDEO = {".mp4", ".mov", ".3gp", ".avi", ".mkv", ".webm"}
EXT_PDF = {".pdf"}
EXT_PLANILHA = {".xlsx", ".xlsm", ".xls"}
EXT_VISIO = {".vsdx", ".vsdm"}
EXT_CAD = {".dwg", ".dxf"}
# Arquivos de projeto binários: guardados, mas não dá para ler a lógica sem exportar.
EXT_PROJETO = {".ap13", ".ap14", ".ap15", ".ap16", ".ap17", ".ap18", ".ap19", ".ap20",
               ".zap13", ".zap14", ".zap15", ".zap16", ".zap17", ".zap18", ".zap19", ".zap20",
               ".s7p", ".stu", ".sta", ".zef", ".acd", ".apa", ".project", ".projectarchive"}

TRECHO_MAX = 1800
TRECHO_SOBREPOSICAO = 2


# --------------------------------------------------------------------------- utilidades

def sem_acento(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def norm_header(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", sem_acento(s or "").lower()).strip("_")


def sha256_arquivo(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def ler_texto(path: Path) -> str:
    data = path.read_bytes()
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        return data.decode("utf-16", errors="replace")
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


_DT_PADROES = [
    (re.compile(r"(\d{4})-(\d{2})-(\d{2})[T ](\d{1,2}):(\d{2})(?::(\d{2}))?"), "ymd"),
    (re.compile(r"(\d{1,2})[/.](\d{1,2})[/.](\d{2,4})[, ]+(\d{1,2}):(\d{2})(?::(\d{2}))?"), "dmy"),
]


def parse_dt(s: str | None) -> str | None:
    """Converte data/hora em ISO 'YYYY-MM-DDTHH:MM:SS' (horário local da fonte, sem fuso).

    Datas com barra são lidas como dia/mês/ano (padrão brasileiro).
    """
    if not s:
        return None
    s = str(s).strip()
    if re.fullmatch(r"\d{10}(\.\d+)?", s):  # epoch em segundos
        return datetime.fromtimestamp(float(s)).replace(microsecond=0).isoformat()
    if re.fullmatch(r"\d{13}", s):  # epoch em ms
        return datetime.fromtimestamp(int(s) / 1000).replace(microsecond=0).isoformat()
    for rx, ordem in _DT_PADROES:
        m = rx.search(s)
        if not m:
            continue
        g = m.groups()
        try:
            if ordem == "ymd":
                y, mo, d = int(g[0]), int(g[1]), int(g[2])
            else:
                d, mo, y = int(g[0]), int(g[1]), int(g[2])
                if y < 100:
                    y += 2000
            dt = datetime(y, mo, d, int(g[3]), int(g[4]), int(g[5] or 0))
            return dt.isoformat()
        except ValueError:
            continue
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})|(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if m:
        try:
            if m.group(1):
                return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
            return datetime(int(m.group(6)), int(m.group(5)), int(m.group(4))).isoformat()
        except ValueError:
            return None
    return None


def inferir_tipo(nome: str) -> str:
    ext = Path(nome).suffix.lower()
    n = nome.lower()
    if ext in EXT_IMAGEM:
        return "foto"
    if ext in EXT_AUDIO:
        return "audio"
    if ext in EXT_VIDEO:
        return "video"
    if ext in EXT_PROJETO:
        return "backup"
    if ext in EXT_PDF | EXT_PLANILHA | EXT_VISIO | EXT_CAD:
        return "documentacao"
    if ext in {".scl", ".awl", ".stl", ".st", ".udt", ".db", ".l5x", ".l5k", ".xef", ".pou", ".exp"}:
        return "backup"
    if "whatsapp" in n or n.endswith("_chat.txt") or "conversa do whatsapp" in n:
        return "whatsapp"
    if ext == ".log" or "log" in Path(n).stem or "alarm" in n or "evento" in n or "event" in n:
        return "log"
    return "outro"


# --------------------------------------------------------------------------- trechos

def trechos_de_linhas(texto: str, rotulo: str = "linhas") -> list[tuple[str, str, str | None]]:
    """Divide texto em trechos de ~TRECHO_MAX caracteres, guardando o intervalo de linhas."""
    linhas = texto.splitlines()
    out: list[tuple[str, str, str | None]] = []
    i = 0
    while i < len(linhas):
        tam, j = 0, i
        while j < len(linhas) and (tam + len(linhas[j]) + 1 <= TRECHO_MAX or j == i):
            tam += len(linhas[j]) + 1
            j += 1
        bloco = "\n".join(linhas[i:j]).strip()
        if bloco:
            out.append((bloco, f"{rotulo} {i + 1}-{j}", parse_dt(bloco[:400])))
        if j >= len(linhas):
            break
        i = max(j - TRECHO_SOBREPOSICAO, i + 1)
    return out


_WA_ANDROID = re.compile(r"^(\d{1,2}/\d{1,2}/\d{2,4}),? (\d{1,2}:\d{2})(?::\d{2})? - ([^:]{1,60}?): (.*)$")
_WA_IOS = re.compile(r"^‎?\[(\d{1,2}/\d{1,2}/\d{2,4}),? (\d{1,2}:\d{2}(?::\d{2})?)\] ([^:]{1,60}?): (.*)$")
_WA_SISTEMA = re.compile(r"^(\d{1,2}/\d{1,2}/\d{2,4}),? (\d{1,2}:\d{2})(?::\d{2})? - (.*)$")


def parse_whatsapp(texto: str) -> list[dict]:
    """Lê export .txt do WhatsApp (Android ou iOS) em mensagens {ts, autor, texto}."""
    msgs: list[dict] = []
    for linha in texto.splitlines():
        linha = linha.replace(" ", " ").replace("‎", "")
        m = _WA_ANDROID.match(linha) or _WA_IOS.match(linha)
        if m:
            msgs.append({"ts": parse_dt(f"{m.group(1)} {m.group(2)}"), "autor": m.group(3).strip(),
                         "texto": m.group(4)})
            continue
        s = _WA_SISTEMA.match(linha)
        if s:
            msgs.append({"ts": parse_dt(f"{s.group(1)} {s.group(2)}"), "autor": None, "texto": s.group(3)})
        elif msgs:
            msgs[-1]["texto"] += "\n" + linha
    return msgs


def parse_waha_json(dados) -> list[dict]:
    """Lê mensagens exportadas da API WAHA (lista de objetos de mensagem)."""
    if isinstance(dados, dict):
        dados = dados.get("messages") or dados.get("data") or []
    msgs = []
    for m in dados if isinstance(dados, list) else []:
        if not isinstance(m, dict):
            continue
        corpo = m.get("body") or m.get("text") or m.get("caption") or ""
        if m.get("hasMedia") and not corpo:
            corpo = "[mídia]"
        extra = m.get("_data") if isinstance(m.get("_data"), dict) else {}
        autor = m.get("notifyName") or extra.get("notifyName") or m.get("author") or m.get("from")
        if m.get("fromMe"):
            autor = "eu"
        ts = m.get("timestamp")
        msgs.append({"ts": parse_dt(str(ts)) if ts is not None else None, "autor": autor, "texto": corpo})
    return msgs


def trechos_de_mensagens(msgs: list[dict]) -> list[tuple[str, str, str | None]]:
    out = []
    bloco: list[str] = []
    ini = fim = None
    tam = 0

    def fechar():
        if bloco:
            out.append(("\n".join(bloco), f"mensagens {ini or '?'} a {fim or '?'}", ini))

    for m in msgs:
        ts = m.get("ts")
        linha = f"[{(ts or '?').replace('T', ' ')}] {m.get('autor') or 'sistema'}: {m.get('texto', '')}"
        if bloco and tam + len(linha) > TRECHO_MAX:
            fechar()
            bloco, tam, ini = [], 0, None
        if ini is None:
            ini = ts
        fim = ts
        bloco.append(linha)
        tam += len(linha) + 1
    fechar()
    return out


def trechos_pdf(path: Path) -> list[tuple[str, str, str | None]]:
    from pypdf import PdfReader, apply_configuration

    # Diagramas exportados do Visio (arquiteturas de rede) passam do limite padrão de 2 milhões de
    # tokens por página; 10x ainda protege contra PDF malicioso.
    with apply_configuration(stream_parsing_work_maximum_count=20_000_000):
        reader = PdfReader(str(path))
        if reader.is_encrypted:
            reader.decrypt("")  # PDFs só com restrição de impressão/cópia abrem com senha vazia
        out = []
        falhas = []
        for n, page in enumerate(reader.pages, start=1):
            try:
                txt = (page.extract_text() or "").strip()
            except Exception as e:  # uma página ruim não derruba o documento inteiro
                log.warning("PDF %s: falha ao ler a página %s: %s", path.name, n, e)
                falhas.append(n)
                continue
            if not txt:
                continue
            for k, (t, _, _) in enumerate(trechos_de_linhas(txt)):
                out.append((t, f"página {n}" + (f" (parte {k + 1})" if k else ""), None))
    if falhas and not out:
        raise ValueError(f"não foi possível ler nenhuma página (falhas nas páginas {falhas[:10]})")
    return out


# --------------------------------------------------------------------------- CSV estruturado

SINONIMOS_ALARME = {
    "ts": ["timestamp", "data_hora", "datahora", "data_e_hora", "date_time", "datetime", "time_stamp",
           "event_time", "horario", "data_hora_evento", "time", "hora_evento"],
    "data": ["data", "date", "dia"],
    "hora": ["hora", "hour", "horario_evento"],
    "tag": ["tag", "ponto", "point", "source", "fonte", "origem", "objeto", "object", "name", "nome",
            "variavel", "loop", "malha", "tag_name", "tagname", "position", "posicao"],
    "mensagem": ["mensagem", "message", "descricao", "description", "texto", "text", "evento", "event",
                 "alarm_text", "alarme"],
    "prioridade": ["prioridade", "priority", "severidade", "severity", "classe", "class", "nivel", "level"],
    "estado": ["estado", "state", "status", "condicao", "condition", "transicao", "transition", "tipo", "type"],
    "area": ["area", "unidade", "unit", "planta", "plant", "grupo", "group", "processo", "process"],
}

SINONIMOS_OCORRENCIA = {
    "data_hora": ["data_hora", "datahora", "data", "date", "inicio", "data_inicio", "timestamp", "abertura"],
    "equipamento": ["equipamento", "equipment", "ativo", "asset", "maquina", "machine"],
    "tag": ["tag"],
    "area": ["area", "setor", "unidade", "planta"],
    "plataforma": ["plataforma", "sistema", "platform", "controlador", "fabricante"],
    "sintoma": ["sintoma", "falha", "problema", "descricao", "defeito", "symptom", "description"],
    "causa_raiz": ["causa_raiz", "causa", "root_cause", "cause"],
    "categoria_causa": ["categoria_causa", "categoria", "tipo_falha", "category", "modo_falha"],
    "acao": ["acao", "acao_tomada", "solucao", "action", "correcao"],
    "tempo_parada_min": ["tempo_parada_min", "tempo_parada", "parada_min", "downtime", "downtime_min",
                         "minutos_parada", "duracao_min", "duracao"],
    "licao": ["licao", "licao_aprendida", "lesson", "observacao"],
}


def ler_csv(texto: str) -> tuple[list[str], list[list[str]]]:
    amostra = texto[:5000]
    try:
        dialeto = csv.Sniffer().sniff(amostra, delimiters=";,\t|")
        delim = dialeto.delimiter
    except csv.Error:
        delim = ";" if amostra.count(";") >= amostra.count(",") else ","
    linhas = [r for r in csv.reader(io.StringIO(texto), delimiter=delim) if any(c.strip() for c in r)]
    if not linhas:
        return [], []
    return linhas[0], linhas[1:]


def mapear_colunas(cabecalho: list[str], sinonimos: dict[str, list[str]]) -> dict[str, int]:
    norm = [norm_header(h) for h in cabecalho]
    mapa: dict[str, int] = {}
    for campo, nomes in sinonimos.items():
        for nome in nomes:  # a ordem dos sinônimos define a prioridade
            if nome in norm and norm.index(nome) not in mapa.values():
                mapa[campo] = norm.index(nome)
                break
    return mapa


def normalizar_estado(v: str | None) -> str | None:
    if not v:
        return None
    s = sem_acento(v).lower().strip()
    if re.search(r"unack|nao reconh", s):
        return "ativo"
    if re.search(r"normal|rtn|return|retorn|clear|went|^out$|^off$|reset|^norm|^fim|^saiu", s):
        return "normal"
    if re.search(r"ack|reconhec", s):
        return "reconhecido"
    if re.search(r"ativ|activ|alarm|come|^in$|^on$|raise|^set$|^entrou|^inicio", s):
        return "ativo"
    return s[:30]


def importar_alarmes(conn, doc_id: int, texto: str) -> int:
    cab, linhas = ler_csv(texto)
    mapa = mapear_colunas(cab, SINONIMOS_ALARME)
    if "tag" not in mapa or not ({"ts"} <= mapa.keys() or {"data", "hora"} <= mapa.keys() or "data" in mapa):
        raise ValueError(
            "CSV de alarmes precisa ter pelo menos colunas de data/hora e de tag. "
            f"Colunas encontradas: {', '.join(cab)}")

    def col(row, campo):
        i = mapa.get(campo)
        return row[i].strip() if i is not None and i < len(row) else None

    n = 0
    for row in linhas:
        ts = parse_dt(col(row, "ts")) if "ts" in mapa else parse_dt(f"{col(row, 'data')} {col(row, 'hora') or ''}")
        tag = col(row, "tag")
        if not ts or not tag:
            continue
        conn.execute(
            "INSERT INTO alarmes (ts, tag, mensagem, prioridade, estado, area, doc_id) VALUES (?,?,?,?,?,?,?)",
            (ts, tag, col(row, "mensagem"), col(row, "prioridade"), normalizar_estado(col(row, "estado")),
             col(row, "area"), doc_id))
        n += 1
    return n


def _num(v: str | None) -> float | None:
    if v is None or not str(v).strip():
        return None
    s = str(v).strip().lower().replace(",", ".")
    m = re.match(r"^(\d+):(\d{2})(?::(\d{2}))?$", s)  # hh:mm[:ss]
    if m:
        return int(m.group(1)) * 60 + int(m.group(2)) + int(m.group(3) or 0) / 60
    m = re.match(r"^([\d.]+)\s*(h|hr|hrs|horas?)$", s)
    if m:
        return float(m.group(1)) * 60
    m = re.match(r"^([\d.]+)", s)
    return float(m.group(1)) if m else None


def importar_ocorrencias_csv(conn, doc_id: int, texto: str, autor: str | None) -> int:
    cab, linhas = ler_csv(texto)
    mapa = mapear_colunas(cab, SINONIMOS_OCORRENCIA)
    faltando = {"data_hora", "equipamento", "sintoma"} - mapa.keys()
    if faltando:
        raise ValueError(f"CSV de ocorrências sem as colunas: {', '.join(sorted(faltando))}. "
                         f"Colunas encontradas: {', '.join(cab)}")
    n = 0
    for row in linhas:
        def col(campo):
            i = mapa.get(campo)
            return row[i].strip() if i is not None and i < len(row) and row[i].strip() else None
        dh, eq, sint = parse_dt(col("data_hora")), col("equipamento"), col("sintoma")
        if not (dh and eq and sint):
            continue
        criar_ocorrencia(conn, {
            "data_hora": dh, "equipamento": eq, "tag": col("tag"), "area": col("area"),
            "plataforma": col("plataforma"), "sintoma": sint, "causa_raiz": col("causa_raiz"),
            "categoria_causa": col("categoria_causa"), "acao": col("acao"),
            "tempo_parada_min": _num(col("tempo_parada_min")), "licao": col("licao"),
            "fontes": f"importado de documento #{doc_id}",
        }, autor=autor, origem="importacao", indexar=False)
        n += 1
    return n


# --------------------------------------------------------------------------- registros do assistente

def _doc_texto(conn, nome: str, tipo: str, texto: str, autor: str | None, **meta) -> int:
    cur = conn.execute(
        "INSERT INTO documentos (nome, tipo, plataforma, area, equipamento, enviado_por, enviado_em,"
        " status, n_trechos) VALUES (?,?,?,?,?,?,?, 'ok', 0)",
        (nome, tipo, meta.get("plataforma"), meta.get("area"), meta.get("equipamento"), autor, db.agora()))
    doc_id = cur.lastrowid
    gravar_trechos(conn, doc_id, [(texto, tipo, meta.get("ts"))])
    return doc_id


def criar_nota(conn, alvo: str, texto: str, fonte: str | None, autor: str | None, origem: str) -> int:
    doc_id = _doc_texto(conn, f"Nota: {alvo}", "nota", f"NOTA sobre {alvo}\n{texto}\nFonte: {fonte or '-'}",
                        autor, equipamento=alvo)
    cur = conn.execute(
        "INSERT INTO notas (alvo, texto, fonte, autor, origem, doc_id, criada_em) VALUES (?,?,?,?,?,?,?)",
        (alvo, texto, fonte, autor, origem, doc_id, db.agora()))
    return cur.lastrowid


CAMPOS_OCORRENCIA = ["data_hora", "equipamento", "tag", "area", "plataforma", "sintoma", "causa_raiz",
                     "categoria_causa", "acao", "blocos_parametros", "tempo_parada_min", "licao", "fontes"]


def criar_ocorrencia(conn, dados: dict, autor: str | None, origem: str, indexar: bool = True) -> int:
    d = {k: dados.get(k) for k in CAMPOS_OCORRENCIA}
    d["data_hora"] = parse_dt(d["data_hora"]) or d["data_hora"]
    if d.get("tempo_parada_min") is not None:
        d["tempo_parada_min"] = _num(str(d["tempo_parada_min"]))
    doc_id = None
    if indexar:
        resumo = "\n".join(f"{k}: {v}" for k, v in d.items() if v not in (None, ""))
        doc_id = _doc_texto(conn, f"Ocorrência: {d['equipamento']} {d['data_hora']}", "ocorrencia",
                            "RESUMO DE OCORRÊNCIA\n" + resumo, autor, equipamento=d["equipamento"],
                            area=d.get("area"), plataforma=d.get("plataforma"), ts=d["data_hora"])
    cols = CAMPOS_OCORRENCIA + ["autor", "origem", "doc_id", "criada_em"]
    vals = [d[k] for k in CAMPOS_OCORRENCIA] + [autor, origem, doc_id, db.agora()]
    cur = conn.execute(f"INSERT INTO ocorrencias ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", vals)
    return cur.lastrowid


# --------------------------------------------------------------------------- documentos

def gravar_trechos(conn, doc_id: int, trechos: list[tuple[str, str, str | None]]) -> int:
    conn.execute("DELETE FROM trechos WHERE doc_id = ?", (doc_id,))
    conn.executemany("INSERT INTO trechos (doc_id, ordem, texto, local, ts) VALUES (?,?,?,?,?)",
                     [(doc_id, i, t, loc, ts) for i, (t, loc, ts) in enumerate(trechos)])
    conn.execute("UPDATE documentos SET n_trechos = ? WHERE id = ?", (len(trechos), doc_id))
    return len(trechos)


def registrar_arquivo(origem: Path, nome: str, *, tipo: str = "auto", enviado_por: str | None = None,
                      plataforma: str | None = None, area: str | None = None,
                      equipamento: str | None = None, observacao: str | None = None,
                      pai_id: int | None = None, mover: bool = False) -> int:
    """Copia o arquivo para a pasta da base e cria o registro (status 'processando')."""
    nome = Path(nome).name or "arquivo"
    tipo = tipo if tipo in TIPOS else inferir_tipo(nome)
    with db.session() as conn:
        cur = conn.execute(
            "INSERT INTO documentos (nome, tipo, plataforma, area, equipamento, enviado_por, enviado_em,"
            " observacao, pai_id, status) VALUES (?,?,?,?,?,?,?,?,?, 'processando')",
            (nome, tipo, plataforma, area, equipamento, enviado_por, db.agora(), observacao, pai_id))
        doc_id = cur.lastrowid
        destino = config.FILES_DIR / f"{doc_id:06d}_{re.sub(r'[^A-Za-z0-9._-]+', '_', nome)}"
        destino.parent.mkdir(parents=True, exist_ok=True)
        (shutil.move if mover else shutil.copyfile)(str(origem), str(destino))
        conn.execute("UPDATE documentos SET caminho = ?, sha256 = ?, tamanho = ? WHERE id = ?",
                     (str(destino), sha256_arquivo(destino), destino.stat().st_size, doc_id))
    return doc_id


def _extrair_zip(conn_doc: dict, path: Path) -> list[int]:
    """Extrai um .zip (ex.: export do WhatsApp com mídias) e registra cada arquivo como filho."""
    filhos = []
    pasta = path.with_suffix("")
    pasta.mkdir(exist_ok=True)
    with zipfile.ZipFile(path) as z:
        tem_chat = any(Path(i.filename).name.lower() in {"_chat.txt"} or "whatsapp" in i.filename.lower()
                       for i in z.infolist())
        for info in z.infolist():
            if info.is_dir() or info.file_size == 0:
                continue
            nome = Path(info.filename).name  # descarta diretórios: evita zip slip
            if not nome or nome.startswith("."):
                continue
            alvo = pasta / nome
            with z.open(info) as src, alvo.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            tipo = "whatsapp" if tem_chat and nome.lower().endswith(".txt") else "auto"
            filhos.append(registrar_arquivo(alvo, nome, tipo=tipo, enviado_por=conn_doc["enviado_por"],
                                            plataforma=conn_doc["plataforma"], area=conn_doc["area"],
                                            equipamento=conn_doc["equipamento"], pai_id=conn_doc["id"],
                                            mover=True))
    shutil.rmtree(pasta, ignore_errors=True)
    return filhos


def _transcrever_audio(path: Path) -> str | None:
    try:
        from faster_whisper import WhisperModel  # dependência opcional
    except ImportError:
        return None
    modelo = WhisperModel("small", device="cpu", compute_type="int8")
    segmentos, _ = modelo.transcribe(str(path), language="pt")
    return "\n".join(f"[{s.start:6.1f}s] {s.text.strip()}" for s in segmentos)


def contextualizar_klabin(trechos: list[tuple[str, str, str | None]], info: dict,
                          nome: str) -> list[tuple[str, str, str | None]]:
    """Põe a ficha do documento no início e o rótulo (código · máquina · tipo) em cada trecho."""
    rotulo = leitores.rotulo_documento(info)
    return ([(leitores.ficha_documento(info, nome), "ficha do documento", None)]
            + [(f"{rotulo}\n{t}", loc, ts) for t, loc, ts in trechos])


def _area_da_capa(trechos: list[tuple[str, str, str | None]]) -> str | None:
    """Usa o campo 'Área:' da capa das planilhas Klabin, se houver (ex. '34231 - SDCD, ...')."""
    for t, _, _ in trechos[:3]:
        m = re.search(r"^Área: (3\d{4} - .+)$", t, re.M)
        if m:
            info = leitores.identificar_documento(t)
            maq = info.get("maquina") if info else None
            nome = m.group(1).strip()
            return nome + (f" ({maq})" if maq and maq not in nome else "")
    return None


def _definir_area(conn, doc_id: int, info: dict, da_capa: str | None) -> None:
    """Preenche a área do documento. O nome da área vem da capa das planilhas Klabin; quando um
    documento não tem capa, reaproveita o nome já aprendido de outro documento da mesma área."""
    curta = leitores.area_documento(info)
    if da_capa:
        conn.execute("UPDATE documentos SET area = ? WHERE id = ?", (da_capa, doc_id))
        conn.execute("UPDATE documentos SET area = ? WHERE area = ?", (da_capa, curta))  # corrige os anteriores
        return
    conhecida = conn.execute("SELECT area FROM documentos WHERE area LIKE ? AND id != ? LIMIT 1",
                             (f"{info['area']} - %", doc_id)).fetchone()
    conn.execute("UPDATE documentos SET area = ? WHERE id = ?", (conhecida[0] if conhecida else curta, doc_id))


def processar_documento(doc_id: int) -> None:
    """Extrai texto do arquivo e grava os trechos. Erros ficam registrados no documento."""
    with db.session() as conn:
        doc = dict(conn.execute("SELECT * FROM documentos WHERE id = ?", (doc_id,)).fetchone())
    path = Path(doc["caminho"])
    ext = path.suffix.lower()
    status, erro, observ = "ok", None, None
    filhos: list[int] = []
    try:
        trechos: list[tuple[str, str, str | None]] = []
        with db.session() as conn:
            dup = conn.execute(
                "SELECT id, nome FROM documentos WHERE sha256 = ? AND id != ?"
                " AND status NOT IN ('erro', 'duplicado', 'processando') ORDER BY id LIMIT 1",
                (doc["sha256"], doc_id)).fetchone()

        if dup:
            # Mesmo conteúdo já indexado (ex.: arquivo solto + o mesmo dentro de um .zip): não repete
            # os trechos, senão a busca devolve o mesmo resultado duas vezes.
            status = "duplicado"
            observ = f"conteúdo idêntico ao documento #{dup['id']} ({dup['nome']}); não indexado de novo"
        elif ext == ".zip":
            filhos = _extrair_zip(doc, path)
            trechos = [(f"Arquivo compactado com {len(filhos)} arquivos.", "zip", None)]
        elif doc["tipo"] == "alarmes":
            texto = leitores.planilha_para_csv(path) if ext in EXT_PLANILHA else ler_texto(path)
            with db.session() as conn:
                conn.execute("DELETE FROM alarmes WHERE doc_id = ?", (doc_id,))
                n = importar_alarmes(conn, doc_id, texto)
            observ = f"{n} eventos de alarme importados"
            trechos = trechos_de_linhas(texto)
        elif doc["tipo"] == "ocorrencias":
            texto = leitores.planilha_para_csv(path) if ext in EXT_PLANILHA else ler_texto(path)
            with db.session() as conn:
                n = importar_ocorrencias_csv(conn, doc_id, texto, doc["enviado_por"])
            observ = f"{n} ocorrências importadas"
            trechos = trechos_de_linhas(texto)
        elif ext in EXT_PDF:
            trechos = trechos_pdf(path)
            if not trechos:
                status, observ = "sem_texto", ("PDF sem texto extraível (escaneado ou com o texto convertido "
                                               "em linhas; envie o DWG de mesmo nome, se houver, ou faça OCR)")
            elif sum(len(t[0]) for t in trechos) < 200:
                observ = ("PDF com pouco texto (desenho com textos convertidos em linhas?); "
                          "o DWG de mesmo nome costuma ter as tags")
        elif ext in EXT_PLANILHA:
            trechos = leitores.trechos_planilha(path)
            if not trechos:
                status, observ = "sem_texto", "planilha vazia"
        elif ext in EXT_VISIO:
            trechos = leitores.trechos_visio(path)
            if not trechos:
                status, observ = "sem_texto", "desenho Visio sem textos"
        elif ext == ".dwg" and not leitores.conversor_dwg():
            status = "pendente"
            observ = ("DWG guardado; falta o conversor LibreDWG (dwg2dxf) neste servidor para ler os textos. "
                      "Depois de instalar, use 'reprocessar pendentes'")
        elif ext in EXT_CAD:
            trechos = leitores.trechos_dwg(path) if ext == ".dwg" else leitores.trechos_dxf(path)
            if not trechos:
                status, observ = "sem_texto", "desenho sem textos (só geometria)"
        elif ext in EXT_IMAGEM:
            if config.DESCRIBE_IMAGES:
                from .llm import descrever_imagem
                desc = descrever_imagem(path, doc.get("observacao"))
                trechos = [(f"FOTO {doc['nome']}\n{desc}", "descrição da imagem (gerada por IA)", None)]
            else:
                status, observ = "pendente", "descrição automática de imagens desativada"
        elif ext in EXT_AUDIO or ext in EXT_VIDEO:
            transcricao = _transcrever_audio(path) if ext in EXT_AUDIO else None
            if transcricao:
                trechos = trechos_de_linhas(f"TRANSCRIÇÃO (automática) de {doc['nome']}\n{transcricao}",
                                            "transcrição")
            else:
                status = "pendente"
                observ = ("aguardando transcrição: envie um .txt com a transcrição/descrição, "
                          "ou instale faster-whisper para áudio")
        elif ext in EXT_PROJETO:
            status = "binario"
            observ = ("arquivo de projeto binário guardado; para análise de lógica, exporte em texto "
                      "(SCL/XML/L5X/relatórios do DNA Explorer/FbCAD) e envie também")
            trechos = [(f"Arquivo de projeto {doc['nome']} (binário, não lido)", "arquivo", None)]
        elif ext == ".json" and doc["tipo"] == "whatsapp":
            trechos = trechos_de_mensagens(parse_waha_json(json.loads(ler_texto(path))))
        elif ext in EXT_TEXTO or doc["tipo"] in {"log", "whatsapp", "backup"}:
            texto = ler_texto(path)
            msgs = parse_whatsapp(texto) if (doc["tipo"] == "whatsapp" or ext == ".txt") else []
            if msgs and (doc["tipo"] == "whatsapp" or len(msgs) >= 3):
                trechos = trechos_de_mensagens(msgs)
                if doc["tipo"] != "whatsapp":
                    with db.session() as conn:
                        conn.execute("UPDATE documentos SET tipo='whatsapp' WHERE id=?", (doc_id,))
            else:
                trechos = trechos_de_linhas(texto)
        else:
            status, observ = "nao_suportado", f"extensão {ext or '(nenhuma)'} sem leitor de texto"

        info = leitores.identificar_documento(doc["nome"])
        if info and trechos and status != "duplicado":
            trechos = contextualizar_klabin(trechos, info, doc["nome"])
        with db.session() as conn:
            gravar_trechos(conn, doc_id, trechos)
            if info and not doc.get("area"):
                _definir_area(conn, doc_id, info, _area_da_capa(trechos))
    except Exception as e:  # registra o erro no documento
        log.exception("falha ao processar documento %s", doc_id)
        status, erro = "erro", f"{type(e).__name__}: {e}"[:500]
    with db.session() as conn:
        conn.execute("UPDATE documentos SET status = ?, erro = ?, observacao = COALESCE(?, observacao)"
                     " WHERE id = ?", (status, erro, observ, doc_id))
    for f in filhos:
        processar_documento(f)
