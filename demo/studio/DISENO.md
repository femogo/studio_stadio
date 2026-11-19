# Estudio de agentes — Documento de diseño (v2, sobre LangGraph)

Estado: **F1, F2 y F3 terminadas** · Revisión 2.4 (rev. 2.3 + D9 y criterios §13.7, aprobados el 2026-11-19) · Fecha: 2026-11-19

> **Para la sesión que implementa:** este documento es el encargo completo. Lo normativo está aquí; `REVISION_EGM.md`
> es contexto (por qué se decidió cada cosa). Donde una sección lleva **[F1]**, **[F2]** o **[F3]**, esa es su fase.
> **Lo que no esté marcado F1 no se implementa en la fase 1: no aparece en el menú y sus endpoints no existen.**
> Convención: `{a, b}` en prosa describe campos; no es un literal de Python. Los bloques de código Python sí son literales.

## 0. Objetivo, decisiones y no objetivos

Construir, en la máquina local y con coste cero, un **estudio visual para diseñar, ejecutar y depurar agentes** con motor
**LangGraph 1.2.x**, que cubra las funcionalidades de la §10 (alcance completo al terminar la fase 3). La generación de
vídeos de personajes históricos (pipeline ya validado) es **una plantilla más**.

Principios:

1. **Un proyecto es un `StateGraph` de verdad.** El JSON del proyecto se compila a LangGraph en cada ejecución.
   `compiler.py` debe poder importarse sin FastAPI (`build(project, *, checkpointer=None, store=None, cache=None)`), para
   que la exportación [F3] reutilice el mismo código.
2. **Un módulo es un archivo `.py`** en `studio/modules/`, editable desde la GUI o desde fuera, sin reiniciar.
3. **No reinventar lo que LangGraph ya hace** (joins, condicionales, ciclos, interrupts, checkpoints, time travel, store,
   caché, reintentos, timeouts). Lo propio se limita a: GUI, compilador JSON→grafo, ejecución en subproceso de trabajo
   pesado/GPU, y equivalentes locales de lo que en LangGraph Studio depende de LangSmith/Platform.

Decisiones del propietario (2026-11-18):

| # | Decisión |
|---|---|
| D1 | Alcance = **paridad de depuración (F1) + paridad con LangGraph Platform (F2–F3)**: asistentes versionados, double-texting completo, crons, datasets y experimentos se implementan en F2/F3. |
| D2 | **LAN con token obligatorio**: escucha en `0.0.0.0:8790`; si `token` está vacío al arrancar, se genera uno aleatorio, se guarda en `settings.json` y se imprime en el log y en la salida de `arrancar.sh`. |
| D3 | No se usa LangGraph Studio en la nube. La exportación a `langgraph.json` [F3] queda como vía de escape opcional. *(decidido por el implementador del diseño; el propietario puede cambiarlo)* |
| D4 | **Primera entrega = editor visual completo** (no solo una GUI mínima): ver §13. El diálogo «Crear vídeo nuevo» y `/quick-video` entran en F1. |
| D5 | Plantilla de vídeo: la revisión humana **solo aparece cuando el texto lo genera el LLM**. Rechazar **vuelve al nodo Guion con el comentario**, como máximo 3 rechazos; al tercero la ejecución termina sin vídeo. |
| D6 | Ollama con `keep_alive="5m"` y **descarga explícita de los modelos cargados antes de cada nodo `gpu: "exclusive"`** (§5.6). *(decidido por el implementador del diseño)* |
| D7 | Los runs de v1 **no se importan como hilos**: se archivan en `legacy/runs/` y la galería Vídeos los muestra como ficheros con la etiqueta «v1». *(decidido por el implementador del diseño)* |
| D8 | *(2026-11-18, tras F3)* **La app es para crear flujos, no de vídeos.** Inicio muestra primero los **proyectos** (flujos) con «+ Nuevo proyecto» y debajo los **agentes** (= asistentes, §3.5) con «+ Nuevo agente». «Crear vídeo nuevo» deja de estar en Inicio: el diálogo guiado (`/quick-video`) se abre solo al elegir la plantilla «Clip de personaje» en «Nuevo proyecto». El menú «Vídeos» pasa a **Salidas** (todas las salidas, filtrables por tipo) y «Asistentes» se rotula **Agentes**. Sustituye la parte de D4 sobre «Crear vídeo nuevo» |
| D9 | *(2026-11-19, aprobado; ver §13.7)* **Dos conceptos en la interfaz: Flujos y Agentes.** «Proyecto» se rotula **Flujo** en toda la GUI (menú, Inicio, botones, diálogos, Estudio, Hilos, módulo `subgrafo`). **Agente** = flujo + configuración versionada (el asistente de §3.5; la palabra «asistente» desaparece de la GUI). El nodo `agente_react` se rotula **Agente con herramientas** para no chocar con «Agente». API, carpetas (`projects/`), claves JSON y ids de los tests **no cambian**. Además, un *Agente con herramientas* puede usar **flujos como herramientas** (campo `flujos`). Amplía D8 |

No objetivos: despliegue en LangGraph Platform/Cloud, multiusuario, compatibilidad con `langgraph_sdk` o con la API de
LangGraph Server, webhooks salientes, UI generativa, A2A, ejecución en otras máquinas.

Glosario (un término, un significado):
**proyecto** = grafo editable · **módulo** = tipo de nodo (archivo `.py`) · **nodo** = instancia de un módulo en un
proyecto · **plantilla** = proyecto de partida (`templates/*.json`); un módulo nuevo parte de un **esqueleto de módulo**;
un prompt es un **texto con marcadores `{clave}`** · **asistente** [F2] = configuración con nombre y versionada de un
proyecto · **hilo** = historia persistente (`thread_id`) con checkpoints · **ejecución** = una llamada a `astream` sobre
un hilo · **checkpoint** = foto del estado tras cada superstep · **estado** = los valores del grafo; **status** = ciclo de
vida (de una ejecución, nodo o hilo) · **ajustes** = `settings.json` · **configuración del nodo** = `cfg` · **copiar hilo**
= duplicar un hilo entero · **bifurcar** = crear una rama nueva desde un checkpoint · **duplicar módulo** = copiar un `.py`.

## 1. Entorno y restricciones (verificado el 2026-11-18)

| Elemento | Valor |
|---|---|
| Máquina | VM Linux, 6 CPU, 10 GB RAM (+7 GB swap), 165 GB libres, IP LAN privada |
| GPU | RTX 4060, **8 GB VRAM** |
| Carpeta base (`BASE`) | `/opt/historico/demo` (no es repo git) |
| Intérprete «pesado» | `/opt/historico/demo/.venv/bin/python` — **Python 3.10.20**: torch 2.4.1+cu121, diffusers 0.31.0, transformers 4.46.3 (**no** actualizar a ≥5: rompe con torch 2.4), kokoro 0.9.4, setuptools 69.5.1, deps de SadTalker |
| Intérprete «estudio» | `/opt/historico/demo/studio/.venv-studio/bin/python` — **Python 3.12.3**: langgraph 1.2.22, langgraph-checkpoint 4.3.2, langgraph-checkpoint-sqlite 3.1.3, langgraph-prebuilt 1.1.2, langchain-core, langsmith, aiosqlite, sqlite_vec, fastapi, uvicorn |
| LLM | Ollama en `localhost:11434`: qwen2.5:7b, gemma4:e2b, gemma4:12b-it-qat, deepseek-r1-qwen14b (Q3_K_S), nomic-embed-text |
| Herramientas | ffmpeg, espeak-ng, uv (`/root/.local/bin/uv`), Chromium headless en `~/.cache/ms-playwright/chromium_headless_shell-1243/chrome-headless-shell-linux64/chrome-headless-shell` |
| SadTalker | `BASE/SadTalker`, pesos 256 y 512 descargados y **parcheado** (np.float/np.int, `align_img` con `float(t[0])`, basicsr `functional_tensor`→`functional`, `setuptools<70`). **No re-clonar.** |
| Retrato de prueba | `BASE/cleo_0.png` (y `cleo_1.png`, `cleo_2.png`, `napoleon_demo.png`) |
| Puertos | 8790 = estudio (v1 hoy; v2 al terminar F1). **Durante el desarrollo v2 escucha en 8791.** 8799 = tests. 8788 y 80 son de otros servicios. |
| v1 en marcha | `studio/server.py` (v1) en 8790, lanzado con `setsid nohup`. Para pararlo sin matar tu propia shell: `kill $(pgrep -f "[s]erver.py --port 8790")` o, si arrancó sin `--port`, `kill $(pgrep -f "[p]ython server.py")`. **No usar `pkill -f` con un patrón que aparezca en tu propio comando.** |

**Hallazgo crítico (verificado):** en Python 3.10, `interrupt()` y `get_stream_writer()` fallan dentro de nodos `async`
(`RuntimeError: Called get_config outside of a runnable context`; en el código: `ASYNCIO_ACCEPTS_CONTEXT =
sys.version_info >= (3, 11)`). Por eso **el servidor/motor corre en 3.12** y los nodos pesados son **subprocesos 3.10**.

Probado en ejecución (prototipo `studio/docs/prototipo_lg.py`, 3.12): `StateGraph` con estado dict y reducer propio,
nodos async, `add_edge([a,b], c)` (join que acumula entre supersteps), `add_conditional_edges`, `interrupt` +
`Command(resume=…)`, `AsyncSqliteSaver`, `astream` con `stream_mode=["updates","custom","tasks"]`, `aget_state`,
`aget_state_history`, `aupdate_state` sobre un checkpoint antiguo (bifurcación), `get_graph().draw_mermaid()`,
`RetryPolicy`, `timeout=` numérico, `max_concurrency`, `recursion_limit`.

Existe en el paquete (firma comprobada, **no ejecutado**): `interrupt(..., response_schema=)`, `Command(update, goto,
graph=Command.PARENT)`, `Send`, `CachePolicy` + `langgraph.cache.sqlite.SqliteCache(path=…)`, `TimeoutPolicy(run_timeout,
idle_timeout, refresh_on)`, `defer=`, `error_handler=` (callable con parámetro `error: NodeError`), `destinations=`,
`RunControl.request_drain()` → `GraphDrained`, `NodeTimeoutError`, `GraphRecursionError`, `Overwrite`,
`langgraph.store.sqlite.aio.AsyncSqliteStore` (requiere `await store.setup()`), `Runtime` (context, store, stream_writer,
heartbeat, execution_info{thread_id, run_id, task_id, node_attempt…}, control; `previous` solo en la API funcional),
`@entrypoint/@task`. Lo verificado en el paso **1a** está en §1.2.

**`AsyncSqliteSaver` NO implementa** `acopy_thread`, `adelete_for_runs` ni `aprune` (lanzan `NotImplementedError`); solo
`adelete_thread`. Ver §5.8.

### 1.2 Resultados del spike 1a (`tests/test_spike_lg.py`, 10 pruebas en verde)

| Pregunta | Resultado verificado | Cambio en el diseño |
|---|---|---|
| Drain con un interrupt en el superstep | **No** lanza `GraphDrained`: el stream termina normal con `__interrupt__` → `interrupted`. Las tareas hermanas ya terminadas no se repiten al reanudar | Ninguno (§5.4 confirmado) |
| Drain sin interrupt | `GraphDrained` al acabar el superstep; `next` = siguiente superstep | Ninguno |
| Cancelar `astream` con una tarea paralela terminada (`durability` async y sync) | Su escritura pendiente se conserva y **no se repite** al Continuar. Al continuar, `updates` la reemite como `{nodo: valor, "__metadata__": {"cached": True}}` y `tasks` emite su **inicio sin resultado** | §5.3 confirmado. §6.2: el motor ignora la clave `__metadata__` y **no** usa `tasks` para `node.start`/`node.end` (los emite el wrapper) |
| `astream(input)` sobre un hilo con `next` pendiente | Abandona el `next` pendiente y arranca desde START sobre el estado acumulado (los reducers se aplican) | §5.2: «Iniciar» en un hilo con `next` pendiente pide confirmación en la GUI |
| `aupdate_state(as_node="__copy__")` | **Ignora `values`** (sin error): crea una bifurcación con los mismos valores, `next` y padre, `metadata.source="fork"` | §5.2: `__copy__` = «bifurcar sin cambios». **No existe «editar sin disparar aristas»**: editar usa `as_node` = escritor y re-evalúa sus aristas (también las condicionales) con los valores editados |
| `interrupt(response_schema=dict)` | Aparece tal cual en `aget_state().interrupts[i].response_schema`; reanudar con `Command(resume={id: valor})` | Ninguno |
| `TimeoutPolicy(idle_timeout, refresh_on="heartbeat")` + `runtime.heartbeat()` | Funciona; sin heartbeat → `NodeTimeoutError(kind="idle")`. `run_timeout` no se refresca → `kind="run"` | §4: se compila solo `idle_timeout` con `refresh_on="heartbeat"`; el wrapper aplica `run_timeout` y lanza `NodeTimeoutError(kind="run")`; mientras espera la GPU llama a `heartbeat()` |
| `error_handler` y reintentos | El handler se llama **después** de agotar los reintentos, con `NodeError(node, error)`; aparece el nodo `__error_handler__<id>` | Ninguno (F2) |
| Columnas | `checkpoints(thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id, type, checkpoint, metadata)`, `writes(thread_id, checkpoint_ns, checkpoint_id, task_id, task_path, idx, channel, type, value)`. Copiar ambas tablas cambiando `thread_id` da un hilo que se puede continuar | §5.8 confirmado |
| `astream(..., subgraphs=True, version="v1")` | Emite tuplas `(ns, modo, dato)`; `checkpoints` trae `config, metadata, values, next, parent_config, tasks` | Ninguno |

### 1.1 Dependencias por fase

Instalar siempre con `uv pip install --python /opt/historico/demo/studio/.venv-studio/bin/python …`:

- **F1:** `pytest pytest-asyncio playwright python-multipart httpx` (Playwright usa el Chromium existente mediante `executable_path`; no ejecutar
  `playwright install`). CodeMirror 5.65.16 **vendorizado** en `static/vendor/codemirror/` (descargar una vez de cdnjs:
  `codemirror.min.js`, `codemirror.min.css`, `mode/python/python.min.js`, `theme/material-darker.min.css`,
  `addon/edit/matchbrackets.min.js`); la GUI no depende de CDN en tiempo de ejecución.
- **F2:** `langchain-ollama` (ChatOllama para el modo Chat y el stream `messages`).
- **F3:** `croniter langchain-mcp-adapters`.
- `arrancar.sh` exporta `LANGSMITH_TRACING=false`.

## 2. Arquitectura

```
Navegador (GUI estática: index.html + app.js + app.css + vendor/, sin build)
   │  REST JSON + SSE (/api/events)          cookie/cabecera con token
   ▼
server.py  ── FastAPI + uvicorn (1 worker, sin --reload), intérprete «estudio» 3.12
   ├─ modules.py   descubre módulos; lee META/CONFIG por AST (ast.literal_eval, sin ejecutar); valida; instantáneas por sha
   ├─ compiler.py  proyecto + asistente → StateGraph compilado (se compila en cada ejecución; son milisegundos)
   ├─ engine.py    ejecuciones: astream → eventos normalizados → bus SSE + tabla events; pausa/parada/continuar;
   │               semáforo GPU; reconciliación al arrancar
   ├─ db.py        studio.sqlite (aiosqlite, WAL, migraciones con PRAGMA user_version)
   └─ nodos
        ├─ exec "local":   run() del módulo dentro del proceso 3.12 (async directo o sync en un pool de threads propio)
        └─ exec "proceso": subprocess `<intérprete> -u studio/runner.py <ruta .py> <carpeta tarea> <node_id>` (§3.1.1)

Datos: studio/data/checkpoints.sqlite (AsyncSqliteSaver) · store.sqlite [F2] · cache.sqlite [F2] · studio.sqlite
```

### 2.1 Ficheros

| Ruta (bajo `studio/`) | Acción | Fase |
|---|---|---|
| `server.py` | **Sustituir** (la versión v1 pasa a `legacy/` en el paso 1i) | F1 |
| `static/index.html` | **Sustituir** (la v1 sirve solo como referencia visual) | F1 |
| `runner.py` | **Modificar** según §3.1.1 (`@@R`, `thread_id`, `context`, gpu lock) | F1 |
| `arrancar.sh` | **Modificar**: `.venv-studio`, `LANGSMITH_TRACING=false`, imprimir URL y token | F1 |
| `compiler.py`, `engine.py`, `modules.py`, `db.py` | Crear | F1 |
| `static/app.js`, `static/app.css`, `static/vendor/codemirror/…` | Crear | F1 |
| `modules/*.py` (lista §8), `modules/.historial/` | Crear | F1–F3 |
| `templates/*.json` (§9 y Anexo B) | Crear | F1–F3 |
| `settings.json` (se crea con los valores por defecto de §3.7 si no existe) | Crear | F1 |
| `data/` (sqlite, `modsnap/`, `gpu.lock`) | Crear en el arranque | F1 |
| `projects/<slug>/project.json`, `projects/<slug>/versions/<v>.json`, `projects/<slug>/threads/…` | Crear en uso | F1 |
| `tests/` | Crear (§13.1) | F1 |
| `assistants.py`, `herramientas.py`, `modules/tools/*.py`, `static/views/f2.js` | Crear (asistentes, descubrimiento de herramientas, vistas F2) | F2 |
| `docs/prototipo_lg.py` | Ya existe: prototipo verificado | — |
| `nodes.py`, `config.json`, `runs/`, `server.py` v1, `static/index.html` v1 | **Archivar** en `legacy/` (paso 1i) | F1 |
| `DISENO.md`, `REVISION_EGM.md` | Mantener | — |
| `BASE/generar_clip.py` | Se conserva **sin cambios** (CLI independiente) | — |

Correspondencia v1 → v2 (`studio/nodes.py` es la referencia funcional): `guion`→`llm_ollama.py`, `voz`→`tts_kokoro.py`,
`retrato`→`retrato_sdxl.py`, `lipsync`→`lipsync_sadtalker.py`, `montaje`→`montaje_video.py`. La firma pasa de
`(st, cfg, rd)` a `run(state, cfg, ctx)`; `rd`→`ctx.run_dir`; `P/S/log`→`ctx.progress/ctx.update/ctx.log`; el resultado
se **devuelve** (no se emite con `S`).

### 2.2 Alternativas descartadas

- Un único proceso 3.10: `interrupt()` y el stream custom fallan en nodos async.
- Pasar el entorno pesado a 3.12: rompe torch 2.4/transformers 4.46 y los parches de SadTalker.
- `langgraph dev` + Studio oficial: depende de la nube y no permite editar el grafo.
- Motor propio (v1): reimplementa joins/condicionales con heurísticas frágiles.

## 3. Modelo de datos

### 3.1 Módulo (`studio/modules/<nombre>.py`)

Nombre de archivo: `^[a-z][a-z0-9_]{0,47}\.py$`. Archivos que empiezan por `_` se ignoran.

