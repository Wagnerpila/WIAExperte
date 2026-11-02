import zipfile
from pathlib import Path

from app import agent, db, ingest, painel


def _arquivo(tmp_path: Path, nome: str, texto: str) -> Path:
    p = tmp_path / nome
    p.write_text(texto, encoding="utf-8")
    return p


def test_fts_query_neutraliza_operadores():
    q = db.fts_query('FIC-101 "drop" NOT válvula* OR')
    assert '"FIC 101"' in q and '"FIC"' in q and '"101"' in q
    assert "*" not in q
    with db.session() as conn:  # não pode dar erro de sintaxe FTS5
        conn.execute("SELECT * FROM trechos_fts WHERE trechos_fts MATCH ?", (q,)).fetchall()
    assert db.fts_query("   ") == ""


def test_parse_dt_formatos():
    assert ingest.parse_dt("2026-11-01 14:32:10") == "2026-11-01T14:32:10"
    assert ingest.parse_dt("01/11/2026 14:32") == "2026-11-01T14:32:00"
    assert ingest.parse_dt("01.11.26 07:05:09") == "2026-11-01T07:05:09"
    assert ingest.parse_dt("15/10/2026") == "2026-10-15T00:00:00"
    assert ingest.parse_dt("lixo") is None


def test_whatsapp_android_e_ios():
    android = ("01/11/2026 14:32 - João: Picador 2 desarmou de novo\n"
               "continuação da mensagem\n"
               "01/11/2026 14:35 - Maria: Viu o OCF no Altivar?\n"
               "01/11/2026 14:36 - Você entrou usando o link\n")
    msgs = ingest.parse_whatsapp(android)
    assert len(msgs) == 3
    assert msgs[0]["autor"] == "João" and "continuação" in msgs[0]["texto"]
    assert msgs[2]["autor"] is None
    ios = "[01/11/2026, 14:32:10] João: Picador 2 desarmou\n[01/11/2026, 14:33:00] Maria: ok"
    msgs = ingest.parse_whatsapp(ios)
    assert [m["autor"] for m in msgs] == ["João", "Maria"]
    assert msgs[0]["ts"] == "2026-11-01T14:32:10"


def test_ingestao_e_busca(tmp_path):
    log = "\n".join([f"2026-10-20 10:{i:02d}:00 evento normal {i}" for i in range(60)]
                    + ["2026-10-20 11:00:00 ALARME FIC-101 desvio alto, válvula FV-101 travada"])
    doc_id = ingest.registrar_arquivo(_arquivo(tmp_path, "log_caldeira.log", log), "log_caldeira.log",
                                      enviado_por="teste", area="Caldeira")
    ingest.processar_documento(doc_id)
    with db.session() as conn:
        d = conn.execute("SELECT * FROM documentos WHERE id=?", (doc_id,)).fetchone()
        assert d["status"] == "ok" and d["tipo"] == "log" and d["n_trechos"] >= 2
        r = agent.t_buscar_base(conn, {"consulta": "FIC-101 travada"}, "teste")
        assert r["resultados"] and "FIC-101" in r["resultados"][0]["texto"]
        assert r["resultados"][0]["ts"].startswith("2026-10-20")
        # filtro por janela de tempo e acento ignorado
        r2 = agent.t_buscar_base(conn, {"consulta": "valvula", "data_inicio": "2026-10-21"}, "teste")
        assert r2["resultados"] == []
        r3 = agent.t_buscar_base(conn, {"consulta": "valvula"}, "teste")
        assert r3["resultados"]


def test_duplicado_e_binario(tmp_path):
    p = _arquivo(tmp_path, "a.txt", "conteúdo igual")
    a = ingest.registrar_arquivo(p, "a.txt")
    ingest.processar_documento(a)
    b = ingest.registrar_arquivo(p, "b.txt")
    ingest.processar_documento(b)
    proj = tmp_path / "Linha2.ap17"
    proj.write_bytes(b"\x00\x01binario")
    c = ingest.registrar_arquivo(proj, proj.name)
    ingest.processar_documento(c)
    with db.session() as conn:
        assert "idêntico" in conn.execute("SELECT observacao FROM documentos WHERE id=?", (b,)).fetchone()[0]
        row = conn.execute("SELECT tipo, status FROM documentos WHERE id=?", (c,)).fetchone()
        assert (row["tipo"], row["status"]) == ("backup", "binario")


