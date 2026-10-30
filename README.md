# Sistema de estudio multiagente

Aplicación para estudiar con agentes de IA. Cada sesión construye un contexto de estudio a partir de lo que describe el usuario (objetivo, nivel previo y material). Si el contexto no es suficiente, el sistema hace preguntas de aclaración. Después crea un supervisor con subagentes especializados y responde dentro de ese contexto.

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
| `api/` | API FastAPI. `app.py` expone el WebSocket de sesión (`/ws/{thread_id}`), los endpoints `GET` y `POST /chats/{chat_id}/pdfs` y `DELETE /chats/{chat_id}`. En el arranque compila los grafos y lanza los workers de documentos. `cors.py` define los orígenes permitidos. |
| `config/` | `settings.py`: modelos y temperaturas, reglas de formato, rutas de datos y límites de subida (20 MB por PDF, 20 PDFs y 200 MB por sesión). |
| `core/` | Grafos y agentes. `graphs.py` define los grafos de contexto, sesión y procesamiento de documentos. `agent.py` crea el supervisor y los subagentes y `tools.py` define la búsqueda web y la búsqueda en PDFs. `prompts.py`, `schemas.py` y `states.py` contienen prompts, modelos Pydantic y estados. `llm.py` y `chain.py` construyen modelos y cadenas, `reducer.py` acota el historial y `astream.py` envía los eventos por WebSocket. |
| `documents/` | Procesamiento de PDFs. `processor.py` extrae con Docling e indexa en Chroma. `document_repository.py` guarda el catálogo en SQLite. `document_worker.py` procesa la cola, `document_cleanup.py` elimina archivos y vectores huérfanos y `session_file.py` gestiona los directorios de cada sesión. |
| `tests/` | Pruebas `unittest` de la caché LRU de `SessionStudyGraph`. |
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
| `NOTAS_TEST_CACHE.md` | Explicación del test de la caché. |

## Configuración

Copia la plantilla y completa las claves:

```bash
cp .env.example .env
```

| Variable | Descripción |
|---|---|
| `OPENAI_API_KEY` | Clave de OpenRouter, usada por el backend como clave de un proveedor compatible con OpenAI. |
| `TAVILY_API_KEY` | Clave de Tavily para la búsqueda web del clasificador de contexto. |

Variables opcionales, con sus valores por defecto en `config/settings.py`: `DOCUMENT_WORKERS` (1), `DOCUMENT_MAX_ATTEMPTS` (3) y `SESSION_STUDY_SUPERVISOR_CACHE_MAX_ENTRIES` (100).

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

Abre la interfaz en `127.0.0.1:5173`. Por defecto, la interfaz se conecta a `ws://127.0.0.1:8000`, el puerto del backend publicado en el host.

Comandos útiles:

```bash
docker compose down      # detiene los servicios y conserva los datos
docker compose down -v   # además borra los volúmenes: sesiones, PDFs, índices y caché de modelos
```

Datos y volúmenes:

- `data_volume` se monta en `/app/data`. Contiene `app.db` (catálogo de PDFs), `sessions/<chat_id>/` (PDFs y base vectorial Chroma de cada sesión) y las bases SQLite de los checkpoints de LangGraph.
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

Ejecútalo desde la raíz: las bases SQLite de los checkpoints usan rutas relativas (`data/...`).

Cliente: ver [client/README.md](client/README.md).

## Tests

```bash
.venv/bin/python -m unittest tests.test_session_study_graph_cache
```

Qué comprueba el test: [NOTAS_TEST_CACHE.md](NOTAS_TEST_CACHE.md).
