"""Integração sob demanda com o GreenDocs (gestão de documentos técnicos da Klabin).

Nada é baixado em massa: o assistente pesquisa no GreenDocs quando a base não tem a resposta
(`buscar`) e baixa só os documentos escolhidos (`baixar`), que entram na base pela ingestão normal.

O GreenDocs é um app web (React + SignalR) sem API pública de busca, então usamos um Chromium
automatizado (Playwright). A sessão fica salva em /data para não fazer login a cada pergunta.
Credenciais: WIA_GREENDOCS_EMAIL / WIA_GREENDOCS_SENHA no Ambiente (nunca no código).
"""
from __future__ import annotations

import logging
import os
import re
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote

from . import config, db

log = logging.getLogger("wia.greendocs")

_LOCK = threading.Lock()  # um navegador por vez (o servidor tem um worker só)
TIMEOUT_BUSCA_MS = 120_000  # a busca no conteúdo dos arquivos leva ~20 s
TIMEOUT_DOWNLOAD_MS = 15 * 60_000
MAX_RESULTADOS = 25


class GreenDocsErro(RuntimeError):
    pass


def habilitado() -> bool:
    return bool(config.GREENDOCS_EMAIL and config.GREENDOCS_SENHA)


def _estado() -> Path:
    return config.DATA_DIR / "greendocs_sessao.json"


# A busca terminou quando o indicador de carregamento some de dentro da tabela e aparecem linhas
# ou "Nenhum item por aqui". Só vale depois de ver o carregamento (ou 10 s sem ele): logo ao abrir,
# a página às vezes mostra o "nenhum item" de antes de a busca começar.
JS_BUSCA_TERMINOU = r"""
() => {
  const w = window, agora = performance.now();
  if (!w.__gdInicio) w.__gdInicio = agora;
  const carregando = !!document.querySelector('[class*=FlatTable] [role=progressbar]');
  if (carregando) { w.__gdViuCarregar = true; return false; }
  if (!w.__gdViuCarregar && agora - w.__gdInicio < 10000) return false;
  const linhas = document.querySelectorAll('[class*=FlatTableContentColumnContainer] a[href*="/app/item/"]').length;
  return linhas > 0 || /Nenhum item por aqui/.test(document.body.innerText);
}
"""

# Lê a tabela de resultados. O GreenDocs monta a tabela por colunas: a 1ª coluna é a caixa de
# seleção e as seguintes seguem a ordem dos cabeçalhos ("Nome", "Revisão", "Descrição"...).
JS_TABELA = r"""
() => {
  const cont = document.querySelector('[class*=FlatTableContainer]');
  if (!cont) return {cabecalhos: [], linhas: []};
  const cab = [...cont.querySelectorAll('[class*=FlatTableHeader] [class*=Cell], [class*=HeaderCell]')]
      .map(h => h.innerText.trim()).filter(Boolean);
  const cols = [...cont.querySelectorAll('[class*=FlatTableContentColumnContainer]')].slice(-cab.length);
  const n = Math.max(0, ...cols.map(c => c.children.length));
  const linhas = [];
  for (let i = 0; i < n; i++) {
    const l = {};
    cols.forEach((c, k) => {
      const cel = c.children[i];
      if (!cel) return;
      l[cab[k]] = cel.innerText.trim();
      const a = cel.querySelector('a[href*="/app/item/"]');
      if (a) l._link = a.getAttribute('href');
    });
    linhas.push(l);
  }
  const tot = document.body.innerText.match(/de\s*(\d+)\s*itens totais/);
  return {cabecalhos: cab, linhas, total: tot ? +tot[1] : linhas.length};
}
"""


def interpretar_tabela(dados: dict) -> list[dict]:
    """Converte a tabela lida do navegador em resultados {nome, revisao, descricao, link, ...}."""
    out = []
    for linha in dados.get("linhas", []):
        nome = (linha.get("Nome") or "").strip()
        link = linha.get("_link") or ""
        if not nome or not link.startswith("/app/item/"):
            continue
        out.append({
            "nome": nome,
            "revisao": (linha.get("Revisão") or linha.get("Rev") or "").strip() or None,
            "descricao": (linha.get("Descrição") or "").strip() or None,
            "tipo_item": (linha.get("Tipo de Item") or "").strip() or None,
            "situacao": (linha.get("Atividade") or "").strip() or None,
            "link": link.split("&_p=")[0],
        })
    return out


