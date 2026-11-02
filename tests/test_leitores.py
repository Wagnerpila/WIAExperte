"""Leitores de planilha, Visio, DXF/DWG e o código de documento Klabin."""
import zipfile
from datetime import datetime
from pathlib import Path

import openpyxl

from app import db, ingest, leitores


# --------------------------------------------------------------------------- código Klabin

def test_identificar_documento_klabin():
    info = leitores.identificar_documento("34231-I-LIO-00117-0001_EN.pdf")
    assert info["codigo"] == "34231-I-LIO-00117-0001"
    assert info["maquina"] == "MP28" and info["disciplina_nome"] == "Instrumentação"
    assert info["tipo_doc_nome"] == "Lista de I/O" and info["idioma"] == "inglês"
    info = leitores.identificar_documento("34129-I-EQU-00176-0003.dwg")
    assert info["maquina"] == "MP27" and info["area_nome"] == "SDCD, Monitoramento e Controle"
    assert info["folha"] == "0003" and info["idioma"] is None
    assert leitores.identificar_documento("manual_altivar.pdf") is None
    assert leitores.rotulo_documento(info).startswith("[34129-I-EQU-00176-0003 · MP27 ·")


# --------------------------------------------------------------------------- planilhas

def _planilha_lio(caminho: Path) -> Path:
    wb = openpyxl.Workbook()
    capa = wb.active
    capa.title = "Capa"
    capa["A1"], capa["B1"] = "Área", "Status"          # listas suspensas do modelo (devem ser ignoradas)
    capa["A2"] = "00 - Geral da Fábrica"
    capa["D5"], capa["F5"] = "Área:", "34129 - SDCD, Monitoramento e Controle"
    capa["D6"], capa["F6"] = "Título do documento:", "Valmet DMM Measurement Point List"
    for nome in ("Status", "Areas"):
        wb.create_sheet(nome)["A1"] = "lista auxiliar que não deve ir para a base"
    ws = wb.create_sheet("MPlist")
    ws["A1"], ws["B1"] = "MEASUREMENT POINT LIST", "VA260449H005.01"
    ws["A2"], ws["B2"] = datetime(2019, 10, 17), "Rev. A"
    ws["A4"], ws["C4"] = "Ponto de medição", "Sensor"   # grupos mesclados acima do cabeçalho
    ws.merge_cells("A4:B4")
    ws.merge_cells("C4:D4")
    for col, v in zip("ABCDE", ["Índice", "Target / Objectivo", "Model / Modelo", "Position / Posição",
                                "Box code"]):
        ws[f"{col}5"] = v
    ws.append([1, "MP-27 Depurador Base", "RVT-105", "=34112M3410-VT1", "+34129JB7610"])
    ws.append([2, "MP-27 Depurador Base", "RVT-120", "=34112M3410-VT2", "+34129JB7610"])
    ws.append(["Seção de prensas"])
    ws.append([3, "MP-27 Prensa 1", "RVT-105", "=34121P1-VT1", 7.0])
    for linha in (6, 7, 9):  # no arquivo real são textos; o openpyxl tomaria "=..." por fórmula
        ws[f"D{linha}"].data_type = "s"
    wb.save(caminho)
    return caminho


def test_planilha_cabecalho_grupos_e_capa(tmp_path):
    trechos = leitores.trechos_planilha(_planilha_lio(tmp_path / "34129-I-LIO-00172-0001.xlsx"))
    texto = "\n".join(t for t, _, _ in trechos)
    assert "lista auxiliar" not in texto and "Geral da Fábrica" not in texto
    assert "Área: 34129 - SDCD, Monitoramento e Controle" in texto
    assert "Título do documento: Valmet DMM Measurement Point List" in texto
    assert ("Linha 6: Ponto de medição > Índice: 1; Ponto de medição > Target / Objectivo: MP-27 Depurador Base; "
            "Sensor > Model / Modelo: RVT-105; Sensor > Position / Posição: =34112M3410-VT1; "
            "Box code: +34129JB7610") in texto
    assert "## Seção de prensas" in texto
    assert "Box code: 7;" not in texto and "Box code: 7" in texto  # 7.0 vira 7
    assert "2019-10-17" in texto  # data do cabeçalho do documento
    locais = [loc for _, loc, _ in trechos]
    assert any(loc.startswith("aba 'MPlist', linha") for loc in locais)


