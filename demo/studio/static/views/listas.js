/* Flujos (antes Proyectos), Hilos, Salidas y Configuración. */
"use strict";
(() => {
const {h, api, toast, toastErr, modal, confirmDlg, promptDlg, fmtDate, fmtBytes, pill, download, form} = App;

/* ------------------------------------------------------------------ Flujos */
App.nuevoProyecto = async () => {
  const tpls = await api.get("/templates");
  let tpl = tpls.find(t => t.id === "vacio")?.id || tpls[0]?.id;
  const nombre = h("input", {placeholder: "Mi flujo", id: "npNombre"});
  const desc = h("input", {placeholder: "Descripción (opcional)"});
  const list = h("div", {class: "cards"}, tpls.map(t => h("div", {class: "card big" + (t.id === tpl ? " sel" : ""), "data-t": t.id,
    style: {borderColor: t.id === tpl ? "var(--gold)" : ""}, onclick: e => {
      tpl = t.id;
      for (const c of list.children) c.style.borderColor = c.dataset.t === tpl ? "var(--gold)" : "";
      if (!nombre.value) nombre.placeholder = t.name;
    }}, h("div", {class: "ico"}, t.icon), h("h4", {}, t.name), h("p", {}, t.desc))));
  modal({title: "Nuevo flujo", wide: true, body: h("div", {},
    h("label", {class: "f"}, h("span", {}, "Nombre"), nombre), h("label", {class: "f"}, h("span", {}, "Descripción"), desc),
    h("h3", {style: {margin: "8px 0"}}, "Plantilla"), list),
    actions: [{label: "Cancelar"}, {label: "Crear", class: "primary", onclick: async () => {
      // la plantilla de vídeo tiene su propio asistente (personaje, texto, retrato, voz); crea el mismo tipo de proyecto
      if (tpl === "clip_personaje" && App.crearVideo) { setTimeout(() => App.crearVideo(nombre.value.trim()), 0); return; }
      try {
        const p = await api.post("/projects", {name: nombre.value.trim() || tpls.find(t => t.id === tpl).name, template: tpl, desc: desc.value.trim()});
        App.go(`#/estudio/${p.slug}`);
      } catch (e) { toastErr(e); return false; }
    }}]});
};

/* D9: «Proyectos» se rotula «Flujos»; #/proyectos sigue funcionando y redirige */
App.views.proyectos = async () => { location.replace("#/flujos"); };
App.views.flujos = async (main) => {
  const page = h("div", {class: "page"});
  main.append(page);
  const file = h("input", {type: "file", accept: "application/json,.json", class: "hidden"});
  file.addEventListener("change", async () => {
    if (!file.files[0]) return;
    try {
      const data = JSON.parse(await file.files[0].text());
      const p = await api.post("/projects/import", data);
      toast(`Importado como «${p.name}»`, "ok"); paint();
    } catch (e) { toastErr(e); }
    file.value = "";
  });
  page.append(h("header", {}, h("h2", {}, "Flujos"), h("span", {class: "sp"}),
    h("button", {onclick: () => file.click()}, "Importar JSON"), file,
    h("button", {class: "primary", onclick: App.nuevoProyecto}, "+ Nuevo")));
  const grid = h("div", {class: "cards"});
  page.append(grid);
  async function paint() {
    const ps = await api.get("/projects");
    grid.replaceChildren(...(ps.length ? ps.map(p => h("div", {class: "card"},
      h("h4", {}, p.name), h("p", {}, p.desc || "—"),
      h("div", {class: "row wrap"}, pill(p.ultimo_status), h("span", {class: "mut", style: {fontSize: "12px"}},
        `${p.n_nodes} nodos · ${p.n_threads} hilos · v${p.version} · ${fmtDate(p.updated)}`)),
      h("div", {class: "acts"},
        h("button", {class: "sm primary", onclick: () => App.go(`#/estudio/${p.slug}`)}, "Abrir"),
        h("button", {class: "sm", onclick: async () => {
          const n = await promptDlg("Duplicar flujo", `${p.name} (copia)`, "Nombre");
          if (n) { try { await api.post(`/projects/${p.slug}/duplicate`, {name: n}); paint(); } catch (e) { toastErr(e); } }
        }}, "Duplicar"),
        h("button", {class: "sm", onclick: async () => download(`${p.slug}.json`, JSON.stringify(await api.get(`/projects/${p.slug}/export`), null, 2))}, "Exportar"),
        h("button", {class: "sm danger", onclick: async () => {
          if (!await confirmDlg(`¿Borrar «${p.name}» con sus ${p.n_threads} hilos y todos sus ficheros?`, "Borrar", true)) return;
          try { await api.del(`/projects/${p.slug}`); paint(); } catch (e) { toastErr(e); }
        }}, "Borrar")))) : [h("p", {class: "empty"}, "No hay flujos. Crea uno desde una plantilla.")]));
  }
  await paint();
};

/* ------------------------------------------------------------------ Hilos */
App.views.hilos = async (main, _parts, params) => {
  const page = h("div", {class: "page"});
  main.append(page);
  const projects = await api.get("/projects");
  const sel = h("select", {style: {width: "auto"}}, h("option", {value: ""}, "Todos los flujos"),
    projects.map(p => h("option", {value: p.slug}, p.name)));
  sel.value = params.proyecto || "";
  sel.addEventListener("change", paint);
  const ocultos = h("input", {type: "checkbox", id: "verOcultos"});
  ocultos.addEventListener("change", paint);
  const tbody = h("tbody");
  page.append(h("header", {}, h("h2", {}, "Hilos"), h("span", {class: "sp"}),
    h("label", {class: "chk", style: {margin: 0}, title: "Hilos de experimentos y ramas abandonadas"}, ocultos, "mostrar ocultos"), sel),
    h("div", {class: "tablewrap"}, h("table", {class: "t"},
      h("thead", {}, h("tr", {}, ["Título", "Flujo", "Estado", "Actualizado", "Salida", ""].map(x => h("th", {}, x)))), tbody)));
  const names = Object.fromEntries(projects.map(p => [p.slug, p.name]));
  async function paint() {
    const qs = new URLSearchParams();
    if (sel.value) qs.set("project", sel.value);
    if (ocultos.checked) qs.set("ocultos", "1");
    const ths = await api.get("/threads?" + qs);
    tbody.replaceChildren(...(ths.length ? ths.map(t => h("tr", {},
      h("td", {}, h("a", {href: `#/estudio/${t.project}?hilo=${t.thread_id}`}, t.title || t.thread_id.slice(0, 8))),
      h("td", {}, names[t.project] || t.project),
      h("td", {}, pill(t.status)),
      h("td", {class: "mut"}, fmtDate(t.updated)),
      h("td", {}, t.salida ? (t.salida.kind === "video" ? h("a", {href: "#", onclick: e => { e.preventDefault(); App.playVideo({...t.salida, thread_title: t.title, project: t.project}); }}, "▶ vídeo")
        : h("span", {class: "mut"}, (t.salida.texto || t.salida.path || "").slice(0, 50))) : h("span", {class: "mut"}, "—")),
      h("td", {class: "acts"},
        h("button", {class: "sm", onclick: () => App.go(`#/estudio/${t.project}?hilo=${t.thread_id}`)}, "Abrir"),
        h("button", {class: "sm", onclick: async () => {
          const n = await promptDlg("Renombrar hilo", t.title || "", "Título");
          if (n) { await api.patch(`/threads/${t.thread_id}`, {title: n}).catch(toastErr); paint(); }
        }}, "Renombrar"),
        h("button", {class: "sm", onclick: async () => { try { await api.post(`/threads/${t.thread_id}/copy`); toast("Hilo copiado", "ok"); paint(); } catch (e) { toastErr(e); } }}, "Copiar"),
        t.ultimo_run && t.ultimo_run.status === "success" && App.f3 ? h("button", {class: "sm", title: "Añadir su última ejecución a un dataset", onclick: async () => {
          const r = await api.get(`/runs/${t.ultimo_run.run_id}`); if (r.kind !== "input") { toast("La última ejecución no tuvo entrada (Continuar/Responder): ábrela en Trazas"); return; } App.f3.addToDataset(r); }}, "→ Dataset") : null,
        h("button", {class: "sm danger", onclick: async () => {
          if (!await confirmDlg(`¿Borrar el hilo «${t.title}» con su historia y ficheros?`, "Borrar", true)) return;
          try { await api.del(`/threads/${t.thread_id}`); paint(); } catch (e) { toastErr(e); }
        }}, "Borrar")))) : [h("tr", {}, h("td", {colspan: 6, class: "empty"}, "No hay hilos."))]));
  }
  await paint();
};

/* ------------------------------------------------------------------ Salidas (vídeo, audio, imagen y texto de cualquier flujo) */
const TIPOS = [["", "Todas"], ["video", "Vídeo"], ["audio", "Audio"], ["image", "Imagen"], ["text", "Texto"]];
const TIPO_N = {video: ["vídeo", "vídeos"], audio: ["audio", "audios"], image: ["imagen", "imágenes"], text: ["texto", "textos"]};
const ICO = {
  play: '<svg viewBox="0 0 24 24"><path d="M8 5.5v13l11-6.5z"/></svg>',
  down: '<svg viewBox="0 0 24 24"><path d="M12 4v11m0 0-4.5-4.5M12 15l4.5-4.5M5 19h14"/></svg>',
  open: '<svg viewBox="0 0 24 24"><path d="M14 5h5v5m0-5-8 8M10 5H6a1 1 0 0 0-1 1v12a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-4"/></svg>',
  trash: '<svg viewBox="0 0 24 24"><path d="M5 7h14M10 7V5h4v2m-7 0 1 12h8l1-12M10.5 10.5v5m3-5v5"/></svg>',
  eye: '<svg viewBox="0 0 24 24"><path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z"/><circle cx="12" cy="12" r="2.8"/></svg>',
  chev: '<svg viewBox="0 0 24 24"><path d="m8 10 4 4 4-4"/></svg>',
};
const ico = (k) => h("i", {class: "ico", "aria-hidden": "true", html: ICO[k]});
const nTipo = (k, n) => `${n} ${(TIPO_N[k] || [k, k])[n === 1 ? 0 : 1]}`;
function relT(t) {
  if (!t) return "—";
  const s = Date.now() / 1000 - t;
  if (s < 60) return "ahora mismo";
  if (s < 3600) return `hace ${Math.floor(s / 60)} min`;
  if (s < 86400) return `hace ${Math.floor(s / 3600)} h`;
  if (s < 86400 * 7) { const d = Math.floor(s / 86400); return d === 1 ? "ayer" : `hace ${d} días`; }
  return new Date(t * 1000).toLocaleDateString("es-ES", {day: "numeric", month: "short"});
}
/* «20261118-101823» → «Ejecución del 18 nov, 10:18» */
function tituloV1(n) {
  const m = /^(\d{4})(\d\d)(\d\d)-(\d\d)(\d\d)/.exec(n || "");
  if (!m) return n || "Ejecución v1";
  const d = new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]);
  return `Ejecución del ${d.toLocaleDateString("es-ES", {day: "numeric", month: "short"})}, ${m[4]}:${m[5]}`;
}
function salidaCard(o, onDelete, i) {
  const titulo = o.legacy ? tituloV1(o.thread_title) : (o.thread_title || o.clave || o.project);
  const ver = () => o.kind === "video" ? App.playVideo(o)
    : o.kind === "image" ? window.open(App.media(o.path), "_blank")
      : modal({title: o.clave || "Texto", wide: true, body: h("div", {class: "transcript"}, o.texto || "—")});
  let media;
  if (o.kind === "video") media = h("div", {class: "omedia", onclick: ver},
    h("video", {src: App.media(o.path) + "#t=0.5", preload: "metadata", muted: true}), h("span", {class: "oplay"}, ico("play")));
  else if (o.kind === "image") media = h("div", {class: "omedia", onclick: ver}, h("img", {src: App.media(o.path), loading: "lazy", alt: titulo}));
  else if (o.kind === "audio") media = h("div", {class: "omedia oaudio"},
    h("div", {class: "wave", "aria-hidden": "true"}, Array.from({length: 38}, (_, k) => h("i", {style: `--h:${18 + Math.abs(Math.sin((k + 1) * 1.7 + (o.id || 0))) * 72}%`}))),
    h("audio", {src: App.media(o.path), controls: true, preload: "none"}));
  else media = h("div", {class: "omedia otext", onclick: ver}, h("p", {}, (o.texto || "").slice(0, 320) || "—"));
  return h("article", {class: `ocard k-${o.kind} rv`, style: `--d:${Math.min(i, 12) * 40}ms`, "data-out": o.id},
    media,
    h("span", {class: "okind"}, (TIPO_N[o.kind] || [o.kind])[0]),
    h("div", {class: "obody"},
      h("h4", {title: titulo}, titulo),
      o.kind !== "text" && o.texto ? h("p", {class: "oquote"}, o.texto) : null,
      h("div", {class: "ometa"}, h("span", {}, o.node || o.clave || "salida"), h("span", {class: "sp"}),
        h("span", {title: fmtDate(o.ts)}, relT(o.ts)))),
    h("div", {class: "oacts"},
      o.kind === "text" ? h("button", {class: "sm ghost", onclick: ver}, ico("eye"), "Ver") : null,
      o.path ? h("a", {class: "btn sm ghost", href: App.media(o.path), download: ""}, ico("down"), "Descargar") : null,
      o.thread_id ? h("button", {class: "sm ghost", title: "Abrir el hilo en el Estudio",
        onclick: () => App.go(`#/estudio/${o.project}?hilo=${o.thread_id}`)}, ico("open"), "Hilo") : null,
      h("span", {class: "sp"}),
      h("button", {class: "sm ghost odel", title: "Eliminar esta salida", "aria-label": "Eliminar esta salida", onclick: () => onDelete(o)}, ico("trash"))));
}

