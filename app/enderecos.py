"""Endereçamento de I/O: pontos (tag → remota/gabinete → cartão → canal → borneira/JB) e remotas.

Duas fontes alimentam a tabela `io_pontos`:
- o cadastro do projeto RemotasPuma (data.js com `window.REMOTAS_DATA`), que também traz a capacidade
  instalada de cada remota;
- as listas de I/O em planilha que entram na base (as que já estão e as novas): as colunas de
  endereçamento são reconhecidas pelo nome do cabeçalho e pelo conteúdo.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import unicodedata
from pathlib import Path

from . import db, leitores

log = logging.getLogger(__name__)

FONTE_PUMA = "RemotasPuma"
TIPOS = ["DI", "DO", "AI", "AO", "RTD/TC"]
MIN_PONTOS_LISTA = 5          # menos que isso numa aba não é lista de I/O
EXT_PLANILHA = {".xlsx", ".xlsm", ".xls"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS io_pontos (
    id           INTEGER PRIMARY KEY,
    fonte        TEXT NOT NULL,
    doc_id       INTEGER REFERENCES documentos(id) ON DELETE CASCADE,
    tag          TEXT NOT NULL,
    tag_norm     TEXT NOT NULL,
    prefixo      TEXT,
    descricao    TEXT,
    tipo         TEXT,
    remota       TEXT,
    cartao       TEXT,
    slot         TEXT,
    canal        TEXT,
    borneira     TEXT,
    jb           TEXT,
    jb_borne     TEXT,
    controlador  TEXT,
    area         TEXT,
    local        TEXT,
    extra_json   TEXT
);
CREATE INDEX IF NOT EXISTS ix_io_tag ON io_pontos(tag_norm);
CREATE INDEX IF NOT EXISTS ix_io_remota ON io_pontos(remota);
CREATE INDEX IF NOT EXISTS ix_io_doc ON io_pontos(doc_id);

CREATE TABLE IF NOT EXISTS remotas (
    id               TEXT PRIMARY KEY,
    area             TEXT,
    sistema          TEXT,
    grupo            TEXT,
    rota             TEXT,
    seq              INTEGER,
    capacidade_json  TEXT,
    fonte            TEXT
);

-- Planilhas da base já analisadas (com ou sem pontos), para não reler a cada inicialização.
CREATE TABLE IF NOT EXISTS io_docs (
    doc_id  INTEGER PRIMARY KEY REFERENCES documentos(id) ON DELETE CASCADE,
    pontos  INTEGER NOT NULL,
    em      TEXT NOT NULL
);
"""


def garantir_tabelas(conn) -> None:
    conn.executescript(SCHEMA)


