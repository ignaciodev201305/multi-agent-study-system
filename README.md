# Sistema de estudio multiagente

Aplicación para estudiar con agentes de IA. Cada sesión construye un contexto de estudio a partir de lo que describe el usuario (objetivo, nivel previo y material). Si el contexto no es suficiente, el sistema hace preguntas de aclaración. Después arma un plan (temario y especialistas elegidos de un catálogo de arquetipos: explicador, entrenador de ejercicios, evaluador, analista de documentos, investigador) y un profesor principal que consulta a esos especialistas como herramientas, verifica la matemática con SymPy y registra el progreso del alumno por tema.

El usuario puede subir PDFs de dos tipos:

- **`rag`**: el documento se extrae con Docling, se trocea e indexa en Chroma. Los agentes consultan los fragmentos relevantes de la sesión.
- **`study_map`**: se genera un mapa de estudio (resumen, temas, fórmulas, ejercicios y conceptos) que se incluye en el contexto de la sesión.

Los PDFs se procesan en segundo plano: la subida responde con estado `queued` y pasa por `processing` hasta `ready` o `failed`.

## Componentes

- **Backend** (`api/`, `core/`, `documents/`, `config/`): FastAPI y LangGraph sobre Python 3.14. Usa OpenRouter para los modelos, Tavily para la búsqueda web, Chroma para los vectores y SQLite para el catálogo de PDFs y los checkpoints de los grafos.
- **Cliente** (`client/`): interfaz con Electron, React, Vite y TypeScript. Se comunica con el backend por WebSocket.

## Estructura del proyecto

| Ruta | Contenido |
|---|---|
| `api/` | API FastAPI. `app.py`: lifespan (grafos, workers de documentos) y wiring. `ws.py`: WebSocket de sesión `/ws/{thread_id}` con lock por turno, heartbeat y "la última conexión gana". `routes.py`: `GET /chats/{id}/messages`, `GET`/`POST /chats/{id}/pdfs` y `DELETE /chats/{id}`. `security.py`: token local, validación de ids y límites. `cors.py`: orígenes permitidos. |
| `config/` | `settings.py`: registro de modelos por rol (`MODEL_ROLES`, con override por entorno), rutas de datos y límites. |
| `core/` | Grafos y agentes. `graphs/` (`study.py`: grafo padre con un checkpointer; `context.py`: subgrafo de contexto; `session.py`: nodos del turno), `agents/` (`catalog.py`: arquetipos por función y técnicas pedagógicas; `builder.py`: supervisor con `create_agent` y subagentes expuestos como tools), `prompts/` (por grafo), `tools/` (`math` con SymPy en subproceso, `search_documents` con páginas, `get_study_material`, `record_progress` y `web_search`). `llm.py` construye modelos por rol, `chain.py` cadenas, `streaming.py` envía los eventos por WebSocket; `schemas.py` y `states.py` contienen modelos Pydantic y estados. |
| `documents/` | Procesamiento de PDFs en segundo plano. `pipeline.py`: Docling en un proceso aparte, índice Chroma y resumen por documento. `repository.py`: catálogo SQLite (estado y resumen de cada documento). `worker.py` procesa la cola, `cleanup.py` elimina archivos y vectores huérfanos y `storage.py` borra los directorios de cada sesión. |
| `tests/` | Pruebas `unittest` (WebSocket, seguridad, historial, catálogo de documentos y caché LRU de supervisores). |
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
| `TAVILY_API_KEY` | Clave de Tavily para la búsqueda web del clasificador de contexto. |
| `APP_TOKEN` | Token local obligatorio. La API lo exige en HTTP (`Authorization: Bearer`) y en el WebSocket (subprotocolo `token.<APP_TOKEN>`); sin él no arranca. Genéralo con `python -c "import secrets; print(secrets.token_hex(32))"` y pégalo en el campo «Token local» de la pantalla de inicio del cliente. |

Variables opcionales, con sus valores por defecto en `config/settings.py`: `DOCUMENT_WORKERS` (1), `DOCUMENT_MAX_ATTEMPTS` (3), `SESSION_STUDY_SUPERVISOR_CACHE_MAX_ENTRIES` (100), `DOCLING_MAX_PAGES` (400), `DOCLING_TIMEOUT_SECONDS` (900), `DOCUMENT_SUMMARY_CHAR_BUDGET` (120000), `WS_MAX_MESSAGE_CHARS` (8000) y `WS_MAX_TURNS_PER_MINUTE` (20).

Modelos por rol (`context_classifier`, `factory`, `supervisor`, `subagent_light`, `subagent_heavy`, `summarizer`): se cambian sin tocar código con `MODEL_<ROL>`, `TEMPERATURE_<ROL>` y `REASONING_<ROL>` (p. ej. `MODEL_SUPERVISOR=openai/gpt-5.5`). Los embeddings con `MODEL_EMBEDDINGS`.

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

- `data_volume` se monta en `/app/data`. Contiene `app.db` (catálogo de PDFs), `sessions/<chat_id>/` (PDFs y base vectorial Chroma de cada sesión) y `study.db` (checkpoints de LangGraph: un estado por sesión).
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

**Migración:** las sesiones anteriores no se migran. Si vienes de una versión con `context_study.db`, `session_study.db` o `document_processor.db`, limpia `data/` (con Docker, `docker compose down -v`) antes de arrancar.

Cliente: ver [client/README.md](client/README.md).

## Tests

```bash
.venv/bin/python -m unittest discover tests
```

Los grafos y el LLM se simulan: los tests no necesitan claves ni red.
