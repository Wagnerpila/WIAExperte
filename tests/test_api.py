"""Testes da API web com o modelo simulado (nenhuma chamada real à Anthropic)."""
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import agent, auth, db, main


class Bloco(SimpleNamespace):
    def to_dict(self):
        return {k: v for k, v in vars(self).items()}


def resposta(blocos, stop):
    return SimpleNamespace(content=blocos, stop_reason=stop, model="claude-opus-5-5",
                           usage=SimpleNamespace(input_tokens=100, output_tokens=50, cache_read_input_tokens=0,
                                                 cache_creation_input_tokens=0))


class ClienteFalso:
    """Primeiro pede uma busca na base; depois responde com texto."""

    def __init__(self):
        self.chamadas = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.chamadas.append(kw)
        if len(self.chamadas) == 1:
            return resposta([Bloco(type="text", text="Vou buscar."),
                             Bloco(type="tool_use", id="tu_1", name="buscar_base", input={"consulta": "FIC-101"})],
                            "tool_use")
        return resposta([Bloco(type="text", text="Não encontrei isso na base.")], "end_turn")


@pytest.fixture
def cli(monkeypatch):
    falso = ClienteFalso()
    monkeypatch.setattr(agent, "client", lambda: falso)
    auth.criar_usuario("tecnico1", "senha-bem-longa")
    c = TestClient(main.app)
    c.falso = falso
    return c


def login(c):
    r = c.post("/api/login", json={"usuario": "tecnico1", "senha": "senha-bem-longa"})
    assert r.status_code == 200


def test_exige_login(cli):
    assert cli.get("/api/painel").status_code == 401
    assert cli.post("/api/login", json={"usuario": "tecnico1", "senha": "errada"}).status_code == 401
    login(cli)
    assert cli.get("/api/me").json()["username"] == "tecnico1"


def test_conversa_com_ferramenta(cli):
    login(cli)
    cid = cli.post("/api/conversas").json()["id"]
    r = cli.post(f"/api/conversas/{cid}/mensagens", data={"texto": "FIC-101 oscilando"})
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["texto"] == "Não encontrei isso na base."
    assert corpo["ferramentas"][0]["nome"] == "buscar_base"
    # segunda chamada recebeu o histórico append-only com o tool_result
    msgs = cli.falso.chamadas[1]["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"]
    assert msgs[2]["content"][0]["type"] == "tool_result" and msgs[2]["content"][0]["tool_use_id"] == "tu_1"
    assert "fallbacks" in cli.falso.chamadas[0] and cli.falso.chamadas[0]["cache_control"] == {"type": "ephemeral"}
    conv = cli.get(f"/api/conversas/{cid}").json()
    assert conv["titulo"] == "FIC-101 oscilando"
    assert [m["papel"] for m in conv["mensagens"]] == ["user", "assistant"]
    painel = cli.get("/api/painel?dias=0").json()
    assert painel["uso"]["chamadas"] == 2 and painel["uso"]["ferramentas"][0]["nome"] == "buscar_base"


def test_conversa_de_outro_usuario(cli):
    login(cli)
    cid = cli.post("/api/conversas").json()["id"]
    auth.criar_usuario("outro", "outra-senha-longa")
    c2 = TestClient(main.app)
    c2.post("/api/login", json={"usuario": "outro", "senha": "outra-senha-longa"})
    assert c2.get(f"/api/conversas/{cid}").status_code == 404


def test_upload_e_ocorrencia(cli):
    login(cli)
    r = cli.post("/api/documentos", files=[("arquivos", ("manual.txt", b"Manual do posicionador XYZ", "text/plain"))],
                 data={"tipo": "documentacao", "plataforma": "Instrumentação"})
    assert r.status_code == 200
    doc_id = r.json()["ids"][0]
    d = cli.get(f"/api/documentos/{doc_id}").json()  # TestClient roda as background tasks
    assert d["status"] == "ok" and d["trechos"]
    r = cli.post("/api/ocorrencias", json={"data_hora": "2026-11-01T08:00", "equipamento": "Picador 2",
                                           "sintoma": "Desarme", "tempo_parada_min": 30})
    assert r.status_code == 200
    p = cli.get("/api/painel?dias=0").json()
    assert p["ocorrencias"]["total"] == 1 and p["base"]["por_tipo"][0]["tipo"] == "documentacao"
    assert cli.post("/api/ocorrencias", json={"data_hora": "ontem", "equipamento": "x", "sintoma": "y"}).status_code == 400


def test_foto_no_chat_rejeita_outros_arquivos(cli):
    login(cli)
    cid = cli.post("/api/conversas").json()["id"]
    r = cli.post(f"/api/conversas/{cid}/mensagens", data={"texto": "veja"},
                 files=[("fotos", ("x.pdf", b"%PDF", "application/pdf"))])
    assert r.status_code == 400


