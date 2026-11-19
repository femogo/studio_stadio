# Revisión EGM de DISENO.md

**Documento revisado:** `/opt/historico/demo/studio/DISENO.md` (líneas 1–455)
**Fecha:** 2026-11-18
**Prioridad de la revisión:** que una sesión nueva de Claude Code pueda implementar el sistema leyendo solo este documento.

## Estado de los revisores

| Revisor | Estado |
| --- | --- |
| `egm-structural` | completado |
| `egm-style` | completado |
| `egm-comprehension` | completado |
| `egm-design` | completado |
| `egm-codebase-details` | completado |
| `egm-implementation-readiness` | completado |
| `egm-zoom-out` | completado |
| `egm-inventive-catalyst` | completado |

## Veredicto

**Todavía no se puede implementar.** La arquitectura es sólida: un motor LangGraph en Python 3.12, nodos pesados como subprocesos 3.10, y breakpoints que se pasan en cada ejecución en lugar de compilarse. Los revisores contrastaron casi todas las APIs citadas con el código fuente instalado (ver Anexo A).

Lo que falta son los **contratos**: el protocolo del subproceso, el esquema de SQLite y de `settings.json`, los eventos SSE, los cuerpos de la API REST, la traducción del estado, las plantillas concretas, el plan de pruebas y a qué fase pertenece cada elemento. También hay varias afirmaciones que contradicen el `runner.py` existente o la versión instalada de LangGraph.

Hay que entender que los revisores leyeron el código fuente y comprobaron firmas, pero **no ejecutaron nada**.

**Recuento:** 16 bloqueantes, 24 importantes y 22 menores.

---

## Hallazgos bloqueantes

### B1. El protocolo del subproceso aparece de tres formas distintas
- **Lentes:** codebase-details, structural, style, comprehension, design, implementation-readiness, inventive-catalyst
- **Dónde:** §2 l.72–73 (`@@P/@@S/@@L`), §3.1 l.109 y l.116–126 (`@@R`, `ctx`), §4 l.209–210
- **Problema:**
  - `@@L` no está definido en ninguna parte y `@@R` no existe en `runner.py`.
  - `runner.py` envía el resultado final como un `@@S` más (runner.py:87–88), así que el resultado no se distingue de una actualización parcial. Además, v1 aplica al estado todos los `@@S`, lo que haría que un nodo que se mata a medias deje escrituras parciales en el checkpoint.
  - `runner.py` llama a `run` sin `await` y rechaza todo lo que no sea un dict (runner.py:75, 84–86). Sin embargo, §3.1 dice que `run` puede ser async y puede devolver un `Command`.
  - El `Ctx` de `runner.py` no tiene `thread_id`, `context` ni `store`; tiene `studio`, que no está documentado.
  - No se especifica el formato de `_in_<nodo>.json`. El argv real espera **rutas absolutas** al `.py` y a la carpeta, no un nombre de módulo.
  - El formato de `@@STORE` no está definido.
- **Propuesta:** crear una sola subsección canónica, «§3.1.1 Protocolo del subproceso», con este texto:
  > argv: `<ruta absoluta modules/x.py> <ruta absoluta carpeta de la tarea> <node_id>`. Lanzar con `start_new_session=True`, `stderr=STDOUT`, `cwd=/opt/historico/demo`. Entrada `_in.json` = `{"state": dict plano (solo META.inputs si existe), "cfg": config resuelta, "settings": settings.json, "node": node_id, "thread_id", "run_id", "context"}`.
  > Mensajes: `@@P <float 0..1>` progreso · `@@S <json en una línea>` vista previa en vivo (evento `custom`, **no se aplica al estado**) · `@@R <json en una línea>` resultado, exactamente una vez; es lo único que se aplica al estado · cualquier otra línea = log. No existe `@@L`.
  > Si la salida es 0 y no ha llegado `@@R`, es un error del nodo.
  > En modo proceso `run` es síncrona y devuelve dict o None. Devolver un `Command`, un `Send` o una corrutina es un error («solo en modo local»). `sys.exit("msg")` es un error y `sys.exit()` es un éxito sin salida.
  > `ctx` en proceso: `run_dir, node_id, thread_id, context, settings, base, log, progress, update, fill, path, stream`. `ctx.store` lanza `NotImplementedError`.

  Añadir que **`runner.py` debe modificarse** para emitir `@@R` en lugar del `@@S` final y para incluir `thread_id`/`context`. Sustituir l.73 por «protocolo definido en §3.1.1». Quitar `@@STORE` del documento. Si se necesita store en proceso, usar la alternativa de la propuesta de I23.

### B2. Las secciones normativas no dicen a qué fase pertenece cada cosa, y la fase 1 es demasiado grande
- **Lentes:** zoom-out, structural, implementation-readiness, comprehension, style
- **Dónde:** §2–§10 frente a §13 l.416–422
- **Problema:** las fases solo aparecen en §13; §3–§8 describen el sistema completo. Hay contradicciones directas con la fase 1:
  - «Toda ejecución usa un asistente» (l.190) y `POST /runs` con `assistant_id`, pero los asistentes son de fase 2.
  - §4.5 compila con `store=` y `cache=`, que son de fase 2.
  - El menú lateral de fase 1 incluye Asistentes, Memoria, Trazas, Datasets y Programaciones.
  - La barra superior tiene deshacer/rehacer y el lienzo tiene minimapa, ambos de fase 2.
  - La pestaña Prompt es de fase 3.
  - No tienen fase: la vista Hilos, `/quick-video`, el diálogo «Crear vídeo nuevo», `/system`, el token, la migración y el tema claro.
  - «Time travel básico» no está definido.
  - Además, la fase 1 junta tres trabajos grandes: compilador, motor y editor visual.
- **Propuesta:**
  1. Añadir una columna o etiqueta **[F1]/[F2]/[F3]** a cada viñeta de §5, cada fila de §6, cada vista y pestaña de §7 y cada módulo de §8. Añadir a §13: «Lo que no esté marcado F1 no se implementa en la fase 1: no aparece en el menú y sus endpoints no existen».
  2. Contenido propuesto para F1:
     - **Módulos:** `entrada, salida, llm_ollama, revision_humana, codigo_python, tts_kokoro, retrato_sdxl, lipsync_sadtalker, montaje_video` y un módulo de prueba `prueba_lenta.py`.
     - **Motor:** sin asistentes (`assistant = {"context": {}, "node_overrides": {}}` implícito; `context_schema` se ignora); double-texting solo `reject`; sin store ni caché.
     - **Time travel en F1:** lista y árbol de checkpoints, ver `values`/`next`, «reproducir desde aquí», «editar y bifurcar». «Re-ejecutar nodo» pasa a F2.
     - **Lienzo F1:** zoom, desplazamiento, rejilla, ajustar a la vista, arrastrar, crear y borrar aristas, editar condición, selección simple + Supr. Pasan a F2: multiselección, copiar/pegar, auto-ordenar, minimapa y xray.
  3. Dividir la fase 1 en pasos ordenados, cada uno cerrado con sus propias pruebas:
     - 1a: spike de LangGraph (I8)
     - 1b: `modules.py` + cambios en `runner.py`
     - 1c: `compiler.py`
     - 1d: `db.py` + `engine.py`
     - 1e: REST/SSE
     - 1f: módulos de vídeo + plantilla 1
     - 1g: GUI
     - 1h: migración + `arrancar.sh`

     Durante el desarrollo, v2 escucha en **8791** y v1 sigue en 8790 hasta el paso 1h.
  4. Dividir el trabajo de esta forma también permite tener el vídeo funcionando (criterio 1) antes de que exista el editor del lienzo.

### B3. Las plantillas solo están en prosa, y sus identificadores se contradicen
- **Lentes:** comprehension, structural, style, implementation-readiness
- **Dónde:** §9 l.357–364, l.132 y l.271 (`debate_clip`), §6 `GET /templates`
- **Problema:**
  - `debate_clip` aparece en el ejemplo de un clip de un solo personaje y en `/quick-video`, pero el debate es la plantilla 2, de fase 3.
  - No se dice dónde se guardan las plantillas.
  - La plantilla 1 («voz → lipsync ← retrato» seguida de un join) no tiene ids de nodo, claves de estado ni rama `else`.
  - La plantilla 3 usa `state['iteracion']`, pero ningún nodo la produce, y `llm_ollama` escribe el JSON en una sola clave.
  - La plantilla 6, «Vacío», no pasa la validación porque no tiene nodo de entrada.