def _sem_acento(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def norm_tag(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", _sem_acento(str(s or "")).upper())


def chave_remota(s: str) -> str:
    return norm_tag(s)


# --------------------------------------------------------------------------- tipo do ponto

_TIPO_DIRETO = {"DI": "DI", "DO": "DO", "AI": "AI", "AO": "AO", "ED": "DI", "SD": "DO", "EA": "AI", "SA": "AO",
                "RTD": "RTD/TC", "TC": "RTD/TC", "RTD/TC": "RTD/TC", "PT100": "RTD/TC", "TI": "RTD/TC"}


def tipo_do_ponto(tipo: str | None, cartao: str | None) -> str | None:
    """DI/DO/AI/AO/RTD-TC a partir da coluna de tipo ou, sem ela, do modelo do cartão (AIF8V → AI)."""
    for v in (tipo, cartao):
        s = re.sub(r"\s+", "", _sem_acento(str(v or "")).upper())
        if not s:
            continue
        if s in _TIPO_DIRETO:
            return _TIPO_DIRETO[s]
        if re.match(r"^(RTD|PT100|TC\b|TERMOP)", s):
            return "RTD/TC"
        m = re.match(r"^(DI|DO|AI|AO|ED|SD|EA|SA)(?![A-Z]{2,}\b)", s)
        if m:
            return _TIPO_DIRETO[m.group(1)]
    return None


# --------------------------------------------------------------------------- leitura de listas de I/O

# Campo → (padrões que pontuam no nome da coluna, padrões que eliminam). Nomes já sem acento e minúsculos,
# no formato "grupo > coluna" (grupos mesclados acima do cabeçalho).
CAMPOS = {
    "tag": ([(r"\btag\b", 5), (r"sensor > position|sensor > posi", 6), (r"position|posicao", 2),
             (r"instrumento|instrument\b", 3), (r"ponto|point", 1)],
            [r"cabo|cable|caixa|junction|box|gabinete|cabinet|painel|panel|acn|cartao|slot|ethernet|port"]),
    "descricao": ([(r"descri", 6), (r"target|objetiv", 4), (r"servico|service|funcao|function", 3),
                   (r"ponto de medicao > target", 2)], [r"tipo|type"]),
    "tipo": ([(r"^tipo$|> tipo$|tipo de sinal|tipo do ponto|tipo i/?o|signal type|i/?o type|^type$", 6)],
             [r"cartao|card|unit|modelo|model|caixa|cabo|cable"]),
    "cartao": ([(r"tipo do cartao|i/o unit|io unit|card type|modulo|module|modelo do cartao", 6)], []),
    "slot": ([(r"slot|posicao do cartao|card pos", 6), (r"cartao|card|rack", 3)],
             [r"tipo do cartao|i/o unit|card type"]),
    "canal": ([(r"channel|canal", 6), (r"^ch$|> ch$|\bch\b", 5)], []),
    "borneira": ([(r"borneira|regua|bornes?\b", 6),
                  (r"(gabinete|cabinet|painel|panel|remota).*(pin|terminais|terminal)", 4)],
                 [r"caixa|junction|box"]),
    "jb": ([(r"caixa de juncao > (box|codigo)|junction box > box|box code|codigo da caixa", 7),
            (r"caixa de juncao|junction box|\bjb\b", 3)],
           [r"tira|strip|terminal|par\b|pair|cor\b|color"]),
    "jb_borne": ([(r"(caixa|junction|box|jb).*(tira|strip|terminal|borne)", 7), (r"terminal strip|tira de terminais", 4)],
                 []),
    "remota": ([(r"(i/o|io|remota|rio).*> (cabinet|gabinete|painel|panel)", 7), (r"remota|\brio\b", 7),
                (r"(painel|panel|gabinete|cabinet|armario)[^>]*$", 3)],
               [r"pin|terminais|terminal|conector|connector|ethernet|caixa|junction|special|vfd|slot|unit|"
                r"group|grupo|channel|canal|ibc|position|posicao"]),
    "controlador": ([(r"acn position|acn posicao|controlador|controller|\bcpu\b", 6), (r"\bacn\b", 2)],
                    [r"gabinete > cabinet|ethernet|i/o"]),
    "io_grupo": ([(r"i/o grupo|i/o group|io group", 6)], []),
    "ibc": ([(r"^ibc$|> ibc$", 6)], []),
    "conector": ([(r"conector|connector|\bcon\.?$", 6)], [r"special"]),
}

RX_TAG_VALOR = re.compile(r"^(?=.*[A-Z])(?=.*\d)[A-Z0-9][A-Z0-9+\-_./]{2,39}$")


def _nome(s: str) -> str:
    return re.sub(r"\s+", " ", _sem_acento(s).lower()).strip()


def _pontuar(nome: str, campo: str) -> int:
    pos, neg = CAMPOS[campo]
    if any(re.search(p, nome) for p in neg):
        return 0
    return max((w for p, w in pos if re.search(p, nome)), default=0)


def mapear_colunas(nomes: list[str], amostra: list[list]) -> dict[str, int]:
    """Escolhe uma coluna para cada campo (a de maior pontuação; cada coluna serve a um campo só)."""
    nn = [_nome(n) for n in nomes]
    cand = []
    for campo in CAMPOS:
        for c, n in enumerate(nn):
            pts = _pontuar(n, campo)
            if campo == "tag" and pts:
                vals = [leitores._celula(r[c]).upper() for r in amostra if c < len(r) and not leitores._vazio(r[c])]
                if vals:
                    pts += int(4 * sum(1 for v in vals if RX_TAG_VALOR.match(v)) / len(vals))
            if pts:
                cand.append((pts, -c, campo, c))
    cand.sort(reverse=True)
    escolha: dict[str, int] = {}
    usadas: set[int] = set()
    for pts, _, campo, c in cand:
        if campo in escolha or c in usadas:
            continue
        escolha[campo] = c
        usadas.add(c)
    return escolha


def pontos_da_aba(aba: leitores.Aba) -> list[dict]:
    h = leitores._linha_cabecalho(aba.linhas)
    if h is None:
        return []
    nomes = leitores._nomes_colunas(aba, h)
    dados = aba.linhas[h + 1:]
    cols = mapear_colunas(nomes, dados[:60])
    if "tag" not in cols or not ({"canal", "slot", "borneira"} & cols.keys()) \
            or not ({"remota", "jb", "slot", "cartao"} & cols.keys()):
        return []

    def val(row, campo):
        c = cols.get(campo)
        if c is None or c >= len(row) or leitores._vazio(row[c]):
            return None
        return leitores._celula(row[c])

    pontos = []
    for i, row in enumerate(dados, start=h + 2):
        tag = val(row, "tag")
        if not tag or not RX_TAG_VALOR.match(tag.upper().replace(" ", "")):
            continue
        p = {campo: val(row, campo) for campo in ("descricao", "remota", "cartao", "slot", "canal", "borneira",
                                                   "jb", "jb_borne", "controlador")}
        if not any(p[k] for k in ("remota", "slot", "canal", "borneira", "jb")):
            continue
        p["tag"] = tag.strip()
        p["tipo"] = tipo_do_ponto(val(row, "tipo"), p["cartao"])
        extra = {k: val(row, k) for k in ("io_grupo", "ibc", "conector") if val(row, k)}
        p["extra"] = extra
        p["local"] = f"aba '{aba.nome.strip()}', linha {i}"
        pontos.append(p)
    return pontos if len(pontos) >= MIN_PONTOS_LISTA else []


def pontos_da_planilha(path: Path) -> list[dict]:
    out: list[dict] = []
    for aba in leitores.ler_abas(path):
        if aba.nome.strip().lower() in leitores.ABAS_IGNORADAS or aba.nome.strip().lower() == "capa":
            continue
        out.extend(pontos_da_aba(aba))
    return out


def _inserir(conn, pontos: list[dict], fonte: str, doc_id: int | None, area: str | None) -> int:
    conn.executemany(
        "INSERT INTO io_pontos (fonte, doc_id, tag, tag_norm, prefixo, descricao, tipo, remota, cartao, slot, canal,"
        " borneira, jb, jb_borne, controlador, area, local, extra_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(fonte, doc_id, p["tag"], norm_tag((p.get("prefixo") or "") + p["tag"]), p.get("prefixo"), p.get("descricao"),
          p.get("tipo"), p.get("remota"), p.get("cartao"), p.get("slot"), p.get("canal"), p.get("borneira"),
          p.get("jb"), p.get("jb_borne"), p.get("controlador"), p.get("area") or area, p.get("local"),
          json.dumps(p.get("extra") or {}, ensure_ascii=False) if p.get("extra") else None) for p in pontos])
    return len(pontos)