def test_admin_pelo_ambiente(monkeypatch):
    from app import config
    monkeypatch.setattr(config, "ADMIN_USUARIO", "Wagner")
    monkeypatch.setattr(config, "ADMIN_SENHA", "senha-do-env")
    auth.garantir_admin_do_ambiente()
    assert auth.autenticar("wagner", "senha-do-env")["papel"] == "admin"
    monkeypatch.setattr(config, "ADMIN_SENHA", "outra-senha")  # nova implantação redefine a senha
    auth.garantir_admin_do_ambiente()
    assert auth.autenticar("wagner", "senha-do-env") is None
    assert auth.autenticar("WAGNER", "outra-senha")
    monkeypatch.setattr(config, "ADMIN_SENHA", "curta")  # senha inválida não derruba o app
    auth.garantir_admin_do_ambiente()
    assert auth.autenticar("wagner", "outra-senha")


def test_apagar_duplicados(cli, tmp_path):
    from pathlib import Path

    from app import ingest

    assert cli.get("/api/duplicados").status_code == 401  # exige login
    login(cli)
    p = tmp_path / "34231-I-EQU-00120-0001.txt"
    p.write_text("tag 34221RNF-30101-VT1", encoding="utf-8")
    ids = []
    for nome in ("a.txt", "b.txt", "c.txt"):
        ids.append(ingest.registrar_arquivo(p, nome))
        ingest.processar_documento(ids[-1])
    orfao_src = tmp_path / "orfao.txt"
    orfao_src.write_text("conteúdo cujo original foi apagado", encoding="utf-8")
    orig = ingest.registrar_arquivo(orfao_src, "orig.txt")
    ingest.processar_documento(orig)
    orfao = ingest.registrar_arquivo(orfao_src, "orfao.txt")
    ingest.processar_documento(orfao)
    with db.session() as conn:
        conn.execute("DELETE FROM documentos WHERE id = ?", (orig,))  # sem o original, não é apagado
        caminhos = {r["id"]: r["caminho"] for r in conn.execute("SELECT id, caminho FROM documentos")}

    assert cli.get("/api/duplicados").json()["quantidade"] == 2
    r = cli.post("/api/duplicados/apagar").json()
    assert r["apagados"] == 2 and r["bytes"] > 0
    with db.session() as conn:
        restantes = {row["id"]: row["status"] for row in conn.execute("SELECT id, status FROM documentos")}
    assert restantes == {ids[0]: "ok", orfao: "duplicado"}
    assert not Path(caminhos[ids[1]]).exists() and Path(caminhos[ids[0]]).exists()
    assert cli.get("/api/duplicados").json()["quantidade"] == 0


def test_envio_em_partes(cli):
    import io
    import zipfile

    from app import config

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("34239-I-MLH-00272-0009/notas.txt", "malha 34239 tag PT-101")
        zf.writestr("34239-I-MLH-00272-0009/ruido.bin", bytes(range(256)) * 200)
    dados = buf.getvalue()
    login(cli)
    r = cli.post("/api/envios", json={"nome": "greendocs.zip", "tamanho": len(dados)})
    assert r.status_code == 200
    eid = r.json()["id"]
    meio = len(dados) // 2
    assert cli.put(f"/api/envios/{eid}?offset=0", content=dados[:meio]).json()["recebido"] == meio
    # parte repetida (ex.: rede caiu depois de o servidor gravar): 409 com o ponto de retomada
    rep = cli.put(f"/api/envios/{eid}?offset=0", content=dados[:meio])
    assert rep.status_code == 409 and rep.json()["recebido"] == meio
    assert cli.post(f"/api/envios/{eid}/concluir").status_code == 409  # ainda incompleto
    assert cli.put(f"/api/envios/{eid}?offset={meio}", content=dados[meio:]).json()["recebido"] == len(dados)
    r = cli.post(f"/api/envios/{eid}/concluir", data={"tipo": "auto"})
    assert r.status_code == 200
    doc_id = r.json()["ids"][0]
    with db.session() as conn:
        d = conn.execute("SELECT nome, status, sha256 FROM documentos WHERE id=?", (doc_id,)).fetchone()
        filhos = [x["nome"] for x in conn.execute("SELECT nome FROM documentos WHERE pai_id=?", (doc_id,))]
    import hashlib
    assert d["nome"] == "greendocs.zip" and d["status"] == "ok"
    assert d["sha256"] == hashlib.sha256(dados).hexdigest()
    assert sorted(filhos) == ["notas.txt", "ruido.bin"]
    assert cli.get(f"/api/envios/{eid}").status_code == 404  # concluído não pode ser reusado
    assert cli.post("/api/envios", json={"nome": "x.zip", "tamanho": (config.MAX_UPLOAD_MB + 1) << 20}).status_code == 413


def test_envio_em_partes_de_outro_usuario(cli):
    login(cli)
    eid = cli.post("/api/envios", json={"nome": "a.txt", "tamanho": 3}).json()["id"]
    auth.criar_usuario("outro2", "outra-senha-longa")
    c2 = TestClient(main.app)
    c2.post("/api/login", json={"usuario": "outro2", "senha": "outra-senha-longa"})
    assert c2.put(f"/api/envios/{eid}?offset=0", content=b"abc").status_code == 404
    assert c2.put("/api/envios/..%2F..%2Fwia?offset=0", content=b"abc").status_code == 404
