import { BoardPanel } from './board/BoardPanel';
import { ProgressPanel } from './ProgressPanel';
import { PythonPanel } from './PythonPanel';
import type { Board, SidePanelTab } from '../types';
import styles from '../App.module.css';

interface StudySidePanelProps {
  sessionId: string;
  title: string;
  backendUrl: string;
  board: Board;
  isBusy: boolean;
  tab: SidePanelTab;
  pythonCode: string;
  onTabChange: (tab: SidePanelTab) => void;
  onClose: () => void;
  onPythonCodeChange: (code: string) => void;
  onOpenInPython: (code: string) => void;
  onShareWithTutor: (message: string) => void;
}

const tabs: { id: SidePanelTab; label: string }[] = [
  { id: 'board', label: 'Pizarra' },
  { id: 'progress', label: 'Progreso' },
  { id: 'python', label: 'Python' },
];

export function StudySidePanel({
  sessionId,
  title,
  backendUrl,
  board,
  isBusy,
  tab,
  pythonCode,
  onTabChange,
  onClose,
  onPythonCodeChange,
  onOpenInPython,
  onShareWithTutor,
}: StudySidePanelProps): JSX.Element {
  const openInPython = (code: string): void => {
    const replaces = pythonCode.trim() && pythonCode !== code;
    if (replaces && !window.confirm('¿Reemplazar el código que tienes en la pestaña Python?')) return;
    onOpenInPython(code);
  };

  return (
    <aside className={styles.studyPanel} aria-label="Panel de estudio">
      <header className={styles.studyPanelHeader}>
        <div className={styles.typeToggle} role="tablist">
          {tabs.map((item) => (
            <button
              aria-selected={tab === item.id}
              className={styles.typeOption}
              data-active={tab === item.id ? true : undefined}
              key={item.id}
              onClick={() => onTabChange(item.id)}
              role="tab"
              type="button"
            >
              {item.label}
            </button>
          ))}
        </div>
        <button aria-label="Cerrar panel" className={styles.iconButton} onClick={onClose} title="Cerrar panel" type="button">
          <svg aria-hidden="true" viewBox="0 0 16 16" fill="none">
            <path d="m4.5 4.5 7 7m0-7-7 7" stroke="currentColor" strokeLinecap="round" strokeWidth="1.5" />
          </svg>
        </button>
      </header>
      <div className={styles.studyPanelBody} role="tabpanel">
        {tab === 'board' && <BoardPanel board={board} onOpenInPython={openInPython} title={title} />}
        {tab === 'progress' && <ProgressPanel backendUrl={backendUrl} isBusy={isBusy} sessionId={sessionId} />}
        {/* Sigue montado para no perder la ejecución al cambiar de pestaña. */}
        <PythonPanel
          code={pythonCode}
          hidden={tab !== 'python'}
          onCodeChange={onPythonCodeChange}
          onShare={onShareWithTutor}
        />
      </div>
    </aside>
  );
}