- **Propuesta:**
  - Ids fijos: `clip_personaje, debate, escritor_critico, react_chat, resumen_rss, vacio`. Sustituir `debate_clip` por `clip_personaje` en l.132 y l.271.
  - Guardar las plantillas en `studio/templates/<id>.json` (mismo esquema que `project.json`, sin `slug/created/version`).
  - Añadir un anexo con el JSON **literal** de las plantillas 1, 3 y 6. Decisiones mínimas para la plantilla 1:
    - **Nodos:** `entrada, guion(llm_ollama), revision(revision_humana), voz, retrato, lipsync, montaje, salida`.
    - **Aristas:**
      - `__start__→entrada`
      - `entrada→retrato` (incondicional)
      - `entrada→guion` con condición `s.get('origen_texto')=='tema'`
      - `entrada→voz` con condición `else`
      - `guion→revision→voz`
      - `[voz,retrato]→lipsync`
      - `lipsync→montaje→salida→__end__`
    - Hay que decir explicitamente que una arista condicional y una incondicional que salen del mismo nodo se compilan las dos.
    - Renombrar `modo` a `origen_texto`, porque «modo» ya se usa para local/proceso y para Grafo/Chat.
    - **Entrada de la ejecución:** `{"origen_texto": "texto"|"tema", "personaje", "texto"?, "tema"?, "nombre"?, "subtitulo"?, "palabras"?}`.
  - **Plantilla 3:** opción `expandir_json: true` en `llm_ollama` (vuelca las claves del JSON en el estado), un nodo contador `codigo_python` que devuelve `{'iteracion': s.get('iteracion',0)+1}`, y condiciones escritas con `s.get(...)`.
  - **Plantilla 6:** `__start__→entrada→salida→__end__`.

### B4. El criterio 4 no se puede cumplir tal como está escrito
- **Lentes:** comprehension, codebase-details, implementation-readiness, structural
- **Dónde:** §13 l.431, §12 l.409
- **Problema:** en v1, `nombre` (el rótulo) es **configuración** del nodo montaje (`config.json` `montaje.nombre`; nodes.py:193 lee `cfg["nombre"]`), no una clave del estado. Por tanto, editar el estado no cambia el rótulo.
- **Propuesta (recomendada):** «`montaje_video.py` lee `nombre`, `subtitulo` y `aviso` del estado si existen; si no, de `cfg`. `entrada` de la plantilla 1 pone `nombre` en el estado». La alternativa es reescribir el criterio como «editar la configuración `nombre` del nodo Montaje durante la pausa».

### B5. La traducción entre el dict plano y `data` no está especificada
- **Lentes:** implementation-readiness, comprehension, style, design, inventive-catalyst, zoom-out, codebase-details
- **Dónde:** §3.3 l.163–169, §3.4 l.183, §5 (editar estado y bifurcar)
- **Problema:** falta decidir:
  - si el merge es superficial o profundo;
  - qué pasa cuando una clave existe declarada y también dentro de `data`;
  - cómo se borra una clave;
  - qué estado ven las condiciones;
  - qué ocurre con una clave inexistente.

  Además:
  - `{"__overwrite__": v}` solo lo interpreta LangGraph en canales **con reductor** (channels/binop.py:35–51); dentro de `data` o con `replace` no hace nada.
  - Con `merge`, las ramas paralelas fusionan claves no declaradas en silencio, mientras que las claves `replace` declaradas lanzan `InvalidUpdateError`.
  - `aupdate_state` exige `as_node` tras un superstep paralelo (pregel/main.py:2000–2003).
- **Propuesta.** Elegir una de dos opciones.
  - **Opción A (recomendada, más simple):** eliminar `data`. El compilador obtiene los canales de la unión de `META.outputs/inputs` de los módulos usados, `state_schema`, las claves que aparecen en las condiciones (por AST) y las claves de la entrada. El reductor por defecto es `replace`. Un módulo que devuelve una clave no declarada provoca un error claro. `codigo_python` declara sus salidas en CONFIG. `__overwrite__` solo se admite en claves con reductor.
  - **Opción B (mantener `data`):** escribir el algoritmo.
    - **Lectura:** `flat = {**state.get('data',{}), **{k: state[k] for k in declaradas if k in state}}`. Si hay colisión, gana la declarada.
    - **Escritura:** clave declarada → canal; clave que empieza por `__` (salvo `__overwrite__`) → error; resto → `data[k]`. El merge es superficial (`{**viejo, **nuevo}`).
    - **Borrado:** marcador `{"__delete__": true}`, que el reductor respeta.
    - `sum = a+b`, `max = max(a,b)`.

  En ambas opciones:
  - Las condiciones ven el dict plano y se recomienda usar `s.get`; una clave ausente devuelve `None`.
  - La misma traducción se aplica a la entrada de la ejecución, a `POST state` y a `aupdate_state`.
  - Los ids de nodo deben cumplir `^[a-z][a-z0-9_]*$` y no pueden coincidir con claves del estado (LangGraph prohíbe `|` y `:`; state.py:807–811).

### B6. `error_to` apunta a un nodo, pero `error_handler` es un callable
- **Lentes:** codebase-details, implementation-readiness, design
- **Dónde:** §3.2 l.147, §4 l.205–206, §5 l.248, §1 l.52
- **Problema:** en 1.2.22, `add_node(error_handler=…)` recibe un **callable** (state.py:677, 699). LangGraph crea un nodo oculto `__error_handler__<id>` (state.py:866–889) y le inyecta el error solo a través de un parámetro llamado `error` anotado como `NodeError` (_runnable.py:252–260, errors.py:191–207).
- **Propuesta:** «F1: `error_to` se guarda pero no se compila. F2: se compila como
  ```python
  from langgraph.errors import NodeError
  async def h(state, error: NodeError):
      return Command(goto=<error_to>, update={"error": {"node": error.node, "msg": repr(error.error)}})
  ```
  y se declaran `destinations`. La GUI oculta los nodos `__error_handler__*`.» Además, verificar con un spike si el handler se ejecuta después de agotar los reintentos, y quitar el punto de la lista de «pendiente de verificar».

### B7. No hay esquema de `studio.sqlite` ni de `settings.json`
- **Lentes:** implementation-readiness, comprehension, structural, style
- **Dónde:** §2 l.69 y l.85–88, §3.5, l.107, l.255, §7 l.321–327
- **Problema:**
  - Las tablas, columnas y claves de ajustes habría que inventarlas.
  - l.85 deja abierto si los asistentes van en JSON o en SQLite.
- **Propuesta:**
  - Decidir: «asistentes solo en `studio.sqlite`» y borrar la línea del árbol de ficheros.
  - Añadir «§3.6 Esquema de studio.sqlite» con DDL, al menos para F1:
    ```sql
    threads(thread_id TEXT PK, project TEXT, title TEXT, metadata JSON, created REAL, updated REAL, legacy INT DEFAULT 0)
    runs(run_id TEXT PK, thread_id TEXT REFERENCES threads ON DELETE CASCADE, project_version INT, module_shas JSON,
         kind TEXT CHECK(kind IN('input','resume','continue','replay','fork')), input JSON, checkpoint_id TEXT,
         checkpoint_id_inicio TEXT, interrupt_before JSON, interrupt_after JSON, status TEXT, error TEXT,
         pgids JSON, t0 REAL, t1 REAL)
    events(id INTEGER PK AUTOINCREMENT, run_id, thread_id, ns, type, node, task_id, data JSON, ts REAL)  -- índices (run_id,id), (thread_id,id)
    outputs(run_id, thread_id, project, node, kind CHECK(kind IN('video','audio','image','text')), path, ts)  -- alimenta Vídeos
    ```
  - Añadir el DDL de F2/F3 (asistentes, versiones de asistentes, datasets, ejemplos, experimentos, crons), marcado por fase.
  - Acceso con `aiosqlite` (ya instalado), WAL, migraciones con `PRAGMA user_version`. Timestamps en epoch, en segundos como float.
  - Añadir «§3.7 settings.json» con el fichero literal de valores por defecto:
    ```json
    {"host":"127.0.0.1","port":8790,"token":"","ollama_url":"http://localhost:11434","ollama_model":"qwen2.5:7b",
     "keep_alive":"0","embed_model":"nomic-embed-text","recursion_limit":50,"max_concurrency":null,
     "durability":"async","default_timeout":600,"python_proceso":"/opt/historico/demo/.venv/bin/python",
     "runs_keep":50,"theme":"dark","video":{"aviso":"…","color":"0xE8C766","voz":"ef_dora","retratos":"/opt/historico/demo"}}
    ```
    El valor de `host` depende de la decisión del propietario D2. Indicar qué claves exigen reiniciar (`host`, `port`).

