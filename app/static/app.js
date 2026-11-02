/* WIA Expert — interface web (chat, painel, base, ocorrências, glossário). */
(function () {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const { esc, num } = window.Graf;

  const estado = { usuario: null, conversa: null, enviando: false, dias: 90, abaAtual: "chat", pollDocs: null };

  // ------------------------------------------------------------------ utilidades
  async function api(caminho, op = {}) {
    const cfg = { credentials: "same-origin", ...op, headers: { ...(op.headers || {}) } };
    if (op.json !== undefined) { cfg.body = JSON.stringify(op.json); cfg.headers["Content-Type"] = "application/json"; }
    const r = await fetch(caminho, cfg);
    if (r.status === 401 && !caminho.endsWith("/login")) { mostrarLogin(); throw new Error("sessão expirada"); }
    const tipo = r.headers.get("content-type") || "";
    const corpo = tipo.includes("json") ? await r.json() : await r.text();
    if (!r.ok) throw new Error((corpo && corpo.detail) || `erro ${r.status}`);
    return corpo;
  }
  const guardar = (k, v) => { try { localStorage.setItem(k, v); } catch (_) { } };
  const ler = (k) => { try { return localStorage.getItem(k); } catch (_) { return null; } };
  // Datas sem fuso (horário da fonte) são exibidas como estão; datas UTC do sistema viram horário local.
  const dataBR = (s) => { if (!s) return "–"; const d = new Date(s); return isNaN(d) ? s : d.toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" }); };
  const duracao = (min) => (min === null || min === undefined) ? "–" : min >= 60 ? `${num(min / 60, 1)} h` : `${num(min, 0)} min`;
  const bytes = (b) => b > 1e9 ? `${num(b / 1e9, 1)} GB` : b > 1e6 ? `${num(b / 1e6, 1)} MB` : `${num(b / 1e3, 0)} kB`;
  function markdown(txt) {
    if (window.marked && window.DOMPurify) return DOMPurify.sanitize(marked.parse(txt || "", { breaks: true }));
    return `<p style="white-space:pre-wrap">${esc(txt)}</p>`;
  }
  function dialogo(titulo, html) {
    $("#dialogo-titulo").textContent = titulo;
    $("#dialogo-conteudo").innerHTML = html;
    $("#dialogo").showModal();
  }
  $("#dialogo-fechar").addEventListener("click", () => $("#dialogo").close());

  // ------------------------------------------------------------------ tema
  function aplicarTema(t) { if (t) document.documentElement.setAttribute("data-theme", t); }
  aplicarTema(ler("wia-tema"));
  $("#btn-tema").addEventListener("click", () => {
    const atual = document.documentElement.getAttribute("data-theme") ||
      (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    const novo = atual === "dark" ? "light" : "dark";
    aplicarTema(novo); guardar("wia-tema", novo);
    if (estado.abaAtual === "painel") carregarPainel();
  });

  // ------------------------------------------------------------------ login
  function mostrarLogin() { $("#app").classList.add("hidden"); $("#tela-login").classList.remove("hidden"); $("#login-usuario").focus(); }
  async function entrar(u) {
    estado.usuario = u;
    $("#nome-usuario").textContent = u.username;
    $("#tela-login").classList.add("hidden");
    $("#app").classList.remove("hidden");
    await carregarConversas();
    const aba = ler("wia-aba") || "chat";
    trocarAba(aba);
  }
  $("#form-login").addEventListener("submit", async (e) => {
    e.preventDefault();
    $("#login-erro").textContent = "";
    try {
      const u = await api("/api/login", { method: "POST", json: { usuario: $("#login-usuario").value, senha: $("#login-senha").value } });
      $("#login-senha").value = "";
      entrar(u);
    } catch (err) { $("#login-erro").textContent = err.message; }
  });
  $("#btn-sair").addEventListener("click", async () => { await api("/api/logout", { method: "POST" }).catch(() => { }); mostrarLogin(); });

  // ------------------------------------------------------------------ abas
  function trocarAba(aba) {
    estado.abaAtual = aba; guardar("wia-aba", aba);
    $$("nav.abas button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.aba === aba)));
    $$("[data-painel]").forEach((s) => s.classList.toggle("hidden", s.dataset.painel !== aba));
    if (aba === "painel") carregarPainel();
    if (aba === "base") carregarDocs();
    if (aba === "ocorrencias") carregarOcorrencias();
    if (aba === "glossario") carregarGlossario();
  }
  $$("nav.abas button").forEach((b) => b.addEventListener("click", () => trocarAba(b.dataset.aba)));

  // ------------------------------------------------------------------ chat
  const SUGESTOES = [
    "A CPU do CLP foi para STOP depois de uma queda de energia. Por onde começo?",
    "Uma sequência no DNA não avança do passo atual. Como descubro o que está bloqueando?",
    "Drive Altivar desarmando com OCF na partida. Quais as causas mais prováveis?",
    "O que você já tem na base? Liste o que falta para a área da caldeira de recuperação.",
  ];

  async function carregarConversas() {
    const lista = await api("/api/conversas");
    const box = $("#conversas");
    box.innerHTML = lista.length ? "" : `<div class="muted small" style="padding:8px">Nenhuma conversa ainda.</div>`;
    lista.forEach((c) => {
      const b = document.createElement("button");
      b.className = "conv"; b.textContent = c.titulo; b.title = c.titulo;
      b.setAttribute("aria-current", String(estado.conversa === c.id));
      b.addEventListener("click", () => { abrirConversa(c.id); $("#aba-chat").classList.remove("mostrar-conversas"); });
      box.appendChild(b);
    });
    if (!estado.conversa) renderMensagens([]);
  }

  async function abrirConversa(id) {
    estado.conversa = id;
    const c = await api(`/api/conversas/${id}`);
    $("#titulo-conversa").textContent = c.titulo;
    renderMensagens(c.mensagens);
    $$("#conversas button.conv").forEach((b) => b.setAttribute("aria-current", "false"));
    carregarConversas();
  }

  function htmlMensagem(m) {
    const ferr = (m.ferramentas || []).length
      ? `<div class="ferramentas">${m.ferramentas.map((f) => `<span>${f.ok ? "🔎" : "⚠️"} ${esc(f.resumo)}</span>`).join("")}</div>` : "";
    const anexos = (m.anexos || []).length ? `<div class="anexos">📎 ${m.anexos.map((a) => esc(a.nome)).join(", ")}</div>` : "";
    const corpo = m.papel === "assistant" ? markdown(m.texto) : esc(m.texto);
    return `<div class="msg ${m.papel}"><div class="quem">${m.papel === "assistant" ? "Assistente" : "Você"} · ${dataBR(m.criada_em)}</div>${ferr}<div class="corpo">${corpo}</div>${anexos}</div>`;
  }

  function renderMensagens(msgs) {
    const box = $("#mensagens");
    if (!msgs.length) {
      box.innerHTML = `<div class="vazio-chat"><h2>Como posso ajudar?</h2>
        <p>Descreva o sintoma com equipamento, tag, horário e o que mudou. Pode mandar foto da IHM, do painel ou dos LEDs.</p>
        <div class="sugestoes">${SUGESTOES.map((s) => `<button class="btn" type="button">${esc(s)}</button>`).join("")}</div></div>`;
      $$(".sugestoes button", box).forEach((b) => b.addEventListener("click", () => { $("#texto").value = b.textContent; $("#texto").focus(); }));
      return;
    }
    box.innerHTML = msgs.map(htmlMensagem).join("");
    box.scrollTop = box.scrollHeight;
  }

  $("#btn-nova").addEventListener("click", () => {
    estado.conversa = null; $("#titulo-conversa").textContent = "Nova conversa"; renderMensagens([]);
    $$("#conversas button.conv").forEach((b) => b.setAttribute("aria-current", "false"));
    $("#aba-chat").classList.remove("mostrar-conversas"); $("#texto").focus();
  });
  $("#btn-mostrar-conversas").addEventListener("click", () => $("#aba-chat").classList.toggle("mostrar-conversas"));

  $("#fotos").addEventListener("change", () => {
    const n = $("#fotos").files.length;
    $("#previa-fotos").textContent = n ? `📎 ${Array.from($("#fotos").files).map((f) => f.name).join(", ")}` : "";
  });
  const ta = $("#texto");
  ta.addEventListener("input", () => { ta.style.height = "auto"; ta.style.height = Math.min(ta.scrollHeight, 200) + "px"; });
  ta.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey && !matchMedia("(max-width: 800px)").matches) { e.preventDefault(); $("#form-chat").requestSubmit(); } });

  $("#form-chat").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (estado.enviando) return;
    const texto = ta.value.trim();
    const fotos = Array.from($("#fotos").files);
    if (!texto && !fotos.length) return;
    estado.enviando = true; $("#btn-enviar").disabled = true;
    try {
      if (!estado.conversa) { const c = await api("/api/conversas", { method: "POST" }); estado.conversa = c.id; }
      const box = $("#mensagens");
      if (box.querySelector(".vazio-chat")) box.innerHTML = "";
      box.insertAdjacentHTML("beforeend", htmlMensagem({ papel: "user", texto, criada_em: new Date().toISOString(), anexos: fotos.map((f) => ({ nome: f.name })) }));
      box.insertAdjacentHTML("beforeend", `<div class="msg assistant" id="digitando"><div class="corpo digitando">Consultando a base e analisando…</div></div>`);
      box.scrollTop = box.scrollHeight;
      ta.value = ""; ta.style.height = "auto"; $("#fotos").value = ""; $("#previa-fotos").textContent = "";
      const fd = new FormData();
      fd.append("texto", texto);
      fotos.forEach((f) => fd.append("fotos", f));
      const r = await api(`/api/conversas/${estado.conversa}/mensagens`, { method: "POST", body: fd });
      $("#digitando")?.remove();
      box.insertAdjacentHTML("beforeend", htmlMensagem({ papel: "assistant", texto: r.texto, ferramentas: r.ferramentas, criada_em: new Date().toISOString() }));
      box.scrollTop = box.scrollHeight;
      carregarConversas();
    } catch (err) {
      $("#digitando")?.remove();
      $("#mensagens").insertAdjacentHTML("beforeend", `<div class="msg assistant"><div class="corpo" style="color:var(--critical)">Erro: ${esc(err.message)}</div></div>`);
    } finally { estado.enviando = false; $("#btn-enviar").disabled = false; }
  });

  // ------------------------------------------------------------------ painel
  $$("#periodo button").forEach((b) => b.addEventListener("click", () => {
    estado.dias = Number(b.dataset.dias);
    $$("#periodo button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    carregarPainel();
  }));
  $("#btn-atualizar-painel").addEventListener("click", () => carregarPainel());

  function tile(rot, val, extra = "") {
    return `<div class="tile"><div class="rot">${esc(rot)}</div><div class="val">${val}</div>${extra}</div>`;
  }
  function status(nivel, texto) {
    const rot = { bom: "dentro da referência", atencao: "atenção", ruim: "acima da referência" }[nivel];
    return `<div class="status ${nivel}"><i aria-hidden="true"></i>${rot}</div><div class="ref">${esc(texto)}</div>`;
  }
  function nivel(v, bom, atencao) { return v === null || v === undefined ? null : v <= bom ? "bom" : v <= atencao ? "atencao" : "ruim"; }

  function secao(pai, titulo, sub) {
    const s = document.createElement("section");
    s.className = "secao-painel";
    s.innerHTML = `<header><h2>${esc(titulo)}</h2>${sub ? `<span class="small muted">${sub}</span>` : ""}</header>`;
    pai.appendChild(s);
    return s;
  }
  function grade(pai) { const g = document.createElement("div"); g.className = "graficos"; pai.appendChild(g); return g; }
  function bloco(pai, html) { const d = document.createElement("div"); d.innerHTML = html; pai.appendChild(d.firstElementChild || d); return d; }

  async function carregarPainel() {
    const box = $("#painel-conteudo");
    box.innerHTML = `<div class="muted">Carregando…</div>`;
    let p;
    try { p = await api(`/api/painel?dias=${estado.dias}`); } catch (e) { box.innerHTML = `<div class="cartao" style="color:var(--critical)">${esc(e.message)}</div>`; return; }
    $("#painel-gerado").textContent = `atualizado ${dataBR(p.gerado_em)}`;
    box.innerHTML = "";
    box.classList.add("pilha");
    const B = p.base, O = p.ocorrencias, A = p.alarmes, U = p.uso;
    const tipos = Object.fromEntries((B.por_tipo || []).map((t) => [t.tipo, t.n]));
    const periodoTxt = estado.dias ? `últimos ${estado.dias} dias` : "todo o histórico";

    // ---- primeiros passos
    const passos = [
      [tipos.documentacao, "Subir manuais, P&IDs, descritivos funcionais e listas de I/O (tipo Documentação)."],
      [tipos.backup, "Subir backups e exportações de programa em texto (SCL/AWL/XML do TIA, L5X, relatórios do DNA Explorer/FbCAD, exportações do Control Expert)."],
      [tipos.whatsapp, "Exportar o grupo do WhatsApp (Exportar conversa → incluir mídia → .zip) e subir o arquivo."],
      [tipos.alarmes, "Exportar o histórico de alarmes/eventos em CSV e subir como “CSV de alarmes” para ligar os indicadores ISA-18.2."],
      [O.total || tipos.ocorrencias, "Registrar ocorrências (aba Ocorrências, pelo chat ou importando o CSV de histórico de manutenção)."],
      [B.glossario, "Cadastrar apelidos de equipamentos e siglas do grupo no glossário."],
    ];
    if (passos.some(([ok]) => !ok)) {
      bloco(box, `<div class="cartao"><h2>Primeiros passos</h2><p class="small muted">O painel se preenche conforme a base cresce. Faltam:</p>
        <ol class="passos">${passos.map(([ok, t]) => `<li class="${ok ? "feito" : ""}">${esc(t)}</li>`).join("")}</ol></div>`);
    }

    // ---- base de conhecimento
    let s = secao(box, "Base de conhecimento", `${num(B.documentos)} documentos · ${bytes(B.bytes || 0)}`);
    bloco(s, `<div class="tiles">
      ${tile("Documentos", num(B.documentos))}
      ${tile("Trechos indexados", num(B.trechos))}
      ${tile("Trechos de WhatsApp", num(B.mensagens_whatsapp_trechos))}
      ${tile("Notas", num(B.notas))}
      ${tile("Termos no glossário", num(B.glossario))}
      ${tile("Pendências", num((B.pendencias || []).length), (B.pendencias || []).length ? `<div class="ref">erro, sem texto, aguardando transcrição ou binário</div>` : "")}
    </div>`);
    let g = grade(s);
    Graf.cartao(g, "Documentos por tipo", null, (el) => Graf.barrasH(el, B.por_tipo, { rotulo: (d) => d.tipo, valor: (d) => d.n, vazio: "Nenhum documento ainda. Use a aba Base." }),
      [{ t: "Tipo", v: "tipo" }, { t: "Documentos", v: "n", num: true, casas: 0 }, { t: "Tamanho", v: (d) => bytes(d.bytes) }], B.por_tipo);
    Graf.cartao(g, "Documentos por plataforma", null, (el) => Graf.barrasH(el, B.por_plataforma, { rotulo: (d) => d.plataforma, valor: (d) => d.n, vazio: "Sem documentos." }),
      [{ t: "Plataforma", v: "plataforma" }, { t: "Documentos", v: "n", num: true, casas: 0 }], B.por_plataforma);
    Graf.cartao(g, "Documentos por área", null, (el) => Graf.barrasH(el, B.por_area, { rotulo: (d) => d.area, valor: (d) => d.n, vazio: "Sem documentos." }),
      [{ t: "Área", v: "area" }, { t: "Documentos", v: "n", num: true, casas: 0 }], B.por_area);
    Graf.cartao(g, "Envios por semana", periodoTxt, (el) => Graf.colunas(el, B.envios_semana, { x: (d) => d.semana.slice(5).split("-").reverse().join("/"), y: (d) => d.n, dica: (d) => `<strong>Semana de ${esc(d.semana)}</strong><br>${num(d.n)} documentos`, vazio: "Nenhum envio no período." }),
      [{ t: "Semana (segunda-feira)", v: "semana" }, { t: "Documentos", v: "n", num: true, casas: 0 }], B.envios_semana);
    if ((B.pendencias || []).length) {
      bloco(s, `<div class="grafico" style="margin-top:12px"><h3>Pendências da base</h3>${Graf.tabela([
        { t: "#", v: "id", num: true, casas: 0 }, { t: "Documento", v: "nome" }, { t: "Tipo", v: "tipo" },
        { t: "Status", v: "status" }, { t: "Motivo", v: "motivo" }, { t: "Enviado", v: (d) => dataBR(d.enviado_em) }], B.pendencias)}</div>`);
    }

    // ---- ocorrências
    s = secao(box, "Ocorrências e confiabilidade", `${periodoTxt} · MTTR = tempo médio de parada por ocorrência; MTBF = tempo médio entre ocorrências`);
    bloco(s, `<div class="tiles">
      ${tile("Ocorrências", num(O.total))}
      ${tile("Tempo de parada", duracao(O.parada_total_min))}
      ${tile("MTTR", duracao(O.mttr_min), `<div class="ref">média das ocorrências com tempo informado</div>`)}
      ${tile("MTBF da planta", O.mtbf_planta_h ? `${num(O.mtbf_planta_h, 0)} <small>h</small>` : "–", `<div class="ref">período ÷ nº de ocorrências</div>`)}
      ${tile("Equipamentos afetados", num(O.equipamentos_afetados))}
      ${tile("Com repetição", num(O.repetidas), `<div class="ref">equipamentos com 2+ ocorrências</div>`)}
      ${tile("Com causa raiz", O.pct_com_causa === null ? "–" : `${num(O.pct_com_causa, 0)}<small>%</small>`)}
    </div>`);
    g = grade(s);
    const vazioOc = "Nenhuma ocorrência no período. Registre na aba Ocorrências ou pelo chat.";
    Graf.cartao(g, "Pareto: tempo de parada por equipamento", "valor e % acumulado do total", (el) => Graf.barrasH(el, O.pareto_parada, {
      rotulo: (d) => d.equipamento, valor: (d) => d.parada_min, fmt: duracao, extra: (d) => `(${num(d.acumulado_pct, 0)}% acum.)`, vazio: vazioOc,
      dica: (d) => `<strong>${esc(d.equipamento)}</strong><br>${duracao(d.parada_min)} em ${num(d.n)} ocorrência(s)<br>${num(d.acumulado_pct, 0)}% acumulado`,
    }), [{ t: "Equipamento", v: "equipamento" }, { t: "Parada (min)", v: "parada_min", num: true }, { t: "Ocorrências", v: "n", num: true, casas: 0 }, { t: "% acumulado", v: "acumulado_pct", num: true }], O.pareto_parada);
    Graf.cartao(g, "Equipamentos com mais ocorrências", "bad actors", (el) => Graf.barrasH(el, O.pareto_n, {
      rotulo: (d) => d.equipamento, valor: (d) => d.n, vazio: vazioOc,
      dica: (d) => `<strong>${esc(d.equipamento)}</strong><br>${num(d.n)} ocorrências · ${duracao(d.parada_min)}<br>MTBF ${d.mtbf_h ? num(d.mtbf_h, 0) + " h" : "–"} · MTTR ${duracao(d.mttr_min)}`,
    }), [{ t: "Equipamento", v: "equipamento" }, { t: "Ocorrências", v: "n", num: true, casas: 0 }, { t: "MTBF (h)", v: "mtbf_h", num: true }, { t: "MTTR (min)", v: "mttr_min", num: true }, { t: "Última", v: (d) => dataBR(d.ultima) }], O.pareto_n);
    Graf.cartao(g, "Por categoria de causa", null, (el) => Graf.barrasH(el, O.por_categoria, { rotulo: (d) => d.nome, valor: (d) => d.n, vazio: vazioOc }),
      [{ t: "Categoria", v: "nome" }, { t: "Ocorrências", v: "n", num: true, casas: 0 }, { t: "Parada (min)", v: "parada_min", num: true }], O.por_categoria);
    Graf.cartao(g, "Por área", null, (el) => Graf.barrasH(el, O.por_area, { rotulo: (d) => d.nome, valor: (d) => d.n, vazio: vazioOc }),
      [{ t: "Área", v: "nome" }, { t: "Ocorrências", v: "n", num: true, casas: 0 }, { t: "Parada (min)", v: "parada_min", num: true }], O.por_area);
    Graf.cartao(g, "Por plataforma", null, (el) => Graf.barrasH(el, O.por_plataforma, { rotulo: (d) => d.nome, valor: (d) => d.n, vazio: vazioOc }),
      [{ t: "Plataforma", v: "nome" }, { t: "Ocorrências", v: "n", num: true, casas: 0 }, { t: "Parada (min)", v: "parada_min", num: true }], O.por_plataforma);
    Graf.cartao(g, "Ocorrências por mês", null, (el) => Graf.colunas(el, O.mensal, { x: (d) => d.mes.split("-").reverse().join("/"), y: (d) => d.n, vazio: vazioOc, dica: (d) => `<strong>${esc(d.mes)}</strong><br>${num(d.n)} ocorrências · ${duracao(d.parada_min)}` }),
      [{ t: "Mês", v: "mes" }, { t: "Ocorrências", v: "n", num: true, casas: 0 }, { t: "Parada (min)", v: "parada_min", num: true }], O.mensal);
    if ((O.equipamentos_repetidos || []).length) {
      bloco(s, `<div class="grafico" style="margin-top:12px"><h3>Falhas repetidas</h3><p class="small muted">Candidatos a análise de causa raiz e ação preventiva.</p>${Graf.tabela([
        { t: "Equipamento", v: "equipamento" }, { t: "Área", v: "area" }, { t: "Ocorrências", v: "n", num: true, casas: 0 },
        { t: "MTBF (h)", v: "mtbf_h", num: true }, { t: "MTTR", v: (d) => duracao(d.mttr_min) }, { t: "Parada total", v: (d) => duracao(d.parada_min) }, { t: "Última", v: (d) => dataBR(d.ultima) }], O.equipamentos_repetidos)}</div>`);
    }
    if ((O.recentes || []).length) {
      bloco(s, `<div class="grafico" style="margin-top:12px"><h3>Ocorrências recentes</h3>${Graf.tabela([
        { t: "Data", v: (d) => dataBR(d.data_hora) }, { t: "Equipamento", v: "equipamento" }, { t: "Sintoma", v: "sintoma" },
        { t: "Causa raiz", v: "causa_raiz" }, { t: "Parada", v: (d) => duracao(d.tempo_parada_min) }], O.recentes)}</div>`);
    }

    // ---- alarmes
    s = secao(box, "Desempenho de alarmes", `referências EEMUA 191 / ISA-18.2 (orientativas; vale a filosofia de alarmes da planta) · ${periodoTxt}`);
    if (!A.total_eventos) {
      bloco(s, `<div class="cartao vazio">Nenhum evento de alarme importado. Exporte o histórico de alarmes/eventos do sistema (DNA, WinCC, Control Expert…) em CSV com colunas de data/hora e tag (estado e prioridade se houver) e envie na aba Base como “CSV de alarmes”. Veja o <a href="/static/modelos/alarmes_modelo.csv" download>modelo</a>.</div>`);
    } else {
      const R = A.referencias;
      bloco(s, `<div class="tiles">
        ${tile("Alarmes anunciados", num(A.anunciados), `<div class="ref">${dataBR(A.inicio)} a ${dataBR(A.fim)}${A.tem_estado ? "" : " · sem coluna de estado: todos os eventos contam"}</div>`)}
        ${tile("Média por 10 min", num(A.media_10min, 2), status(nivel(A.media_10min, 1, 2), R.media_10min.texto))}
        ${tile("Pico em 10 min", num(A.pico_10min), status(A.pico_10min <= 10 ? "bom" : "ruim", R.pico_10min.texto))}
        ${tile("Tempo em inundação", `${num(A.pct_flood, 2)}<small>%</small>`, status(nivel(A.pct_flood, 1, 5), R.pct_flood.texto))}
        ${tile("Standing (>24 h)", A.tem_estado ? num(A.n_standing) : "–", A.tem_estado ? status(nivel(A.n_standing, 4, 9), R.standing.texto) : `<div class="ref">precisa da coluna de estado</div>`)}
        ${tile("Top 10 = % do total", `${num(A.top10_pct, 0)}<small>%</small>`, status(nivel(A.top10_pct, 20, 50), R.top10_pct.texto))}
      </div>`);
      g = grade(s);
      Graf.cartao(g, "Alarmes por dia", null, (el) => Graf.colunas(el, A.diario, { x: (d) => d.dia.slice(5).split("-").reverse().join("/"), y: (d) => d.n }),
        [{ t: "Dia", v: "dia" }, { t: "Alarmes", v: "n", num: true, casas: 0 }], A.diario);
      Graf.cartao(g, "Top 10 alarmes mais frequentes", "primeiros alvos de racionalização", (el) => Graf.barrasH(el, A.top10, { rotulo: (d) => d.tag, valor: (d) => d.n }),
        [{ t: "Tag", v: "tag" }, { t: "Alarmes", v: "n", num: true, casas: 0 }], A.top10);
      Graf.cartao(g, "Por prioridade", null, (el) => Graf.barrasH(el, A.por_prioridade, { rotulo: (d) => d.nome, valor: (d) => d.n }),
        [{ t: "Prioridade", v: "nome" }, { t: "Alarmes", v: "n", num: true, casas: 0 }], A.por_prioridade);
      bloco(s, `<div class="graficos">
        <div class="grafico"><h3>Chattering</h3><p class="small muted">3+ ativações da mesma tag em 60 s</p>${Graf.tabela([{ t: "Tag", v: "tag" }, { t: "Rajadas", v: "rajadas", num: true, casas: 0 }, { t: "Ativações", v: "ativacoes", num: true, casas: 0 }], A.chattering)}</div>
        <div class="grafico"><h3>Standing</h3><p class="small muted">último estado ativo há mais de 24 h</p>${A.tem_estado ? Graf.tabela([{ t: "Tag", v: "tag" }, { t: "Desde", v: (d) => dataBR(d.desde) }, { t: "Mensagem", v: "mensagem" }], A.standing) : `<div class="vazio">Sem coluna de estado no CSV.</div>`}</div>
      </div>`);
    }

    // ---- lacunas
    s = secao(box, "Lacunas da base", "equipamentos com ocorrência registrada e sem backup, documentação ou logs na base");
    bloco(s, (p.lacunas || []).length
      ? `<div class="grafico">${Graf.tabela([{ t: "Equipamento", v: "equipamento" }, { t: "Ocorrências", v: "ocorrencias", num: true, casas: 0 }, { t: "Falta na base", v: (d) => d.faltando.join(", ") }], p.lacunas)}</div>`
      : `<div class="cartao vazio">${O.total ? "Todos os equipamentos com ocorrência têm backup, documentação e logs na base." : "Aparece aqui quando houver ocorrências registradas."}</div>`);

    // ---- uso
    s = secao(box, "Uso do assistente", `${periodoTxt} · modelo ${esc(U.modelo)} · custo estimado pela tabela de preços configurada`);
    bloco(s, `<div class="tiles">
      ${tile("Respostas", num(U.respostas))}
      ${tile("Conversas ativas", num(U.conversas))}
      ${tile("Chamadas à API", num(U.chamadas))}
      ${tile("Custo estimado", `US$ ${num(U.custo_usd, 2)}`)}
      ${tile("Entrada vinda do cache", U.pct_cache === null ? "–" : `${num(U.pct_cache, 0)}<small>%</small>`)}
      ${tile("Tempo médio por chamada", `${num(U.tempo_medio_s, 1)} <small>s</small>`)}
      ${tile("Buscas sem resultado", num(U.buscas_sem_resultado), `<div class="ref">indicam o que falta subir</div>`)}
    </div>`);
    g = grade(s);
    Graf.cartao(g, "Custo estimado por dia (US$)", null, (el) => Graf.colunas(el, U.diario, { x: (d) => d.dia.slice(5).split("-").reverse().join("/"), y: (d) => d.custo_usd, fmt: (v) => num(v, 2), fmtEixo: (v) => num(v, 2), vazio: "Sem uso no período.", dica: (d) => `<strong>${esc(d.dia)}</strong><br>US$ ${num(d.custo_usd, 3)} · ${num(d.chamadas)} chamadas` }),
      [{ t: "Dia", v: "dia" }, { t: "Chamadas", v: "chamadas", num: true, casas: 0 }, { t: "Custo (US$)", v: "custo_usd", num: true, casas: 3 }], U.diario);
    Graf.cartao(g, "Ferramentas usadas pelo assistente", null, (el) => Graf.barrasH(el, U.ferramentas, { rotulo: (d) => d.nome, valor: (d) => d.n, vazio: "Sem uso no período.", dica: (d) => `<strong>${esc(d.nome)}</strong><br>${num(d.n)} chamadas · ${num(d.erros)} erros` }),
      [{ t: "Ferramenta", v: "nome" }, { t: "Chamadas", v: "n", num: true, casas: 0 }, { t: "Erros", v: "erros", num: true, casas: 0 }], U.ferramentas);
  }
  let tRedim;
  window.addEventListener("resize", () => { clearTimeout(tRedim); tRedim = setTimeout(() => { if (estado.abaAtual === "painel") $$(".grafico").forEach((c) => c._render && c._render()); }, 200); });

  // ------------------------------------------------------------------ base
  const zona = $("#zona-upload"), inputArq = $("#arquivos"), inputPasta = $("#pasta");
  let selecionados = [];  // [{ file, caminho }]
  // Lixo que o Windows/Office deixa nas pastas: não vai para a base.
  const ehLixo = (nome) => /^(thumbs\.db|desktop\.ini|\.ds_store)$/i.test(nome) || nome.startsWith("~$") || nome.startsWith(".");

  function atualizarSelecao(itens) {
    const vistos = new Set();
    selecionados = itens.filter((it) => {
      const chave = it.caminho + "|" + it.file.size;
      if (ehLixo(it.file.name) || vistos.has(chave)) return false;
      vistos.add(chave); return true;
    });
    const box = $("#lista-envio");
    $("#btn-enviar-arquivos").disabled = !selecionados.length;
    if (!selecionados.length) { box.textContent = ""; return; }
    const total = selecionados.reduce((s, it) => s + it.file.size, 0);
    const pastas = new Set(selecionados.map((it) => it.caminho.split("/").slice(0, -1).join("/")).filter(Boolean));
    const porExt = {};
    selecionados.forEach((it) => { const m = it.file.name.match(/\.([^.]+)$/); const e = m ? m[1].toLowerCase() : "(sem extensão)"; porExt[e] = (porExt[e] || 0) + 1; });
    const exts = Object.entries(porExt).sort((a, b) => b[1] - a[1]).map(([e, n]) => `${n} ${e}`).join(" · ");
    const nomes = selecionados.slice(0, 8).map((it) => it.caminho).join(", ") + (selecionados.length > 8 ? `, … (+${selecionados.length - 8})` : "");
    box.innerHTML = `<strong>${num(selecionados.length)} arquivo(s)</strong>${pastas.size ? ` em ${num(pastas.size)} pasta(s)` : ""}, ${bytes(total)} — ${esc(exts)}<br><span class="muted">${esc(nomes)}</span>`;
  }
  const deLista = (files) => Array.from(files).map((f) => ({ file: f, caminho: f.webkitRelativePath || f.name }));

  // Arrastar pastas: percorre as subpastas pela API de entradas do navegador.
  function lerEntrada(entrada, prefixo, saida) {
    return new Promise((resolve) => {
      if (entrada.isFile) {
        entrada.file((f) => { saida.push({ file: f, caminho: prefixo + f.name }); resolve(); }, () => resolve());
        return;
      }
      if (!entrada.isDirectory) { resolve(); return; }
      const leitor = entrada.createReader();
      const filhos = [];
      const lote = () => leitor.readEntries(async (ents) => {  // devolve no máximo ~100 por chamada
        if (ents.length) { filhos.push(...ents); lote(); return; }
        for (const f of filhos) await lerEntrada(f, prefixo + entrada.name + "/", saida);
        resolve();
      }, () => resolve());
      lote();
    });
  }

  zona.addEventListener("click", () => inputArq.click());
  zona.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); inputArq.click(); } });
  $("#btn-escolher-pasta").addEventListener("click", () => inputPasta.click());
  inputArq.addEventListener("change", () => atualizarSelecao(deLista(inputArq.files)));
  inputPasta.addEventListener("change", () => atualizarSelecao(deLista(inputPasta.files)));
  ["dragenter", "dragover"].forEach((ev) => zona.addEventListener(ev, (e) => { e.preventDefault(); zona.classList.add("arrastando"); }));
  ["dragleave", "drop"].forEach((ev) => zona.addEventListener(ev, (e) => { e.preventDefault(); zona.classList.remove("arrastando"); }));
  zona.addEventListener("drop", async (e) => {
    const entradas = Array.from(e.dataTransfer.items || []).map((it) => it.webkitGetAsEntry && it.webkitGetAsEntry()).filter(Boolean);
    if (!entradas.length) { atualizarSelecao(deLista(e.dataTransfer.files)); return; }
    $("#lista-envio").textContent = "Lendo as pastas…";
    const itens = [];
    for (const en of entradas) await lerEntrada(en, "", itens);
    atualizarSelecao(itens);
  });

  // Envia em lotes (até 10 arquivos ou ~40 MB por requisição) para não estourar tempo nem memória.
  function lotes(itens) {
    const out = []; let atual = [], tam = 0;
    for (const it of itens) {
      if (atual.length && (atual.length >= 10 || tam + it.file.size > 40 * (1 << 20))) { out.push(atual); atual = []; tam = 0; }
      atual.push(it); tam += it.file.size;
    }
    if (atual.length) out.push(atual);
    return out;
  }

  $("#btn-enviar-arquivos").addEventListener("click", async () => {
    const bt = $("#btn-enviar-arquivos"), box = $("#lista-envio");
    const itens = selecionados.slice(), grupos = lotes(itens);
    bt.disabled = true;
    let enviados = 0;
    const falhas = [], naoEnviados = [];
    for (const [i, grupo] of grupos.entries()) {
      bt.textContent = `Enviando ${num(enviados)}/${num(itens.length)}…`;
      box.textContent = `Lote ${i + 1} de ${grupos.length}: ${grupo.map((it) => it.file.name).join(", ")}`;
      try {
        const fd = new FormData();
        grupo.forEach((it) => fd.append("arquivos", it.file, it.file.name));
        fd.append("tipo", $("#up-tipo").value);
        ["plataforma", "area", "equipamento"].forEach((k) => fd.append(k, $("#up-" + k).value));
        fd.append("observacao", $("#up-obs").value);
        await api("/api/documentos", { method: "POST", body: fd });
        enviados += grupo.length;
      } catch (e) { falhas.push(`${grupo.map((it) => it.caminho).join(", ")}: ${e.message}`); naoEnviados.push(...grupo); }
      if (i % 3 === 2) carregarDocs();
    }
    bt.textContent = "Enviar";
    inputArq.value = ""; inputPasta.value = "";
    if (falhas.length) {
      selecionados = naoEnviados;
      box.innerHTML = `<span style="color:var(--critical)">${num(enviados)} enviado(s); falharam ${falhas.length} lote(s) — clique em Enviar para tentar de novo:<br>${falhas.map(esc).join("<br>")}</span>`;
      bt.disabled = false;
    } else {
      selecionados = []; $("#up-obs").value = "";
      box.textContent = `${num(enviados)} arquivo(s) enviados. O processamento continua no servidor; acompanhe o status na lista abaixo.`;
      bt.disabled = true;
    }
    carregarDocs();
  });
  function alertaInline(el, msg) { el.innerHTML = `<span style="color:var(--critical)">${esc(msg)}</span>`; }

  const tiposDoc = ["documentacao", "backup", "log", "whatsapp", "alarmes", "ocorrencias", "foto", "audio", "video", "outro"];
  $("#filtro-tipo").insertAdjacentHTML("beforeend", tiposDoc.map((t) => `<option value="${t}">${t}</option>`).join(""));
  let tFiltro;
  $("#filtro-docs").addEventListener("input", () => { clearTimeout(tFiltro); tFiltro = setTimeout(carregarDocs, 300); });
  $("#filtro-tipo").addEventListener("change", carregarDocs);

  async function atualizarDuplicados() {
    const bt = $("#btn-apagar-duplicados");
    try {
      const r = await api("/api/duplicados");
      bt.classList.toggle("hidden", !r.quantidade);
      bt.textContent = `Apagar ${num(r.quantidade)} duplicado(s)`;
      bt.dataset.qtd = r.quantidade; bt.dataset.bytes = r.bytes;
    } catch (e) { bt.classList.add("hidden"); }
  }
  $("#btn-apagar-duplicados").addEventListener("click", () => {
    const bt = $("#btn-apagar-duplicados");
    dialogo(`Apagar ${num(+bt.dataset.qtd)} duplicado(s)?`,
      `<p>São arquivos com conteúdo idêntico a outro documento que já está indexado (ex.: o mesmo PDF solto e dentro de um .zip). O original continua na base; só as cópias (${bytes(+bt.dataset.bytes)}) são removidas. Isso não pode ser desfeito.</p><button class="btn perigo" id="confirma-duplicados">Apagar duplicados</button>`);
    $("#confirma-duplicados").addEventListener("click", async (e) => {
      e.target.disabled = true; e.target.textContent = "Apagando…";
      const r = await api("/api/duplicados/apagar", { method: "POST" });
      $("#dialogo").close();
      $("#lista-envio").textContent = `${num(r.apagados)} duplicado(s) apagado(s), ${bytes(r.bytes)} liberados.`;
      carregarDocs();
    });
  });

  async function carregarDocs() {
    atualizarDuplicados();
    const q = new URLSearchParams();
    if ($("#filtro-docs").value) q.set("filtro", $("#filtro-docs").value);
    if ($("#filtro-tipo").value) q.set("tipo", $("#filtro-tipo").value);
    const docs = await api("/api/documentos?" + q);
    const box = $("#tabela-docs");
    if (!docs.length) { box.innerHTML = `<div class="vazio">Nenhum documento. Envie o primeiro acima.</div>`; return; }
    box.innerHTML = `<table class="dados"><thead><tr><th>#</th><th>Nome</th><th>Tipo</th><th>Status</th><th>Plataforma</th><th>Área</th><th>Equipamento</th><th class="num">Trechos</th><th>Enviado</th><th></th></tr></thead><tbody>${docs.map((d) => `
      <tr><td class="num">${d.id}</td><td>${esc(d.nome)}${d.pai_id ? ` <span class="muted small">(de #${d.pai_id})</span>` : ""}${d.observacao ? `<div class="small muted">${esc(d.observacao)}</div>` : ""}${d.erro ? `<div class="small" style="color:var(--critical)">${esc(d.erro)}</div>` : ""}</td>
      <td>${esc(d.tipo)}</td><td><span class="selo ${esc(d.status)}">${esc(d.status)}</span></td><td>${esc(d.plataforma || "")}</td><td>${esc(d.area || "")}</td><td>${esc(d.equipamento || "")}</td>
      <td class="num">${num(d.n_trechos)}</td><td class="small">${dataBR(d.enviado_em)}<div class="muted">${esc(d.enviado_por || "")}</div></td>
      <td style="white-space:nowrap"><button class="btn mini" data-ver="${d.id}">Ver</button> <a class="btn mini" href="/api/documentos/${d.id}/arquivo">Baixar</a> <button class="btn mini" data-repro="${d.id}">Reprocessar</button> <button class="btn mini perigo" data-apagar="${d.id}">Apagar</button></td></tr>`).join("")}</tbody></table>`;
    $$("[data-ver]", box).forEach((b) => b.addEventListener("click", () => verDoc(b.dataset.ver)));
    $$("[data-repro]", box).forEach((b) => b.addEventListener("click", async () => { const fd = new FormData(); await api(`/api/documentos/${b.dataset.repro}/reprocessar`, { method: "POST", body: fd }); carregarDocs(); }));
    $$("[data-apagar]", box).forEach((b) => b.addEventListener("click", () => confirmarApagar(b.dataset.apagar)));
    clearTimeout(estado.pollDocs);
    if (docs.some((d) => d.status === "processando") && estado.abaAtual === "base") estado.pollDocs = setTimeout(carregarDocs, 3000);
  }
  function confirmarApagar(id) {
    dialogo(`Apagar documento #${id}?`, `<p>O arquivo e os trechos indexados serão removidos da base. Isso não pode ser desfeito.</p><button class="btn perigo" id="confirma-apagar">Apagar</button>`);
    $("#confirma-apagar").addEventListener("click", async () => { await api(`/api/documentos/${id}`, { method: "DELETE" }); $("#dialogo").close(); carregarDocs(); });
  }
  async function verDoc(id) {
    const d = await api(`/api/documentos/${id}`);
    dialogo(`#${d.id} ${d.nome}`, `<p class="small muted">${esc(d.tipo)} · ${esc(d.status)} · ${num(d.n_trechos)} trechos · sha256 ${esc((d.sha256 || "").slice(0, 16))}…</p>
      ${d.erro ? `<p style="color:var(--critical)">${esc(d.erro)}</p>` : ""}${d.observacao ? `<p>${esc(d.observacao)}</p>` : ""}
      ${(d.trechos || []).map((t) => `<h3>${esc(t.local || "")}${t.ts ? " · " + esc(t.ts.replace("T", " ")) : ""}</h3><pre>${esc(t.texto)}</pre>`).join("") || `<div class="vazio">Sem texto extraído.</div>`}
      ${d.n_trechos > 30 ? `<p class="muted small">Mostrando os 30 primeiros trechos.</p>` : ""}`);
  }

  // ------------------------------------------------------------------ ocorrências
  $("#form-ocorrencia").addEventListener("submit", async (e) => {
    e.preventDefault();
    const dados = Object.fromEntries(new FormData(e.target).entries());
    Object.keys(dados).forEach((k) => { if (dados[k] === "") delete dados[k]; });
    if (dados.tempo_parada_min) dados.tempo_parada_min = Number(dados.tempo_parada_min);
    try { await api("/api/ocorrencias", { method: "POST", json: dados }); e.target.reset(); carregarOcorrencias(); }
    catch (err) { dialogo("Erro", `<p>${esc(err.message)}</p>`); }
  });
  $("#filtro-oc").addEventListener("input", () => carregarOcorrencias());
  async function carregarOcorrencias() {
    const f = $("#filtro-oc").value.toLowerCase();
    const lista = (await api("/api/ocorrencias")).filter((o) => !f || JSON.stringify(o).toLowerCase().includes(f));
    const box = $("#tabela-oc");
    if (!lista.length) { box.innerHTML = `<div class="vazio">Nenhuma ocorrência registrada.</div>`; return; }
    box.innerHTML = Graf.tabela([
      { t: "Data", v: (o) => dataBR(o.data_hora) }, { t: "Equipamento", v: "equipamento" }, { t: "Tag", v: "tag" }, { t: "Área", v: "area" },
      { t: "Sintoma", v: "sintoma" }, { t: "Causa raiz", v: "causa_raiz" }, { t: "Categoria", v: "categoria_causa" },
      { t: "Parada", v: (o) => duracao(o.tempo_parada_min) }, { t: "Origem", v: "origem" },
      { t: "", html: true, v: (o) => `<button class="btn mini" data-oc="${o.id}">Ver</button>` }], lista);
    $$("[data-oc]", box).forEach((b) => b.addEventListener("click", () => {
      const o = lista.find((x) => String(x.id) === b.dataset.oc);
      const campos = [["Data/hora", dataBR(o.data_hora)], ["Equipamento", o.equipamento], ["Tag", o.tag], ["Área", o.area], ["Plataforma", o.plataforma],
        ["Sintoma", o.sintoma], ["Causa raiz", o.causa_raiz], ["Categoria", o.categoria_causa], ["Ação", o.acao], ["Blocos/parâmetros", o.blocos_parametros],
        ["Tempo de parada", duracao(o.tempo_parada_min)], ["Lição aprendida", o.licao], ["Fontes", o.fontes], ["Registrado por", `${o.autor || "–"} (${o.origem})`]];
      dialogo(`Ocorrência #${o.id}`, `<table class="dados">${campos.map(([k, v]) => `<tr><th>${esc(k)}</th><td style="white-space:pre-wrap">${esc(v || "–")}</td></tr>`).join("")}</table>
        <p><button class="btn perigo" id="apagar-oc">Apagar ocorrência</button></p>`);
      $("#apagar-oc").addEventListener("click", async () => { await api(`/api/ocorrencias/${o.id}`, { method: "DELETE" }); $("#dialogo").close(); carregarOcorrencias(); });
    }));
  }

  // ------------------------------------------------------------------ glossário e notas
  $("#form-termo").addEventListener("submit", async (e) => {
    e.preventDefault();
    await api("/api/glossario", { method: "POST", json: Object.fromEntries(new FormData(e.target).entries()) });
    e.target.reset(); carregarGlossario();
  });
  $("#form-nota").addEventListener("submit", async (e) => {
    e.preventDefault();
    await api("/api/notas", { method: "POST", json: Object.fromEntries(new FormData(e.target).entries()) });
    e.target.reset(); carregarGlossario();
  });
  async function carregarGlossario() {
    const [g, n] = await Promise.all([api("/api/glossario"), api("/api/notas")]);
    $("#tabela-glossario").innerHTML = g.length ? Graf.tabela([{ t: "Termo", v: "termo" }, { t: "Significado", v: "significado" }, { t: "Por", v: "autor" }, { t: "Atualizado", v: (x) => dataBR(x.atualizado_em) }], g) : `<div class="vazio">Glossário vazio.</div>`;
    $("#tabela-notas").innerHTML = n.length ? Graf.tabela([{ t: "Alvo", v: "alvo" }, { t: "Nota", v: "texto" }, { t: "Fonte", v: "fonte" }, { t: "Por", v: "autor" }, { t: "Origem", v: "origem" }, { t: "Data", v: (x) => dataBR(x.criada_em) }], n) : `<div class="vazio">Nenhuma nota.</div>`;
  }

  // ------------------------------------------------------------------ início
  api("/api/me").then(entrar).catch(mostrarLogin);
})();