```python
META = {                         # literal (ast.literal_eval)
    "name": "Voz (Kokoro)", "category": "Audio", "icon": "🎙", "desc": "Texto a voz en español.",
    "kind": "task",              # F1: task | input | output | interrupt   F2: router | subgraph | tools | agent
    "exec": "proceso",           # local | proceso
    "interprete": "pesado",      # solo exec proceso: pesado (.venv 3.10) | estudio (.venv-studio 3.12)
    "gpu": "shared",             # false | "shared" | "exclusive"   (§5.6)
    "llm": False,                # True = módulo LLM (pestaña Prompt [F3], descarga de VRAM)
    "inputs": ["texto"],         # claves del estado que lee; en proceso SOLO estas se envían (§3.1.1)
    "outputs": ["audio", "duracion"],              # claves que puede devolver
    "outputs_from_config": [],   # nombres de campos de CONFIG cuyo VALOR es una clave de salida (p.ej. "campo_salida")
    "inputs_from_config": [],    # idem para entradas
    "weight": 5,                 # peso en la barra de progreso general
    "defaults": {"retry": {"max_attempts": 1}, "timeout": {"run": 900, "idle": 180}},
}
CONFIG = [                       # campos del formulario del nodo
    {"key": "voz", "label": "Voz", "type": "select", "default": "", "options": ["ef_dora", "em_alex", "em_santa"],
     "setting": "video.voz", "help": "ef_dora = mujer · em_alex / em_santa = hombre"},
]
def run(state, cfg, ctx):        # ver reglas por modo
    ...
```

[F2] Más claves de `META`: `"command": True` = el módulo enruta con `Command`/`Send` (sus aristas salientes son
destinos, no aristas fijas; solo `exec: local`); `"store_reads"` (ver `ctx.store`). Un campo de `CONFIG` con
`"required": True` y valor vacío o 0 es error de validación del proyecto.

Tipos de campo: `text, textarea, code, number, select, checkbox, json, password, path, model` (desplegable con
`/api/ollama/models`), `state_key` (texto con autocompletado de claves conocidas), `keys` (lista de claves separadas por
comas).

**`cfg` (resolución, gana el último):** `default` de CONFIG → `config` del nodo en el proyecto → `node_overrides` del
asistente [F2]. Si el valor resultante es `""`/`None` y el campo tiene `setting`, se usa `settings[...]` (ruta con puntos,
p. ej. `video.voz`). Se resuelve en el servidor antes de llamar al módulo. Los `number` se convierten a `int`/`float`.

**Reglas por modo:**

- `exec: "local"`: `run` puede ser `def` o `async def`. Devuelve `dict` (actualización), `Command`, una lista de `Send`
  [F2] o `None`. Los `def` se ejecutan en un `ThreadPoolExecutor` propio de 4 workers (no el executor por defecto). Un
  módulo local no puede importar `torch`, `diffusers` ni `kokoro` (lo rechaza la validación AST). **Detener no puede
  interrumpir un `run` síncrono local:** su resultado se descarta.
- `exec: "proceso"`: `run` es `def`, devuelve `dict` o `None`. Devolver `Command`, `Send` o una corrutina es error
  («solo en modo local»). `sys.exit("mensaje")` = error; `sys.exit()` o `sys.exit(0)` = éxito sin salida.

**`ctx`:**

| Miembro | local | proceso | Descripción |
|---|---|---|---|
| `log(*a)` | ✓ | ✓ | línea al log del nodo (evento `node.log`) |
| `progress(x)` | ✓ | ✓ | x ∈ [0,1] (`node.progress`); también refresca el `idle_timeout` |
| `update(dict)` | ✓ | ✓ | **vista previa** en vivo (`node.partial`); **no modifica el estado** |
| `fill(texto, state)` | ✓ | ✓ | sustituye `{clave}` por `state[clave]`; clave ausente → `""` |
| `path(p)` | ✓ | ✓ | ruta relativa a `BASE` → absoluta |
| `stream(cmd, cwd, on_line)` | ✓ | ✓ | subproceso con log línea a línea (incluye `\r` de tqdm); devuelve el código de salida, no lanza |
| `run_dir` | ✓ | ✓ | carpeta de la tarea (§3.8), ya creada |
| `base`, `node_id`, `thread_id`, `run_id`, `settings`, `context` | ✓ | ✓ | `settings` sin `token` |
| `token(texto)` | ✓ | — | emite un token de LLM (`token`) |
| `interrupt(value, schema)` | ✓ | — | `langgraph.types.interrupt(value, response_schema=schema)` |
| `store` [F2] | ✓ | parcial | local: `BaseStore`. Proceso: lecturas declaradas en `META["store_reads"]` se precargan; `ctx.store.put(ns, key, valor, index=True)` / `delete` se devuelven como `"__store_put__": [[ns, key, valor, index], …]` en el resultado |
| `writer`, `runtime` | ✓ | — | `runtime.stream_writer`, objeto `Runtime` |

Validación de módulos («suave» = solo avisos, nunca bloquea; «dura» = el módulo queda marcado con error y no se puede
usar): dura = sintaxis, `META`/`CONFIG` no literales, falta `run`, `kind`/`exec`/`gpu` con valor desconocido, import
prohibido en local. Suave = claves de `inputs` que ningún nodo produce, etc.

#### 3.1.1 Protocolo del subproceso (único y canónico)

- **Lanzamiento:** `argv = [<intérprete>, "-u", "<BASE>/studio/runner.py", "<ruta absoluta del .py>", "<ruta absoluta de
  la carpeta de la tarea>", "<node_id>"]`, `cwd=BASE`, `stdout=PIPE`, `stderr=STDOUT`, `start_new_session=True`. El `pgid`
  se guarda en `runs.pgids` (para la reconciliación §5.9).
- **Entrada:** `<carpeta>/_in.json` = `{"state": {solo las claves de META.inputs (+ inputs_from_config) presentes},
  "cfg": {cfg resuelta}, "settings": {ajustes sin token}, "node": "<id>", "thread_id": "…", "run_id": "…",
  "context": {…}, "store": {precargas [F2]}}`.
- **Mensajes por stdout** (una línea cada uno): `@@P <float 0..1>` progreso · `@@S <json en una línea>` vista previa (no se
  aplica al estado) · `@@R <json en una línea>` **resultado, exactamente una vez, es lo único que se aplica al estado** ·
  cualquier otra línea = log. **No existen `@@L` ni `@@STORE`.**
- **Fin:** salida 0 con `@@R` → éxito. Salida 0 sin `@@R` → éxito sin actualización solo si `run` devolvió `None`
  (runner emite `@@R {}`); si no llegó `@@R`, error. Salida ≠0 → `NodoProcesoError` con las últimas 50 líneas del log.
  Muerte por señal mientras la ejecución está deteniéndose → cancelación (no error).
- **Cambios en `runner.py`:** emitir `@@R` (no un `@@S` final); añadir `thread_id`, `run_id`, `context` a `Ctx`; leer
  `_in.json` (sin sufijo de nodo, porque la carpeta ya es por tarea); rechazar retorno no-dict con mensaje claro; tomar
  `fcntl.flock` sobre `studio/data/gpu.lock` (exclusivo si `META.gpu == "exclusive"`, compartido si `"shared"`) antes de
  llamar a `run` (así `generar_clip.py` y el estudio no chocan si el CLI también lo toma; el CLI no se modifica en F1).
- Opcional [F2]: mover el protocolo a un descriptor dedicado (fd 3 con `pass_fds`) para que una librería que imprima `@@`
  no lo corrompa.

### 3.2 Proyecto (`projects/<slug>/project.json`)

Slug: `^[a-z0-9][a-z0-9-]{0,47}$`, generado del nombre; si existe se añade `-2`, `-3`… Ids de nodo:
`^[a-z][a-z0-9_]{0,39}$`, no pueden coincidir con claves del estado ni empezar por `__`.

```jsonc
{
  "slug": "clip-cleopatra", "name": "Clip · Cleopatra", "desc": "…", "template": "clip_personaje",
  "created": 1763456789.0, "updated": 1763456789.0, "version": 7,
  "state_schema": {                                   // claves declaradas a mano (opcional; ver §3.3)
    "texto":    {"reducer": "replace", "input": true, "output": true},
    "noticias": {"reducer": "append"},
    "messages": {"reducer": "messages"}               // [F2]
  },
  "context_schema": [],                               // [F2] campos tipo CONFIG
  "nodes": {
    "voz": {
      "module": "tts_kokoro.py", "label": "Voz", "x": 640, "y": 170,
      "config": {"voz": "ef_dora"},
      "retry":   {"max_attempts": 2, "initial_interval": 1.0, "backoff_factor": 2.0, "max_interval": 60, "jitter": true},
      "timeout": {"run": 900, "idle": 180},           // run_timeout / idle_timeout en segundos; null = sin límite
      "breakpoint": {"before": false, "after": false},
      "cache": null,                                   // [F2] {"ttl": 3600}
      "defer": false,                                  // [F2]
      "error_to": null,                                // [F2] id de nodo
      "metadata": {}
    }
  },
  "edges": [
    {"id": "e1", "src": "__start__", "dst": "entrada"},
    {"id": "e2", "src": ["voz", "retrato"], "dst": "lipsync"},
    {"id": "e3", "src": "revision", "dst": "voz", "cond": "s.get('aprobado')", "label": "aprobado"},
    {"id": "e4", "src": "revision", "dst": "__end__", "cond": "else"}
  ],
  "layout": {"zoom": 1.0, "pan": [0, 0]}
}
```

Políticas omitidas → `META.defaults` → ajustes (`default_timeout`, sin reintentos). Cada `PUT` guarda además
`versions/<version>.json` (para avisar al continuar un hilo creado con otra versión).

### 3.3 Estado (opción A: canales explícitos, sin `data`)

El compilador genera un `TypedDict(total=False)` con un canal por cada clave de la **unión** de:
`state_schema` · `META.inputs`/`outputs` de los módulos usados · valores de los campos listados en
`inputs_from_config`/`outputs_from_config` · claves de `valores` de los nodos `entrada` · claves que aparecen en las
condiciones (por AST: `s['x']`, `s.get('x')`, `state[...]`) · claves de la entrada de la ejecución.

- Reducer por defecto `replace` (último valor). Otros: `append` (`operator.add` sobre listas), `merge` (dict superficial
  `{**a, **b}`), `sum`, `max`, `messages` [F2] (`add_messages`). Dos nodos paralelos que escriben la misma clave
  `replace` → `InvalidUpdateError` de LangGraph (se muestra como error del superstep).
- Un módulo que devuelve una clave no declarada → error claro: «la clave `x` no está declarada: añádela a outputs del
  módulo o al esquema de estado del proyecto». `__goto__` no existe: el enrutado se hace con aristas, `router` [F2] o
  `Command` [F2].
- `{"clave": {"__overwrite__": v}}` → `Overwrite(v)`, solo válido en claves con reducer distinto de `replace`.
- Borrar una clave: devolver `None` en una clave `replace`.
- Las condiciones, `fill`, la GUI y la API ven siempre un **dict plano** con las claves presentes. Clave ausente → `s.get`
  devuelve `None`; se recomienda usar `s.get(...)`.
- Todo valor debe sobrevivir a `json.dumps`; si no, error del nodo. Aviso en la GUI si un valor supera 1 MB.

### 3.4 Aristas y condiciones → LangGraph

| Proyecto | LangGraph |
|---|---|
| `src` único, sin `cond` | `add_edge(src, dst)` |
| `src` lista (sin `cond`) | `add_edge([..], dst)` (join; acumula entre supersteps). `src` lista + `cond` = error de validación |
| aristas con `cond` desde el mismo `src` | **una** `add_conditional_edges(src, router, path_map)`; el router evalúa las condiciones en el orden de la lista `edges`; devuelve **todas** las verdaderas (fan-out); `else` solo si ninguna es verdadera; ninguna verdadera y sin `else` → error en el nodo origen |
| aristas sin `cond` + aristas con `cond` desde el mismo `src` | se compilan **ambas** (las incondicionales siempre se disparan) |
| `__start__` / `__end__` | `START` / `END`. Si no hay arista desde `__start__`: START → todos los nodos `kind: input`; si no hay, → nodos sin aristas entrantes; si tampoco hay → error de validación |
| `router` [F2] | el módulo devuelve `str` o `list[str]` (ids destino entre sus aristas salientes) |
| `Command(goto=…)` [F2] | `destinations=` declaradas con sus aristas salientes (solo afecta al dibujo) |
| lista de `Send` [F2] | map-reduce |

Condiciones: expresión Python evaluada con `eval(expr, {"__builtins__": {...}}, {"s": estado_plano, "state":
estado_plano})`. Builtins permitidos: `len, min, max, abs, int, float, str, bool, round, any, all, sorted, sum, isinstance,
list, dict, True, False, None`. Validación: compila (`compile(expr, "<cond>", "eval")`), un solo `else` por origen.
Aviso (no error): un join con una fuente detrás de una condición puede no dispararse nunca.

### 3.5 Asistentes [F2], hilos, ejecuciones

- **F1 no tiene asistentes**: `assistant_id` es opcional en la API y se usa el implícito `{"context": {}, "node_overrides": {}}`.
- **Asistente [F2]**: `{assistant_id, project, name, version, context, node_overrides, created}`; versiones inmutables, una
  activa. **No** se compila un grafo por asistente: `context` y `node_overrides` llegan por `runtime.context` y el wrapper
  resuelve `cfg` en ejecución (§3.1). `context_schema` → `TypedDict(total=False)` dinámico.
- **Hilo**: `{thread_id, project, title, metadata, created, updated}`. Su status (`idle | busy | interrupted | error`) **no
  se guarda**: se calcula con las ejecuciones activas y `aget_state` (`interrupts` / `next`).
- **Ejecución**: ver tabla `runs` (§3.6). `status ∈ {pending, running, pausing, drained, interrupted, success, error,
  stopped}`; `motivo` (si `interrupted`) ∈ `{breakpoint, humano}`.

Transiciones: `pending → running → (success | error | stopped | interrupted | pausing → drained)`.
Hilo: ejecución activa → `busy`; ejecución terminó `interrupted` → `interrupted`; `error` → `error`; resto → `idle`.

### 3.6 Esquema de `studio.sqlite`

aiosqlite, `PRAGMA journal_mode=WAL`, `foreign_keys=ON`, migraciones con `PRAGMA user_version`. Tiempos en epoch (float, s).

```sql
-- F1
CREATE TABLE threads(thread_id TEXT PRIMARY KEY, project TEXT NOT NULL, title TEXT, metadata TEXT DEFAULT '{}',
  created REAL, updated REAL);
CREATE TABLE runs(run_id TEXT PRIMARY KEY, thread_id TEXT REFERENCES threads ON DELETE CASCADE, project TEXT,
  project_version INT, module_shas TEXT,                -- JSON {archivo: sha256}
  kind TEXT CHECK(kind IN ('input','continue','resume','replay','fork')),
  input TEXT, checkpoint_id TEXT, checkpoint_id_inicio TEXT,
  interrupt_before TEXT, interrupt_after TEXT, step INT DEFAULT 0,
  status TEXT, motivo TEXT, error TEXT, traceback TEXT, pgids TEXT DEFAULT '[]', t0 REAL, t1 REAL);
CREATE TABLE events(id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, thread_id TEXT, ns TEXT, type TEXT,
  node TEXT, task_id TEXT, data TEXT, ts REAL);
CREATE INDEX ev_run ON events(run_id, id); CREATE INDEX ev_thread ON events(thread_id, id);
CREATE TABLE outputs(id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, thread_id TEXT, project TEXT, node TEXT,
  kind TEXT CHECK(kind IN ('video','audio','image','text')), clave TEXT, path TEXT, texto TEXT, ts REAL);
-- F2
CREATE TABLE assistants(assistant_id TEXT PRIMARY KEY, project TEXT, name TEXT, active_version INT, created REAL);
CREATE TABLE assistant_versions(assistant_id TEXT REFERENCES assistants ON DELETE CASCADE, version INT,
  context TEXT, node_overrides TEXT, created REAL, PRIMARY KEY(assistant_id, version));
CREATE TABLE run_queue(run_id TEXT PRIMARY KEY, thread_id TEXT, payload TEXT, created REAL);   -- double-texting enqueue
-- F3
CREATE TABLE datasets(dataset_id TEXT PRIMARY KEY, name TEXT, created REAL);
CREATE TABLE examples(example_id TEXT PRIMARY KEY, dataset_id TEXT REFERENCES datasets ON DELETE CASCADE,
  input TEXT, expected TEXT, source_run TEXT, created REAL);
CREATE TABLE experiments(experiment_id TEXT PRIMARY KEY, dataset_id TEXT, project TEXT, assistant_id TEXT,
  evaluators TEXT, status TEXT, created REAL);
CREATE TABLE experiment_results(experiment_id TEXT, example_id TEXT, thread_id TEXT, output TEXT, scores TEXT,
  duration REAL, PRIMARY KEY(experiment_id, example_id));
CREATE TABLE crons(cron_id TEXT PRIMARY KEY, project TEXT, assistant_id TEXT, thread_mode TEXT, thread_id TEXT,
  input TEXT, cron TEXT, active INT, last_run REAL, next_run REAL);
```

### 3.7 `settings.json` (valores por defecto literales)

```json
{
  "host": "0.0.0.0", "port": 8790, "token": "",
  "nombre": "Estudio de agentes", "tema": "oscuro", "abrir_interrupts": true, "sonido": true, "notificaciones": false,
  "snap": 10,
  "ollama_url": "http://localhost:11434", "ollama_model": "qwen2.5:7b", "keep_alive": "5m",
  "embed_model": "nomic-embed-text",
  "recursion_limit": 50, "max_concurrency": null, "durability": "async", "default_timeout": 900,
  "multitask_strategy": "reject",
  "python_pesado": "/opt/historico/demo/.venv/bin/python",
  "python_estudio": "/opt/historico/demo/studio/.venv-studio/bin/python",
  "hilos_conservar": 100,
  "video": {"aviso": "Recreación ficticia generada con IA", "color": "0xE8C766", "voz": "ef_dora",
            "retratos": "/opt/historico/demo"}
}
```

`host` y `port` requieren reiniciar (la GUI lo indica). `durability` solo admite `async|sync` (con `exit`, Detener no deja
checkpoints). `token` vacío al arrancar → se genera (`secrets.token_urlsafe(16)`) y se guarda.

### 3.8 Artefactos

`ctx.run_dir = studio/projects/<slug>/threads/<thread_id>/<node_id>/<task_id>/` (`task_id` de
`runtime.execution_info`). Ahí van `_in.json` y las salidas. En el estado se guardan **rutas absolutas**. Así una
bifurcación, un reintento o un `Send` nunca pisan ficheros de otra rama. La purga se hace **por hilo** (§5.8).
[F2, opcional] reutilización por contenido: si `sha256(código, cfg, inputs, hashes de ficheros de entrada)` coincide con
una tarea anterior con `_done.json`, se devuelve su resultado sin relanzar el subproceso.

## 4. Compilación

`build(project, *, assistant=None, checkpointer=None, store=None, cache=None) → CompiledStateGraph`:

1. **Validar** (errores bloquean, avisos no): módulos existen y sin error duro; aristas a nodos existentes; condiciones
   compilan; un `else` por origen; `src` lista sin `cond`; ids válidos; hay entrada (§3.4); un nodo alcanzable desde
   START (aviso si no); joins detrás de condiciones (aviso).
