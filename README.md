# Sistema de estudio multiagente

Aplicación para estudiar con agentes de IA. Cada sesión construye un contexto de estudio a partir de lo que describe el usuario (objetivo, nivel previo y material). Si el contexto no es suficiente, el sistema hace preguntas de aclaración. Después arma un plan (temario y especialistas elegidos de un catálogo de arquetipos: explicador, entrenador de ejercicios, evaluador, analista de documentos, investigador, tutor de código) y un profesor principal que consulta a esos especialistas como herramientas, verifica la matemática con SymPy, deja lo importante en una pizarra y registra el progreso del alumno por tema.

El usuario puede subir PDFs de dos tipos:

- **`rag`**: el documento se extrae con Docling, se trocea e indexa en Chroma. Los agentes consultan los fragmentos relevantes de la sesión.
- **`study_map`**: se genera un mapa de estudio (resumen, temas, fórmulas, ejercicios y conceptos). El contexto de cada turno solo incluye el resumen y los temas; los agentes piden el resto con `get_study_material`.

Los PDFs se procesan en segundo plano: la subida responde con estado `queued` y pasa por `processing` hasta `ready` o `failed`.

## Funciones de estudio

### Pizarra

Es un espacio persistente junto al chat para lo que el alumno consultará después: fórmulas, pasos, tablas, código y gráficos. Solo escribe el agente: el profesor principal usa `board_add`, `board_edit` y `get_board`, y los especialistas `explicador`, `entrenador_de_ejercicios`, `evaluador` y `tutor_de_codigo` usan `board_add`. Cada bloque guarda su autor.

- Los cambios llegan en vivo por el evento `board_patch` y se guardan en el checkpoint de la sesión al terminar el turno. Si el turno falla, la pizarra vuelve a su estado anterior.
- Los gráficos se muestrean en el servidor con la tool `math`: las expresiones pasan por su lista blanca y el cliente solo dibuja los puntos. No hay zoom interactivo.
- Un bloque con el mismo contenido que otro se rechaza, y cada especialista le informa al profesor principal qué agregó para que no lo repita.
- Los límites se configuran con las variables `BOARD_*`. Los puntos de un gráfico no cuentan para `BOARD_MAX_BLOCK_CHARS`.

### Repaso espaciado

`record_progress` recibe el número del tema en el temario, y cada evaluación se guarda en `data/app.db` con su historial y su fecha de repaso. El calendario es un sistema Leitner con cinco cajas:

| Caja | 0 | 1 | 2 | 3 | 4 |
|---|---|---|---|---|---|
| Días hasta el repaso | 1 | 3 | 7 | 14 | 30 |

- `dominado` sube una caja, hasta la última.
- `en_progreso` mantiene la caja.
- `con_dificultad` vuelve a la caja 0.

Cada turno recibe el progreso guardado y una sección `REPASOS PENDIENTES` con hasta 5 temas vencidos, y el profesor propone uno breve antes de seguir. El progreso se guarda aunque el cliente se desconecte a mitad de turno.

| Endpoint | Contenido |
|---|---|
| `GET /chats/{id}/progress` | Temario de la sesión y, por tema evaluado, estado, caja, fecha de repaso y últimas revisiones. |
| `GET /reviews/due?limit=20` | Repasos vencidos de todas las sesiones, con su `chat_id` y su `chat_title`. `limit` va de 1 a 100. Los repasos de sesiones sin transcripción no se devuelven. |

### Python en el navegador

El alumno ejecuta Python en el cliente con Pyodide, dentro de un Web Worker. El servidor nunca ejecuta código.

- El código del agente nunca corre solo: "Abrir en Python" lo copia al panel y ejecutarlo requiere un clic, porque ese código puede venir influido por un PDF.
- El worker no recibe el token ni la dirección del backend.
- Compartir el resultado con el tutor es explícito: "Enviar al tutor" lo deja en el cuadro de mensaje para que el alumno lo revise y lo envíe.

