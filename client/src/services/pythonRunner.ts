import { fenced } from './boardExport';
import type { PythonWorkerMessage, PythonWorkerRequest } from '../types';

export type PythonRunStatus = 'ok' | 'error' | 'timeout' | 'stopped';

export interface PythonResult {
  status: PythonRunStatus;
  stdout: string;
  stderr: string;
  error: string | null;
  figures: string[];
}

interface ActiveRun {
  id: number;
  timer: ReturnType<typeof setTimeout>;
  resolve: (result: PythonResult) => void;
  onStarted?: () => void;
}

export const RUN_TIMEOUT_MS = 30_000;
// La primera ejecución descarga Python y sus paquetes.
const LOAD_TIMEOUT_MS = 120_000;
const MAX_OUTPUT_CHARS = 20_000;
const MAX_SHARED_CODE_CHARS = 4000;
const MAX_SHARED_OUTPUT_CHARS = 2000;
const BASE64_PATTERN = /^[A-Za-z0-9+/]+={0,2}$/;

// El código del alumno comparte el canal del worker, así que su salida no es de fiar.
const safeText = (value: unknown): string => (typeof value === 'string' ? value.slice(0, MAX_OUTPUT_CHARS) : '');

const safeFigures = (value: unknown): string[] => (Array.isArray(value)
  ? value.filter((item): item is string => typeof item === 'string' && BASE64_PATTERN.test(item))
  : []);

const emptyResult = (status: PythonRunStatus, error: string | null): PythonResult => ({
  status,
  stdout: '',
  stderr: '',
  error,
  figures: [],
});

class PythonRunner {
  private worker: Worker | null = null;
  private active: ActiveRun | null = null;
  private queue: Promise<unknown> = Promise.resolve();
  private nextId = 1;

  run(code: string, onStarted?: () => void): Promise<PythonResult> {
    const result = this.queue.then(() => this.execute(code, onStarted));
    this.queue = result;
    return result;
  }

  terminate(): void {
    if (this.active) this.finish(emptyResult('stopped', null));
  }

  private execute(code: string, onStarted?: () => void): Promise<PythonResult> {
    return new Promise((resolve) => {
      const id = this.nextId;
      this.nextId += 1;
      const timer = setTimeout(
        () => this.abort('La carga de Python superó el tiempo máximo. Comprueba tu conexión.'),
        LOAD_TIMEOUT_MS,
      );
      this.active = { id, timer, resolve, onStarted };
      const request: PythonWorkerRequest = { id, code };
      this.ensureWorker().postMessage(request);
    });
  }

  private ensureWorker(): Worker {
    if (this.worker) return this.worker;
    const worker = new Worker(new URL('./pythonWorker.ts', import.meta.url), { type: 'module' });
    worker.addEventListener('message', (event: MessageEvent<PythonWorkerMessage>) => this.handleMessage(event.data));
    worker.addEventListener('error', () => this.abort('No se pudo iniciar Python.'));
    this.worker = worker;
    return worker;
  }

  private handleMessage(message: PythonWorkerMessage): void {
    const active = this.active;
    if (!active || message.id !== active.id) return;

    if (message.type === 'started') {
      clearTimeout(active.timer);
      active.timer = setTimeout(
        () => this.abort(`La ejecución superó el máximo de ${RUN_TIMEOUT_MS / 1000} s y se detuvo.`),
        RUN_TIMEOUT_MS,
      );
      active.onStarted?.();
      return;
    }

    const error = typeof message.error === 'string' ? safeText(message.error) : null;
    clearTimeout(active.timer);
    this.active = null;
    active.resolve({
      status: error ? 'error' : 'ok',
      stdout: safeText(message.stdout),
      stderr: safeText(message.stderr),
      error,
      figures: safeFigures(message.figures),
    });
  }

  private abort(message: string): void {
    this.finish(emptyResult('timeout', message));
  }

  // Un bucle infinito bloquea al worker: la única forma de cortarlo es destruirlo.
  private finish(result: PythonResult): void {
    this.worker?.terminate();
    this.worker = null;
    if (!this.active) return;
    clearTimeout(this.active.timer);
    this.active.resolve(result);
    this.active = null;
  }
}

export const pythonRunner = new PythonRunner();

const clip = (text: string, maxChars: number): string =>
  text.length > maxChars ? `${text.slice(0, maxChars)}\n… (recortado)` : text;

export const tutorMessage = (code: string, result: PythonResult): string => {
  const output = [result.stdout, result.stderr, result.error].filter(Boolean).join('\n').trim();
  const figures = result.figures.length > 0 ? `\n\nGeneró ${result.figures.length} figura(s).` : '';
  return [
    'Ejecuté este código en mi navegador:',
    fenced('python', clip(code, MAX_SHARED_CODE_CHARS)),
    'Salida:',
    `${fenced('text', clip(output || '(sin salida)', MAX_SHARED_OUTPUT_CHARS))}${figures}`,
  ].join('\n\n');
};