2. **Instantánea de módulos:** al iniciar cada ejecución se calcula `sha256(source)` de cada módulo usado, se copia a
   `data/modsnap/<sha>.py` y se carga con `importlib.util.spec_from_file_location("m_<sha>", …)` (sin `reload`). Los
   `module_shas` se guardan en la ejecución. Al continuar un hilo se usa el código **actual** y, si los sha cambiaron, se
   avisa «código cambiado desde el checkpoint».
3. **Estado:** §3.3.
4. **Nodos:** `add_node(id, wrapper, retry_policy=…, timeout=TimeoutPolicy(run_timeout=…, idle_timeout=…),
   metadata={"module": archivo, "label": etiqueta}, cache_policy=… [F2], defer=… [F2], error_handler=… [F2],
   destinations=… [F2])`. `RetryPolicy(retry_on=…)` explícito que incluya `NodoProcesoError`, `TimeoutError`,
   `ConnectionError` y `NodeTimeoutError` (el `retry_on` por defecto no reintenta `RuntimeError`/`OSError`/`ValueError`).
   - **Wrapper** (siempre `async def wrapper(state, runtime: Runtime, config: RunnableConfig)`; la inyección es por nombre):
     resuelve `cfg`; emite `node.start`; si `gpu`, adquiere el semáforo (§5.6) emitiendo «esperando GPU» y llamando a
     `runtime.heartbeat()` mientras espera; **el timeout del nodo empieza después de adquirirlo** (en `add_node` solo va
     `TimeoutPolicy(idle_timeout=…, refresh_on="heartbeat")`; el wrapper aplica él mismo `run_timeout` con
     `asyncio.timeout` y lanza `NodeTimeoutError(kind="run")`; llama a `runtime.heartbeat()` con cada línea/progreso); ejecuta en local o en proceso; valida la salida
     (claves declaradas, JSON-serializable); traduce `__overwrite__`; emite `node.end`.
   - **Proceso:** §3.1.1. Cancelación: `except asyncio.CancelledError: killpg(SIGTERM); await asyncio.wait_for(proc.wait(),
     1.5); killpg(SIGKILL) si sigue vivo; raise` — **siempre** relanza el `CancelledError` original.
   - **`kind` (F1):** `task`, `input`, `output` e `interrupt` se compilan igual (la diferencia está en el módulo y en la
     GUI). `output`: al terminar, el motor inserta filas en `outputs` según su `cfg.mostrar`. [F2] `router`, `subgraph`,
     `tools`, `agent`: ver §8.
   - **`error_to` [F2]:**
     ```python
     from langgraph.errors import NodeError
     async def handler(state, error: NodeError):
         return Command(goto=error_to, update={"error": {"node": error.node, "msg": repr(error.error)}})
     ```
     con `destinations=(error_to,)`. La GUI oculta los nodos `__error_handler__*`.
5. **Aristas:** §3.4.
6. `compile(checkpointer=AsyncSqliteSaver, store=AsyncSqliteStore [F2], cache=SqliteCache [F2], name=slug)`.
   **Los breakpoints no se compilan:** van en cada `astream` (`interrupt_before/after`).
7. Se compila **en cada ejecución** (no hay caché de grafos compilados).

**Subgrafos [F2]:** el hijo se compila con `checkpointer=None` y `output_schema` = claves `output: true` de su
`state_schema`; se rechazan `append`/`sum` en claves compartidas con el padre; la cadena de subproyectos debe ser acíclica.
Los breakpoints no se propagan al hijo (fuera de alcance).

**Exportar [F3]:** carpeta `export_<slug>/` con `studio_runtime.py` (copia del compilador y wrappers), `project.json`, los
módulos usados, `graph.py` (`graph = build(json.load(...))`) y `langgraph.json` → `graph.py:graph`.

## 5. Ejecución

### 5.1 Llamada

```python
config = {"configurable": {"thread_id": tid, **({"checkpoint_id": cid} if cid else {})},
          "recursion_limit": settings["recursion_limit"], "max_concurrency": settings["max_concurrency"]}
graph.astream(entrada_o_Command_o_None, config, context=asistente_context,
              stream_mode=["updates", "custom", "tasks", "checkpoints"],   # "values" no; "messages" en F2; nunca "debug"
              subgraphs=True, interrupt_before=..., interrupt_after=..., durability=settings["durability"],
              control=RunControl(), version="v1")
```

La tarea asyncio que consume `astream` es la «tarea de la ejecución». Una ejecución por hilo a la vez; varias en hilos
distintos sí (el semáforo GPU las ordena).

### 5.2 Acciones del usuario (un solo verbo para seguir: **Continuar**)

| Acción | Llamada | `runs.kind` |
|---|---|---|
| Iniciar (hilo nuevo o existente) | `POST /threads/{id}/runs {"input": {...}}` → `astream(input)` | input |
| Continuar (tras `drained`, `stopped` o breakpoint) | `{"input": null}` → `astream(None)` | continue |
| Responder a un interrupt humano | `{"resume": {"<interrupt_id>": valor}}` → `astream(Command(resume={id: valor}))` (siempre por id) | resume |
| Reproducir desde un checkpoint | `{"checkpoint_id": cid}` → `astream(None, config con checkpoint_id)` | replay |
| Editar y bifurcar | `POST /threads/{id}/state {"checkpoint_id", "values", "as_node"}` → nuevo cid; luego `POST runs {"checkpoint_id": nuevo}` | fork |
| Paso a paso | `{"step": true, ...}` = `interrupt_before="*"`; mientras esté activo, Continuar envía `step:true` (solo nivel superior) | — |
| Re-ejecutar nodo [F2] | reproducir desde el checkpoint más reciente de la rama actual cuyo `next` contiene el nodo | replay |
| Pausar | `POST /runs/{id}/pause` → `RunControl.request_drain()`; status `pausing` («esperando a que termine <nodos>»); al acabar el superstep → `drained`. **Espera a todas las tareas del superstep** (con SadTalker puede tardar minutos; la GUI lo explica) | — |
| Detener | `POST /runs/{id}/stop` (§5.3) | — |

`as_node` al editar: el nodo que escribió ese checkpoint (`metadata.writes`); si fueron varios, el primero en orden de
`edges`; si no hay escritor (entrada) se omite. Las aristas del escritor se re-evalúan con los valores editados (§1.2).
«Bifurcar sin cambios» = `as_node="__copy__"` con `values` nulo (ignora `values`). **Iniciar** con entrada en un hilo con
`next` pendiente abandona ese `next` y arranca desde START (la GUI pide confirmación).

### 5.3 Detener

1. Marcar la ejecución `stopping` (en memoria) y llamar `task.cancel()` sobre la tarea de la ejecución (**nunca** `killpg`
   primero).
2. Los wrappers de proceso reciben `CancelledError` y matan su grupo (§4); los locales async se cancelan; los locales sync
   siguen en su thread y su resultado se descarta.
3. Esperar a que la tarea termine (el checkpointer conserva el último superstep completo y las escrituras pendientes de
   tareas ya terminadas — verificado en 1a, §1.2). Status `stopped`. El hilo se puede **Continuar**.
4. Criterio: sin procesos `SadTalker/inference.py` en < 3 s.

### 5.4 Interrupts

- **Breakpoint** (antes/después, configurable por nodo en la GUI; `interrupt_before/after` por ejecución) → status
  `interrupted`, `motivo: breakpoint`; la GUI muestra **Continuar** y permite editar el estado (§5.2).
- **Humano** (`ctx.interrupt` en un módulo local, p. ej. `revision_humana.py`) → `interrupted`, `motivo: humano`; la GUI
  muestra el formulario generado desde `response_schema`; **Responder** reanuda. Regla para módulos: **`interrupt` es la
  primera operación del nodo**, porque al reanudar el nodo se ejecuta desde el principio. Varios interrupts pendientes
  (ramas paralelas) se responden uno a uno por id.
- Si el superstep contiene un interrupt y se pidió drain, el status final es `interrupted` (no `drained`) — verificado en 1a.

### 5.5 Time travel

- Historia del hilo: `aget_state_history(config)` → lista de checkpoints `{checkpoint_id, parent_checkpoint_id, step,
  next, writes, created_at}`; la GUI la dibuja como árbol (ramas por `parent_checkpoint_id`).
- Seleccionar un checkpoint muestra `values` (planos), `next`, `tasks`, `interrupts`, `metadata`.
- **Reproducir desde aquí** y **Editar y bifurcar**: §5.2. La rama original queda intacta (artefactos por tarea, §3.8).

### 5.6 GPU y VRAM

- `META.gpu`: `false` | `"shared"` (Kokoro, LLM de Ollama) | `"exclusive"` (SDXL, SadTalker).
- Semáforo en el servidor: lector/escritor asyncio — varios `shared` a la vez o un `exclusive` solo — y además
  `fcntl.flock` sobre `data/gpu.lock` dentro de `runner.py` (compartido/exclusivo) para coordinar con procesos externos.
- **Antes de un nodo `exclusive`:** pedir a Ollama `GET /api/ps` y, por cada modelo cargado, `POST /api/generate
  {"model": m, "keep_alive": 0}` (D6). Así `keep_alive="5m"` no deja sin VRAM al vídeo y los bucles LLM no recargan el
  modelo en cada llamada.
- El timeout del nodo empieza después de adquirir la GPU; la GUI muestra «esperando GPU».
- `max_concurrency=null` por defecto (la GPU ya la ordena el semáforo).
- Cada nodo de proceso carga y descarga su modelo (20–60 s de carga en SDXL/Kokoro): decisión consciente para liberar
  VRAM; «workers persistentes» queda como posible F3.

### 5.7 Double-texting

- **F1:** solo `reject` → `409 {"code": "thread_busy"}`.
- **F2:** `enqueue` (fila en `run_queue`, un worker por hilo la lanza al terminar la actual), `interrupt` («interrumpir y
  lanzar»: detener la actual y lanzar la nueva desde el último checkpoint) y `rollback` (detener la actual y lanzar la nueva
  desde `checkpoint_id_inicio` de la actual: es una bifurcación; la rama abandonada se marca `rolled_back` en
  `threads.metadata` y se oculta por defecto). No se borra ningún checkpoint.

### 5.8 Copiar, borrar y purgar hilos

- **Copiar hilo:** SQL propio en una transacción sobre `checkpoints.sqlite`: `INSERT INTO checkpoints SELECT
  <nuevo_thread_id>, … FROM checkpoints WHERE thread_id=?` e igual con `writes` (confirmar columnas con `.schema` en 1a),
  más la fila de `threads` y copia de la carpeta de artefactos (reescribiendo el prefijo de rutas en el estado no es
  necesario: las rutas viejas siguen existiendo; la copia solo se necesita si se purga el original — documentarlo).
- **Borrar hilo:** `adelete_thread` + filas de `studio.sqlite` (cascada) + carpeta `threads/<id>/` + entradas de caché [F2].
- **Purgar:** conservar los `hilos_conservar` más recientes por proyecto; borrar el resto con «Borrar hilo». Copia de
  seguridad opcional con `VACUUM INTO`.

### 5.9 Arranque y apagado

- Lifespan de FastAPI: abrir `AsyncSqliteSaver`, `AsyncSqliteStore` (+`await store.setup()`) [F2], `SqliteCache` [F2] y
  `studio.sqlite`; cerrarlos al salir.
- Al arrancar (reconciliación): para cada `runs.pgids` de ejecuciones `running/pausing`, si `/proc/<pid>/cmdline`
  contiene `runner.py`, `killpg`; `running|pausing` → `stopped` (error «servidor reiniciado»); `pending` → `error`.
- Al apagar: `killpg` de todos los subprocesos vivos.
- uvicorn con 1 worker y sin `--reload`.

### 5.10 Programaciones [F3]

Bucle asyncio con `croniter`; cada disparo crea una ejecución normal (hilo nuevo o fijo según `thread_mode`). Detalle en §6.4.

## 6. API

Bajo `/api`, JSON UTF-8. **Autenticación:** todas las rutas `/api/*` exigen el token en la cabecera `X-Token` o en la
cookie `studio_token` (`/`, `/static/*` libres). Sin token válido → 401 y la GUI pide el token y lo guarda en la cookie.
**Errores:** `{"detail": str, "code": str, "line"?: int}` · 400 `validation` · 401 · 404 · 409 `version_conflict` |
`thread_busy` | `module_in_use` | `not_interrupted` · 422 `syntax`.

### 6.1 Contratos F1

| Endpoint | Cuerpo → Respuesta |
|---|---|
| `GET /modules` | → `[{file, meta, config, error, usos: [slug…]}]` |
| `GET /modules/{file}/source` | → `{source, sha}` |
| `PUT /modules/{file}/source` | `{source, sha}` (sha cargado; distinto → 409 `version_conflict`) → 200 `{meta, config, sha}` · 422 `{detail, line}`. Guarda antes `modules/.historial/<file>.<YYYYMMDD-HHMMSS>` |
| `POST /modules` | `{name, from?: file}` (esqueleto o duplicar módulo) → 201 `{file}` |
| `DELETE /modules/{file}` | → 204 · 409 `module_in_use {usos}`. Mueve a `.historial` |
| `GET /modules/{file}/history` · `POST /modules/{file}/restore {ts}` | historial y restaurar |
| `GET /templates` | → `[{id, name, desc, icon, fase}]` |
| `GET /projects` | → `[{slug, name, desc, template, updated, version, n_nodes, n_threads, ultimo_status}]` |
| `POST /projects` | `{name, template, desc?}` → 201 proyecto |
| `GET /projects/{slug}` | → proyecto |
| `PUT /projects/{slug}` | proyecto completo con la `version` cargada → 200 proyecto (`version+1`) · 409 `{code: "version_conflict", current}` |
| `DELETE /projects/{slug}` | → 204 (borra hilos y artefactos; 409 `thread_busy` si hay ejecución activa) |
| `POST /projects/{slug}/duplicate` | `{name}` → 201 |
| `POST /projects/import` | proyecto JSON (sin slug/created/version) → 201 |
| `GET /projects/{slug}/export` | → JSON descargable (F3: `?format=py`) |
| `GET /projects/{slug}/validate` | → `{errores: [...], avisos: [...], canales: {clave: reducer}}` |
| `GET /projects/{slug}/mermaid` | → `{mermaid}` (de `get_graph().draw_mermaid()`) |
| `GET /threads?project=` · `POST /threads {project, title?}` | lista · 201 `{thread_id}` |
| `GET/PATCH/DELETE /threads/{id}` | PATCH `{title?, metadata?}` |
| `POST /threads/{id}/copy` | → 201 `{thread_id}` |
| `GET /threads/{id}/state?checkpoint_id=` | → `{values, next, tasks, interrupts: [{id, value, response_schema, node}], checkpoint_id, parent_checkpoint_id, metadata, created_at, version_proyecto}` |
| `GET /threads/{id}/history?limit=` | → `[{checkpoint_id, parent_checkpoint_id, step, next, writes, created_at, source}]` |
| `POST /threads/{id}/state` | `{values, checkpoint_id?, as_node?}` → `{checkpoint_id}` · 409 `thread_busy` |
| `GET /threads/{id}/view` | → `{run, nodos: {id: {status, progress, attempts, count, error, cached}}, aristas_recorridas: [edge_id]}` (para reconstruir colores al recargar) |
| `POST /threads/{id}/runs` | `{input?: obj|null, resume?: {id: valor}, checkpoint_id?, interrupt_before?, interrupt_after?, step?: bool, multitask_strategy?}` — como mucho uno de `input`/`resume`; ambos nulos = continuar → 202 `{run_id, status: "pending"}` · 409 `thread_busy` |
| `GET /runs?thread=&limit=` · `GET /runs/{id}` | |
| `POST /runs/{id}/pause` · `POST /runs/{id}/stop` | → 202 |
| `GET /media?path=` | solo ficheros bajo `BASE` con extensión de medio (`.mp4 .webm .wav .mp3 .png .jpg .jpeg .webp .txt`); `FileResponse` (soporta Range) |
| `GET /images` | → retratos en `settings.video.retratos` (png/jpg, no recursivo) |
| `POST /images` | multipart campo `file` (png/jpg ≤ 20 MB) → guardado en `settings.video.retratos` → `{path}` (requiere `python-multipart`, añadir a F1) |
| `GET/PUT /settings` | `{schema, values}` / valores |
| `GET /system` | → `{gpu: {name, mem_used, mem_total, temp}, ram, disco, versiones, ollama: {ok, cargados}}` |
| `GET /ollama/models` | → `[nombre]` |
| `GET /outputs?kind=video` | galería: filas de `outputs` + vídeos `legacy/runs/*/final.mp4` con `legacy: true` |
| `DELETE /outputs/{id}` | borra el fichero y la fila |
| `POST /quick-video` | `{nombre_proyecto?, personaje, origen_texto, texto?, tema?, imagen?, prompt_imagen?, voz, nombre, subtitulo, iniciar: bool}` → crea proyecto desde `clip_personaje`, ajusta `config`, crea hilo y (si `iniciar`) lanza → 201 `{slug, thread_id, run_id?}` |

Los contratos de F2 están en §6.3 y los de F3 en §6.4.

### 6.3 Contratos F2 *(rev. 2.2, aprobado el 2026-11-18)*

Mismas reglas que §6 (token, formato de error). Códigos nuevos: 409 `assistant_in_use`, 409 `not_rerunnable`,
502 `ollama` (Ollama no responde al calcular embeddings o al hablar con el LLM), 404 `not_found` para cualquier id
inexistente.

**Asistentes** (tablas `assistants` y `assistant_versions` de §3.6). Un asistente pertenece a un proyecto. Las versiones
son inmutables; cambiar `context` o `node_overrides` crea la versión `n+1` y la activa.

| Endpoint | Cuerpo → Respuesta |
|---|---|
| `GET /projects/{slug}/assistants` | → `[{assistant_id, project, name, active_version, n_versions, created, updated}]` |
| `POST /projects/{slug}/assistants` | `{name, context?: obj, node_overrides?: {nodo: {campo: valor}}}` → 201 asistente (versión 1, activa) · 400 `validation` |
| `GET /assistants` *(D8)* | → todos los asistentes de todos los proyectos, el más reciente primero, con `project_name` (para Inicio) |
| `GET /assistants/{id}` | → `{assistant_id, project, name, active_version, n_versions, created, updated, context, node_overrides}` (de la versión activa) |
| `PATCH /assistants/{id}` | `{name?, context?, node_overrides?}` → 200 asistente. Solo `name` = renombrar sin versión nueva; con `context` o `node_overrides` = versión nueva (los campos ausentes se copian de la activa) |
| `GET /assistants/{id}/versions` | → `[{version, context, node_overrides, created}]` (descendente) |
| `POST /assistants/{id}/activate/{v}` | → 200 asistente · 404 si no existe la versión |
| `DELETE /assistants/{id}` | → 204 (las ejecuciones antiguas conservan `assistant_id`/`assistant_version` como texto) |