def test_planilha_grupo_esparso_sem_mesclagem(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["From", None, None, "To", None, None])
    ws.append(["Room", "Cabinet", "Device", "Room", "Cabinet", "Device"])
    ws.append(["SE-01", "CC1", "FO01", "SE-02", "CC2", "FO02"])
    ws.append(["SE-02", "CC2", "FO01", "SE-03", "CC3", "FO02"])
    p = tmp_path / "cabos.xlsx"
    wb.save(p)
    texto = leitores.trechos_planilha(p)[0][0]
    assert "From > Room: SE-01; From > Cabinet: CC1" in texto and "To > Device: FO02" in texto


def test_planilha_para_csv(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Relatório de alarmes"])
    ws.append(["Data/Hora", "Tag", "Mensagem"])
    ws.append([datetime(2026, 11, 1, 14, 32), "FIC-101", "Vazão baixa"])
    ws.append([datetime(2026, 11, 1, 14, 40), "FIC-101", "Normal"])
    p = tmp_path / "alarmes.xlsx"
    wb.save(p)
    csv_txt = leitores.planilha_para_csv(p)
    assert csv_txt.splitlines()[0] == "Data/Hora;Tag;Mensagem"
    assert "2026-11-01 14:32:00;FIC-101" in csv_txt


# --------------------------------------------------------------------------- Visio

_NS = "http://schemas.microsoft.com/office/visio/2012/main"
_NSR = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _vsdx(caminho: Path) -> Path:
    pages = (f"<Pages xmlns='{_NS}' xmlns:r='{_NSR}'><Page ID='0' Name='Cabinet Layout'><Rel r:id='rId1'/></Page>"
             f"<Page ID='1' Name='Fundo' Background='1'><Rel r:id='rId2'/></Page></Pages>")
    rels = ("<Relationships xmlns='http://schemas.openxmlformats.org/package/2006/relationships'>"
            "<Relationship Id='rId1' Target='page1.xml'/><Relationship Id='rId2' Target='page2.xml'/>"
            "</Relationships>")
    page1 = (f"<PageContents xmlns='{_NS}'><Shapes>"
             "<Shape ID='1'><Section N='Property'>"
             "<Row N='ITEM'><Cell N='Value' V='VAL0418322 : CABINET VX 8806.071'/><Cell N='Label' V='Item'/></Row>"
             "<Row N='SUP'><Cell N='Value' V='Rittal'/><Cell N='Label' V='Supplier'/></Row>"
             "<Row N='P'><Cell N='Value' V='0'/><Cell N='Label' V='Precision'/></Row>"
             "</Section><Text>TOP <cp IX='0'/>FRONT</Text></Shape>"
             "<Shape ID='2'><Text>34129CC7601</Text></Shape>"
             "<Shape ID='3'><Text>34129CC7601</Text></Shape>"
             "</Shapes></PageContents>")
    page2 = f"<PageContents xmlns='{_NS}'><Shapes><Shape ID='9'><Text>logo de fundo</Text></Shape></Shapes></PageContents>"
    with zipfile.ZipFile(caminho, "w") as z:
        z.writestr("visio/pages/pages.xml", pages)
        z.writestr("visio/pages/_rels/pages.xml.rels", rels)
        z.writestr("visio/pages/page1.xml", page1)
        z.writestr("visio/pages/page2.xml", page2)
    return caminho


def test_visio_textos_e_dados_de_forma(tmp_path):
    trechos = leitores.trechos_visio(_vsdx(tmp_path / "armario.vsdx"))
    assert len(trechos) == 1
    texto, local, _ = trechos[0]
    assert local == "página 1 (Cabinet Layout)"
    assert "TOP FRONT {Item: VAL0418322 : CABINET VX 8806.071; Supplier: Rittal}" in texto
    assert texto.count("34129CC7601") == 1  # repetidos saem uma vez só
    assert "Precision" not in texto and "logo de fundo" not in texto


# --------------------------------------------------------------------------- AutoCAD

def _dxf(pares: list[tuple[str, str]]) -> str:
    return "\n".join(f"{c}\n{v}" for c, v in pares) + "\n"


DXF_EXEMPLO = _dxf([
    ("0", "SECTION"), ("2", "HEADER"), ("9", "$ACADVER"), ("1", "AC1032"), ("0", "ENDSEC"),
    ("0", "SECTION"), ("2", "OBJECTS"), ("0", "XRECORD"), ("1", "não é texto do desenho"), ("0", "ENDSEC"),
    ("0", "SECTION"), ("2", "ENTITIES"),
    ("0", "TEXT"), ("8", "0"), ("1", "PAR DE PAIN%%201IS LATERAIS"),
    ("0", "TEXT"), ("1", "12"),
    ("0", "MTEXT"), ("3", "{\\fCalibri|b0|i0|c0|p34;\\H1x;\\C7;Bomba de "), ("1", "Depurador}\\P=34112M3410-VT1"),
    ("0", "MTEXT"), ("1", "\\pxt2,6;xxx-ST\\Pxxx-STL \\U+00D8 50"),
    ("0", "INSERT"), ("2", "VALMETframe_A3x1"), ("66", "1"),
    ("0", "ATTRIB"), ("1", "W1002AAH1-007"), ("2", "E_DRW_NUMBER"),
    ("0", "ATTRIB"), ("1", ""), ("2", "E_VAZIO"),
    ("0", "ATTRIB"), ("1", "Press / Prensa"), ("2", "E_DRW_NAME3"),
    ("0", "SEQEND"),
    ("0", "TEXT"), ("1", "PAR DE PAIN%%201IS LATERAIS"),
    ("0", "DIMENSION"), ("1", "<>"), ("3", "Standard"),
    ("0", "ENDSEC"), ("0", "EOF"),
])


def test_dxf_textos_e_atributos(tmp_path):
    p = tmp_path / "desenho.dxf"
    p.write_text(DXF_EXEMPLO, encoding="utf-8")
    dados = leitores.textos_dxf(p)
    assert dados["textos"] == ["PAR DE PAINÉIS LATERAIS", "Bomba de Depurador\n=34112M3410-VT1",
                               "xxx-ST\nxxx-STL Ø 50"]
    assert dados["blocos"] == ["VALMETframe_A3x1: E_DRW_NUMBER=W1002AAH1-007; E_DRW_NAME3=Press / Prensa"]
    texto = leitores.trechos_dxf(p)[0][0]
    assert "Bomba de Depurador / =34112M3410-VT1" in texto and "Standard" not in texto


def test_dwg_sem_conversor_fica_pendente(tmp_path, monkeypatch):
    monkeypatch.setattr(leitores.shutil, "which", lambda nome: None)
    p = tmp_path / "34231-I-EQU-00120-0003.dwg"
    p.write_bytes(b"AC1032" + b"\x00" * 100)
    doc_id = ingest.registrar_arquivo(p, p.name)
    ingest.processar_documento(doc_id)
    with db.session() as conn:
        d = conn.execute("SELECT tipo, status, observacao FROM documentos WHERE id=?", (doc_id,)).fetchone()
    assert d["tipo"] == "documentacao" and d["status"] == "pendente" and "LibreDWG" in d["observacao"]


# --------------------------------------------------------------------------- integração com a base

def test_ingestao_planilha_klabin_com_ficha_e_duplicado(tmp_path):
    p = _planilha_lio(tmp_path / "34129-I-LIO-00172-0001.xlsx")
    doc_id = ingest.registrar_arquivo(p, p.name)
    ingest.processar_documento(doc_id)
    copia = ingest.registrar_arquivo(p, "copia_" + p.name)
    ingest.processar_documento(copia)
    with db.session() as conn:
        d = conn.execute("SELECT * FROM documentos WHERE id=?", (doc_id,)).fetchone()
        assert d["status"] == "ok" and d["tipo"] == "documentacao"
        assert d["area"] == "34129 - SDCD, Monitoramento e Controle (MP27)"
        trechos = [r["texto"] for r in conn.execute("SELECT texto FROM trechos WHERE doc_id=? ORDER BY ordem",
                                                    (doc_id,))]
        assert trechos[0].startswith("FICHA DO DOCUMENTO 34129-I-LIO-00172-0001")
        assert all(t.startswith("[34129-I-LIO-00172-0001 · MP27 · Lista de I/O]") for t in trechos[1:])
        achados = conn.execute("SELECT t.doc_id FROM trechos_fts JOIN trechos t ON t.id = trechos_fts.rowid"
                               " WHERE trechos_fts MATCH ?", (db.fts_query("34112M3410-VT2 MP27"),)).fetchall()
        assert {r[0] for r in achados} == {doc_id}  # a cópia não foi indexada de novo
        dup = conn.execute("SELECT status, n_trechos, observacao FROM documentos WHERE id=?", (copia,)).fetchone()
    assert dup["status"] == "duplicado" and dup["n_trechos"] == 0 and f"#{doc_id}" in dup["observacao"]