### B8. El contrato de eventos SSE ocupa una sola línea
- **Lentes:** implementation-readiness, structural, design
- **Dónde:** §5 l.228, §6 «Eventos», §7 l.294–296
- **Problema:** los colores de los nodos, la insignia ×N, el ⚡, el progreso por nodo, las aristas recorridas y los estados de la ejecución necesitan eventos que no están definidos. Faltan además:
  - la reconexión;
  - un límite de volumen: `debug` duplica `tasks`+`checkpoints`, y `values` repite el estado completo en cada paso;
  - cómo serializar `Interrupt`, `Send` y `BaseMessage`.
- **Propuesta:** añadir «§6.1 Eventos SSE»:
  - **Endpoint:** `GET /api/events?thread=<id>&after=<id>`. Formato `id:`/`event:`/`data:`; `: ping` cada 15 s; respeta `Last-Event-ID` y reenvía desde la tabla `events`.
  - **Sobre:** `{id, run_id, thread_id, ns, type, node, task_id, data, ts}`.
  - **Lista cerrada de `type`:**
    - `run.status {status, error?, traceback?}`
    - `node.start {attempt}`
    - `node.progress {value}`
    - `node.log {line}`
    - `node.partial {patch}`
    - `node.end {status: success|error|cached|interrupted, output, error?, duration}`
    - `interrupt {id, value, response_schema, node}`
    - `checkpoint {checkpoint_id, parent_id, step, next, source}`
    - `token {text}`
    - `thread.status {status}`
  - **Correspondencia con los modos de stream:** `tasks`→`node.start/end`; `custom`→`node.*`/`token`; `checkpoints`→`checkpoint`; `__interrupt__` en `updates`→`interrupt`.
  - **Modos de stream:** no usar `debug`; `values` solo al final; `version="v1"`. Con esto se elimina el pendiente «stream v2».
  - **Serialización:** `json.dumps(default=_enc)` (Interrupt→`{id,value,response_schema}`, Send→`{node,arg}`, BaseMessage→`model_dump()`, Path→`str`).
  - **Escritura de la traza:** por lotes cada 250 ms. Cola acotada por suscriptor: se descartan los eventos de progreso, nunca los de estado.
  - **Instantánea:** añadir `GET /api/threads/{id}/view` (estado por nodo: status, progreso, intentos, contador, último error) para que la GUI reconstruya los colores al recargar.

### B9. Casi ningún endpoint REST tiene cuerpo, respuesta ni códigos de error
- **Lentes:** implementation-readiness, structural
- **Dónde:** §6 l.258–271
- **Propuesta:**
  - **Regla global de errores:** `{"detail": str, "code": str, "line"?: int}`, con 400 validación, 404, 409 (`version_conflict`, `thread_busy`, `module_in_use`) y 422 sintaxis.
  - **Contratos mínimos de F1:**
    - `POST /projects {name, template}` → 201. El slug se genera con `[a-z0-9-]{1,48}`, añadiendo `-2`, `-3` si colisiona.
    - `PUT /projects/{slug}`: cuerpo con el `project.json` completo y la `version` cargada. Si no coincide, 409 `{code:"version_conflict", current}`; si coincide, se guarda como `version+1`.
    - `POST /threads/{id}/runs {input|null, resume|null, checkpoint_id?, interrupt_before?, interrupt_after?, step?}`. Como mucho uno de `input`/`resume`; los dos nulos significa «continuar». Devuelve 202 `{run_id, status:"pending"}`.
    - `GET /threads/{id}/state?checkpoint_id=` → `{values (planos), next, tasks, interrupts:[{id,value,response_schema}], checkpoint_id, parent_checkpoint_id, metadata, created_at}`.
    - `POST /threads/{id}/state {values, checkpoint_id?, as_node?}` → `{checkpoint_id}`.
    - `PUT /modules/{file}/source {source}` → 200 `{meta, config}` o 422 `{detail, line}`.
  - Definir también `/images` (campo multipart y carpeta de destino) y `/projects/import`.
  - Añadir una línea: «La API no pretende ser compatible con `langgraph_sdk` ni con la API de LangGraph Server».

### B10. Al detener, el kill puede acabar en reintento, y el orden de cancelación no está definido
- **Lentes:** design, inventive-catalyst, codebase-details, implementation-readiness
- **Dónde:** §3.1 l.126, §5 l.231–232 y l.247, criterio 3
- **Problema:**
  - Tras `killpg`, el subproceso sale con código ≠0, y según l.126 eso «pasa por retry/error_handler». La `RetryPolicy` relanzaría SadTalker y el criterio 3 fallaría de vez en cuando.
  - LangGraph convierte un `CancelledError` que lanza el propio nodo en `NodeCancelledError`, que se trata como un error normal (pregel/_retry.py).
  - Al revés, el `retry_on` por defecto **no** reintenta `RuntimeError`, `OSError` ni `ValueError` (_internal/_retry.py:1–29). Si el wrapper lanza `RuntimeError`, nunca habrá reintentos.
  - Con `durability="exit"`, Detener no deja checkpoints.
- **Propuesta:** especificar en §4.3/§5:
  > Detener = `task.cancel()` de la tarea que consume `astream` (nunca `killpg` primero). El wrapper hace `except asyncio.CancelledError: killpg(SIGTERM); await wait_for(proc.wait(), 1.5); killpg(SIGKILL); raise` y **siempre** vuelve a lanzar el CancelledError original. Una salida por señal con `stopping` activo cuenta como cancelación, no como error. Una salida ≠0 normal lanza `NodoProcesoError(Exception)` (clase propia), con las últimas 50 líneas del log, y el compilador pasa un `retry_on` explícito que la incluye. Después de cancelar se espera al flush del checkpointer y la ejecución pasa a `stopped`.

  Limitar la durabilidad a `async|sync`, o mostrar `exit` en la GUI como «no reanudable».

### B11. Copiar hilo, rollback y purga dependen de métodos que AsyncSqliteSaver no implementa
- **Lentes:** design, codebase-details, implementation-readiness, comprehension, inventive-catalyst
- **Dónde:** §3.5 l.192, §5 l.245–246, §6 `/threads/{id}/copy` y `/maintenance/prune`
- **Problema:** `acopy_thread`, `adelete_for_runs` y `aprune` solo existen en la clase base, donde lanzan `NotImplementedError` (checkpoint/base/__init__.py:355–419). El saver de SQLite solo implementa `adelete_thread` (sqlite/aio.py:618). Copiar hilo es de F1 (vista Hilos), así que fallaría en tiempo de ejecución.
- **Propuesta:**
  - «Copiar hilo = SQL propio en una sola transacción: `INSERT … SELECT` sobre las tablas `checkpoints` y `writes` cambiando `thread_id`». Confirmar las columnas con `.schema` en el spike.
  - «Rollback = no borrar nada: cancelar la ejecución y lanzar la nueva con `checkpoint_id = checkpoint_id_inicio` (una bifurcación). La rama abandonada queda marcada como `rolled_back` y oculta por defecto». Pasa a F2.
  - «Purga = solo hilos completos (`adelete_thread` + filas de studio.sqlite + carpeta de artefactos)».