Validación (400 `validation`, `detail` dice qué campo): `node_overrides` solo con ids de nodo existentes y claves de la
`CONFIG` de su módulo; `context` contra `project.context_schema` (lista de campos con el formato de `CONFIG`; clave
desconocida = error, `number` se convierte, falta de campo = su `default`). El `context` efectivo de una ejecución =
defaults de `context_schema` + `context` de la versión. Llega a los módulos por `ctx.context` (local y proceso) y a
LangGraph por `astream(context=…)`.

**Ejecuciones (cambios sobre §6.1):** `POST /threads/{id}/runs` acepta además `assistant_id?`, `assistant_version?`
(por defecto la activa) y `multitask_strategy ∈ {reject, enqueue, interrupt, rollback}` (por defecto
`settings.multitask_strategy`; solo actúa si el hilo está ocupado). Respuestas: `reject` → 409 `thread_busy` (igual que
F1) · `enqueue` → 202 `{run_id, status: "pending", queued: true, position}` · `interrupt`/`rollback` → 202 `{run_id,
status: "pending", replaces: <run_id anterior>}`. Tabla `runs`: columnas nuevas `assistant_id`, `assistant_version`,
`multitask_strategy`; `status` admite además `queued` (en `run_queue`, aún no lanzada) y `motivo` admite `interrupt` y
`rollback` (ejecución detenida por una nueva). `POST /runs/{id}/stop` sobre una `queued` la saca de la cola → `stopped`.

| Endpoint | Cuerpo → Respuesta |
|---|---|
| `POST /threads/{id}/nodes/{node}/rerun` | `{assistant_id?, interrupt_before?, interrupt_after?}` → 202 `{run_id, status: "pending", checkpoint_id}` (= §5.2 «Re-ejecutar nodo»: `replay` desde el checkpoint más reciente **de la rama actual** —ancestros del checkpoint de cabeza— cuyo `next` contiene `node`) · 409 `not_rerunnable` si ningún checkpoint de la rama lo tiene en `next` · 409 `thread_busy` |
| `GET /threads/{id}/history?rolled_back=1` | como F1; por defecto **oculta** los checkpoints marcados en `threads.metadata.rolled_back` |

**Memoria (Store).** `AsyncSqliteStore` en `data/store.sqlite`, `index={"dims": 768, "embed": fn, "fields": ["$"]}`;
`fn` llama a `POST {ollama_url}/api/embed` con `settings.embed_model`. Un namespace es una lista de cadenas; en la URL se
escribe como segmentos separados por `/` (`ns=usuarios/fer`; un segmento no puede contener `/` ni estar vacío).

| Endpoint | Cuerpo → Respuesta |
|---|---|
| `GET /store/namespaces?prefix=&max_depth=&limit=100` | → `[[seg, …]]` |
| `GET /store/items?ns=&query=&limit=20&offset=0` | sin `query` → lista ordenada por `updated_at` desc; con `query` → búsqueda semántica → `[{namespace, key, value, created_at, updated_at, score}]` (score solo con `query`) · 502 `ollama` |
| `GET /store/items?ns=&key=` | → un item · 404 |
| `PUT /store/items` | `{namespace: [..], key, value: obj, index?: bool}` (`index: false` = sin embedding) → 200 item · 502 `ollama` |
| `DELETE /store/items?ns=&key=` | → 204 |

Módulos en proceso (§3.1 `ctx.store` «parcial»): `META["store_reads"] = [[seg, …], …]` (namespaces literales; un segmento
puede ser `{clave}` y se sustituye con `ctx.context`/estado) → el motor precarga hasta 200 items por namespace en
`_in.json["store"]`; `ctx.store.get(ns, key)`, `ctx.store.search(ns)` (sin semántica: filtro por prefijo) y
`ctx.store.put(ns, key, valor)`; las escrituras se devuelven como `"__store_put__"` en el resultado y el wrapper las
aplica con `aput` antes de devolver la actualización (no es una clave del estado).

**Caché de nodos.** `node.cache = {"ttl": s | null}` → `CachePolicy(key_func, ttl)`. Clave = `sha256(slug, node_id,
sha del módulo, cfg efectiva, valores de META.inputs)`. Un fichero por proyecto: `data/cache/<slug>.sqlite`
(`SqliteCache`), para poder vaciarlo por proyecto (desviación de §2, que decía un solo `cache.sqlite`). Al acertar, si la
salida contiene rutas bajo `BASE` que ya no existen, se trata como fallo de caché y el nodo se ejecuta.

| Endpoint | Cuerpo → Respuesta |
|---|---|
| `GET /projects/{slug}/cache` | → `{entradas, bytes}` |
| `DELETE /projects/{slug}/cache?node=` | → 204 (sin `node` vacía todo el proyecto) |

**Trazas.**

| Endpoint | Cuerpo → Respuesta |
|---|---|
| `GET /runs?thread=&project=&status=&since=&limit=` | §6.1 con filtros nuevos (`project`, `status` separado por comas, `since` epoch) |
| `GET /runs/{id}/trace` | → `{run, spans: [{task_id, node, label, ns, attempt, t0, t_gpu, t1, status, error, cached}], totals: {duration, n_spans, errores, reintentos, espera_gpu, tokens}}`. `t0` = primer `node.start`; `t_gpu` = fin de la espera de GPU (null si no esperó); `t1` = `node.end`; spans de subgrafos con su `ns`. Se calcula de la tabla `events` |

**Herramientas y grafo.**

| Endpoint | Cuerpo → Respuesta |
|---|---|
| `GET /tools` | → `[{name, desc, file, args_schema, error}]` (funciones `@tool` de `modules/tools/*.py`, descubiertas por AST como los módulos; error de importación → `error`) |
| `GET /projects/{slug}/mermaid?xray=1` | como F1; `xray=1` expande subgrafos y agentes |
| `GET /threads/{id}/messages` | → `[{id, role: user\|assistant\|tool\|system, content, tool_calls?, tool_call_id?, name?}]` (de la clave `messages` del estado actual; 404 si el proyecto no la tiene) |

**Eventos SSE nuevos** (amplían la lista cerrada de §6.2): `token {text, msg_id}` (ahora también desde el stream
`messages` de LangGraph, con `node` = nodo que llama al LLM) · `run.queued {position}` · `node.end` admite `status:
cached` (emitido por el motor cuando `updates` trae `__metadata__.cached`, ya que el wrapper no se ejecuta). Los spans de
`__error_handler__*` se emiten con `node` = nodo que falló y `data.handler = true`.

**Settings:** `multitask_strategy` admite las cuatro estrategias; se añade `store_items_por_ns: 200`.

### 6.4 Contratos F3 *(rev. 2.3, aprobado el 2026-11-18)*

Mismas reglas que §6 y §6.3 (token, formato de error, 404 `not_found`, 502 `ollama`). Códigos nuevos: 400 `cron`
(expresión cron no válida), 400 `unsupported` (la exportación funcional o el playground no admiten el proyecto/nodo; el
`detail` dice qué), 409 `experiment_running`, 502 `mcp` (un servidor MCP no arranca o no responde).

**Migración `user_version = 3`.** Las tablas F3 de §3.6 con estos cambios: `datasets` + `desc TEXT`, `project TEXT`
(proyecto sugerido, opcional); `examples` + `metadata TEXT DEFAULT '{}'`; `experiments` + `name TEXT`,
`assistant_version INT`, `concurrency INT DEFAULT 1`, `t0 REAL`, `t1 REAL`, `error TEXT`; `experiment_results` +
`run_id TEXT`, `status TEXT`, `error TEXT` (`scores` = JSON `{evaluador: {score: float|null, comentario: str}}`);
`crons` + `name TEXT`, `multitask_strategy TEXT DEFAULT 'enqueue'`, `created REAL`, `last_error TEXT`; `runs` +
`cron_id TEXT`, `experiment_id TEXT`.

**Datasets.** Un ejemplo = `{input: obj, expected: obj|null}`; `input` es la entrada de una ejecución (`POST runs
{"input"}`), `expected` lo que leen los evaluadores.

| Endpoint | Cuerpo → Respuesta |
|---|---|
| `GET /datasets` | → `[{dataset_id, name, desc, project, n_examples, n_experiments, created}]` |
| `POST /datasets` | `{name, desc?, project?}` → 201 dataset · 400 `validation` (nombre vacío o repetido) |
| `GET /datasets/{id}` | → dataset · `PATCH {name?, desc?, project?}` → 200 · `DELETE` → 204 (borra ejemplos y experimentos; sus hilos quedan) |
| `GET /datasets/{id}/examples?limit=&offset=` | → `[{example_id, input, expected, metadata, source_run, created}]` |
| `POST /datasets/{id}/examples` | `{input: obj, expected?: obj, metadata?}` o lista de ellos → 201 `[example]` |
| `POST /datasets/{id}/examples/from-run` | `{run_id, expected?: obj}` → 201 example. `input` = `runs.input` (400 si la ejecución no tuvo entrada, p. ej. `continue`); `expected` por defecto = valores del estado final del hilo de esa ejecución restringidos a las claves `output: true` del `state_schema` (todas si no hay ninguna marcada); `source_run` = `run_id` |
| `PATCH /examples/{id}` | `{input?, expected?, metadata?}` → 200 · `DELETE /examples/{id}` → 204 |
| `GET /datasets/{id}/export` · `POST /datasets/import` | JSONL (`{"input", "expected", "metadata"}` por línea; la primera línea puede ser `{"__dataset__": {name, desc}}`) → 201 dataset |

**Experimentos.** Ejecutan cada ejemplo del dataset en un **hilo nuevo** del proyecto (`threads.metadata = {experiment:
id, example: id, oculto: true}`; la vista Hilos los oculta salvo con «mostrar ocultos»), con ejecuciones normales del
motor (`runs.experiment_id`), y después aplican los evaluadores. `concurrency` (1–4, por defecto 1) = ejemplos a la vez
(la GPU sigue ordenada por el semáforo). Un ejemplo cuya ejecución termina `interrupted` se da por terminado con
`status: interrupted` y sin puntuaciones (no se responde a interrupts). `output` = valores finales del hilo (claves
`output: true`, o todas).

Evaluadores (`evaluators` = lista, nombres únicos):
- `{name, type: "expr", expr}`: expresión con las mismas reglas y builtins que las condiciones (§3.4), variables
  `output`, `expected`, `input` (dicts planos). `bool` → 1.0/0.0; número → float; otra cosa o excepción → `score: null`
  y `comentario` = el error. Se valida con `compile` al crear (400).
- `{name, type: "llm", prompt, modelo?}` (LLM-juez): `prompt` con marcadores `{input}`, `{output}`, `{expected}` (JSON);
  se llama a Ollama con `format: "json"`, `temperature: 0` y se espera `{"score": 0..1, "comentario": "…"}`; respuesta
  no válida → `score: null`. Ollama caído → `score: null`, `comentario: "ollama"` (el experimento no falla).

| Endpoint | Cuerpo → Respuesta |
|---|---|
| `POST /experiments` | `{dataset_id, project, name?, assistant_id?, assistant_version?, evaluators: […], concurrency?}` → 202 `{experiment_id, status: "pending"}` · 400 `validation` (proyecto que no compila, evaluador mal formado, dataset vacío) |
| `GET /experiments?dataset=&project=` | → `[{experiment_id, name, dataset_id, project, assistant_id, assistant_version, status, n, hechos, resumen, t0, t1}]`; `resumen` = `{evaluador: media de los score no nulos}`; `status ∈ {pending, running, success, error, stopped}` (`success` = todos los ejemplos terminaron, aunque alguno tenga error de ejecución) |
| `GET /experiments/{id}` | → experimento + `evaluators` + `results: [{example_id, input, expected, thread_id, run_id, status, output, scores, duration, error}]` |
| `POST /experiments/{id}/stop` | → 202: detiene las ejecuciones en curso (§5.3) y no lanza más; status `stopped` |
| `POST /experiments/{id}/rescore` | `{evaluators}` → 202: vuelve a puntuar los resultados guardados sin re-ejecutar (sustituye `evaluators` y `scores`) · 409 `experiment_running` |
| `DELETE /experiments/{id}` | → 204 · 409 `experiment_running`. Borra sus filas; sus hilos se borran con «Borrar hilo» (§5.8) |
| `GET /experiments/compare?ids=a,b[,…]` | (mismo dataset; si no, 400) → `{experimentos: [...], filas: [{example_id, input, expected, por_experimento: {exp_id: {output, scores, status, duration}}}], resumen: {exp_id: {evaluador: media}}}` |

Al reiniciar el servidor, un experimento `pending/running` pasa a `stopped` (sus ejecuciones ya se reconcilian por
§5.9). Eventos SSE nuevos (amplían §6.2; `thread_id = null`): `experiment.status {experiment_id, status, hechos, n}`.

**Programaciones (crons).** Expresión de 5 campos (minuto…día de la semana) o de 6 con **segundos al final** (convención
de `croniter`, usada por los tests), en hora local de la máquina. `thread_mode ∈ {nuevo, fijo}`: `nuevo` crea un hilo
por disparo (`threads.metadata = {cron: id}`, título `«<name> · <fecha>»`); `fijo` usa `thread_id` (obligatorio y del
mismo proyecto) y, si está ocupado, aplica `multitask_strategy` del cron (por defecto `enqueue`; con `reject` el disparo
se salta y se anota en `last_error`).

| Endpoint | Cuerpo → Respuesta |
|---|---|
| `GET /crons?project=` | → `[{cron_id, name, project, assistant_id, thread_mode, thread_id, input, cron, multitask_strategy, active, last_run, next_run, last_error, created}]` |
| `POST /crons` | `{project, name?, cron, input?: obj, thread_mode?: "nuevo", thread_id?, assistant_id?, multitask_strategy?, active?: true}` → 201 cron con `next_run` · 400 `cron` · 400 `validation` |
| `GET /crons/{id}` · `PATCH /crons/{id}` (mismos campos; recalcula `next_run`) · `DELETE /crons/{id}` | → cron · 200 · 204 (no borra hilos) |
| `POST /crons/{id}/run` | dispara ahora sin mover `next_run` → 202 `{run_id, thread_id}` (o la respuesta de double-texting de §6.3) |
| `GET /crons/{id}/runs?limit=` | → ejecuciones con `runs.cron_id = id` (formato de `GET /runs`) |

Planificador (§5.10): una tarea asyncio en el lifespan que despierta cada segundo y dispara los crons activos con
`next_run ≤ ahora`; tras disparar, `last_run = ahora` y `next_run` = siguiente ocurrencia **posterior a ahora** (sin
ponerse al día: si el servidor estuvo parado, al arrancar se dispara **una vez** cada cron vencido). Un fallo al lanzar
(proyecto borrado o que no compila) se guarda en `last_error` sin desactivar el cron. Evento SSE `cron.fired {cron_id,
run_id, thread_id}`.

**Playground de prompts.** Ejecuta **un nodo aislado** (fuera del grafo, sin hilo, sin checkpoints, sin `events`) con un
estado dado y variantes de su `cfg`. Solo nodos cuyo módulo tiene `META.llm = True` y `exec: local` con `kind ∈ {task,
router}` (hoy `llm_ollama`, `router_llm`, `chat_ollama`); el resto → 400 `unsupported`. `ctx.interrupt` y `ctx.store.put`
lanzan error en el playground; `ctx.store` de lectura funciona.

| Endpoint | Cuerpo → Respuesta |
|---|---|
| `POST /projects/{slug}/nodes/{node}/playground` | `{state?: obj, thread_id?, checkpoint_id?, assistant_id?, variantes: [{campo: valor}, …] (1–4; `{}` = cfg actual)}` → 200 `{resultados: [{cfg, prompt, sistema, salida, tokens, logs, duracion, error}]}`. Estado de partida: `state` si viene; si no, los valores del hilo en `checkpoint_id` (o su cabeza); si no, `{}`. `cfg` = resolución de §3.1 + override del asistente + la variante. `prompt`/`sistema` = `ctx.fill` de esos campos si existen. Las variantes se ejecutan **en secuencia**. Error del módulo → `error` en esa variante (200). Ollama caído → 502 `ollama` |

Guardar una variante en el nodo se hace con el `PUT /projects/{slug}` de siempre (la GUI copia la `cfg` al nodo).

**Exportar.** `GET /projects/{slug}/export?format=py` → `application/zip` con la carpeta `export_<slug>/`:

| Fichero | Contenido |
|---|---|
| `studio_runtime/` | copia de `compiler.py`, `modules.py`, `runner.py`, `herramientas.py` (solo lo que `build` importa; sin FastAPI) |
| `modules/` | los `.py` de los módulos usados (incluidos los de subproyectos) y, si hay un nodo `agent`, `modules/tools/*.py` |
| `projects/` | `project.json` y los de sus subproyectos (`subgrafo`) |
| `settings.json` | ajustes actuales **sin `token`** |
| `graph.py` | fija `STUDIO_HOME` a su carpeta, `graph = build(project).graph` sin checkpointer (lo pone quien lo sirva) |
| `funcional.py` | solo si el proyecto es exportable a la API funcional (ver abajo): `graph` con `@entrypoint`/`@task` |
| `langgraph.json` | `{"dependencies": ["."], "graphs": {"<slug>": "./graph.py:graph"[, "<slug>_funcional": "./funcional.py:graph"]}, "env": ".env"}` |
| `requirements.txt` · `LEEME.md` | versiones del intérprete estudio; cómo ejecutarlo, qué nodos necesitan el intérprete pesado/GPU, por qué no hay `funcional.py` si falta |

**API funcional** (`funcional.py`): un `@task` por nodo (llama al mismo wrapper que el compilador) y un
`@entrypoint(checkpointer=…)` que recorre el grafo por supersteps: lanza en paralelo las tareas de la frontera, aplica
las actualizaciones con los reducers de §3.3, y calcula la frontera siguiente con las mismas reglas de aristas (§3.4:
condiciones en orden, fan-out, `else`, joins que esperan a todas sus fuentes, ciclos) con el límite `recursion_limit`.
`interrupt` dentro de un módulo funciona igual (al reanudar, las tareas ya terminadas no se repiten). **No admite**
(→ sin `funcional.py` y motivo en `LEEME.md`; con `?format=funcional` → 400 `unsupported`): `Send`/`Command`, `kind`
`router`/`subgraph`/`agent`, `cache`, `defer`, `error_to`. `GET /projects/{slug}/export?format=funcional` → solo el
`.py` (text/x-python).

**MCP.** Ajuste nuevo `mcp_servidores: {}` = `{nombre: {"transport": "stdio", "command": str, "args": [str], "env"?:
{}} | {"transport": "streamable_http", "url": str}}` (formato de `MultiServerMCPClient` de `langchain-mcp-adapters`).
Las herramientas de un servidor se llaman `<nombre>__<herramienta>`. Las sesiones no se mantienen abiertas: cada
llamada abre y cierra la suya (sencillo; el coste de arrancar un servidor stdio es aceptable).

| Endpoint | Cuerpo → Respuesta |
|---|---|
| `GET /mcp/servers` | → `[{nombre, transport, ok, error, tools: [{name, desc, args_schema}]}]` (lista las herramientas de cada servidor con un timeout de 10 s; un servidor caído → `ok: false` y `error`, nunca 502) |
| `POST /mcp/call` | `{servidor, herramienta, args: obj}` → 200 `{resultado}` · 404 servidor o herramienta · 502 `mcp` (prueba manual desde la GUI) |

