# Estudio Multiagente

Aplicación de escritorio para Linux construida con Electron, Vite, React y TypeScript. El renderer se comunica con el backend existente mediante WebSocket; el proceso principal de Electron no inicia ni modifica el servidor. También puede usarse solo en el navegador, sin Electron.

## Requisitos

- Node.js 22.20 o posterior y npm.
- El backend Python instalado en la raíz del repositorio.
- Conexión a internet: la usan los modelos del backend y la primera ejecución de Python.

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

En Windows:

```powershell
.venv\Scripts\python -m uvicorn api.app:app --port 8000
```

En otra terminal, desde `client/`, inicia la aplicación de escritorio:

```bash
npm run dev
```

O úsala en el navegador, sin Electron, y abre `http://127.0.0.1:5173`:

```bash
npx vite --host 127.0.0.1
```

La aplicación usa por defecto `ws://127.0.0.1:8000/ws/{thread_id}`. La dirección base se puede cambiar en la pantalla de inicio, en el campo «Backend WebSocket». El servidor debe estar disponible antes de enviar el primer mensaje.

## Token local

El backend exige su `APP_TOKEN` en toda conexión. Escríbelo en el campo «Token local» de la pantalla de inicio: se guarda en el `localStorage` del renderer, viaja en la cabecera `Authorization: Bearer` en HTTP y como subprotocolo `token.<APP_TOKEN>` en el WebSocket, porque el navegador no permite cabeceras en ese canal.

## Comprobar y compilar

```bash
npm run typecheck
npm run build
```

Para abrir la versión compilada:

```bash
npm start
```

La versión compilada se sirve como `app://bundle`, un esquema propio de Electron, porque el backend no acepta el Origin `null` que tendría una página cargada desde `file://`.

## Empaquetar para Linux

```bash
npm run package:linux
```

Electron Builder genera AppImage y `.deb` dentro de `client/release/`.

La configuración usa Electron 44 y electron-builder 27.0.0-alpha.10.

## Panel lateral

La sesión tiene un panel a la derecha con tres pestañas. Se cierra con su botón y se reabre desde «Pizarra», en la barra superior; su estado y la pestaña activa se recuerdan. Por debajo de 1100 px se abre como capa sobre la conversación.

- **Pizarra:** muestra los bloques de la sesión y permite exportarlos a Markdown. No se guarda en el navegador: la fuente de verdad es el servidor. El contenido de los bloques es texto no confiable y se renderiza sin `dangerouslySetInnerHTML`.
- **Progreso:** muestra el temario con el estado de cada tema, la próxima fecha de repaso y la última evidencia. Se refresca al abrir la pestaña y con cada turno.
- **Python:** editor y ejecución en el navegador.

La pantalla de inicio agrega «Para repasar hoy» con los repasos vencidos de `GET /reviews/due`, y oculta los que no tienen sesión local.

## Modelos

El botón «Modelos» abre el panel de configuración. Desde la pantalla de inicio edita los modelos globales, y desde la barra de la sesión, los de esa sesión.

Cada rol muestra su modelo efectivo y su origen: por defecto, global o sesión. «Cambiar» abre un buscador con precio por millón de tokens y contexto, filtrado por lo que exige ese rol, y «Restablecer» quita el ajuste. Si un modelo guardado ya no está disponible, la fila lo indica.

El cambio aplica desde el mensaje siguiente. El cliente solo habla con el backend: nunca recibe la clave de OpenRouter.

## Matemática

Los mensajes y los bloques de la pizarra se renderizan como Markdown con `remark-math` y `rehype-katex`: `$...$` para matemática en línea y `$$...$$` en bloque. Los bloques `math` reciben LaTeX sin delimitadores.

## Python con Pyodide

El código corre en un Web Worker (`src/services/pythonWorker.ts`), administrado desde `src/services/pythonRunner.ts`.

- Pyodide se carga desde `https://cdn.jsdelivr.net/pyodide/v<VERSION>/full/`, así el repositorio no carga decenas de MB. La versión exacta está en la constante `PYODIDE_VERSION` del worker; antes de cambiarla, comprueba que exista en el CDN.
- La primera ejecución descarga Python y puede tardar unos segundos. `numpy`, `sympy`, `matplotlib` y los demás paquetes de Pyodide se cargan al importarlos.
- Cada ejecución arranca sin las variables de la anterior. `input()` falla con un error, porque no hay entrada estándar.
- La ejecución se corta a los 30 s: el worker se destruye y se crea otro en la siguiente ejecución, porque un bucle infinito no se puede interrumpir de otra forma. La carga inicial tiene su propio límite de 120 s.
- La salida se trunca a 20 000 caracteres. Las figuras abiertas de matplotlib se muestran como PNG, hasta 8 por ejecución.
- El código se guarda por sesión en el store persistido, con un máximo de 20 000 caracteres. Cerrar el panel o cambiar de sesión detiene la ejecución en curso; cambiar de pestaña no.
- «Enviar al tutor» deja el código (hasta 4000 caracteres) y la salida (hasta 2000) en el cuadro de mensaje, sin enviarlo.

No hay Content Security Policy en `index.html`, `nginx.conf` ni Electron. Si se agrega una, debe permitir `cdn.jsdelivr.net`, `wasm-unsafe-eval` y `worker-src`.

## Protocolo e integración

El cliente envía `{ "content": "..." }` por la ruta `/ws/{thread_id}` y reconoce `thinking`, `human_input_request`, `context_processed`, `agent_response`, `board_patch`, `status` y `error`. Los fragmentos `thinking` se acumulan y se muestran en vivo en la conversación; las respuestas también se guardan localmente en el renderer. En el backend, el estado de cada sesión se conserva mediante checkpoints persistidos en SQLite, por lo que la memoria no depende de que la conexión WebSocket o el proceso Python permanezcan activos.

Los fragmentos `thinking` se acumulan durante 100 ms antes de llegar al store, porque renderizar la sesión con cada uno bloquearía la interfaz. Se agrupan por `node` y `step` cuando esos campos están disponibles, y se muestran como actividad en curso o completada. La interfaz etiqueta el grafo de contexto o el grafo de estudio según la fase de la sesión. También se aceptan eventos con metadatos anidados o serializados delante del texto en `content`.

La respuesta `agent_response` contiene solo `content`, sin el nombre del subagente. La interfaz etiqueta esas respuestas como «Supervisor» y las preguntas de setup como «Preparación»; para distinguir tutores concretos, el backend debe incluir metadatos de agente en sus eventos.

El evento `board_patch` lleva `content` vacío, `revision`, `op` (`upsert`, `remove` o `clear`) y `block` o `block_id` según la operación. Un parche solo se aplica si su revisión es la siguiente a la local; si no, el cliente pide `GET /chats/{id}/messages`, que trae la pizarra completa. También se resincroniza al abrir una sesión, al recibir `agent_response` y ante un error, lo que revierte los parches de un turno fallido. Si esa respuesta trae `turn_active` y la sesión no está esperando un turno propio, el cliente repite la consulta cada 3 s hasta que termine, con un máximo de 60 intentos.