### B12. Los artefactos se nombran por nodo y por ejecución, y eso rompe bifurcaciones, reintentos y `Send`
- **Lentes:** design, comprehension, inventive-catalyst, implementation-readiness
- **Dónde:** §2 l.86, §3.1 (`run_dir`), §4 l.209
- **Problema:**
  - v1 escribe nombres fijos (`voz.wav`, `retrato.png`, `rmtree(rd/"sadtalker")`).
  - Con `Send`, varias tareas del mismo nodo se pisan `_in_<nodo>.json`.
  - Bifurcar, reproducir o reanudar crea un `run_id` nuevo, y el estado acaba apuntando a varias carpetas.
  - Purgar «ejecuciones a conservar» deja checkpoints que apuntan a ficheros borrados.
  - Una bifurcación que reutilice la carpeta rompe el criterio 5 («rama original intacta»).
- **Propuesta:** «`ctx.run_dir` = `projects/<slug>/threads/<thread_id>/<node_id>/<task_id>/` (task_id sale de `runtime.execution_info`). Ahí van `_in.json` y las salidas. Las rutas en el estado son absolutas. La purga se hace por hilo». Como mejora opcional (F2), ver I24 sobre reutilizar artefactos.

### B13. El nodo de interrupt está definido dos veces y su respuesta no está definida
- **Lentes:** style, implementation-readiness, comprehension
- **Dónde:** §4 l.211–212, §8 l.339, criterio 1
- **Problema:**
  - El compilador tiene un caso especial para `kind: interrupt` y además existe el módulo `revision_humana.py`.
  - «Aplica la respuesta (`aprobado`, `valor`, `comentario`)» no dice qué clave se escribe ni qué ocurre si `aprobado=false`.
  - `{"campo","valor","mensaje","schema"}` es un `set` en Python, no un dict.
- **Propuesta:**
  > El comportamiento vive en `revision_humana.py` (modo local, usa `ctx.interrupt`). El compilador no tiene caso especial para `kind: interrupt`. `value = {"campo": cfg.campo, "valor": state[campo], "mensaje": cfg.mensaje}`; `response_schema = {"type":"object","properties":{"aprobado":{"type":"boolean"},"valor":{"type":"string"},"comentario":{"type":"string"}},"required":["aprobado"]}`. Si es true, escribe `valor` en `state[campo]`.

  Qué ocurre con `false` lo decide el propietario (D5). Añadir la regla: «`interrupt` es la primera operación del nodo, porque al reanudar el nodo se ejecuta desde el principio».

### B14. No hay plan de pruebas, ni fixtures, ni definición de terminado
- **Lentes:** implementation-readiness, structural, comprehension, style
- **Dónde:** §13 l.424–441
- **Problema:**
  - Los criterios se describen como «automatizables», pero no hay framework, ni ubicación, ni mediciones, ni tiempos.
  - Todos necesitan GPU real, con varios minutos por iteración.
  - Las fases 2 y 3 no tienen criterios.
- **Propuesta:** añadir «§13.1 Plan de pruebas»:
  - **Ubicación y ejecución:** `studio/tests/`, `.venv-studio/bin/pytest -q`, con marcadores `gpu` y `ui`. Por defecto se excluyen los `gpu`.
  - **Servidor de prueba:** fixture uvicorn en el puerto 8799 con `STUDIO_HOME` temporal.
  - **`prueba_lenta.py`:** proceso, sin GPU; duerme N s emitiendo `@@P`. Se usa para los criterios 2 y 3 sin GPU. El criterio 3 se ejecuta además una vez con SadTalker real.
  - **Fixture de vídeo rápido:** retrato existente `/opt/historico/demo/cleo_0.png`, texto de unas 10 palabras, `tam=256`, `mejora=false`, `quieto=true`.
  - **Mediciones:**
    - **Criterio 1:** `ffprobe` sale con 0, la duración es > 1 s y hay vídeo h264.
    - **Criterio 3:** a los 3 s, `pgrep -f SadTalker/inference.py` vacío y `nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits` ≤ max(300, línea base + 50), con `ollama ps` vacío.
    - **Criterio 6:** `run.status` con `GraphRecursionError`.
    - **Criterio 8:** igualdad del JSON ignorando `slug, created, updated, version`.
    - **Criterio 9:** Playwright recoge `console.error` y `pageerror` en cada ruta F1, a 1440×900 y 1024×768, sin desbordamiento horizontal.
  - **Casos límite:** `else` duplicado, join con un nodo inexistente, condición con sintaxis errónea, 409 por versión, `reject` del double-texting, salida no serializable.
  - **Tiempos:** medir las duraciones reales en el paso 1f y anotarlas; timeout de pytest de 15 min para `gpu`.
  - **Definición de terminado:** «fase N terminada = todos los tests `fN` pasan, `arrancar.sh` levanta en 8790 y v1 está archivado». Añadir al menos un criterio por funcionalidad en F2 y F3, o la frase «criterios a definir antes de empezar la fase».

### B15. Faltan dependencias y algunas no están instaladas
- **Lentes:** structural, codebase-details, design, comprehension, inventive-catalyst, implementation-readiness, zoom-out
- **Dónde:** §1 l.33, §5 l.251, §7 l.299, §8 l.335–338
- **Problema:**
  - `.venv-studio` **no** tiene `croniter`, `langchain-ollama`, `langchain`, `langchain-mcp-adapters`, `langgraph-cli`, `pytest` ni `playwright` de Python.
  - Sí tiene, sin estar documentados: `langchain_core` 1.8.2, `langsmith`, `aiosqlite` y `sqlite_vec`.
  - El modo de stream `messages` solo emite tokens desde chat models de LangChain (pregel/_messages.py). Un `llm_ollama` que llame a Ollama por HTTP no produce tokens por `messages`.
  - CodeMirror «sin build» y sin decir de dónde se carga.
- **Propuesta:** añadir «§1.1 Dependencias», con el comando `uv pip install --python studio/.venv-studio/bin/python …` por fase:
  - **F1:** `pytest pytest-asyncio playwright`. Playwright reutiliza el Chromium de `~/.cache/ms-playwright/chromium_headless_shell-1243` mediante `executable_path`.
  - **F2:** `langchain-ollama`.
  - **F3:** `croniter langchain-mcp-adapters`.

  Además, decidir y escribir:
  - «En F1, `llm_ollama.py` llama a `/api/generate` por HTTP y emite los tokens con `ctx.writer({'type':'token',…})` por `custom`. El modo `messages` y `ChatOllama` empiezan en F2 (Chat)».
  - «CodeMirror 5.65 vendorizado en `static/vendor/codemirror/`, sin CDN». En F1 basta con un `<textarea>` si se quiere simplificar.
  - Fijar `LANGSMITH_TRACING=false` en `arrancar.sh`.

### B16. No se dice qué ficheros se crean, cuáles se modifican y cuáles se archivan
- **Lentes:** structural, comprehension, style, implementation-readiness, zoom-out
- **Dónde:** l.3, §2 l.76–89, §12
- **Problema:**
  - `server.py`, `runner.py`, `static/index.html` y `arrancar.sh` ya existen, y el documento no dice si se reescriben, se adaptan o se archivan.
  - §12 archiva `nodes.py` pero no el `server.py` ni el `index.html` de v1.
  - Faltan en el árbol `templates/`, `legacy/`, `tests/` y `static/vendor/`.
  - «(+ app.js, app.css si se separa)» contradice §7, que ya decide separarlos.
- **Propuesta:** añadir «§2.1 Ficheros» como tabla `ruta | acción | fase`:
  - **Crear:** `compiler.py, engine.py, modules.py, db.py, static/app.js, static/app.css, static/vendor/…, modules/*.py, templates/*.json, settings.json, tests/…`.
  - **Modificar:** `runner.py` (ver B1) y `arrancar.sh` (`exec ./.venv-studio/bin/python server.py`, con `setsid nohup` como ahora).
  - **Sustituir:** `server.py` y `static/index.html` (la versión v1 sirve solo como referencia visual).
  - **Archivar en `legacy/` en el paso 1h:** `nodes.py`, `config.json`, `runs/`, y el `server.py` e `index.html` de v1.

  Añadir también la correspondencia de funciones a portar:
  - `nodes.guion` → `llm_ollama.py`
  - `voz` → `tts_kokoro.py`
  - `retrato` → `retrato_sdxl.py`
  - `lipsync` → `lipsync_sadtalker.py`
  - `montaje` → `montaje_video.py`

  La firma cambia de `(st, cfg, rd)` a `run(state, cfg, ctx)`. Cambian también los nombres de las llamadas: `rd`→`ctx.run_dir` y `P/S/log`→`ctx.progress/update/log`.

