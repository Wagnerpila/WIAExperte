/* WIA Expert — dashboard de remotas e cards de endereçamento de I/O (aba Remotas e chat). */
(function () {
  "use strict";
  const { esc, num } = window.Graf;
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const ORDEM_TIPOS = ["DI", "DO", "AI", "AO", "RTD/TC"];
  const ctx = { api: null, dialogo: null, dados: null, filtro: { q: "", area: "", tipo: "", ocup: "" }, vista: "remotas" };

  const classeTipo = (t) => "tp-" + String(t || "x").replace(/[^A-Za-z]/g, "");
  const chip = (t, n) => `<span class="tp ${classeTipo(t)}">${esc(t || "?")}${n !== undefined ? ` <b>${num(n)}</b>` : ""}</span>`;
  const pct = (v) => (v === null || v === undefined) ? "–" : `${num(v * 100, 0)}%`;
  const nivelOcup = (v) => v === null || v === undefined ? "nd" : v >= 0.9 ? "ruim" : v >= 0.7 ? "atencao" : "bom";
  const ordenarTipos = (obj) => Object.keys(obj || {}).sort((a, b) => {
    const ia = ORDEM_TIPOS.indexOf(a), ib = ORDEM_TIPOS.indexOf(b);
    return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
  });
  const sn = (s) => String(s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();

  function barraOcup(v, rotulo) {
    const n = nivelOcup(v);
    return `<div class="ocup ${n}"><div class="ocup-rot"><span>${esc(rotulo || "Ocupação")}</span><span>${pct(v)}</span></div>
      <div class="ocup-trilho" role="img" aria-label="Ocupação ${pct(v)}"><i style="width:${v ? Math.min(100, Math.round(v * 100)) : 0}%"></i></div></div>`;
  }

  /* Barra empilhada por tipo (composição dos pontos). */
  function composicao(tipos, total) {
    const ks = ordenarTipos(tipos);
    if (!total) return "";
    return `<div class="comp" role="img" aria-label="${ks.map((k) => `${k} ${tipos[k]}`).join(", ")}">${ks.map((k) =>
      `<i class="${classeTipo(k)}" style="flex:${tipos[k]}" title="${esc(k)}: ${num(tipos[k])}"></i>`).join("")}</div>`;
  }

  // ------------------------------------------------------------------ dashboard
  async function carregar() {
    const box = $("#remotas-conteudo");
    box.innerHTML = `<div class="muted">Carregando…</div>`;
    try { ctx.dados = await ctx.api("/api/remotas"); } catch (e) { box.innerHTML = `<div class="cartao erro">${esc(e.message)}</div>`; return; }
    const d = ctx.dados;
    const sel = $("#rem-area"), atual = sel.value;
    sel.innerHTML = `<option value="">Todas as áreas</option>` + d.por_area.map((a) => `<option>${esc(a.area)}</option>`).join("");
    sel.value = atual;
    $("#rem-gerado").textContent = d.indexando ? "lendo planilhas da base…" : "";
    render();
  }

  function remotasFiltradas() {
    const f = ctx.filtro, q = sn(f.q).trim();
    return (ctx.dados.remotas || []).filter((r) => {
      if (f.area && r.area !== f.area && !(r.areas || []).includes(f.area)) return false;
      if (f.tipo && !(r.tipos || {})[f.tipo]) return false;
      if (f.ocup === "critica" && !(r.ocupacao >= 0.9)) return false;
      if (f.ocup === "reserva" && r.reserva_ok !== false) return false;
      if (f.ocup === "semcap" && r.ocupacao !== null) return false;
      if (q && !sn([r.id, r.area, r.sistema, r.grupo, r.rota, (r.fontes || []).join(" ")].join(" ")).replace(/[-+\s]/g, "").includes(q.replace(/[-+\s]/g, ""))) return false;
      return true;
    });
  }

  function tile(rot, val, extra = "", cls = "") {
    return `<div class="tile ${cls}"><div class="rot">${esc(rot)}</div><div class="val">${val}</div>${extra}</div>`;
  }

  function render() {
    const d = ctx.dados, k = d.kpis, box = $("#remotas-conteudo");
    if (!k.pontos && !k.remotas) {
      box.innerHTML = `<div class="cartao pilha"><h2>Nenhum endereçamento ainda</h2>
        <p class="muted">Importe o cadastro de remotas (arquivo <code>data.js</code> do projeto RemotasPuma) pelo botão acima,
        ou envie listas de I/O em planilha (LIO) na aba Base — elas são lidas automaticamente.</p></div>`;
      return;
    }
    const lista = remotasFiltradas();
    box.innerHTML = "";

    // ---- indicadores
    const ocupN = nivelOcup(k.ocupacao);
    const kp = document.createElement("div");
    kp.className = "tiles kpis-remotas";
    kp.innerHTML =
      tile("Remotas / gabinetes", num(k.remotas), `<div class="ref">${num(k.areas)} áreas</div>`) +
      tile("Pontos de I/O", num(k.pontos), `<div class="ref">${num(k.listas)} lista(s) de I/O da base + cadastro</div>`) +
      tile("Ocupação geral", pct(k.ocupacao), barraOcup(k.ocupacao, `${num(k.utilizados)} de ${num(k.instalados)} canais`), "kpi-" + ocupN) +
      tile("Canais livres", num(k.livres), `<div class="ref">nas remotas com capacidade cadastrada</div>`) +
      tile("Remotas ≥ 90%", num(k.criticas), `<button class="link-filtro" data-ocup="critica">ver</button>`, k.criticas ? "kpi-ruim" : "kpi-bom") +
      tile("Abaixo da reserva", num(k.sem_reserva), `<button class="link-filtro" data-ocup="reserva">ver</button>`, k.sem_reserva ? "kpi-atencao" : "kpi-bom");
    box.appendChild(kp);
    $$(".link-filtro", kp).forEach((b) => b.addEventListener("click", () => {
      ctx.filtro.ocup = b.dataset.ocup; $("#rem-ocup").value = b.dataset.ocup; trocarVista("remotas");
    }));

    // ---- gráficos
    const g = document.createElement("div");
    g.className = "graficos";
    box.appendChild(g);
    Graf.cartao(g, "Pontos por área", `${num(d.por_area.length)} áreas`, (el) => Graf.barrasH(el, d.por_area.slice(0, 14), {
      rotulo: (a) => a.area, valor: (a) => a.pontos,
      dica: (a) => `<strong>${esc(a.area)}</strong><br>${num(a.pontos)} pontos · ${num(a.remotas)} remotas` +
        (a.instalados ? `<br>ocupação ${pct(a.utilizados / a.instalados)}` : ""),
    }), [{ t: "Área", v: "area" }, { t: "Remotas", v: "remotas", num: true, casas: 0 }, { t: "Pontos", v: "pontos", num: true, casas: 0 }], d.por_area);

    const tot = d.por_tipo.reduce((s, t) => s + t.n, 0);
    const cartTipo = document.createElement("div");
    cartTipo.className = "grafico";
    cartTipo.innerHTML = `<header><h3>Pontos por tipo de sinal</h3><span class="small muted">${num(tot)} pontos</span></header>
      ${composicao(Object.fromEntries(d.por_tipo.map((t) => [t.tipo, t.n])), tot)}
      <div class="tipos-legenda">${d.por_tipo.map((t) => `<div>${chip(t.tipo)}<span class="tl-n">${num(t.n)}</span><span class="tl-p">${pct(t.n / tot)}</span></div>`).join("")}</div>`;
    g.appendChild(cartTipo);

    const cartOc = document.createElement("div");
    cartOc.className = "grafico";
    const maxF = Math.max(1, ...d.faixas_ocupacao.map((f) => f.n));
    cartOc.innerHTML = `<header><h3>Ocupação das remotas</h3><span class="small muted">com capacidade cadastrada</span></header>
      <div class="faixas">${d.faixas_ocupacao.map((f, i) => `<div class="faixa f${i}"><span>${esc(f.faixa)}</span>
        <div class="faixa-trilho"><i style="width:${Math.round(f.n / maxF * 100)}%"></i></div><b>${num(f.n)}</b></div>`).join("")}</div>
      <p class="small muted" style="margin:10px 0 0">Mais ocupadas: ${(d.remotas.filter((r) => r.ocupacao !== null).sort((a, b) => b.ocupacao - a.ocupacao).slice(0, 4)
        .map((r) => `<a href="#" data-remota="${esc(r.id)}">${esc(r.id)}</a> ${pct(r.ocupacao)}`).join(" · ")) || "–"}</p>`;
    g.appendChild(cartOc);
    $$("a[data-remota]", cartOc).forEach((a) => a.addEventListener("click", (e) => { e.preventDefault(); abrirRemota(a.dataset.remota); }));

    // ---- lista
    const sec = document.createElement("div");
    sec.className = "pilha";
    sec.style.marginTop = "16px";
    box.appendChild(sec);
    if (ctx.vista === "remotas") {
      sec.innerHTML = `<div class="small muted">${num(lista.length)} remota(s) no filtro</div>` +
        (lista.length ? `<div class="grade-remotas">${lista.map(cardRemota).join("")}</div>` : `<div class="vazio">Nenhuma remota no filtro.</div>`);
      $$(".r-card", sec).forEach((c) => c.addEventListener("click", () => abrirRemota(c.dataset.id)));
    } else {
      renderPontos(sec);
    }
  }

  function cardRemota(r) {
    const badges = [r.area && `<span class="selo">${esc(r.area)}</span>`,
      r.sistema && `<span class="selo azul">${esc(r.sistema)}</span>`,
      r.grupo && `<span class="selo">${esc(r.grupo)}${r.rota ? " · " + esc(r.rota) : ""}</span>`].filter(Boolean).join("");
    const doBase = (r.fontes || []).filter((f) => f !== "RemotasPuma");
    return `<button class="r-card ocup-${nivelOcup(r.ocupacao)}" data-id="${esc(r.id)}" type="button">
      <div class="r-topo"><div><div class="r-id">${esc(r.id)}</div><div class="small muted">${num(r.pontos)} pontos</div></div>
        <div class="r-anel" style="--p:${r.ocupacao === null ? 0 : Math.round(r.ocupacao * 100)}"><span>${r.ocupacao === null ? "–" : pct(r.ocupacao)}</span></div></div>
      <div class="r-selos">${badges}</div>
      ${composicao(r.tipos, r.pontos)}
      <div class="r-tipos">${ordenarTipos(r.tipos).map((t) => chip(t, r.tipos[t])).join("")}</div>
      ${doBase.length ? `<div class="small muted r-fonte" title="${esc(doBase.join(", "))}">📄 ${esc(doBase.slice(0, 2).join(", "))}${doBase.length > 2 ? "…" : ""}</div>` : ""}
    </button>`;
  }

  // ------------------------------------------------------------------ pontos (tabela)
  let buscaPontos = 0;
  async function renderPontos(sec) {
    const f = ctx.filtro, meu = ++buscaPontos;
    sec.innerHTML = `<div class="muted small">Buscando…</div>`;
    const qs = new URLSearchParams({ q: f.q, area: f.area, tipo: f.tipo, limite: "500" });
    let pts;
    try { pts = await ctx.api(`/api/io?${qs}`); } catch (e) { sec.innerHTML = `<div class="erro">${esc(e.message)}</div>`; return; }
    if (meu !== buscaPontos) return;
    sec.innerHTML = `<div class="small muted">${pts.length >= 500 ? "Primeiros 500 pontos — refine a busca" : `${num(pts.length)} ponto(s)`}</div>` +
      (pts.length ? `<div class="tabela-wrap">${tabelaPontos(pts, true)}</div>` : `<div class="vazio">Nenhum ponto encontrado.</div>`);
    $$("tr[data-remota]", sec).forEach((tr) => tr.addEventListener("click", () => abrirRemota(tr.dataset.remota)));
  }

  function tabelaPontos(pts, comRemota) {
    return `<table class="dados pontos"><thead><tr>${comRemota ? "<th>Remota</th>" : ""}<th>Tag</th><th>Descrição</th><th>Tipo</th>
      <th>Cartão / slot</th><th>Canal</th><th>Borneira</th><th>JB · borne</th><th>Fonte</th></tr></thead><tbody>${pts.map((p) => `
      <tr ${comRemota && p.remota ? `data-remota="${esc(p.remota)}" class="clicavel"` : ""}>
        ${comRemota ? `<td class="mono">${esc(p.remota || "–")}</td>` : ""}
        <td class="mono"><strong>${esc((p.prefixo ? p.prefixo + " " : "") + p.tag)}</strong></td>
        <td>${esc(p.descricao || "")}</td><td>${chip(p.tipo)}</td>
        <td class="mono">${esc([p.cartao, p.slot && "slot " + p.slot].filter(Boolean).join(" · ") || "–")}</td>
        <td class="mono">${esc(p.canal ?? "–")}</td><td class="mono">${esc(p.borneira || "–")}</td>
        <td class="mono">${esc([p.jb, p.jb_borne].filter(Boolean).join(" · ") || "–")}</td>
        <td class="small muted">${esc((p.fontes || []).join(", "))}</td></tr>`).join("")}</tbody></table>`;
  }

  // ------------------------------------------------------------------ detalhe da remota
  async function abrirRemota(id) {
    ctx.dialogo(id, `<div class="muted">Carregando…</div>`);
    let r;
    try { r = await ctx.api(`/api/remotas/${encodeURIComponent(id)}`); } catch (e) { $("#dialogo-conteudo").innerHTML = `<div class="erro">${esc(e.message)}</div>`; return; }
    const cap = r.capacidade && r.capacidade.tipos;
    const capHtml = cap ? `<div class="cap-grade">${ordenarTipos(cap).map((t) => {
      const c = cap[t], o = c.instalados ? c.utilizados / c.instalados : null;
      return `<div class="cap ${classeTipo(t)}">${chip(t)}${barraOcup(o, `${num(c.utilizados)} / ${num(c.instalados)}`)}
        <div class="small muted">Reserva ${num(c.reservaAtual)} · exigida ${num(c.reservaExigida)}${c.reservaAtual < c.reservaExigida ? ` <span class="txt-ruim">▼ abaixo</span>` : ""}</div></div>`;
    }).join("")}</div>` : `<p class="small muted">Sem capacidade instalada cadastrada para este gabinete (vem só das listas de I/O).</p>`;
    const cartoes = (r.cartoes || []).slice(0, 120);
    $("#dialogo-conteudo").innerHTML = `
      <div class="det-topo">
        <div class="r-selos">${[r.area, r.sistema, r.grupo && `${r.grupo}${r.rota ? " · " + r.rota : ""}${r.seq ? ` (parada #${r.seq})` : ""}`]
          .filter(Boolean).map((b) => `<span class="selo">${esc(b)}</span>`).join("")}</div>
        <div class="tiles det-kpis">${tile("Pontos", num(r.pontos))}${tile("Ocupação", pct(r.ocupacao), r.ocupacao === null ? `<div class="ref">sem capacidade cadastrada</div>` : barraOcup(r.ocupacao, ""), r.ocupacao === null ? "" : "kpi-" + nivelOcup(r.ocupacao))}
          ${tile("Cartões / borneiras", num(cartoes.length))}${tile("Fontes", num((r.fontes || []).length), `<div class="ref">${esc((r.fontes || []).join(", "))}</div>`)}</div>
        ${composicao(r.tipos, r.pontos)}
      </div>
      <h3 class="det-tit">Capacidade instalada</h3>${capHtml}
      <h3 class="det-tit">Mapa de cartões / borneiras</h3>
      <div class="rack">${cartoes.map((c) => `<button type="button" class="slot ${classeTipo(c.tipo)}" data-slot="${esc(c.slot)}" title="${esc(c.cartao || "")}">
        <span class="s-id">${esc(c.slot)}</span><span class="s-mod">${esc(c.cartao || c.tipo || "")}</span><span class="s-n">${num(c.canais)} ch</span></button>`).join("")}</div>
      <h3 class="det-tit">Pontos de I/O</h3>
      <input type="text" id="det-filtro" placeholder="Filtrar pontos desta remota (tag, descrição, canal…)">
      <div class="tabela-wrap" id="det-tabela"></div>
      <div style="margin-top:12px"><button class="btn mini" id="det-imprimir" type="button">🖨 Imprimir</button></div>`;
    const pts = r.pontos_lista || [];
    const desenhar = (q) => {
      const s = sn(q).replace(/[-\s]/g, "");
      const f = !s ? pts : pts.filter((p) => sn([p.prefixo, p.tag, p.descricao, p.canal, p.slot, p.cartao, p.borneira, p.jb].join(" ")).replace(/[-\s]/g, "").includes(s));
      $("#det-tabela").innerHTML = f.length ? tabelaPontos(f, false) : `<div class="vazio">Nada no filtro.</div>`;
    };
    desenhar("");
    $("#det-filtro").addEventListener("input", (e) => desenhar(e.target.value));
    $$(".rack .slot").forEach((b) => b.addEventListener("click", () => {
      const sl = b.dataset.slot;
      $("#det-filtro").value = "";
      const f = pts.filter((p) => (p.slot || p.borneira || "—") === sl);
      $("#det-tabela").innerHTML = tabelaPontos(f, false);
      $$(".rack .slot").forEach((x) => x.classList.toggle("ativo", x === b));
    }));
    $("#det-imprimir").addEventListener("click", () => window.print());
  }

  // ------------------------------------------------------------------ cards do chat
  function cardsEndereco(cartoes) {
    if (!cartoes || !cartoes.length) return "";
    return `<div class="end-cards">${cartoes.map((p) => {
      const passos = [
        p.jb && { rot: "Caixa de junção", val: p.jb, sub: p.jb_borne && `borne ${p.jb_borne}` },
        p.remota && { rot: "Remota / gabinete", val: p.remota, sub: p.borneira && `borneira ${p.borneira}`, remota: p.remota },
        (p.cartao || p.slot) && { rot: "Cartão", val: p.cartao || "—", sub: p.slot && `slot ${p.slot}` },
        p.canal !== undefined && { rot: "Canal", val: p.canal },
        p.controlador && { rot: "Controlador", val: p.controlador },
      ].filter(Boolean);
      return `<div class="end-card ${classeTipo(p.tipo)}">
        <div class="end-topo"><div><div class="end-tag">${esc((p.prefixo ? p.prefixo + " " : "") + p.tag)}</div>
          <div class="small muted">${esc(p.descricao || "")}</div></div>${chip(p.tipo)}</div>
        <div class="end-cadeia">${passos.map((s, i) => `${i ? `<span class="seta" aria-hidden="true">→</span>` : ""}
          <div class="elo${s.remota ? " clicavel" : ""}" ${s.remota ? `data-remota="${esc(s.remota)}" role="button" tabindex="0"` : ""}>
          <span class="elo-rot">${esc(s.rot)}</span><span class="elo-val">${esc(s.val)}</span>${s.sub ? `<span class="elo-sub">${esc(s.sub)}</span>` : ""}</div>`).join("")}</div>
        <div class="end-fonte small muted">${p.area ? esc(p.area) + " · " : ""}fonte: ${esc((p.fontes || []).join(", ") || "–")}${p.local ? " · " + esc(p.local) : ""}</div>
      </div>`;
    }).join("")}</div>`;
  }

  function ligarCards(raiz) {
    $$(".elo[data-remota]", raiz).forEach((e) => {
      const abrir = () => abrirRemota(e.dataset.remota);
      e.addEventListener("click", abrir);
      e.addEventListener("keydown", (ev) => { if (ev.key === "Enter") abrir(); });
    });
  }

  // ------------------------------------------------------------------ controles da aba
  function trocarVista(v) {
    ctx.vista = v;
    $$("#rem-vista button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.vista === v)));
    if (ctx.dados) render();
  }

  function init(op) {
    ctx.api = op.api; ctx.dialogo = op.dialogo;
    let t;
    $("#rem-busca").addEventListener("input", (e) => { clearTimeout(t); t = setTimeout(() => { ctx.filtro.q = e.target.value; if (ctx.dados) render(); }, 200); });
    $("#rem-area").addEventListener("change", (e) => { ctx.filtro.area = e.target.value; render(); });
    $("#rem-tipo").addEventListener("change", (e) => { ctx.filtro.tipo = e.target.value; render(); });
    $("#rem-ocup").addEventListener("change", (e) => { ctx.filtro.ocup = e.target.value; render(); });
    $$("#rem-vista button").forEach((b) => b.addEventListener("click", () => trocarVista(b.dataset.vista)));
    $("#rem-limpar").addEventListener("click", () => {
      ctx.filtro = { q: "", area: "", tipo: "", ocup: "" };
      ["#rem-busca", "#rem-area", "#rem-tipo", "#rem-ocup"].forEach((s) => { $(s).value = ""; });
      render();
    });
    $("#rem-importar").addEventListener("click", () => $("#rem-arquivo").click());
    $("#rem-arquivo").addEventListener("change", async (e) => {
      const f = e.target.files[0];
      e.target.value = "";
      if (!f) return;
      $("#rem-gerado").textContent = "importando cadastro…";
      const fd = new FormData();
      fd.append("arquivo", f);
      try {
        const r = await ctx.api("/api/remotas/importar", { method: "POST", body: fd });
        $("#rem-gerado").textContent = `${num(r.remotas)} remotas e ${num(r.pontos)} pontos importados`;
        carregar();
      } catch (err) { $("#rem-gerado").textContent = "erro: " + err.message; }
    });
    $("#rem-reler").addEventListener("click", async () => {
      await ctx.api("/api/remotas/reindexar", { method: "POST" }).catch(() => { });
      $("#rem-gerado").textContent = "lendo planilhas da base… atualize em alguns minutos";
    });
    $("#rem-atualizar").addEventListener("click", carregar);
  }

  window.Remotas = { init, carregar, cardsEndereco, ligarCards, abrirRemota };
})();
