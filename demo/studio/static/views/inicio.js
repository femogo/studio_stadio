/* Inicio: sistema (cabecera), flujos y agentes; cabe en pantalla sin scroll. */
"use strict";
(() => {
const {h, api, toast, toastErr, modal, fmtDate, fmtBytes, media, pill, store} = App;

App.views.inicio = async (main) => {
  const page = h("div", {class: "page inicio"});
  main.append(page);
  const [projects, agentes, sys] = await Promise.all([api.get("/projects"), api.get("/assistants").catch(() => []),
    api.get("/system").catch(() => null)]);
  projects.sort((a, b) => (b.updated || 0) - (a.updated || 0));
  const MAX = 12;
  const activas = sys ? sys.activas : 0;
  const fecha = new Date().toLocaleDateString("es-ES", {weekday: "long", day: "numeric", month: "long"});
  let i = 0; const rv = () => `--d:${(i++) * 55}ms`;  // revelado escalonado
  // «Ver todos (N)» solo cuando hay más tarjetas de las que caben en una fila (se recalcula al cambiar el tamaño)
  const verFlujos = h("a", {href: "#/flujos", class: "vertodos", hidden: true});
  const verAgentes = h("a", {href: "#/asistentes", class: "vertodos", hidden: true});

  page.append(
    // ---------------------------------------------------------------- cabecera = sistema
    h("header", {class: "hero rv", style: rv()},
      h("div", {class: "hero-txt"},
        h("div", {class: "eyebrow"}, h("i", {class: "dot" + (sys?.ollama?.ok ? " ok" : "")}),
          App.state.settings?.nombre || "Estudio de agentes"),
        h("h2", {}, "Sistema", h("em", {}, ".")),
        h("small", {class: "fecha"}, fecha),
        sys?.versiones ? h("small", {class: "vers"}, `Python ${sys.versiones.python} · LangGraph ${sys.versiones.langgraph}`) : null),
      sys ? h("div", {class: "instr"},
        inst("GPU", sys.gpu ? `${Math.round(sys.gpu.mem_used / 1024 * 10) / 10}` : "—", sys.gpu ? `/ ${Math.round(sys.gpu.mem_total / 1024)} GB` : "",
          sys.gpu ? `${sys.gpu.name.replace(/^NVIDIA\s+/, "")} · ${sys.gpu.temp} °C` : "Sin GPU", sys.gpu ? sys.gpu.mem_used / sys.gpu.mem_total : null),
        inst("Ollama", sys.ollama.ok ? "Activo" : "Caído", "", sys.ollama.cargados.length ? sys.ollama.cargados.join(", ") : "Sin modelos cargados", null,
          sys.ollama.ok ? "ok" : "bad"),
        inst("Ejecuciones", String(activas), activas === 1 ? "activa" : "activas", activas ? "Trabajando ahora" : "En reposo", null, activas ? "live" : ""),
        inst("RAM", fmtBytes(sys.ram.total - sys.ram.disponible).replace(/\s?GB$/, ""), `/ ${fmtBytes(sys.ram.total)}`,
          `${fmtBytes(sys.ram.disponible)} disponibles`, 1 - sys.ram.disponible / sys.ram.total),
        inst("Disco", fmtBytes(sys.disco.libre).replace(/\s?GB$/, ""), "GB", `libres de ${fmtBytes(sys.disco.total)}`, 1 - sys.disco.libre / sys.disco.total))
        : h("p", {class: "mut"}, "Sin datos del sistema.")),

    // ---------------------------------------------------------------- flujos
    secHead("01", "Flujos", projects.length, "Grafos editables en el Estudio", verFlujos,
      h("button", {class: "primary", id: "nuevoProyectoBtn", onclick: () => App.nuevoProyecto()}, "Crear flujo")),
    projects.length
      ? h("div", {class: "cards", id: "inicioProyectos"}, projects.slice(0, MAX).map(p =>
        h("div", {class: "card big pcard rv", style: rv(), "data-p": p.slug, tabindex: 0,
          onclick: () => App.go(`#/estudio/${p.slug}`), onkeydown: e => { if (e.key === "Enter") App.go(`#/estudio/${p.slug}`); }},
          h("div", {class: "ctop"}, h("div", {class: "glyph", html: glyph(p.slug, p.n_nodes)}), h("span", {class: "sp"}),
            p.ultimo_status ? pill(p.ultimo_status) : h("span", {class: "pill"}, "sin ejecutar")),
          h("h4", {title: p.name}, p.name),
          h("p", {}, p.desc || "Sin descripción"),
          h("div", {class: "cfoot"},
            h("span", {}, h("b", {}, p.n_nodes), " nodos"), h("span", {}, h("b", {}, p.n_threads), " hilos"),
            h("span", {class: "sp"}), h("span", {title: fmtDate(p.updated)}, rel(p.updated))))))
      : vacio("Tu primer flujo empieza aquí",
        "Un flujo es un grafo de nodos que se edita, depura y ejecuta en el Estudio."),

    // ---------------------------------------------------------------- agentes
    secHead("02", "Agentes", agentes.length, "Flujos con identidad, contexto y versiones", verAgentes,
      h("button", {class: "primary", id: "nuevoAgenteBtn", disabled: !projects.length,
        title: projects.length ? "" : "Crea antes un flujo", onclick: () => nuevoAgente(projects)}, "Crear agente")),
    agentes.length
      ? h("div", {class: "cards", id: "inicioAgentes"}, agentes.slice(0, MAX).map(a =>
        h("div", {class: "card big acard rv", style: rv(), "data-a": a.assistant_id, tabindex: 0,
          onclick: () => { store.set("asistente_" + a.project, a.assistant_id); App.go(`#/estudio/${a.project}`); }},
          h("div", {class: "ctop"},
            h("div", {class: "avatar", style: `--hue:${hue(a.name)}`}, iniciales(a.name)),
            h("div", {class: "grow"}, h("h4", {title: a.name}, a.name),
              h("p", {}, "sobre ", h("span", {class: "flow"}, a.project_name || a.project))),
            h("button", {class: "sm ghost cfg", title: "Configurar agente", "aria-label": "Configurar agente",
              onclick: e => { e.stopPropagation(); App.go(`#/asistentes?proyecto=${a.project}&asistente=${a.assistant_id}`); }}, "Configurar")),
          h("div", {class: "cfoot"},
            h("span", {class: "ver"}, `v${a.active_version}`), h("span", {}, `${a.n_versions} ${a.n_versions === 1 ? "versión" : "versiones"}`),
            h("span", {class: "sp"}), h("span", {title: fmtDate(a.updated || a.created)}, rel(a.updated || a.created))))))
      : h("div", {id: "inicioAgentes"}, vacio("Aún no hay agentes",
        projects.length ? "Un agente ejecuta un flujo con su propio nombre, contexto y ajustes de nodos, y guarda versiones."
          : "Cuando tengas un flujo podrás crear agentes sobre él: mismo flujo, distinto nombre, contexto y ajustes.")));

  const ajustar = () => {
    unaFila(page.querySelector("#inicioProyectos.cards"), verFlujos, projects.length);
    unaFila(page.querySelector("#inicioAgentes.cards"), verAgentes, agentes.length);
  };
  ajustar();
  let raf = 0;
  const onResize = () => { cancelAnimationFrame(raf); raf = requestAnimationFrame(ajustar); };
  window.addEventListener("resize", onResize);
  return {destroy() { window.removeEventListener("resize", onResize); cancelAnimationFrame(raf); }};
};

/* Deja visible solo la primera fila de tarjetas (Inicio sin scroll) y enseña «Ver todos (N)» si se ocultó alguna. */
function unaFila(grid, ver, total) {
  if (!grid) { ver.hidden = true; return; }
  const cs = [...grid.children];
  for (const c of cs) c.hidden = false;
  const top = cs[0]?.offsetTop;
  let ocultas = 0;
  for (const c of cs) if (c.offsetTop > top) { c.hidden = true; ocultas++; }
  ver.hidden = !(ocultas || total > cs.length);
  ver.textContent = `Ver todos (${total})`;
}

function secHead(n, titulo, count, sub, ...acts) {
  return h("div", {class: "sec"},
    h("span", {class: "secn"}, n),
    h("div", {class: "sect"}, h("div", {class: "row"}, h("h3", {}, titulo), count != null ? h("span", {class: "count"}, count) : null),
      sub ? h("small", {}, sub) : null),
    h("span", {class: "sp"}), ...acts);
}
function vacio(t, p) {
  return h("div", {class: "vacio rv", style: "--d:120ms"},
    h("div", {class: "vacio-art", "aria-hidden": "true", html: `<svg viewBox="0 0 120 64"><path d="M22 32 C 44 32, 44 14, 62 14 M22 32 C 44 32, 44 50, 62 50 M78 14 C 92 14, 92 32, 98 32 M78 50 C 92 50, 92 32, 98 32"/><rect x="6" y="24" width="16" height="16" rx="4"/><rect x="62" y="6" width="16" height="16" rx="4"/><rect x="62" y="42" width="16" height="16" rx="4"/><circle cx="106" cy="32" r="8"/></svg>`}),
    h("div", {class: "grow"}, h("b", {}, t), h("p", {}, p)));
}
function inst(lbl, val, unit, sub, frac, st = "") {
  const pct = frac == null ? null : Math.max(0, Math.min(1, frac));
  return h("div", {class: "inst " + st},
    h("div", {class: "ilbl"}, st ? h("i", {class: "dot " + st}) : null, lbl),
    h("div", {class: "ival"}, h("b", {}, val), unit ? h("span", {}, " " + unit) : null),
    pct == null ? h("div", {class: "imeter none"}) : h("div", {class: "imeter" + (pct > .85 ? " hot" : "")}, h("i", {style: {width: `${(pct * 100).toFixed(1)}%`}})),
    h("small", {title: sub}, sub));
}
function rel(t) {
  if (!t) return "—";
  const s = Date.now() / 1000 - t;
  if (s < 60) return "ahora mismo";
  if (s < 3600) return `hace ${Math.floor(s / 60)} min`;
  if (s < 86400) return `hace ${Math.floor(s / 3600)} h`;
  if (s < 86400 * 7) { const d = Math.floor(s / 86400); return d === 1 ? "ayer" : `hace ${d} días`; }
  return new Date(t * 1000).toLocaleDateString("es-ES", {day: "numeric", month: "short", year: s > 86400 * 300 ? "numeric" : undefined});
}
function hash(s) { let x = 2166136261; for (const c of String(s)) { x ^= c.charCodeAt(0); x = Math.imul(x, 16777619); } return x >>> 0; }
function hue(s) { return hash(s) % 360; }
function iniciales(s) { const w = String(s || "?").trim().split(/\s+/); return ((w[0]?.[0] || "?") + (w[1]?.[0] || "")).toUpperCase(); }
/* Miniatura determinista de un grafo: columnas de nodos unidas por curvas (sólo decorativa). */
function glyph(seed, n) {
  let r = hash(seed); const rnd = () => ((r = Math.imul(r ^ (r >>> 15), 2246822507) >>> 0) % 1000) / 1000;
  const total = Math.max(2, Math.min(7, n || 2)), cols = [];
  const ncol = Math.min(total, 3 + Math.floor(rnd() * 2));  // entrada y salida de 1 nodo, el resto repartido en medio
  for (let c = 0; c < ncol; c++) cols.push(1);
  for (let k = total - ncol; k > 0; k--) cols[1 + Math.floor(rnd() * (ncol - 2))]++;
  const W = 76, H = 30, pts = cols.map((k, ci) => Array.from({length: k}, (_, j) =>
    [cols.length === 1 ? W / 2 : 5 + ci * (W - 10) / (cols.length - 1), H / 2 + (j - (k - 1) / 2) * 12]));
  let d = "";
  for (let c = 1; c < pts.length; c++) for (const [x2, y2] of pts[c]) {
    const [x1, y1] = pts[c - 1][Math.floor(rnd() * pts[c - 1].length)], m = (x1 + x2) / 2;
    d += `M${x1} ${y1}C${m} ${y1} ${m} ${y2} ${x2} ${y2}`;
  }
  const dots = pts.flat().map(([x, y], k) => `<circle cx="${x}" cy="${y}" r="${k === 0 ? 3.6 : 3}"${k === 0 ? ' class="s"' : ""}/>`).join("");
  return `<svg viewBox="0 0 ${W} ${H}"><path d="${d}"/>${dots}</svg>`;
}
/* Nuevo agente = asistente de un proyecto: nombre + proyecto; el contexto y los ajustes se editan después en Agentes */
function nuevoAgente(projects) {
  const ps = h("select", {id: "naProyecto"}, projects.map(p => h("option", {value: p.slug}, p.name)));
  const nombre = h("input", {placeholder: "Mi agente", id: "naNombre"});
  modal({title: "Nuevo agente", body: h("div", {},
    h("p", {class: "mut", style: {fontSize: "12.5px"}}, "Un agente ejecuta un flujo con su propio nombre, contexto y ajustes de nodos. Puedes crear varios agentes sobre el mismo flujo."),
    h("label", {class: "f"}, h("span", {}, "Flujo"), ps), h("label", {class: "f"}, h("span", {}, "Nombre"), nombre)),
  actions: [{label: "Cancelar"}, {label: "Crear", class: "primary", onclick: async () => {
    try {
      const a = await api.post(`/projects/${ps.value}/assistants`, {name: nombre.value.trim() || "Agente"});
      App.go(`#/asistentes?proyecto=${ps.value}&asistente=${a.assistant_id}`);
    } catch (e) { toastErr(e); return false; }
  }}]});
}

App.videoCard = (v, onDelete) => h("div", {class: "card"},
  h("div", {class: "thumb", onclick: () => App.playVideo(v)},
    h("video", {src: media(v.path) + "#t=0.5", preload: "metadata", muted: true}), v.legacy ? h("span", {class: "pill v1"}, "v1") : null),
  h("h4", {title: v.thread_title || ""}, v.legacy ? `v1 · ${v.thread_title}` : (v.thread_title || v.project)),
  h("p", {}, v.texto || "—"),
  h("div", {class: "mut", style: {fontSize: "11.5px"}}, `${v.project} · ${fmtDate(v.ts)}`),
  onDelete ? h("div", {class: "acts"},
    h("a", {href: media(v.path), download: ""}, h("button", {class: "sm"}, "Descargar")),
    !v.legacy && v.thread_id ? h("button", {class: "sm", onclick: () => App.go(`#/estudio/${v.project}?hilo=${v.thread_id}`)}, "Abrir hilo") : null,
    !v.legacy ? h("button", {class: "sm danger", onclick: () => onDelete(v)}, "Borrar") : null) : null);

App.playVideo = (v) => modal({title: v.legacy ? `Vídeo v1 · ${v.thread_title}` : (v.thread_title || "Vídeo"), wide: true,
  body: h("div", {class: "outgrid"}, h("video", {src: media(v.path), controls: true, autoplay: true}),
    h("div", {class: "transcript"}, h("div", {class: "who"}, "Transcripción"), v.texto || "—"))});

/* ------------------------------------- asistente de la plantilla «Clip de personaje» (Nuevo proyecto) → /quick-video */
async function crearVideo(nombreInicial = "") {
  const imgs = await api.get("/images").catch(() => []);
  let origen = "texto", imgMode = "existente", imagen = (imgs.find(i => i.name === "cleo_0.png") || imgs[0] || {}).path || "";
  const nombreProy = h("input", {placeholder: "Clip · Cleopatra", id: "qvProyecto", value: nombreInicial});
  const personaje = h("input", {value: "Cleopatra VII", id: "qvPersonaje"});
  const texto = h("textarea", {rows: 3, id: "qvTexto"}, "");
  texto.value = "Napoleón, tú llegaste a Egipto con cañones y sabios. Yo lo goberné veinte años.";
  const tema = h("textarea", {rows: 3, id: "qvTema"});
  tema.value = "Responde a Napoleón, que presume de su expedición a Egipto de 1798.";
  const prompt = h("textarea", {rows: 3}, "");
  prompt.value = "photorealistic portrait of Cleopatra VII, ancient Egyptian queen, looking directly at camera, frontal, head and shoulders, neutral closed mouth, gold diadem, soft cinematic key light, 85mm, sharp focus";
  const voz = h("select", {}, ["ef_dora", "em_alex", "em_santa"].map(v => h("option", {value: v}, v)));
  voz.value = App.state.settings?.video?.voz || "ef_dora";
  const nombre = h("input", {value: "CLEOPATRA VII"});
  const subtitulo = h("input", {value: "Reina de Egipto (51–30 a.C.)"});
  const iniciar = h("input", {type: "checkbox", checked: true});
  const grid = h("div", {class: "imgpick"});
  const paintImgs = (list) => {
    grid.innerHTML = "";
    for (const i of list) grid.append(h("img", {src: media(i.path), title: i.name, class: i.path === imagen ? "on" : "", loading: "lazy",
      onclick: e => { imagen = i.path; for (const x of grid.children) x.classList.remove("on"); e.target.classList.add("on"); }}));
  };
  paintImgs(imgs);
  const upload = h("input", {type: "file", accept: "image/png,image/jpeg"});
  upload.addEventListener("change", async () => {
    if (!upload.files[0]) return;
    const fd = new FormData(); fd.append("file", upload.files[0]);
    try { const r = await api("/images", {method: "POST", body: fd}); imagen = r.path; paintImgs(await api.get("/images")); toast("Imagen subida", "ok"); }
    catch (e) { toastErr(e); }
  });
  const segO = seg([["texto", "Texto fijo"], ["tema", "Tema para el LLM"]], v => { origen = v; paint(); });
  const segI = seg([["existente", "Elegir o subir"], ["sdxl", "Generar con SDXL"]], v => { imgMode = v; paint(); });
  const wTexto = h("label", {class: "f"}, h("span", {}, "Texto que dirá el personaje"), texto);
  const wTema = h("label", {class: "f"}, h("span", {}, "Tema (el LLM escribe el guion y tú lo revisas)"), tema);
  const wImg = h("div", {}, grid, h("label", {class: "f", style: {marginTop: "8px"}}, h("span", {}, "Subir retrato (PNG/JPG, frontal, boca cerrada)"), upload));
  const wPrompt = h("label", {class: "f"}, h("span", {}, "Prompt del retrato"), prompt);
  function paint() {
    wTexto.classList.toggle("hidden", origen !== "texto"); wTema.classList.toggle("hidden", origen !== "tema");
    wImg.classList.toggle("hidden", imgMode !== "existente"); wPrompt.classList.toggle("hidden", imgMode !== "sdxl");
  }
  paint();
  modal({title: "Nuevo flujo · Clip de personaje", wide: true, body: h("div", {},
    h("div", {class: "row wrap", style: {alignItems: "flex-start", gap: "14px"}},
      h("div", {class: "grow", style: {minWidth: "260px"}},
        h("label", {class: "f"}, h("span", {}, "Nombre del flujo (opcional)"), nombreProy),
        h("label", {class: "f"}, h("span", {}, "Personaje"), personaje),
        h("div", {class: "f"}, h("span", {class: "mut", style: {fontSize: "12px"}}, "Origen del texto"), h("div", {}, segO)),
        wTexto, wTema,
        h("div", {class: "row"}, h("label", {class: "f grow"}, h("span", {}, "Rótulo"), nombre), h("label", {class: "f grow"}, h("span", {}, "Subtítulo"), subtitulo)),
        h("label", {class: "f"}, h("span", {}, "Voz"), voz)),
      h("div", {class: "grow", style: {minWidth: "260px"}},
        h("div", {class: "f"}, h("span", {class: "mut", style: {fontSize: "12px"}}, "Retrato"), h("div", {}, segI)), wImg, wPrompt,
        h("label", {class: "chk"}, iniciar, "Iniciar al crear")))),
    actions: [{label: "Cancelar"}, {label: "Crear", class: "primary", onclick: async () => {
      const b = {nombre_proyecto: nombreProy.value.trim() || null, personaje: personaje.value.trim(), origen_texto: origen,
        texto: texto.value.trim(), tema: tema.value.trim(), voz: voz.value, nombre: nombre.value.trim(), subtitulo: subtitulo.value.trim(),
        iniciar: iniciar.checked};
      if (imgMode === "sdxl") b.prompt_imagen = prompt.value.trim(); else b.imagen = imagen;
      try {
        const r = await api.post("/quick-video", b);
        toast(r.run_id ? "Vídeo en marcha" : "Flujo creado", "ok");
        App.go(`#/estudio/${r.slug}?hilo=${r.thread_id}`);
      } catch (e) { toastErr(e); return false; }
    }}]});
}
App.crearVideo = crearVideo;
function seg(opts, on) {
  const el = h("div", {class: "seg"});
  opts.forEach(([v, l], i) => el.append(h("button", {class: i === 0 ? "on" : "", type: "button", onclick: e => {
    for (const b of el.children) b.classList.remove("on"); e.currentTarget.classList.add("on"); on(v);
  }}, l)));
  return el;
}
App.seg = seg;
})();