Uso desde el grafo: módulo `mcp_herramientas.py` (task / local; `servidor: select`, `herramienta: text`, `argumentos:
json` con `{clave}` sustituidos desde el estado, `campo_salida: state_key = resultado_mcp`; error del servidor = error
del nodo, reintentable) y campo nuevo `mcp: keys = ""` en `agente_react` (nombres de servidores cuyas herramientas se
añaden a las de `modules/tools/`).

### 6.2 Eventos SSE

- `GET /api/events?thread=<id>&after=<event_id>` (sin `thread` = todos los hilos). Formato SSE con `id:` (= `events.id`),
  `event: <type>`, `data: <json>`; `: ping` cada 15 s. Respeta `Last-Event-ID` y reenvía desde la tabla `events`.
- **Sobre:** `{id, run_id, thread_id, ns, type, node, task_id, data, ts}` (`ns` = espacio de nombres del subgrafo, `""` en
  el nivel superior).
- **Tipos (lista cerrada):** `run.status {status, motivo?, error?, traceback?}` · `node.start {attempt, esperando_gpu?}` ·
  `node.progress {value}` · `node.log {line}` · `node.partial {patch}` · `node.end {status: success|error|cached|
  interrupted, output, error?, duration}` · `interrupt {id, value, response_schema, node}` · `checkpoint {checkpoint_id,
  parent_checkpoint_id, step, next, source}` · `token {text}` · `thread.status {status}` · `edge {edge_id}` (arista
  recorrida, calculada por el motor al terminar un nodo comparando con `next`).
- **Correspondencia:** `node.start`/`node.end` los emite el wrapper (no `tasks`, que reemite inicios de escrituras
  reutilizadas, §1.2); la clave `__metadata__` de `updates` se ignora; `custom` (lo que emite el wrapper) → `node.progress|log|partial`,
  `token`; `checkpoints` → `checkpoint`; `updates` con `__interrupt__` → `interrupt`.
- **Serialización:** `json.dumps(obj, ensure_ascii=False, default=_enc)`; `_enc`: `Interrupt` → `{id, value,
  response_schema}`, `Send` → `{node, arg}`, `BaseMessage` → `model_dump()`, `Path` → `str`, resto → `str`.
- **Volumen:** escritura en `events` por lotes cada 250 ms; `node.progress` y `node.partial` se agregan (último valor por
  nodo cada 200 ms) y **no** se guardan en `events` (solo se emiten en vivo); `node.log` sí se guarda. Cola acotada por
  suscriptor: si se llena, se descartan progresos, nunca cambios de status.

## 7. GUI

Página única: `static/index.html` + `app.js` + `app.css`, JS sin framework ni build, CodeMirror vendorizado. Tema oscuro
por defecto y claro opcional (variables CSS). Diseñada para 1440×900 y 1024×768. Rutas por hash (`#/inicio`,
`#/estudio/<slug>?hilo=<id>`, …). Todos los textos en español.

**Menú lateral izquierdo plegable** (ancho completo ↔ carril de iconos; misma animación que el panel derecho; estado en
`localStorage`): Inicio · Estudio · Hilos · Salidas · Proyectos (*D9: «Flujos»*) · Módulos · Configuración **[F1]** · Agentes (asistentes) · Memoria ·
Trazas **[F2]** · Datasets · Programaciones **[F3]**. Al pie: ejecución activa (status + barra) y VRAM usada.

**Inicio** *(D8)*: 1) **Proyectos** (flujos, los 12 más recientes; «Ver todos») con «+ Nuevo proyecto» (desde
plantilla); 2) **Agentes** (asistentes de todos los proyectos, `GET /assistants`) con «+ Nuevo agente» (proyecto +
nombre → vista Agentes para editar contexto y ajustes); clic en un agente abre el Estudio con ese asistente
seleccionado; 3) estado del sistema (GPU, VRAM, Ollama, ejecuciones activas). Elegir la plantilla «Clip de personaje»
en «Nuevo proyecto» abre su diálogo guiado (nombre del proyecto, personaje, origen del texto, texto o tema, retrato,
voz, rótulos, «iniciar al crear») → `/quick-video` y navegar al Estudio.

**Estudio [F1]** (`#/estudio/<slug>`):
- Barra superior: nombre del proyecto editable · selector de hilo (+ hilo nuevo) · [F2] selector de asistente · Iniciar
  (abre formulario de entrada generado desde los canales `input: true` + editor JSON) · Paso a paso · Pausar · Continuar
  · Detener · barra de progreso general (suma de `weight × progreso`; 100 % al terminar) · Validar · Exportar JSON ·
  Deshacer/Rehacer · [F2] Grafo/Chat.
- **Lienzo [F1, completo — D4]:** zoom con rueda, desplazamiento arrastrando el fondo, rejilla con ajuste
  (`settings.snap`), minimapa, «ajustar a la vista», auto-ordenar por capas; nodos arrastrables con puerto de entrada
  (izquierda) y salida (derecha); crear arista arrastrando de salida a entrada (join: soltar varias aristas en el mismo
  destino y marcar «esperar a todas» en el panel de la arista); clic en arista → panel con condición, etiqueta y orden;
  selección simple y múltiple (Shift+clic, recuadro), mover en grupo, Supr para borrar (con confirmación si son nodos),
  copiar/pegar (Ctrl+C/V, ids nuevos), deshacer/rehacer (Ctrl+Z/Y, pila de 50 cambios de proyecto); «+ Añadir módulo»:
  paleta con búsqueda y categorías, clic o arrastrar al lienzo; nodos `__start__` y `__end__` siempre visibles. Guardado
  automático (debounce 800 ms) con control de `version`; si hay 409 se avisa y se recarga.
- Colores de nodo: gris = pendiente · naranja (pulso) = ejecutando · naranja discontinuo = pausando/esperando/esperando
  GPU · verde = correcto · rojo = error · azul = breakpoint (◐ antes, ◑ después) · insignia ×N = veces que el nodo se ha
  ejecutado en esta ejecución · ⚡ = de caché [F2]. Aristas: verde = recorrida, naranja animada = activa, etiqueta con la
  condición. Barra de progreso dentro de cada nodo.
- **Panel derecho del nodo** (plegable, pestañas): **Configuración** (nombre, formulario CONFIG, políticas
  retry/timeout, breakpoints antes/después, [F2] caché/defer/error_to; botones Guardar, Valores por defecto, Continuar
  desde aquí si está en pausa) · **Proceso** (log en vivo, intentos, error con traceback) · **Estado** (entrada/salida del
  nodo en el checkpoint seleccionado; editable cuando no se ejecuta) · **Código** (CodeMirror del `.py`; Ctrl+S guarda con
  validación y muestra la línea del error; aviso «afecta a N nodos en M proyectos»; botón «Duplicar como módulo nuevo» que
  copia el `.py` y cambia este nodo al nuevo) · **Prompt [F3]** (playground para módulos con `META.llm`).
- **Panel inferior** (plegable y redimensionable), pestañas: **Salida** (vídeo/audio/imagen/texto según el nodo `output`;
  transcripción a la derecha del vídeo) · **Historia** (árbol de checkpoints; seleccionar → estado y next; Reproducir desde
  aquí; Editar y bifurcar) · **Estado** (JSON del estado del hilo, editable si no hay ejecución) · **Interrupts**
  (formularios pendientes desde `response_schema`; se abre solo si `abrir_interrupts`) · **Eventos** (flujo crudo,
  filtrable por tipo).
- **Modo Chat [F2]:** para grafos con clave `messages`: conversación con tokens en streaming, herramientas plegables,
  interrupts dentro del chat.

**Hilos [F1]:** lista por proyecto (título, status calculado, fecha, salida), abrir en Estudio, renombrar, copiar, borrar.
**Salidas** *(D8; antes «Vídeos»)*: todas las salidas de `/outputs`, filtrables por tipo (vídeo, audio, imagen, texto;
los vídeos de v1 con etiqueta), reproductor de vídeo con transcripción, descargar, abrir hilo, borrar. `#/videos` sigue
funcionando (= Salidas filtrado a vídeo).
**Proyectos [F1]** *(D9: rotulado «Flujos», `#/flujos`)*: tarjetas; nuevo (plantilla), duplicar, importar/exportar JSON, borrar.
**Módulos [F1]:** biblioteca por categoría (icono, nombre, archivo, entradas→salidas, exec, gpu, usos, error); editor a
pantalla completa; nuevo módulo (esqueleto documentado); duplicar; borrar (si no se usa); historial con diff y restaurar.
**Configuración [F1]:** formulario de `settings.json` agrupado — General (nombre, tema, abrir interrupts, sonido,
notificación (solo localhost/HTTPS), rejilla) · Ejecución (recursion_limit, max_concurrency, durabilidad async/sync,
timeout por defecto, estrategia double-texting [F2 para las no-reject], hilos a conservar, intérpretes) · Modelos (URL de
Ollama, modelo por defecto, keep_alive, modelo de embeddings) · Vídeo (aviso, color, voz, carpeta de retratos) ·
Almacenamiento (tamaños, purgar) · Seguridad (token, host/puerto — requieren reiniciar) · Sistema (solo lectura).
**Asistentes [F2]**, **Memoria [F2]** (namespaces, buscar, ver/editar/borrar; búsqueda semántica con
`index={"dims": 768, "embed": fn}` donde `fn` llama a `/api/embed` de Ollama), **Trazas [F2]** (lista de ejecuciones y
cascada por nodo desde `events`), **Datasets [F3]** (ejemplos, «añadir desde esta ejecución», experimentos con
evaluadores — expresión Python o LLM-juez — y tabla comparativa), **Programaciones [F3]**.

## 8. Módulos

### 8.1 F1

| Archivo | kind / exec / gpu | CONFIG (key: tipo = defecto) | inputs → outputs | Comportamiento |
|---|---|---|---|---|
| `entrada.py` | input / local / — | `valores: json = {}` | — → claves de `valores` | Devuelve las claves de `valores` que **no** estén ya en el estado (gana la entrada de la ejecución) |
| `salida.py` | output / local / — | `mostrar: json = [{"clave":"texto","como":"texto"}]` (`como` ∈ texto/video/audio/imagen) | claves de `mostrar` → — | Devuelve None; el motor registra `outputs` |
| `llm_ollama.py` | task / local / shared, `llm: True` | `modelo: model = ""` (setting `ollama_model`) · `url: text = ""` (setting `ollama_url`) · `sistema: textarea = ""` · `prompt: textarea` · `campo_salida: state_key = texto` · `formato: select[texto,json] = texto` · `claves_json: keys = ""` · `temperatura: number = 0.7` · `num_predict: number = 400` · `timeout: number = 300` | marcadores del prompt → `campo_salida` o `claves_json` | `POST /api/generate` (stream) con `keep_alive` de ajustes; tokens con `ctx.token`; texto: quita comillas; json: `format: "json"`, `json.loads`; si `claves_json` no está vacío vuelca esas claves al estado, si no guarda el objeto en `campo_salida` |
| `revision_humana.py` | interrupt / local / — | `campo: state_key = texto` · `mensaje: text = "Revisa el guion"` · `campo_comentario: state_key = comentario_revision` | `campo` → `campo`, `aprobado`, `campo_comentario`, `rechazos` | 1.º `ctx.interrupt({"campo", "valor": s[campo], "mensaje"}, schema)` con `schema = {"type":"object","properties":{"aprobado":{"type":"boolean"},"valor":{"type":"string"},"comentario":{"type":"string"}},"required":["aprobado"]}`. Aprobado → `{campo: valor or s[campo], "aprobado": True}`. Rechazado → `{"aprobado": False, campo_comentario: comentario, "rechazos": s.get("rechazos",0)+1}` |
| `codigo_python.py` | task / proceso (intérprete estudio) / — | `codigo: code = 'return {"resultado": len(state.get("texto") or "")}'` · `salidas: keys = resultado` · `entradas: keys = texto` | `entradas` → `salidas` | Envuelve el código en `def _f(state, ctx):` y lo ejecuta |
| `tts_kokoro.py` | task / proceso pesado / shared | `campo_texto: state_key = texto` · `voz: select = ""` (setting `video.voz`) · `velocidad: number = 0.95` | `campo_texto` → `audio`, `duracion`, `voz_usada` | Código de `nodes.voz`. *(rev. 2.1: la salida se llama `voz_usada` porque `voz` es el id del nodo en `clip_personaje` y §3.2 prohíbe que coincidan)* |
| `retrato_sdxl.py` | task / proceso pesado / exclusive | `modo: select[existente,sdxl] = existente` · `imagen: path = cleo_0.png` · `prompt: textarea` · `negativo: textarea` · `semilla: number = 7` · `pasos: number = 30` · `guidance: number = 6.5` | — → `imagen` | Código de `nodes.retrato` |
| `lipsync_sadtalker.py` | task / proceso pesado / exclusive | `tam: select[256,512] = 512` (→ int) · `mejora: checkbox = true` · `quieto: checkbox = true` · `expresion: number = 1.1` | `audio`, `imagen` → `video_lipsync` | Código de `nodes.lipsync` con progreso por fases (landmark Det, 3DMM Extraction, mel, audio2exp, Face Renderer, seamlessClone, Face Enhancer; pesos 3/5/1/2/40/15/34) |
| `montaje_video.py` | task / proceso pesado / — | `nombre: text = ""` · `subtitulo: text = ""` · `aviso: text = ""` (setting `video.aviso`) · `color: text = ""` (setting `video.color`) · `crf: number = 18` | `video_lipsync`, `duracion`, `nombre`, `subtitulo` → `video_final` | Código de `nodes.montaje`. **`nombre` y `subtitulo` se leen del estado si existen; si no, de `cfg`** |
| `prueba_lenta.py` | task / proceso (intérprete estudio) / — | `segundos: number = 10` · `fallar: checkbox = false` · `clave_salida: state_key = prueba` | — → `clave_salida` | Duerme emitiendo `@@P` cada 0,1 s; para tests sin GPU |

### 8.2 F2 y F3

F2: `plantilla_texto.py`, `http_peticion.py` (timeout obligatorio), `rss_noticias.py`, `memoria.py` (Store),
`mapa_send.py` (fan-out con `Send`), `router_llm.py` (kind router), `subgrafo.py` (kind subgraph), `chat_ollama.py`
(ChatOllama sobre `messages`), `agente_react.py` (kind agent: `ToolNode` + `tools_condition`; **no** usar
`create_react_agent`, que está deprecado), herramientas = funciones `@tool` en `modules/tools/*.py` (kind tools).
F3: `mcp_herramientas.py` (`langchain-mcp-adapters`).

## 9. Plantillas (`studio/templates/<id>.json`)

Mismo esquema que `project.json` sin `slug/created/updated/version`. Las de F1 están **literalmente** en el Anexo B.

| id | Fase | Descripción |
|---|---|---|
| `clip_personaje` | F1 | Clip de un personaje (pipeline v1 + revisión con rechazo y bucle, D5) |
| `escritor_critico` | F1 | Escritor ↔ crítico (JSON puntuación/crítica) con bucle y límite de iteraciones |
| `vacio` | F1 | `__start__ → entrada → salida → __end__` |
| `react_chat` | F2 | Agente ReAct con herramientas y memoria, modo Chat |
| `resumen_rss` | F2 | RSS → resumen LLM → revisión → voz → salida (audio) |
| `debate` | F3 | Moderador + 2 personajes por turnos, un clip por intervención con `Send`, montaje concatenado |

## 10. Matriz de funcionalidades

| Funcionalidad (LangGraph / Studio / Platform) | Cómo | Fase |
|---|---|---|
| Visualizar y **editar** el grafo | Lienzo propio | F1 |
| Ejecutar con entrada | Formulario desde canales `input` + JSON | F1 |
| Streaming de actualizaciones, progreso y tokens | `updates/custom/tasks/checkpoints` + SSE | F1 |
| Hilos: crear, listar, cambiar, copiar, borrar | Checkpointer + `threads` + SQL propio | F1 |
| Breakpoints antes/después, paso a paso | `interrupt_before/after` por ejecución | F1 |
| Human-in-the-loop con formularios tipados | `interrupt(response_schema)` + `Command(resume)` | F1 |
| Editar estado | `aupdate_state(as_node)` | F1 |
| Time travel: historia, reproducir, bifurcar | `aget_state_history`, `checkpoint_id`, `aupdate_state` | F1 |
| Pausa cooperativa / detener / continuar | `RunControl`, cancelación, `astream(None)` | F1 |
| Reintentos, timeouts | `RetryPolicy`, `TimeoutPolicy` (+ heartbeat) | F1 |
| Condicionales, joins, ciclos, recursion_limit | §3.4 | F1 |
| Editor de código de módulos con recarga | Instantáneas por sha | F1 |
| Re-ejecutar un nodo | Reproducir desde checkpoint | F2 |
| Asistentes versionados | `runtime.context` + tablas | F2 |
| Memoria a largo plazo, búsqueda semántica | `AsyncSqliteStore` + embeddings de Ollama | F2 |
| Caché de nodos | `CachePolicy` con `key_func` propio (slug, nodo, sha del módulo, cfg efectiva, inputs; comprobar que los ficheros existen al acertar) | F2 |
| Manejador de errores, nodos diferidos | `error_handler`, `defer` | F2 |
| Map-reduce, `Command`, routers, subgrafos (xray) | `Send`, `Command`, §4 | F2 |
| Modo Chat, stream `messages` | ChatOllama | F2 |
| Double-texting (enqueue/interrupt/rollback) | §5.7 | F2 |
| Trazas (sustituye a LangSmith) | `events` | F2 |
| Datasets y experimentos | Tablas propias | F3 |
| Programaciones (cron) | croniter | F3 |
| Playground de prompts | Pestaña Prompt | F3 |
| Exportar `.py` / `langgraph.json` | §4 | F3 |
| API funcional (`@entrypoint/@task`) | Exportación alternativa | F3 |
| MCP | `langchain-mcp-adapters` | F3 |

## 11. Seguridad

Herramienta local de un solo usuario que **ejecuta código arbitrario** (módulos, condiciones, `codigo_python`). Escucha en
`0.0.0.0` (D2) con **token obligatorio**; la GUI muestra un aviso permanente si `token` es corto (<16). `/api/media`
restringido a `BASE` y a extensiones de medios (resolver la ruta y comprobar `is_relative_to(BASE)`); slugs, ids y
nombres de archivo validados por regex; nunca servir `settings.json`, `data/` ni `.sqlite` como estáticos; `settings`
enviados a los nodos sin `token`.

## 12. Migración desde v1 (paso 1i)

1. Parar v1 (§1). 2. Mover a `legacy/`: `nodes.py`, `config.json`, `server.py` (v1), `static/index.html` (v1) y `runs/`
→ `legacy/runs/`. 3. En los `state.json` movidos, reescribir el prefijo `/opt/historico/demo/studio/runs/` →
`/opt/historico/demo/studio/legacy/runs/`. 4. `GET /outputs?kind=video` incluye `legacy/runs/*/final.mp4` (hoy existen en
`20261118-101057` y `20261118-101823`) con `legacy: true` y el texto de su `state.json` (D7). 5. Crear el proyecto
«Clip · Cleopatra» desde `clip_personaje` trasladando `config.json` así: `entrada.texto/tema/personaje/modo` →
`entrada.config.valores` (`modo` → `origen_texto`); `guion.*` → `guion.config` (`plantilla` → `prompt`, `palabras` →
`valores.palabras`); `revision.activo` se ignora (ahora depende de `origen_texto`, D5); `lipsync.tam` cadena → número;
`montaje.nombre/subtitulo` → `valores.nombre/subtitulo`; `breakpoint` → `breakpoint.before`. 6. `arrancar.sh` en 8790.