---

## Hallazgos importantes

### I1. Los asistentes contradicen la fase 1, y la clave de caché los multiplica
- **Lentes:** comprehension, style, inventive-catalyst, design, zoom-out
- **Dónde:** §3.5 l.188–190, §4 l.218, §6 Ejecuciones
- **Propuesta:**
  - **F1:** sin asistentes; `assistant_id` es opcional en la API.
  - **F2:** compilar **una vez por versión del proyecto**. Los `node_overrides` y el `context` se resuelven en ejecución desde `runtime.context`, con este orden: `default` de CONFIG → `config` del nodo → `node_overrides` → `settings[setting]` si el valor queda vacío. Así no hace falta un grafo por asistente, y la exportación es equivalente.
  - `context_schema` (lista de campos) se convierte en un `TypedDict(total=False)` generado dinámicamente.

### I2. El semáforo de GPU cuenta para el timeout, no cubre Ollama y choca con `max_concurrency`
- **Lentes:** design, inventive-catalyst, style
- **Dónde:** §3.1 `gpu`, §5 l.243–244, §14 l.445–447
- **Problema:**
  - Un nodo que espera el semáforo ya está dentro de su `TimeoutPolicy`, así que puede expirar sin haber arrancado.
  - Ollama usa VRAM, pero los nodos LLM son «local» sin `gpu`.
  - `generar_clip.py` no respeta un `asyncio.Semaphore` que vive dentro del servidor.
  - `max_concurrency=1` serializa también ramas de CPU, y en LangGraph limita tareas **dentro** de una ejecución, no entre ejecuciones.
  - Con `keep_alive=0` global, cada llamada al LLM recarga el modelo, lo que hace lentos la plantilla 3 y el modo Chat.
- **Propuesta:**
  - `META.gpu: "exclusive" | "shared" | false`, válido también en modo local. Los LLM usan `shared`.
  - Antes de un nodo `exclusive`, descargar los modelos que muestre `/api/ps` con `POST /api/generate {model, keep_alive:0}`. Se puede volver a `keep_alive="5m"`; esto queda a decisión del propietario (D6).
  - El reloj del timeout empieza **después** de adquirir el semáforo.
  - `max_concurrency=None` por defecto.
  - Lock entre procesos con `fcntl.flock` sobre `data/gpu.lock`, que toman `runner.py` y el CLI.
  - La GUI muestra «esperando GPU».

### I3. La recarga en caliente por mtime contradice la caché de grafos compilados
- **Lentes:** design, inventive-catalyst, zoom-out, style
- **Dónde:** §0 principio 2, §2 l.63, §4 punto 3 y punto 6
- **Problema:**
  - Hay dos mecanismos que se solapan: el wrapper recarga por mtime y la clave de caché incluye los mtimes. Una ejecución puede usar código distinto del que se compiló.
  - El mtime no es fiable con dos guardados en el mismo segundo ni con una restauración del historial.
  - `importlib.reload` en un servidor de larga duración deja estado viejo.
  - La clave no incluye `settings.json` ni los subproyectos.
- **Propuesta:**
  - Al empezar cada ejecución, guardar `sha256(source)` de cada módulo en `data/modsnap/<sha>.py` y cargarlo con `spec_from_file_location("m_<sha>")`, sin `reload`. Guardar `module_shas` en la ejecución.
  - Clave de caché = `(slug, project_version, sha de cada módulo, incluidos los de subproyectos)`. Los ajustes se resuelven siempre en ejecución.
  - Política al reanudar: usar el código actual y avisar «código cambiado desde el checkpoint».
  - Alternativa de zoom-out: compilar en cada ejecución (son milisegundos) y eliminar el punto 6.

### I4. TimeoutPolicy: nombres de campos y relación con el subproceso
- **Lentes:** codebase-details, implementation-readiness, design
- **Dónde:** §3.2 l.146, §1 l.53, §3.1 l.103
- **Propuesta:**
  - «`run`→`run_timeout`, `idle`→`idle_timeout`. El wrapper de proceso llama a `runtime.heartbeat()` o reenvía cada línea por `runtime.stream_writer`, y eso reinicia el `idle` (`refresh_on="auto"`). Solo funciona en nodos async, y el wrapper siempre lo es» (types.py:461–524, runtime.py:209).
  - Unificar la forma: `"timeout": {"run": s, "idle": s|null}` también en `META.defaults` (l.103 usa un escalar).
  - Quitar el punto de la lista de «pendiente de verificar».

### I5. Firma del wrapper y campos reales de `Runtime`
- **Lentes:** codebase-details
- **Dónde:** §1 l.51, §4 l.206
- **Propuesta:**
  - En §1: «`Runtime` (context, store, stream_writer, heartbeat, execution_info{thread_id, run_id, task_id, node_attempt…}, control; `previous` solo en la API funcional)».
  - En §4: «firma: `async def wrapper(state, runtime: Runtime, config: RunnableConfig)`; la inyección es por nombre de parámetro».

### I6. Arranque, apagado, procesos huérfanos y reconciliación (criterio 10)
- **Lentes:** structural, design, inventive-catalyst, implementation-readiness
- **Dónde:** §2, §5, criterio 10
- **Problema:**
  - Con `start_new_session=True`, un SadTalker huérfano sigue reteniendo VRAM si el servidor muere.
  - Las ejecuciones `running` y los hilos `busy` quedan con un estado falso.
  - El bus, el semáforo y `RunControl` viven en memoria.
- **Propuesta:** añadir «§5.1 Arranque y apagado»:
  - **Lifespan de FastAPI:** abre y cierra saver, store, cache (`await store.setup()`) y la conexión a `studio.sqlite`.
  - **Al arrancar:**
    1. `killpg` de los `pgids` guardados que sigan vivos, comprobando antes `/proc/<pid>/cmdline`.
    2. `running`→`stopped`.
    3. `pending`→`error` («servidor reiniciado»).
  - **Al apagar:** `killpg` de todos los subprocesos.
  - **Estado del hilo:** `busy/interrupted/idle` no se guarda; se calcula con las ejecuciones activas y `aget_state`.
  - **Uvicorn:** un solo worker y sin `--reload`.

### I7. La migración de v1 no coincide con los datos reales
- **Lentes:** codebase-details, comprehension, structural, style, zoom-out
- **Dónde:** §12 l.408–411
- **Problema:**
  - Solo 2 de los 4 runs tienen `final.mp4` (20261118-101057 y 20261118-101823).
  - `state.json` contiene rutas absolutas `/opt/historico/demo/studio/runs/…`, que se rompen al mover la carpeta a `legacy/`.
  - El modelo de hilo no tiene marca de solo lectura.
  - `config.json` está organizado por nodo v1, y el prompt usa `{palabras}` sacado de `cfg`; `ctx.fill` solo rellena desde el estado (runner.py:37–39).
- **Propuesta:** primero mover a `legacy/runs/`, reescribir el prefijo de las rutas e importar solo las carpetas que tienen `final.mp4`, como hilos `legacy=1` con una fila en `outputs`. Las acciones de ejecución quedan deshabilitadas en esos hilos.

  Tabla de correspondencia `config.json` → plantilla 1:
  - `breakpoint` → `breakpoint.before`
  - `revision.activo=false` → la condición de revisión
  - `palabras` → la entrada de la ejecución
  - `lipsync.tam="512"` (cadena) → número

  Alternativa de zoom-out: no importar nada y que Vídeos muestre también `legacy/runs/*/final.mp4` como ficheros. Lo decide el propietario (D7).