def indexar_documento(doc_id: int) -> int:
    """(Re)lê os pontos de I/O de uma planilha da base. Devolve quantos pontos entraram."""
    with db.session() as conn:
        doc = conn.execute("SELECT id, nome, caminho, area, status FROM documentos WHERE id = ?", (doc_id,)).fetchone()
    if not doc or not doc["caminho"] or Path(doc["caminho"]).suffix.lower() not in EXT_PLANILHA:
        return 0
    pontos: list[dict] = []
    if doc["status"] not in ("duplicado", "erro") and Path(doc["caminho"]).exists():
        try:
            pontos = pontos_da_planilha(Path(doc["caminho"]))
        except Exception:
            log.exception("falha ao ler pontos de I/O do documento %s", doc_id)
    info = leitores.identificar_documento(doc["nome"])
    # Máquinas usam o mesmo nome de área do cadastro de remotas, para agrupar no painel.
    if info and info.get("maquina"):
        area = f"Máquina de Papel {info['maquina']}"
    else:
        area = doc["area"] or (leitores.area_documento(info) if info else None)
    with db.session() as conn:
        conn.execute("DELETE FROM io_pontos WHERE doc_id = ?", (doc_id,))
        n = _inserir(conn, pontos, Path(doc["nome"]).stem, doc_id, area)
        conn.execute("INSERT INTO io_docs (doc_id, pontos, em) VALUES (?,?,?) ON CONFLICT(doc_id) DO UPDATE"
                     " SET pontos=excluded.pontos, em=excluded.em", (doc_id, n, db.agora()))
    if n:
        log.info("documento %s (%s): %d pontos de I/O", doc_id, doc["nome"], n)
    return n


