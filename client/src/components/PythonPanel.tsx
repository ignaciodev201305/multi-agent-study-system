import { useEffect, useRef, useState } from 'react';
import { pythonRunner, tutorMessage } from '../services/pythonRunner';
import type { PythonResult } from '../services/pythonRunner';
import styles from '../App.module.css';

const MAX_CODE_CHARS = 20_000;

type RunPhase = 'idle' | 'loading' | 'running';

interface FinishedRun {
  code: string;
  result: PythonResult;
}

interface PythonPanelProps {
  hidden: boolean;
  code: string;
  onCodeChange: (code: string) => void;
  onShare: (message: string) => void;
}

const phaseText: Record<RunPhase, string> = {
  idle: '',
  loading: 'Preparando Python…',
  running: 'Ejecutando…',
};

export function PythonPanel({ hidden, code, onCodeChange, onShare }: PythonPanelProps): JSX.Element {
  const [phase, setPhase] = useState<RunPhase>('idle');
  const [finishedRun, setFinishedRun] = useState<FinishedRun | null>(null);
  const isRunning = useRef(false);
  const result = finishedRun?.result;

  // Sin el panel a la vista nadie podría detener la ejecución.
  useEffect(() => () => {
    if (isRunning.current) pythonRunner.terminate();
  }, []);

  const run = async (): Promise<void> => {
    if (isRunning.current || !code.trim()) return;
    isRunning.current = true;
    setFinishedRun(null);
    setPhase('loading');
    const nextResult = await pythonRunner.run(code, () => setPhase('running'));
    isRunning.current = false;
    setPhase('idle');
    if (nextResult.status !== 'stopped') setFinishedRun({ code, result: nextResult });
  };

  return (
    <div className={styles.pythonPanel} hidden={hidden}>
      <textarea
        aria-label="Código Python"
        className={styles.pythonEditor}
        maxLength={MAX_CODE_CHARS}
        onChange={(event) => onCodeChange(event.target.value)}
        placeholder="print('Hola')"
        spellCheck={false}
        value={code}
      />
      <div className={styles.pythonActions}>
        <button className={styles.uploadCta} disabled={phase !== 'idle' || !code.trim()} onClick={() => void run()} type="button">
          Ejecutar
        </button>
        <button className={styles.pythonButton} disabled={phase === 'idle'} onClick={() => pythonRunner.terminate()} type="button">
          Detener
        </button>
        <button
          className={styles.pythonButton}
          disabled={!finishedRun}
          onClick={() => finishedRun && onShare(tutorMessage(finishedRun.code, finishedRun.result))}
          type="button"
        >
          Enviar al tutor
        </button>
      </div>
      <p className={styles.pythonHint}>
        {phaseText[phase] || 'Corre solo en tu navegador, sin archivos ni red fiables. La primera vez descarga Python.'}
      </p>
      {result && (
        <div className={styles.pythonOutput} aria-live="polite">
          {result.stdout && <pre>{result.stdout}</pre>}
          {result.stderr && <pre data-kind="error">{result.stderr}</pre>}
          {result.error && <pre data-kind="error">{result.error}</pre>}
          {result.figures.map((figure, index) => (
            <img alt={`Figura ${index + 1}`} key={index} src={`data:image/png;base64,${figure}`} />
          ))}
          {!result.stdout && !result.stderr && !result.error && result.figures.length === 0 && (
            <p className={styles.pythonHint}>El código terminó sin salida.</p>
          )}
        </div>
      )}
    </div>
  );
}
