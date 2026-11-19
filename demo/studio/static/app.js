/* Estudio de agentes v2 — núcleo de la GUI (sin framework ni build). Las vistas están en static/views/*.js */
"use strict";
const App = window.App = {views: {}, state: {settings: null, system: null, modules: null}};

/* ------------------------------------------------------------------ utilidades */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "html") el.innerHTML = v;
    else if (k === "value") el.value = v;
    else if (k === "checked") el.checked = !!v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const k of kids.flat(Infinity)) {
    if (k == null || k === false) continue;
    el.append(k instanceof Node ? k : document.createTextNode(String(k)));
  }
  return el;
}
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const fmtDate = t => t ? new Date(t * 1000).toLocaleString("es-ES", {dateStyle: "short", timeStyle: "short"}) : "—";
const fmtDur = s => s == null ? "—" : s < 60 ? `${s.toFixed(1)} s` : `${Math.floor(s / 60)} min ${Math.round(s % 60)} s`;
const fmtBytes = b => b == null ? "—" : b > 1e9 ? (b / 1e9).toFixed(1) + " GB" : b > 1e6 ? (b / 1e6).toFixed(1) + " MB" : (b / 1e3).toFixed(0) + " kB";
const media = p => "/api/media?path=" + encodeURIComponent(p);
const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
const clone = o => JSON.parse(JSON.stringify(o));
const STATUS_ES = {pending: "pendiente", running: "ejecutando", pausing: "pausando", drained: "en pausa", interrupted: "interrumpido",
  success: "correcto", error: "error", stopped: "detenido", idle: "libre", busy: "ocupado", waiting_gpu: "esperando GPU",
  queued: "en cola", cached: "de caché"};