## 13. Fases, pasos y criterios de aceptación

**F1** (entrega = editor visual completo, D4), en pasos ordenados; cada paso termina con sus tests en verde:

| Paso | Contenido | Tests |
|---|---|---|
| 1a | Spike de LangGraph en `tests/test_spike_lg.py`: drain con un interrupt en el superstep (esperado `interrupted`); cancelar `astream` con una tarea paralela ya terminada y continuar (esperado: no se repite); `astream(input)` sobre un hilo con `next` pendiente; `as_node="__copy__"`; `interrupt(response_schema)` aparece en `aget_state().interrupts`; `TimeoutPolicy(idle_timeout)` + `heartbeat`; columnas de `checkpoints`/`writes` para copiar hilo. Si algún resultado difiere, **actualizar este documento** antes de seguir | spike |
| 1b | `modules.py` (AST, validación, instantáneas) + cambios de `runner.py` + `prueba_lenta.py`, `entrada.py`, `salida.py`, `codigo_python.py` | unit |
| 1c | `compiler.py` (estado §3.3, aristas §3.4, validación, wrappers) | unit (grafos de prueba sin GPU) |
| 1d | `db.py` + `engine.py` (ejecuciones, eventos, pausa/parada/continuar, semáforo GPU, reconciliación) | integración con `prueba_lenta` |
| 1e | API REST + SSE (§6) + token | integración HTTP en puerto 8799 |
| 1f | Módulos de vídeo + `llm_ollama` + `revision_humana` + plantillas F1; medir tiempos reales y anotarlos aquí | `gpu` |
| 1g | GUI: shell (menú plegable, rutas), Inicio + Crear vídeo, Proyectos, Módulos (editor), Hilos, Vídeos, Configuración | `ui` |
| 1h | GUI Estudio: lienzo completo, panel de nodo (incl. Código), panel inferior (Salida, Historia, Estado, Interrupts, Eventos) | `ui` |
| 1i | Migración §12, `arrancar.sh`, v2 pasa a 8790 | todos |

**Criterios de aceptación F1:**

1. `clip_personaje` con `origen_texto="texto"` produce `video_final` (ffprobe sale con 0, duración > 1 s, códec h264) y se
   reproduce en la GUI. Con `origen_texto="tema"`, la revisión bloquea hasta responder; el texto editado llega a la voz;
   rechazar vuelve al guion con el comentario; al tercer rechazo la ejecución termina sin vídeo.
2. Pausar durante la voz deja la ejecución `drained` (tras terminar el superstep); Continuar termina sin repetir nodos
   completados (verificable con `prueba_lenta` y con el contador ×N).
3. Detener durante el lip-sync: a los 3 s `pgrep -f SadTalker/inference.py` vacío y VRAM ≤ max(300 MiB, base + 50) con
   `ollama ps` vacío; el hilo se puede Continuar desde el último checkpoint. (Sin GPU: mismo test con `prueba_lenta`.)
4. Breakpoint antes de Montaje: se para; editar `nombre` en el estado; Continuar → el vídeo lleva el rótulo nuevo.
5. Time travel: en un hilo terminado, bifurcar desde el checkpoint previo a la voz con otro texto → nueva rama en el
   árbol; los ficheros y el estado de la rama original quedan intactos.
6. `escritor_critico` recorre el bucle ≥ 2 veces y termina por la condición; con `recursion_limit=5` la ejecución termina
   en `error` con `GraphRecursionError` visible en la GUI.
7. Guardar un módulo con un error de sintaxis desde la GUI → 422 con la línea; corregido → la siguiente ejecución usa el
   código nuevo sin reiniciar.
8. Mover, añadir, conectar, borrar, copiar/pegar y deshacer nodos persiste tras recargar; exportar e importar un proyecto
   da un JSON igual (ignorando `slug, created, updated, version`).
9. Playwright: sin `console.error` ni `pageerror` en todas las rutas F1, a 1440×900 y 1024×768, sin scroll horizontal.
10. Reiniciar el servidor no pierde hilos, historia, proyectos ni ajustes; una ejecución `running` pasa a `stopped`.
11. Sin token → 401 en `/api/*`; con token en la cookie, todo funciona.

**F2 y F3:** los criterios se escriben (en este documento) **antes** de empezar la fase, uno por fila de la §10 (F2 en §13.3, F3 en §13.5).

### 13.3 F2: criterios de aceptación y pasos *(rev. 2.2, aprobado el 2026-11-18)*

**Criterios de aceptación F2** (uno por fila F2 de la §10; contratos en §6.3). «Test» indica cómo se verifica:
`not gpu` = suite por defecto; `llm` = marcador nuevo que necesita Ollama con qwen2.5:7b (excluido por defecto como
`gpu`); `ui` = Playwright.

| # | Fila §10 | Criterio | Test |
|---|---|---|---|
| A1 | Re-ejecutar un nodo | En un hilo terminado de un grafo `a → b → c` (`prueba_lenta`), `POST /threads/{id}/nodes/b/rerun` crea una ejecución `replay` desde el checkpoint cuyo `next = ["b"]`: se ejecutan `b` y `c` (×1 cada uno) y **no** `a`; la cabeza del hilo cuelga de ese checkpoint (rama nueva en la historia) y la rama original sigue intacta. Un nodo que no está en el `next` de ningún checkpoint de la rama actual → 409 `not_rerunnable`. En la GUI, botón «Re-ejecutar» en el panel del nodo | `not gpu` + `ui` |
| A2 | Asistentes versionados | Crear asistente con `node_overrides = {n: {clave_salida: "x"}}` y `context = {tono: "serio"}`; ejecutar con él → la salida va a `x` y el módulo ve `ctx.context.tono` (local y proceso). `PATCH` con otro override → versión 2 activa; la versión 1 no cambia (`GET /versions`); `activate/1` → la siguiente ejecución vuelve a usar `x`. `runs` guarda `assistant_id` y `assistant_version`. Override con nodo o campo inexistente, o `context` con clave fuera de `context_schema` → 400. Vista Asistentes (lista, crear, versiones, activar, diff de versiones) y selector de asistente en la barra del Estudio | `not gpu` + `ui` |
| A3 | Memoria a largo plazo, búsqueda semántica | `memoria.py` (local) guarda en el hilo 1 dos recuerdos en `["usuarios","{usuario}"]`; en el hilo 2 (otro `thread_id`) `buscar` con «¿qué comida le gusta?» devuelve primero el recuerdo sobre comida. Los items sobreviven a reiniciar el servidor. Un módulo en proceso con `store_reads` lee lo precargado y su `__store_put__` queda escrito. `PUT/GET/DELETE /store/items` y `GET /store/namespaces` cumplen §6.3; con Ollama caído, la búsqueda → 502 `ollama` y la lectura por clave sigue funcionando. Vista Memoria (árbol de namespaces, búsqueda, ver/editar/borrar) | `not gpu` (CRUD) + `llm` (semántica: usa `nomic-embed-text`) + `ui` |
| A4 | Caché de nodos | Nodo `prueba_lenta(segundos=3)` con `cache: {ttl: 60}`: la 2.ª ejecución en **otro hilo** con la misma entrada termina ese nodo en < 0,5 s, `node.end.status = "cached"`, no se lanza subproceso y la GUI muestra ⚡. Es fallo de caché (se ejecuta) si cambia: la `cfg`, una entrada, el código del módulo, el asistente que lo sobrescribe, o ha vencido el `ttl`, o se borró un fichero de su salida. `DELETE /projects/{slug}/cache` lo vacía | `not gpu` + `ui` |
| A5 | Manejador de errores, nodos diferidos | Nodo `prueba_lenta(fallar=true)` con `retry.max_attempts=2` y `error_to="recuperar"`: falla 2 veces, la ejecución sigue por `recuperar`, que ve `error = {node, msg}`, y termina `success`; el lienzo no muestra `__error_handler__*`, el nodo que falló queda rojo y `recuperar` verde. `error_to` inexistente o igual al propio nodo → error de validación. **Defer:** `START → a (0,2 s) → c (defer)` y `START → b (2 s) → c`, con aristas simples: `c` se ejecuta **una vez** y su `node.start` es posterior al `node.end` de `b` | `not gpu` |
| A6 | Map-reduce, `Command`, routers, subgrafos (xray) | **Send:** `mapa_send` sobre una lista de 3 elementos lanza el destino 3 veces en paralelo (×3, 3 carpetas de tarea distintas) y la clave `append` acaba con 3 elementos; Detener en mitad + Continuar no repite las terminadas. **Command:** un módulo local que devuelve `Command(goto=…, update=…)` va al destino y aplica la actualización; un `goto` fuera de sus aristas salientes → error del nodo con la lista válida. **Router:** un `kind: router` que devuelve un id (o lista) sigue esas aristas; id no válido → error con la lista; `router_llm` clasifica 3 frases de prueba en su rama correcta. **Subgrafo:** proyecto padre con `subgrafo(proyecto=hijo)`: los eventos del hijo llegan con `ns ≠ ""`, el `interrupt` de una `revision_humana` dentro del hijo para al padre y Responder lo reanuda; ciclo padre→hijo→padre y claves compartidas `append`/`sum` → error de validación; `mermaid?xray=1` y el interruptor «xray» del lienzo muestran los nodos del hijo con sus colores | `not gpu` (Send, Command, router fijo, subgrafo) + `llm` (`router_llm`) + `ui` (xray) |
| A7 | Modo Chat, stream `messages` | Plantilla `react_chat` (`agente_react` + herramientas `calculadora` y `recordar`/`consultar_memoria` + clave `messages` con reducer `messages`): «¿cuánto es 37×91?» → aparece la llamada a `calculadora` (plegable) y la respuesta contiene 3367; llegan ≥ 5 eventos `token` antes del `node.end` del agente. «Recuerda que me llamo Ana» en el hilo 1 → en el hilo 2 «¿cómo me llamo?» responde Ana (Store). Una herramienta que pide confirmación (`interrupt`) muestra el formulario dentro del chat y la respuesta lo reanuda. El conmutador Grafo/Chat solo aparece si el proyecto tiene `messages`. `chat_ollama` sin herramientas responde y emite tokens | `llm` + `ui` (con un modelo falso en `not gpu` para el dibujo del chat) |
| A8 | Double-texting | Con una ejecución de `prueba_lenta(5 s)` en marcha: **enqueue** ×2 → 202 `queued` con `position` 1 y 2; se ejecutan en orden FIFO al terminar la actual, cada una sobre el estado de la anterior; `stop` sobre una en cola la quita (`stopped`) sin lanzarla; las de la cola sobreviven a reiniciar el servidor. **interrupt** → la actual pasa a `stopped` (`motivo: interrupt`) en < 3 s y la nueva arranca desde el último checkpoint (las escrituras terminadas no se pierden). **rollback** → la actual `stopped` (`motivo: rollback`), la nueva parte de `checkpoint_id_inicio` de la actual y su estado **no** contiene nada escrito por la abandonada; `history` la oculta y `?rolled_back=1` la muestra; no se borra ningún checkpoint (si la abandonada empezó en un hilo vacío, sus checkpoints se mueven a un hilo oculto `rb-<run_id>` con `metadata.oculto = true`). **reject** sigue dando 409 | `not gpu` |
| A9 | Trazas | `GET /runs/{id}/trace` de una ejecución con ramas paralelas, un reintento, una espera de GPU (`prueba_lenta` con `gpu: exclusive` en un módulo de prueba) y un nodo cacheado devuelve un span por intento, los paralelos se solapan en el tiempo, `t_gpu` > `t0` en el que esperó, `cached` en el cacheado y `totals.duration` = `t1 − t0` de la ejecución (± 0,5 s). Vista Trazas: lista filtrable (proyecto, status, fecha) y cascada por nodo (barra gris = espera GPU, color = status, ⚡, reintentos), clic en un span → log del nodo | `not gpu` + `ui` |

Criterios transversales F2:

- **T1 (módulos y plantillas F2, §8.2 y §9):** `plantilla_texto`, `http_peticion` (timeout obligatorio: sin él, error de
  validación), `rss_noticias` pasan sus tests unitarios con un servidor HTTP local de prueba; la plantilla
  `resumen_rss` corre de punta a punta contra un RSS local y produce audio (`gpu`).
- **T2 (no regresión):** los 72 tests `not gpu` y los 4 `gpu` de F1 siguen en verde; `-m llm` en verde una vez.
- **T3 (GUI):** criterio 9 de F1 ampliado a las rutas F2 (Asistentes, Memoria, Trazas, Estudio en modo Chat y con xray).
- **No se hace en F2** (se queda opcional, sin criterio): protocolo por fd 3 (§3.1.1) y reutilización por contenido
  (§3.8).

**Resultados del spike 2a** (`tests/test_spike_f2.py`, 10 pruebas en verde, 2 de ellas `llm`):

| Pregunta | Resultado verificado | Consecuencia |
|---|---|---|
| Acierto de `CachePolicy` | El nodo **no se llama**; `updates` trae `{nodo: valor, "__metadata__": {"cached": True}}`; `cache.aclear()` vacía | El motor emite `node.start`+`node.end {status: cached}` a partir de `updates` (A4) |
| `defer=True` | Se ejecuta una vez, cuando no quedan otras tareas (después de la rama larga) | Ninguna |
| `Send` | Cada rama tiene su `execution_info.task_id`; un **nodo** puede devolver `Command(goto=[Send…])` con `destinations` | `mapa_send` es un nodo local que devuelve `Command(goto=[Send…])` |
| Subgrafo compilado con `checkpointer=None` | Hereda el del padre; `interrupt` dentro → `next=("sub",)` en el padre, se reanuda por id; `ns = ("sub:<task>",)`; `get_graph(xray=1)` dibuja los nodos internos | Ninguna |
| `error_handler` | Se llama tras agotar reintentos; `updates` lo muestra como `__error_handler__<nodo>`; **no pasa por el wrapper** | El motor convierte ese `updates` en `node.end {handler: true}` del nodo que falló |
| `AsyncSqliteStore` + índice | `asearch(query)` ordena por similitud con `score`; `index=False` guarda sin embedding; con `nomic-embed-text` real (768) acierta | Ninguna |
| Stream `messages` | Un LLM llamado dentro de un nodo async **sin pasar `config`** emite sus fragmentos con `metadata.langgraph_node` (contextvars, 3.12); `ChatOllama` + `ToolNode` + `tools_condition` con qwen2.5:7b resuelve 37×91 | Ninguna |

**Pasos F2** (cada uno termina con sus tests en verde y la suite de F1 intacta):

| Paso | Contenido | Tests |
|---|---|---|
| 2a | `langchain-ollama`; spike `tests/test_spike_f2.py`: cómo aparece en el stream un acierto de `CachePolicy`; `defer`; `Send` con el wrapper (task_id distinto por rama); subgrafo compilado con `checkpointer=None` + `interrupt` dentro; `AsyncSqliteStore` con índice y embeddings de Ollama; stream `messages` con `ChatOllama` llamado desde el wrapper; `error_handler` con `Command(goto)`. Si algo difiere, actualizar §6.3/§13.3 antes de seguir | spike |
| 2b | Migración de `studio.sqlite` a `user_version = 2` (tablas y columnas F2); asistentes (API, `context_schema`, overrides, `runtime.context`) | A2 |
| 2c | Double-texting (cola, interrupt, rollback, reconciliación de la cola) + re-ejecutar nodo | A8, A1 |
| 2d | Compilador: caché (`data/cache/<slug>.sqlite`), `error_to`, `defer` | A4, A5 |
| 2e | `Send` (`mapa_send`), `Command`, `kind: router` (`router_llm`), `kind: subgraph` (`subgrafo`), `mermaid?xray=1` | A6 |
| 2f | Store: lifespan, API, `memoria.py`, `store_reads`/`__store_put__` en `runner.py` | A3 |
| 2g | Mensajes: reducer `messages`, stream `messages` → `token`, `chat_ollama`, `agente_react` + `modules/tools/`, `/tools`, `/threads/{id}/messages`, plantilla `react_chat` | A7 |
| 2h | Trazas (`/runs/{id}/trace`, filtros de `/runs`) + `plantilla_texto`, `http_peticion`, `rss_noticias`, plantilla `resumen_rss` | A9, T1 |
| 2i | GUI F2: vistas Asistentes, Memoria, Trazas; Estudio: selector de asistente, Grafo/Chat, xray, ⚡, Re-ejecutar, campos caché/defer/error_to, estrategia en Configuración | `ui`, T3 |
| 2j | Regresión completa (`not gpu`, `gpu`, `llm`) y estado de F2 en §13.4 | T2 |

### 13.2 Estado de F1 y notas de implementación (2026-11-18)

F1 **terminada**: 72 tests `not gpu` (spike 10, unit 40, integración HTTP 8, motor 9, ui 5) y 4 tests `gpu` en verde;
v2 en 8790 (`arrancar.sh`), v1 en `legacy/`.

Tiempos reales medidos (RTX 4060, clip de ~10 palabras, retrato existente, desde el estudio):

| Nodo | Tiempo | Nota |
|---|---|---|
| Voz (Kokoro) | ≈ 13 s | incluye cargar el modelo |
| Retrato (existente) | ≈ 13 s | casi todo es esperar la GPU: es `exclusive` y la voz (`shared`) va en paralelo |
| Lip-sync 256, sin GFPGAN | ≈ 40 s | |
| Lip-sync 512 con GFPGAN | 2–4 min | la fase Face Enhancer domina |
| Montaje | ≈ 2,5 s | |
| Guion (qwen2.5:7b, 60–200 tokens) | 5–15 s | + carga del modelo la primera vez |
| Clip completo 256 sin mejora (texto fijo) | ≈ 70 s | |

Desviaciones y añadidos respecto al texto anterior (todas menores):
- Endpoints extra de solo lectura: `GET /threads/{id}/events?limit=&run=&types=` (reconstruir logs y la pestaña Eventos al
  recargar) y `GET /runs/active` (pie del menú).
- La GUI se separó desde el principio en `app.js` + `views/{inicio,listas,modulos,estudio}.js` (§14).
- `POST /threads/{id}/runs` con `checkpoint_id` decide `kind` = `fork` si ese checkpoint viene de `aupdate_state`
  (`source` = `update`/`fork`) y `replay` en otro caso.
- El proyecto migrado «Clip · Cleopatra» añade al prompt de v1 las dos líneas de «Versión anterior» y «Comentario del
  revisor» para que el bucle de rechazo (D5) funcione.
- `runner.py` antepone un espacio a las líneas de log que empiezan por `@@` (no corrompen el protocolo).
- Al apagar, uvicorn espera como máximo 8 s a las conexiones SSE abiertas.

### 13.4 Estado de F2 y notas de implementación (2026-11-18)

