"""Endereçamento de I/O: leitura de listas de I/O, cadastro RemotasPuma, painel, API e ferramenta do chat."""
import json

import openpyxl
import pytest
from fastapi.testclient import TestClient

from app import agent, auth, db, enderecos, ingest, main

CABECALHO = ["Índice", "Target / Objectivo", "Variable / Variável", "Model / Modelo", "Position / Posição",
             "Box code / Código da  caixa", "Terminal strip / Tira de  terminais", "Connector / Con.",
             "Pin / Terminais", "I/O Slot / Posição do cartão", "I/O Unit / Tipo do cartão", "Channel / Canal",
             "Cabinet / Gabinete", "ACN Position / ACN posição"]
GRUPOS = [(2, 3, "Ponto de medição"), (4, 5, "Sensor"), (6, 7, "Caixa de junção"), (8, 13, "ACN I/O Gabinete"),
          (14, 14, "ACN Gabinete")]


def lista_io(path, n=8, titulo="LISTA DE PONTOS"):
    """Planilha no formato da lista de pontos da MP28 (grupos mesclados acima do cabeçalho)."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "MPlist"
    ws["F2"] = titulo
    for c0, c1, nome in GRUPOS:
        ws.cell(4, c0, nome)
        if c1 > c0:
            ws.merge_cells(start_row=4, start_column=c0, end_row=4, end_column=c1)
    for c, nome in enumerate(CABECALHO, start=1):
        ws.cell(5, c, nome)
    for i in range(n):
        ws.append([i + 1, "MP-28 1o Prensa Rolo 30101", "aceleração", "RVT-105", f"34221RNF-30101-VT{i + 1}",
                   "34231JB28010", f"{3 * i + 1}/{3 * i + 2}/{3 * i + 3}", "X06", f"{2 * i + 1}/{2 * i + 2}",
                   0, "AIF8V", i, "34231CC28000", "CP11"])
    wb.save(path)
    return path


def test_le_lista_de_io_pelos_cabecalhos(tmp_path):
    pts = enderecos.pontos_da_planilha(lista_io(tmp_path / "34231-I-LIO-00117-0001.xlsx"))
    assert len(pts) == 8
    p = pts[2]
    assert p["tag"] == "34221RNF-30101-VT3"
    assert p["remota"] == "34231CC28000" and p["jb"] == "34231JB28010" and p["jb_borne"] == "7/8/9"
    assert p["cartao"] == "AIF8V" and p["slot"] == "0" and p["canal"] == "2" and p["borneira"] == "5/6"
    assert p["controlador"] == "CP11" and p["tipo"] == "AI" and p["extra"]["conector"] == "X06"
    assert p["descricao"].startswith("MP-28")


def test_planilha_comum_nao_vira_lista_de_io(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Item", "Descrição", "Quantidade", "Fabricante"])
    for i in range(10):
        ws.append([i, f"Parafuso M{i}", 3, "ACME"])
    wb.save(tmp_path / "mat.xlsx")
    assert enderecos.pontos_da_planilha(tmp_path / "mat.xlsx") == []


@pytest.mark.parametrize("tipo,cartao,esperado", [
    ("DI", None, "DI"), ("ED", None, "DI"), (None, "AIF8V", "AI"), (None, "DOF16", "DO"), ("RTD", None, "RTD/TC"),
    (None, "PT100", "RTD/TC"), (None, "XYZ", None), ("AO", "DIF", "AO")])
def test_tipo_do_ponto(tipo, cartao, esperado):
    assert enderecos.tipo_do_ponto(tipo, cartao) == esperado


DATA_JS = "window.REMOTAS_DATA = " + json.dumps({
    "generatedAt": "2026-08-02T15:48:00Z",
    "points": [
        {"area": "Máquina de Papel MP28", "prefixo": "34206", "tag": "ZSH-11250", "desc": "Chave fim-de-curso",
         "painel": "34231-RIO-27760", "borneira": "CP01.2.0.0", "ch": "0", "tipo": "DI", "obs": "inferida"},
        {"area": "Máquina de Papel MP28", "prefixo": "34206", "tag": "FT-11251", "desc": "Vazão",
         "painel": "34231-RIO-27760", "borneira": "CP01.2.1.0", "ch": "3", "tipo": "AI", "obs": ""},
        {"area": "Preparo de Madeira", "prefixo": "31112", "tag": "XS1521", "desc": "Falha do termistor",
         "painel": "31100-RIO-12-11", "borneira": "110111", "ch": "3", "tipo": "DI", "obs": ""},
    ],
    "remotas": [
        {"id": "34231-RIO-27760", "area": "Máquina de Papel MP28", "sistema": "SDCD · CP01",
         "capacidade": {"tipos": {"DI": {"instalados": 16, "utilizados": 15, "reservaExigida": 3, "reservaAtual": 1}},
                        "total": {"instalados": 20, "utilizados": 19, "reservaExigida": 4, "reservaAtual": 1}}},
        {"id": "31100-RIO-12-11", "area": "Preparo de Madeira", "grupo": "PMAD", "rota": "Rota1", "seq": 2,
         "capacidade": {"tipos": {}, "total": {"instalados": 10, "utilizados": 4, "reservaExigida": 2,
                                               "reservaAtual": 6}}},
    ],
}, ensure_ascii=False) + ";"


def test_importa_cadastro_e_monta_painel(tmp_path):
    r = enderecos.importar_remotas_puma(DATA_JS)
    assert r == {"pontos": 3, "remotas": 2, "gerado_em": "2026-08-02T15:48:00Z"}
    enderecos.importar_remotas_puma(DATA_JS)  # reimportar substitui, não duplica

    # lista de I/O da base entra junto, inclusive repetida em outra planilha (não conta duas vezes)
    for nome in ("34231-I-LIO-00117-0001.xlsx", "34231-I-LIO-00118-0001.xlsx"):
        doc = ingest.registrar_arquivo(lista_io(tmp_path / nome, titulo=nome), nome, enviado_por="t")
        ingest.processar_documento(doc)

    with db.session() as conn:
        p = enderecos.painel(conn)
        k = p["kpis"]
        assert k["remotas"] == 3 and k["pontos"] == 3 + 8 and k["listas"] == 2
        assert k["instalados"] == 30 and k["utilizados"] == 23 and k["criticas"] == 1 and k["sem_reserva"] == 1
        assert {t["tipo"]: t["n"] for t in p["por_tipo"]} == {"DI": 2, "AI": 9}
        cc = next(r for r in p["remotas"] if r["id"] == "34231CC28000")
        assert cc["pontos"] == 8 and cc["ocupacao"] is None and cc["sistema"] == "CP11"
        assert cc["fontes"] == ["34231-I-LIO-00117-0001", "34231-I-LIO-00118-0001"]

        ponto = enderecos.buscar_pontos(conn, "34221RNF-30101-VT3")
        assert len(ponto) == 1 and ponto[0]["canal"] == "2" and len(ponto[0]["fontes"]) == 2
        assert enderecos.buscar_pontos(conn, "ZSH11250")[0]["prefixo"] == "34206"  # sem hífen também acha
        assert len(enderecos.buscar_pontos(conn, "JB28010")) == 8

        det = enderecos.remota(conn, "34231CC28000")
        assert det["cartoes"] == [{"slot": "0", "cartao": "AIF8V", "tipo": "AI", "canais": 8}]

    # apagar a planilha da base tira os pontos dela
    with db.session() as conn:
        conn.execute("DELETE FROM documentos WHERE nome = '34231-I-LIO-00118-0001.xlsx'")
        assert enderecos.buscar_pontos(conn, "34221RNF-30101-VT3")[0]["fontes"] == ["34231-I-LIO-00117-0001"]


def test_indexa_planilhas_que_ja_estavam_na_base(tmp_path):
    doc = ingest.registrar_arquivo(lista_io(tmp_path / "34231-I-LIO-00117-0001.xlsx"), "34231-I-LIO-00117-0001.xlsx")
    ingest.processar_documento(doc)
    with db.session() as conn:  # simula base antiga, de antes do leitor de I/O
        conn.execute("DELETE FROM io_pontos")
        conn.execute("DELETE FROM io_docs")
    assert enderecos.indexar_pendentes() == {"planilhas": 1, "com_pontos": 1, "pontos": 8}
    assert enderecos.indexar_pendentes()["planilhas"] == 0  # já lida


def test_ferramenta_do_chat_devolve_pontos(tmp_path):
    enderecos.importar_remotas_puma(DATA_JS)
    saida, ok, resumo = agent.executar_ferramenta("endereco_io", {"consulta": "ZSH-11250"}, "t", 1)
    assert ok and "1 pontos" in resumo
    res = json.loads(saida)
    assert res["pontos"][0]["remota"] == "34231-RIO-27760" and res["remotas"][0]["id"] == "34231-RIO-27760"
    card = enderecos.cartao_visual(res["pontos"][0])
    assert card["tag"] == "ZSH-11250" and card["borneira"] == "CP01.2.0.0" and "extra" not in card


def test_api_remotas(tmp_path):
    auth.criar_usuario("tecnico1", "senha-bem-longa")
    c = TestClient(main.app)
    assert c.get("/api/remotas").status_code == 401
    c.post("/api/login", json={"usuario": "tecnico1", "senha": "senha-bem-longa"})
    assert c.get("/api/remotas").json()["kpis"]["remotas"] == 0
    r = c.post("/api/remotas/importar", files={"arquivo": ("data.js", DATA_JS.encode("utf-8"), "text/javascript")})
    assert r.status_code == 200, r.text
    d = c.get("/api/remotas").json()
    assert d["kpis"]["remotas"] == 2 and d["por_area"][0]["area"] == "Máquina de Papel MP28"
    det = c.get("/api/remotas/34231-RIO-27760").json()
    assert det["pontos"] == 2 and len(det["pontos_lista"]) == 2
    assert c.get("/api/remotas/NAO-EXISTE").status_code == 404
    assert [p["tag"] for p in c.get("/api/io", params={"tipo": "AI"}).json()] == ["FT-11251"]
    ruim = c.post("/api/remotas/importar", files={"arquivo": ("x.js", b"nada aqui", "text/javascript")})
    assert ruim.status_code == 400
    assert "remotas.js?v=" in c.get("/").text
