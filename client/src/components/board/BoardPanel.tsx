import { useEffect, useRef, useState } from 'react';
import { boardMarkdown, downloadMarkdown } from '../../services/boardExport';
import type { Board, BlockFocus } from '../../types';
import { BoardBlock } from './BoardBlock';
import styles from '../../App.module.css';

interface BoardPanelProps {
  board: Board;
  title: string;
  focusRequest: BlockFocus | null;
  onOpenInPython: (code: string) => void;
  onAskTutor: (message: string) => void;
}

const HIGHLIGHT_MS = 1600;

export function BoardPanel({ board, title, focusRequest, onOpenInPython, onAskTutor }: BoardPanelProps): JSX.Element {
  const list = useRef<HTMLDivElement>(null);
  const [highlightedId, setHighlightedId] = useState<string | null>(null);
  const blockCount = board.blocks.length;

  // Muestra el bloque recién agregado.
  useEffect(() => {
    const element = list.current;
    if (element) element.scrollTop = element.scrollHeight;
  }, [blockCount]);

  // Va después del desplazamiento anterior para ganarle al abrir el panel.
  useEffect(() => {
    if (!focusRequest) return undefined;
    setHighlightedId(null);
    const element = list.current?.querySelector(`[data-block-id="${focusRequest.blockId}"]`);
    if (!element) return undefined;
    element.scrollIntoView({ block: 'center' });
    setHighlightedId(focusRequest.blockId);
    const timer = window.setTimeout(() => setHighlightedId(null), HIGHLIGHT_MS);
    return () => window.clearTimeout(timer);
  }, [focusRequest]);

  if (blockCount === 0) {
    return (
      <div className={styles.panelEmpty}>
        <p>La pizarra está vacía.</p>
        <span>Aquí aparecerán las fórmulas, pasos, tablas y gráficos de la sesión.</span>
      </div>
    );
  }

  return (
    <>
      <div className={styles.panelActions}>
        <span>{blockCount} {blockCount === 1 ? 'bloque' : 'bloques'}</span>
        <button onClick={() => downloadMarkdown(title, boardMarkdown(title, board.blocks))} type="button">
          Exportar Markdown
        </button>
      </div>
      <div className={styles.boardList} ref={list}>
        {board.blocks.map((block) => (
          <BoardBlock
            block={block}
            highlighted={block.id === highlightedId}
            key={block.id}
            onAskTutor={onAskTutor}
            onOpenInPython={onOpenInPython}
          />
        ))}
      </div>
    </>
  );
}