def indexar_pendentes(todos: bool = False) -> dict:
    """Lê as planilhas da base que ainda não passaram pelo leitor de I/O (ou todas, com todos=True)."""
    with db.session() as conn:
        sql = ("SELECT d.id, d.caminho FROM documentos d LEFT JOIN io_docs i ON i.doc_id = d.id"
               " WHERE d.status = 'ok'" + ("" if todos else " AND i.doc_id IS NULL") + " ORDER BY d.id")
        ids = [r["id"] for r in conn.execute(sql) if r["caminho"] and Path(r["caminho"]).suffix.lower() in EXT_PLANILHA]
    docs = pontos = 0
    for i in ids:
        n = indexar_documento(i)
        docs += 1 if n else 0
        pontos += n
    return {"planilhas": len(ids), "com_pontos": docs, "pontos": pontos}


_THREAD: threading.Thread | None = None


def indexar_em_segundo_plano(todos: bool = False) -> bool:
    global _THREAD
    if _THREAD and _THREAD.is_alive():
        return False

    def rodar():
        try:
            r = indexar_pendentes(todos)
            log.info("leitor de I/O: %s", r)
        except Exception:
            log.exception("falha no leitor de I/O")

    _THREAD = threading.Thread(target=rodar, daemon=True, name="indexar-io")
    _THREAD.start()
    return True


def indexando() -> bool:
    return bool(_THREAD and _THREAD.is_alive())


# --------------------------------------------------------------------------- cadastro RemotasPuma

def ler_data_js(texto: str) -> dict:
    i, j = texto.find("{"), texto.rfind("}")
    if i < 0 or j < i:
        raise ValueError("arquivo sem o objeto REMOTAS_DATA")
    d = json.loads(texto[i:j + 1])
    if not isinstance(d.get("points"), list) or not isinstance(d.get("remotas"), list):
        raise ValueError("REMOTAS_DATA sem 'points' e 'remotas'")
    return d