App.views.salidas = async (main, _parts, params) => {
  const page = h("div", {class: "page inicio salidas"});
  main.append(page);
  let tipo = params.tipo || "";
  const seg = h("div", {class: "chips", id: "salidaTipo", role: "tablist"});
  const resumen = h("p", {class: "lede"});
  page.append(h("header", {class: "shead rv"},
    h("div", {class: "grow"},
      h("div", {class: "eyebrow"}, "Biblioteca"),
      h("h2", {}, "Salidas"), resumen),
    seg));
  const cuerpo = h("div", {});
  page.append(cuerpo);
  const plegados = new Set(App.store.get("salidas_plegados", []));

  async function borrar(lista, msg) {
    if (!await confirmDlg(msg, "Eliminar", true)) return;
    let ko = 0;
    for (const o of lista) { try { await api.del(`/outputs/${encodeURIComponent(o.id)}`); } catch (e) { ko++; toastErr(e); } }
    if (lista.length > 1 && !ko) toast(`${lista.length} salidas eliminadas`, "ok");
    else if (!ko) toast("Salida eliminada", "ok");
    paint();
  }

  async function paint() {
    const [todas, projects] = await Promise.all([api.get("/outputs?limit=500"), api.get("/projects").catch(() => [])]);
    const nombres = Object.fromEntries(projects.map(p => [p.slug, p.name]));
    const cuenta = k => k ? todas.filter(o => o.kind === k).length : todas.length;
    seg.replaceChildren(...TIPOS.map(([v, l]) => h("button", {type: "button", role: "tab", class: v === tipo ? "on" : "", "aria-selected": String(v === tipo),
      "data-tipo": v, disabled: !!(v && !cuenta(v)),
      onclick: () => { tipo = v; history.replaceState(null, "", "#/salidas" + (v ? `?tipo=${v}` : "")); paint(); }},
      l, h("span", {class: "n"}, cuenta(v)))));
    const os = tipo ? todas.filter(o => o.kind === tipo) : todas;
    // agrupar por proyecto, el más reciente primero; el archivo de v1 siempre al final
    const grupos = new Map();
    for (const o of os) {
      const k = o.legacy ? "__v1" : o.project;
      if (!grupos.has(k)) grupos.set(k, []);
      grupos.get(k).push(o);
    }
    const orden = [...grupos.entries()].sort((a, b) => (a[0] === "__v1") - (b[0] === "__v1") ||
      Math.max(...b[1].map(o => o.ts || 0)) - Math.max(...a[1].map(o => o.ts || 0)));
    resumen.textContent = todas.length
      ? `${nTipoTotal(todas.length)} en ${grupos.size === 1 ? "1 flujo" : `${grupos.size} flujos`}${tipo ? ` · filtrado por ${TIPO_N[tipo][1]}` : ""}. Todo lo que registran los nodos «Salida» de tus flujos.`
      : "Todo lo que registran los nodos «Salida» de tus flujos aparecerá aquí, agrupado por flujo.";
    if (!os.length) {
      cuerpo.replaceChildren(h("div", {class: "vacio rv"},
        h("div", {class: "grow"}, h("b", {}, todas.length ? "Nada de este tipo" : "Todavía no hay salidas"),
          h("p", {}, todas.length ? "Prueba con otro filtro." : "Ejecuta un flujo que termine en un nodo «Salida» y sus resultados se guardarán aquí."))));
      return;
    }
    let idx = 0;
    cuerpo.replaceChildren(...orden.map(([k, lista], gi) => {
      const v1 = k === "__v1";
      const nombre = v1 ? "Archivo v1" : (nombres[k] || k);
      const porTipo = Object.entries(lista.reduce((m, o) => (m[o.kind] = (m[o.kind] || 0) + 1, m), {})).map(([t, n]) => nTipo(t, n)).join(" · ");
      const plegado = plegados.has(k);
      const sec = h("section", {class: "ogrp" + (plegado ? " plegado" : ""), "data-proyecto": k});
      const toggle = () => {
        sec.classList.toggle("plegado");
        sec.classList.contains("plegado") ? plegados.add(k) : plegados.delete(k);
        App.store.set("salidas_plegados", [...plegados]);
      };
      sec.append(
        h("div", {class: "sec"},
          h("span", {class: "secn"}, String(gi + 1).padStart(2, "0")),
          h("div", {class: "sect"},
            h("div", {class: "row"}, h("h3", {onclick: toggle}, nombre), h("span", {class: "count"}, lista.length),
              h("button", {class: "ghost fold", title: "Plegar / desplegar", "aria-label": "Plegar o desplegar", onclick: toggle}, ico("chev"))),
            h("small", {}, v1 ? `${porTipo} · ejecuciones de la versión anterior del estudio` : porTipo)),
          h("span", {class: "sp"}),
          !v1 && nombres[k] ? h("button", {class: "sm", onclick: () => App.go(`#/estudio/${k}`)}, ico("open"), "Abrir flujo") : null,
          h("button", {class: "sm danger vaciar", onclick: () => borrar(lista,
            `¿Eliminar ${lista.length === 1 ? "la salida" : `las ${lista.length} salidas`}${tipo ? ` de tipo ${TIPO_N[tipo][0]}` : ""} de «${nombre}»? Se borran también sus ficheros${v1 ? " y las carpetas de ejecución v1" : ""}. No se puede deshacer.`)},
            ico("trash"), lista.length === 1 ? "Eliminar" : "Eliminar todas")),
        h("div", {class: "ogrid"}, lista.map(o => salidaCard(o, x => borrar([x],
          x.legacy ? `¿Eliminar «${tituloV1(x.thread_title)}»? Se borra su carpeta de ejecución v1 completa (vídeo, audio e intermedios).`
            : "¿Eliminar esta salida? Se borra también su fichero.") , idx++))));
      return sec;
    }));
  }
  const nTipoTotal = n => n === 1 ? "1 salida" : `${n} salidas`;
  await paint();
};
App.views.videos = (main, parts) => App.views.salidas(main, parts, {tipo: "video"});   // ruta antigua

