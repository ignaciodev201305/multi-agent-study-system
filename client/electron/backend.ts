// Inicia el backend Python del repositorio cuando no hay uno escuchando y lo detiene al salir.
import { spawn, type ChildProcess } from 'node:child_process';
import { createWriteStream, existsSync, readFileSync, writeFileSync } from 'node:fs';
import { connect } from 'node:net';
import { join } from 'node:path';
import { app, dialog } from 'electron';

const BACKEND_HOST = '127.0.0.1';
const BACKEND_PORT = 8000;
const STARTUP_TIMEOUT_MS = 60_000;
const POLL_INTERVAL_MS = 500;

let backendProcess: ChildProcess | null = null;

const configPath = (): string => join(app.getPath('userData'), 'config.json');

/** Devuelve la ruta del log del backend que lanza la app. */
export const backendLogPath = (): string => join(app.getPath('userData'), 'backend.log');

const pythonPath = (repoDir: string): string =>
  process.platform === 'win32'
    ? join(repoDir, '.venv', 'Scripts', 'python.exe')
    : join(repoDir, '.venv', 'bin', 'python');

const isRepoDir = (repoDir: string): boolean =>
  existsSync(pythonPath(repoDir)) && existsSync(join(repoDir, 'api', 'app.py'));

const isPortOpen = (): Promise<boolean> =>
  new Promise((resolve) => {
    const socket = connect(BACKEND_PORT, BACKEND_HOST);
    socket.setTimeout(1000);
    socket.once('connect', () => { socket.destroy(); resolve(true); });
    socket.once('timeout', () => { socket.destroy(); resolve(false); });
    socket.once('error', () => resolve(false));
  });

const readSavedRepoDir = (): string | null => {
  try {
    const { repoDir } = JSON.parse(readFileSync(configPath(), 'utf8')) as { repoDir?: unknown };
    return typeof repoDir === 'string' && isRepoDir(repoDir) ? repoDir : null;
  } catch {
    return null;
  }
};

const askRepoDir = async (): Promise<string | null> => {
  const result = await dialog.showOpenDialog({
    title: 'Elige la carpeta del repositorio (la que tiene .venv y api/)',
    properties: ['openDirectory'],
  });
  const repoDir = result.filePaths[0];
  if (result.canceled || !repoDir) return null;
  if (!isRepoDir(repoDir)) {
    dialog.showErrorBox('Carpeta no válida', 'La carpeta elegida no tiene .venv con Python ni api/app.py.');
    return null;
  }
  writeFileSync(configPath(), JSON.stringify({ repoDir }, null, 2));
  return repoDir;
};

const waitForPort = async (child: ChildProcess, spawnFailed: () => boolean): Promise<boolean> => {
  const deadline = Date.now() + STARTUP_TIMEOUT_MS;
  while (Date.now() < deadline) {
    if (child.exitCode !== null || spawnFailed()) return false;
    if (await isPortOpen()) return true;
    await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
  }
  return false;
};

/** Garantiza un backend en 127.0.0.1:8000; devuelve false si no se pudo iniciar. */
export const ensureBackend = async (): Promise<boolean> => {
  if (await isPortOpen()) return true;
  const repoDir = readSavedRepoDir() ?? (await askRepoDir());
  if (!repoDir) return false;

  const log = createWriteStream(backendLogPath(), { flags: 'w' });
  const child = spawn(
    pythonPath(repoDir),
    ['-m', 'uvicorn', 'api.app:app', '--host', BACKEND_HOST, '--port', String(BACKEND_PORT), '--ws-max-size', '65536'],
    { cwd: repoDir, windowsHide: true },
  );
  let spawnFailed = false;
  child.once('error', (error) => {
    spawnFailed = true;
    log.write(`No se pudo lanzar Python: ${error.message}\n`);
  });
  child.stdout?.pipe(log);
  child.stderr?.pipe(log);
  backendProcess = child;

  if (await waitForPort(child, () => spawnFailed)) return true;
  stopBackend();
  dialog.showErrorBox('No se pudo iniciar el backend', `Revisa el log en ${backendLogPath()}`);
  return false;
};

/** Devuelve el APP_TOKEN del .env del repositorio guardado, o '' si no hay. */
export const readLocalToken = (): string => {
  const repoDir = readSavedRepoDir();
  if (!repoDir) return '';
  try {
    const env = readFileSync(join(repoDir, '.env'), 'utf8');
    const match = env.match(/^\s*(?:export\s+)?APP_TOKEN\s*=\s*(.*)$/m);
    return match ? match[1].trim().replace(/^(['"])(.*)\1$/, '$2').trim() : '';
  } catch {
    return '';
  }
};

/** Detiene el backend solo si lo inició la app. */
export const stopBackend = (): void => {
  if (backendProcess && backendProcess.exitCode === null) backendProcess.kill();
  backendProcess = null;
};