def importar_remotas_puma(texto: str) -> dict:
    """Substitui o cadastro RemotasPuma (pontos + remotas com capacidade) pelo conteúdo do data.js."""
    d = ler_data_js(texto)
    pontos = []
    for p in d["points"]:
        tag = str(p.get("tag") or "").strip()
        if not tag:
            continue
        pontos.append({"tag": tag, "prefixo": str(p.get("prefixo") or "") or None, "descricao": p.get("desc") or None,
                       "tipo": tipo_do_ponto(p.get("tipo"), None) or p.get("tipo") or None,
                       "remota": p.get("painel") or None, "borneira": str(p.get("borneira") or "") or None,
                       "canal": str(p.get("ch") or "") or None, "area": p.get("area") or None,
                       "extra": {"obs": p["obs"]} if p.get("obs") else None})
    with db.session() as conn:
        conn.execute("DELETE FROM io_pontos WHERE fonte = ?", (FONTE_PUMA,))
        conn.execute("DELETE FROM remotas WHERE fonte = ?", (FONTE_PUMA,))
        n = _inserir(conn, pontos, FONTE_PUMA, None, None)
        conn.executemany(
            "INSERT INTO remotas (id, area, sistema, grupo, rota, seq, capacidade_json, fonte) VALUES (?,?,?,?,?,?,?,?)"
            " ON CONFLICT(id) DO UPDATE SET area=excluded.area, sistema=excluded.sistema, grupo=excluded.grupo,"
            " rota=excluded.rota, seq=excluded.seq, capacidade_json=excluded.capacidade_json, fonte=excluded.fonte",
            [(r["id"], r.get("area"), r.get("sistema"), r.get("grupo"), r.get("rota"), r.get("seq"),
              json.dumps(r.get("capacidade")) if r.get("capacidade") else None, FONTE_PUMA)
             for r in d["remotas"] if r.get("id")])
    return {"pontos": n, "remotas": len(d["remotas"]), "gerado_em": d.get("generatedAt")}


# --------------------------------------------------------------------------- consultas

# Um mesmo ponto pode estar em mais de uma lista (ex. LIO de pontos e lista de sinais): agrupa pelo endereço.
_CHAVE_PONTO = "p.tag_norm || '|' || COALESCE(p.remota,'') || '|' || COALESCE(p.slot,'') || '|' || COALESCE(p.canal,'')"


def _ponto(r) -> dict:
    d = dict(r)
    extra = d.pop("extra_json", None)
    d["extra"] = json.loads(extra) if extra else {}
    d["fontes"] = sorted(set((d.get("fontes") or d.get("fonte") or "").split("\x1f")) - {""})
    d.pop("fonte", None)
    return d


def buscar_pontos(conn, consulta: str = "", area: str | None = None, tipo: str | None = None,
                  remota: str | None = None, limite: int = 200) -> list[dict]:
    where, params = [], []
    termos = [t for t in re.split(r"\s+", consulta.strip()) if t]
    for t in termos:
        nt = norm_tag(t)
        if not nt:
            continue
        where.append("(p.tag_norm LIKE ? OR REPLACE(REPLACE(UPPER(COALESCE(p.remota,'')),'-',''),'+','') LIKE ?"
                     " OR UPPER(COALESCE(p.jb,'')) LIKE ? OR UPPER(COALESCE(p.descricao,'')) LIKE ?"
                     " OR UPPER(COALESCE(p.cartao,'')) LIKE ?)")
        params += [f"%{nt}%", f"%{nt}%", f"%{t.upper()}%", f"%{t.upper()}%", f"%{t.upper()}%"]
    if area:
        where.append("p.area = ?"); params.append(area)
    if tipo:
        where.append("p.tipo = ?"); params.append(tipo)
    if remota:
        where.append("p.remota = ?"); params.append(remota)
    sql = ("SELECT MIN(p.id) id, p.tag, p.prefixo, MAX(p.descricao) descricao, MAX(p.tipo) tipo, p.remota,"
           " MAX(p.cartao) cartao, p.slot, p.canal, MAX(p.borneira) borneira, MAX(p.jb) jb, MAX(p.jb_borne) jb_borne,"
           " MAX(p.controlador) controlador, MAX(p.area) area, MAX(p.local) local, MAX(p.extra_json) extra_json,"
           " GROUP_CONCAT(p.fonte, char(31)) fontes, MAX(p.doc_id) doc_id"
           " FROM io_pontos p" + (" WHERE " + " AND ".join(where) if where else "") +
           f" GROUP BY {_CHAVE_PONTO} ORDER BY p.remota, CAST(p.slot AS INTEGER), p.slot,"
           " CAST(p.canal AS INTEGER), p.canal, p.tag LIMIT ?")
    params.append(max(1, min(int(limite), 5000)))
    return [_ponto(r) for r in conn.execute(sql, params)]