/* ------------------------------------------------------------------ Configuración */
App.views.config = async (main) => {
  const page = h("div", {class: "page", style: {maxWidth: "900px"}});
  main.append(page);
  const {schema, values} = await api.get("/settings");
  const get = (k) => k.split(".").reduce((o, p) => o?.[p], values);
  const forms = [];
  page.append(h("header", {}, h("h2", {}, "Configuración"), h("span", {class: "sp"}),
    h("button", {class: "primary", onclick: save}, "Guardar")));
  for (const g of schema) {
    const vals = Object.fromEntries(g.campos.map(c => [c.key, get(c.key)]));
    const f = form(g.campos.map(c => ({...c, help: [c.help, c.reinicio ? "requiere reiniciar el servidor" : null].filter(Boolean).join(" · ")})), vals);
    forms.push(f);
    page.append(h("div", {class: "sec"}, h("h3", {}, g.grupo)), f.el);
  }
  if (App.f3) App.f3.mcpSection(page);
  page.append(h("div", {class: "sec"}, h("h3", {}, "Almacenamiento")),
    h("p", {class: "mut"}, `Se conservan los ${values.hilos_conservar} hilos más recientes por flujo; al crear uno nuevo se borran los más antiguos (con sus checkpoints y ficheros).`));
  const sys = await api.get("/system").catch(() => null);
  if (sys) page.append(h("div", {class: "sec"}, h("h3", {}, "Sistema (solo lectura)")),
    h("dl", {class: "kv"},
      h("dt", {}, "GPU"), h("dd", {}, sys.gpu ? `${sys.gpu.name} · ${sys.gpu.mem_used}/${sys.gpu.mem_total} MiB · ${sys.gpu.temp} °C` : "—"),
      h("dt", {}, "RAM disponible"), h("dd", {}, fmtBytes(sys.ram.disponible) + " de " + fmtBytes(sys.ram.total)),
      h("dt", {}, "Disco libre"), h("dd", {}, fmtBytes(sys.disco.libre)),
      h("dt", {}, "Ollama"), h("dd", {}, sys.ollama.ok ? "activo · " + (sys.ollama.cargados.join(", ") || "sin modelos cargados") : "sin respuesta"),
      h("dt", {}, "Versiones"), h("dd", {}, Object.entries(sys.versiones).map(([k, v]) => `${k} ${v}`).join(" · "))));
  if (App.state.settings?.notificaciones && "Notification" in window && Notification.permission === "default")
    page.append(h("button", {onclick: () => Notification.requestPermission()}, "Permitir notificaciones"));
  async function save() {
    const out = {};
    try {
      for (const f of forms) for (const [k, v] of Object.entries(f.values())) {
        const parts = k.split("."); let o = out;
        while (parts.length > 1) { const p = parts.shift(); o = o[p] = o[p] || {}; }
        o[parts[0]] = v;
      }
      const r = await api.put("/settings", {values: out});
      App.state.settings = r.values;
      document.documentElement.dataset.theme = r.values.tema === "claro" ? "claro" : "oscuro";
      if (r.values.token !== values.token) document.cookie = `studio_token=${encodeURIComponent(r.values.token)}; path=/; max-age=31536000; SameSite=Strict`;
      toast("Ajustes guardados" + ((r.values.host !== values.host || r.values.port !== values.port) ? " · reinicia el servidor para aplicar host/puerto" : ""), "ok");
    } catch (e) { toastErr(e); }
  }
};
})();
