/* Biblioteca de módulos, editor de código (CodeMirror) e historial con diff. */
"use strict";
(() => {
const {h, api, toast, toastErr, modal, confirmDlg, promptDlg, pill} = App;

/* Editor de un módulo: carga, Ctrl+S guarda con validación (422 → línea marcada; 409 → conflicto). */
App.moduleEditor = (wrap, file, {onSaved} = {}) => {
  let sha = null, errLine = null;
  const cm = CodeMirror(wrap, {mode: "python", theme: document.documentElement.dataset.theme === "claro" ? "default" : "material-darker",
    lineNumbers: true, indentUnit: 4, matchBrackets: true, value: "", extraKeys: {"Ctrl-S": () => save(), "Cmd-S": () => save(),
      Tab: c => c.execCommand(c.somethingSelected() ? "indentMore" : "insertSoftTab")}});
  const status = h("span", {class: "mut", style: {fontSize: "12px"}}, "");
  async function load() {
    const r = await api.get(`/modules/${file}/source`);
    sha = r.sha; cm.setValue(r.source); cm.clearHistory(); mark(null); status.textContent = "";
  }
  function mark(line) {
    if (errLine != null) cm.removeLineClass(errLine, "background", "cm-errline");
    errLine = line;
    if (line != null) { cm.addLineClass(line, "background", "cm-errline"); cm.scrollIntoView({line, ch: 0}, 80); }
  }
  async function save() {
    try {
      const r = await api.put(`/modules/${file}/source`, {source: cm.getValue(), sha});
      sha = r.sha; mark(null);
      status.textContent = "Guardado ✓";
      toast(`${file} guardado` + (r.usos?.length ? ` · afecta a ${r.usos.length} flujo(s)` : ""), "ok");
      App.state.modules = null;
      onSaved && onSaved(r);
    } catch (e) {
      if (e.status === 422) { mark(Math.max(0, (e.body.line || 1) - 1)); status.textContent = `Error línea ${e.body.line}: ${e.body.detail}`; toast(`Error en la línea ${e.body.line}: ${e.body.detail}`, "err"); }
      else if (e.status === 409) { toast("El módulo cambió fuera del editor: recarga para ver la versión actual", "err"); status.textContent = "Conflicto de versión"; }
      else toastErr(e);
    }
  }
  setTimeout(() => cm.refresh(), 0);
  return {cm, load, save, status, get sha() { return sha; }};
};

function lineDiff(a, b) {
  const A = a.split("\n"), B = b.split("\n");
  if (A.length * B.length > 4e6) return [["d", "(demasiado grande para comparar)"]];
  const n = A.length, m = B.length, L = Array.from({length: n + 1}, () => new Uint32Array(m + 1));
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--) L[i][j] = A[i] === B[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
  const out = []; let i = 0, j = 0;
  while (i < n && j < m) {
    if (A[i] === B[j]) { out.push([" ", A[i]]); i++; j++; }
    else if (L[i + 1][j] >= L[i][j + 1]) out.push(["d", A[i++]]);
    else out.push(["a", B[j++]]);
  }
  while (i < n) out.push(["d", A[i++]]);
  while (j < m) out.push(["a", B[j++]]);
  return out;
}

App.moduleHistory = async (file, currentSource, onRestore) => {
  const hist = await api.get(`/modules/${file}/history`);
  if (!hist.length) { toast("Este módulo no tiene versiones anteriores"); return; }
  const diffBox = h("div", {class: "diff"});
  const sel = h("select", {}, hist.map(x => h("option", {value: x.ts}, x.ts)));
  const show = () => {
    const v = hist.find(x => x.ts === sel.value);
    diffBox.replaceChildren(...lineDiff(v.source, currentSource()).filter((d, i, arr) => d[0] !== " " || arr.slice(Math.max(0, i - 2), i + 3).some(x => x[0] !== " "))
      .map(([k, l]) => h("div", {class: k === " " ? "" : k}, (k === "a" ? "+ " : k === "d" ? "- " : "  ") + l)));
  };
  sel.addEventListener("change", show);
  show();
  modal({title: `Historial de ${file}`, wide: true, body: h("div", {},
    h("div", {class: "row"}, h("span", {class: "mut"}, "Versión guardada:"), sel, h("span", {class: "mut", style: {fontSize: "12px"}}, "– = esa versión · + = actual")), h("div", {style: {height: "8px"}}), diffBox),
    actions: [{label: "Cerrar"}, {label: "Restaurar esta versión", class: "primary", onclick: async () => {
      try { await api.post(`/modules/${file}/restore`, {ts: sel.value}); toast("Versión restaurada", "ok"); onRestore && onRestore(); }
      catch (e) { toastErr(e); return false; }
    }}]});
};

App.nuevoModulo = async (from) => {
  const n = await promptDlg(from ? `Duplicar ${from}` : "Nuevo módulo", from ? "" : "", "Nombre del módulo");
  if (!n) return null;
  try { const r = await api.post("/modules", {name: n, from: from || undefined}); App.state.modules = null; toast(`Creado ${r.file}`, "ok"); return r.file; }
  catch (e) { toastErr(e); return null; }
};

App.views.modulos = async (main, parts) => {
  if (parts[0]) return editorPage(main, parts[0]);
  const page = h("div", {class: "page"});
  main.append(page);
  const q = h("input", {placeholder: "Buscar…", style: {width: "220px"}});
  page.append(h("header", {}, h("h2", {}, "Módulos"), h("span", {class: "sp"}), q,
    h("button", {class: "primary", onclick: async () => { const f = await App.nuevoModulo(); if (f) App.go(`#/modulos/${f}`); }}, "+ Nuevo módulo")));
  const body = h("div");
  page.append(body);
  let mods = await App.loadModules(true);
  q.addEventListener("input", paint);
  function paint() {
    const t = q.value.toLowerCase();
    const list = mods.filter(m => !t || (m.file + m.meta.name + m.meta.desc + m.meta.category).toLowerCase().includes(t));
    const cats = [...new Set(list.map(m => m.meta.category || "General"))].sort();
    body.replaceChildren(...cats.map(c => h("div", {},
      h("div", {class: "sec"}, h("h3", {}, c)),
      h("div", {class: "modgrid"}, list.filter(m => (m.meta.category || "General") === c).map(card)))));
    if (!list.length) body.append(h("p", {class: "empty"}, "Ningún módulo coincide."));
  }
  function card(m) {
    const io = `${(m.meta.inputs || []).concat((m.meta.inputs_from_config || []).map(x => `⟨${x}⟩`)).join(", ") || "—"} → ${(m.meta.outputs || []).concat((m.meta.outputs_from_config || []).map(x => `⟨${x}⟩`)).join(", ") || "—"}`;
    return h("div", {class: "mod" + (m.error ? " err" : "")},
      h("div", {class: "h"}, h("i", {}, m.meta.icon || "▣"), h("b", {class: "grow"}, m.meta.name), h("span", {class: "mut mono", style: {fontSize: "11px"}}, m.file)),
      h("div", {class: "mut", style: {fontSize: "12.5px"}}, m.meta.desc || ""),
      h("div", {class: "io"}, io),
      h("div", {class: "row wrap"}, h("span", {class: "pill"}, m.meta.kind), h("span", {class: "pill"}, m.meta.exec + (m.meta.exec === "proceso" ? " · " + m.meta.interprete : "")),
        m.meta.gpu ? h("span", {class: "pill running"}, "GPU " + m.meta.gpu) : null,
        h("span", {class: "mut", style: {fontSize: "12px"}}, m.usos.length ? `usado en ${m.usos.length}` : "sin usar")),
      m.error ? h("div", {style: {color: "var(--red)", fontSize: "12px"}}, `Línea ${m.line}: ${m.error}`) : null,
      h("div", {class: "row wrap"},
        h("button", {class: "sm primary", onclick: () => App.go(`#/modulos/${m.file}`)}, "Editar"),
        h("button", {class: "sm", onclick: async () => { const f = await App.nuevoModulo(m.file); if (f) { mods = await App.loadModules(true); paint(); } }}, "Duplicar"),
        h("button", {class: "sm danger", disabled: m.usos.length > 0, title: m.usos.length ? "En uso: " + m.usos.join(", ") : "", onclick: async () => {
          if (!await confirmDlg(`¿Borrar ${m.file}? Se guarda una copia en el historial.`, "Borrar", true)) return;
          try { await api.del(`/modules/${m.file}`); mods = await App.loadModules(true); paint(); } catch (e) { toastErr(e); }
        }}, "Borrar")));
  }
  paint();
};

async function editorPage(main, file) {
  const wrap = h("div", {class: "editor-full"});
  main.append(wrap);
  const cmwrap = h("div", {class: "cmwrap"});
  const ed = App.moduleEditor(cmwrap, file);
  const mods = await App.loadModules(true);
  const m = mods.find(x => x.file === file);
  if (!m) throw new App.ApiError(404, {detail: `No existe el módulo ${file}`});
  wrap.append(h("header", {},
    h("button", {class: "sm", onclick: () => App.go("#/modulos")}, "← Módulos"),
    h("b", {}, `${m.meta.icon || ""} ${m.meta.name}`), h("span", {class: "mut mono"}, file),
    h("span", {class: "mut", style: {fontSize: "12px"}}, m.usos.length ? `afecta a nodos en ${m.usos.length} flujo(s)` : "sin usar"),
    h("span", {class: "sp"}), ed.status,
    h("button", {class: "sm", onclick: () => App.moduleHistory(file, () => ed.cm.getValue(), () => ed.load())}, "Historial"),
    h("button", {class: "sm", onclick: async () => { const f = await App.nuevoModulo(file); if (f) App.go(`#/modulos/${f}`); }}, "Duplicar"),
    h("button", {class: "sm primary", onclick: () => ed.save()}, "Guardar ", h("kbd", {}, "Ctrl+S"))), cmwrap);
  await ed.load();
  ed.cm.refresh();
  ed.cm.focus();
}
})();