def _capacidade(js: str | None) -> dict | None:
    return json.loads(js) if js else None


def listar_remotas(conn) -> list[dict]:
    """Remotas com pontos por tipo (sem repetir pontos de listas diferentes) e capacidade, quando houver."""
    cad = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM remotas")}
    agg: dict[str, dict] = {}
    rows = conn.execute(
        f"SELECT p.remota, COALESCE(MAX(p.tipo), '?') tipo, MAX(p.area) area, GROUP_CONCAT(DISTINCT p.fonte) fontes,"
        f" MAX(p.controlador) controlador FROM io_pontos p WHERE p.remota IS NOT NULL GROUP BY p.remota, {_CHAVE_PONTO}")
    for r in rows:
        a = agg.setdefault(r["remota"], {"id": r["remota"], "pontos": 0, "tipos": {}, "areas": {}, "fontes": set(),
                                         "controladores": set()})
        a["pontos"] += 1
        a["tipos"][r["tipo"]] = a["tipos"].get(r["tipo"], 0) + 1
        if r["area"]:
            a["areas"][r["area"]] = a["areas"].get(r["area"], 0) + 1
        a["fontes"].update((r["fontes"] or "").split(","))
        if r["controlador"]:
            a["controladores"].add(r["controlador"])
    out = []
    for rid in set(agg) | set(cad):
        a = agg.get(rid, {"id": rid, "pontos": 0, "tipos": {}, "areas": {}, "fontes": set(), "controladores": set()})
        c = cad.get(rid, {})
        areas = sorted(a["areas"], key=lambda k: -a["areas"][k])
        cap = _capacidade(c.get("capacidade_json"))
        tot = (cap or {}).get("total") or {}
        out.append({
            "id": rid, "area": c.get("area") or (areas[0] if areas else None), "areas": areas,
            "sistema": c.get("sistema") or (", ".join(sorted(a["controladores"])) or None),
            "grupo": c.get("grupo"), "rota": c.get("rota"), "seq": c.get("seq"),
            "pontos": a["pontos"], "tipos": a["tipos"], "capacidade": cap,
            "ocupacao": (tot["utilizados"] / tot["instalados"]) if tot.get("instalados") else None,
            "reserva_ok": (tot["reservaAtual"] >= tot["reservaExigida"]) if tot.get("instalados") else None,
            "fontes": sorted(f for f in a["fontes"] | ({c["fonte"]} if c.get("fonte") else set()) if f),
        })
    out.sort(key=lambda r: (r["area"] or "~", r["grupo"] or "~", r["seq"] or 0, r["id"]))
    return out