const pill = (st, txt) => h("span", {class: "pill " + (st || "")}, txt || STATUS_ES[st] || st || "—");
const store = {
  get(k, d) { try { const v = localStorage.getItem("estudio." + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("estudio." + k, JSON.stringify(v)); } catch { /* sin almacenamiento */ } },
};
Object.assign(App, {$, $$, h, esc, fmtDate, fmtDur, fmtBytes, media, debounce, clone, pill, store, STATUS_ES});

/* ------------------------------------------------------------------ API */
class ApiError extends Error {
  constructor(status, body) { super(body?.detail || `HTTP ${status}`); this.status = status; this.body = body || {}; }
}
async function api(path, opts = {}) {
  const o = {method: opts.method || "GET", headers: {}, credentials: "same-origin"};
  if (opts.body !== undefined) {
    if (opts.body instanceof FormData) o.body = opts.body;
    else { o.headers["Content-Type"] = "application/json"; o.body = JSON.stringify(opts.body); }
  }
  const r = await fetch("/api" + path, o);
  if (r.status === 401) { await askToken(); return api(path, opts); }
  if (r.status === 204) return null;
  const ct = r.headers.get("content-type") || "";
  const body = ct.includes("json") ? await r.json() : await r.text();
  if (!r.ok) throw new ApiError(r.status, typeof body === "object" ? body : {detail: body});
  return body;
}
api.get = p => api(p);
api.post = (p, b) => api(p, {method: "POST", body: b ?? {}});
api.put = (p, b) => api(p, {method: "PUT", body: b});
api.patch = (p, b) => api(p, {method: "PATCH", body: b});
api.del = p => api(p, {method: "DELETE"});
App.api = api; App.ApiError = ApiError;

let tokenPrompt = null;
function askToken() {
  if (tokenPrompt) return tokenPrompt;
  tokenPrompt = new Promise(res => {
    const inp = h("input", {type: "password", placeholder: "token", autocomplete: "current-password"});
    const ok = () => {
      document.cookie = `studio_token=${encodeURIComponent(inp.value.trim())}; path=/; max-age=31536000; SameSite=Strict`;
      bg.remove(); tokenPrompt = null; res();
    };
    inp.addEventListener("keydown", e => { if (e.key === "Enter") ok(); });
    const bg = h("div", {class: "modal-bg"}, h("div", {class: "modal"},
      h("header", {}, h("h2", {}, "Token del estudio")),
      h("div", {class: "body"}, h("p", {class: "mut"}, "Escribe el token que aparece al arrancar el estudio (arrancar.sh) o en settings.json."), inp),
      h("footer", {}, h("button", {class: "primary", onclick: ok}, "Entrar"))));
    document.body.append(bg); inp.focus();
  });
  return tokenPrompt;
}

/* ------------------------------------------------------------------ avisos y diálogos */
function toast(msg, kind = "", ms = 4500) {
  let box = $(".toasts");
  if (!box) { box = h("div", {class: "toasts"}); document.body.append(box); }
  const t = h("div", {class: "toast " + kind}, msg);
  box.append(t);
  setTimeout(() => t.remove(), ms);
}
function toastErr(e) { toast(e?.message || String(e), "err", 7000); }
function modal({title, body, actions = [], wide = false, onclose}) {
  const close = () => { bg.remove(); document.removeEventListener("keydown", key); onclose && onclose(); };
  const key = e => { if (e.key === "Escape") close(); };
  const foot = h("footer", {}, actions.map(a => h("button", {class: a.class || "", onclick: async () => {
    if (a.onclick) { const r = await a.onclick(close); if (r === false) return; }
    if (a.close !== false) close();
  }}, a.label)));
  const bg = h("div", {class: "modal-bg", onmousedown: e => { if (e.target === bg) close(); }},
    h("div", {class: "modal" + (wide ? " wide" : "")},
      h("header", {}, h("h2", {}, title), h("button", {class: "ghost sm", onclick: close, title: "Cerrar"}, "✕")),
      h("div", {class: "body"}, body), actions.length ? foot : null));
  document.body.append(bg);
  document.addEventListener("keydown", key);
  return {close, el: bg};
}
function confirmDlg(msg, okLabel = "Aceptar", danger = false) {
  return new Promise(res => {
    let done = false;
    modal({title: "Confirmar", body: h("p", {}, msg), onclose: () => { if (!done) res(false); }, actions: [
      {label: "Cancelar", onclick: () => { done = true; res(false); }},
      {label: okLabel, class: danger ? "danger" : "primary", onclick: () => { done = true; res(true); }}]});
  });
}
function promptDlg(title, value = "", label = "") {
  return new Promise(res => {
    let done = false;
    const inp = h("input", {value});
    const m = modal({title, body: h("label", {class: "f"}, h("span", {}, label), inp), onclose: () => { if (!done) res(null); },
      actions: [{label: "Cancelar", onclick: () => { done = true; res(null); }},
        {label: "Aceptar", class: "primary", onclick: () => { done = true; res(inp.value.trim()); }}]});
    inp.addEventListener("keydown", e => { if (e.key === "Enter") { done = true; res(inp.value.trim()); m.close(); } });
    setTimeout(() => { inp.focus(); inp.select(); }, 0);
  });
}
function download(name, text, type = "application/json") {
  const a = h("a", {href: URL.createObjectURL(new Blob([text], {type})), download: name});
  document.body.append(a); a.click(); a.remove();
}
Object.assign(App, {toast, toastErr, modal, confirmDlg, promptDlg, download});

/* ------------------------------------------------------------------ formularios */
/* Campo de CONFIG → control. get() devuelve el valor tipado. */
function field(f, value, opts = {}) {
  const t = f.type || "text";
  let input, get;
  const v = value === undefined ? f.default : value;
  if (t === "checkbox") {
    input = h("input", {type: "checkbox", checked: !!v});
    get = () => input.checked;
    const lab = h("label", {class: "chk"}, input, f.label || f.key, f.help ? h("small", {class: "mut"}, " · " + f.help) : null);
    return {el: lab, get, input};
  }
  if (t === "textarea" || t === "code") {
    input = h("textarea", {class: t === "code" ? "code" : "", rows: t === "code" ? 6 : 4, spellcheck: t !== "code"}, "");
    input.value = v ?? "";
    get = () => input.value;
  } else if (t === "json") {
    input = h("textarea", {class: "code", rows: 5, spellcheck: false});
    input.value = typeof v === "string" ? v : JSON.stringify(v ?? null, null, 2);
    get = () => { const s = input.value.trim(); if (!s) return null; try { return JSON.parse(s); } catch { throw new Error(`«${f.label || f.key}» no es JSON válido`); } };
  } else if (t === "select" || t === "model") {
    input = h("select", {});
    const fill = list => {
      input.innerHTML = "";
      const vals = [...new Set(["", ...list.map(String), v == null ? "" : String(v)])];
      for (const o of vals) input.append(h("option", {value: o}, o === "" ? (f.setting ? "(por defecto de ajustes)" : "—") : o));
      input.value = v == null ? "" : String(v);
    };
    fill(f.options || []);
    if (t === "model") App.ollamaModels().then(fill).catch(() => {});
    const numeric = (f.options || []).length && f.options.every(o => typeof o === "number");
    get = () => numeric && input.value !== "" ? Number(input.value) : input.value;
  } else if (t === "number") {
    input = h("input", {type: "number", step: "any", value: v ?? ""});
    get = () => input.value === "" ? null : Number(input.value);
  } else if (t === "password") {
    input = h("input", {type: "password", value: v ?? "", autocomplete: "off"});
    get = () => input.value;
  } else {
    input = h("input", {value: v ?? "", list: t === "state_key" && opts.keys ? opts.keysId : null, spellcheck: false});
    get = () => input.value;
  }
  const help = [f.help, f.setting ? `vacío = ajuste ${f.setting}` : null].filter(Boolean).join(" · ");
  const lab = h("label", {class: "f"}, h("span", {}, f.label || f.key), input, help ? h("small", {}, help) : null);
  return {el: lab, get, input};
}
function form(fields, values = {}, opts = {}) {
  const items = fields.map(f => ({f, ...field(f, values[f.key], opts)}));
  return {el: h("div", {}, items.map(i => i.el)), values() { const o = {}; for (const i of items) o[i.f.key] = i.get(); return o; }, items};
}
/* Formulario desde JSON Schema (interrupts con response_schema) */
function schemaForm(schema, initial = {}) {
  const props = schema?.properties || {};
  const fields = Object.entries(props).map(([k, p]) => ({key: k, label: p.title || k + ((schema.required || []).includes(k) ? " *" : ""),
    type: p.type === "boolean" ? "checkbox" : p.type === "number" || p.type === "integer" ? "number" :
      p.enum ? "select" : p.type === "string" ? ((initial[k] || "").length > 60 || k === "valor" ? "textarea" : "text") : "json",
    options: p.enum, help: p.description}));
  return form(fields, initial);
}
Object.assign(App, {field, form, schemaForm});

let modelsCache = null;
App.ollamaModels = () => modelsCache || (modelsCache = api.get("/ollama/models").catch(() => []));
App.loadModules = async (force) => { if (force || !App.state.modules) App.state.modules = await api.get("/modules"); return App.state.modules; };

/* ------------------------------------------------------------------ SSE */
App.sse = {
  es: null, handlers: new Set(), lastId: 0,
  start() {
    if (this.es) return;
    const url = "/api/events" + (this.lastId ? `?after=${this.lastId}` : "");
    this.es = new EventSource(url, {withCredentials: true});
    const types = ["run.status", "node.start", "node.progress", "node.log", "node.partial", "node.end", "interrupt",
      "checkpoint", "token", "thread.status", "edge", "run.queued", "experiment.status", "cron.fired"];
    for (const t of types) this.es.addEventListener(t, e => {
      let ev; try { ev = JSON.parse(e.data); } catch { return; }
      if (ev.id) this.lastId = Math.max(this.lastId, ev.id);
      for (const fn of this.handlers) { try { fn(ev); } catch (err) { console.warn(err); } }
    });
    this.es.onerror = () => {
      if (this.es && this.es.readyState === 2) { this.es = null; setTimeout(() => this.start(), 3000); }
    };
  },
  on(fn) { this.handlers.add(fn); return () => this.handlers.delete(fn); },
};

/* ------------------------------------------------------------------ shell y rutas */
const MENU = [
  ["inicio", "⌂", "Inicio"], ["estudio", "◈", "Estudio"], ["hilos", "≡", "Hilos"], ["salidas", "▶", "Salidas"],
  ["flujos", "▦", "Flujos"], ["modulos", "⧉", "Módulos"], ["asistentes", "☺", "Agentes"], ["memoria", "◎", "Memoria"],
  ["trazas", "≋", "Trazas"], ["datasets", "▤", "Datasets"], ["programaciones", "⏲", "Programaciones"], ["config", "⚙", "Configuración"]];

function shell() {
  const collapsed = store.get("menu_c", false);
  const app = h("div", {id: "app", class: collapsed ? "menu-c" : ""},
    h("aside", {id: "menu"},
      h("div", {class: "brand"},
        h("button", {class: "ghost sm", title: "Plegar/desplegar menú", id: "menuToggle", onclick: () => {
          app.classList.toggle("menu-c"); store.set("menu_c", app.classList.contains("menu-c"));
        }}, "☰"),
        h("b", {}, "Estudio ", h("span", {}, "de agentes"))),
      h("nav", {}, MENU.map(([id, ico, lbl]) => h("a", {href: "#/" + id, "data-r": id, title: lbl}, h("i", {}, ico), h("span", {class: "lbl"}, lbl)))),
      h("div", {class: "foot", id: "menuFoot"},
        h("div", {class: "row"}, h("span", {id: "footRun"}, "—"), h("span", {class: "lbl mut", id: "footRunTxt"}, "sin ejecuciones")),
        h("div", {class: "bar", id: "footBar"}, h("i", {})),
        h("div", {class: "lbl", id: "footVram"}, "VRAM —"))),
    h("div", {style: {display: "flex", flexDirection: "column", minWidth: 0, minHeight: 0}},
      h("div", {id: "warn"}),
      h("main", {id: "main", style: {flex: "1"}})));
  document.body.append(app);
}

let current = null;
async function route() {
  const hash = location.hash || "#/inicio";
  const [path, qs] = hash.slice(2).split("?");
  const parts = path.split("/").filter(Boolean);
  const name = parts[0] || "inicio";
  const params = Object.fromEntries(new URLSearchParams(qs || ""));
  for (const a of $$("#menu nav a")) a.classList.toggle("on", a.dataset.r === name);
  const view = App.views[name];
  if (current && current.destroy) { try { current.destroy(); } catch (e) { console.warn(e); } }
  current = null;
  const main = $("#main");
  main.innerHTML = "";
  if (!view) { main.append(h("div", {class: "page"}, h("p", {class: "empty"}, "Página no encontrada"))); return; }
  try {
    current = (await view(main, parts.slice(1), params)) || null;
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) main.append(h("div", {class: "page"}, h("p", {class: "empty"}, e.message)));
    else { console.warn(e); toastErr(e); }
  }
}
App.go = (hash) => { if (location.hash === hash) route(); else location.hash = hash; };
App.route = route;

