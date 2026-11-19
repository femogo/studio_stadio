/* Fase 2: vistas Asistentes, Memoria (Store) y Trazas. */
"use strict";
(() => {
const {h, api, toast, toastErr, modal, confirmDlg, promptDlg, fmtDate, fmtDur, pill, store, form} = App;
const J = v => JSON.stringify(v ?? {}, null, 2);
const parseJ = (txt, what) => { const s = (txt || "").trim(); if (!s) return {}; try { return JSON.parse(s); } catch { throw new Error(`${what}: JSON no válido`); } };

async function projectSelect(current, onchange) {
  const ps = await api.get("/projects");
  const sel = h("select", {style: {width: "auto"}, id: "selProyecto"}, ps.map(p => h("option", {value: p.slug}, p.name)));
  sel.value = ps.some(p => p.slug === current) ? current : (ps[0]?.slug || "");
  sel.addEventListener("change", () => onchange(sel.value));
  return {sel, ps};
}

/* ------------------------------------------------------------------ Asistentes */
App.views.asistentes = async (main, _parts, params) => {
  const page = h("div", {class: "page"});
  main.append(page);
  let slug = params.proyecto || store.get("ultimo_proyecto", null);
  const {sel, ps} = await projectSelect(slug, v => { slug = v; history.replaceState(null, "", `#/asistentes?proyecto=${v}`); paint(); });
  slug = sel.value;
  const list = h("div", {class: "split2"});
  page.append(h("header", {}, h("h2", {}, "Agentes"), h("span", {class: "mut"}, "un flujo con nombre, contexto y ajustes propios, versionado"),
    h("span", {class: "sp"}), sel, h("button", {class: "primary", id: "nuevoAsistente", onclick: () => editDlg(null)}, "+ Nuevo")), list);
  if (!ps.length) { list.append(h("p", {class: "empty"}, "No hay flujos.")); return; }
  let proyecto = null, sele = params.asistente || null;

  async function paint() {
    proyecto = await api.get(`/projects/${slug}`);
    const as = await api.get(`/projects/${slug}/assistants`);
    if (!as.some(a => a.assistant_id === sele)) sele = as[0]?.assistant_id || null;
    const left = h("div", {class: "alist"}, as.length ? as.map(a => h("div", {class: "it" + (a.assistant_id === sele ? " on" : ""), "data-a": a.assistant_id,
      onclick: () => { sele = a.assistant_id; paint(); }},
      h("b", {}, a.name), h("small", {class: "mut"}, `v${a.active_version} activa · ${a.n_versions} versión(es) · ${fmtDate(a.updated)}`))) :
      h("p", {class: "mut"}, "Este flujo no tiene agentes. Un agente guarda un contexto y ajustes de nodos con nombre; cada cambio crea una versión."));
    const right = h("div", {class: "adet"});
    list.replaceChildren(left, right);
    if (sele) await detail(right, sele);
  }

  async function detail(box, aid) {
    const [a, vs] = await Promise.all([api.get(`/assistants/${aid}`), api.get(`/assistants/${aid}/versions`)]);
    let cmpA = vs[1]?.version ?? vs[0]?.version, cmpB = vs[0]?.version;
    const diffBox = h("div", {});
    const paintDiff = () => {
      const va = vs.find(v => v.version === +cmpA), vb = vs.find(v => v.version === +cmpB);
      if (!va || !vb) { diffBox.replaceChildren(); return; }
      const rows = [];
      for (const part of ["context", "node_overrides"]) {
        const keys = new Set([...Object.keys(va[part] || {}), ...Object.keys(vb[part] || {})]);
        for (const k of keys) {
          const x = JSON.stringify(va[part]?.[k]), y = JSON.stringify(vb[part]?.[k]);
          rows.push(h("tr", {class: x === y ? "" : "chg"}, h("td", {}, part), h("td", {class: "mono"}, k), h("td", {class: "mono"}, x ?? "—"), h("td", {class: "mono"}, y ?? "—")));
        }
      }
      diffBox.replaceChildren(rows.length ? h("table", {class: "t"}, h("thead", {}, h("tr", {}, ["", "clave", `v${cmpA}`, `v${cmpB}`].map(t => h("th", {}, t)))), h("tbody", {}, rows))
        : h("p", {class: "mut"}, "Sin diferencias."));
    };
    const vsel = (val, set) => { const s = h("select", {style: {width: "auto"}}, vs.map(v => h("option", {value: v.version}, `v${v.version}`))); s.value = val; s.addEventListener("change", () => { set(s.value); paintDiff(); }); return s; };
    box.replaceChildren(
      h("div", {class: "row wrap"}, h("h3", {style: {textTransform: "none", fontSize: "16px", color: "var(--fg)"}}, a.name), pill("success", `v${a.active_version} activa`),
        h("span", {class: "sp"}),
        h("button", {class: "sm", onclick: () => editDlg(a)}, "Editar (versión nueva)"),
        h("button", {class: "sm", onclick: async () => { const n = await promptDlg("Renombrar agente", a.name, "Nombre"); if (n) { await api.patch(`/assistants/${aid}`, {name: n}).catch(toastErr); paint(); } }}, "Renombrar"),
        h("button", {class: "sm", onclick: () => App.go(`#/estudio/${slug}`)}, "Abrir flujo"),
        h("button", {class: "sm danger", onclick: async () => { if (await confirmDlg(`¿Borrar el agente «${a.name}» y sus versiones?`, "Borrar", true)) { await api.del(`/assistants/${aid}`).catch(toastErr); sele = null; paint(); } }}, "Borrar")),
      h("div", {class: "mut mono", style: {fontSize: "11px", margin: "4px 0 12px"}}, aid),
      h("h3", {}, "Versiones"),
      h("div", {class: "tablewrap"}, h("table", {class: "t"}, h("thead", {}, h("tr", {}, ["Versión", "Contexto", "Ajustes de nodos", "Fecha", ""].map(t => h("th", {}, t)))),
        h("tbody", {}, vs.map(v => h("tr", {},
          h("td", {}, `v${v.version}`, v.version === a.active_version ? " ✓" : ""),
          h("td", {class: "mono"}, JSON.stringify(v.context).slice(0, 80)),
          h("td", {class: "mono"}, JSON.stringify(v.node_overrides).slice(0, 80)),
          h("td", {}, fmtDate(v.created)),
          h("td", {class: "acts"}, v.version === a.active_version ? h("span", {class: "mut"}, "activa") :
            h("button", {class: "sm", onclick: async () => { await api.post(`/assistants/${aid}/activate/${v.version}`).catch(toastErr); paint(); }}, "Activar"))))))),
      vs.length > 1 ? h("div", {style: {marginTop: "14px"}}, h("div", {class: "row"}, h("h3", {}, "Comparar"), vsel(cmpA, x => { cmpA = x; }), "↔", vsel(cmpB, x => { cmpB = x; })), diffBox) : null);
    paintDiff();
  }

  function editDlg(a) {
    const schema = proyecto.context_schema || [];
    const name = h("input", {value: a?.name || "", id: "asNombre"});
    const cf = form(schema, a?.context || {});
    const ctxTa = h("textarea", {class: "code", rows: 4, spellcheck: false});
    ctxTa.value = J(a?.context || {});
    const ovTa = h("textarea", {class: "code", rows: 7, spellcheck: false, id: "asOverrides"});
    ovTa.value = J(a?.node_overrides || {});
    const nodos = Object.entries(proyecto.nodes || {}).map(([id, n]) => `${id} (${n.module})`).join(", ");
    modal({title: a ? `Editar «${a.name}» (crea la versión ${a.n_versions + 1})` : "Nuevo agente", wide: true, body: h("div", {},
      h("label", {class: "f"}, h("span", {}, "Nombre"), name),
      h("h3", {style: {margin: "6px 0 8px"}}, "Contexto"),
      schema.length ? cf.el : h("div", {}, h("p", {class: "mut", style: {fontSize: "12px"}}, "El flujo no declara context_schema; el contexto debe quedar vacío."), ctxTa),
      h("h3", {style: {margin: "6px 0 8px"}}, "Ajustes de nodos (node_overrides)"),
      h("label", {class: "f"}, h("span", {}, "{nodo: {campo: valor}} — sustituyen la configuración del nodo en las ejecuciones con este agente"), ovTa,
        h("small", {}, "Nodos: " + nodos))),
    actions: [{label: "Cancelar"}, {label: a ? "Guardar versión nueva" : "Crear", class: "primary", onclick: async () => {
      try {
        const context = schema.length ? Object.fromEntries(Object.entries(cf.values()).filter(([, v]) => v !== "" && v != null)) : parseJ(ctxTa.value, "Contexto");
        const node_overrides = parseJ(ovTa.value, "Ajustes de nodos");
        if (a) { await api.patch(`/assistants/${a.assistant_id}`, {name: name.value.trim() || a.name, context, node_overrides}); sele = a.assistant_id; }
        else { const r = await api.post(`/projects/${slug}/assistants`, {name: name.value.trim(), context, node_overrides}); sele = r.assistant_id; }
        toast("Agente guardado", "ok"); paint();
      } catch (e) { toastErr(e); return false; }
    }}]});
  }
  await paint();
};

/* ------------------------------------------------------------------ Memoria (Store) */
App.views.memoria = async (main, _parts, params) => {
  const page = h("div", {class: "page"});
  main.append(page);
  let ns = params.ns || "";
  const tree = h("div", {class: "alist"});
  const right = h("div", {class: "adet"});
  const q = h("input", {placeholder: "Buscar por significado (embeddings de Ollama)…", id: "memQuery"});
  q.addEventListener("keydown", e => { if (e.key === "Enter") items(); });
  page.append(h("header", {}, h("h2", {}, "Memoria"), h("span", {class: "mut"}, "Store a largo plazo, compartido entre hilos"), h("span", {class: "sp"}),
    h("button", {class: "primary", onclick: () => itemDlg(null)}, "+ Nuevo item")), h("div", {class: "split2"}, tree, right));

  async function paintTree() {
    const nss = await api.get("/store/namespaces?limit=500");
    const paths = nss.map(n => n.join("/")).sort();
    if (!ns && paths.length) ns = paths[0];
    tree.replaceChildren(...(paths.length ? paths.map(p => h("div", {class: "it" + (p === ns ? " on" : ""), "data-ns": p, onclick: () => { ns = p; history.replaceState(null, "", `#/memoria?ns=${encodeURIComponent(p)}`); paintTree(); items(); }},
      h("span", {class: "mono"}, p.split("/").map((s, i) => i ? ["/", h("b", {}, s)] : s)))) : [h("p", {class: "mut"}, "El Store está vacío. Los módulos Memoria y las herramientas recordar/consultar_memoria escriben aquí.")]));
  }
  async function items() {
    if (!ns) { right.replaceChildren(h("p", {class: "mut"}, "Elige un namespace.")); return; }
    let list;
    try { list = await api.get(`/store/items?ns=${encodeURIComponent(ns)}&limit=100` + (q.value.trim() ? `&query=${encodeURIComponent(q.value.trim())}` : "")); }
    catch (e) { toastErr(e); list = []; }
    right.replaceChildren(
      h("div", {class: "row", style: {marginBottom: "10px"}}, h("b", {class: "mono"}, ns), h("span", {class: "sp"}), q,
        h("button", {class: "sm", onclick: items}, "Buscar"), q.value ? h("button", {class: "sm ghost", onclick: () => { q.value = ""; items(); }}, "✕") : null),
      list.length ? h("div", {class: "tablewrap"}, h("table", {class: "t"}, h("thead", {}, h("tr", {}, ["Clave", "Valor", q.value ? "Similitud" : "Actualizado", ""].map(t => h("th", {}, t)))),
        h("tbody", {}, list.map(it => h("tr", {"data-key": it.key},
          h("td", {class: "mono"}, it.key), h("td", {class: "mono", style: {maxWidth: "520px", overflow: "hidden", textOverflow: "ellipsis"}}, JSON.stringify(it.value).slice(0, 200)),
          h("td", {}, it.score != null ? it.score.toFixed(3) : fmtDate(it.updated_at)),
          h("td", {class: "acts"}, h("button", {class: "sm", onclick: () => itemDlg(it)}, "Ver / editar"),
            h("button", {class: "sm danger", onclick: async () => { if (await confirmDlg(`¿Borrar ${ns}/${it.key}?`, "Borrar", true)) { await api.del(`/store/items?ns=${encodeURIComponent(ns)}&key=${encodeURIComponent(it.key)}`).catch(toastErr); await paintTree(); items(); } }}, "Borrar"))))))) :
        h("p", {class: "mut"}, "Sin items."));
  }
  function itemDlg(it) {
    const nsIn = h("input", {value: it ? it.namespace.join("/") : ns, disabled: !!it});
    const key = h("input", {value: it?.key || "", disabled: !!it});
    const ta = h("textarea", {class: "code", rows: 10, spellcheck: false});
    ta.value = J(it?.value || {texto: ""});
    const idx = h("input", {type: "checkbox", checked: true});
    modal({title: it ? `Item ${it.key}` : "Nuevo item", wide: true, body: h("div", {},
      h("label", {class: "f"}, h("span", {}, "Namespace (segmentos separados por /)"), nsIn), h("label", {class: "f"}, h("span", {}, "Clave"), key),
      h("label", {class: "f"}, h("span", {}, "Valor (objeto JSON)"), ta), h("label", {class: "chk"}, idx, "Indexar para la búsqueda semántica (necesita Ollama)"),
      it ? h("small", {class: "mut"}, `Creado ${fmtDate(it.created_at)} · actualizado ${fmtDate(it.updated_at)}`) : null),
    actions: [{label: "Cancelar"}, {label: "Guardar", class: "primary", onclick: async () => {
      try {
        const segs = nsIn.value.split("/").map(s => s.trim()).filter(Boolean);
        await api.put("/store/items", {namespace: segs, key: key.value.trim(), value: parseJ(ta.value, "Valor"), index: idx.checked});
        ns = segs.join("/"); await paintTree(); items(); toast("Guardado", "ok");
      } catch (e) { toastErr(e); return false; }
    }}]});
  }
  await paintTree(); await items();
};

/* ------------------------------------------------------------------ Trazas */
App.views.trazas = async (main, _parts, params) => {
  const page = h("div", {class: "page wide"});
  main.append(page);
  const ps = await api.get("/projects");
  const fp = h("select", {style: {width: "auto"}}, h("option", {value: ""}, "Todos los flujos"), ps.map(p => h("option", {value: p.slug}, p.name)));
  const fs = h("select", {style: {width: "auto"}}, ["", "success", "error", "stopped", "interrupted", "drained", "running", "queued"].map(s => h("option", {value: s}, s ? (App.STATUS_ES[s] || s) : "Cualquier estado")));
  const fd = h("select", {style: {width: "auto"}}, [["", "Siempre"], ["3600", "Última hora"], ["86400", "Último día"], ["604800", "Última semana"]].map(([v, l]) => h("option", {value: v}, l)));
  fp.value = params.proyecto || "";
  for (const s of [fp, fs, fd]) s.addEventListener("change", lista);
  const tbody = h("tbody");
  const det = h("div", {id: "trazaDet"});
  page.append(h("header", {}, h("h2", {}, "Trazas"), h("span", {class: "mut"}, "ejecuciones y cascada por nodo"), h("span", {class: "sp"}), fp, fs, fd),
    h("div", {class: "tablewrap", style: {maxHeight: "34vh", overflow: "auto"}}, h("table", {class: "t"},
      h("thead", {}, h("tr", {}, ["Inicio", "Flujo", "Tipo", "Estado", "Duración", "Agente", ""].map(t => h("th", {}, t)))), tbody)), det);
  let sel = params.run || null;
  async function lista() {
    const qs = new URLSearchParams({limit: "200"});
    if (fp.value) qs.set("project", fp.value);
    if (fs.value) qs.set("status", fs.value);
    if (fd.value) qs.set("since", String(Date.now() / 1000 - +fd.value));
    const runs = await api.get(`/runs?${qs}`);
    tbody.replaceChildren(...(runs.length ? runs.map(r => h("tr", {class: r.run_id === sel ? "on" : "", "data-run": r.run_id, style: {cursor: "pointer"}, onclick: () => { sel = r.run_id; history.replaceState(null, "", `#/trazas?run=${r.run_id}`); lista(); traza(); }},
      h("td", {}, fmtDate(r.t0)), h("td", {}, r.project), h("td", {}, r.kind), h("td", {}, pill(r.status, (App.STATUS_ES[r.status] || r.status) + (r.motivo ? ` (${r.motivo})` : ""))),
      h("td", {}, r.t1 ? fmtDur(r.t1 - r.t0) : "—"), h("td", {class: "mono"}, r.assistant_id ? `${r.assistant_id.slice(0, 8)} v${r.assistant_version}` : "—"),
      h("td", {class: "acts"}, h("button", {class: "sm", onclick: e => { e.stopPropagation(); App.go(`#/estudio/${r.project}?hilo=${r.thread_id}`); }}, "Abrir hilo"),
        r.kind === "input" && r.status === "success" && App.f3 ? h("button", {class: "sm", title: "Añadir esta ejecución como ejemplo de un dataset", onclick: e => { e.stopPropagation(); App.f3.addToDataset(r); }}, "→ Dataset") : null))) :
      [h("tr", {}, h("td", {colspan: 7, class: "mut"}, "Sin ejecuciones con esos filtros."))]));
  }
  async function traza() {
    if (!sel) { det.replaceChildren(h("p", {class: "mut", style: {marginTop: "14px"}}, "Elige una ejecución para ver su cascada.")); return; }
    let tr;
    try { tr = await api.get(`/runs/${sel}/trace`); } catch (e) { toastErr(e); return; }
    const r = tr.run, t0 = r.t0, dur = Math.max(0.001, tr.totals.duration || 0.001);
    const pct = t => `${Math.max(0, Math.min(100, (t - t0) / dur * 100))}%`;
    const logBox = h("pre", {class: "loglines", style: {maxHeight: "220px"}}, "Pulsa una barra para ver el log de ese nodo.");
    const rows = tr.spans.map(sp => {
      const fin = sp.t1 ?? (r.t1 || Date.now() / 1000);
      const bar = h("div", {class: "wbar " + (sp.status || ""), style: {left: pct(sp.t0), width: `calc(${pct(fin)} - ${pct(sp.t0)} + 2px)`},
        title: `${sp.label} · ${App.STATUS_ES[sp.status] || sp.status} · ${fmtDur(fin - sp.t0)}${sp.t_gpu ? ` (esperó GPU ${fmtDur(sp.t_gpu - sp.t0)})` : ""}${sp.error ? "\n" + sp.error : ""}`},
        sp.t_gpu ? h("i", {class: "gpu", style: {width: `${(sp.t_gpu - sp.t0) / Math.max(0.001, fin - sp.t0) * 100}%`}}) : null);
      return h("div", {class: "wrow", "data-span": sp.node, onclick: async () => {
        const evs = await api.get(`/threads/${r.thread_id}/events?run=${r.run_id}&types=node.log&limit=5000`);
        logBox.textContent = evs.filter(e => e.node === sp.node && (e.ns || "") === sp.ns).map(e => e.data.line).join("\n") || "(sin log)";
      }},
        h("div", {class: "wlbl"}, sp.ns ? h("span", {class: "mut"}, "↳ ") : null, sp.cached ? "⚡ " : "", sp.label, sp.attempt > 1 ? h("span", {class: "mut"}, ` · intento ${sp.attempt}`) : null),
        h("div", {class: "wtrack"}, bar), h("div", {class: "wdur mono"}, fmtDur(fin - sp.t0)));
    });
    const t = tr.totals;
    det.replaceChildren(
      h("div", {class: "row wrap", style: {margin: "16px 0 8px"}}, h("h3", {}, "Cascada"), pill(r.status),
        h("span", {class: "mut", style: {fontSize: "12.5px"}}, `${fmtDur(t.duration)} · ${t.n_spans} tramos · ${t.errores} errores · ${t.reintentos} reintentos · GPU esperada ${fmtDur(t.espera_gpu)} · ${t.tokens} tokens`),
        r.error ? h("span", {style: {color: "var(--red)", fontSize: "12.5px"}}, r.error) : null),
      h("div", {class: "wfall"}, rows.length ? rows : h("p", {class: "mut"}, "Sin nodos ejecutados.")),
      h("div", {class: "row mut", style: {gap: "14px", fontSize: "12px", margin: "6px 0 10px"}},
        h("span", {}, h("i", {class: "lg gpu"}), " espera de GPU"), h("span", {}, h("i", {class: "lg success"}), " correcto"),
        h("span", {}, h("i", {class: "lg error"}), " error"), h("span", {}, h("i", {class: "lg cached"}), " ⚡ caché")),
      logBox);
  }
  await lista(); await traza();
};
})();
