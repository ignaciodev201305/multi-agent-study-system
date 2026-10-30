# Estudio Multiagente

Aplicación de escritorio para Linux construida con Electron, Vite, React y TypeScript. El renderer se comunica con el backend existente mediante WebSocket; el proceso principal de Electron no inicia ni modifica el servidor.

## Requisitos

- Node.js 22.20 o posterior y npm.
- El backend Python instalado en la raíz del repositorio.

## Instalar

Desde `client/`:

```bash
npm install
```

## Iniciar en desarrollo

En una terminal, desde la raíz del repositorio, inicia el backend:

```bash
.venv/bin/uvicorn api.app:app --reload --port 8000
```

En otra terminal, desde `client/`:

```bash
npm run dev
```

La aplicación usa por defecto `ws://127.0.0.1:8000/ws/{thread_id}`. La dirección base se puede cambiar en la pantalla de inicio, en el campo «Backend WebSocket». El servidor debe estar disponible antes de enviar el primer mensaje.

## Comprobar y compilar

```bash
npm run typecheck
npm run build
```

Para abrir la versión compilada:

```bash
npm start
```

## Empaquetar para Linux

```bash
npm run package:linux
```

Electron Builder genera AppImage y `.deb` dentro de `client/release/`.

La configuración usa Electron 44 y electron-builder 27.0.0-alpha.10.

## Protocolo e integración

El cliente envía `{ "content": "..." }` por la ruta `/ws/{thread_id}` y reconoce `thinking`, `human_input_request`, `context_processed`, `agent_response` y `error`. Los fragmentos `thinking` se acumulan y se muestran en vivo en la conversación; las respuestas también se guardan localmente en el renderer. En el backend, el estado de cada sesión se conserva mediante checkpoints persistidos en SQLite, por lo que la memoria no depende de que la conexión WebSocket o el proceso Python permanezcan activos.

Los fragmentos `thinking` se agrupan por `node` y `step` cuando esos campos están disponibles, y se muestran como actividad en curso o completada. La interfaz etiqueta el grafo de contexto o el grafo de estudio según la fase de la sesión. También se aceptan eventos con metadatos anidados o serializados delante del texto en `content`.

La respuesta `agent_response` contiene solo `content`, sin el nombre del subagente. La interfaz etiqueta esas respuestas como «Supervisor» y las preguntas de setup como «Preparación»; para distinguir tutores concretos, el backend debe incluir metadatos de agente en sus eventos.