@contextmanager
def _pagina():
    if not habilitado():
        raise GreenDocsErro("integração com o GreenDocs não configurada (WIA_GREENDOCS_EMAIL/SENHA)")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise GreenDocsErro("Playwright não instalado neste servidor") from e
    with _LOCK, sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        estado = _estado()
        ctx = browser.new_context(storage_state=str(estado) if estado.is_file() else None,
                                  accept_downloads=True, locale="pt-BR", viewport={"width": 1600, "height": 900})
        ctx.set_default_timeout(45_000)
        page = ctx.new_page()
        try:
            yield page
        finally:
            try:
                estado.parent.mkdir(parents=True, exist_ok=True)
                ctx.storage_state(path=str(estado))
                os.chmod(estado, 0o600)
            except Exception:  # salvar a sessão é só otimização
                log.warning("não consegui salvar a sessão do GreenDocs", exc_info=True)
            ctx.close()
            browser.close()


def _login(page) -> None:
    log.info("GreenDocs: fazendo login")
    page.goto(f"{config.GREENDOCS_URL}/Account/LogOn", wait_until="domcontentloaded")
    page.fill("#email", config.GREENDOCS_EMAIL)
    page.fill("#password", config.GREENDOCS_SENHA)
    page.click("#btnLogin")
    try:
        page.wait_for_url(lambda u: "/Account/LogOn" not in u, timeout=45_000)
    except Exception as e:
        raise GreenDocsErro("login no GreenDocs recusado (confira e-mail e senha no Ambiente)") from e


def _abrir(page, caminho: str) -> None:
    url = config.GREENDOCS_URL + caminho
    page.goto(url, wait_until="domcontentloaded")
    if "/Account/LogOn" in page.url:
        _login(page)
        page.goto(url, wait_until="domcontentloaded")
        if "/Account/LogOn" in page.url:
            raise GreenDocsErro("o GreenDocs voltou para a tela de login depois de entrar")


def buscar(termo: str, no_conteudo: bool = True) -> dict:
    """Pesquisa no GreenDocs (ambiente PUMA). `no_conteudo` procura também dentro dos arquivos
    (tags, códigos, textos), que é o que acha documentos por uma tag; sem ele, só descrição."""
    termo = re.sub(r"[/\\?#%]+", " ", termo or "").strip()
    if not termo:
        raise GreenDocsErro("termo de busca vazio")
    caminho = (f"/app/search/{quote(termo, safe='')}?withComments=false"
               f"&withContent={'true' if no_conteudo else 'false'}&_p={config.GREENDOCS_PROJETO}")
    with _pagina() as page:
        _abrir(page, caminho)
        page.wait_for_function(JS_BUSCA_TERMINOU, timeout=TIMEOUT_BUSCA_MS, polling=1000)
        dados = page.evaluate(JS_TABELA)
    res = interpretar_tabela(dados)[:MAX_RESULTADOS]
    _guardar_vistos(res)
    return {"termo": termo, "no_conteudo": no_conteudo, "total": dados.get("total", len(res)), "resultados": res}


def _guardar_vistos(res: list[dict]) -> None:
    with db.session() as conn:
        for r in res:
            conn.execute(
                "INSERT INTO greendocs_itens (nome, revisao, descricao, link, visto_em) VALUES (?,?,?,?,?)"
                " ON CONFLICT(nome) DO UPDATE SET revisao=excluded.revisao, descricao=excluded.descricao,"
                " link=excluded.link, visto_em=excluded.visto_em",
                (r["nome"], r["revisao"], r["descricao"], r["link"], db.agora()))


def _nome_seguro(nome: str) -> str:
    return re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", Path(nome).name).strip(" .") or "arquivo"


