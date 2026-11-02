"""Integração GreenDocs: leitura da tabela de resultados e ferramentas do chat (navegador simulado)."""
import json
from pathlib import Path

import pytest

from app import agent, config, db, greendocs

# Imita a estrutura do GreenDocs (tabela montada por colunas; a 1ª é a caixa de seleção). Dados fictícios.
HTML_RESULTADOS = """
<html><body><div class="styles__FlatTableContainer-sc-1 x">
  <div class="styles__FlatTableHeader-sc-2">
    <div class="HeaderCell">Nome</div><div class="HeaderCell">Revisão</div>
    <div class="HeaderCell">Descrição</div><div class="HeaderCell">Tipo de Item</div>
    <div class="HeaderCell">Atividade</div>
  </div>
  <div class="styles__FlatTableContentContainer-sc-3">
    <div class="styles__FlatTableContentColumnContainer-sc-4"><div><input type=checkbox></div><div><input type=checkbox></div></div>
    <div class="styles__FlatTableContentColumnContainer-sc-4">
      <div><span><a href="/app/item/800841?instanceId=1&revisionId=2&_p=14"><span>34231-I-LIO-00117-0001</span></a></span></div>
      <div><span><a href="/app/item/800843"><span>34231-I-LIO-00118-0001</span></a></span></div>
    </div>
    <div class="styles__FlatTableContentColumnContainer-sc-4"><div>a</div><div>b</div></div>
    <div class="styles__FlatTableContentColumnContainer-sc-4"><div>MEASUREMENT POINT LIST</div><div>SIGNAL CONNECTION LIST</div></div>
    <div class="styles__FlatTableContentColumnContainer-sc-4"><div>Documentos Técnicos</div><div>Documentos Técnicos</div></div>
    <div class="styles__FlatTableContentColumnContainer-sc-4"><div>Finalizado</div><div>Finalizado</div></div>
  </div>
</div>Mostrando 2 de 2 itens totais</body></html>
"""


def test_interpretar_tabela_no_navegador():
    pw = pytest.importorskip("playwright.sync_api")
    try:
        with pw.sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.set_content(HTML_RESULTADOS)
            dados = page.evaluate(greendocs.JS_TABELA)
            browser.close()
    except Exception as e:  # navegador não instalado nesta máquina
        pytest.skip(f"Chromium do Playwright indisponível: {e}")
    assert dados["total"] == 2
    res = greendocs.interpretar_tabela(dados)
    assert res == [
        {"nome": "34231-I-LIO-00117-0001", "revisao": "a", "descricao": "MEASUREMENT POINT LIST",
         "tipo_item": "Documentos Técnicos", "situacao": "Finalizado",
         "link": "/app/item/800841?instanceId=1&revisionId=2"},
        {"nome": "34231-I-LIO-00118-0001", "revisao": "b", "descricao": "SIGNAL CONNECTION LIST",
         "tipo_item": "Documentos Técnicos", "situacao": "Finalizado", "link": "/app/item/800843"},
    ]


def test_ferramentas_so_aparecem_com_credenciais(monkeypatch):
    monkeypatch.setattr(config, "GREENDOCS_EMAIL", "")
    assert "buscar_greendocs" not in {t["name"] for t in agent.tools_disponiveis()}
    monkeypatch.setattr(config, "GREENDOCS_EMAIL", "robo@exemplo.com")
    monkeypatch.setattr(config, "GREENDOCS_SENHA", "x")
    assert {"buscar_greendocs", "baixar_greendocs"} <= {t["name"] for t in agent.tools_disponiveis()}


def test_link_invalido_recusado(tmp_path):
    with pytest.raises(greendocs.GreenDocsErro):
        greendocs.baixar("https://outro-site.com/app/item/1", tmp_path)


def test_buscar_e_baixar_pelo_chat(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "GREENDOCS_EMAIL", "robo@exemplo.com")
    monkeypatch.setattr(config, "GREENDOCS_SENHA", "x")

    class PaginaFalsa:
        def __init__(self):
            self.url = ""

        def goto(self, url, **kw):
            self.url = url

        def wait_for_function(self, *a, **kw):
            pass

        def evaluate(self, js):
            return {"total": 1, "linhas": [{"Nome": "34231-I-LIO-00117-0001", "Revisão": "a",
                                            "Descrição": "MEASUREMENT POINT LIST",
                                            "_link": "/app/item/800841?instanceId=1&revisionId=2&_p=14"}]}

    from contextlib import contextmanager

    @contextmanager
    def pagina_falsa():
        yield PaginaFalsa()

    monkeypatch.setattr(greendocs, "_pagina", pagina_falsa)
    saida, ok, resumo = agent.executar_ferramenta("buscar_greendocs", {"consulta": "34221RNF-30101-VT1"}, "t", 1)
    res = json.loads(saida)
    assert ok and res["resultados"][0]["nome"] == "34231-I-LIO-00117-0001"
    assert res["resultados"][0]["na_base"] == "não" and "GreenDocs" in resumo

    def baixar_falso(link, destino):
        assert link == "/app/item/800841?instanceId=1&revisionId=2"
        destino.mkdir(parents=True, exist_ok=True)
        arq = destino / "00_34231-I-LIO-00117-0001.txt"
        arq.write_text("ponto 34221RNF-30101-VT1 caixa +34231JB1", encoding="utf-8")
        return {"revisao": "a", "arquivos": [arq]}

    monkeypatch.setattr(greendocs, "baixar", baixar_falso)
    saida, ok, resumo = agent.executar_ferramenta(
        "baixar_greendocs", {"documentos": ["34231-I-LIO-00117-0001", "nao-visto"]}, "tecnico1", 1)
    res = json.loads(saida)
    assert ok
    b = {x["nome"]: x for x in res["baixados"]}
    assert b["34231-I-LIO-00117-0001"]["documentos"][0]["status"] == "ok"
    assert "use buscar_greendocs antes" in b["nao-visto"]["erro"]
    with db.session() as conn:
        d = conn.execute("SELECT nome, enviado_por, observacao FROM documentos").fetchone()
        assert d["nome"] == "34231-I-LIO-00117-0001.txt" and d["enviado_por"] == "greendocs/tecnico1"
        assert d["observacao"].startswith("GreenDocs: 34231-I-LIO-00117-0001 rev a — MEASUREMENT POINT LIST")
        achados = agent.t_buscar_base(conn, {"consulta": "34221RNF-30101-VT1"}, "t")["resultados"]
        assert achados
        # nova busca: agora aparece como já na base, na mesma revisão
        r2 = {"nome": "34231-I-LIO-00117-0001", "revisao": "a"}
        greendocs.situacao_na_base(conn, [r2])
        assert r2["na_base"] == "sim"