Los detalles están en [client/README.md](client/README.md#python-con-pyodide).

### Modo profundo

El botón «Profundo» del cuadro de mensaje activa, solo para esa sesión, un profesor principal construido con [Deep Agents](https://docs.langchain.com/oss/python/deepagents/overview) (`create_deep_agent`), pensado para cálculos complejos e investigaciones largas. Está apagado por defecto y se apaga al recargar el cliente.

- Planifica el turno con `write_todos` y guarda resultados intermedios en archivos virtuales (`write_file`, `read_file`, `edit_file`, `ls`, `glob`, `grep`). Esos archivos viven solo en memoria durante el turno: no tocan el disco, y el modo no tiene `execute`.
- Además de los especialistas del plan, delega con `task` en `general-purpose`, un investigador de solo lectura (`math`, `search_documents`, `get_study_material` y `web_search`) que no ve la conversación ni escribe en la pizarra ni en el progreso.
- Tiene su propio tope: `DEEP_SUPERVISOR_MAX_MODEL_CALLS` llamadas al modelo y `DEEP_SUPERVISOR_RECURSION_LIMIT` pasos. Es más lento y más caro que un turno normal; el panel de uso y `scripts/usage_report.py` muestran su costo, y sus tools aparecen en `tool_usage`.
- Cambiar el interruptor reconstruye el supervisor de la sesión desde el turno siguiente. En la preparación y las aclaraciones no aplica.

### Modelos por rol

Desde la interfaz se elige el modelo de cada rol entre los del catálogo de OpenRouter, para todas las sesiones o solo para una. El orden de prioridad es: sesión, global, `MODEL_<ROL>` y valor por defecto. La clave sigue solo en `.env` y nunca llega al cliente.

- `supervisor`, `subagent_light`, `subagent_heavy` y `context_classifier` exigen un modelo con tools. `factory` y `summarizer` solo necesitan texto.
- El catálogo se guarda 1 h en memoria. Si OpenRouter falla, se usa la última copia. Las variantes `:batch` se excluyen, porque no responden en `chat/completions`.
- Un cambio aplica desde el turno siguiente, y el supervisor se reconstruye si cambió alguno de sus modelos.
- `temperature` y `reasoning_effort` se omiten en los modelos que no los soportan.
- El nivel de razonamiento se elige por rol, con la misma prioridad que el modelo: sesión, global, `REASONING_<ROL>` y valor por defecto. El panel solo ofrece los niveles que el catálogo informa para ese modelo. Si un nivel guardado no existe en el modelo elegido, se prueba el siguiente; si ninguno sirve, no se envía y el modelo usa su nivel por defecto.
- Si un modelo guardado desaparece del catálogo o deja de cumplir los requisitos, se usa el siguiente en la prioridad, queda en el log y la interfaz lo marca como no disponible.
- Los embeddings quedan fuera del selector, porque cambiarlos invalidaría los vectores ya guardados.

| Endpoint | Contenido |
|---|---|
| `GET /models?role=&q=` | Modelos aptos para un rol, con precio por millón de tokens y contexto. |
| `GET` y `PUT /models/settings` | Modelo efectivo de cada rol y ajuste global. |
| `GET` y `PUT /chats/{id}/models` | Modelo efectivo de cada rol en una sesión y su ajuste. |
| `PUT /models/effort` y `PUT /chats/{id}/effort` | Nivel de razonamiento de un rol, global o de la sesión. |

El `PUT` recibe `{"role": "...", "model": "..."}`, valida contra el catálogo y con `model: null` quita el ajuste.

### Uso de contexto, tokens y costo

La barra superior de cada sesión muestra el contexto usado, el límite del modelo, el porcentaje y el costo, por ejemplo `24,3k / 200k · 12 % · $0,0421`. Al hacer clic se abre el detalle por rol y modelo. Los datos se actualizan tras cada llamada al modelo y se guardan en `app.db`.

- **Contexto usado:** entrada más salida de la última llamada del supervisor. Los subagentes no cuentan, porque cada uno trabaja con su propio contexto.
- **Límite de contexto:** el `context_length` del modelo de esa llamada en el catálogo. Si se desconoce, solo se muestran los tokens, sin porcentaje.
- **Costo de sesión:** suma de todas las llamadas, incluidos clasificador, planificador, resúmenes, subagentes y procesamiento de documentos. Los turnos y documentos fallidos también cuentan, porque ese gasto ocurrió.
- **Costo estimado (`≈`):** si OpenRouter no informa el costo, se calcula con los precios del catálogo. La estimación no descuenta la caché, así que puede quedar alta.
- **Costo desconocido (`$?`):** sin precio conocido no se asume cero. Si solo falta una parte, se muestra lo conocido seguido de `+ $?`.
- **No incluye** embeddings ni búsquedas web.
- **No es retroactivo:** las sesiones anteriores a esta función empiezan en cero y muestran `Sin uso registrado`.

| Endpoint | Función |
| --- | --- |
| `GET /chats/{id}/usage` | Totales, detalle por rol y modelo, costo del último turno y contexto. |

`GET /chats/{id}/messages` incluye el mismo resumen en `usage` para recuperarlo al reconectar, y el WebSocket envía un evento `usage` tras cada llamada. `DELETE /chats/{id}` borra también el uso.

### Sesiones y transcripción

El servidor es la fuente de verdad de las sesiones: se pueden abrir desde cualquier cliente. El backend escribe en `data/app.db` una transcripción propia en cada turno, independiente del checkpoint de LangGraph, porque el checkpoint no sirve de historial: la preparación se borra al aprobar el contexto, el resumidor reemplaza los mensajes viejos de estudio y las preguntas de aclaración son interrupciones, no mensajes.

- El mensaje del usuario se guarda antes de correr el grafo, así que sobrevive a una recarga a mitad de turno.
- Se guardan la pregunta de aclaración (agente `Preparación`), el aviso de contexto aprobado y cada respuesta de estudio (agente `Supervisor`). La respuesta se guarda antes de enviar `agent_response`.
- La sesión se crea con el primer mensaje, con los primeros 42 caracteres como título; `context_processed` lo reemplaza por el título del contexto. Si falla la escritura, se registra y el turno sigue.
- `CHAT_MESSAGES_MAX_ENTRIES` limita los mensajes que devuelve `GET /chats/{id}/messages` (los últimos N).
- Las sesiones que solo tienen checkpoint, anteriores a la transcripción, no aparecen en el listado y no se limpian.

| Endpoint | Contenido |
|---|---|
| `GET /chats` | Sesiones de la más reciente a la más antigua: `chat_id`, `title`, `phase`, `created_at`, `updated_at` y `document_count`. |
| `GET /chats/{id}/messages` | Transcripción en `messages` (`id`, `role`, `agent`, `content`, `created_at`) y estado del grafo: `phase`, `pending_question`, `turn_active`, `board`, `progress` y `usage`. |
| `DELETE /chats/{id}` | Borra la sesión, su transcripción, su progreso, su uso, sus documentos y su checkpoint. |

## Componentes

- **Backend** (`api/`, `core/`, `documents/`, `config/`): FastAPI y LangGraph sobre Python 3.14. Usa OpenRouter para los modelos, Tavily para la búsqueda web, Chroma para los vectores y SQLite para las sesiones y su transcripción, el catálogo de PDFs, el progreso del alumno y los checkpoints de los grafos.
- **Cliente** (`client/`): interfaz con Electron, React, Vite y TypeScript. Se comunica con el backend por WebSocket y ejecuta Python en el navegador con Pyodide.

## Estructura del proyecto

| Ruta | Contenido |
|---|---|
| `api/` | API FastAPI. `app.py`: lifespan (grafos, workers de documentos) y wiring. `ws.py`: WebSocket de sesión `/ws/{thread_id}` con lock por turno, heartbeat y "la última conexión gana". `routes.py`: `GET /chats`, `GET /chats/{id}/messages`, `GET /chats/{id}/progress`, `GET /reviews/due`, `GET /chats/{id}/usage`, los endpoints de modelos, `GET`/`POST /chats/{id}/pdfs` y `DELETE /chats/{id}`. `security.py`: token local, validación de ids y límites. `cors.py`: orígenes permitidos. |
| `config/` | `settings.py`: registro de modelos por rol (`MODEL_ROLES`, con override por entorno), rutas de datos y límites. |
| `core/` | Grafos y agentes. `graphs/` (`study.py`: grafo padre con un checkpointer; `context.py`: subgrafo de contexto; `session.py`: nodos del turno), `agents/` (`catalog.py`: arquetipos por función y técnicas pedagógicas; `builder.py`: supervisor con `create_agent` y subagentes expuestos como tools), `prompts/` (por grafo), `chats.py` (sesiones y transcripción en SQLite), `study/` (`scheduler.py`: calendario Leitner; `repository.py`: progreso e historial en SQLite), `tools/` (`math` con SymPy en subproceso, `search_documents` con páginas, `get_study_material`, `record_progress`, `web_search` y las tools de pizarra). `board.py` define los bloques y las operaciones de la pizarra, `model_catalog.py` consulta OpenRouter, `model_settings.py` guarda las preferencias y resuelve el modelo de cada rol, `usage.py` registra y guarda los tokens y el costo de cada llamada, `llm.py` construye modelos por rol, `chain.py` cadenas, `streaming.py` envía los eventos por WebSocket; `schemas.py` y `states.py` contienen modelos Pydantic y estados. |
| `documents/` | Procesamiento de PDFs en segundo plano. `pipeline.py`: Docling en un proceso aparte, índice Chroma y resumen por documento. `repository.py`: catálogo SQLite (estado y resumen de cada documento). `worker.py` procesa la cola, `cleanup.py` elimina archivos y vectores huérfanos y `storage.py` borra los directorios de cada sesión. |
| `tests/` | Pruebas `unittest` (WebSocket, seguridad, historial, catálogo de documentos, caché LRU de supervisores, agentes, grafo de estudio, tool `math`, pizarra, calendario, progreso, modelos y uso). |
| `client/` | Cliente Electron, React y Vite. Ver [client/README.md](client/README.md). |

Archivos de la raíz:

| Archivo | Contenido |
|---|---|
| `Dockerfile` | Imagen del backend: Python 3.14-slim, usuario sin privilegios y Uvicorn en el puerto 8000. |
| `docker-compose.yml` | Servicios `backend` (puerto 8000) y `frontend` (puerto 5173), volúmenes y healthchecks. |
| `.dockerignore` | Excluye del contexto de build del backend el cliente, `.env`, `data/` y archivos locales. |
| `.gitignore` | Excluye `.venv/`, `.env`, `__pycache__/` y `data/` del repositorio. |
| `.env.example` | Plantilla de variables de entorno. |
| `requirements.txt` | Dependencias de Python con versiones fijadas. |

## Configuración

Copia la plantilla y completa las claves:

```bash
cp .env.example .env
```

| Variable | Descripción |
|---|---|
| `OPENAI_API_KEY` | Clave de OpenRouter, usada por el backend como clave de un proveedor compatible con OpenAI. |
| `TAVILY_API_KEY` | Clave de Tavily para la búsqueda web del clasificador de contexto y del arquetipo investigador. |
| `APP_TOKEN` | Token local obligatorio. La API lo exige en HTTP (`Authorization: Bearer`) y en el WebSocket (subprotocolo `token.<APP_TOKEN>`); sin él no arranca. Genéralo con `python -c "import secrets; print(secrets.token_hex(32))"` La app de Electron lo lee de este `.env` cuando el backend es local; en el navegador o con un servidor remoto, pégalo en el campo «Token del servidor» de la pantalla de inicio. |

Variables opcionales, con sus valores por defecto en `config/settings.py`: `DOCUMENT_WORKERS` (1), `DOCUMENT_MAX_ATTEMPTS` (3), `SESSION_STUDY_SUPERVISOR_CACHE_MAX_ENTRIES` (100), `CHAT_MESSAGES_MAX_ENTRIES` (500), `DOCLING_MAX_PAGES` (400), `DOCLING_TIMEOUT_SECONDS` (900), `DOCUMENT_SUMMARY_CHAR_BUDGET` (120000), `WS_MAX_MESSAGE_CHARS` (8000), `WS_MAX_TURNS_PER_MINUTE` (20), `MATH_TIMEOUT_SECONDS` (8), `SUPERVISOR_RECURSION_LIMIT` (40), `DEEP_SUPERVISOR_RECURSION_LIMIT` (150), `DEEP_SUPERVISOR_MAX_MODEL_CALLS` (20), `BOARD_MAX_BLOCKS` (60), `BOARD_MAX_BLOCK_CHARS` (8000), `BOARD_MAX_OPS_PER_TURN` (8) y `BOARD_MAX_PLOT_FUNCTIONS` (4).

Modelos por rol (`context_classifier`, `factory`, `supervisor`, `subagent_light`, `subagent_heavy`, `summarizer`): se cambian sin tocar código con `MODEL_<ROL>`, `TEMPERATURE_<ROL>` y `REASONING_<ROL>` (p. ej. `MODEL_SUPERVISOR=openai/gpt-5.5`). Lo elegido en la interfaz tiene prioridad sobre estas variables. Los embeddings con `MODEL_EMBEDDINGS`.

## Levantar con Docker

Requisitos: Docker y Docker Compose v2 (`docker compose`), una clave de OpenRouter y una de Tavily. La primera construcción tarda y ocupa bastante espacio, porque la imagen del backend instala Docling y PyTorch.

Desde la raíz del repositorio, con el `.env` ya configurado:

```bash
docker compose up --build
```

| Servicio | URL | Descripción |
|---|---|---|
| `frontend` | http://127.0.0.1:5173 | Interfaz web servida por Nginx. |
| `backend` | http://127.0.0.1:8000 | API y WebSocket `/ws/{thread_id}`. |
| `backend` | http://127.0.0.1:8000/docs | Documentación interactiva de la API. |

Abre la interfaz en `127.0.0.1:5173` y escribe el `APP_TOKEN` en la pantalla de inicio. Por defecto, la interfaz se conecta a `ws://127.0.0.1:8000`. Ambos puertos se publican solo en `127.0.0.1`, no en la LAN.

Comandos útiles:

```bash
docker compose down      # detiene los servicios y conserva los datos
docker compose down -v   # además borra los volúmenes: sesiones, PDFs, índices y caché de modelos
```

Datos y volúmenes:

- `data_volume` se monta en `/app/data`. Contiene `app.db` (sesiones y transcripción, catálogo de PDFs, progreso, repasos, preferencias de modelos y uso), `sessions/<chat_id>/` (PDFs y base vectorial Chroma de cada sesión) y `study.db` (checkpoints de LangGraph: un estado por sesión, con su pizarra).
- `models_cache` guarda la caché de modelos de Docling y HuggingFace para no descargarlos en cada arranque.
- Para ver los datos desde el host, cambia `data_volume` por el bind mount `./data:/app/data`. El directorio debe ser escribible por el UID 10001, que es el usuario del contenedor.

## Desarrollo local sin Docker

Backend, desde la raíz del repositorio y con Python 3.14 (la misma versión que la imagen Docker):

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
.venv/bin/uvicorn api.app:app --reload --port 8000
```

En Windows, con el mismo `.env`:

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m uvicorn api.app:app --port 8000
```

El cliente arranca aparte, con Electron o solo en el navegador: ver [client/README.md](client/README.md). La app de Electron levanta este backend sola si no está corriendo, y en Fedora se instala como `.rpm` con acceso en el menú de aplicaciones.

**Migración:** las sesiones anteriores no se migran, tampoco las que el cliente guardaba en el `localStorage` del navegador: la nueva versión las descarta y empieza de cero. Si vienes de una versión con `context_study.db`, `session_study.db` o `document_processor.db`, limpia `data/` (con Docker, `docker compose down -v`) antes de arrancar.

**Progreso anterior:** el progreso guardado antes en el checkpoint de cada sesión se ignora, porque sus temas no tenían número. La sesión sigue funcionando y el progreso se reconstruye con las evaluaciones nuevas. La pizarra y `topic_progress` no requieren migración: una sesión existente empieza con ambas vacías.

## Tests

```bash
.venv/bin/python -m unittest discover tests
```

En Windows:

```powershell
.venv\Scripts\python -m unittest discover tests
```

Los grafos y el LLM se simulan: los tests no necesitan claves ni red.
