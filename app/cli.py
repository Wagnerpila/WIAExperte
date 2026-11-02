"""Comandos de administração.

    python -m app.cli criar-usuario <nome> [--admin]
    python -m app.cli importar <pasta-ou-arquivo> [--tipo T] [--plataforma P] [--area A] [--equipamento E]
    python -m app.cli reprocessar-pendentes
    python -m app.cli apagar-duplicados
    python -m app.cli greendocs-buscar <termo> [--so-descricao]
    python -m app.cli greendocs-baixar <nome-do-documento> [...]
"""
from __future__ import annotations

import argparse
import getpass
from pathlib import Path

from . import auth, db, greendocs, ingest


def main() -> None:
    db.init_db()
    ap = argparse.ArgumentParser(prog="python -m app.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)

    cu = sub.add_parser("criar-usuario", help="cria ou troca a senha de um usuário")
    cu.add_argument("nome")
    cu.add_argument("--admin", action="store_true")

    im = sub.add_parser("importar", help="importa arquivos de uma pasta para a base")
    im.add_argument("caminho")
    im.add_argument("--tipo", default="auto", choices=["auto"] + ingest.TIPOS)
    im.add_argument("--plataforma")
    im.add_argument("--area")
    im.add_argument("--equipamento")
    im.add_argument("--usuario", default="importacao")

    sub.add_parser("reprocessar-pendentes", help="reprocessa documentos com erro ou pendentes")
    sub.add_parser("apagar-duplicados", help="apaga os documentos marcados como duplicado (o original fica)")
    gb = sub.add_parser("greendocs-buscar", help="testa a busca no GreenDocs")
    gb.add_argument("termo")
    gb.add_argument("--so-descricao", action="store_true", help="não procurar dentro dos arquivos")
    gd = sub.add_parser("greendocs-baixar", help="baixa documentos do GreenDocs para a base (após uma busca)")
    gd.add_argument("nomes", nargs="+")

    a = ap.parse_args()
    if a.cmd == "criar-usuario":
        senha = getpass.getpass("Senha (mín. 8 caracteres): ")
        if senha != getpass.getpass("Repita a senha: "):
            raise SystemExit("as senhas não conferem")
        auth.criar_usuario(a.nome, senha, "admin" if a.admin else "tecnico")
        print(f"usuário {a.nome.lower()} salvo")
    elif a.cmd == "importar":
        raiz = Path(a.caminho)
        arquivos = [raiz] if raiz.is_file() else sorted(p for p in raiz.rglob("*") if p.is_file())
        for p in arquivos:
            doc_id = ingest.registrar_arquivo(p, p.name, tipo=a.tipo, enviado_por=a.usuario,
                                              plataforma=a.plataforma, area=a.area, equipamento=a.equipamento)
            ingest.processar_documento(doc_id)
            with db.session() as conn:
                d = conn.execute("SELECT status, n_trechos, COALESCE(erro, observacao, '') m FROM documentos"
                                 " WHERE id=?", (doc_id,)).fetchone()
            print(f"#{doc_id} {p.name}: {d['status']} ({d['n_trechos']} trechos) {d['m']}")
    elif a.cmd == "reprocessar-pendentes":
        with db.session() as conn:
            ids = [r[0] for r in conn.execute(
                "SELECT id FROM documentos WHERE status IN ('erro','pendente','processando')")]
        for i in ids:
            ingest.processar_documento(i)
            print(f"#{i} reprocessado")
    elif a.cmd == "greendocs-buscar":
        r = greendocs.buscar(a.termo, no_conteudo=not a.so_descricao)
        with db.session() as conn:
            greendocs.situacao_na_base(conn, r["resultados"])
        print(f"{r['total']} resultado(s) para {r['termo']!r}")
        for x in r["resultados"]:
            print(f"  {x['nome']}  rev {x['revisao']}  [{x['na_base']}]  {(x['descricao'] or '')[:90]}")
    elif a.cmd == "greendocs-baixar":
        for x in greendocs.baixar_para_base(a.nomes, "cli"):
            if x.get("erro"):
                print(f"{x['nome']}: ERRO {x['erro']}")
                continue
            for d in x["documentos"]:
                print(f"{x['nome']} rev {x['revisao']}: #{d['id']} {d['nome']} {d['status']} ({d['n_trechos']} trechos)")
    elif a.cmd == "apagar-duplicados":
        r = ingest.apagar_duplicados()
        print(f"{r['apagados']} duplicado(s) apagado(s), {r['bytes'] / 1e6:.1f} MB liberados")


if __name__ == "__main__":
    main()