def baixar(link: str, destino: Path) -> dict:
    """Abre o documento e baixa todos os arquivos da revisão atual (botão Download do painel Arquivos)."""
    if not re.fullmatch(r"/app/item/\d+(\?[\w=&%-]*)?", link or ""):
        raise GreenDocsErro(f"link de documento inválido: {link!r}")
    destino.mkdir(parents=True, exist_ok=True)
    arquivos: list[Path] = []
    with _pagina() as page:
        sep = "&" if "?" in link else "?"
        _abrir(page, f"{link}{sep}_p={config.GREENDOCS_PROJETO}")
        page.get_by_role("button", name=re.compile(r"^arquivos$", re.I)).first.click()
        botoes = page.get_by_role("button", name=re.compile(r"^download$", re.I))
        try:
            botoes.first.wait_for(state="visible", timeout=30_000)
        except Exception as e:
            raise GreenDocsErro("documento sem arquivo para download (ou sem permissão)") from e
        texto = page.inner_text("body")
        m = re.search(r"REVIS[ÃA]O\s+(\S+)", texto)
        for i in range(botoes.count()):
            with page.expect_download(timeout=TIMEOUT_DOWNLOAD_MS) as info:
                botoes.nth(i).click()
            dl = info.value
            alvo = destino / f"{i:02d}_{_nome_seguro(dl.suggested_filename)}"
            dl.save_as(str(alvo))
            arquivos.append(alvo)
    return {"revisao": m.group(1) if m else None, "arquivos": arquivos}


def baixar_para_base(nomes: list[str], usuario: str) -> list[dict]:
    """Baixa documentos já vistos numa busca e coloca na base (processamento na hora)."""
    from . import ingest

    with db.session() as conn:
        itens = {r["nome"]: dict(r) for r in conn.execute(
            f"SELECT * FROM greendocs_itens WHERE nome IN ({','.join('?' * len(nomes))})", nomes)}
    saida = []
    for nome in nomes:
        item = itens.get(nome)
        if not item:
            saida.append({"nome": nome, "erro": "documento não está nos resultados de uma busca recente; "
                                                  "use buscar_greendocs antes"})
            continue
        try:
            with tempfile.TemporaryDirectory(prefix="wia_gd_", dir=config.DATA_DIR) as tmp:
                r = baixar(item["link"], Path(tmp))
                ids = []
                for arq in r["arquivos"]:
                    nome_arq = arq.name.split("_", 1)[1]
                    if arq.stat().st_size > config.MAX_UPLOAD_MB * 1024 * 1024:
                        raise GreenDocsErro(f"{nome_arq} maior que {config.MAX_UPLOAD_MB} MB")
                    obs = f"GreenDocs: {nome} rev {r['revisao'] or item['revisao'] or '?'}"
                    if item.get("descricao"):
                        obs += f" — {item['descricao'][:300]}"
                    doc_id = ingest.registrar_arquivo(arq, nome_arq, enviado_por=f"greendocs/{usuario}",
                                                      observacao=obs, mover=True)
                    ingest.processar_documento(doc_id)
                    ids.append(doc_id)
            with db.session() as conn:
                conn.execute("UPDATE greendocs_itens SET revisao_baixada = ?, doc_ids = ?, baixado_em = ?"
                             " WHERE nome = ?", (r["revisao"] or item["revisao"], ",".join(map(str, ids)),
                                                 db.agora(), nome))
                docs = [dict(d) for d in conn.execute(
                    f"SELECT id, nome, status, n_trechos, observacao FROM documentos WHERE id IN "
                    f"({','.join('?' * len(ids))}) OR pai_id IN ({','.join('?' * len(ids))})", ids + ids)] if ids else []
            saida.append({"nome": nome, "revisao": r["revisao"] or item["revisao"], "documentos": docs})
        except Exception as e:
            log.exception("GreenDocs: falha ao baixar %s", nome)
            saida.append({"nome": nome, "erro": f"{type(e).__name__}: {e}"[:300]})
    return saida


def situacao_na_base(conn, resultados: list[dict]) -> None:
    """Marca em cada resultado se o documento (e qual revisão) já está na base."""
    for r in resultados:
        row = conn.execute("SELECT revisao_baixada FROM greendocs_itens WHERE nome = ? AND doc_ids IS NOT NULL",
                           (r["nome"],)).fetchone()
        if row:
            r["na_base"] = "sim" if row[0] == r["revisao"] else f"sim, revisão {row[0]} (GreenDocs tem {r['revisao']})"
            continue
        tem = conn.execute("SELECT 1 FROM documentos WHERE nome LIKE ? AND status IN ('ok','duplicado') LIMIT 1",
                           (r["nome"] + "%",)).fetchone()
        r["na_base"] = "sim (enviado manualmente; revisão desconhecida)" if tem else "não"
