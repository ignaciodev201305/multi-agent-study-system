# Estudio Multiagente

Aplicación de escritorio para Linux construida con Electron, Vite, React y TypeScript. El renderer se comunica con el backend existente mediante WebSocket. Si nada escucha en `127.0.0.1:8000`, el proceso principal de Electron inicia el backend del repositorio y lo detiene al cerrar la app (ver [Backend automático](#backend-automático)). También puede usarse solo en el navegador, sin Electron.

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

Con Electron no hace falta iniciar el backend aparte. Para usar el modo `--reload` o solo el navegador, inícialo en una terminal, desde la raíz del repositorio:

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

La aplicación usa por defecto `ws://127.0.0.1:8000/ws/{thread_id}`. Si la página se sirve por HTTP o HTTPS desde otro host (el proxy de [Acceso desde el celular](../README.md#acceso-desde-el-celular)), usa su propio origen con el prefijo `/api`. La dirección base se puede cambiar en la pantalla de inicio, en el campo «Backend WebSocket». El servidor debe estar disponible antes de enviar el primer mensaje.

## Token

El backend exige su `APP_TOKEN` en toda conexión.

- En Electron, si «Backend WebSocket» apunta a esta máquina (`127.0.0.1`, `localhost` o `[::1]`), la app lee `APP_TOKEN` del `.env` de la carpeta del repositorio guardada en `config.json` y lo usa sin mostrar el campo.
- En el navegador, o con un backend en otro equipo, aparece el campo «Token del servidor» para escribirlo a mano.

El token se guarda en el `localStorage` del renderer, viaja en la cabecera `Authorization: Bearer` en HTTP y como subprotocolo `token.<APP_TOKEN>` en el WebSocket, porque el navegador no permite cabeceras en ese canal.

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

## Backend automático

Al abrir, la app muestra «Iniciando backend…» y comprueba `127.0.0.1:8000`:

- Si ya responde (Uvicorn a mano o Docker), lo usa y no lanza otro.
- Si no, lanza `.venv/bin/python -m uvicorn api.app:app` (en Windows, `.venv\Scripts\python.exe`) desde la carpeta del repositorio, con su `.env`. La primera vez pide elegir esa carpeta, que debe tener `.venv` y `api/app.py`, y la guarda en `config.json`.
- Espera hasta 60 s. Si no arranca, avisa con la ruta del log y abre igual la interfaz.
- Al cerrar la app detiene solo el backend que lanzó.

`config.json` y `backend.log` quedan en `~/.config/Estudio Multiagente/` (en Windows, `%APPDATA%\Estudio Multiagente\`). Para elegir otra carpeta del repositorio, borra `config.json`.

## Instalar en Fedora

El paquete se compila en la misma máquina Linux, después de `git pull`. El repositorio tiene que tener su `.venv` con `requirements.txt` instalado y su `.env`, como indica el [README principal](../README.md).

```bash
sudo dnf install rpm-build
npm install
npm run package:linux
sudo dnf install ./release/*.rpm
```

`package:linux` genera un `.rpm` y un AppImage en `client/release/`. El `.rpm` deja «Estudio Multiagente» en Actividades con su ícono (`build/icon.png`), desde donde se puede anclar al dock. Para actualizarla, repite los dos últimos comandos.

No hace falta escribir el token: se toma del `.env` del repositorio.

La configuración usa Electron 44 y electron-builder 27.0.0-alpha.10.

## Panel lateral

La sesión tiene un panel a la derecha con tres pestañas. Se cierra con su botón y se reabre desde «Pizarra», en la barra superior; su estado y la pestaña activa se recuerdan. Por debajo de 1100 px se abre como capa sobre la conversación. Por debajo de 760 px ocupa toda la pantalla (ver [Pantallas angostas y táctiles](#pantallas-angostas-y-táctiles)).

- **Pizarra:** muestra los bloques de la sesión y permite exportarlos a Markdown. Los gráficos de funciones (`plot`) y los de datos (`chart`, con líneas, puntos o barras) comparten el mismo componente SVG. No se guarda en el navegador: la fuente de verdad es el servidor. El contenido de los bloques es texto no confiable y se renderiza sin `dangerouslySetInnerHTML`.
- **Progreso:** muestra el temario con el estado de cada tema, la próxima fecha de repaso y la última evidencia. Se refresca al abrir la pestaña y con cada turno.
- **Python:** editor y ejecución en el navegador.

La pantalla de inicio agrega «Para repasar hoy» con los repasos vencidos de `GET /reviews/due`, con el título de la sesión que devuelve el servidor.

## Pantallas angostas y táctiles

Por debajo de 760 px la sesión usa una sola columna, pensada para el celular:

- «Material» y el panel lateral se abren como capas a pantalla completa y empiezan cerrados; la barra superior se desplaza en horizontal si no caben sus botones.
- La altura sigue al teclado en pantalla (`100dvh` e `interactive-widget=resizes-content`) y se respeta el área segura (`env(safe-area-inset-*)`).
- Sin puntero fino (`pointer: coarse`), los botones miden al menos 38–44 px, el cuadro de mensaje usa 16 px y Enter inserta un salto de línea: se envía con el botón.
- Sin hover, el botón de borrar sesión queda siempre visible.
- En los gráficos, tocar o arrastrar en horizontal muestra los valores; el arrastre vertical desplaza la pizarra.
- Al volver de segundo plano se recargan la lista y la sesión abierta, porque el sistema corta el WebSocket mientras la app no está visible.

La ventana de Electron mantiene su ancho mínimo de 760 px.

## Instalar en el celular

Servido por HTTPS (ver [Acceso desde el celular](../README.md#acceso-desde-el-celular)), el cliente es una PWA: en Chrome para Android, menú → «Agregar a la pantalla principal» o «Instalar app» deja un ícono que abre la aplicación sin la barra del navegador.

- `public/manifest.webmanifest` declara el nombre, el modo `standalone` y los íconos de `public/icons/`, generados desde `build/icon.png` (192 y 512 px, y uno `maskable` con el logo al 80 % sobre `#101113`).
- `public/sw.js` guarda solo el shell: `index.html`, `/assets/`, los íconos y el manifest. No intercepta `/api/`, el WebSocket, peticiones que no sean `GET` ni otros orígenes (Pyodide), así que ni el token ni las respuestas del backend quedan en caché.
- Se registra solo en la versión compilada y bajo `https:`; en Electron y con Vite no hay service worker.
- Con el shell guardado, la app abre aunque la PC esté apagada y la pantalla de inicio explica por qué no cargan las sesiones.
- Al cambiar la lista de rutas guardadas, sube la versión de `CACHE` en `sw.js` para descartar la caché anterior.

## Modelos

El botón «Modelos» abre el panel de configuración. Desde la pantalla de inicio edita los modelos globales, y desde la barra de la sesión, los de esa sesión.

Cada rol muestra su modelo efectivo y su origen: por defecto, global o sesión. «Cambiar» abre un buscador con precio por millón de tokens y contexto, filtrado por lo que exige ese rol, y «Restablecer» quita el ajuste. Si un modelo guardado ya no está disponible, la fila lo indica.

El cambio aplica desde el mensaje siguiente. El cliente solo habla con el backend: nunca recibe la clave de OpenRouter.

## Matemática

Los mensajes y los bloques de la pizarra se renderizan como Markdown con `remark-math` y `rehype-katex`: `$...$` para matemática en línea y `$$...$$` en bloque. Los bloques `math` reciben LaTeX sin delimitadores.

## Diagramas

Los bloques `diagram` de la pizarra se dibujan con [Mermaid](https://mermaid.js.org/). La librería se descarga con `import()` solo cuando aparece el primer diagrama, así no pesa en el arranque.

- Se inicializa con `securityLevel: 'strict'` y sin etiquetas HTML, y el texto se valida con `mermaid.parse` antes de dibujarlo.
- El SVG resultante se muestra como `<img>` con una URL `data:`, sin `dangerouslySetInnerHTML`: como imagen no puede ejecutar scripts ni tocar la página.
- Si el texto no es Mermaid válido, el bloque muestra ese texto tal cual.
- Un toque o clic alterna entre ajustar el diagrama al ancho y verlo a tamaño real con desplazamiento, útil en el celular.
- La exportación a Markdown lo deja como bloque ```` ```mermaid ````.

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

## Sesiones y mensajes

La lista de sesiones, los mensajes y la cantidad de documentos vienen del servidor (`GET /chats` y `GET /chats/{id}/messages`); el `localStorage` guarda solo las preferencias: sesión activa, tema, dirección y token del backend, estado de los paneles y el código de Python. Por eso una sesión se ve igual desde otro navegador o dispositivo. Las sesiones que una versión anterior guardó en el navegador no se migran: se descartan al actualizar.

- La lista se carga al iniciar, al volver al inicio y después de cada evento que cierra un turno. Si falla, la pantalla de inicio muestra «No se pudieron cargar las sesiones» con el motivo (no se llegó al backend, el proxy responde pero el backend está detenido, o el token fue rechazado) y un botón «Reintentar»; no hay caché de sesiones sin conexión.
- Una sesión nueva existe solo en el cliente hasta su primer mensaje; el backend la crea en ese primer turno.
- Al enviar se muestra el mensaje del usuario al instante, y con `human_input_request`, `context_processed` y `agent_response` se muestra el contenido recibido. Enseguida se pide `GET /chats/{id}/messages`, y la lista del servidor reemplaza a la local sin mezclarla.
- El panel «Material» pide los documentos con `GET /chats/{id}/pdfs` al abrirse y después de subir uno; el contador sale de esa lista.

## Protocolo e integración

El cliente envía `{ "content": "...", "deep": false }` por la ruta `/ws/{thread_id}` y reconoce `thinking`, `human_input_request`, `context_processed`, `agent_response`, `board_patch`, `status` y `error`. Los fragmentos `thinking` se acumulan y se muestran en vivo en la conversación; las respuestas se muestran al instante y luego se reemplazan por la transcripción del servidor. En el backend, el estado de cada sesión se conserva mediante checkpoints persistidos en SQLite, por lo que la memoria no depende de que la conexión WebSocket o el proceso Python permanezcan activos.

Los fragmentos `thinking` se acumulan durante 100 ms antes de llegar al store, porque renderizar la sesión con cada uno bloquearía la interfaz. Se agrupan por `node` y `step` cuando esos campos están disponibles, y se muestran como actividad en curso o completada. La interfaz etiqueta el grafo de contexto o el grafo de estudio según la fase de la sesión. El servidor manda `node` y `step` como campos del evento.

`deep` es opcional y booleano: `true` pide el modo profundo para ese turno, y el backend rechaza otro tipo con `invalid_message`. El cliente lo envía según el botón «Profundo» de la sesión, que vive en el store sin persistir, y solo en la fase de estudio.

La respuesta `agent_response` contiene solo `content`, sin el nombre del subagente. El servidor guarda esas respuestas con el agente «Supervisor» y las preguntas de setup con «Preparación», y la conversación muestra la etiqueta `agent` de cada mensaje; el mensaje del usuario se etiqueta «Tú».

El evento `board_patch` lleva `content` vacío, `revision`, `op` (`upsert`, `remove` o `clear`) y `block` o `block_id` según la operación. Un parche solo se aplica si su revisión es la siguiente a la local; si no, el cliente pide `GET /chats/{id}/messages`, que trae la pizarra completa. También se resincroniza al abrir una sesión, al recibir `agent_response` y ante un error, lo que revierte los parches de un turno fallido. Si esa respuesta trae `turn_active` y la sesión no está esperando un turno propio, el cliente repite la consulta cada 3 s hasta que termine, con un máximo de 60 intentos.