def painel(conn) -> dict:
    """Indicadores do dashboard de remotas."""
    remotas = listar_remotas(conn)
    tipos: dict[str, int] = {}
    areas: dict[str, dict] = {}
    for r in remotas:
        for t, n in r["tipos"].items():
            tipos[t] = tipos.get(t, 0) + n
        a = areas.setdefault(r["area"] or "(sem área)", {"area": r["area"] or "(sem área)", "remotas": 0, "pontos": 0,
                                                         "instalados": 0, "utilizados": 0})
        a["remotas"] += 1
        a["pontos"] += r["pontos"]
        tot = (r["capacidade"] or {}).get("total") or {}
        a["instalados"] += tot.get("instalados") or 0
        a["utilizados"] += tot.get("utilizados") or 0
    com_cap = [r for r in remotas if r["ocupacao"] is not None]
    inst = sum(r["capacidade"]["total"]["instalados"] for r in com_cap)
    util = sum(r["capacidade"]["total"]["utilizados"] for r in com_cap)
    faixas = [{"faixa": f, "n": 0} for f in ("< 50%", "50–70%", "70–90%", "≥ 90%")]
    for r in com_cap:
        o = r["ocupacao"]
        faixas[0 if o < .5 else 1 if o < .7 else 2 if o < .9 else 3]["n"] += 1
    fontes = [dict(r) for r in conn.execute(
        "SELECT p.fonte, p.doc_id, COUNT(*) pontos, COUNT(DISTINCT p.remota) remotas FROM io_pontos p"
        " GROUP BY p.fonte, p.doc_id ORDER BY pontos DESC")]
    sem_endereco = conn.execute("SELECT COUNT(*) FROM io_pontos WHERE remota IS NULL").fetchone()[0]
    return {
        "gerado_em": db.agora(),
        "indexando": indexando(),
        "kpis": {
            "remotas": len(remotas), "pontos": sum(r["pontos"] for r in remotas) + sem_endereco,
            "areas": len([a for a in areas if a != "(sem área)"]),
            "ocupacao": (util / inst) if inst else None, "instalados": inst, "utilizados": util,
            "livres": inst - util if inst else None,
            "criticas": sum(1 for r in com_cap if r["ocupacao"] >= .9),
            "sem_reserva": sum(1 for r in com_cap if r["reserva_ok"] is False),
            "listas": len([f for f in fontes if f["doc_id"]]),
        },
        "por_tipo": [{"tipo": t, "n": tipos[t]} for t in sorted(tipos, key=lambda t: (TIPOS + [t]).index(t))],
        "por_area": sorted(areas.values(), key=lambda a: -a["pontos"]),
        "faixas_ocupacao": faixas,
        "fontes": fontes,
        "remotas": remotas,
    }


def remota(conn, rid: str) -> dict | None:
    r = next((x for x in listar_remotas(conn) if x["id"] == rid), None)
    if r is None:
        return None
    r["pontos_lista"] = buscar_pontos(conn, remota=rid, limite=5000)
    # Cartões: agrupa os pontos por slot/cartão para o mapa visual do rack.
    cartoes: dict[str, dict] = {}
    for p in r["pontos_lista"]:
        k = p.get("slot") or p.get("borneira") or "—"
        c = cartoes.setdefault(k, {"slot": k, "cartao": p.get("cartao"), "tipo": p.get("tipo"), "canais": 0})
        c["canais"] += 1
        c["cartao"] = c["cartao"] or p.get("cartao")
        c["tipo"] = c["tipo"] or p.get("tipo")
    def ordem(c):  # slots numéricos primeiro, em ordem; borneiras e sem slot no fim
        s = str(c["slot"])
        return (0, int(s), "") if s.isdigit() else (1, 0, s)
    r["cartoes"] = sorted(cartoes.values(), key=ordem)
    return r


def endereco(conn, consulta: str, limite: int = 20) -> dict:
    """Para o assistente: pontos que casam com a consulta + o resumo das remotas envolvidas."""
    pontos = buscar_pontos(conn, consulta, limite=limite)
    ids = {p["remota"] for p in pontos if p["remota"]}
    rem = [r for r in listar_remotas(conn) if r["id"] in ids]
    for r in rem:
        r.pop("capacidade", None)
    return {"pontos": pontos, "remotas": rem, "total": len(pontos)}


def cartao_visual(p: dict) -> dict:
    """Versão enxuta de um ponto para o card do chat."""
    campos = ("tag", "prefixo", "descricao", "tipo", "remota", "cartao", "slot", "canal", "borneira", "jb", "jb_borne",
              "controlador", "area", "local", "doc_id")
    d = {k: p.get(k) for k in campos if p.get(k) not in (None, "")}
    d["fontes"] = p.get("fontes") or []
    return d
