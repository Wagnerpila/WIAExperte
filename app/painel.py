"""Indicadores do painel: base de conhecimento, ocorrências (MTTR/MTBF/Pareto), alarmes (ISA-18.2) e uso."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta

from . import config

# Referências de desempenho de alarmes (EEMUA 191 / ISA-18.2 / IEC 62682, citadas em fontes secundárias).
# São orientativas: a filosofia de alarmes da planta define os limites oficiais.
REF_ALARMES = {
    "media_10min": {"bom": 1.0, "maximo": 2.0, "texto": "≈1 alarme por 10 min por operador é aceitável; >2 é sobrecarga"},
    "pico_10min": {"maximo": 10, "texto": ">10 alarmes em 10 min caracteriza inundação (flood)"},
    "pct_flood": {"maximo": 1.0, "texto": "<1% do tempo em inundação"},
    "standing": {"maximo": 5, "texto": "<5 alarmes ativos há mais de 24 h (IEC 62682); EEMUA cita <10"},
    "top10_pct": {"maximo": 20.0, "texto": "os 10 alarmes mais frequentes devem somar menos de ~20% do total"},
}


def _desde(dias: int | None) -> str | None:
    if not dias:
        return None
    return (datetime.now() - timedelta(days=dias)).strftime("%Y-%m-%dT%H:%M:%S")


def _dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _semana(s: str) -> str:
    d = _dt(s)
    if not d:
        return "?"
    seg = d - timedelta(days=d.weekday())
    return seg.strftime("%Y-%m-%d")


def base(conn, desde: str | None) -> dict:
    por_tipo = [dict(r) for r in conn.execute(
        "SELECT tipo, COUNT(*) n, COALESCE(SUM(tamanho),0) bytes FROM documentos"
        " WHERE tipo NOT IN ('nota','ocorrencia') GROUP BY tipo ORDER BY n DESC")]
    por_status = [dict(r) for r in conn.execute(
        "SELECT status, COUNT(*) n FROM documentos WHERE tipo NOT IN ('nota','ocorrencia')"
        " GROUP BY status ORDER BY n DESC")]
    por_plataforma = [dict(r) for r in conn.execute(
        "SELECT COALESCE(NULLIF(plataforma,''),'(não informada)') plataforma, COUNT(*) n FROM documentos"
        " WHERE tipo NOT IN ('nota','ocorrencia') GROUP BY 1 ORDER BY n DESC")]
    por_area = [dict(r) for r in conn.execute(
        "SELECT COALESCE(NULLIF(area,''),'(não informada)') area, COUNT(*) n FROM documentos"
        " WHERE tipo NOT IN ('nota','ocorrencia') GROUP BY 1 ORDER BY n DESC LIMIT 15")]
    envios = defaultdict(int)
    for r in conn.execute("SELECT enviado_em FROM documentos WHERE tipo NOT IN ('nota','ocorrencia')"
                          + (" AND enviado_em >= ?" if desde else ""), ([desde] if desde else [])):
        envios[_semana(r[0])] += 1
    pendencias = [dict(r) for r in conn.execute(
        "SELECT id, nome, tipo, status, COALESCE(erro, observacao) motivo, enviado_em FROM documentos"
        " WHERE status IN ('erro','pendente','sem_texto','nao_suportado','binario','processando')"
        " ORDER BY enviado_em DESC LIMIT 50")]
    recentes = [dict(r) for r in conn.execute(
        "SELECT id, nome, tipo, status, plataforma, area, equipamento, enviado_por, enviado_em, n_trechos,"
        " observacao FROM documentos WHERE tipo NOT IN ('nota','ocorrencia') ORDER BY id DESC LIMIT 15")]
    tot = conn.execute("SELECT COUNT(*), COALESCE(SUM(tamanho),0) FROM documentos"
                       " WHERE tipo NOT IN ('nota','ocorrencia')").fetchone()
    return {
        "documentos": tot[0], "bytes": tot[1],
        "trechos": conn.execute("SELECT COUNT(*) FROM trechos t JOIN documentos d ON d.id = t.doc_id"
                                " WHERE d.tipo NOT IN ('nota','ocorrencia')").fetchone()[0],
        "notas": conn.execute("SELECT COUNT(*) FROM notas").fetchone()[0],
        "glossario": conn.execute("SELECT COUNT(*) FROM glossario").fetchone()[0],
        "mensagens_whatsapp_trechos": conn.execute(
            "SELECT COUNT(*) FROM trechos t JOIN documentos d ON d.id=t.doc_id WHERE d.tipo='whatsapp'").fetchone()[0],
        "por_tipo": por_tipo, "por_status": por_status, "por_plataforma": por_plataforma, "por_area": por_area,
        "envios_semana": [{"semana": k, "n": v} for k, v in sorted(envios.items())],
        "pendencias": pendencias, "recentes": recentes,
    }


def ocorrencias(conn, desde: str | None, dias: int | None) -> dict:
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM ocorrencias" + (" WHERE data_hora >= ?" if desde else "") + " ORDER BY data_hora",
        ([desde] if desde else []))]
    n = len(rows)
    paradas = [r["tempo_parada_min"] for r in rows if r["tempo_parada_min"] is not None]
    total_parada = sum(paradas)

    por_eq: dict[str, dict] = {}
    for r in rows:
        e = por_eq.setdefault(r["equipamento"], {"equipamento": r["equipamento"], "n": 0, "parada_min": 0.0,
                                                 "datas": [], "area": r["area"], "plataforma": r["plataforma"]})
        e["n"] += 1
        e["parada_min"] += r["tempo_parada_min"] or 0
        e["datas"].append(r["data_hora"])

    # Período observado: janela escolhida, ou do primeiro registro até hoje.
    if dias:
        periodo_h = dias * 24.0
    else:
        primeiro = _dt(rows[0]["data_hora"]) if rows else None
        periodo_h = max((datetime.now() - primeiro).total_seconds() / 3600, 24.0) if primeiro else 0

    lista_eq = []
    for e in por_eq.values():
        ds = sorted(d for d in (_dt(x) for x in e.pop("datas")) if d)
        intervalos = [(b - a).total_seconds() / 3600 for a, b in zip(ds, ds[1:])]
        e["mtbf_h"] = round(sum(intervalos) / len(intervalos), 1) if intervalos else None
        e["mttr_min"] = round(e["parada_min"] / e["n"], 1) if e["parada_min"] else None
        e["ultima"] = ds[-1].isoformat() if ds else None
        lista_eq.append(e)
    pareto_n = sorted(lista_eq, key=lambda e: (-e["n"], -e["parada_min"]))[:15]
    pareto_parada = sorted([e for e in lista_eq if e["parada_min"]], key=lambda e: -e["parada_min"])[:15]
    acum, tot_par = 0.0, total_parada or 1
    for e in pareto_parada:
        acum += e["parada_min"]
        e["acumulado_pct"] = round(100 * acum / tot_par, 1)

    def contar(campo: str) -> list[dict]:
        c = Counter((r[campo] or "(não informado)") for r in rows)
        p = defaultdict(float)
        for r in rows:
            p[r[campo] or "(não informado)"] += r["tempo_parada_min"] or 0
        return [{"nome": k, "n": v, "parada_min": round(p[k], 1)} for k, v in c.most_common(15)]

    mensal = defaultdict(lambda: {"n": 0, "parada_min": 0.0})
    for r in rows:
        k = (r["data_hora"] or "?")[:7]
        mensal[k]["n"] += 1
        mensal[k]["parada_min"] += r["tempo_parada_min"] or 0

    repetidas = [e for e in sorted(lista_eq, key=lambda e: -e["n"]) if e["n"] >= 2]
    sem_causa = sum(1 for r in rows if not r["causa_raiz"])
    return {
        "total": n,
        "parada_total_min": round(total_parada, 1),
        "mttr_min": round(total_parada / len(paradas), 1) if paradas else None,
        "mtbf_planta_h": round(periodo_h / n, 1) if n else None,
        "equipamentos_afetados": len(por_eq),
        "repetidas": len(repetidas),
        "sem_causa_raiz": sem_causa,
        "pct_com_causa": round(100 * (n - sem_causa) / n, 1) if n else None,
        "pareto_n": pareto_n, "pareto_parada": pareto_parada,
        "por_area": contar("area"), "por_plataforma": contar("plataforma"), "por_categoria": contar("categoria_causa"),
        "mensal": [{"mes": k, "n": v["n"], "parada_min": round(v["parada_min"], 1)} for k, v in sorted(mensal.items())],
        "equipamentos_repetidos": repetidas[:20],
        "recentes": list(reversed(rows[-15:])),
    }


def alarmes(conn, desde: str | None) -> dict:
    rows = conn.execute("SELECT ts, tag, prioridade, estado, mensagem FROM alarmes"
                        + (" WHERE ts >= ?" if desde else "") + " ORDER BY ts", ([desde] if desde else [])).fetchall()
    total_eventos = len(rows)
    if not rows:
        return {"total_eventos": 0, "referencias": REF_ALARMES}
    tem_estado = any(r["estado"] for r in rows)
    # Alarmes anunciados = transições para ativo (ou todos os eventos se não houver coluna de estado).
    anunciados = [r for r in rows if (r["estado"] == "ativo") or not tem_estado]
    tempos = [(_dt(r["ts"]), r) for r in anunciados]
    tempos = [(t, r) for t, r in tempos if t]
    if not tempos:
        return {"total_eventos": total_eventos, "anunciados": 0, "referencias": REF_ALARMES}
    ini, fim = tempos[0][0], tempos[-1][0]
    n_janelas = max(1, int((fim - ini).total_seconds() // 600) + 1)
    janelas = Counter(int((t - ini).total_seconds() // 600) for t, _ in tempos)
    floods = sum(1 for v in janelas.values() if v > 10)
    por_tag = Counter(r["tag"] for _, r in tempos)
    top10 = por_tag.most_common(10)

    # Chattering: 3 ou mais ativações da mesma tag em 60 s.
    por_tag_t = defaultdict(list)
    for t, r in tempos:
        por_tag_t[r["tag"]].append(t)
    chattering = []
    for tag, ts in por_tag_t.items():
        rajadas = sum(1 for i in range(len(ts) - 2) if (ts[i + 2] - ts[i]).total_seconds() <= 60)
        if rajadas:
            chattering.append({"tag": tag, "rajadas": rajadas, "ativacoes": len(ts)})
    chattering.sort(key=lambda x: -x["rajadas"])

    # Standing: último estado da tag é ativo e já dura mais de 24 h em relação ao fim dos dados.
    standing = []
    if tem_estado:
        ultimo = {}
        for r in rows:
            ultimo[r["tag"]] = r
        for tag, r in ultimo.items():
            t = _dt(r["ts"])
            if r["estado"] == "ativo" and t and (fim - t).total_seconds() > 86400:
                standing.append({"tag": tag, "desde": r["ts"], "mensagem": r["mensagem"]})

    diario = Counter(t.strftime("%Y-%m-%d") for t, _ in tempos)
    prioridade = Counter((r["prioridade"] or "(sem prioridade)") for _, r in tempos)
    return {
        "total_eventos": total_eventos,
        "anunciados": len(tempos),
        "tem_estado": tem_estado,
        "inicio": ini.isoformat(), "fim": fim.isoformat(),
        "media_10min": round(len(tempos) / n_janelas, 2),
        "pico_10min": max(janelas.values()),
        "pct_flood": round(100 * floods / n_janelas, 2),
        "janelas_flood": floods,
        "top10": [{"tag": k, "n": v} for k, v in top10],
        "top10_pct": round(100 * sum(v for _, v in top10) / len(tempos), 1),
        "chattering": chattering[:15],
        "standing": standing[:30],
        "n_standing": len(standing),
        "diario": [{"dia": k, "n": v} for k, v in sorted(diario.items())],
        "por_prioridade": [{"nome": k, "n": v} for k, v in prioridade.most_common()],
        "referencias": REF_ALARMES,
    }


def uso(conn, desde: str | None) -> dict:
    desde_utc = desde  # uso_api guarda UTC; diferença de fuso é irrelevante para a janela em dias
    p = [desde_utc] if desde_utc else []
    w = " WHERE ts >= ?" if desde_utc else ""
    agg = conn.execute("SELECT COUNT(*) chamadas, COALESCE(SUM(input_tokens),0) inp, COALESCE(SUM(output_tokens),0) outp,"
                       " COALESCE(SUM(cache_read),0) cr, COALESCE(SUM(cache_write),0) cw, COALESCE(AVG(segundos),0) seg"
                       f" FROM uso_api{w}", p).fetchone()

    def custo(i, o, cr, cw):
        return (i * config.PRICE_INPUT + o * config.PRICE_OUTPUT + cr * config.PRICE_CACHE_READ
                + cw * config.PRICE_CACHE_WRITE) / 1_000_000

    diario = [dict(r) | {"custo_usd": round(custo(r["inp"], r["outp"], r["cr"], r["cw"]), 4)} for r in conn.execute(
        "SELECT substr(ts,1,10) dia, COUNT(*) chamadas, SUM(input_tokens) inp, SUM(output_tokens) outp,"
        f" SUM(cache_read) cr, SUM(cache_write) cw FROM uso_api{w} GROUP BY dia ORDER BY dia", p)]
    ferr = [dict(r) for r in conn.execute(
        f"SELECT nome, COUNT(*) n, SUM(CASE WHEN ok=0 THEN 1 ELSE 0 END) erros FROM ferramentas_log{w}"
        " GROUP BY nome ORDER BY n DESC", p)]
    buscas_vazias = conn.execute(
        f"SELECT COUNT(*) FROM ferramentas_log{w}{' AND' if w else ' WHERE'} nome='buscar_base' AND n_resultados=0",
        p).fetchone()[0]
    perguntas = conn.execute(
        "SELECT COUNT(*) FROM mensagens_tela" + (" WHERE criada_em >= ?" if desde_utc else ""), p).fetchone()[0]
    conversas = conn.execute(
        "SELECT COUNT(*) FROM conversas" + (" WHERE atualizada_em >= ?" if desde_utc else ""), p).fetchone()[0]
    por_finalidade = [dict(r) for r in conn.execute(
        f"SELECT finalidade, COUNT(*) n FROM uso_api{w} GROUP BY finalidade", p)]
    entrada_total = agg["inp"] + agg["cr"] + agg["cw"]
    return {
        "chamadas": agg["chamadas"], "respostas": perguntas, "conversas": conversas,
        "tokens_entrada": agg["inp"], "tokens_saida": agg["outp"], "cache_leitura": agg["cr"], "cache_escrita": agg["cw"],
        "pct_cache": round(100 * agg["cr"] / entrada_total, 1) if entrada_total else None,
        "custo_usd": round(custo(agg["inp"], agg["outp"], agg["cr"], agg["cw"]), 2),
        "tempo_medio_s": round(agg["seg"], 1),
        "diario": diario, "ferramentas": ferr, "buscas_sem_resultado": buscas_vazias,
        "por_finalidade": por_finalidade, "modelo": config.MODEL,
    }


def lacunas(conn) -> list[dict]:
    """Equipamentos com ocorrência registrada mas sem backup ou documentação na base."""
    out = []
    for r in conn.execute("SELECT equipamento, COUNT(*) n FROM ocorrencias GROUP BY equipamento ORDER BY n DESC LIMIT 40"):
        eq = r["equipamento"]
        like = f"%{eq}%"
        tem = conn.execute(
            "SELECT"
            " SUM(CASE WHEN tipo='backup' THEN 1 ELSE 0 END) backups,"
            " SUM(CASE WHEN tipo='documentacao' THEN 1 ELSE 0 END) docs,"
            " SUM(CASE WHEN tipo IN ('log','alarmes') THEN 1 ELSE 0 END) logs"
            " FROM documentos WHERE (equipamento LIKE ? OR nome LIKE ?) AND tipo NOT IN ('nota','ocorrencia')",
            (like, like)).fetchone()
        faltando = [nome for nome, v in (("backup", tem["backups"]), ("documentação", tem["docs"]),
                                         ("logs", tem["logs"])) if not v]
        if faltando:
            out.append({"equipamento": eq, "ocorrencias": r["n"], "faltando": faltando})
    return out


def montar(conn, dias: int | None) -> dict:
    desde = _desde(dias)
    return {
        "gerado_em": datetime.now().isoformat(timespec="seconds"),
        "dias": dias,
        "base": base(conn, desde),
        "ocorrencias": ocorrencias(conn, desde, dias),
        "alarmes": alarmes(conn, desde),
        "uso": uso(conn, desde),
        "lacunas": lacunas(conn),
    }
