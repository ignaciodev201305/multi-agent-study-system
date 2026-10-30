import type { PythonWorkerMessage, PythonWorkerRequest } from '../types';

// Versión exacta publicada en jsDelivr.
const PYODIDE_VERSION = '314.0.7';
const INDEX_URL = `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`;
const MAX_OUTPUT_CHARS = 20_000;
const TRUNCATION_NOTICE = '\n… (salida truncada)';

// Dibuja sin pantalla, porque el worker no tiene DOM.
const SETUP_SOURCE = `
import os
import warnings

os.environ['MPLBACKEND'] = 'AGG'
warnings.filterwarnings('ignore', message='.*non-interactive.*')
`;

const FIGURES_SOURCE = `
import base64
import io
import json
import sys

images = []
pyplot = sys.modules.get('matplotlib.pyplot')
if pyplot is not None:
    for number in pyplot.get_fignums()[:8]:
        buffer = io.BytesIO()
        pyplot.figure(number).savefig(buffer, format='png', dpi=110, bbox_inches='tight')
        images.append(base64.b64encode(buffer.getvalue()).decode('ascii'))
    pyplot.close('all')
json.dumps(images)
`;

interface PythonNamespace {
  destroy: () => void;
}

interface Pyodide {
  globals: { get: (name: 'dict') => () => PythonNamespace };
  loadPackagesFromImports: (code: string, options: { messageCallback: () => void }) => Promise<unknown>;
  runPython: (code: string) => unknown;
  runPythonAsync: (code: string, options: { globals: PythonNamespace }) => Promise<unknown>;
  setStderr: (options: { batched: (text: string) => void }) => void;
  setStdin: (options: { error: true }) => void;
  setStdout: (options: { batched: (text: string) => void }) => void;
}

interface OutputCapture {
  text: () => string;
  write: (line: string) => void;
}

let runtime: Promise<Pyodide> | undefined;

const loadRuntime = async (): Promise<Pyodide> => {
  const module = await import(/* @vite-ignore */ `${INDEX_URL}pyodide.mjs`) as {
    loadPyodide: (options: { indexURL: string }) => Promise<Pyodide>;
  };
  const pyodide = await module.loadPyodide({ indexURL: INDEX_URL });
  pyodide.setStdin({ error: true });
  pyodide.runPython(SETUP_SOURCE);
  return pyodide;
};

const createCapture = (): OutputCapture => {
  let output = '';
  let truncated = false;
  return {
    text: () => output,
    write: (line) => {
      if (truncated) return;
      output += `${line}\n`;
      if (output.length > MAX_OUTPUT_CHARS) {
        output = `${output.slice(0, MAX_OUTPUT_CHARS - TRUNCATION_NOTICE.length)}${TRUNCATION_NOTICE}`;
        truncated = true;
      }
    },
  };
};

const errorMessage = (reason: unknown): string => (reason instanceof Error ? reason.message : String(reason));

// Quita los marcos internos de Pyodide: al alumno y al tutor solo les sirve su código.
const cleanTraceback = (message: string): string => {
  const lines = message.split('\n');
  const firstOwnLine = lines.findIndex((line) => line.startsWith('  File "<exec>"'));
  return firstOwnLine === -1 ? message : ['Traceback (most recent call last):', ...lines.slice(firstOwnLine)].join('\n');
};

const post = (message: PythonWorkerMessage): void => self.postMessage(message);

const runInNamespace = async (pyodide: Pyodide, code: string): Promise<unknown> => {
  const namespace = pyodide.globals.get('dict')();
  try {
    return await pyodide.runPythonAsync(code, { globals: namespace });
  } finally {
    namespace.destroy();
  }
};

const execute = async ({ id, code }: PythonWorkerRequest): Promise<void> => {
  const stdout = createCapture();
  const stderr = createCapture();
  let figures: string[] = [];
  let error: string | null = null;

  try {
    runtime ??= loadRuntime();
    const pyodide = await runtime;
    pyodide.setStdout({ batched: stdout.write });
    pyodide.setStderr({ batched: stderr.write });
    await pyodide.loadPackagesFromImports(code, { messageCallback: () => undefined });
    post({ type: 'started', id });
    try {
      await runInNamespace(pyodide, code);
    } catch (reason: unknown) {
      error = cleanTraceback(errorMessage(reason));
    }
    figures = JSON.parse(await runInNamespace(pyodide, FIGURES_SOURCE) as string) as string[];
  } catch (reason: unknown) {
    // Sin esto, un fallo de carga dejaría al worker sin poder reintentar.
    runtime = undefined;
    error = `No se pudo preparar Python: ${errorMessage(reason)}`;
  }

  post({ type: 'result', id, stdout: stdout.text(), stderr: stderr.text(), error, figures });
};

self.addEventListener('message', (event: MessageEvent<PythonWorkerRequest>) => void execute(event.data));