def test_zip_whatsapp_sem_zip_slip(tmp_path):
    z = tmp_path / "WhatsApp Chat - Manutencao.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("_chat.txt", "01/11/2026 14:32 - João: bomba B-12 com cavitação\n")
        zf.writestr("../../fora.txt", "tentativa de escapar da pasta")
    zid = ingest.registrar_arquivo(z, z.name)
    ingest.processar_documento(zid)
    with db.session() as conn:
        filhos = conn.execute("SELECT nome, tipo, status, caminho FROM documentos WHERE pai_id=?", (zid,)).fetchall()
        assert {f["nome"] for f in filhos} == {"_chat.txt", "fora.txt"}
        chat = next(f for f in filhos if f["nome"] == "_chat.txt")
        assert chat["tipo"] == "whatsapp" and chat["status"] == "ok"
        for f in filhos:  # tudo dentro da pasta da base
            assert Path(f["caminho"]).resolve().parent == ingest.config.FILES_DIR.resolve()
        assert agent.t_buscar_base(conn, {"consulta": "cavitação"}, "t")["resultados"]


def test_alarmes_indicadores(tmp_path):
    linhas = ["Data/Hora;Tag;Mensagem;Prioridade;Estado"]
    # 12 alarmes em 5 minutos -> inundação; tag LIC-1 com chattering
    for i in range(12):
        linhas.append(f"01/11/2026 08:{i // 4:02d}:{(i % 4) * 10:02d};LIC-1;Nível alto;Alta;ALARM")
    linhas.append("01/11/2026 09:00:00;PT-9;Pressão baixa;Média;ALARM")
    linhas.append("03/11/2026 09:00:00;TT-3;Temp alta;Baixa;ALARM")
    linhas.append("03/11/2026 09:30:00;TT-3;Temp alta;Baixa;NORMAL")
    csv_p = _arquivo(tmp_path, "alarmes.csv", "\n".join(linhas))
    doc_id = ingest.registrar_arquivo(csv_p, "alarmes.csv", tipo="alarmes")
    ingest.processar_documento(doc_id)
    with db.session() as conn:
        assert conn.execute("SELECT COUNT(*) FROM alarmes").fetchone()[0] == 15
        a = painel.alarmes(conn, None)
    assert a["anunciados"] == 14 and a["tem_estado"]
    assert a["pico_10min"] == 12 and a["janelas_flood"] == 1
    assert a["chattering"][0]["tag"] == "LIC-1"
    assert {s["tag"] for s in a["standing"]} == {"LIC-1", "PT-9"}  # TT-3 voltou ao normal


def test_ocorrencias_csv_e_painel(tmp_path):
    texto = ("Data;Equipamento;Sintoma;Causa;Categoria;Tempo parada\n"
             "01/09/2026 10:00;Picador 2;Desarme OCF;Rampa curta;elétrica;1:30\n"
             "11/09/2026 10:00;Picador 2;Desarme OCF;Rampa curta;elétrica;30\n"
             "05/09/2026 08:00;Bomba B-12;Cavitação;;mecânica;2 h\n")
    doc_id = ingest.registrar_arquivo(_arquivo(tmp_path, "hist.csv", texto), "hist.csv", tipo="ocorrencias")
    ingest.processar_documento(doc_id)
    with db.session() as conn:
        o = painel.ocorrencias(conn, None, None)
        lac = painel.lacunas(conn)
    assert o["total"] == 3
    assert o["parada_total_min"] == 240.0 and o["mttr_min"] == 80.0
    pic = next(e for e in o["pareto_n"] if e["equipamento"] == "Picador 2")
    assert pic["n"] == 2 and pic["mtbf_h"] == 240.0
    assert o["repetidas"] == 1 and o["sem_causa_raiz"] == 1
    assert o["pareto_parada"][0]["equipamento"] in {"Picador 2", "Bomba B-12"}
    assert o["pareto_parada"][-1]["acumulado_pct"] == 100.0
    assert {l["equipamento"] for l in lac} == {"Picador 2", "Bomba B-12"}


def test_ferramenta_com_erro_nao_derruba():
    saida, ok, _ = agent.executar_ferramenta("ler_documento", {"documento_id": "abc"}, "t", 0)
    assert not ok and "erro" in saida
    saida, ok, _ = agent.executar_ferramenta("inexistente", {}, "t", 0)
    assert not ok


def test_painel_vazio():
    with db.session() as conn:
        p = painel.montar(conn, 90)
    assert p["base"]["documentos"] == 0
    assert p["ocorrencias"]["total"] == 0 and p["ocorrencias"]["mttr_min"] is None
    assert p["alarmes"]["total_eventos"] == 0
    assert p["uso"]["chamadas"] == 0
