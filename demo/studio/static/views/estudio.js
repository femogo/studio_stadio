/* Estudio: lienzo del grafo, panel de nodo/arista, panel inferior y controles de ejecución. */
"use strict";
(() => {
const {h, api, toast, toastErr, modal, confirmDlg, promptDlg, fmtDate, fmtDur, media, pill, store, clone, debounce, form, schemaForm, STATUS_ES} = App;
const NS = "http://www.w3.org/2000/svg";
const W = 184, H = 66, TW = 120, TH = 40;   // nodo normal / terminal
function s(tag, attrs, ...kids) {
  const el = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v); else el.setAttribute(k, v);
  }
  for (const k of kids.flat()) if (k != null) el.append(k instanceof Node ? k : document.createTextNode(String(k)));
  return el;
}
const isTerm = id => id === "__start__" || id === "__end__";
const srcList = e => Array.isArray(e.src) ? e.src : [e.src];
const ACTIVE = ["pending", "running", "pausing"];

App.views.estudio = async (main, parts, params) => {
  let slug = parts[0];
  if (!slug) {
    const last = store.get("ultimo_proyecto", null);
    const ps = await api.get("/projects");
    slug = ps.find(p => p.slug === last)?.slug || ps[0]?.slug;
    if (!slug) {
      main.append(h("div", {class: "page"}, h("p", {class: "empty"}, "No hay flujos todavía."),
        h("div", {class: "row", style: {justifyContent: "center"}}, h("button", {class: "primary", onclick: App.nuevoProyecto}, "+ Nuevo flujo"))));
      return;
    }
    history.replaceState(null, "", `#/estudio/${slug}`);
  }
  store.set("ultimo_proyecto", slug);
  const st = new Studio(main, slug, params.hilo);
  await st.init();
  return st;
};

class Studio {
  constructor(main, slug, hilo) {
    this.main = main; this.slug = slug; this.threadId = hilo || null;
    this.undo = []; this.redo = []; this.sel = new Set(); this.selEdge = null;
    this.view = {nodos: {}, aristas: new Set(), run: null}; this.logs = {}; this.partial = {}; this.tokens = {};
    this.events = []; this.interrupts = []; this.cp = null; this.stepMode = false; this.validation = null;
    this.rtab = "config"; this.btab = store.get("btab", "salida");
    this.off = []; this.inner = {}; this.xray = store.get("xray", false);
    this.mode = store.get("modo_" + slug, "grafo"); this.assistants = []; this.assistantId = store.get("asistente_" + slug, "");
  }
  /* ---------------------------------------------------------------- carga */
  async init() {
    const [p, mods] = await Promise.all([api.get(`/projects/${this.slug}`), App.loadModules(true)]);
    this.p = p; this.mods = Object.fromEntries(mods.map(m => [m.file, m]));
    this.p.layout = this.p.layout || {zoom: 1, pan: [0, 0]};
    this.build();
    await this.loadAssistants();
    await this.loadThreads();
    this.render(); this.fitIfNeeded();
    this.validate();
    this.off.push(App.sse.on(ev => this.onEvent(ev)));
    const key = e => this.onKey(e);
    document.addEventListener("keydown", key);
    this.off.push(() => document.removeEventListener("keydown", key));
    const rs = () => this.renderMinimap();
    window.addEventListener("resize", rs);
    this.off.push(() => window.removeEventListener("resize", rs));
  }
  destroy() { for (const f of this.off) f(); clearTimeout(this.saveT); if (this.dirty) this.save(); }

  async loadAssistants() {
    try { this.assistants = await api.get(`/projects/${this.slug}/assistants`); } catch { this.assistants = []; }
    if (!this.assistants.some(a => a.assistant_id === this.assistantId)) this.assistantId = "";
    this.asstSel.replaceChildren(h("option", {value: ""}, "(sin agente)"),
      ...this.assistants.map(a => h("option", {value: a.assistant_id}, `${a.name} · v${a.active_version}`)),
      h("option", {value: "__gestionar__"}, "Gestionar agentes…"));
    this.asstSel.value = this.assistantId;
  }
  async loadThreads(select) {
    this.threads = await api.get(`/threads?project=${this.slug}`);
    if (select) this.threadId = select;
    if (this.threadId && !this.threads.some(t => t.thread_id === this.threadId)) this.threadId = null;
    if (!this.threadId && this.threads[0]) this.threadId = this.threads[0].thread_id;
    this.paintThreadSel();
    await this.loadThread();
  }
  async loadThread() {
    this.view = {nodos: {}, aristas: new Set(), run: null}; this.logs = {}; this.partial = {}; this.tokens = {};
    this.events = []; this.interrupts = []; this.cp = null; this.state = null; this.hist = []; this.inner = {}; this.msgs = [];
    if (this.threadId) {
      history.replaceState(null, "", `#/estudio/${this.slug}?hilo=${this.threadId}`);
      const [v, evs] = await Promise.all([api.get(`/threads/${this.threadId}/view`), api.get(`/threads/${this.threadId}/events?limit=3000`)]);
      this.view = {nodos: v.nodos, aristas: new Set(v.aristas_recorridas), run: v.run};
      this.events = evs;
      for (const ev of evs) {
        if (ev.run_id !== v.run?.run_id) continue;
        if (ev.type === "node.log" && !ev.ns) (this.logs[ev.node] = this.logs[ev.node] || []).push(ev.data.line);
        if (ev.ns) this.trackInner(ev);
      }
      await Promise.all([this.loadState(), this.loadHistory(), this.loadMessages()]);
    } else history.replaceState(null, "", `#/estudio/${this.slug}`);
    this.paintControls(); this.render(); this.paintRight(); this.paintBottom();
  }
  async loadState() {
    if (!this.threadId) return;
    try {
      this.state = await api.get(`/threads/${this.threadId}/state`);
      this.interrupts = this.state.interrupts || [];
    } catch (e) { this.state = null; this.interrupts = []; if (e.status !== 400) toastErr(e); }
  }
  async loadHistory() {
    if (!this.threadId) return;
    try { this.hist = await api.get(`/threads/${this.threadId}/history`); } catch { this.hist = []; }
  }
  get hasChat() { return this.validation?.canales?.messages === "messages"; }
  async loadMessages() {
    this.msgs = [];
    if (!this.threadId || !this.hasChat) return;
    try { this.msgs = await api.get(`/threads/${this.threadId}/messages`); } catch { this.msgs = []; }
  }
  trackInner(ev) {
    const top = (ev.ns || "").split("|")[0].split(":")[0];
    if (!top) return;
    const m = this.inner[top] = this.inner[top] || {};
    const inner = ev.type === "token" ? ev.data?.inner : ev.node;
    if (!inner) return;
    if (ev.type === "node.start" && !("esperando_gpu" in (ev.data || {}))) m[inner] = "running";
    else if (ev.type === "node.end") m[inner] = ev.data.status;
    else if (ev.type === "token") m[inner] = "running";
    else if (ev.type === "interrupt") m[inner] = "interrupted";
  }
  innerNodes(id) {
    const n = this.p.nodes[id], mod = n && this.mods[n.module];
    if (!mod) return null;
    if (mod.meta.kind === "subgraph") return this.validation?.subgrafos?.[id]?.nodos || [];
    if (mod.meta.kind === "agent") return ["llm", "herramientas"];
    return null;
  }
  get run() { return this.view.run; }
  get running() { return this.run && ACTIVE.includes(this.run.status); }

  /* ---------------------------------------------------------------- estructura */
  build() {
    this.nameIn = h("input", {class: "pname", value: this.p.name, title: "Nombre del flujo", onchange: () => this.change(p => { p.name = this.nameIn.value.trim() || p.name; })});
    this.threadSel = h("select", {title: "Hilo", onchange: () => { this.threadId = this.threadSel.value || null; this.loadThread(); }});
    this.btn = {
      start: h("button", {class: "primary", onclick: () => this.startDialog(false), title: "Iniciar con una entrada"}, "▶ Iniciar"),
      step: h("button", {onclick: () => this.stepAction(), title: "Paso a paso (para antes de cada nodo)"}, "⏭ Paso"),
      pause: h("button", {onclick: () => this.pause(), title: "Pausar al terminar el superstep"}, "⏸ Pausar"),
      cont: h("button", {onclick: () => this.cont(), title: "Continuar"}, "⏵ Continuar"),
      stop: h("button", {class: "danger", onclick: () => this.stop(), title: "Detener"}, "■ Detener"),
      undo: h("button", {class: "sm ghost", onclick: () => this.doUndo(), title: "Deshacer (Ctrl+Z)"}, "↶"),
      redo: h("button", {class: "sm ghost", onclick: () => this.doRedo(), title: "Rehacer (Ctrl+Y)"}, "↷"),
    };
    this.asstSel = h("select", {title: "Agente (configuración versionada del flujo)", id: "asstSel", style: {maxWidth: "190px"}, onchange: () => {
      if (this.asstSel.value === "__gestionar__") { this.asstSel.value = this.assistantId; App.go(`#/asistentes?proyecto=${this.slug}`); return; }
      this.assistantId = this.asstSel.value; store.set("asistente_" + this.slug, this.assistantId);
    }});
    this.modeBtn = h("button", {class: "sm", id: "modoChat", title: "Cambiar entre el grafo y la conversación", onclick: () => this.setMode(this.mode === "chat" ? "grafo" : "chat")}, "💬 Chat");
    this.statusPill = h("span");
    this.progBar = h("div", {class: "bar"}, h("i", {}));
    this.progTxt = h("span", {class: "mut mono", style: {fontSize: "11px", minWidth: "34px"}}, "");
    const bar = h("div", {class: "sbar"},
      this.nameIn, this.threadSel,
      h("button", {class: "sm", title: "Hilo nuevo", onclick: () => this.newThread()}, "+ hilo"),
      this.asstSel,
      h("span", {class: "sepv"}),
      this.btn.start, this.btn.step, this.btn.pause, this.btn.cont, this.btn.stop,
      h("div", {class: "prog"}, this.statusPill, this.progBar, this.progTxt),
      h("button", {class: "sm", onclick: () => this.validateDlg()}, "Validar"),
      h("button", {class: "sm", id: "exportarBtn", onclick: () => App.f3.exportDlg(this.slug)}, "Exportar ▾"),
      this.btn.undo, this.btn.redo, this.modeBtn);
    // lienzo
    this.svg = s("svg", {class: "canvas"});
    this.vp = s("g");
    this.gEdges = s("g"); this.gNodes = s("g"); this.gOver = s("g");
    this.svg.append(s("defs", {},
      s("pattern", {id: "gridp", width: 20, height: 20, patternUnits: "userSpaceOnUse", class: "gridpat"}, s("circle", {cx: 1, cy: 1, r: 1})),
      s("marker", {id: "arr", viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: "auto-start-reverse"},
        s("path", {d: "M0,0 L10,5 L0,10 z", fill: "var(--mut)"}))));
    this.gridRect = s("rect", {x: -50000, y: -50000, width: 100000, height: 100000, fill: "url(#gridp)"});
    this.vp.append(this.gridRect, this.gEdges, this.gNodes, this.gOver);
    this.svg.append(this.vp);
    this.zoomLbl = h("div", {class: "zoomlbl"});
    this.minimap = h("div", {class: "minimap", title: "Minimapa (clic para centrar)"});
    this.palette = h("div", {class: "palette hidden"});
    this.canvas = h("div", {class: "canvas-wrap"},
      this.svg,
      h("div", {class: "canvas-tools"},
        h("button", {class: "sm primary", onclick: () => this.togglePalette()}, "+ Añadir módulo"),
        h("button", {class: "sm", onclick: () => this.fit(), title: "Ajustar a la vista"}, "⤢ Ajustar"),
        h("button", {class: "sm", onclick: () => this.autoLayout(), title: "Ordenar por capas"}, "☷ Ordenar"),
        this.xrayBtn = h("button", {class: "sm" + (this.xray ? " primary" : ""), id: "xrayBtn", title: "Ver los nodos internos de subgrafos y agentes", onclick: () => {
          this.xray = !this.xray; store.set("xray", this.xray); this.xrayBtn.classList.toggle("primary", this.xray); this.render();
        }}, "xray")),
      this.palette, this.zoomLbl, this.minimap);
    this.bindCanvas();
    // panel derecho
    this.rp = h("div", {class: "rpanel c"});
    // panel inferior
    this.bp = h("div", {class: "bpanel" + (store.get("bp_c", false) ? " c" : "")});
    this.bp.style.height = store.get("bp_c", false) ? "auto" : store.get("bp_h", 260) + "px";
    this.chat = h("div", {class: "chat hidden"});
    this.root = h("div", {class: "studio"}, bar, h("div", {class: "smid"}, this.canvas, this.chat, this.rp), this.bp);
    this.main.append(this.root);
    this.buildBottom();
  }