F2 **terminada**: criterios A1–A9 y T1–T3 en verde. Tests nuevos: spike F2 10 (2 `llm`), `test_f2_api.py` 18,
`test_f2_modulos.py` 3, `test_f2_ui.py` 2, `test_f2_llm.py` 4 (`llm`), `test_f2_gpu.py` 1 (`gpu`). Suite por defecto:
103 (72 de F1 + 31 de F2). `pytest.ini` excluye por defecto `gpu` y `llm`; se lanzan con `-m gpu` y `-m llm`.

| Paso | Hecho |
|---|---|
| 2a | `langchain-ollama` 1.1.0; spike con 10 pruebas (resultados arriba) |
| 2b | Migración `user_version = 2`; asistentes (API §6.3, `context_schema`, overrides, `astream(context=…)`) |
| 2c | Double-texting (`enqueue` con cola persistente, `interrupt`, `rollback`) y re-ejecutar nodo |
| 2d | Caché por proyecto (`EstudioCache`), `error_to`, `defer` |
| 2e | `mapa_send`, `Command`, `kind: router` (`router_llm`), `kind: subgraph` (`subgrafo`), `mermaid?xray=1` |
| 2f | Store con índice (`nomic-embed-text`, 768), API `/store`, `memoria.py`, Store en subprocesos |
| 2g | Reducer `messages`, stream `messages` → `token`, `chat_ollama`, `agente_react` + `modules/tools/` (calculadora, recordar, consultar_memoria, pedir_confirmacion), plantilla `react_chat` |
| 2h | Trazas, `plantilla_texto`, `http_peticion`, `rss_noticias`, plantilla `resumen_rss` |
| 2i | GUI: vistas Asistentes, Memoria y Trazas; Estudio con selector de asistente, modo Chat, xray, ⚡, re-ejecutar, caché/diferido/error_to y estrategia de double-texting al iniciar con el hilo ocupado |

Desviaciones y decisiones de implementación (todas menores):
- **Asistentes:** los `node_overrides` se aplican **al compilar** (se compila en cada ejecución, §4.7) y también cuentan
  para declarar claves (`outputs_from_config`); el `context` llega además por `astream(context=…)`. Sin asistente, el
  contexto efectivo son los `default` del `context_schema`.
- **Subgrafos y agentes** no se añaden como nodo-grafo directo: un wrapper invoca `hijo.ainvoke(..., config)` (hereda el
  checkpointer, admite `interrupt` y `get_graph(xray=1)` lo dibuja), y emite `node.start`/`node.end` del nodo padre.
  Las claves `append`/`sum` del hijo son internas y no pueden coincidir con claves del padre.
- **Agente** (`kind: agent`): subgrafo interno `llm ↔ herramientas` (`ToolNode` + `tools_condition`); las herramientas se
  cargan de `modules/tools/*.py` en cada compilación. Los tokens del LLM interno se atribuyen al nodo agente
  (`data.inner = "llm"`).
- La clave `messages` toma por defecto el reducer `messages` aunque no esté en `state_schema`.
- **Rollback**: el inicio de la ejecución abandonada es su `checkpoint_id` explícito o, si no, `checkpoint_id_inicio`.
- **Cola**: al apagar no se lanzan ejecuciones de la cola; al arrancar se reanudan. Una ejecución en cola tiene
  `status = queued` en `runs` hasta que se lanza (mismo `run_id`).
- **Trazas**: los aciertos de caché tienen un span con duración 0; los manejadores de error no generan span (el nodo
  que falló lo muestra la vista con «error manejado»).
- `node.end` de un acierto de caché lo emite el motor a partir de `updates` solo para nodos con `cache` (las escrituras
  reutilizadas al Continuar se siguen ignorando, §1.2).
- GUI: «Iniciar» queda activo con una ejecución en marcha; el diálogo pide entonces la estrategia (por defecto, la de
  ajustes si no es `reject`). En modo Chat, un mensaje enviado con el hilo ocupado se encola.

### 13.5 F3: criterios de aceptación y pasos *(rev. 2.3, aprobado el 2026-11-18)*

**Criterios de aceptación F3** (uno por fila F3 de la §10; contratos en §6.4). «Test»: `not gpu` = suite por defecto;
`llm` = Ollama real (qwen2.5:7b); `gpu`; `ui` = Playwright. Los servidores MCP de prueba son scripts locales con
`FastMCP` (paquete `mcp`, dependencia de `langchain-mcp-adapters`), sin red.

| # | Fila §10 | Criterio | Test |
|---|---|---|---|
| C1 | Datasets y experimentos | Crear un dataset con 3 ejemplos (`input: {texto}`, `expected: {longitud}`) sobre un proyecto `entrada → codigo_python(longitud = len(texto)) → salida`; un ejemplo se añade con `from-run` desde una ejecución terminada y su `expected` = salida final. Experimento con evaluador `expr` `output.get('longitud') == expected.get('longitud')` → 3 resultados `success`, `resumen = {evaluador: 1.0}`, 3 hilos ocultos con `metadata.experiment`; cambiar el código para que falle un caso y repetir → `resumen < 1.0`; `compare?ids=a,b` alinea los 3 ejemplos y muestra la diferencia. `rescore` con otro evaluador cambia las puntuaciones sin crear ejecuciones nuevas. `stop` a mitad (ejemplos con `prueba_lenta(3 s)`, `concurrency=1`) → `stopped`, sin más ejecuciones lanzadas. Evaluador `expr` con sintaxis errónea → 400; uno que lanza → `score: null` con el error. Export JSONL → import da un dataset con los mismos ejemplos. LLM-juez: con un prompt de «¿es la respuesta un saludo?», puntúa `"hola"` ≥ 0,5 y `"7"` < 0,5; con Ollama caído → `score: null, comentario: "ollama"` | `not gpu` + `llm` (juez) + `ui` |
| C2 | Programaciones (cron) | Cron `* * * * * */2` (cada 2 s, `thread_mode: nuevo`) sobre `prueba_lenta(0,2 s)`: en 7 s hay ≥ 2 ejecuciones con `runs.cron_id`, cada una en un hilo nuevo con `metadata.cron`, y llegan eventos `cron.fired`. `active: false` → no hay más disparos. `thread_mode: fijo` con `prueba_lenta(3 s)` y estrategia `enqueue` → las ejecuciones se encolan en ese hilo y terminan en orden; con `reject` el disparo se salta y `last_error` lo dice. Expresión no válida → 400 `cron`. `POST /crons/{id}/run` lanza ya sin mover `next_run`. Tras reiniciar el servidor el cron sigue (con `next_run` recalculado) y uno vencido mientras estaba parado se dispara **una sola vez**. Proyecto borrado → `last_error` sin tumbar el planificador. Vista Programaciones (lista, crear con vista previa de las 5 próximas fechas, activar/desactivar, disparar ya, ver ejecuciones) | `not gpu` + `ui` |
| C3 | Playground de prompts | Sobre un nodo `llm_ollama` de `escritor_critico`, `POST …/playground` con 2 variantes (temperatura 0 y prompt distinto) y `state` explícito devuelve 2 resultados con `prompt` ya sustituido, `salida` no vacía y `tokens` > 0; no crea hilos, ejecuciones, eventos ni checkpoints. Sin `state`, toma los valores del hilo indicado. Con asistente, su override se aplica antes de la variante. `router_llm` devuelve el destino elegido como `salida`. Nodo no LLM (`prueba_lenta`) o `agente_react` → 400 `unsupported`. Ollama caído → 502. **Sin Ollama** (`not gpu`): mismo test con un módulo de prueba `llm: True` que no llama a Ollama (comprueba resolución de `cfg`, `fill`, secuencia de variantes, aislamiento). GUI: pestaña **Prompt** en el panel del nodo (solo módulos LLM): estado de partida, hasta 4 variantes lado a lado, «Probar», «Guardar en el nodo» (persiste tras recargar) | `not gpu` + `llm` + `ui` |
| C4 | Exportar `.py` / `langgraph.json` | `export?format=py` de `escritor_critico` (con el LLM sustituido por un módulo de prueba) y de un proyecto con `subgrafo`, `codigo_python` (proceso) y una condición: descomprimido en una carpeta temporal **fuera de `studio/`**, un proceso Python **nuevo** del intérprete estudio importa `graph.py` sin importar FastAPI ni `server` (comprobado con `sys.modules`), lo compila con un `InMemorySaver` y `ainvoke` con la misma entrada da el **mismo estado final** que la ejecución en el estudio; los subprocesos usan los módulos y `runner.py` de la exportación, no los de `studio/`. `langgraph.json` es JSON válido y cada `graphs[…]` apunta a un fichero y variable que existen. `settings.json` exportado no contiene `token`. GUI: «Exportar ▾ JSON / Python (zip)» | `not gpu` + `ui` |
| C5 | API funcional | `export?format=funcional` de (a) un DAG con un join `[a, b] → c`, (b) un bucle con condición y `else` (tipo `escritor_critico` con módulos de prueba) y (c) un grafo con `revision_humana`: en un proceso nuevo, con `InMemorySaver`, (a) y (b) dan el mismo estado final que `graph.py` y el mismo número de ejecuciones por nodo; en (b) `recursion_limit` bajo → `GraphRecursionError`; (c) se para con un interrupt cuyo `value` es el de `revision_humana` y `Command(resume=…)` lo termina sin repetir los nodos anteriores (contador de llamadas). Proyecto con `mapa_send`, `router`, `subgrafo`, `agent`, `cache`, `defer` o `error_to` → 400 `unsupported` con el motivo, y el zip de C4 no trae `funcional.py` sino la explicación en `LEEME.md` | `not gpu` |
| C6 | MCP | Con `mcp_servidores` = un servidor stdio de prueba (`sumar(a, b)`, `eco(texto)`) y otro inexistente: `GET /mcp/servers` lista las 2 herramientas del primero y `ok: false` con el error del segundo (en < 15 s, sin 500). `POST /mcp/call sumar {a:2, b:3}` → 5. Un nodo `mcp_herramientas(servidor, herramienta=eco, argumentos={"texto": "{texto}"})` escribe el resultado en `campo_salida`; servidor caído → error del nodo (y se reintenta según `retry`). `agente_react` con `mcp: prueba` (llm) responde «¿cuánto es 2+3 usando la herramienta?» llamando a `prueba__sumar`. Vista: sección MCP en Configuración (editar servidores en JSON, estado y herramientas de cada uno, probar una llamada) | `not gpu` + `llm` (agente) + `ui` |

Criterios transversales F3:

- **T1 (plantilla `debate`, §9):** crear desde la plantilla genera el proyecto y su subproyecto `<slug>-intervencion`
  (voz → lip-sync de un personaje, usado por `subgrafo`); el moderador (`llm_ollama`, JSON) produce los turnos, `mapa_send`
  lanza **un clip por intervención** y `concatenar_video.py` (módulo nuevo, proceso pesado, ffmpeg `concat`) los une en
  el orden de los turnos. Con 2 personajes × 2 turnos (texto corto, `tam=256`, sin mejora) → `video_final` con
  duración = suma de los clips ± 1 s (`gpu` + `llm`). Sin GPU (`not gpu`): misma topología con módulos de prueba
  comprueba el orden de concatenación.
- **T2 (no regresión):** los 103 tests `not gpu`, los 5 `gpu` y los `llm` de F1+F2 siguen en verde.
- **T3 (GUI):** criterio 9 de F1 ampliado a las rutas F3 (Datasets, Experimentos/comparar, Programaciones, Estudio con
  pestaña Prompt, Configuración con MCP).
- **No se hace en F3** (sin criterio): workers persistentes de modelos (§5.6), sesiones MCP persistentes, puesta al día
  de crons atrasados, ejecución de experimentos en otras máquinas, protocolo por fd 3 y reutilización por contenido
  (siguen opcionales desde F2).

**Resultados del spike 3a** (`tests/test_spike_f3.py`, 6 pruebas en verde; croniter 7.0.0, langchain-mcp-adapters
0.3.2, mcp 1.30.0):

| Pregunta | Resultado verificado | Consecuencia |
|---|---|---|
| `croniter` con 6 campos | `* * * * * */2` = cada 2 s (segundos al final); `croniter.is_valid` distingue expresiones válidas | Validación con `is_valid` → 400 `cron` |
| `@entrypoint`/`@task` | Tareas lanzadas antes de esperarlas corren en paralelo; `interrupt` dentro de una tarea para el entrypoint y `Command(resume)` lo reanuda **sin repetir** las tareas terminadas; una tarea puede llamarse en bucle; el entrypoint recibe `config` (con `recursion_limit`) por inyección | El límite de supersteps lo aplica el propio bucle de `funcional.py` con `config["recursion_limit"]` |
| MCP | `MultiServerMCPClient.get_tools(server_name=)` lista y llama a un `FastMCP` stdio en < 15 s; un comando inexistente lanza excepción. `tool.ainvoke` devuelve bloques de contenido; `session.call_tool` da además `structuredContent` (`{"result": 5}`) e `isError` | `/mcp/call` y `mcp_herramientas` usan `session.call_tool`: resultado = `structuredContent["result"]` si es la única clave, si no `structuredContent`, si no el texto (JSON si se puede); `isError` → error |
| `build()` copiado fuera de `studio/` | Con `compiler.py`, `modules.py`, `runner.py`, `herramientas.py` en `studio_runtime/` y `STUDIO_HOME` = carpeta exportada, un proceso nuevo compila y ejecuta (incluido un nodo de proceso con el `runner.py` copiado) sin importar FastAPI | Ninguna |
| `Send` hacia un nodo `subgrafo` | Funciona: 3 envíos → 3 invocaciones del hijo en paralelo; sus salidas se reúnen con la clave `append` del padre | `debate` usa `mapa_send → subgrafo` |

**Pasos F3** (cada uno termina con sus tests en verde y las suites de F1 y F2 intactas):

| Paso | Contenido | Tests |
|---|---|---|
| 3a | `croniter langchain-mcp-adapters`; spike `tests/test_spike_f3.py`: `croniter` con segundos al final y `get_next` desde «ahora»; `@entrypoint`/`@task` con `AsyncSqliteSaver`/`InMemorySaver`: tareas en paralelo, `interrupt` dentro de una tarea y reanudar sin repetir las terminadas, `recursion_limit`; `MultiServerMCPClient` contra un `FastMCP` stdio local (listar, llamar, servidor caído); `build()` importado desde una copia en otra carpeta con `STUDIO_HOME` propio; `Send` hacia un nodo `subgrafo` (para `debate`). Si algo difiere, actualizar §6.4/§13.5 antes de seguir | spike |
| 3b | Migración `user_version = 3`; datasets, ejemplos, `from-run`, JSONL | C1 (parte) |
| 3c | Experimentos: lanzador con `concurrency`, evaluadores `expr` y `llm`, `stop`, `rescore`, `compare`, reconciliación al arrancar, evento `experiment.status` | C1 |
| 3d | Planificador de crons en el lifespan, API, `cron.fired`, reinicio | C2 |
| 3e | Playground (`/nodes/{node}/playground`) | C3 |
| 3f | Exportar `format=py` (zip) y `format=funcional` | C4, C5 |
| 3g | MCP: ajuste `mcp_servidores`, `/mcp/servers`, `/mcp/call`, `mcp_herramientas.py`, campo `mcp` en `agente_react` | C6 |
| 3h | `concatenar_video.py`, plantillas con subproyectos, plantilla `debate` | T1 |
| 3i | GUI F3 (`static/views/f3.js`): Datasets (ejemplos, añadir desde una ejecución —botón también en Trazas e Hilos—, importar/exportar), Experimentos (lanzar, progreso, tabla de resultados, comparar), Programaciones, pestaña Prompt, Exportar ▾, MCP en Configuración | `ui`, T3 |
| 3j | Regresión completa (`not gpu`, `gpu`, `llm`) y estado de F3 en §13.6 | T2 |

### 13.6 Estado de F3 y notas de implementación (2026-11-18)

F3 **terminada**: criterios C1–C6 y T1–T3 en verde. Tests nuevos: spike F3 6, `test_f3_api.py` 8, `test_f3_export.py` 5,
`test_f3_debate.py` 2 (1 `gpu`), `test_f3_ui.py` 2, `test_f3_llm.py` 3 (`llm`). Regresión final (T2): suite por
defecto **125** en verde (103 de F1+F2 + 22 de F3), `-m llm` **9**, `-m gpu` **6** (≈ 9 min). Ficheros nuevos: `f3.py` (evaluadores,
crons, playground, JSONL), `exportar.py`, `funcional_rt.py`, `mcp_cliente.py`, `static/views/f3.js`,
`modules/mcp_herramientas.py`, `modules/concatenar_video.py`, `templates/debate.json`.

| Paso | Hecho |
|---|---|
| 3a | croniter 7.0.0, langchain-mcp-adapters 0.3.2 (mcp 1.30.0); spike con 6 pruebas (resultados en §13.5) |
| 3b | Migración `user_version = 3`; datasets, ejemplos, `from-run`, JSONL |
| 3c | Experimentos (`concurrency`, evaluadores `expr`/`llm`, `stop`, `rescore`, `compare`, reconciliación) |
| 3d | Planificador de crons en el lifespan (cada 0,5 s), API, `cron.fired`, disparo único de los vencidos al arrancar |
| 3e | Playground (`/nodes/{node}/playground`) |
| 3f | `export?format=py` (zip) y `format=funcional` (`funcional_rt.build_funcional`) |
| 3g | MCP: `mcp_servidores`, `/mcp/servers`, `/mcp/call`, `mcp_herramientas`, campo `mcp` de `agente_react` |
| 3h | `concatenar_video`, plantillas con `subproyectos`, plantilla `debate` |
| 3i | GUI: Datasets (ejemplos, experimentos, comparar, importar/exportar), Programaciones, pestaña Prompt, «Exportar ▾», «→ Dataset» en Trazas e Hilos, «mostrar ocultos» en Hilos, MCP en Configuración |

Desviaciones y decisiones de implementación (todas menores):
- Endpoints extra para la GUI, que responden siempre 200 (un 400 aparece como error en la consola del navegador):
  `GET /crons/preview?cron=&n=5` → `{cron, valido, proximas, error}` y `GET /projects/{slug}/export?format=funcional&check=1`
  → `{ok, motivos}`.
- **VRAM:** el semáforo `shared` dejaba entrar a la vez a todos los nodos GPU compartidos; con `debate` (4 Kokoro en
  paralelo por el Send, ≈ 0,8 GB cada uno, más qwen2.5:7b cargado) se agotaba la VRAM. Ajuste nuevo
  `gpu_procesos_compartidos: 2` = máximo de nodos `gpu: "shared"` **en subproceso** a la vez (los LLM locales, que usan
  Ollama, no cuentan); 0 = sin límite.
- `tts_kokoro` gana el campo opcional `campo_voz` (clave del estado con la voz; la usa `debate` para una voz por
  personaje). Sin él se comporta igual que antes.
- Plantillas: clave `subproyectos: {clave: proyecto}`; al crear se crean como `<slug>-<clave>` y se sustituye
  `{slug}-<clave>` en la configuración de los nodos.