### I8. El prototipo es efímero, mezcla APIs ejecutadas con APIs solo comprobadas por firma, y los pendientes no tienen plan B
- **Lentes:** comprehension, structural, codebase-details, implementation-readiness, design, style
- **Dónde:** §1 l.44–54
- **Problema:**
  - `scratchpad/lgtest/t.py` es una ruta relativa dentro de un scratchpad de sesión que la sesión nueva no tendrá.
  - El prototipo solo ejecutó una parte de las APIs. Sin `response_schema`, sin `as_node`, sin caché, sin Send, sin subgrafos y sin `interrupt_before`.
- **Propuesta:**
  - Copiar el prototipo a `studio/docs/prototipo_lg.py`.
  - Separar en el texto «probado en ejecución: …» de «existe en el paquete (firma comprobada): …».
  - Convertir los pendientes en el **paso 1a (spike)**, con resultado esperado y alternativa para cada uno:
    - drain con un interrupt en el superstep: se espera `status=interrupted`, no `drained` (bloquea F1);
    - cancelar `astream` con una tarea paralela ya terminada: sus pending writes se conservan y no se vuelve a ejecutar al reanudar (criterios 2 y 3);
    - `astream(input)` sobre un hilo con tareas `next` pendientes;
    - `error_handler`: antes o después de los reintentos;
    - `stream_writer` llamado desde `to_thread`;
    - breakpoints en subgrafos.
  - Los pendientes «idle_timeout» y «stream v2» ya quedan resueltos por I4 y B8.

### I9. La semántica de cada `kind` está repartida o falta, y `entrada`/`salida` no están definidos
- **Lentes:** comprehension, structural, implementation-readiness, zoom-out
- **Dónde:** §3.1 l.98, §3.4, §4, §8 l.333–338
- **Propuesta:** añadir en §4 una tabla «kind → qué hace el compilador / firma y retorno de `run`»:
  - **F1:**
    - `task`, `input` y `output` se comportan como `task`.
    - `interrupt` es un `task` con icono propio (B13).
  - **F2:**
    - `router` devuelve `str | list[str]` con ids de nodo destino.
    - `subgraph`: ver I10.
    - `tools`/`agent`: hay que definir qué es una herramienta, por ejemplo funciones `@tool` en `modules/tools/*.py`. Si no se define, se aplaza.

  Comportamiento de los módulos de entrada y salida:
  - **`entrada.py`:** CONFIG `valores` (json); devuelve las claves de `valores` que **no** estén ya en el estado, de modo que gana la entrada de la ejecución.
  - **`salida.py`:** CONFIG `mostrar=[{"clave","como": texto|video|audio|imagen}]`; devuelve None. En `node.end`, el motor inserta filas en `outputs` para los ficheros que existan, y de ahí se alimentan Vídeos y la pestaña Salida.

### I10. Subgrafos: listas `append` duplicadas, checkpointer y ciclos
- **Lentes:** design, inventive-catalyst
- **Dónde:** §4 punto 3 (`kind: subgraph`)
- **Problema:**
  - Un hijo con `append` devuelve la lista completa y el padre la vuelve a concatenar, así que los elementos salen duplicados.
  - Las claves que solo están en uno de los dos esquemas se pierden sin aviso.
  - No se dice cómo compilar el hijo, ni cómo detectar ciclos A→B→A.
  - `interrupt_before/after` no llega al hijo (está por verificar).
- **Propuesta (F2):**
  - El hijo se compila con `checkpointer=None` y con `output_schema` limitado a las claves `output: true`.
  - Se rechazan `append`/`sum` en claves compartidas.
  - Se validan los ciclos como un DAG.
  - El hash del hijo entra en la clave de caché.
  - Breakpoints en subgrafos: fuera de alcance o compilados en el hijo.

### I11. Continuar, reanudar, responder, paso a paso y `as_node`: falta una correspondencia única
- **Lentes:** style, implementation-readiness, design
- **Dónde:** §5 l.229–242, §6 Ejecuciones, §7 barra superior
- **Propuesta:** una tabla y un solo verbo, **Continuar**:

  | Situación | Llamada |
  |---|---|
  | `drained`, `stopped` o breakpoint | `POST runs {input:null}` |
  | Interrupt humano (**Responder**) | `POST runs {resume:{<interrupt_id>: valor}}`, siempre por id |
  | Reproducir desde un checkpoint | `{checkpoint_id}` |
  | Bifurcar | `POST state {checkpoint_id, values, as_node}` y después `POST runs {checkpoint_id:<nuevo>}` |

  Reglas adicionales:
  - **Paso a paso:** `step:true` equivale a `interrupt_before="*"`. Mientras el modo esté activo, Continuar envía `step:true`. Solo afecta al nivel superior del grafo.
  - **`as_node` al editar en un breakpoint:** el nodo que escribió el checkpoint (`metadata.writes`); si son varios, se aplica a todos. Para bifurcar sin disparar aristas, evaluar `as_node="__copy__"` (main.py:1885).
  - **Re-ejecutar nodo:** elegir una sola opción: «reproducir desde el checkpoint más reciente de la rama cuyo `next` contenga el nodo».

### I12. Los estados (status) mezclan breakpoint e interrupt humano, y no hay transiciones definidas
- **Lentes:** style, comprehension, inventive-catalyst, codebase-details
- **Dónde:** §3.5 l.191–194, §5 l.229–234
- **Propuesta:**
  - Usar `status: interrupted` con `motivo: breakpoint | humano`, porque la GUI muestra un formulario en un caso y el botón Continuar en el otro.
  - Añadir un estado intermedio «pausando (esperando a <nodo>)». El drain espera a **todas** las tareas del superstep (pregel/_loop.py:684–696); en la plantilla 1, voz y retrato. Pausar durante SadTalker puede tardar unos 10 minutos.
  - Tabla de transiciones de la ejecución al hilo: `stopped`/`drained`/`success` → hilo `idle`; `error` → `error`.
  - Usar «estado» solo para el estado del grafo y `status` para el ciclo de vida.

### I13. El código local síncrono no se puede cancelar y puede agotar el executor
- **Lentes:** design, comprehension, implementation-readiness
- **Dónde:** §4 punto 3 (modo local), §14 l.448
- **Problema:**
  - Un `run` síncrono dentro de `to_thread` no se puede matar.
  - El executor por defecto tiene unos 10 workers en esta VM y `SqliteCache` también los usa.
  - Unos pocos nodos colgados bloquean todo el servidor. El riesgo de l.448 no queda mitigado.
- **Propuesta:**
  - Un `ThreadPoolExecutor` propio y acotado para los módulos locales.
  - Timeout obligatorio en `http_peticion` y `llm_ollama`.
  - `codigo_python.py` con `exec: proceso` (puede usar el intérprete 3.12).
  - El AST rechaza `import torch` en los módulos locales.
  - Documentar: «Detener no interrumpe un `run` síncrono local; su resultado se descarta».

### I14. La exposición en red está decidida y abierta a la vez
- **Lentes:** comprehension, style, structural, implementation-readiness, zoom-out
- **Dónde:** §11 l.402 (127.0.0.1), §14 l.453 («¿abrir a la LAN?»), v1 en 0.0.0.0 (server.py:506, arrancar.sh)
- **Problema:** si el valor por defecto es localhost, el propietario pierde el acceso desde su navegador. La pregunta abierta sobre `generar_clip.py` ya está respondida en l.412.
- **Propuesta:** el propietario decide (D2). Hay que borrar la sección «Abiertas» y escribir la decisión en §11. Sustituir la pregunta sobre `generar_clip.py` por «se conserva sin cambios».

### I15. El double-texting tiene cuatro estrategias sin semántica definida
- **Lentes:** zoom-out, design, comprehension, inventive-catalyst
- **Dónde:** §5 l.244–246
- **Propuesta:**
  - **F1:** solo `reject` (409 `thread_busy`).
  - Si el propietario mantiene el resto (D1):
    - `enqueue` = ejecuciones `pending` en SQLite procesadas por un worker por hilo.
    - `interrupt` = cancelar y lanzar desde `checkpoint_id_inicio`.
    - `rollback` = bifurcación (B11).
  - En la GUI, mostrar `interrupt` como «interrumpir y lanzar».