  /* ---------------------------------------------------------------- persistencia del proyecto */
  change(fn, {noUndo = false} = {}) {
    if (!noUndo) {
      this.undo.push(JSON.stringify({nodes: this.p.nodes, edges: this.p.edges, name: this.p.name, layout: this.p.layout, state_schema: this.p.state_schema}));
      if (this.undo.length > 50) this.undo.shift();
      this.redo = [];
    }
    fn(this.p);
    this.render(); this.paintUndo();
    this.scheduleSave();
  }
  restore(snap) {
    const o = JSON.parse(snap);
    Object.assign(this.p, o);
    this.sel = new Set([...this.sel].filter(id => this.p.nodes[id]));
    if (this.selEdge && !this.p.edges.some(e => e.id === this.selEdge)) this.selEdge = null;
    this.nameIn.value = this.p.name;
    this.render(); this.paintRight(); this.paintUndo(); this.scheduleSave();
  }
  snap() { return JSON.stringify({nodes: this.p.nodes, edges: this.p.edges, name: this.p.name, layout: this.p.layout, state_schema: this.p.state_schema}); }
  doUndo() { if (!this.undo.length) return; this.redo.push(this.snap()); this.restore(this.undo.pop()); }
  doRedo() { if (!this.redo.length) return; this.undo.push(this.snap()); this.restore(this.redo.pop()); }
  paintUndo() { this.btn.undo.disabled = !this.undo.length; this.btn.redo.disabled = !this.redo.length; }
  scheduleSave() { this.dirty = true; clearTimeout(this.saveT); this.saveT = setTimeout(() => this.save(), 800); }
  async save() {
    if (this.saving) { this.scheduleSave(); return; }
    this.saving = true; this.dirty = false;
    try {
      const r = await api.put(`/projects/${this.slug}`, this.p);
      this.p.version = r.version; this.p.updated = r.updated;
      this.validate();
    } catch (e) {
      if (e.status === 409) {
        toast("El flujo cambió en otra ventana: se recarga la versión actual", "err");
        this.p = e.body.current; this.nameIn.value = this.p.name; this.undo = []; this.redo = []; this.render(); this.paintUndo();
      } else toastErr(e);
    } finally { this.saving = false; }
  }
  async validate() {
    const had = this.hasChat;
    try { this.validation = await api.get(`/projects/${this.slug}/validate`); } catch { this.validation = null; }
    this.modeBtn.classList.toggle("hidden", !this.hasChat);
    if (!had && this.hasChat && this.threadId) await this.loadMessages();
    this.applyMode();
    this.render();
  }
  setMode(m) { this.mode = m; store.set("modo_" + this.slug, m); this.applyMode(); }
  applyMode() {
    const chat = this.mode === "chat" && this.hasChat;
    this.canvas.classList.toggle("hidden", chat);
    this.chat.classList.toggle("hidden", !chat);
    this.modeBtn.textContent = chat ? "◈ Grafo" : "💬 Chat";
    if (chat) this.paintChat(); else this.renderMinimap();
  }
  async validateDlg() {
    if (this.dirty) await this.save();
    await this.validate();
    const v = this.validation || {errores: [], avisos: [], canales: {}};
    modal({title: "Validación del flujo", body: h("div", {},
      h("h3", {}, `Errores (${v.errores.length})`), v.errores.length ? h("ul", {}, v.errores.map(e => h("li", {style: {color: "var(--red)"}}, e.msg))) : h("p", {class: "mut"}, "Ninguno."),
      h("h3", {style: {marginTop: "12px"}}, `Avisos (${v.avisos.length})`), v.avisos.length ? h("ul", {}, v.avisos.map(e => h("li", {}, e.msg))) : h("p", {class: "mut"}, "Ninguno."),
      h("h3", {style: {marginTop: "12px"}}, "Canales del estado"),
      h("dl", {class: "kv"}, Object.entries(v.canales).map(([k, r]) => [h("dt", {}, k), h("dd", {}, r)])))});
  }