- `mcp_herramientas.servidor` es un campo de texto (no `select`): las opciones dependen de los ajustes. Un argumento que
  es exactamente `"{clave}"` conserva el tipo del valor del estado.
- `mcp_servidores` se sustituye entero al guardar ajustes (el merge profundo no permitía borrar servidores).
- `Send` aplica las escrituras en el orden de la lista (no en el de terminación); `concatenar_video` ordena por `orden`
  de todos modos.
- Playground: la respuesta incluye además `texto_tokens` (texto emitido con `ctx.token`).
- Exportación: `studio_runtime/` incluye también `funcional_rt.py` y `mcp_cliente.py`; el zip trae un `.env` con
  `LANGSMITH_TRACING=false` (lo referencia `langgraph.json`).

### 13.7 D9: Flujos, Agentes y flujos como herramientas *(rev. 2.4, aprobado el 2026-11-19)*

**Alcance.** Solo cambian los textos visibles, más una herramienta nueva. No cambian la API REST (`/projects`, `/assistants`), las carpetas,
`project.json`, las tablas ni los ids/`data-*` del DOM que usan los tests. Glosario de la GUI: **flujo** = proyecto (§0),
**agente** = asistente (§3.5), **agente con herramientas** = nodo `kind: agent`.

**Renombrado (GUI).** Menú: «Proyectos» → **Flujos** (ruta `#/flujos`; `#/proyectos` sigue funcionando y redirige).
Inicio: «Proyectos» → **Flujos**, «+ Nuevo proyecto» → **+ Nuevo flujo**; «+ Nuevo agente» pide **flujo** + nombre.
Diálogos y títulos: «nombre del proyecto» → «nombre del flujo», «Duplicar/Importar/Exportar/Borrar proyecto» → «… flujo»,
avisos («afecta a N nodos en M flujos»), filtros de Hilos/Trazas/Datasets/Programaciones («Flujo»). Estudio: selector
«Asistente» → **Agente**. Módulo `subgrafo`: `name` «Subflujo», campo `proyecto` rotulado «Flujo» (la clave sigue siendo
`proyecto`). Módulo `agente_react`: `name` **«Agente con herramientas»**. Los mensajes de error de la API que lleguen a
la GUI con la palabra «proyecto» se dejan como están (no se tocan textos del servidor salvo `META`/`CONFIG`).

**Flujos como herramientas.** `agente_react` gana el campo `flujos: keys = ""` (slugs separados por comas). En cada
compilación, cada slug produce una herramienta `flujo__<slug_con_guiones_bajos>`:
- descripción = `desc` del flujo (o su `name` si está vacía);
- argumentos = las claves del `state_schema` del flujo con `input: true`, todas de tipo `str`. Si no hay ninguna, se usa
  un único argumento `texto: str`;
- al llamarla, el flujo se compila como en `subgrafo` pero con `checkpointer=False` (no hereda el del padre: no deja checkpoints en el hilo) y se hace `ainvoke` con esos argumentos y la config del padre. El
  resultado son las claves con `output: true` (o el estado final sin `messages`), en JSON, recortado a 4000 caracteres;
- validación al compilar el padre: si el slug no existe, el flujo se llama a sí mismo (directa o indirectamente, la
  misma comprobación de ciclos que `subgrafo`) o tiene `messages`, da error de validación con el motivo;
- si dentro del flujo hay un `interrupt` (p. ej. `revision_humana`), no se admite: la herramienta devuelve el error
  «el flujo pide revisión humana; no se puede usar como herramienta» y el agente sigue;
- los eventos del flujo llegan con `ns ≠ ""` (como un subgrafo); la exportación funcional (C5) ya rechaza `agent`, así
  que no cambia; la exportación `.py` (C4) incluye `projects/<slug>.json` de esos flujos.

| # | Criterio | Pruebas |
|---|---|---|
| G1 | **Renombrado.** El menú muestra «Flujos» y «Agentes», y en ningún sitio aparecen las palabras «Proyecto/Proyectos» ni «Asistente/Asistentes» (búsqueda en el texto visible de Inicio, Flujos, Estudio con un flujo abierto, Hilos, Agentes, Módulos, Trazas, Datasets y Programaciones; no se miran textos de error del servidor). `#/proyectos` abre la vista Flujos. «+ Nuevo flujo» con la plantilla «Clip de personaje» sigue abriendo el diálogo guiado. En la biblioteca de Módulos aparecen «Agente con herramientas» y «Subflujo». Todas las suites existentes pasan sin cambiar ids ni rutas de API (solo se actualizan las aserciones de texto visible) | `ui` + suites actuales |
| G2 | **Flujo como herramienta (sin LLM).** Flujo hijo `entrada → codigo_python(longitud = len(texto)) → salida`, con `texto` `input: true` y `longitud` `output: true`. Un agente con `flujos = hijo` y un modelo falso que pide `flujo__hijo(texto="hola")`: la herramienta devuelve `{"longitud": 4}` en un `ToolMessage`, llegan eventos del hijo con `ns ≠ ""` y el hilo del padre no crea checkpoints del hijo. Si `flujos` apunta a un slug inexistente, al propio padre (o hay un ciclo indirecto vía subflujo) o a un flujo con `messages`, `GET …/validate` da el error en el nodo agente con el motivo y una ejecución termina `error` (como cualquier flujo no válido: `POST …/runs` acepta con 202 y falla al compilar). Si el hijo tiene `revision_humana`, el `ToolMessage` lleva el error y la ejecución del padre termina `success`. La exportación `.py` del padre incluye `projects/<hijo>.json` y, en un proceso nuevo con el mismo modelo falso, da la misma respuesta final (como C4) | `not gpu` |
| G3 | **Flujo como herramienta (Ollama real).** Con `qwen` y `flujos = hijo` (G2), a la pregunta «¿cuántas letras tiene "murciélago"?» el agente llama a `flujo__hijo` y la respuesta contiene 10. En el chat del Estudio la llamada aparece plegable con el nombre «flujo · hijo» | `llm` + `ui` |

| Paso | Contenido | Criterio |
|---|---|---|
| 4a | Textos de la GUI (`app.js`, `views/*.js`), ruta `#/flujos` con redirección y `META`/`CONFIG` de `subgrafo` y `agente_react`; aserciones de texto de los tests | G1 |
| 4b | `flujos` en `agente_react`: construcción de herramientas en `compiler.py`, validación de ciclos compartida con `subgrafo`, `exportar.py` | G2 |
| 4c | Prueba con Ollama y vista del chat | G3 |

### 13.8 Estado de D9 y notas de implementación (2026-11-19)

Pasos 4a–4c **hechos**; G1–G3 en verde (`tests/test_d9_ui.py` → G1 [`ui`], `tests/test_d9_flujos.py` → G2 [`not gpu`],
`tests/test_d9_llm.py` → G3 [`llm`]).

- **4a.** Textos en `app.js` y `views/*.js`; el menú usa la ruta `flujos` (`App.views.flujos`); `App.views.proyectos`
  hace `location.replace("#/flujos")`. Los ids (`nuevoProyectoBtn`, `naProyecto`, `qvProyecto`, `asstSel`,
  `nuevoAsistente`…), las claves de `localStorage` y la ruta `#/asistentes` no cambian. Módulos: `subgrafo` → «Subflujo»,
  `agente_react` → «Agente con herramientas»; la plantilla `react_chat` se llama «Agente con herramientas (chat)»; las
  ayudas «contexto del asistente» pasan a «contexto del agente»; el rótulo del ajuste `hilos_conservar` (`db.py`) dice «por flujo». Tests de texto actualizados: `test_f3_ui.py`
  (orden «Flujos», «Agentes») y `test_ui.py` (token → vista Flujos).
- **4b.** `compiler.py`: `_validate_flujos` (en `validate`, para nodos `kind: agent` con `flujos`; misma pila de ciclos que
  `subgrafo`, así que detecta ciclos mixtos subflujo/herramienta), `tool_name_flujo`, `_tool_flujo` (`StructuredTool` con
  `args_schema` de `pydantic.create_model`, todas `str`) y `_build_agent(..., extra_tools)`. Spike: con
  `checkpointer=None` el hijo invocado desde la herramienta **sí** guarda checkpoints bajo `agente:…|herramientas:…`; con
  `checkpointer=False` no, y los eventos siguen llegando con `ns ≠ ""`. Un `interrupt` dentro lanza `GraphInterrupt`,
  que la herramienta convierte en el texto de error. `exportar.subproyectos` recoge también los slugs de `flujos`.
  Si `herramientas` está vacío se siguen cargando todas las de `modules/tools` (como antes).
- **4c.** Chat del Estudio: las llamadas `flujo__<slug>` se rotulan «⧉ flujo · <slug con guiones bajos>».

### 13.1 Plan de pruebas

- `studio/tests/`, `pytest -q` con `.venv-studio`; marcadores `spike`, `gpu`, `ui`. Por defecto se excluye `gpu`
  (`-m "not gpu"`); los `gpu` llevan timeout de 15 min.
- Servidor de prueba: fixture que lanza uvicorn en 8799 con `STUDIO_HOME` apuntando a una carpeta temporal (el servidor
  toma todas sus rutas de `STUDIO_HOME`, por defecto `studio/`), token fijo.
- Fixture de vídeo rápido: retrato existente `BASE/cleo_0.png`, texto de ~10 palabras, `tam=256`, `mejora=false`,
  `quieto=true`.
- Casos límite obligatorios: `else` duplicado, join con nodo inexistente, condición con sintaxis errónea, 409 por
  versión, `thread_busy`, salida no serializable, clave no declarada, módulo con `import torch` en local.
- Definición de terminado de F1: todos los tests `not gpu` en verde, los `gpu` ejecutados una vez en verde, `arrancar.sh`
  levanta en 8790 y v1 está en `legacy/`.

## 14. Riesgos

- **VRAM 8 GB:** §5.6. **RAM 10 GB:** SDXL con offload usa ~6–7 GB; el semáforo `exclusive` impide simultaneidad.
- **Código local síncrono:** no cancelable; pool propio de 4 threads; módulos lentos → `exec: proceso`.
- **Hilos y versiones del proyecto:** continuar un hilo con otra versión puede dejar en `next` nodos que ya no existen →
  aviso en la GUI y error claro si se intenta.
- **Tamaño de la GUI:** separar `app.js` por vistas (`app.js` + `views/*.js`) si supera ~3000 líneas.

---

## Anexo A. Correspondencia con la revisión EGM

Todos los bloqueantes (B1–B16) e importantes (I1–I24) de `REVISION_EGM.md` están resueltos en este documento:
B1 §3.1.1 · B2 marcas de fase y §13 · B3 §9/Anexo B · B4 §8 montaje y criterio 4 · B5 §3.3 · B6 §4 · B7 §3.6/§3.7 ·
B8 §6.2 · B9 §6.1 · B10 §4/§5.3 · B11 §5.7/§5.8 · B12 §3.8 · B13 §8 revision_humana/§5.4 · B14 §13.1 · B15 §1.1 ·
B16 §2.1 · I1 §3.5 · I2 §5.6 · I3 §4.2 · I4/I5 §4 · I6 §5.9 · I7 §12 · I8 paso 1a · I9 §4/§8 · I10 §4 · I11 §5.2 ·
I12 §3.5 · I13 §3.1 · I14 D2/§11 · I15 §5.7 · I16 §0 · I17 §3.1 · I18 glosario · I19 convención · I20 §3.4 ·
I21 §3.3/§3.1.1 · I22 §0/§4 · I23 §3.1 · I24 §3.8 (opcional F2). Menores: incorporados donde aplican.

## Anexo B. Plantillas F1 literales

`templates/clip_personaje.json`:

```json
{
  "name": "Clip de personaje", "desc": "Un personaje histórico habla a cámara: guion (fijo o LLM con revisión), voz, retrato, lip-sync y montaje.",
  "template": "clip_personaje", "state_schema": {}, "context_schema": [],
  "nodes": {
    "entrada":  {"module": "entrada.py", "label": "Entrada", "x": 40, "y": 200, "config": {"valores": {
      "origen_texto": "texto", "personaje": "Cleopatra VII",
      "texto": "Napoleón, tú llegaste a Egipto con cañones y sabios. Yo lo goberné veinte años.",
      "tema": "Responde a Napoleón, que presume de su expedición a Egipto de 1798.",
      "palabras": 14, "nombre": "CLEOPATRA VII", "subtitulo": "Reina de Egipto (51–30 a.C.)",
      "comentario_revision": "", "rechazos": 0}}},
    "guion":    {"module": "llm_ollama.py", "label": "Guion", "x": 260, "y": 60, "config": {
      "prompt": "Eres {personaje} en un debate histórico televisado. {tema} Responde con UNA intervención de unas {palabras} palabras, en español, en primera persona, con datos históricos reales y el tono propio del personaje. Solo el texto hablado, sin comillas ni acotaciones.\nVersión anterior (si la hay): {texto}\nComentario del revisor (si lo hay): {comentario_revision}",
      "campo_salida": "texto", "formato": "texto", "temperatura": 0.7, "num_predict": 200}},
    "revision": {"module": "revision_humana.py", "label": "Revisión", "x": 480, "y": 60, "config": {
      "campo": "texto", "mensaje": "Revisa el guion antes de generar el vídeo", "campo_comentario": "comentario_revision"}},
    "voz":      {"module": "tts_kokoro.py", "label": "Voz", "x": 700, "y": 200, "config": {"voz": "ef_dora", "velocidad": 0.95}},
    "retrato":  {"module": "retrato_sdxl.py", "label": "Retrato", "x": 480, "y": 340, "config": {"modo": "existente", "imagen": "cleo_0.png"}},
    "lipsync":  {"module": "lipsync_sadtalker.py", "label": "Lip-sync", "x": 920, "y": 270, "config": {"tam": 512, "mejora": true, "quieto": true, "expresion": 1.1}},
    "montaje":  {"module": "montaje_video.py", "label": "Montaje", "x": 1140, "y": 270, "config": {"crf": 18}},
    "salida":   {"module": "salida.py", "label": "Salida", "x": 1360, "y": 270, "config": {"mostrar": [
      {"clave": "video_final", "como": "video"}, {"clave": "texto", "como": "texto"}]}}
  },
  "edges": [
    {"id": "e1",  "src": "__start__", "dst": "entrada"},
    {"id": "e2",  "src": "entrada", "dst": "retrato"},
    {"id": "e3",  "src": "entrada", "dst": "guion", "cond": "s.get('origen_texto') == 'tema'", "label": "tema"},
    {"id": "e4",  "src": "entrada", "dst": "voz", "cond": "else", "label": "texto fijo"},
    {"id": "e5",  "src": "guion", "dst": "revision"},
    {"id": "e6",  "src": "revision", "dst": "voz", "cond": "s.get('aprobado')", "label": "aprobado"},
    {"id": "e7",  "src": "revision", "dst": "guion", "cond": "not s.get('aprobado') and (s.get('rechazos') or 0) < 3", "label": "reescribir"},
    {"id": "e8",  "src": "revision", "dst": "__end__", "cond": "else", "label": "3 rechazos"},
    {"id": "e9",  "src": ["voz", "retrato"], "dst": "lipsync"},
    {"id": "e10", "src": "lipsync", "dst": "montaje"},
    {"id": "e11", "src": "montaje", "dst": "salida"},
    {"id": "e12", "src": "salida", "dst": "__end__"}
  ],
  "layout": {"zoom": 1.0, "pan": [0, 0]}
}
```

`templates/escritor_critico.json`:

```json
{
  "name": "Escritor con autocrítica", "desc": "Un LLM escribe, otro lo puntúa en JSON y se reescribe hasta puntuación ≥ 8 o 3 iteraciones.",
  "template": "escritor_critico", "state_schema": {}, "context_schema": [],
  "nodes": {
    "entrada":  {"module": "entrada.py", "label": "Entrada", "x": 40, "y": 180, "config": {"valores": {
      "tema": "Por qué Cleopatra fue una gobernante brillante", "texto": "", "critica": "(sin crítica todavía)",
      "puntuacion": 0, "iteracion": 0}}},
    "escritor": {"module": "llm_ollama.py", "label": "Escritor", "x": 260, "y": 180, "config": {
      "prompt": "Escribe un párrafo de unas 60 palabras para un debate histórico sobre: {tema}\nTexto anterior: {texto}\nCrítica recibida: {critica}\nDevuelve solo el párrafo.",
      "campo_salida": "texto", "formato": "texto", "temperatura": 0.8}},
    "critico":  {"module": "llm_ollama.py", "label": "Crítico", "x": 480, "y": 180, "config": {
      "prompt": "Evalúa este párrafo para un debate histórico (rigor, tono y gancho). Responde SOLO con JSON {\"puntuacion\": entero de 1 a 10, \"critica\": \"una frase\"}.\nPárrafo: {texto}",
      "formato": "json", "claves_json": "puntuacion,critica", "temperatura": 0.2}},
    "contador": {"module": "codigo_python.py", "label": "Contador", "x": 700, "y": 180, "config": {
      "codigo": "return {\"iteracion\": int(state.get(\"iteracion\") or 0) + 1}", "entradas": "iteracion", "salidas": "iteracion"}},
    "salida":   {"module": "salida.py", "label": "Salida", "x": 920, "y": 180, "config": {"mostrar": [{"clave": "texto", "como": "texto"}]}}
  },
  "edges": [
    {"id": "e1", "src": "__start__", "dst": "entrada"},
    {"id": "e2", "src": "entrada", "dst": "escritor"},
    {"id": "e3", "src": "escritor", "dst": "critico"},
    {"id": "e4", "src": "critico", "dst": "contador"},
    {"id": "e5", "src": "contador", "dst": "salida", "cond": "int(s.get('puntuacion') or 0) >= 8 or (s.get('iteracion') or 0) >= 3", "label": "listo"},
    {"id": "e6", "src": "contador", "dst": "escritor", "cond": "else", "label": "reescribir"},
    {"id": "e7", "src": "salida", "dst": "__end__"}
  ],
  "layout": {"zoom": 1.0, "pan": [0, 0]}
}
```

`templates/vacio.json`:

```json
{
  "name": "Vacío", "desc": "Punto de partida mínimo.", "template": "vacio", "state_schema": {}, "context_schema": [],
  "nodes": {
    "entrada": {"module": "entrada.py", "label": "Entrada", "x": 80, "y": 160, "config": {"valores": {"texto": "Hola"}}},
    "salida":  {"module": "salida.py", "label": "Salida", "x": 340, "y": 160, "config": {"mostrar": [{"clave": "texto", "como": "texto"}]}}
  },
  "edges": [
    {"id": "e1", "src": "__start__", "dst": "entrada"},
    {"id": "e2", "src": "entrada", "dst": "salida"},
    {"id": "e3", "src": "salida", "dst": "__end__"}
  ],
  "layout": {"zoom": 1.0, "pan": [0, 0]}
}
```

Nota (rev. 2.1): en `clip_personaje`, `e7` incluye `not s.get('aprobado')`; sin ello, al aprobar tras un rechazo
se disparaban a la vez `voz` y `guion` (las condiciones verdaderas hacen fan-out, §3.4).

Nota sobre `valores`: en las plantillas es un objeto JSON (no una cadena); el campo `json` de la GUI lo edita como texto
y lo guarda como objeto.