### I16. Alcance: paridad con Studio o paridad con LangGraph Platform
- **Lentes:** zoom-out, structural, style
- **Dónde:** §0 l.8, §10, §6 (Asistentes, Datasets, Programaciones)
- **Problema:** para un solo usuario, los asistentes versionados, los crons, los datasets/experimentos y el double-texting completo duplican cosas que ya existen o que el caso de vídeo no necesita. Un cron del sistema puede llamar a `POST /runs` sin coste.
- **Propuesta:**
  - Cambiar l.8 por «cubrir las funcionalidades listadas en §10; alcance completo al terminar la fase 3».
  - Mover a §0 una lista de «No objetivos».
  - Añadir «§2.2 Alternativas descartadas»:
    - un único proceso 3.10, porque falla el interrupt async;
    - pasar el venv pesado a 3.12, porque rompe torch 2.4 y los parches;
    - `langgraph dev` + Studio oficial, porque depende de la nube.
  - Cuánto se recorta lo decide el propietario (D1, D3).

### I17. La interfaz de `ctx` y la precedencia de `cfg` no están definidas
- **Lentes:** style, codebase-details, comprehension
- **Dónde:** §3.1 l.105–122
- **Propuesta:** después de l.110, añadir:
  > `cfg` = un valor por cada `key` de CONFIG, resuelto en este orden (gana el último): `default` → `config` del nodo → `node_overrides` (F2). Si el valor queda vacío y el campo tiene `setting`, se usa `settings[setting]`, resuelto en el servidor antes de escribir `_in.json`. `ctx.update` = vista previa por `custom`, no modifica el estado. `fill` sustituye `{clave}` por `state[clave]`. `path(p)` es relativa a `base` = `/opt/historico/demo`. `progress(x)` con x en [0,1]. `stream(...)` devuelve el código de salida y no lanza excepción.

  Definir también «validación suave» como «solo avisos; nunca bloquea». Definir «módulos LLM» con `META["llm"]=True`.

### I18. Hay términos con más de un significado que pueden llevar a implementar mal
- **Lentes:** style, structural
- **Dónde:** todo el documento
- **Propuesta:**
  - **asistente:** l.282 «asistente modal» → «diálogo guiado».
  - **hilo:** l.207 «en hilo si es sync» → «en un *worker thread* del SO».
  - **plantilla:** reservarla para las plantillas de proyecto. Para módulos nuevos, «esqueleto de módulo»; para prompts, «texto con marcadores `{clave}`».
  - **fork:** separar «copiar hilo», «bifurcar» y «duplicar módulo», y quitar la palabra «fork».
  - **configuración/ajustes:** «ajustes» = `settings.json`.
  - **ejecución:** la insignia ×N significa «veces que el nodo se ha ejecutado dentro de esta ejecución».
  - **límite:** l.229 «límite de superstep» → «al terminar el superstep en curso».
  - **clave:** «clave de acceso» → «token de acceso».

### I19. Pseudocódigo que en Python significa otra cosa
- **Lentes:** style, design
- **Dónde:** l.206 `metadata={"module","label"}`, l.211, l.225 `{"thread_id","checkpoint_id"?}`
- **Problema:** son literales de `set` en Python, y una sesión podría copiarlos tal cual.
- **Propuesta:** escribirlos como dicts, por ejemplo `metadata={"module": <archivo>, "label": <etiqueta>}`. Añadir a §3: «`{a, b}` en este documento describe campos, no es un literal de Python».

### I20. Reglas de aristas y condiciones incompletas
- **Lentes:** style, comprehension, design, implementation-readiness
- **Dónde:** §3.4 l.171–184, §4 punto 1
- **Propuesta:** añadir estas reglas:
  - «`else` se toma solo si ninguna otra condición es verdadera. Si ninguna es verdadera y no hay `else`, es un error en el nodo origen».
  - «Sin arista desde `__start__`: START → todos los nodos `kind: input`; si no hay ninguno, todos los nodos sin aristas entrantes; si tampoco hay, error de validación».
  - «`src` como lista junto con `cond` es un error de validación».
  - «Builtins permitidos: `len, min, max, abs, int, float, str, bool, round, any, all, sorted, sum`, más `True/False/None`».
  - «Aviso de validación: un join cuya fuente está detrás de una rama condicional no se dispara nunca».

### I21. Serialización entre intérpretes y tamaño del estado
- **Lentes:** design, inventive-catalyst
- **Dónde:** §14 «Serialización», §3.1
- **Problema:** los `messages` de LangChain no son JSON y `runner.py` (3.10) no tiene `langchain_core`. Además, `data` se copia entera en cada checkpoint.
- **Propuesta:**
  - A los nodos de proceso solo se les pasan las claves de `META.inputs`.
  - Los mensajes se convierten con `messages_to_dict`.
  - Toda salida pasa un round-trip por `json` y, si falla, da un error claro.
  - Aviso para valores de más de 1 MB.

### I22. La exportación a `.py` necesita un runtime, y eso afecta al diseño del compilador desde ya
- **Lentes:** inventive-catalyst, design, zoom-out
- **Dónde:** §0 principio 1, §4 l.220–221
- **Problema:** el `.py` «autónomo» depende de los wrappers, del router de condiciones y de `runner.py`. Un exportador que genere código sería un segundo compilador.
- **Propuesta:**
  - Escribir ya en §4 que `compiler.py` debe poder importarse sin FastAPI y exponer `build(project, *, checkpointer=None, store=None)`.
  - La exportación es una carpeta con `studio_runtime.py`, `project.json`, los módulos y un `graph.py` de 10 líneas, y `langgraph.json` apunta a `graph.py:graph`.
  - Zoom-out propone adelantar la exportación de `langgraph.json` como vía de escape (D3).

### I23. `ctx.store` en modo proceso: el protocolo `@@STORE` es un segundo canal RPC
- **Lentes:** inventive-catalyst, zoom-out
- **Dónde:** §3.1 l.121–122
- **Propuesta:**
  - Eliminar `@@STORE`.
  - Lecturas: se declaran en `META["store_reads"]` y el wrapper las precarga en `_in.json`.
  - Escrituras: van en el resultado (`@@R {"update":…, "store_put":[[ns,key,val]]}`) y el wrapper las aplica.

### I24. Detener o un fallo pierden un SadTalker de 10 minutos (mejora opcional)
- **Lentes:** inventive-catalyst
- **Dónde:** §5 l.229–232
- **Propuesta (F2):**
  - Artefactos direccionados por contenido: `sha256(código del módulo, cfg resuelta, inputs, hash de los ficheros de entrada)`. Si existe `_done.json`, el resultado se reutiliza sin lanzar el subproceso.
  - `ctx.stage(nombre)` para saltar fases de SadTalker ya completadas.
  - Purga de artefactos que ningún checkpoint referencia.

---

## Hallazgos menores

1. **Caché de nodos (F2) con la clave equivocada.** La clave real es `qualname del wrapper + nombre de nodo + pickle de la entrada` (pregel/_algo.py). No incluye ni la config, ni el código, ni el proyecto, así que cambiar la voz devolvería el audio antiguo. Usar un `key_func` propio con slug, node_id, sha del módulo, cfg efectiva e inputs, y comprobar al acertar que las rutas existen. *(design)* Se deja como menor solo porque es de F2. Al llegar a F2 hay que tratarlo como **importante**.
2. **Volumen de trazas.** Quitar el modo `debug`; agregar los tokens por tarea. *(design; incluido en B8)*
3. **`create_react_agent` está deprecado** (chat_agent_executor.py:274). Funciona, pero emite un aviso. Alternativa: `ToolNode` + `tools_condition`. *(codebase-details)*
4. **Imports exactos:**
   - `from langgraph.cache.sqlite import SqliteCache` (`SqliteCache(path=…)`);
   - `AsyncSqliteStore` en `langgraph.store.sqlite.aio`, que necesita `await store.setup()`.

   *(codebase-details)*