  /* ---------------------------------------------------------------- geometría */
  pos(id) {
    if (id === "__start__") return this.p.layout.start || this.defaultTerm("start");
    if (id === "__end__") return this.p.layout.end || this.defaultTerm("end");
    const n = this.p.nodes[id]; return {x: n.x ?? 0, y: n.y ?? 0};
  }
  defaultTerm(which) {
    const ns = Object.values(this.p.nodes);
    const minX = Math.min(...ns.map(n => n.x ?? 0), 200), maxX = Math.max(...ns.map(n => n.x ?? 0), 200);
    const avgY = ns.length ? ns.reduce((a, n) => a + (n.y ?? 0), 0) / ns.length : 150;
    return which === "start" ? {x: minX - 170, y: avgY + 12} : {x: maxX + 240, y: avgY + 12};
  }
  size(id) { return isTerm(id) ? [TW, TH] : [W, H]; }
  outPt(id) { const p = this.pos(id), [w, hh] = this.size(id); return [p.x + w, p.y + hh / 2]; }
  inPt(id) { const p = this.pos(id), [, hh] = this.size(id); return [p.x, p.y + hh / 2]; }
  toWorld(cx, cy) {
    const r = this.svg.getBoundingClientRect(), z = this.p.layout.zoom || 1, [px, py] = this.p.layout.pan || [0, 0];
    return [(cx - r.left - px) / z, (cy - r.top - py) / z];
  }
  applyVP() {
    const z = this.p.layout.zoom || 1, [px, py] = this.p.layout.pan || [0, 0];
    this.vp.setAttribute("transform", `translate(${px},${py}) scale(${z})`);
    this.zoomLbl.textContent = `${Math.round(z * 100)} %`;
    this.renderMinimap();
  }
  allIds() { return ["__start__", ...Object.keys(this.p.nodes), "__end__"]; }
  bbox(ids = this.allIds()) {
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const id of ids) { const p = this.pos(id), [w, hh] = this.size(id); x0 = Math.min(x0, p.x); y0 = Math.min(y0, p.y); x1 = Math.max(x1, p.x + w); y1 = Math.max(y1, p.y + hh); }
    return {x0, y0, x1, y1};
  }
  fit() {
    const r = this.svg.getBoundingClientRect(); if (!r.width) return;
    const b = this.bbox(), pad = 60;
    const z = Math.min(1.5, Math.max(0.2, Math.min((r.width - pad * 2) / (b.x1 - b.x0), (r.height - pad * 2) / (b.y1 - b.y0))));
    this.p.layout.zoom = z;
    this.p.layout.pan = [(r.width - (b.x1 - b.x0) * z) / 2 - b.x0 * z, (r.height - (b.y1 - b.y0) * z) / 2 - b.y0 * z];
    this.applyVP(); this.scheduleSave();
  }
  fitIfNeeded() {
    requestAnimationFrame(() => {
      const r = this.svg.getBoundingClientRect(), b = this.bbox(), z = this.p.layout.zoom || 1, [px, py] = this.p.layout.pan || [0, 0];
      const visible = b.x1 * z + px > 0 && b.x0 * z + px < r.width && b.y1 * z + py > 0 && b.y0 * z + py < r.height;
      if (!visible || (!this.p.layout.pan?.[0] && !this.p.layout.pan?.[1])) this.fit(); else this.applyVP();
    });
  }
  autoLayout() {
    const ids = Object.keys(this.p.nodes), succ = {};
    for (const e of this.p.edges) for (const sId of srcList(e)) (succ[sId] = succ[sId] || []).push(e.dst);
    const rank = {"__start__": 0}, seen = new Set(), order = [];
    const visit = (id, onStack) => {     // ranks por camino más largo ignorando aristas de retroceso
      if (onStack.has(id)) return;
      onStack.add(id);
      for (const d of succ[id] || []) {
        if (onStack.has(d)) continue;
        if ((rank[d] ?? -1) < rank[id] + 1) { rank[d] = rank[id] + 1; visit(d, onStack); }
      }
      onStack.delete(id); if (!seen.has(id)) { seen.add(id); order.push(id); }
    };
    visit("__start__", new Set());
    let maxR = Math.max(0, ...Object.values(rank));
    for (const id of ids) if (rank[id] == null) rank[id] = ++maxR;
    const byR = {};
    for (const id of ids) (byR[rank[id]] = byR[rank[id]] || []).push(id);
    this.change(p => {
      for (const [r, list] of Object.entries(byR)) list.forEach((id, i) => { p.nodes[id].x = 40 + r * 240; p.nodes[id].y = 40 + i * 120 - (list.length - 1) * 60 + 200; });
      const endR = Math.max(...ids.map(i => rank[i]), 0) + 1;
      p.layout.start = {x: 40 - 140, y: 200 + 12}; p.layout.end = {x: 40 + endR * 240, y: 200 + 12};
    });
    this.fit();
  }

  /* ---------------------------------------------------------------- render */
  nodeStatus(id) {
    const v = this.view.nodos[id] || {};
    let stc = v.status || "pending";
    if (this.run?.status === "pausing" && stc === "running") stc = "pausing";
    if (this.interrupts.some(i => i.node === id)) stc = "interrupted";
    else if (this.state && this.run?.status === "interrupted" && this.run?.motivo === "breakpoint" && (this.state.next || []).includes(id)) stc = "breakpoint";
    return stc;
  }
  render() {
    if (!this.p) return;
    const errNodes = new Set((this.validation?.errores || []).map(e => e.node).filter(Boolean));
    // aristas
    this.gEdges.replaceChildren();
    const running = new Set(Object.entries(this.view.nodos).filter(([, v]) => v.status === "running" || v.status === "waiting_gpu").map(([k]) => k));
    this.p.edges.forEach((e, idx) => {
      const srcs = srcList(e).filter(x => x === "__start__" || this.p.nodes[x]);
      if (!(e.dst === "__end__" || this.p.nodes[e.dst])) return;
      const cls = ["gedge", e.cond ? "cond" : "", this.view.aristas.has(e.id) ? "done" : "", running.has(e.dst) && this.view.aristas.has(e.id) ? "active" : "", this.selEdge === e.id ? "sel" : ""].join(" ");
      const g = s("g", {"data-edge": e.id});
      let mid = null;
      for (const sId of srcs) {
        const [x1, y1] = this.outPt(sId), [x2, y2] = this.inPt(e.dst);
        const dx = Math.max(40, Math.abs(x2 - x1) / 2);
        const back = x2 < x1 + 20;
        const d = back ? `M${x1},${y1} C${x1 + 80},${y1 + 90} ${x2 - 80},${y2 + 90} ${x2},${y2}` : `M${x1},${y1} C${x1 + dx},${y1} ${x2 - dx},${y2} ${x2},${y2}`;
        g.append(s("path", {d, class: "gedge-hit"}), s("path", {d, class: cls, "marker-end": "url(#arr)"}));
        if (!mid) mid = back ? [(x1 + x2) / 2, Math.max(y1, y2) + 68] : [(x1 + x2) / 2, (y1 + y2) / 2];
      }
      const lbl = e.label || (e.cond ? (e.cond === "else" ? "else" : e.cond.length > 26 ? e.cond.slice(0, 25) + "…" : e.cond) : srcs.length > 1 ? "esperar a todas" : "");
      if (lbl && mid) {
        const t = s("text", {x: mid[0], y: mid[1] - 4, "text-anchor": "middle", class: "elabel"}, (this.p.edges.filter(x => srcList(x)[0] === srcs[0] && x.cond).length > 1 && e.cond ? `${idx + 1}· ` : "") + lbl);
        g.append(s("rect", {x: mid[0] - lbl.length * 3 - 6, y: mid[1] - 16, width: lbl.length * 6 + 12, height: 16, rx: 4, class: "elabel-bg"}), t);
      }
      g.addEventListener("mousedown", ev => { ev.stopPropagation(); this.selectEdge(e.id); });
      this.gEdges.append(g);
    });
    // nodos
    this.gNodes.replaceChildren();
    for (const id of this.allIds()) {
      const p = this.pos(id), [w, hh] = this.size(id);
      const n = this.p.nodes[id], mod = n ? this.mods[n.module] : null;
      const stc = isTerm(id) ? "term" : this.nodeStatus(id);
      const v = this.view.nodos[id] || {};
      const g = s("g", {class: `gnode ${stc} ${this.sel.has(id) ? "sel" : ""} ${errNodes.has(id) || (n && (!mod || mod.error)) ? "err-mod" : ""} ${isTerm(id) ? "term" : ""}`,
        transform: `translate(${p.x},${p.y})`, "data-node": id});
      g.append(s("rect", {class: "box", width: w, height: hh, rx: 10}));
      if (isTerm(id)) {
        g.append(s("text", {x: w / 2, y: hh / 2 + 4, "text-anchor": "middle", class: "ttl"}, id === "__start__" ? "▶ Inicio" : "■ Fin"));
      } else {
        g.append(s("text", {x: 12, y: 26, class: "ico"}, mod?.meta.icon || "▣"),
          s("text", {x: 36, y: 25, class: "ttl"}, (n.label || id).slice(0, 20)),
          s("text", {x: 36, y: 42, class: "sub"}, `${id} · ${(mod?.meta.name || n.module || "?").slice(0, 18)}`));
        const prog = v.status === "success" ? 1 : (v.progress || 0);
        g.append(s("rect", {class: "pbar", x: 10, y: hh - 12, width: w - 20, height: 4, rx: 2}),
          s("rect", {class: "pfill", x: 10, y: hh - 12, width: (w - 20) * Math.max(0, Math.min(1, prog)), height: 4, rx: 2}));
        if (v.count) {
          const txt = `×${v.count}`;
          g.append(s("rect", {class: "badgebg", x: w - 14 - txt.length * 6, y: -8, width: txt.length * 6 + 10, height: 16, rx: 8}),
            s("text", {class: "badge", x: w - 9 - txt.length * 6, y: 4}, txt));
        }
        const bp = n.breakpoint || {};
        if (bp.before) g.append(s("text", {class: "bp", x: 4, y: -4}, "◐"));
        if (bp.after) g.append(s("text", {class: "bp", x: w - 12, y: hh + 12}, "◑"));
        if (v.status === "waiting_gpu") g.append(s("text", {class: "sub", x: 36, y: hh + 14}, "esperando GPU"));
        if (v.cached || v.status === "cached") g.append(s("text", {class: "cachedmark", x: w - 22, y: 25}, "⚡"));
        if (n.defer) g.append(s("text", {class: "sub", x: 12, y: hh + 14}, "diferido"));
        const inner = this.xray ? this.innerNodes(id) : null;
        if (inner && inner.length) {
          const gx = s("g", {class: "xray"}), st = this.inner[id] || {};
          inner.forEach((nm, i) => {
            const cw = Math.min(84, (w - 8) / Math.min(inner.length, 2) - 4), cx = 4 + (i % 2) * (cw + 4), cy = hh + 6 + Math.floor(i / 2) * 22;
            gx.append(s("rect", {x: cx, y: cy, width: cw, height: 18, rx: 5, class: st[nm] || "pending"}),
              s("text", {x: cx + 6, y: cy + 13}, nm.slice(0, 13)));
          });
          g.append(gx);
        }
      }
      if (id !== "__start__") g.append(s("circle", {class: "port in", cx: 0, cy: hh / 2, r: 6, "data-port": "in"}));
      if (id !== "__end__") g.append(s("circle", {class: "port out", cx: w, cy: hh / 2, r: 6, "data-port": "out"}));
      this.gNodes.append(g);
    }
    this.applyVP();
    this.paintProgress();
  }
  renderMinimap() {
    if (!this.p || !this.minimap.isConnected) return;
    const b = this.bbox(), r = this.svg.getBoundingClientRect(), z = this.p.layout.zoom || 1, [px, py] = this.p.layout.pan || [0, 0];
    const vx0 = -px / z, vy0 = -py / z, vx1 = vx0 + r.width / z, vy1 = vy0 + r.height / z;
    const X0 = Math.min(b.x0, vx0) - 40, Y0 = Math.min(b.y0, vy0) - 40, X1 = Math.max(b.x1, vx1) + 40, Y1 = Math.max(b.y1, vy1) + 40;
    const m = s("svg", {viewBox: `${X0} ${Y0} ${X1 - X0} ${Y1 - Y0}`, preserveAspectRatio: "xMidYMid meet"});
    for (const id of this.allIds()) { const p = this.pos(id), [w, hh] = this.size(id); m.append(s("rect", {x: p.x, y: p.y, width: w, height: hh, rx: 8, fill: this.sel.has(id) ? "var(--gold)" : "var(--off)"})); }
    m.append(s("rect", {x: vx0, y: vy0, width: vx1 - vx0, height: vy1 - vy0, fill: "none", stroke: "var(--gold)", "stroke-width": (X1 - X0) / 180}));
    m.addEventListener("mousedown", ev => {
      const rr = m.getBoundingClientRect(), sc = Math.max((X1 - X0) / rr.width, (Y1 - Y0) / rr.height);
      const ox = (rr.width - (X1 - X0) / sc) / 2, oy = (rr.height - (Y1 - Y0) / sc) / 2;
      const wx = X0 + (ev.clientX - rr.left - ox) * sc, wy = Y0 + (ev.clientY - rr.top - oy) * sc;
      this.p.layout.pan = [r.width / 2 - wx * z, r.height / 2 - wy * z]; this.applyVP(); this.scheduleSave();
    });
    this.minimap.replaceChildren(m);
  }
  paintProgress() {
    let tot = 0, done = 0;
    for (const [id, n] of Object.entries(this.p.nodes)) {
      const w = this.mods[n.module]?.meta.weight ?? 1, v = this.view.nodos[id];
      if (!v) continue;
      tot += w; done += w * (v.status === "success" ? 1 : v.progress || 0);
    }
    let frac = tot ? done / tot : 0;
    if (this.run?.status === "success") frac = 1;
    this.progBar.firstChild.style.width = `${Math.round(frac * 100)}%`;
    this.progBar.classList.toggle("ok", this.run?.status === "success");
    this.progTxt.textContent = this.run ? `${Math.round(frac * 100)} %` : "";
    if (this.run) App.setFootProgress(this.run.run_id, frac);
  }
  paintThreadSel() {
    this.threadSel.replaceChildren(...(this.threads.length ? this.threads.map(t => h("option", {value: t.thread_id}, `${t.title || t.thread_id.slice(0, 8)} · ${STATUS_ES[t.status] || t.status}`))
      : [h("option", {value: ""}, "(sin hilos)")]));
    this.threadSel.value = this.threadId || "";
  }
  paintControls() {
    const r = this.run, active = this.running;
    const hasInt = this.interrupts.length > 0;
    const pendingNext = (this.state?.next || []).length > 0;
    this.btn.start.disabled = false;   // con una ejecución activa, el diálogo ofrece la estrategia de double-texting
    this.btn.step.disabled = active;
    this.btn.step.classList.toggle("primary", this.stepMode);
    this.btn.pause.disabled = !(r && r.status === "running");
    this.btn.cont.disabled = active || !this.threadId || hasInt || !pendingNext;
    this.btn.stop.disabled = !active;
    this.statusPill.replaceChildren(r ? pill(r.status, (STATUS_ES[r.status] || r.status) + (r.status === "interrupted" && r.motivo ? ` (${r.motivo})` : "") +
      (r.status === "pausing" ? " — esperando a que termine el superstep" : "")) : h("span", {class: "mut", style: {fontSize: "12px"}}, this.threadId ? "sin ejecuciones" : "sin hilo"));
    this.paintUndo();
  }

  /* ---------------------------------------------------------------- interacción con el lienzo */
  bindCanvas() {
    const svg = this.svg;
    svg.addEventListener("wheel", e => {
      e.preventDefault();
      const z0 = this.p.layout.zoom || 1, z = Math.max(0.15, Math.min(2.5, z0 * (e.deltaY < 0 ? 1.1 : 1 / 1.1)));
      const r = svg.getBoundingClientRect(), cx = e.clientX - r.left, cy = e.clientY - r.top, [px, py] = this.p.layout.pan || [0, 0];
      this.p.layout.pan = [cx - (cx - px) * z / z0, cy - (cy - py) * z / z0]; this.p.layout.zoom = z;
      this.applyVP(); this.scheduleSave();
    }, {passive: false});
    svg.addEventListener("mousedown", e => this.onDown(e));
    svg.addEventListener("dblclick", e => { const g = e.target.closest("[data-node]"); if (g && !isTerm(g.dataset.node)) { this.rtab = "config"; this.paintRight(); } });
    this.canvas.addEventListener("dragover", e => { if (e.dataTransfer.types.includes("text/x-modulo")) e.preventDefault(); });
    this.canvas.addEventListener("drop", e => {
      const f = e.dataTransfer.getData("text/x-modulo"); if (!f) return;
      e.preventDefault(); const [x, y] = this.toWorld(e.clientX, e.clientY); this.addNode(f, x - W / 2, y - H / 2);
    });
  }
  onDown(e) {
    if (e.button !== 0) return;
    const port = e.target.closest("[data-port]"), gnode = e.target.closest("[data-node]");
    const snap = App.state.settings?.snap || 10;
    const [wx, wy] = this.toWorld(e.clientX, e.clientY);
    if (port && port.dataset.port === "out") {           // crear arista
      const src = gnode.dataset.node, [x1, y1] = this.outPt(src);
      const ghost = s("path", {class: "ghost-edge"}); this.gOver.append(ghost);
      const mv = ev => { const [x2, y2] = this.toWorld(ev.clientX, ev.clientY); ghost.setAttribute("d", `M${x1},${y1} C${x1 + 60},${y1} ${x2 - 60},${y2} ${x2},${y2}`); };
      const up = ev => {
        removeEventListener("mousemove", mv); removeEventListener("mouseup", up); ghost.remove();
        const tgt = document.elementFromPoint(ev.clientX, ev.clientY)?.closest?.("[data-node]");
        if (tgt && tgt.dataset.node !== "__start__") this.addEdge(src, tgt.dataset.node);
      };
      addEventListener("mousemove", mv); addEventListener("mouseup", up);
      e.preventDefault(); return;
    }
    if (gnode) {                                          // seleccionar y arrastrar
      const id = gnode.dataset.node;
      if (e.shiftKey) { this.sel.has(id) ? this.sel.delete(id) : this.sel.add(id); }
      else if (!this.sel.has(id)) this.sel = new Set([id]);
      this.selEdge = null;
      const ids = [...this.sel], start = ids.map(i => ({...this.pos(i)})), before = this.snap();
      let moved = false;
      const mv = ev => {
        const [x, y] = this.toWorld(ev.clientX, ev.clientY), dx = x - wx, dy = y - wy;
        if (!moved && Math.hypot(dx, dy) < 3) return;
        moved = true;
        ids.forEach((i, k) => this.setPos(i, Math.round((start[k].x + dx) / snap) * snap, Math.round((start[k].y + dy) / snap) * snap));
        this.render();
      };
      const up = () => {
        removeEventListener("mousemove", mv); removeEventListener("mouseup", up);
        if (moved) { this.undo.push(before); if (this.undo.length > 50) this.undo.shift(); this.redo = []; this.paintUndo(); this.scheduleSave(); }
        this.render(); this.paintRight();
      };
      addEventListener("mousemove", mv); addEventListener("mouseup", up);
      this.render(); e.preventDefault(); return;
    }
    if (e.shiftKey) {                                     // selección por recuadro
      const rect = s("rect", {class: "selrect"}); this.gOver.append(rect);
      const mv = ev => {
        const [x, y] = this.toWorld(ev.clientX, ev.clientY);
        rect.setAttribute("x", Math.min(x, wx)); rect.setAttribute("y", Math.min(y, wy));
        rect.setAttribute("width", Math.abs(x - wx)); rect.setAttribute("height", Math.abs(y - wy));
      };
      const up = ev => {
        removeEventListener("mousemove", mv); removeEventListener("mouseup", up); rect.remove();
        const [x, y] = this.toWorld(ev.clientX, ev.clientY), x0 = Math.min(x, wx), x1 = Math.max(x, wx), y0 = Math.min(y, wy), y1 = Math.max(y, wy);
        for (const id of Object.keys(this.p.nodes)) { const p = this.pos(id); if (p.x >= x0 && p.x + W <= x1 && p.y >= y0 && p.y + H <= y1) this.sel.add(id); }
        this.render(); this.paintRight();
      };
      addEventListener("mousemove", mv); addEventListener("mouseup", up); e.preventDefault(); return;
    }
    // desplazar el fondo
    const [px, py] = this.p.layout.pan || [0, 0], sx = e.clientX, sy = e.clientY;
    let moved = false;
    const mv = ev => { moved = true; this.p.layout.pan = [px + ev.clientX - sx, py + ev.clientY - sy]; this.applyVP(); };
    const up = () => {
      removeEventListener("mousemove", mv); removeEventListener("mouseup", up);
      if (moved) this.scheduleSave(); else { this.sel.clear(); this.selEdge = null; this.render(); this.paintRight(); }
    };
    addEventListener("mousemove", mv); addEventListener("mouseup", up);
    this.palette.classList.add("hidden");
  }
  setPos(id, x, y) {
    if (id === "__start__") this.p.layout.start = {x, y};
    else if (id === "__end__") this.p.layout.end = {x, y};
    else { this.p.nodes[id].x = x; this.p.nodes[id].y = y; }
  }
  onKey(e) {
    if (!this.root.isConnected) return;
    const tag = (e.target.tagName || "").toLowerCase();
    if (["input", "textarea", "select"].includes(tag) || e.target.closest?.(".CodeMirror") || document.querySelector(".modal-bg")) return;
    const k = e.key.toLowerCase(), mod = e.ctrlKey || e.metaKey;
    if (mod && k === "z" && !e.shiftKey) { e.preventDefault(); this.doUndo(); }
    else if (mod && (k === "y" || (k === "z" && e.shiftKey))) { e.preventDefault(); this.doRedo(); }
    else if (mod && k === "c") { this.copy(); }
    else if (mod && k === "v") { e.preventDefault(); this.paste(); }
    else if (k === "delete" || k === "backspace") { e.preventDefault(); this.deleteSel(); }
    else if (k === "escape") { this.sel.clear(); this.selEdge = null; this.palette.classList.add("hidden"); this.render(); this.paintRight(); }
  }

  /* ---------------------------------------------------------------- edición del grafo */
  newNodeId(base) {
    base = (base || "nodo").replace(/\.py$/, "").replace(/[^a-z0-9_]/g, "_").replace(/^[^a-z]+/, "") || "nodo";
    base = base.slice(0, 34);
    const used = new Set([...Object.keys(this.p.nodes), ...Object.keys(this.validation?.canales || {})]);
    let id = base, n = 2;
    while (used.has(id)) id = `${base}_${n++}`;
    return id;
  }
  addNode(file, x, y) {
    const mod = this.mods[file]; if (!mod) return;
    const id = this.newNodeId(file.replace(/\.py$/, ""));
    const snap = App.state.settings?.snap || 10;
    if (x == null) {
      const r = this.svg.getBoundingClientRect(); [x, y] = this.toWorld(r.left + r.width / 2 - W / 2, r.top + r.height / 2 - H / 2);
      const choca = (cx, cy) => this.allIds().some(i => { const p = this.pos(i), [w, hh] = this.size(i); return cx < p.x + w + 30 && cx + W + 30 > p.x && cy < p.y + hh + 30 && cy + H + 30 > p.y; });
      for (let k = 0; k < 40 && choca(x, y); k++) y += H + 30;      // busca un hueco libre debajo
    }
    this.change(p => { p.nodes[id] = {module: file, label: mod.meta.name, x: Math.round(x / snap) * snap, y: Math.round(y / snap) * snap, config: {}}; });
    this.sel = new Set([id]); this.selEdge = null; this.rtab = "config";
    this.palette.classList.add("hidden");
    this.render(); this.paintRight();
  }
  addEdge(src, dst) {
    if (src === dst && src.startsWith("__")) return;
    if (this.p.edges.some(e => srcList(e).includes(src) && e.dst === dst && !e.cond)) { toast("Esa arista ya existe"); return; }
    const used = new Set(this.p.edges.map(e => e.id)); let n = this.p.edges.length + 1;
    while (used.has(`e${n}`)) n++;
    const id = `e${n}`;
    this.change(p => { p.edges.push({id, src, dst}); });
    this.selectEdge(id);
  }
  selectEdge(id) { this.selEdge = id; this.sel.clear(); this.render(); this.paintRight(); }
  async deleteSel() {
    if (this.selEdge) { const id = this.selEdge; this.selEdge = null; this.change(p => { p.edges = p.edges.filter(e => e.id !== id); }); this.paintRight(); return; }
    const ids = [...this.sel].filter(i => !isTerm(i));
    if (!ids.length) return;
    if (!await confirmDlg(`¿Borrar ${ids.length === 1 ? "el nodo «" + ids[0] + "»" : ids.length + " nodos"} y sus aristas?`, "Borrar", true)) return;
    this.change(p => {
      for (const id of ids) delete p.nodes[id];
      p.edges = p.edges.map(e => Array.isArray(e.src) ? {...e, src: e.src.filter(x => !ids.includes(x))} : e)
        .map(e => Array.isArray(e.src) && e.src.length === 1 ? {...e, src: e.src[0]} : e)
        .filter(e => (Array.isArray(e.src) ? e.src.length : !ids.includes(e.src)) && !ids.includes(e.dst));
    });
    this.sel.clear(); this.paintRight();
  }
  copy() {
    const ids = [...this.sel].filter(i => !isTerm(i)); if (!ids.length) return;
    const data = {nodes: Object.fromEntries(ids.map(i => [i, clone(this.p.nodes[i])])),
      edges: this.p.edges.filter(e => srcList(e).every(x => ids.includes(x)) && ids.includes(e.dst)).map(clone)};
    App.clipboard = data; store.set("portapapeles", data);
    toast(`${ids.length} nodo(s) copiado(s)`);
  }
  paste() {
    const data = App.clipboard || store.get("portapapeles", null); if (!data) return;
    const map = {}, newIds = [];
    this.change(p => {
      for (const [old, n] of Object.entries(data.nodes)) {
        if (!this.mods[n.module]) continue;
        const id = this.newNodeId(old); map[old] = id; newIds.push(id);
        p.nodes[id] = {...clone(n), x: (n.x || 0) + 40, y: (n.y || 0) + 40};
      }
      const used = new Set(p.edges.map(e => e.id)); let k = p.edges.length + 1;
      for (const e of data.edges) {
        const srcs = srcList(e).map(x => map[x]); if (srcs.some(x => !x) || !map[e.dst]) continue;
        while (used.has(`e${k}`)) k++;
        used.add(`e${k}`);
        p.edges.push({...clone(e), id: `e${k}`, src: srcs.length > 1 ? srcs : srcs[0], dst: map[e.dst]});
      }
    });
    this.sel = new Set(newIds); this.render(); this.paintRight();
  }
  togglePalette() {
    if (!this.palette.classList.contains("hidden")) { this.palette.classList.add("hidden"); return; }
    const q = h("input", {placeholder: "Buscar módulo…"});
    const list = h("div", {class: "list"});
    const paint = () => {
      const t = q.value.toLowerCase();
      const mods = Object.values(this.mods).filter(m => !m.error && (!t || (m.file + m.meta.name + m.meta.desc + m.meta.category).toLowerCase().includes(t)));
      const cats = [...new Set(mods.map(m => m.meta.category || "General"))].sort();
      list.replaceChildren(...cats.flatMap(c => [h("div", {class: "cat"}, c), ...mods.filter(m => (m.meta.category || "General") === c).map(m =>
        h("div", {class: "it", draggable: "true", "data-mod": m.file, onclick: () => this.addNode(m.file),
          ondragstart: e => { e.dataTransfer.setData("text/x-modulo", m.file); e.dataTransfer.effectAllowed = "copy"; }},
          h("i", {}, m.meta.icon || "▣"), h("div", {}, h("b", {}, m.meta.name), h("small", {}, m.meta.desc || m.file))))]));
    };
    q.addEventListener("input", paint);
    this.palette.replaceChildren(h("div", {style: {padding: "8px"}}, q), list);
    this.palette.classList.remove("hidden");
    paint(); q.focus();
  }

  /* ---------------------------------------------------------------- panel derecho */
  paintRight() {
    const rp = this.rp;
    if (this.selEdge) return this.paintEdgePanel();
    const ids = [...this.sel].filter(i => !isTerm(i));
    if (ids.length !== 1) { rp.classList.add("c"); rp.replaceChildren(); return; }
    const id = ids[0], n = this.p.nodes[id], mod = this.mods[n.module];
    rp.classList.remove("c");
    const tabs = [["config", "Configuración"], ["proceso", "Proceso"], ["estado", "Estado"], ["codigo", "Código"]];
    if (App.f3?.esLLM(mod)) tabs.push(["prompt", "Prompt"]);
    else if (this.rtab === "prompt") this.rtab = "config";
    const body = h("div", {class: "tbody" + (this.rtab === "codigo" ? " cm" : "")});
    rp.replaceChildren(
      h("header", {}, h("span", {}, mod?.meta.icon || "▣"), h("b", {}, n.label || id), pill(this.nodeStatus(id)),
        h("button", {class: "ghost sm", title: "Cerrar panel", onclick: () => { this.sel.clear(); this.render(); this.paintRight(); }}, "✕")),
      h("div", {class: "tabs"}, tabs.map(([k, l]) => h("button", {class: this.rtab === k ? "on" : "", onclick: () => { this.rtab = k; this.paintRight(); }}, l))),
      body);
    this.rbody = body; this.rnode = id;
    if (!mod) { body.append(h("p", {style: {color: "var(--red)"}}, `El módulo ${n.module} no existe.`)); return; }
    ({config: () => this.tabConfig(body, id, n, mod), proceso: () => this.tabProceso(body, id), estado: () => this.tabEstado(body, id, mod),
      codigo: () => this.tabCodigo(body, id, n, mod), prompt: () => App.f3.tabPrompt(this, body, id, n, mod)})[this.rtab]();
  }
  tabConfig(body, id, n, mod) {
    if (mod.error) body.append(h("p", {style: {color: "var(--red)"}}, `Error en el módulo (línea ${mod.line}): ${mod.error}`));
    const label = h("input", {value: n.label || ""});
    const keysId = "keys_" + id;
    const dl = h("datalist", {id: keysId}, Object.keys(this.validation?.canales || {}).map(k => h("option", {value: k})));
    const f = form(mod.config, n.config || {}, {keys: true, keysId});
    const d = mod.meta.defaults || {};
    const r = n.retry || d.retry || {}, t = n.timeout || d.timeout || {};
    const num = (v, ph) => h("input", {type: "number", step: "any", value: v ?? "", placeholder: ph || ""});
    const pol = {max: num(r.max_attempts ?? 1), ini: num(r.initial_interval ?? 1), back: num(r.backoff_factor ?? 2), maxi: num(r.max_interval ?? 60),
      run: num(t.run, "sin límite"), idle: num(t.idle, "sin límite")};
    const bpB = h("input", {type: "checkbox", checked: n.breakpoint?.before}), bpA = h("input", {type: "checkbox", checked: n.breakpoint?.after});
    const cacheOn = h("input", {type: "checkbox", checked: n.cache != null, id: "cacheOn"}), cacheTtl = num(n.cache?.ttl, "sin caducidad");
    const deferOn = h("input", {type: "checkbox", checked: !!n.defer});
    const errTo = h("select", {}, h("option", {value: ""}, "(ninguno: el error para la ejecución)"),
      Object.keys(this.p.nodes).filter(x => x !== id).map(x => h("option", {value: x}, x)));
    errTo.value = n.error_to || "";
    const enHist = this.threadId && !this.running && (this.hist || []).some(c => (c.next || []).includes(id));
    const paused = this.run && ["drained", "interrupted", "stopped"].includes(this.run.status) && (this.state?.next || []).length && !this.interrupts.length;
    body.append(dl,
      h("label", {class: "f"}, h("span", {}, "Nombre"), label),
      h("div", {class: "mut", style: {fontSize: "12px", marginBottom: "10px"}}, `${id} · ${mod.file} · ${mod.meta.kind} · ${mod.meta.exec}${mod.meta.gpu ? " · GPU " + mod.meta.gpu : ""}`),
      f.el,
      h("h3", {style: {margin: "14px 0 8px"}}, "Reintentos y timeouts"),
      h("div", {class: "policy"},
        h("label", {class: "f"}, h("span", {}, "Intentos máx."), pol.max), h("label", {class: "f"}, h("span", {}, "Intervalo inicial (s)"), pol.ini),
        h("label", {class: "f"}, h("span", {}, "Factor"), pol.back), h("label", {class: "f"}, h("span", {}, "Intervalo máx. (s)"), pol.maxi),
        h("label", {class: "f"}, h("span", {}, "Timeout total (s)"), pol.run), h("label", {class: "f"}, h("span", {}, "Timeout sin progreso (s)"), pol.idle)),
      h("h3", {style: {margin: "6px 0 8px"}}, "Breakpoints"),
      h("label", {class: "chk"}, bpB, "◐ Parar antes de este nodo"), h("label", {class: "chk"}, bpA, "◑ Parar después de este nodo"),
      h("h3", {style: {margin: "6px 0 8px"}}, "Caché, diferido y errores"),
      h("label", {class: "chk"}, cacheOn, "⚡ Cachear el resultado (misma configuración, entradas y código)"),
      h("label", {class: "f"}, h("span", {}, "Caducidad de la caché (s)"), cacheTtl),
      h("label", {class: "chk"}, deferOn, "Diferido: esperar a que terminen las demás ramas"),
      h("label", {class: "f"}, h("span", {}, "Si falla (tras los reintentos), continuar por"), errTo,
        h("small", {}, "El nodo destino recibe la clave «error» = {node, msg}")),
      h("div", {class: "row wrap", style: {marginTop: "12px"}},
        h("button", {class: "primary", onclick: () => {
          let cfg; try { cfg = f.values(); } catch (e) { toastErr(e); return; }
          const nn = v => v.value === "" ? null : Number(v.value);
          this.change(p => {
            const x = p.nodes[id];
            x.label = label.value.trim() || id; x.config = cfg;
            x.retry = {max_attempts: nn(pol.max) || 1, initial_interval: nn(pol.ini) ?? 1, backoff_factor: nn(pol.back) ?? 2, max_interval: nn(pol.maxi) ?? 60, jitter: true};
            x.timeout = {run: nn(pol.run), idle: nn(pol.idle)};
            x.breakpoint = {before: bpB.checked, after: bpA.checked};
            if (cacheOn.checked) x.cache = {ttl: nn(cacheTtl)}; else delete x.cache;
            if (deferOn.checked) x.defer = true; else delete x.defer;
            if (errTo.value) x.error_to = errTo.value; else delete x.error_to;
          });
          toast("Configuración guardada", "ok"); this.paintRight();
        }}, "Guardar"),
        h("button", {onclick: () => { this.change(p => { p.nodes[id].config = {}; delete p.nodes[id].retry; delete p.nodes[id].timeout; }); this.paintRight(); }}, "Valores por defecto"),
        paused ? h("button", {onclick: () => this.cont()}, "⏵ Continuar desde aquí") : null,
        h("button", {id: "rerunBtn", disabled: !enHist, title: enHist ? "Reproducir desde el último checkpoint de esta rama que tiene este nodo como siguiente" : "Este nodo no aparece en la historia de la rama actual",
          onclick: () => this.rerun(id)}, "⟲ Re-ejecutar nodo")));
  }
  tabProceso(body, id) {
    const v = this.view.nodos[id] || {};
    const lines = this.logs[id] || [];
    this.logBox = h("div", {class: "loglines"}, lines.slice(-1500).join("\n"));
    this.partialBox = h("pre", {class: "mono", style: {fontSize: "12px"}}, this.partial[id] ? JSON.stringify(this.partial[id], null, 2).slice(0, 4000) : "");
    body.append(h("dl", {class: "kv"}, h("dt", {}, "Estado"), h("dd", {}, STATUS_ES[v.status] || v.status || "pendiente"),
      h("dt", {}, "Intentos"), h("dd", {}, v.attempts || 0), h("dt", {}, "Ejecuciones"), h("dd", {}, v.count || 0)),
      v.error ? h("div", {style: {color: "var(--red)", margin: "10px 0", fontSize: "12.5px"}}, h("b", {}, "Error: "), v.error) : null,
      v.handled_by ? h("p", {class: "mut"}, `Error manejado: la ejecución siguió por «${v.handled_by}».`) : null,
      v.cached ? h("p", {class: "mut"}, "⚡ Resultado tomado de la caché: el nodo no se ejecutó.") : null,
      v.error ? h("details", {}, h("summary", {}, "Traceback"), h("pre", {class: "loglines", id: "tbBox"}, "…")) : null,
      this.tokens[id] ? h("div", {}, h("h3", {style: {margin: "10px 0 6px"}}, "Tokens en vivo"), h("div", {class: "transcript", style: {fontSize: "14px"}}, this.tokens[id])) : null,
      this.partial[id] ? h("div", {}, h("h3", {style: {margin: "10px 0 6px"}}, "Vista previa"), this.partialBox) : null,
      h("h3", {style: {margin: "10px 0 6px"}}, "Log"), this.logBox);
    this.logBox.scrollTop = this.logBox.scrollHeight;
    if (v.error) {
      const ev = [...this.events].reverse().find(e => e.type === "node.end" && e.node === id && e.data.traceback);
      const box = body.querySelector("#tbBox"); if (box) box.textContent = ev?.data.traceback || "(sin traceback)";
    }
  }
  async tabEstado(body, id, mod) {
    if (!this.threadId) { body.append(h("p", {class: "mut"}, "Crea o elige un hilo.")); return; }
    const io = this.validation?.io?.[id] || {inputs: mod.meta.inputs || [], outputs: mod.meta.outputs || []};
    let snapSt = this.state;
    if (this.cp) { try { snapSt = await api.get(`/threads/${this.threadId}/state?checkpoint_id=${this.cp}`); } catch { /* */ } }
    const vals = snapSt?.values || {};
    const keys = [...new Set([...io.inputs, ...io.outputs])];
    const areas = {};
    body.append(h("p", {class: "mut", style: {fontSize: "12px"}}, this.cp ? `Checkpoint seleccionado ${this.cp.slice(0, 8)}…` : "Estado actual del hilo"));
    for (const [title, ks] of [["Entrada", io.inputs], ["Salida", io.outputs]]) {
      body.append(h("h3", {style: {margin: "10px 0 6px"}}, title));
      if (!ks.length) body.append(h("p", {class: "mut"}, mod.meta.exec === "local" && title === "Entrada" ? "Recibe el estado completo." : "—"));
      for (const k of ks) {
        const ta = h("textarea", {class: "code", rows: 2, spellcheck: false, disabled: this.running});
        ta.value = vals[k] === undefined ? "" : JSON.stringify(vals[k], null, 2);
        areas[k] = ta;
        body.append(h("label", {class: "f"}, h("span", {}, k), ta, typeof vals[k] === "string" && /\.(mp4|wav|png|jpg|webm|mp3)$/.test(vals[k]) ? h("small", {}, h("a", {href: media(vals[k]), target: "_blank"}, "abrir fichero")) : null));
      }
    }
    if (keys.length) body.append(h("button", {class: "primary", disabled: this.running, onclick: async () => {
      const values = {};
      try {
        for (const [k, ta] of Object.entries(areas)) {
          const old = JSON.stringify(vals[k]), txt = ta.value.trim();
          if ((txt || "undefined") === (old || "undefined") || txt === old) continue;
          values[k] = txt ? JSON.parse(txt) : null;
        }
      } catch { toast("Algún valor no es JSON válido (los textos van entre comillas)", "err"); return; }
      if (!Object.keys(values).length) { toast("Sin cambios"); return; }
      await this.editState(values, this.cp);
    }}, this.cp ? "Editar y bifurcar desde este checkpoint" : "Guardar en el estado"));
  }
  tabCodigo(body, id, n, mod) {
    const usos = mod.usos || [];
    const nNodes = Object.values(this.p.nodes).filter(x => x.module === mod.file).length;
    const wrap = h("div", {class: "cmwrap", style: {flex: "1", minHeight: "0"}});
    const ed = App.moduleEditor(wrap, mod.file, {onSaved: async () => { this.mods = Object.fromEntries((await App.loadModules(true)).map(m => [m.file, m])); this.validate(); }});
    body.append(h("div", {style: {padding: "8px 10px", borderBottom: "1px solid var(--line)"}},
      h("div", {class: "mut", style: {fontSize: "12px", marginBottom: "6px"}}, `${mod.file} · afecta a ${nNodes} nodo(s) aquí y a ${usos.length} flujo(s) en total`),
      h("div", {class: "row wrap"}, h("button", {class: "sm primary", onclick: () => ed.save()}, "Guardar ", h("kbd", {}, "Ctrl+S")),
        h("button", {class: "sm", onclick: () => App.moduleHistory(mod.file, () => ed.cm.getValue(), () => ed.load())}, "Historial"),
        h("button", {class: "sm", onclick: async () => {
          const f = await App.nuevoModulo(mod.file); if (!f) return;
          this.mods = Object.fromEntries((await App.loadModules(true)).map(m => [m.file, m]));
          this.change(p => { p.nodes[id].module = f; }); this.paintRight();
        }}, "Duplicar como módulo nuevo"), ed.status)), wrap);
    ed.load().then(() => ed.cm.refresh());
  }
  paintEdgePanel() {
    const e = this.p.edges.find(x => x.id === this.selEdge); if (!e) { this.selEdge = null; return this.paintRight(); }
    const rp = this.rp; rp.classList.remove("c");
    const srcs = srcList(e);
    const cond = h("textarea", {class: "code", rows: 3, spellcheck: false, placeholder: "vacío = siempre · else = si ninguna otra condición se cumple · p. ej. s.get('aprobado')"});
    cond.value = e.cond || "";
    const label = h("input", {value: e.label || ""});
    const incoming = this.p.edges.filter(x => x.dst === e.dst && !x.cond);
    const join = h("input", {type: "checkbox", checked: srcs.length > 1, disabled: !!e.cond || (srcs.length < 2 && incoming.length < 2)});
    const same = this.p.edges.filter(x => srcList(x)[0] === srcs[0]);
    const idx = this.p.edges.indexOf(e);
    rp.replaceChildren(
      h("header", {}, h("b", {}, `Arista ${e.id}`), h("button", {class: "ghost sm", onclick: () => { this.selEdge = null; this.render(); this.paintRight(); }}, "✕")),
      h("div", {class: "tbody"},
        h("dl", {class: "kv"}, h("dt", {}, "Desde"), h("dd", {}, srcs.join(", ")), h("dt", {}, "Hasta"), h("dd", {}, e.dst)),
        h("div", {style: {height: "10px"}}),
        h("label", {class: "f"}, h("span", {}, "Condición (Python sobre s)"), cond,
          h("small", {}, "Se evalúan en el orden de la lista; se siguen TODAS las verdaderas; «else» solo si ninguna lo es.")),
        h("label", {class: "f"}, h("span", {}, "Etiqueta"), label),
        h("label", {class: "chk"}, join, "Esperar a todas (join de las aristas sin condición que llegan a " + e.dst + ")"),
        h("div", {class: "row wrap"},
          h("button", {class: "primary", onclick: () => {
            const c = cond.value.trim();
            this.change(p => {
              const x = p.edges.find(y => y.id === e.id);
              if (c) x.cond = c; else delete x.cond;
              if (label.value.trim()) x.label = label.value.trim(); else delete x.label;
              if (!c && join.checked && srcList(x).length < 2) {        // fusionar en un join
                const inc = p.edges.filter(y => y.dst === x.dst && !y.cond);
                x.src = [...new Set(inc.flatMap(srcList))];
                p.edges = p.edges.filter(y => y === x || !inc.includes(y));
              } else if (!join.checked && srcList(x).length > 1) {        // separar el join
                const list = srcList(x); x.src = list[0];
                const used = new Set(p.edges.map(y => y.id)); let k = p.edges.length + 1;
                for (const sId of list.slice(1)) { while (used.has(`e${k}`)) k++; used.add(`e${k}`); p.edges.push({id: `e${k}`, src: sId, dst: x.dst}); }
              }
            });
            this.paintRight(); toast("Arista guardada", "ok");
          }}, "Guardar"),
          h("button", {title: "Subir (orden de evaluación)", disabled: idx <= 0, onclick: () => this.moveEdge(e.id, -1)}, "↑"),
          h("button", {title: "Bajar", disabled: idx >= this.p.edges.length - 1, onclick: () => this.moveEdge(e.id, 1)}, "↓"),
          h("button", {class: "danger", onclick: () => this.deleteSel()}, "Borrar")),
        same.length > 1 ? h("p", {class: "mut", style: {fontSize: "12px", marginTop: "10px"}}, `Orden desde ${srcs[0]}: ` + same.map(x => `${x.id}${x.cond ? " [" + x.cond.slice(0, 18) + "]" : ""}`).join(" → ")) : null));
  }
  moveEdge(id, d) {
    this.change(p => { const i = p.edges.findIndex(x => x.id === id), j = i + d; if (j < 0 || j >= p.edges.length) return; [p.edges[i], p.edges[j]] = [p.edges[j], p.edges[i]]; });
    this.paintRight();
  }

  /* ---------------------------------------------------------------- panel inferior */
  buildBottom() {
    const resizer = h("div", {class: "resizer", onmousedown: e => {
      const y0 = e.clientY, h0 = this.bp.getBoundingClientRect().height;
      const mv = ev => { const nh = Math.max(120, Math.min(window.innerHeight - 200, h0 - (ev.clientY - y0))); this.bp.style.height = nh + "px"; };
      const up = () => { removeEventListener("mousemove", mv); removeEventListener("mouseup", up); store.set("bp_h", parseInt(this.bp.style.height)); this.renderMinimap(); };
      addEventListener("mousemove", mv); addEventListener("mouseup", up); e.preventDefault();
    }});
    this.btabs = h("div", {class: "tabs"});
    this.bbody = h("div", {class: "bbody"});
    this.bp.append(resizer, this.btabs, this.bbody);
  }
  paintBottom() {
    const tabs = [["salida", "Salida"], ["historia", "Historia"], ["estado", "Estado"], ["interrupts", "Interrupts"], ["eventos", "Eventos"]];
    const collapsed = this.bp.classList.contains("c");
    this.btabs.replaceChildren(...tabs.map(([k, l]) => h("button", {class: this.btab === k && !collapsed ? "on" : "", "data-btab": k, onclick: () => {
      this.btab = k; store.set("btab", k);
      if (collapsed) this.toggleBottom(false); else this.paintBottom();
    }}, l, k === "interrupts" && this.interrupts.length ? h("span", {class: "n"}, this.interrupts.length) : null)),
    h("span", {class: "sp"}), h("button", {class: "ghost sm", title: collapsed ? "Mostrar panel" : "Plegar panel", onclick: () => this.toggleBottom(!collapsed)}, collapsed ? "▲" : "▼"));
    if (collapsed) return;
    this.bbody.replaceChildren();
    if (!this.threadId) { this.bbody.append(h("p", {class: "mut"}, "Este flujo no tiene hilos: pulsa «Iniciar» o «+ hilo».")); return; }
    ({salida: () => this.bSalida(), historia: () => this.bHistoria(), estado: () => this.bEstado(), interrupts: () => this.bInterrupts(), eventos: () => this.bEventos()})[this.btab]();
  }
  toggleBottom(c) {
    this.bp.classList.toggle("c", c); store.set("bp_c", c);
    this.bp.style.height = c ? "auto" : store.get("bp_h", 260) + "px";
    this.paintBottom(); setTimeout(() => this.renderMinimap(), 50);
  }
  bSalida() {
    const vals = this.state?.values || {};
    const outs = Object.entries(this.p.nodes).filter(([, n]) => this.mods[n.module]?.meta.kind === "output");
    const items = outs.flatMap(([, n]) => (n.config?.mostrar || this.mods[n.module].config.find(c => c.key === "mostrar")?.default || []));
    const vid = items.find(i => i.como === "video" && vals[i.clave]);
    const textos = items.filter(i => i.como === "texto" && vals[i.clave] != null);
    const otros = items.filter(i => ["audio", "imagen"].includes(i.como) && vals[i.clave]);
    const live = Object.entries(this.tokens).filter(([, t]) => t).map(([n, t]) => h("div", {}, h("div", {class: "who"}, `${n} (en vivo)`), t));
    if (!vid && !textos.length && !otros.length && !live.length) { this.bbody.append(h("p", {class: "mut"}, this.running ? "Ejecutando… la salida aparecerá aquí." : "Sin salida todavía.")); return; }
    const right = h("div", {class: "transcript"},
      live, textos.map(i => h("div", {style: {marginBottom: "12px"}}, h("div", {class: "who"}, i.clave), typeof vals[i.clave] === "string" ? vals[i.clave] : JSON.stringify(vals[i.clave], null, 2))),
      otros.map(i => h("div", {}, h("div", {class: "who"}, i.clave), i.como === "audio" ? h("audio", {src: media(vals[i.clave]), controls: true}) : h("img", {src: media(vals[i.clave]), style: {maxWidth: "260px"}}))));
    this.bbody.append(vid ? h("div", {class: "outgrid"}, h("video", {src: media(vals[vid.clave]), controls: true, preload: "metadata"}), right) : right);
  }
  bHistoria() {
    const hist = this.hist || [];
    if (!hist.length) { this.bbody.append(h("p", {class: "mut"}, "Sin checkpoints.")); return; }
    const byParent = {}, ids = new Set(hist.map(x => x.checkpoint_id));
    for (const c of hist) (byParent[ids.has(c.parent_checkpoint_id) ? c.parent_checkpoint_id : "root"] = byParent[ids.has(c.parent_checkpoint_id) ? c.parent_checkpoint_id : "root"] || []).push(c);
    for (const k in byParent) byParent[k].sort((a, b) => (a.created_at || "").localeCompare(b.created_at || ""));
    const tree = h("div", {class: "htree"});
    const walk = (pid, depth, branch) => {
      const kids = byParent[pid] || [];
      kids.forEach((c, i) => {
        const nb = branch || (i > 0 ? 1 : 0);
        tree.append(h("div", {class: `cp ${c.source} ${this.cp === c.checkpoint_id ? "on" : ""}`, style: {paddingLeft: `${6 + depth * 14}px`}, onclick: () => { this.cp = c.checkpoint_id; this.paintBottom(); if (this.rtab === "estado") this.paintRight(); }},
          h("span", {class: "dot"}), `#${c.step} `, h("span", {class: "mut"}, `${(c.writes || []).join(",") || c.source}`), ` → ${(c.next || []).join(", ") || "fin"}`, c.interrupts ? " ✋" : ""));
        walk(c.checkpoint_id, depth + (kids.length > 1 ? 1 : 0), nb);
      });
    };
    walk("root", 0, 0);
    const det = h("div", {style: {minWidth: 0, overflow: "auto"}});
    this.bbody.append(h("div", {class: "histwrap"}, tree, det));
    if (!this.cp) { det.append(h("p", {class: "mut"}, "Selecciona un checkpoint para ver su estado.")); return; }
    api.get(`/threads/${this.threadId}/state?checkpoint_id=${this.cp}`).then(sn => {
      const ta = h("textarea", {class: "code", rows: 8, spellcheck: false});
      ta.value = JSON.stringify(sn.values, null, 2);
      det.append(h("dl", {class: "kv"}, h("dt", {}, "Checkpoint"), h("dd", {}, sn.checkpoint_id), h("dt", {}, "Paso"), h("dd", {}, sn.metadata?.step),
        h("dt", {}, "Origen"), h("dd", {}, sn.metadata?.source), h("dt", {}, "Siguiente"), h("dd", {}, sn.next.join(", ") || "—"),
        h("dt", {}, "Fecha"), h("dd", {}, sn.created_at)),
        h("div", {class: "row wrap", style: {margin: "8px 0"}},
          h("button", {class: "sm primary", disabled: this.running || !sn.next.length, onclick: () => this.replay(sn.checkpoint_id)}, "⟲ Reproducir desde aquí"),
          h("button", {class: "sm", disabled: this.running, onclick: async () => {
            let vals; try { vals = JSON.parse(ta.value); } catch { toast("JSON no válido", "err"); return; }
            const diff = Object.fromEntries(Object.entries(vals).filter(([k, v]) => JSON.stringify(v) !== JSON.stringify(sn.values[k])));
            for (const k of Object.keys(sn.values)) if (!(k in vals)) diff[k] = null;
            if (!Object.keys(diff).length) { toast("Edita algún valor para bifurcar"); return; }
            await this.editState(diff, sn.checkpoint_id, true);
          }}, "✎ Editar y bifurcar"),
          h("button", {class: "sm", disabled: this.running, onclick: async () => {
            try { const r = await api.post(`/threads/${this.threadId}/state`, {checkpoint_id: sn.checkpoint_id, values: null, as_node: "__copy__"}); this.cp = r.checkpoint_id; await this.loadHistory(); this.paintBottom(); toast("Rama creada", "ok"); }
            catch (e) { toastErr(e); }
          }}, "Bifurcar sin cambios")),
        h("label", {class: "f"}, h("span", {}, "Valores (edítalos y pulsa «Editar y bifurcar»)"), ta));
    }).catch(toastErr);
  }
  bEstado() {
    const vals = this.state?.values || {};
    const ta = h("textarea", {class: "code", rows: 10, spellcheck: false, disabled: this.running, style: {height: "calc(100% - 44px)", minHeight: "120px"}});
    ta.value = JSON.stringify(vals, null, 2);
    this.bbody.append(h("div", {class: "row", style: {marginBottom: "6px"}},
      h("span", {class: "mut", style: {fontSize: "12px"}}, `Siguiente: ${(this.state?.next || []).join(", ") || "—"} · checkpoint ${(this.state?.checkpoint_id || "").slice(0, 8)}`),
      this.state && this.state.version_proyecto && this.state.version_proyecto !== this.state.version_actual ? h("span", {class: "pill drained"}, `hilo creado con la versión ${this.state.version_proyecto} del proyecto`) : null,
      h("span", {class: "sp"}),
      h("button", {class: "sm primary", disabled: this.running, onclick: async () => {
        let nv; try { nv = JSON.parse(ta.value); } catch { toast("JSON no válido", "err"); return; }
        const diff = Object.fromEntries(Object.entries(nv).filter(([k, v]) => JSON.stringify(v) !== JSON.stringify(vals[k])));
        for (const k of Object.keys(vals)) if (!(k in nv)) diff[k] = null;
        if (!Object.keys(diff).length) { toast("Sin cambios"); return; }
        await this.editState(diff, null);
      }}, "Guardar estado")), ta);
  }
  bInterrupts() {
    if (!this.interrupts.length) { this.bbody.append(h("p", {class: "mut"}, "No hay interrupts pendientes.")); return; }
    for (const it of this.interrupts) {
      const val = it.value || {};
      const init = {};
      if (it.response_schema?.properties?.valor && val.valor != null) init.valor = String(val.valor);
      if (it.response_schema?.properties?.aprobado) init.aprobado = true;
      const f = schemaForm(it.response_schema || {type: "object", properties: {valor: {type: "string"}}}, init);
      this.bbody.append(h("div", {class: "iform", "data-interrupt": it.id},
        h("div", {class: "row"}, h("b", {}, `✋ ${it.node}`), h("span", {class: "mut mono", style: {fontSize: "11px"}}, it.id.slice(0, 10))),
        val.mensaje ? h("p", {}, val.mensaje) : null,
        typeof val !== "object" ? h("pre", {class: "mono"}, JSON.stringify(val)) : null,
        f.el,
        h("button", {class: "primary", onclick: async () => {
          let v; try { v = f.values(); } catch (e) { toastErr(e); return; }
          for (const r of it.response_schema?.required || []) if (v[r] === undefined || v[r] === null) { toast(`Falta «${r}»`, "err"); return; }
          await this.startRun({resume: {[it.id]: v}, step: this.stepMode || undefined});
        }}, "Responder")));
    }
  }
  bEventos() {
    const types = ["(todos)", "run.status", "node.start", "node.end", "node.log", "checkpoint", "interrupt", "edge", "thread.status", "token", "run.queued"];
    const sel = h("select", {style: {width: "auto"}}, types.map(t => h("option", {value: t}, t)));
    sel.value = this.evFilter || "(todos)";
    const list = h("div", {class: "evlist"});
    const paint = () => {
      this.evFilter = sel.value;
      const evs = this.events.filter(e => sel.value === "(todos)" || e.type === sel.value).slice(-600);
      list.replaceChildren(...evs.map(e => h("div", {title: JSON.stringify(e.data)}, h("span", {class: "mut"}, new Date(e.ts * 1000).toLocaleTimeString("es-ES") + " "),
        h("span", {class: "t"}, e.type), " ", e.node || "", " ", JSON.stringify(e.data).slice(0, 220))));
      this.bbody.scrollTop = this.bbody.scrollHeight;
    };
    sel.addEventListener("change", paint);
    this.bbody.append(h("div", {class: "row", style: {marginBottom: "6px"}}, h("span", {class: "mut"}, "Filtrar:"), sel), list);
    this.evList = list; this.evPaint = paint;
    paint();
  }

  /* ---------------------------------------------------------------- ejecución */
  async newThread() {
    try { const r = await api.post("/threads", {project: this.slug}); await this.loadThreads(r.thread_id); toast("Hilo nuevo", "ok"); }
    catch (e) { toastErr(e); }
  }
  async ensureThread() {
    if (this.threadId) return this.threadId;
    const r = await api.post("/threads", {project: this.slug});
    await this.loadThreads(r.thread_id);
    return r.thread_id;
  }
  async startDialog(step) {
    if (this.dirty) await this.save();
    const schema = this.p.state_schema || {};
    const inKeys = Object.entries(schema).filter(([, d]) => d?.input).map(([k]) => k);
    const f = form(inKeys.map(k => ({key: k, label: k, type: "json"})), {});
    const ta = h("textarea", {class: "code", rows: 6, spellcheck: false, id: "runInput"});
    ta.value = "{}";
    const entradaVals = Object.values(this.p.nodes).filter(n => n.module === "entrada.py").map(n => n.config?.valores || {});
    const pending = (this.state?.next || []).length > 0;
    const strat = h("select", {id: "multitask"}, [["enqueue", "Encolar"], ["interrupt", "Interrumpir y lanzar"], ["rollback", "Deshacer (rollback) y lanzar"], ["reject", "Rechazar"]]
      .map(([v, l]) => h("option", {value: v}, l)));
    if (["enqueue", "interrupt", "rollback"].includes(App.state.settings?.multitask_strategy)) strat.value = App.state.settings.multitask_strategy;
    modal({title: step ? "Iniciar paso a paso" : "Iniciar ejecución", body: h("div", {},
      inKeys.length ? h("div", {}, h("h3", {style: {marginBottom: "8px"}}, "Canales de entrada"), f.el) : null,
      h("label", {class: "f"}, h("span", {}, "Entrada (JSON). Las claves que pongas aquí ganan a los valores del nodo Entrada."), ta),
      entradaVals.length ? h("small", {class: "mut"}, "Valores del nodo Entrada: " + Object.keys(Object.assign({}, ...entradaVals)).join(", ")) : null,
      pending ? h("p", {style: {color: "var(--orange)"}}, "Este hilo tiene pasos pendientes (siguiente: " + this.state.next.join(", ") + "). Iniciar con una entrada los abandona y empieza desde el principio; para seguir usa «Continuar».") : null,
      this.running ? h("label", {class: "f"}, h("span", {}, "El hilo está ocupado. ¿Qué hacer con la ejecución en marcha?"), strat,
        h("small", {}, "rechazar = no lanzar · encolar = lanzar al terminar · interrumpir = detenerla y seguir desde su último checkpoint · rollback = detenerla y deshacerla")) : null),
      actions: [{label: "Cancelar"}, {label: step ? "Iniciar paso a paso" : "▶ Iniciar", class: "primary", onclick: async () => {
        let input;
        try { input = JSON.parse(ta.value || "{}"); for (const [k, v] of Object.entries(f.values())) if (v !== null) input[k] = v; }
        catch { toast("La entrada no es JSON válido", "err"); return false; }
        this.stepMode = step;
        await this.startRun({input, step: step || undefined, multitask_strategy: this.running ? strat.value : undefined});
      }}]});
  }
  async startRun(body) {
    try {
      const tid = await this.ensureThread();
      if (this.dirty) await this.save();
      if (this.assistantId) body = {...body, assistant_id: this.assistantId};
      const r = await api.post(`/threads/${tid}/runs`, body);
      if (r.queued) { toast(`Ejecución en cola (posición ${r.position})`, "ok"); return; }
      this.view = {nodos: {}, aristas: new Set(), run: {run_id: r.run_id, status: "pending"}}; this.inner = {};
      this.logs = {}; this.partial = {}; this.tokens = {}; this.interrupts = [];
      this.paintControls(); this.render(); this.paintRight(); this.paintBottom();
    } catch (e) { toastErr(e); }
  }
  stepAction() {
    if (this.threadId && (this.state?.next || []).length && !this.interrupts.length) { this.stepMode = true; this.startRun({input: null, step: true}); }
    else this.startDialog(true);
  }
  cont() { this.startRun({input: null, step: this.stepMode || undefined}); }
  async rerun(id) {
    try {
      const r = await api.post(`/threads/${this.threadId}/nodes/${id}/rerun`, this.assistantId ? {assistant_id: this.assistantId} : {});
      this.view = {nodos: {}, aristas: new Set(), run: {run_id: r.run_id, status: "pending"}}; this.inner = {};
      this.logs = {}; this.partial = {}; this.tokens = {}; this.interrupts = [];
      toast(`Re-ejecutando «${id}» desde el checkpoint ${r.checkpoint_id.slice(0, 8)}`, "ok");
      this.paintControls(); this.render(); this.paintRight(); this.paintBottom();
    } catch (e) { toastErr(e); }
  }
  replay(cid) { this.startRun({checkpoint_id: cid, step: this.stepMode || undefined}); }
  async pause() { if (this.run) await api.post(`/runs/${this.run.run_id}/pause`).catch(toastErr); }
  async stop() { if (this.run) { this.stepMode = false; await api.post(`/runs/${this.run.run_id}/stop`).catch(toastErr); } }
  async editState(values, checkpointId, andRun) {
    try {
      const r = await api.post(`/threads/${this.threadId}/state`, {values, checkpoint_id: checkpointId || undefined});
      toast(checkpointId ? "Rama nueva creada" : "Estado actualizado", "ok");
      this.cp = r.checkpoint_id;
      await Promise.all([this.loadState(), this.loadHistory()]);
      this.paintControls(); this.render(); this.paintBottom(); this.paintRight();
      if (andRun && await confirmDlg("¿Ejecutar la rama nueva ahora?", "Ejecutar")) this.replay(r.checkpoint_id);
    } catch (e) { toastErr(e); }
  }

  /* ---------------------------------------------------------------- modo Chat [F2] */
  paintChat() {
    if (!this.chat || this.chat.classList.contains("hidden")) return;
    const keep = this.chatIn?.value || "";
    const box = h("div", {class: "msgs", id: "chatMsgs"});
    const results = Object.fromEntries((this.msgs || []).filter(m => m.role === "tool").map(m => [m.tool_call_id, m]));
    for (const m of this.msgs || []) {
      if (m.role === "tool" || m.role === "system") continue;
      const txt = typeof m.content === "string" ? m.content : JSON.stringify(m.content);
      if (txt) box.append(h("div", {class: "msg " + m.role}, txt));
      for (const c of m.tool_calls || []) {
        const r = results[c.id];
        // D9: las herramientas flujo__<slug> son flujos usados como herramienta
        const nom = c.name.startsWith("flujo__") ? `⧉ flujo · ${c.name.slice(7)}` : `🔧 ${c.name}`;
        box.append(h("details", {class: "tool"}, h("summary", {}, `${nom}(${Object.values(c.args || {}).map(v => JSON.stringify(v)).join(", ").slice(0, 80)})`),
          h("pre", {}, "Argumentos: " + JSON.stringify(c.args, null, 2) + (r ? "\n\nResultado:\n" + r.content : "\n\n(sin resultado todavía)"))));
      }
    }
    const live = Object.values(this.tokens).filter(Boolean).join("");
    if (live && this.running) box.append(h("div", {class: "msg assistant live"}, live));
    for (const it of this.interrupts) {
      const f = schemaForm(it.response_schema || {type: "object", properties: {valor: {type: "string"}}}, it.response_schema?.properties?.aprobado ? {aprobado: true} : {});
      box.append(h("div", {class: "iform", "data-interrupt": it.id}, h("b", {}, "✋ " + (it.value?.mensaje || it.node)), f.el,
        h("button", {class: "primary", onclick: async () => {
          let v; try { v = f.values(); } catch (e) { toastErr(e); return; }
          await this.startRun({resume: {[it.id]: v}});
        }}, "Responder")));
    }
    if (!box.children.length) box.append(h("p", {class: "mut"}, this.threadId ? "Escribe un mensaje para empezar." : "Escribe un mensaje: se creará un hilo."));
    this.chatIn = h("textarea", {placeholder: "Mensaje… (Enter envía, Mayús+Enter salto de línea)", id: "chatIn"});
    this.chatIn.value = keep;
    const send = async () => {
      const t = this.chatIn.value.trim(); if (!t) return;
      this.chatIn.value = "";
      this.msgs = [...(this.msgs || []), {role: "user", content: t}];
      await this.startRun({input: {messages: [{role: "user", content: t}]}, multitask_strategy: this.running ? "enqueue" : undefined});
      this.paintChat();
    };
    this.chatIn.addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } });
    this.chat.replaceChildren(box, h("div", {class: "inbox"}, this.chatIn, h("button", {class: "primary", id: "chatSend", onclick: send}, "Enviar")));
    box.scrollTop = box.scrollHeight;
  }

  /* ---------------------------------------------------------------- eventos en vivo */
  onEvent(ev) {
    if (!this.threadId || ev.thread_id !== this.threadId) {
      if (ev.type === "thread.status" && ev.thread_id && this.threads?.some(t => t.thread_id === ev.thread_id)) {
        const t = this.threads.find(x => x.thread_id === ev.thread_id); t.status = ev.data.status; this.paintThreadSel();
      }
      return;
    }
    if (ev.id && !["node.progress", "node.partial"].includes(ev.type)) { this.events.push(ev); if (this.events.length > 5000) this.events.splice(0, 1000); }
    const d = ev.data || {};
    if (ev.type === "token") {
      this.tokens[ev.node] = (this.tokens[ev.node] || "") + d.text;
      if (this.mode === "chat" && this.hasChat && !this.chatT) this.chatT = setTimeout(() => { this.chatT = null; this.paintChat(); }, 120);
      if (this.btab === "salida" && !this.tokPaint) { this.tokPaint = setTimeout(() => { this.tokPaint = null; this.paintBottom(); }, 300); }
      if (ev.ns) { this.trackInner(ev); if (this.xray) this.render(); }
      return;
    }
    if (ev.ns) { this.trackInner(ev); if (this.xray && ["node.start", "node.end"].includes(ev.type)) this.render(); return; }
    if (ev.type === "run.status" && d.status === "queued") return;
    if (ev.type === "run.queued") { toast(`Ejecución en cola (posición ${d.position})`); return; }
    const nd = ev.node && (this.view.nodos[ev.node] = this.view.nodos[ev.node] || {status: "pending", progress: 0, count: 0, attempts: 0});
    switch (ev.type) {
    case "run.status":
      if (!this.view.run || this.view.run.run_id !== ev.run_id) { this.view = {nodos: {}, aristas: new Set(), run: {run_id: ev.run_id}}; this.logs = {}; this.tokens = {}; }
      Object.assign(this.view.run, {status: d.status, motivo: d.motivo, error: d.error});
      if (!ACTIVE.includes(d.status)) this.afterRun(d);
      this.paintControls(); this.render();
      break;
    case "node.start":
      if ("esperando_gpu" in d) nd.status = d.esperando_gpu ? "waiting_gpu" : "running";
      else { nd.status = "running"; nd.attempts = d.attempt; nd.progress = 0; nd.error = null; if (d.attempt === 1) nd.count = (nd.count || 0) + 1; this.tokens[ev.node] = ""; }
      this.render(); if (this.rnode === ev.node && this.rtab === "proceso") this.paintRight();
      break;
    case "node.progress": nd.progress = d.value; this.render(); break;
    case "node.partial": this.partial[ev.node] = {...(this.partial[ev.node] || {}), ...(d.patch || {})}; if (this.rnode === ev.node && this.rtab === "proceso" && this.partialBox) this.partialBox.textContent = JSON.stringify(this.partial[ev.node], null, 2).slice(0, 4000); break;
    case "node.log":
      (this.logs[ev.node] = this.logs[ev.node] || []).push(d.line);
      if (this.logs[ev.node].length > 4000) this.logs[ev.node].splice(0, 1000);
      if (this.rnode === ev.node && this.rtab === "proceso" && this.logBox?.isConnected) { this.logBox.textContent += (this.logBox.textContent ? "\n" : "") + d.line; this.logBox.scrollTop = this.logBox.scrollHeight; }
      break;
    case "node.end":
      if (d.handler) { nd.handled_by = d.error_to; break; }
      nd.status = d.status; nd.error = d.error || null; if (d.status === "success" || d.status === "cached") nd.progress = 1;
      nd.cached = d.status === "cached";
      if (d.status !== "error") delete this.tokens[ev.node];
      this.render(); if (this.rnode === ev.node) this.paintRight();
      break;
    case "edge": this.view.aristas.add(d.edge_id); this.render(); break;
    case "interrupt":
      if (!this.interrupts.some(i => i.id === d.id)) this.interrupts.push(d);
      break;
    case "checkpoint": this.refreshHist(); break;
    case "thread.status": {
      const t = this.threads.find(x => x.thread_id === ev.thread_id); if (t) { t.status = d.status; this.paintThreadSel(); }
      break;
    }
    }
    if (this.btab === "eventos" && this.evPaint && ev.id && !this.evT) this.evT = setTimeout(() => { this.evT = null; if (this.evList?.isConnected) this.evPaint(); }, 400);
  }
  refreshHist = debounce(async () => { await this.loadHistory(); if (this.btab === "historia") this.paintBottom(); }, 500);
  async afterRun(d) {
    await Promise.all([this.loadState(), this.loadHistory(), this.loadMessages()]);
    this.tokens = {};
    if (this.mode === "chat") this.paintChat();
    try { const v = await api.get(`/threads/${this.threadId}/view`); this.view = {nodos: v.nodos, aristas: new Set(v.aristas_recorridas), run: v.run}; } catch { /* */ }
    if (d.status === "interrupted" && this.interrupts.length && App.state.settings?.abrir_interrupts !== false) { this.btab = "interrupts"; if (this.bp.classList.contains("c")) this.toggleBottom(false); }
    else if (d.status === "success") this.btab = this.btab === "interrupts" ? "salida" : this.btab;
    if (d.status === "error") toast("Error: " + (d.error || ""), "err", 9000);
    if (d.status !== "interrupted" || d.motivo !== "breakpoint") this.stepMode = d.status === "interrupted" && this.stepMode;
    this.paintControls(); this.render(); this.paintBottom(); this.paintRight();
  }
}
})();
