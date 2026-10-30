import { useEffect, useRef } from 'react';
import { boardMarkdown, downloadMarkdown } from '../../services/boardExport';
import type { Board } from '../../types';
import { BoardBlock } from './BoardBlock';
import styles from '../../App.module.css';

interface BoardPanelProps {
  board: Board;
  title: string;
}

export function BoardPanel({ board, title }: BoardPanelProps): JSX.Element {
  const list = useRef<HTMLDivElement>(null);
  const blockCount = board.blocks.length;

  // Muestra el bloque recién agregado.
  useEffect(() => {
    const element = list.current;
    if (element) element.scrollTop = element.scrollHeight;
  }, [blockCount]);

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
        {board.blocks.map((block) => <BoardBlock block={block} key={block.id} />)}
      </div>
    </>
  );
}
