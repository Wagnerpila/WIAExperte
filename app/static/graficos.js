/* Gráficos SVG leves para o painel: barras horizontais, colunas temporais e tabela alternativa. */
(function () {
  "use strict";
  const NS = "http://www.w3.org/2000/svg";
  const tip = () => document.getElementById("tooltip");

  function el(tag, attrs, pai) {
    const n = document.createElementNS(NS, tag);
    for (const k in attrs || {}) n.setAttribute(k, attrs[k]);
    if (pai) pai.appendChild(n);
    return n;
  }

  function mostrarTip(evt, html) {
    const t = tip();
    t.innerHTML = html;
    t.classList.remove("hidden");
    let x, y;
    if (evt.clientX !== undefined && evt.type !== "focus") { x = evt.clientX; y = evt.clientY; }
    else { const r = evt.target.getBoundingClientRect(); x = r.left + r.width / 2; y = r.top; }
    const w = t.offsetWidth, h = t.offsetHeight;
    t.style.left = Math.min(Math.max(8, x + 12), window.innerWidth - w - 8) + "px";
    t.style.top = Math.max(8, y - h - 12) + "px";
  }
  function esconderTip() { tip().classList.add("hidden"); }

  function ligarTip(alvo, html) {
    alvo.setAttribute("tabindex", "0");
    alvo.setAttribute("class", (alvo.getAttribute("class") || "") + " alvo");
    alvo.addEventListener("mousemove", (e) => mostrarTip(e, html));
    alvo.addEventListener("focus", (e) => mostrarTip(e, html));
    alvo.addEventListener("mouseleave", esconderTip);
    alvo.addEventListener("blur", esconderTip);
  }

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const num = (v, casas = 0) => (v === null || v === undefined || isNaN(v)) ? "–" :
    Number(v).toLocaleString("pt-BR", { maximumFractionDigits: casas, minimumFractionDigits: 0 });

  /* Barra com cantos arredondados só na ponta (4px), ancorada na linha de base. */
  function barraH(g, x, y, w, h) {
    const r = Math.min(4, w / 2, h / 2);
    if (w <= 0) return el("rect", { x, y, width: 0, height: h, class: "barra" }, g);
    return el("path", {
      class: "barra",
      d: `M${x},${y} H${x + w - r} Q${x + w},${y} ${x + w},${y + r} V${y + h - r} Q${x + w},${y + h} ${x + w - r},${y + h} H${x} Z`,
    }, g);
  }
  function barraV(g, x, y, w, h, base) {
    const r = Math.min(4, w / 2, h / 2);
    if (h <= 0) return el("rect", { x, y: base, width: w, height: 0, class: "barra" }, g);
    return el("path", {
      class: "barra",
      d: `M${x},${base} V${y + r} Q${x},${y} ${x + r},${y} H${x + w - r} Q${x + w},${y} ${x + w},${y + r} V${base} Z`,
    }, g);
  }

  function escalaBonita(max) {
    if (max <= 0) return { max: 1, passo: 1 };
    const exp = Math.pow(10, Math.floor(Math.log10(max)));
    const f = max / exp;
    const passo = (f <= 2 ? 0.5 : f <= 5 ? 1 : 2) * exp;
    return { max: Math.ceil(max / passo) * passo, passo };
  }

  /**
   * Barras horizontais.
   * op: {rotulo(d), valor(d), fmt(v), dica(d) -> html, extra(d) -> texto à direita do valor}
   */
  function barrasH(alvo, dados, op) {
    alvo.innerHTML = "";
    if (!dados || !dados.length) { alvo.innerHTML = `<div class="vazio">${esc(op.vazio || "Sem dados no período.")}</div>`; return; }
    const largura = Math.max(alvo.clientWidth || 360, 280);
    const rotW = Math.min(170, Math.round(largura * 0.38));
    const valW = op.extra ? 124 : 56;
    const altB = 14, gap = 8, topo = 4;
    const altura = topo + dados.length * (altB + gap);
    const svg = el("svg", { viewBox: `0 0 ${largura} ${altura}`, role: "img", "aria-label": op.titulo || "gráfico de barras" }, alvo);
    const max = Math.max(...dados.map(op.valor), 0) || 1;
    const areaW = largura - rotW - valW - 8;
    el("line", { x1: rotW, x2: rotW, y1: 0, y2: altura, class: "base" }, svg);
    dados.forEach((d, i) => {
      const y = topo + i * (altB + gap);
      const v = op.valor(d) || 0;
      const g = el("g", {}, svg);
      const rot = String(op.rotulo(d) ?? "");
      const t = el("text", { x: rotW - 8, y: y + altB - 3, "text-anchor": "end" }, g);
      t.textContent = rot.length > 26 ? rot.slice(0, 25) + "…" : rot;
      el("rect", { x: 0, y: y - gap / 2, width: largura, height: altB + gap, fill: "transparent" }, g);
      barraH(g, rotW + 1, y, Math.max(1, (v / max) * areaW), altB);
      const tv = el("text", { x: rotW + 6 + (v / max) * areaW, y: y + altB - 3, class: "valor" }, g);
      tv.textContent = (op.fmt || num)(v) + (op.extra ? "  " + op.extra(d) : "");
      ligarTip(g, op.dica ? op.dica(d) : `<strong>${esc(rot)}</strong><br>${(op.fmt || num)(v)}`);
    });
  }

  /**
   * Colunas por período (série temporal ou categorias ordenadas).
   * op: {x(d) -> rótulo, y(d) -> valor, fmt, dica(d)}
   */
  function colunas(alvo, dados, op) {
    alvo.innerHTML = "";
    if (!dados || !dados.length) { alvo.innerHTML = `<div class="vazio">${esc(op.vazio || "Sem dados no período.")}</div>`; return; }
    const largura = Math.max(alvo.clientWidth || 360, 280);
    const altura = 190, esq = 40, dir = 8, topo = 14, baixo = 26;
    const svg = el("svg", { viewBox: `0 0 ${largura} ${altura}`, role: "img", "aria-label": op.titulo || "gráfico de colunas" }, alvo);
    const vals = dados.map((d) => op.y(d) || 0);
    const { max, passo } = escalaBonita(Math.max(...vals));
    const areaH = altura - topo - baixo, areaW = largura - esq - dir;
    const base = topo + areaH;
    for (let v = 0; v <= max + 1e-9; v += passo) {
      const y = base - (v / max) * areaH;
      el("line", { x1: esq, x2: largura - dir, y1: y, y2: y, class: v === 0 ? "base" : "grade" }, svg);
      const t = el("text", { x: esq - 6, y: y + 4, "text-anchor": "end" }, svg);
      t.textContent = (op.fmtEixo || num)(v);
    }
    const slot = areaW / dados.length;
    const bw = Math.max(2, Math.min(28, slot - 2));
    const passoRot = Math.ceil(dados.length / Math.max(1, Math.floor(areaW / 64)));
    const iMax = vals.indexOf(Math.max(...vals));
    dados.forEach((d, i) => {
      const v = vals[i];
      const x = esq + i * slot + (slot - bw) / 2;
      const h = (v / max) * areaH;
      const g = el("g", {}, svg);
      el("rect", { x: esq + i * slot, y: topo, width: slot, height: areaH, fill: "transparent" }, g);
      barraV(g, x, base - h, bw, h, base);
      if (i % passoRot === 0 || i === dados.length - 1) {
        const t = el("text", { x: x + bw / 2, y: altura - 8, "text-anchor": "middle" }, g);
        t.textContent = op.x(d);
      }
      if ((i === iMax || i === dados.length - 1) && v > 0) {
        const t = el("text", { x: x + bw / 2, y: base - h - 4, "text-anchor": "middle", class: "valor" }, g);
        t.textContent = (op.fmt || num)(v);
      }
      ligarTip(g, op.dica ? op.dica(d) : `<strong>${esc(op.x(d))}</strong><br>${(op.fmt || num)(v)}`);
    });
  }

  function tabela(cols, linhas) {
    if (!linhas || !linhas.length) return `<div class="vazio">Sem dados.</div>`;
    const th = cols.map((c) => `<th class="${c.num ? "num" : ""}">${esc(c.t)}</th>`).join("");
    const tr = linhas.map((l) => "<tr>" + cols.map((c) => {
      const v = typeof c.v === "function" ? c.v(l) : l[c.v];
      return `<td class="${c.num ? "num" : ""}">${c.html ? v : esc(c.num && typeof v === "number" ? num(v, c.casas ?? 1) : (v ?? "–"))}</td>`;
    }).join("") + "</tr>").join("");
    return `<div class="tabela-wrap"><table class="dados"><thead><tr>${th}</tr></thead><tbody>${tr}</tbody></table></div>`;
  }

  /* Cartão de gráfico com alternância gráfico/tabela (acessibilidade e leitura exata). */
  function cartao(pai, titulo, sub, desenhar, cols, linhas) {
    const c = document.createElement("div");
    c.className = "grafico";
    c.innerHTML = `<header><div><h3>${esc(titulo)}</h3>${sub ? `<div class="small muted">${esc(sub)}</div>` : ""}</div>` +
      (linhas && linhas.length ? `<button class="alternar" type="button">ver tabela</button>` : "") + `</header><div class="corpo"></div>`;
    pai.appendChild(c);
    const corpo = c.querySelector(".corpo");
    let modoTabela = false;
    const render = () => { if (modoTabela) corpo.innerHTML = tabela(cols, linhas); else desenhar(corpo); };
    const bt = c.querySelector(".alternar");
    if (bt) bt.addEventListener("click", () => { modoTabela = !modoTabela; bt.textContent = modoTabela ? "ver gráfico" : "ver tabela"; render(); });
    // Desenha depois que a grade define a largura final do cartão.
    requestAnimationFrame(render);
    c._render = render;
    return c;
  }

  window.Graf = { barrasH, colunas, tabela, cartao, esc, num };
})();