5. **Búsqueda semántica:** usar una función propia sobre `/api/embed` de Ollama (`index={'dims':768,'embed':fn}`). La forma `'ollama:modelo'` exige el paquete `langchain`. *(codebase-details)*
6. **Canal de protocolo dedicado.** Opcional: fd 3 mediante `pass_fds`, para que una línea de una librería que empiece por `@@` no corrompa el protocolo. *(design, inventive-catalyst)*
7. **Liberar VRAM al terminar cada nodo** obliga a recargar SDXL o Kokoro cada vez (20–60 s). Dejarlo escrito como decisión consciente y mencionar «workers persistentes» como posible F3. *(design)*
8. **Borrado de un hilo en cascada:** checkpoints, filas de `studio.sqlite`, artefactos y caché. Copia de seguridad con `VACUUM INTO`. *(design)*
9. **Hilos frente a versiones del proyecto.** Un hilo de la v7 que se reanuda con la v8 puede tener en `next` nodos que ya no existen. Guardar `projects/<slug>/versions/<v>.json` y avisar al reanudar. *(design)* Se puede aplazar a F2.
10. **`META.kind`:** limitar F1 a `task/input/output/interrupt`. *(zoom-out)*
11. **Unidades:** timestamps en epoch, en segundos como float; `tokens = {entrada, salida}`; `<ts>` de `.historial` = `YYYYMMDD-HHMMSS`. *(style)*
12. **Valores de enum y claves JSON en un solo idioma.** Por ejemplo `exec: local|subprocess` y cron `{thread: "new"|id, enabled}`. Así se evita el choque entre `proceso` y la pestaña Proceso. *(style)*
13. **«Responsive»:** cambiar «en móvil, solo lectura razonable» por «ancho mínimo 1024 px; por debajo, solo visualización». *(style)*
14. **Nombres explícitos:**
    - «los cuatro últimos» (l.353) → `tts_kokoro.py, retrato_sdxl.py, lipsync_sadtalker.py, montaje_video.py`;
    - extensiones de medios: `.mp4 .webm .wav .mp3 .png .jpg .jpeg .webp`;
    - `GET /images` lee la carpeta de retratos de los ajustes.

    *(style)*
15. **Referencia de Kokoro:** usar `voz()` de `nodes.py` (con `repo_id`); `generar_clip.py` es la versión CLI. *(codebase-details)*
16. **§7 sin subtítulos:** convertir sus apartados en `### 7.1 Menú lateral`, `### 7.2 Inicio`, etc. *(structural)*
17. **Seguridad repetida en tres sitios** (§6, §7, §11). Dejar las reglas completas en §11 y poner referencias en los otros dos. *(structural)*
18. **Cabecera.** Cuando el documento se apruebe, cambiar a «Aprobado para implementación» y añadir: «ante conflicto entre secciones, mandan §3 y §13». *(structural)*
19. **«Exportación alternativa (fase 3)»** (l.395) no aparece en la lista de la fase 3 (l.421). *(style)*
20. **Exageraciones:**
    - «matar al instante» → «en < 3 s»;
    - «de verdad» (l.14) → eliminar;
    - «foto del estado» → «instantánea del estado».

    *(style)*
21. **`ctx.interrupt` en modo local:** documentar que, al reanudar, el nodo se vuelve a ejecutar desde el principio. Hay que llamarlo al inicio del nodo, antes de cualquier efecto. *(design, inventive-catalyst)*
22. **Compatibilidad de API:** escribir que la API no es compatible con `langgraph_sdk` ni con Agent Chat UI, para que nadie intente igualarla. *(zoom-out; incluido en B9)*

---

## Decisiones que necesitan al propietario

1. **D1. Qué significa «todo lo que hace LangGraph Studio».** Hay dos lecturas:
   - **Paridad de depuración:** grafo, hilos, interrupts, time travel y edición del estado.
   - **Paridad con LangGraph Platform:** asistentes versionados, las cuatro estrategias de double-texting, crons, datasets y experimentos.

   De esto depende si I15/I16 se recortan o solo se aplazan.
2. **D2. Red por defecto.** v1 escuchaba en `0.0.0.0` y el borrador propone `127.0.0.1`. Opciones: `0.0.0.0` como en v1 (con token obligatorio, o sin él) o solo localhost.
3. **D3. Uso ocasional de LangGraph Studio en la nube** contra un `langgraph dev` local, a pesar del principio «sin nube». Si se acepta, la exportación `langgraph.json` se adelanta y sobra buena parte de lo marcado como «Propio» en §10.
4. **D4. Primera entrega.** Opción 1a: motor + GUI mínima con el vídeo funcionando. Opción 1b: editor visual completo. También hay que decidir si el diálogo «Crear vídeo nuevo» y `/quick-video` entran en la fase 1.
5. **D5. Revisión humana en la plantilla 1:**
   - ¿solo cuando el texto lo genera el LLM, o también con texto fijo?
   - Si se rechaza (`aprobado=false`), ¿termina con error, vuelve al guion con el comentario, o sigue igualmente?
6. **D6. Ollama y VRAM.** `keep_alive=0` siempre (más seguro, pero las conversaciones y los bucles LLM son lentos) o un `keep_alive` normal con descarga explícita antes de cada nodo GPU.
7. **D7. Runs de v1.** Importarlos como hilos de solo lectura o, más simple, archivarlos y mostrar sus vídeos como ficheros en la galería.

## Decisiones que puede tomar el implementador (con la recomendación de esta revisión)

- Protocolo con `@@R` y `@@S` como vista previa, y modificación de `runner.py` (B1).
- Modelo de estado: opción A, sin `data` (B5).
- `error_to` aplazado a F2 con el handler `NodeError` (B6).
- DDL, `settings.json`, SSE, REST y códigos de error (B7–B9).
- Orden de cancelación al detener y `NodoProcesoError` (B10).
- Copiar hilo y purgar con SQL; rollback como bifurcación (B11).
- Carpeta de artefactos por tarea (B12).
- Criterio 4 con el rótulo leído del estado (B4).
- Ids de las plantillas y su JSON literal (B3).
- Plan de pruebas, puerto 8791 durante el desarrollo y definición de terminado (B14, B2).
- Dependencias: tokens por `custom` en F1 y CodeMirror vendorizado (B15).
- Semáforo GPU compartido/exclusivo con el timeout contado después de adquirirlo (I2).
- Instantáneas de módulos por sha (I3).
- Reconciliación al arrancar (I6).
- Terminología y pseudocódigo (I18, I19).

---

## Anexo A. Afirmaciones del documento comprobadas en el código instalado

- **Versiones:** langgraph 1.2.22, checkpoint 4.3.2, checkpoint-sqlite 3.1.3, prebuilt 1.1.2, Python 3.12.3. `.venv` tiene Python 3.10.20 con torch 2.4.1+cu121, diffusers 0.31.0, transformers 4.46.3, kokoro 0.9.4 y setuptools 69.5.1.
- **El hallazgo de 3.10 es coherente con el código:** `ASYNCIO_ACCEPTS_CONTEXT = sys.version_info >= (3, 11)`.
- **`add_node`** acepta `retry_policy, cache_policy, timeout, defer, error_handler, destinations, metadata, trace_policy`. `destinations` solo afecta al dibujo.
- **Interrupts y Command:** `interrupt(value, *, response_schema=…)`. `Command(resume={interrupt_id: valor})` admite un mapping. `Overwrite` existe.
- **`astream`** admite `context, stream_mode, interrupt_before/after ("*"), durability, control, subgraphs, version`.
- **Control y errores:** `RunControl.request_drain()` → `GraphDrained`. Existen `NodeTimeoutError`, `GraphRecursionError`, `InMemoryCache`, `SqliteCache`, `AsyncSqliteStore`, `AsyncSqliteSaver` y `@entrypoint/@task`.
- **SadTalker:** los parches están aplicados y hay checkpoints para 256 y 512.
- **v1:** puerto 8790, host 0.0.0.0, `killpg` con `start_new_session=True`.
- **`runner.py`:** el orden del argv, `_in_<nodo>.json`, `@@P`/`@@S`, el log, el tratamiento de `\r` y el `Ctx` básico coinciden con lo esperado, con las diferencias que se señalan en B1.
- **`nodes.py`:** coincide con §8 (fases de lipsync, montaje 16:9 con `-progress`, retrato existente o SDXL con offload).
