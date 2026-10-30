import { app, BrowserWindow } from 'electron';
import { join } from 'node:path';

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
    const isAllowed = developmentUrl ? url.startsWith(developmentUrl) : url.startsWith('file:');
    if (!isAllowed) event.preventDefault();
  });

  const developmentUrl = process.env.VITE_DEV_SERVER_URL;
  if (developmentUrl) {
    void window.loadURL(developmentUrl);
  } else {
    void window.loadFile(join(__dirname, '../dist/index.html'));
  }
};

void app.whenReady().then(() => {
  createWindow();
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});
