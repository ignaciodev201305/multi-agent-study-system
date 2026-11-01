import { useRef, useState } from 'react';
import type { CSSProperties, KeyboardEvent, PointerEvent } from 'react';
import { BoardPanel } from './board/BoardPanel';
import { ProgressPanel } from './ProgressPanel';
import { PythonPanel } from './PythonPanel';
import { DEFAULT_SIDE_PANEL_WIDTH } from '../store/useStudyStore';
import type { BlockFocus, Board, SidePanelTab } from '../types';
import styles from '../App.module.css';

interface StudySidePanelProps {
  sessionId: string;
  title: string;
  backendUrl: string;
  board: Board;
  focusRequest: BlockFocus | null;
  isBusy: boolean;
  tab: SidePanelTab;
  pythonCode: string;
  width: number;
  focused: boolean;
  onTabChange: (tab: SidePanelTab) => void;
  onClose: () => void;
  onWidthChange: (width: number) => void;
  onFocusedChange: (focused: boolean) => void;
  onPythonCodeChange: (code: string) => void;
  onOpenInPython: (code: string) => void;
  onShareWithTutor: (message: string) => void;
}

const tabs: { id: SidePanelTab; label: string }[] = [
  { id: 'board', label: 'Pizarra' },
  { id: 'progress', label: 'Progreso' },
  { id: 'python', label: 'Python' },
];

const MIN_PANEL_WIDTH = 320;
const MIN_CHAT_WIDTH = 420;
const MAX_PANEL_RATIO = 0.6;
const KEYBOARD_STEP = 24;

// El panel comparte con el chat el ancho que deja la columna de material.
const clampWidth = (panel: HTMLElement, width: number): number => {
  const shell = panel.parentElement;
  const material = shell?.firstElementChild;
  if (!shell || !(material instanceof HTMLElement)) return width;
  const shared = shell.clientWidth - material.offsetWidth;
  const max = Math.max(MIN_PANEL_WIDTH, Math.min(shared * MAX_PANEL_RATIO, shared - MIN_CHAT_WIDTH));
  return Math.round(Math.min(Math.max(width, MIN_PANEL_WIDTH), max));
};

export function StudySidePanel({
  sessionId,
  title,
  backendUrl,
  board,
  focusRequest,
  isBusy,
  tab,
  pythonCode,
  width,
  focused,
  onTabChange,
  onClose,
  onWidthChange,
  onFocusedChange,
  onPythonCodeChange,
  onOpenInPython,
  onShareWithTutor,
}: StudySidePanelProps): JSX.Element {
  const panel = useRef<HTMLElement>(null);
  // Se guarda al soltar para no escribir en el navegador con cada movimiento.
  const [dragWidth, setDragWidth] = useState<number | null>(null);
  const currentWidth = dragWidth ?? width;

  const startDrag = (event: PointerEvent<HTMLDivElement>): void => {
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    setDragWidth(currentWidth);
  };

  const drag = (event: PointerEvent<HTMLDivElement>): void => {
    if (dragWidth === null || !panel.current) return;
    const right = panel.current.getBoundingClientRect().right;
    setDragWidth(clampWidth(panel.current, right - event.clientX));
  };

  const endDrag = (): void => {
    if (dragWidth !== null) onWidthChange(dragWidth);
    setDragWidth(null);
  };

  const handleResizeKey = (event: KeyboardEvent<HTMLDivElement>): void => {
    if ((event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') || !panel.current) return;
    event.preventDefault();
    const step = event.key === 'ArrowLeft' ? KEYBOARD_STEP : -KEYBOARD_STEP;
    onWidthChange(clampWidth(panel.current, currentWidth + step));
  };

  const openInPython = (code: string): void => {
    const replaces = pythonCode.trim() && pythonCode !== code;
    if (replaces && !window.confirm('¿Reemplazar el código que tienes en la pestaña Python?')) return;
    onOpenInPython(code);
  };

  return (
    <aside
      aria-label="Panel de estudio"
      className={styles.studyPanel}
      ref={panel}
      style={{ '--study-panel-width': `${currentWidth}px` } as CSSProperties}
    >
      {!focused && (
        <div
          aria-label="Ancho del panel"
          aria-orientation="vertical"
          aria-valuemin={MIN_PANEL_WIDTH}
          aria-valuenow={currentWidth}
          className={styles.panelResizer}
          data-dragging={dragWidth !== null ? true : undefined}
          onDoubleClick={() => onWidthChange(DEFAULT_SIDE_PANEL_WIDTH)}
          onKeyDown={handleResizeKey}
          onPointerCancel={endDrag}
          onPointerDown={startDrag}
          onPointerMove={drag}
          onPointerUp={endDrag}
          role="separator"
          tabIndex={0}
          title="Arrastra para cambiar el ancho; doble clic para restablecerlo"
        />
      )}
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
        <button
          aria-label={focused ? 'Reducir panel' : 'Expandir panel'}
          aria-pressed={focused}
          className={styles.iconButton}
          onClick={() => onFocusedChange(!focused)}
          title={focused ? 'Reducir panel (Esc)' : 'Expandir panel'}
          type="button"
        >
          <svg aria-hidden="true" viewBox="0 0 16 16" fill="none">
            <path
              d={focused ? 'M6.5 2.5v4h-4m7-4v4h4m-7 7v-4h-4m7 4v-4h4' : 'M2.5 6.5v-4h4m3 0h4v4m-11 3v4h4m3 0h4v-4'}
              stroke="currentColor"
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth="1.5"
            />
          </svg>
        </button>
        <button aria-label="Cerrar panel" className={styles.iconButton} onClick={onClose} title="Cerrar panel" type="button">
          <svg aria-hidden="true" viewBox="0 0 16 16" fill="none">
            <path d="m4.5 4.5 7 7m0-7-7 7" stroke="currentColor" strokeLinecap="round" strokeWidth="1.5" />
          </svg>
        </button>
      </header>
      <div className={styles.studyPanelBody} role="tabpanel">
        {tab === 'board' && (
          <BoardPanel
            board={board}
            focusRequest={focusRequest}
            onAskTutor={onShareWithTutor}
            onOpenInPython={openInPython}
            title={title}
          />
        )}
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
