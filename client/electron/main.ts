import { app, BrowserWindow, ipcMain, net, protocol } from 'electron';
import { join, normalize, relative } from 'node:path';
import { pathToFileURL } from 'node:url';
import { ensureBackend, readLocalToken, stopBackend } from './backend';

// Se sirve como app://bundle porque el backend no acepta el Origin "null" de file://.
const APP_SCHEME = 'app';
const APP_ORIGIN = `${APP_SCHEME}://bundle`;
const distDirectory = join(__dirname, '../dist');

protocol.registerSchemesAsPrivileged([
  // corsEnabled permite cargar el worker de Python como módulo bajo este esquema.
  { scheme: APP_SCHEME, privileges: { standard: true, secure: true, supportFetchAPI: true, corsEnabled: true } },
]);

const serveBundle = (): void => {
  protocol.handle(APP_SCHEME, (request) => {
    const { pathname } = new URL(request.url);
    const target = normalize(join(distDirectory, decodeURIComponent(pathname)));
    // Solo archivos dentro de dist/; cualquier otra ruta cae en index.html.
    const inside = !relative(distDirectory, target).startsWith('..');
    const file = inside && pathname !== '/' ? target : join(distDirectory, 'index.html');
    return net.fetch(pathToFileURL(file).toString());
  });
};

const LOADING_PAGE = `data:text/html;charset=utf-8,${encodeURIComponent(
  '<body style="margin:0;display:grid;place-items:center;height:100vh;background:#101113;color:#c9cbd1;font:14px system-ui">Iniciando backend…</body>',
)}`;

const iconPath = (): string =>
  process.env.VITE_DEV_SERVER_URL ? join(__dirname, '../public/logo.png') : join(distDirectory, 'logo.png');

const createWindow = (): BrowserWindow => {
  const window = new BrowserWindow({
    width: 1280,
    height: 820,
    minWidth: 760,
    minHeight: 560,
    backgroundColor: '#101113',
    icon: iconPath(),
    webPreferences: {
      preload: join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  window.webContents.on('will-navigate', (event, url) => {
    const developmentUrl = process.env.VITE_DEV_SERVER_URL;
    const isAllowed = developmentUrl ? url.startsWith(developmentUrl) : url.startsWith(APP_ORIGIN);
    if (!isAllowed) event.preventDefault();
  });

  return window;
};

const loadApp = (window: BrowserWindow): void => {
  const developmentUrl = process.env.VITE_DEV_SERVER_URL;
  void window.loadURL(developmentUrl ?? `${APP_ORIGIN}/index.html`);
};

void app.whenReady().then(async () => {
  if (!process.env.VITE_DEV_SERVER_URL) serveBundle();
  ipcMain.handle('local-token', () => readLocalToken());
  const window = createWindow();
  void window.loadURL(LOADING_PAGE);
  // Si el backend no arranca se abre igual la interfaz, que muestra el error de conexión.
  await ensureBackend();
  if (!window.isDestroyed()) loadApp(window);
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) loadApp(createWindow());
  });
});

app.on('will-quit', stopBackend);

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});
