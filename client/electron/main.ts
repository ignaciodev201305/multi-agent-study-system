import { app, BrowserWindow, net, protocol } from 'electron';
import { join, normalize, relative } from 'node:path';
import { pathToFileURL } from 'node:url';

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

const createWindow = (): void => {
  const window = new BrowserWindow({
    width: 1280,
    height: 820,
    minWidth: 760,
    minHeight: 560,
    backgroundColor: '#101113',
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

  const developmentUrl = process.env.VITE_DEV_SERVER_URL;
  if (developmentUrl) {
    void window.loadURL(developmentUrl);
  } else {
    void window.loadURL(`${APP_ORIGIN}/index.html`);
  }
};

void app.whenReady().then(() => {
  if (!process.env.VITE_DEV_SERVER_URL) serveBundle();
  createWindow();
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});
