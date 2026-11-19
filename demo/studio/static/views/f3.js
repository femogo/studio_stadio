/* Fase 3: Datasets y experimentos, Programaciones, pestaña Prompt (playground), Exportar ▾ y MCP. */
"use strict";
(() => {
const {h, api, toast, toastErr, modal, confirmDlg, promptDlg, fmtDate, fmtDur, pill, store, form, download} = App;
const J = v => JSON.stringify(v ?? null, null, 2);
const J1 = (v, n = 90) => { const s = JSON.stringify(v ?? null); return s.length > n ? s.slice(0, n) + "…" : s; };
const parseJ = (txt, what, def = {}) => { const s = (txt || "").trim(); if (!s) return def; try { return JSON.parse(s); } catch { throw new Error(`${what}: JSON no válido`); } };
const ta = (v, rows = 6, id) => { const t = h("textarea", {class: "code", rows, spellcheck: false, id}); t.value = typeof v === "string" ? v : J(v); return t; };
const score = s => s == null ? h("span", {class: "mut"}, "—") : h("span", {class: "score " + (s >= 0.999 ? "ok" : s <= 0.001 ? "ko" : "mid")}, s.toFixed(2));
App.f3 = {};

/* ------------------------------------------------------------------ añadir a un dataset desde una ejecución */
App.f3.addToDataset = async (run) => {
  const dss = await api.get("/datasets");
  const sel = h("select", {id: "dsDestino"}, h("option", {value: ""}, "+ dataset nuevo…"), dss.map(d => h("option", {value: d.dataset_id}, `${d.name} (${d.n_examples})`)));
  const nuevo = h("input", {placeholder: "Nombre del dataset nuevo", value: `Ejemplos · ${run.project}`});
  const exp = ta("", 5);
  exp.placeholder = "(vacío = salida final del hilo)";
  sel.addEventListener("change", () => { nuevo.parentElement.style.display = sel.value ? "none" : ""; });
  modal({title: "Añadir al dataset", body: h("div", {},
    h("p", {class: "mut", style: {fontSize: "12.5px"}}, "La entrada de la ejecución será el input del ejemplo; lo esperado, por defecto, las claves de salida del estado final."),
    h("label", {class: "f"}, h("span", {}, "Dataset"), sel), h("label", {class: "f"}, h("span", {}, "Nombre"), nuevo),
    h("label", {class: "f"}, h("span", {}, "Esperado (JSON, opcional)"), exp)),
  actions: [{label: "Cancelar"}, {label: "Añadir", class: "primary", onclick: async () => {
    try {
      let did = sel.value;
      if (!did) did = (await api.post("/datasets", {name: nuevo.value.trim(), project: run.project})).dataset_id;
      const expected = exp.value.trim() ? parseJ(exp.value, "Esperado") : undefined;
      await api.post(`/datasets/${did}/examples/from-run`, {run_id: run.run_id, expected});
      toast("Ejemplo añadido", "ok");
    } catch (e) { toastErr(e); return false; }
  }}]});
};

/* ------------------------------------------------------------------ Datasets */
App.views.datasets = async (main, parts, params) => {
  const page = h("div", {class: "page wide"});
  main.append(page);
  let sele = parts[0] || params.dataset || null, tab = params.tab || "ejemplos";
  const left = h("div", {class: "alist"}), right = h("div", {class: "adet"});
  page.append(h("header", {}, h("h2", {}, "Datasets"), h("span", {class: "mut"}, "ejemplos, experimentos con evaluadores y comparación"), h("span", {class: "sp"}),
    h("button", {onclick: importar}, "Importar JSONL"),
    h("button", {class: "primary", id: "nuevoDataset", onclick: () => dsDlg(null)}, "+ Nuevo")), h("div", {class: "split2"}, left, right));
  let projects = await api.get("/projects");

  async function paint() {
    const dss = await api.get("/datasets");
    if (!dss.some(d => d.dataset_id === sele)) sele = dss[0]?.dataset_id || null;
    left.replaceChildren(...(dss.length ? dss.map(d => h("div", {class: "it" + (d.dataset_id === sele ? " on" : ""), "data-ds": d.dataset_id,
      onclick: () => { sele = d.dataset_id; history.replaceState(null, "", `#/datasets/${sele}`); paint(); }},
      h("b", {}, d.name), h("small", {class: "mut"}, `${d.n_examples} ejemplos · ${d.n_experiments} experimentos`))) :
      [h("p", {class: "mut"}, "No hay datasets. Crea uno, impórtalo o añade ejemplos desde una ejecución (Trazas o Hilos).")]));
    if (sele) await detail(sele); else right.replaceChildren();
  }

  async function detail(did) {
    const d = await api.get(`/datasets/${did}`);
    const tabs = [["ejemplos", `Ejemplos (${d.n_examples})`], ["experimentos", `Experimentos (${d.n_experiments})`]];
    const body = h("div", {});
    right.replaceChildren(
      h("div", {class: "row wrap"}, h("h3", {style: {textTransform: "none", fontSize: "16px", color: "var(--fg)"}}, d.name),
        d.project ? h("span", {class: "mut"}, "· " + d.project) : null, h("span", {class: "sp"}),
        h("button", {class: "sm", onclick: () => dsDlg(d)}, "Editar"),
        h("a", {class: "btn sm", href: `/api/datasets/${did}/export`, download: `${d.name}.jsonl`}, "Exportar JSONL"),
        h("button", {class: "sm danger", onclick: async () => { if (await confirmDlg(`¿Borrar el dataset «${d.name}» con sus ejemplos y experimentos?`, "Borrar", true)) { await api.del(`/datasets/${did}`).catch(toastErr); sele = null; paint(); } }}, "Borrar")),
      d.desc ? h("p", {class: "mut"}, d.desc) : null,
      h("div", {class: "tabs", style: {margin: "8px 0"}}, tabs.map(([k, l]) => h("button", {class: tab === k ? "on" : "", "data-dtab": k, onclick: () => { tab = k; detail(did); }}, l))),
      body);
    if (tab === "ejemplos") await ejemplos(body, d); else await experimentos(body, d);
  }

  async function ejemplos(body, d) {
    const ejs = await api.get(`/datasets/${d.dataset_id}/examples`);
    body.append(h("div", {class: "row"}, h("span", {class: "sp"}), h("button", {class: "sm primary", id: "nuevoEjemplo", onclick: () => ejDlg(d, null)}, "+ Ejemplo")),
      ejs.length ? h("div", {class: "tablewrap"}, h("table", {class: "t"}, h("thead", {}, h("tr", {}, ["#", "Entrada", "Esperado", "Origen", ""].map(t => h("th", {}, t)))),
        h("tbody", {}, ejs.map((e, i) => h("tr", {"data-ex": e.example_id}, h("td", {class: "mut"}, i + 1), h("td", {class: "mono"}, J1(e.input)), h("td", {class: "mono"}, J1(e.expected)),
          h("td", {class: "mut"}, e.source_run ? "ejecución" : "manual"),
          h("td", {class: "acts"}, h("button", {class: "sm", onclick: () => ejDlg(d, e)}, "Editar"),
            h("button", {class: "sm danger", onclick: async () => { await api.del(`/examples/${e.example_id}`).catch(toastErr); detail(d.dataset_id); }}, "Borrar"))))))) :
        h("p", {class: "mut"}, "Sin ejemplos."));
  }

  function ejDlg(d, e) {
    const tin = ta(e?.input || {}, 6, "ejInput"), tex = ta(e?.expected ?? {}, 6, "ejExpected");
    modal({title: e ? "Editar ejemplo" : "Nuevo ejemplo", wide: true, body: h("div", {},
      h("label", {class: "f"}, h("span", {}, "Entrada (objeto JSON: la entrada de la ejecución)"), tin),
      h("label", {class: "f"}, h("span", {}, "Esperado (objeto JSON o vacío)"), tex)),
    actions: [{label: "Cancelar"}, {label: "Guardar", class: "primary", onclick: async () => {
      try {
        const body = {input: parseJ(tin.value, "Entrada"), expected: parseJ(tex.value, "Esperado", null)};
        if (e) await api.patch(`/examples/${e.example_id}`, body); else await api.post(`/datasets/${d.dataset_id}/examples`, body);
        detail(d.dataset_id);
      } catch (x) { toastErr(x); return false; }
    }}]});
  }

  async function experimentos(body, d) {
    const xs = await api.get(`/experiments?dataset=${d.dataset_id}`);
    const marcados = new Set();
    const cmpBtn = h("button", {class: "sm", disabled: true, id: "compararBtn", onclick: () => comparar([...marcados])}, "Comparar");
    body.append(h("div", {class: "row"}, h("span", {class: "sp"}), cmpBtn,
      h("button", {class: "sm primary", id: "nuevoExperimento", onclick: () => expDlg(d)}, "▶ Nuevo experimento")),
      xs.length ? h("div", {class: "tablewrap"}, h("table", {class: "t"}, h("thead", {}, h("tr", {}, ["", "Nombre", "Flujo", "Estado", "Progreso", "Resumen", "Fecha", ""].map(t => h("th", {}, t)))),
        h("tbody", {}, xs.map(x => {
          const chk = h("input", {type: "checkbox"});
          chk.addEventListener("change", () => { chk.checked ? marcados.add(x.experiment_id) : marcados.delete(x.experiment_id); cmpBtn.disabled = marcados.size < 2; });
          return h("tr", {"data-exp": x.experiment_id}, h("td", {}, chk), h("td", {}, h("a", {href: "#", onclick: ev => { ev.preventDefault(); verExp(x.experiment_id); }}, x.name)),
            h("td", {}, x.project + (x.assistant_id ? ` · asist. v${x.assistant_version}` : "")), h("td", {}, pill(x.status)), h("td", {class: "mono"}, `${x.hechos}/${x.n}`),
            h("td", {}, Object.entries(x.resumen || {}).map(([k, v]) => h("span", {style: {marginRight: "8px"}}, h("small", {class: "mut"}, k + " "), score(v)))),
            h("td", {class: "mut"}, fmtDate(x.created)),
            h("td", {class: "acts"}, ["pending", "running"].includes(x.status) ? h("button", {class: "sm", onclick: async () => { await api.post(`/experiments/${x.experiment_id}/stop`).catch(toastErr); }}, "Detener") :
              h("button", {class: "sm danger", onclick: async () => { if (await confirmDlg(`¿Borrar el experimento «${x.name}»?`, "Borrar", true)) { await api.del(`/experiments/${x.experiment_id}`).catch(toastErr); detail(d.dataset_id); } }}, "Borrar")));
        })))) : h("p", {class: "mut"}, "Sin experimentos. Un experimento ejecuta cada ejemplo en un hilo nuevo y lo puntúa con evaluadores."));
  }

  function evaluadoresEditor(evs) {
    const box = h("div", {});
    const lista = (evs || [{name: "correcto", type: "expr", expr: "output == expected"}]).map(e => ({...e}));
    const paintE = () => box.replaceChildren(...lista.map((e, i) => {
      const name = h("input", {value: e.name, style: {width: "140px"}}); name.addEventListener("input", () => { e.name = name.value; });
      const tipo = h("select", {style: {width: "auto"}}, h("option", {value: "expr"}, "expresión"), h("option", {value: "llm"}, "LLM-juez")); tipo.value = e.type;
      tipo.addEventListener("change", () => { e.type = tipo.value; paintE(); });
      const cuerpo = e.type === "expr" ? h("input", {class: "mono", value: e.expr || "", placeholder: "output.get('x') == expected.get('x')"}) :
        h("textarea", {rows: 3, placeholder: "¿La respuesta {output} es correcta para {input}? Esperado: {expected}"}, "");
      if (e.type === "llm") cuerpo.value = e.prompt || "";
      cuerpo.addEventListener("input", () => { if (e.type === "expr") e.expr = cuerpo.value; else e.prompt = cuerpo.value; });
      return h("div", {class: "evrow"}, h("div", {class: "row"}, name, tipo, h("span", {class: "sp"}), h("button", {class: "sm ghost", onclick: () => { lista.splice(i, 1); paintE(); }}, "✕")), cuerpo);
    }), h("button", {class: "sm", onclick: () => { lista.push({name: `ev${lista.length + 1}`, type: "expr", expr: ""}); paintE(); }}, "+ Evaluador"),
    h("small", {class: "mut", style: {display: "block", marginTop: "4px"}}, "Expresión: variables output, expected, input (dicts); bool → 1/0, número → puntuación. LLM-juez: marcadores {input} {output} {expected}; devuelve score 0..1."));
    paintE();
    return {el: box, values: () => lista.map(e => e.type === "expr" ? {name: e.name.trim(), type: "expr", expr: e.expr || ""} : {name: e.name.trim(), type: "llm", prompt: e.prompt || ""})};
  }

  async function expDlg(d) {
    projects = await api.get("/projects");
    const ps = h("select", {id: "expProyecto"}, projects.map(p => h("option", {value: p.slug}, p.name)));
    ps.value = d.project && projects.some(p => p.slug === d.project) ? d.project : projects[0]?.slug || "";
    const as = h("select", {}, h("option", {value: ""}, "(sin agente)"));
    const loadAs = async () => { as.replaceChildren(h("option", {value: ""}, "(sin agente)"), ...(await api.get(`/projects/${ps.value}/assistants`).catch(() => [])).map(a => h("option", {value: a.assistant_id}, `${a.name} (v${a.active_version})`))); };
    ps.addEventListener("change", loadAs); await loadAs();
    const name = h("input", {placeholder: "(automático)"});
    const conc = h("input", {type: "number", min: 1, max: 4, value: 1});
    const ev = evaluadoresEditor(null);
    modal({title: `Nuevo experimento sobre «${d.name}»`, wide: true, body: h("div", {},
      h("div", {class: "policy"}, h("label", {class: "f"}, h("span", {}, "Flujo"), ps), h("label", {class: "f"}, h("span", {}, "Agente"), as),
        h("label", {class: "f"}, h("span", {}, "Nombre"), name), h("label", {class: "f"}, h("span", {}, "Ejemplos a la vez (1–4)"), conc)),
      h("h3", {style: {margin: "8px 0"}}, "Evaluadores"), ev.el),
    actions: [{label: "Cancelar"}, {label: "Lanzar", class: "primary", onclick: async () => {
      try {
        const r = await api.post("/experiments", {dataset_id: d.dataset_id, project: ps.value, assistant_id: as.value || undefined,
          name: name.value.trim() || undefined, concurrency: +conc.value || 1, evaluators: ev.values()});
        toast("Experimento lanzado", "ok"); tab = "experimentos"; detail(d.dataset_id); verExp(r.experiment_id);
      } catch (e) { toastErr(e); return false; }
    }}]});
  }

  async function verExp(eid) {
    const box = h("div", {});
    let off = null;
    const pinta = async () => {
      const x = await api.get(`/experiments/${eid}`).catch(() => null);
      if (!x) return;
      box.replaceChildren(
        h("div", {class: "row wrap"}, pill(x.status), h("span", {class: "mono"}, `${x.hechos}/${x.n}`), h("span", {class: "mut"}, `${x.project}${x.t1 && x.t0 ? " · " + fmtDur(x.t1 - x.t0) : ""}`),
          Object.entries(x.resumen || {}).map(([k, v]) => h("span", {}, h("small", {class: "mut"}, k + " "), score(v))), h("span", {class: "sp"}),
          !["pending", "running"].includes(x.status) ? h("button", {class: "sm", onclick: () => rescoreDlg(x)}, "Volver a puntuar") : null),
        x.error ? h("p", {style: {color: "var(--red)"}}, x.error) : null,
        h("div", {class: "tablewrap", style: {maxHeight: "60vh", overflow: "auto"}}, h("table", {class: "t"},
          h("thead", {}, h("tr", {}, ["Entrada", "Esperado", "Salida", ...x.evaluators.map(e => e.name), "Estado", "Duración", ""].map(t => h("th", {}, t)))),
          h("tbody", {}, x.results.map(r => h("tr", {}, h("td", {class: "mono"}, J1(r.input, 60)), h("td", {class: "mono"}, J1(r.expected, 60)), h("td", {class: "mono"}, J1(r.output, 80)),
            x.evaluators.map(e => h("td", {title: r.scores?.[e.name]?.comentario || ""}, score(r.scores?.[e.name]?.score))),
            h("td", {}, pill(r.status), r.error ? h("small", {class: "mut", title: r.error}, " ⓘ") : null), h("td", {}, r.duration != null ? fmtDur(r.duration) : "—"),
            h("td", {class: "acts"}, r.thread_id ? h("button", {class: "sm", onclick: () => App.go(`#/estudio/${x.project}?hilo=${r.thread_id}`)}, "Hilo") : null)))))));
      return x;
    };
    const x = await pinta();
    off = App.sse.on(ev => { if (ev.type === "experiment.status" && ev.data.experiment_id === eid) pinta(); });
    modal({title: `Experimento «${x?.name || eid}»`, wide: true, body: box, actions: [{label: "Cerrar"}], onclose: () => { off && off(); if (sele) detail(sele); }});
  }

  function rescoreDlg(x) {
    const ev = evaluadoresEditor(x.evaluators);
    modal({title: "Volver a puntuar (sin re-ejecutar)", wide: true, body: ev.el, actions: [{label: "Cancelar"}, {label: "Puntuar", class: "primary", onclick: async () => {
      try { await api.post(`/experiments/${x.experiment_id}/rescore`, {evaluators: ev.values()}); toast("Puntuando…", "ok"); } catch (e) { toastErr(e); return false; }
    }}]});
  }

  async function comparar(ids) {
    let c;
    try { c = await api.get(`/experiments/compare?ids=${ids.join(",")}`); } catch (e) { toastErr(e); return; }
    const evs = [...new Set(c.experimentos.flatMap(x => x.evaluators.map(e => e.name)))];
    const head = ["Entrada", "Esperado", ...c.experimentos.flatMap(x => [`${x.name}: salida`, ...evs.map(e => `${x.name}: ${e}`)])];
    modal({title: "Comparar experimentos", wide: true, body: h("div", {},
      h("div", {class: "row wrap", style: {marginBottom: "8px"}}, c.experimentos.map(x => h("span", {}, h("b", {}, x.name + " "), evs.map(e => h("span", {style: {marginRight: "6px"}}, h("small", {class: "mut"}, e + " "), score(c.resumen[x.experiment_id]?.[e])))))),
      h("div", {class: "tablewrap", style: {maxHeight: "65vh", overflow: "auto"}}, h("table", {class: "t", id: "tablaComparar"}, h("thead", {}, h("tr", {}, head.map(t => h("th", {}, t)))),
        h("tbody", {}, c.filas.map(f => {
          const vals = c.experimentos.map(x => evs.map(e => f.por_experimento[x.experiment_id]?.scores?.[e]?.score ?? null));
          const distinto = evs.some((_, j) => new Set(vals.map(v => v[j])).size > 1);
          return h("tr", {class: distinto ? "chg" : ""}, h("td", {class: "mono"}, J1(f.input, 50)), h("td", {class: "mono"}, J1(f.expected, 50)),
            c.experimentos.map((x, i) => [h("td", {class: "mono"}, J1(f.por_experimento[x.experiment_id]?.output, 50)), vals[i].map(v => h("td", {}, score(v)))]));
        }))))), actions: [{label: "Cerrar"}]});
  }

  function dsDlg(d) {
    const name = h("input", {value: d?.name || "", id: "dsNombre"});
    const desc = h("textarea", {rows: 2}, ""); desc.value = d?.desc || "";
    const ps = h("select", {}, h("option", {value: ""}, "(ninguno)"), projects.map(p => h("option", {value: p.slug}, p.name))); ps.value = d?.project || "";
    modal({title: d ? "Editar dataset" : "Nuevo dataset", body: h("div", {}, h("label", {class: "f"}, h("span", {}, "Nombre"), name),
      h("label", {class: "f"}, h("span", {}, "Descripción"), desc), h("label", {class: "f"}, h("span", {}, "Flujo sugerido"), ps)),
    actions: [{label: "Cancelar"}, {label: "Guardar", class: "primary", onclick: async () => {
      try {
        const b = {name: name.value.trim(), desc: desc.value, project: ps.value || null};
        if (d) await api.patch(`/datasets/${d.dataset_id}`, b); else sele = (await api.post("/datasets", b)).dataset_id;
        paint();
      } catch (e) { toastErr(e); return false; }
    }}]});
  }

  function importar() {
    const inp = h("input", {type: "file", accept: ".jsonl,.ndjson,.json,text/plain"});
    inp.addEventListener("change", async () => {
      const f = inp.files[0]; if (!f) return;
      try {
        const r = await fetch("/api/datasets/import", {method: "POST", body: await f.text(), credentials: "same-origin", headers: {"Content-Type": "application/x-ndjson"}});
        const b = await r.json();
        if (!r.ok) throw new Error(b.detail || r.status);
        sele = b.dataset_id; toast(`Importado «${b.name}» (${b.n_examples} ejemplos)`, "ok"); paint();
      } catch (e) { toastErr(e); }
    });
    inp.click();
  }

  const off = App.sse.on(ev => { if (ev.type === "experiment.status" && tab === "experimentos" && sele && !document.querySelector(".modal")) detail(sele); });
  await paint();
  return {destroy: off};
};

/* ------------------------------------------------------------------ Programaciones */
App.views.programaciones = async (main) => {
  const page = h("div", {class: "page wide"});
  main.append(page);
  const tbody = h("tbody");
  page.append(h("header", {}, h("h2", {}, "Programaciones"), h("span", {class: "mut"}, "ejecuciones periódicas (cron, hora local)"), h("span", {class: "sp"}),
    h("button", {class: "primary", id: "nuevaProg", onclick: () => cronDlg(null)}, "+ Nueva")),
  h("div", {class: "tablewrap"}, h("table", {class: "t"}, h("thead", {}, h("tr", {}, ["Activa", "Nombre", "Flujo", "Cron", "Hilo", "Último", "Próximo", ""].map(t => h("th", {}, t)))), tbody)));
  async function paint() {
    const cs = await api.get("/crons");
    tbody.replaceChildren(...(cs.length ? cs.map(c => {
      const act = h("input", {type: "checkbox", checked: c.active});
      act.addEventListener("change", async () => { await api.patch(`/crons/${c.cron_id}`, {active: act.checked}).catch(toastErr); paint(); });
      return h("tr", {"data-cron": c.cron_id}, h("td", {}, act), h("td", {}, c.name), h("td", {}, c.project), h("td", {class: "mono"}, c.cron),
        h("td", {}, c.thread_mode === "fijo" ? h("span", {class: "mono", title: c.thread_id}, "fijo " + (c.thread_id || "").slice(0, 8)) : "nuevo por disparo"),
        h("td", {}, fmtDate(c.last_run), c.last_error ? h("div", {style: {color: "var(--red)", fontSize: "11.5px"}, title: c.last_error}, c.last_error.slice(0, 60)) : null),
        h("td", {}, c.active ? fmtDate(c.next_run) : h("span", {class: "mut"}, "—")),
        h("td", {class: "acts"}, h("button", {class: "sm", onclick: async () => { try { const r = await api.post(`/crons/${c.cron_id}/run`); toast("Disparada", "ok"); App.go(`#/estudio/${c.project}?hilo=${r.thread_id}`); } catch (e) { toastErr(e); } }}, "Disparar ya"),
          h("button", {class: "sm", onclick: () => runsDlg(c)}, "Ejecuciones"), h("button", {class: "sm", onclick: () => cronDlg(c)}, "Editar"),
          h("button", {class: "sm danger", onclick: async () => { if (await confirmDlg(`¿Borrar la programación «${c.name}»?`, "Borrar", true)) { await api.del(`/crons/${c.cron_id}`).catch(toastErr); paint(); } }}, "Borrar")));
    }) : [h("tr", {}, h("td", {colspan: 8, class: "empty"}, "No hay programaciones."))]));
  }
  async function runsDlg(c) {
    const rs = await api.get(`/crons/${c.cron_id}/runs?limit=100`);
    modal({title: `Ejecuciones de «${c.name}»`, wide: true, body: rs.length ? h("div", {class: "tablewrap"}, h("table", {class: "t"}, h("thead", {}, h("tr", {}, ["Inicio", "Estado", "Duración", ""].map(t => h("th", {}, t)))),
      h("tbody", {}, rs.map(r => h("tr", {}, h("td", {}, fmtDate(r.t0)), h("td", {}, pill(r.status)), h("td", {}, r.t1 ? fmtDur(r.t1 - r.t0) : "—"),
        h("td", {class: "acts"}, h("button", {class: "sm", onclick: () => App.go(`#/estudio/${r.project}?hilo=${r.thread_id}`)}, "Abrir hilo"))))))) : h("p", {class: "mut"}, "Aún no se ha disparado."),
    actions: [{label: "Cerrar"}]});
  }
  async function cronDlg(c) {
    const projects = await api.get("/projects");
    const ps = h("select", {id: "cronProyecto"}, projects.map(p => h("option", {value: p.slug}, p.name))); ps.value = c?.project || projects[0]?.slug || "";
    const name = h("input", {value: c?.name || "", placeholder: "(nombre del flujo)"});
    const expr = h("input", {class: "mono", value: c?.cron || "0 9 * * 1-5", id: "cronExpr"});
    const prev = h("div", {class: "mut mono", style: {fontSize: "12px", minHeight: "1.2em"}, id: "cronPrev"});
    const verPrev = App.debounce(async () => {
      try {
        const r = await api.get(`/crons/preview?cron=${encodeURIComponent(expr.value.trim())}`);
        prev.style.color = r.valido ? "" : "var(--red)";
        prev.textContent = r.valido ? "Próximas: " + r.proximas.map(fmtDate).join(" · ") : r.error;
      } catch (e) { prev.style.color = "var(--red)"; prev.textContent = e.message; }
    }, 300);
    expr.addEventListener("input", verPrev); verPrev();
    const modo = h("select", {}, h("option", {value: "nuevo"}, "Hilo nuevo en cada disparo"), h("option", {value: "fijo"}, "Siempre el mismo hilo")); modo.value = c?.thread_mode || "nuevo";
    const hilo = h("select", {});
    const estr = h("select", {}, ["enqueue", "reject", "interrupt", "rollback"].map(s => h("option", {value: s}, s))); estr.value = c?.multitask_strategy || "enqueue";
    const as = h("select", {});
    const inp = ta(c?.input || {}, 4);
    const filaFijo = h("div", {class: "policy"}, h("label", {class: "f"}, h("span", {}, "Hilo"), hilo), h("label", {class: "f"}, h("span", {}, "Si el hilo está ocupado"), estr));
    const cargar = async () => {
      const ths = await api.get(`/threads?project=${ps.value}`).catch(() => []);
      hilo.replaceChildren(...ths.map(t => h("option", {value: t.thread_id}, t.title || t.thread_id.slice(0, 8)))); if (c?.thread_id) hilo.value = c.thread_id;
      const aa = await api.get(`/projects/${ps.value}/assistants`).catch(() => []);
      as.replaceChildren(h("option", {value: ""}, "(sin agente)"), ...aa.map(a => h("option", {value: a.assistant_id}, a.name))); as.value = c?.assistant_id || "";
    };
    const vis = () => { filaFijo.style.display = modo.value === "fijo" ? "" : "none"; };
    ps.addEventListener("change", cargar); modo.addEventListener("change", vis); await cargar(); vis();
    modal({title: c ? "Editar programación" : "Nueva programación", wide: true, body: h("div", {},
      h("div", {class: "policy"}, h("label", {class: "f"}, h("span", {}, "Flujo"), ps), h("label", {class: "f"}, h("span", {}, "Nombre"), name)),
      h("label", {class: "f"}, h("span", {}, "Cron (min hora día mes día-semana; 6.º campo opcional = segundos)"), expr, prev),
      h("label", {class: "f"}, h("span", {}, "Hilo"), modo), filaFijo,
      h("label", {class: "f"}, h("span", {}, "Agente"), as), h("label", {class: "f"}, h("span", {}, "Entrada (JSON)"), inp)),
    actions: [{label: "Cancelar"}, {label: "Guardar", class: "primary", onclick: async () => {
      try {
        const b = {project: ps.value, name: name.value.trim(), cron: expr.value.trim(), thread_mode: modo.value,
          thread_id: modo.value === "fijo" ? hilo.value : null, multitask_strategy: estr.value, assistant_id: as.value || null, input: parseJ(inp.value, "Entrada")};
        if (c) await api.patch(`/crons/${c.cron_id}`, b); else await api.post("/crons", b);
        paint();
      } catch (e) { toastErr(e); return false; }
    }}]});
  }
  const off = App.sse.on(ev => { if (ev.type === "cron.fired") paint(); });
  await paint();
  return {destroy: off};
};

/* ------------------------------------------------------------------ Estudio: pestaña Prompt (playground) */
App.f3.esLLM = mod => !!(mod?.meta?.llm && mod.meta.exec === "local" && ["task", "router"].includes(mod.meta.kind));
App.f3.tabPrompt = (st, body, id, n, mod) => {
  const campos = mod.config.filter(f => !["url"].includes(f.key));
  const key = `prompt_${st.slug}_${id}`;
  let vars = store.get(key, null) || [{...(n.config || {})}];
  const estado = ta(st.state?.values ? Object.fromEntries(Object.entries(st.state.values).filter(([k]) => k !== "messages")) : {}, 4, "pgState");
  const grid = h("div", {class: "pgrid"});
  const forms = [];
  const res = h("div", {class: "pgrid", id: "pgRes"});
  const pinta = () => {
    forms.length = 0;
    grid.replaceChildren(...vars.map((v, i) => {
      const f = form(campos, v);
      forms.push(f);
      return h("div", {class: "pcol"}, h("div", {class: "row"}, h("b", {}, `Variante ${i + 1}`), h("span", {class: "sp"}),
        vars.length > 1 ? h("button", {class: "sm ghost", onclick: () => { leer(); vars.splice(i, 1); pinta(); }}, "✕") : null), f.el,
        h("button", {class: "sm", onclick: () => guardar(i)}, "Guardar en el nodo"));
    }));
  };
  const leer = () => { vars = forms.map(f => f.values()); store.set(key, vars); };
  const guardar = i => {
    try { leer(); } catch (e) { toastErr(e); return; }
    st.change(p => { p.nodes[id].config = {...(p.nodes[id].config || {}), ...vars[i]}; });
    toast("Configuración del nodo actualizada", "ok");
  };
  const probar = async () => {
    let state;
    try { leer(); state = parseJ(estado.value, "Estado"); } catch (e) { toastErr(e); return; }
    res.replaceChildren(h("p", {class: "mut"}, "Probando…"));
    try {
      const r = await api.post(`/projects/${st.slug}/nodes/${id}/playground`, {state, variantes: vars, assistant_id: st.assistantId || undefined});
      res.replaceChildren(...r.resultados.map((x, i) => h("div", {class: "pcol"}, h("div", {class: "row"}, h("b", {}, `Variante ${i + 1}`), h("span", {class: "sp"}),
        h("small", {class: "mut"}, `${fmtDur(x.duracion)} · ${x.tokens} tokens`)),
        x.error ? h("div", {style: {color: "var(--red)", fontSize: "12.5px"}}, x.error) : null,
        x.prompt != null ? h("details", {}, h("summary", {}, "Prompt enviado"), h("pre", {class: "loglines"}, (x.sistema ? `[sistema]\n${x.sistema}\n\n` : "") + x.prompt)) : null,
        h("pre", {class: "transcript pgout"}, typeof x.salida === "string" ? x.salida : J(x.salida)))));
    } catch (e) { res.replaceChildren(); toastErr(e); }
  };
  pinta();
  body.append(h("p", {class: "mut", style: {fontSize: "12px"}}, "Ejecuta solo este nodo con el estado de abajo y hasta 4 variantes de su configuración. No crea hilos ni checkpoints."),
    h("label", {class: "f"}, h("span", {}, "Estado de partida (JSON; por defecto, el del checkpoint seleccionado)"), estado),
    h("div", {class: "row", style: {margin: "6px 0"}}, h("button", {class: "primary", id: "pgProbar", onclick: probar}, "▶ Probar"),
      h("button", {class: "sm", disabled: vars.length >= 4, onclick: () => { try { leer(); } catch (e) { toastErr(e); return; } vars.push({...vars[vars.length - 1]}); pinta(); }}, "+ Variante"),
      h("button", {class: "sm ghost", onclick: () => { vars = [{...(n.config || {})}]; store.set(key, null); pinta(); }}, "Restablecer")),
    grid, h("h3", {style: {margin: "10px 0 6px"}}, "Resultados"), res);
};

/* ------------------------------------------------------------------ Estudio: Exportar ▾ */
App.f3.exportDlg = async (slug) => {
  let motivo = null;
  try { const r = await api.get(`/projects/${slug}/export?format=funcional&check=1`); if (!r.ok) motivo = r.motivos.join("; "); } catch { /* sin red */ }
  modal({title: "Exportar flujo", body: h("div", {class: "exportops"},
    h("button", {onclick: async () => download(`${slug}.json`, JSON.stringify(await api.get(`/projects/${slug}/export`), null, 2))}, h("b", {}, "JSON"), h("small", {class: "mut"}, "el flujo, para importarlo en otro estudio")),
    h("a", {class: "btn", href: `/api/projects/${slug}/export?format=py`, download: `export_${slug}.zip`, id: "expPy"}, h("b", {}, "Python (zip)"),
      h("small", {class: "mut"}, "graph.py + langgraph.json + módulos + compilador: se ejecuta sin el estudio")),
    motivo ? h("div", {class: "mut", style: {fontSize: "12px"}}, "API funcional no disponible: " + motivo) :
      h("a", {class: "btn", href: `/api/projects/${slug}/export?format=funcional`, download: `funcional_${slug}.py`}, h("b", {}, "API funcional (.py)"),
        h("small", {class: "mut"}, "@entrypoint/@task; va incluido en el zip"))),
  actions: [{label: "Cerrar"}]});
};

/* ------------------------------------------------------------------ Configuración: MCP */
App.f3.mcpSection = (page) => {
  const box = h("div", {id: "mcpEstado"}, h("p", {class: "mut"}, "Comprobando servidores…"));
  const cargar = async () => {
    let ss;
    try { ss = await api.get("/mcp/servers"); } catch (e) { box.replaceChildren(h("p", {style: {color: "var(--red)"}}, e.message)); return; }
    box.replaceChildren(...(ss.length ? ss.map(s => h("div", {class: "mcpsrv"}, h("div", {class: "row"}, h("b", {}, s.nombre), h("span", {class: "mut"}, s.transport), pill(s.ok ? "success" : "error", s.ok ? `${s.tools.length} herramientas` : "sin conexión")),
      s.error ? h("small", {style: {color: "var(--red)"}}, s.error) : null,
      s.tools.map(t => h("div", {class: "row wrap", style: {fontSize: "12.5px"}}, h("span", {class: "mono"}, `${s.nombre}__${t.name}`), h("span", {class: "mut"}, t.desc),
        h("span", {class: "sp"}), h("button", {class: "sm", onclick: () => probar(s.nombre, t)}, "Probar")))))
      : [h("p", {class: "mut"}, "No hay servidores MCP. Añádelos arriba en «Servidores MCP» (JSON) y guarda.")]));
  };
  const probar = (srv, t) => {
    const args = ta(Object.fromEntries(Object.keys(t.args_schema?.properties || {}).map(k => [k, ""])), 5);
    const out = h("pre", {class: "loglines"}, "");
    modal({title: `Probar ${srv}__${t.name}`, wide: true, body: h("div", {}, h("p", {class: "mut"}, t.desc), h("label", {class: "f"}, h("span", {}, "Argumentos (JSON)"), args), out),
      actions: [{label: "Cerrar"}, {label: "Llamar", class: "primary", onclick: async () => {
        try { const r = await api.post("/mcp/call", {servidor: srv, herramienta: t.name, args: parseJ(args.value, "Argumentos")}); out.textContent = J(r.resultado); }
        catch (e) { out.textContent = e.message; }
        return false;
      }}]});
  };
  page.append(h("div", {class: "sec"}, h("h3", {}, "Servidores MCP (estado)"), h("button", {class: "sm", onclick: cargar}, "Comprobar")), box);
  cargar();
};
})();