/* pie del menú: ejecución activa y VRAM */
const footState = {runs: {}};
async function refreshFoot() {
  try {
    const s = await api.get("/system");
    App.state.system = s;
    $("#footVram").textContent = s.gpu ? `VRAM ${s.gpu.mem_used} / ${s.gpu.mem_total} MiB` : "VRAM —";
    const warn = $("#warn");
    warn.innerHTML = "";
    if (s.token_corto) warn.append(h("div", {class: "warnbar"}, "Aviso: el token es corto (< 16 caracteres). Cámbialo en Configuración → Seguridad."));
    const act = await api.get("/runs/active");
    footState.runs = Object.fromEntries(act.map(r => [r.run_id, r]));
    paintFoot();
  } catch (e) { /* sin conexión */ }
}
function paintFoot() {
  const runs = Object.values(footState.runs);
  const r = runs[0];
  $("#footRun").replaceChildren(r ? pill(r.status) : h("span", {class: "mut"}, "●"));
  $("#footRunTxt").textContent = r ? `${r.project}${runs.length > 1 ? ` (+${runs.length - 1})` : ""}` : "sin ejecuciones";
  $("#footBar i").style.width = r ? `${Math.round((r.progress || 0) * 100)}%` : "0";
}
App.sse.on(ev => {
  if (ev.type === "run.status") {
    if (["pending", "running", "pausing"].includes(ev.data.status)) {
      footState.runs[ev.run_id] = {...(footState.runs[ev.run_id] || {}), run_id: ev.run_id, status: ev.data.status,
        project: footState.runs[ev.run_id]?.project || "…"};
      if (!footState.runs[ev.run_id].project || footState.runs[ev.run_id].project === "…") refreshFoot();
    } else {
      delete footState.runs[ev.run_id];
      if (App.state.settings?.sonido && ["success", "error", "interrupted"].includes(ev.data.status)) beep(ev.data.status);
      if (App.state.settings?.notificaciones && "Notification" in window && Notification.permission === "granted")
        new Notification("Estudio de agentes", {body: `Ejecución: ${STATUS_ES[ev.data.status] || ev.data.status}`});
    }
    paintFoot();
  }
});
function beep(st) {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const o = ctx.createOscillator(); const g = ctx.createGain();
    o.frequency.value = st === "error" ? 220 : st === "interrupted" ? 520 : 660;
    g.gain.value = 0.05; o.connect(g); g.connect(ctx.destination); o.start(); o.stop(ctx.currentTime + 0.18);
  } catch { /* sin audio */ }
}
App.setFootProgress = (runId, p) => { if (footState.runs[runId]) { footState.runs[runId].progress = p; paintFoot(); } };

async function boot() {
  shell();
  try { App.state.settings = (await api.get("/settings")).values; } catch (e) { toastErr(e); }
  document.documentElement.dataset.theme = App.state.settings?.tema === "claro" ? "claro" : "oscuro";
  document.title = App.state.settings?.nombre || "Estudio de agentes";
  App.sse.start();
  refreshFoot();
  setInterval(refreshFoot, 10000);
  window.addEventListener("hashchange", route);
  route();
}
document.addEventListener("DOMContentLoaded", boot);
