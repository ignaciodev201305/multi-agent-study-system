import { useEffect, useMemo, useRef, useState } from 'react';
import { blockMarkdown, boardMarkdown, downloadMarkdown } from '../../services/boardExport';
import type { BlockFocus, Board, BoardBlock as BoardBlockData } from '../../types';
import { BoardBlock } from './BoardBlock';
import styles from '../../App.module.css';

interface BoardPanelProps {
  board: Board;
  title: string;
  focusRequest: BlockFocus | null;
  onOpenInPython: (code: string) => void;
  onAskTutor: (message: string) => void;
}

interface BlockGroup {
  key: string;
  label: string;
  blocks: BoardBlockData[];
}

const HIGHLIGHT_MS = 1600;
// Con pocos bloques el filtro solo ocuparía espacio.
const FILTER_MIN_BLOCKS = 6;
const ALL_KINDS = 'all';

const kindText: Record<BoardBlockData['kind'], string> = {
  markdown: 'Notas',
  math: 'Fórmulas',
  steps: 'Pasos',
  table: 'Tablas',
  code: 'Código',
  plot: 'Gráficos',
  exercise: 'Ejercicios',
};

// Los temas siguen el orden del temario y lo demás queda al final.
const groupByTopic = (blocks: BoardBlockData[]): BlockGroup[] => {
  const topics = new Map<number, BlockGroup>();
  const general: BoardBlockData[] = [];
  for (const block of blocks) {
    if (block.topic_number === undefined) {
      general.push(block);
      continue;
    }
    const group = topics.get(block.topic_number);
    if (group) group.blocks.push(block);
    else {
      topics.set(block.topic_number, {
        key: `topic-${block.topic_number}`,
        label: `${block.topic_number}. ${block.topic ?? 'Tema'}`,
        blocks: [block],
      });
    }
  }
  const groups = [...topics.entries()].sort(([left], [right]) => left - right).map(([, group]) => group);
  return general.length > 0 ? [...groups, { key: 'general', label: 'General', blocks: general }] : groups;
};

const matchesQuery = (block: BoardBlockData, query: string): boolean =>
  `${block.title ?? ''}\n${blockMarkdown(block)}`.toLowerCase().includes(query);

export function BoardPanel({ board, title, focusRequest, onOpenInPython, onAskTutor }: BoardPanelProps): JSX.Element {
  const list = useRef<HTMLDivElement>(null);
  const highlightTimer = useRef<number | undefined>(undefined);
  const [highlightedId, setHighlightedId] = useState<string | null>(null);
  const [pendingFocus, setPendingFocus] = useState<BlockFocus | null>(null);
  const [query, setQuery] = useState('');
  const [kind, setKind] = useState(ALL_KINDS);
  const blockCount = board.blocks.length;
  const needle = query.trim().toLowerCase();
  const isFiltering = needle !== '' || kind !== ALL_KINDS;

  const kinds = useMemo(() => [...new Set(board.blocks.map((block) => block.kind))], [board.blocks]);
  const visibleBlocks = useMemo(
    () => board.blocks.filter((block) => (kind === ALL_KINDS || block.kind === kind) && matchesQuery(block, needle)),
    [board.blocks, kind, needle],
  );
  const hasTopics = visibleBlocks.some((block) => block.topic_number !== undefined);

  // Muestra el bloque recién agregado.
  useEffect(() => {
    const element = list.current;
    if (element) element.scrollTop = element.scrollHeight;
  }, [blockCount]);

  // Un filtro activo podría esconder el bloque pedido.
  useEffect(() => {
    if (!focusRequest) return;
    setHighlightedId(null);
    setQuery('');
    setKind(ALL_KINDS);
    setPendingFocus(focusRequest);
  }, [focusRequest]);

  useEffect(() => {
    if (!pendingFocus || isFiltering) return;
    setPendingFocus(null);
    const element = list.current?.querySelector(`[data-block-id="${pendingFocus.blockId}"]`);
    if (!element) return;
    element.closest('details')?.setAttribute('open', '');
    element.scrollIntoView({ block: 'center' });
    setHighlightedId(pendingFocus.blockId);
    window.clearTimeout(highlightTimer.current);
    highlightTimer.current = window.setTimeout(() => setHighlightedId(null), HIGHLIGHT_MS);
  }, [pendingFocus, isFiltering]);

  useEffect(() => () => window.clearTimeout(highlightTimer.current), []);

  if (blockCount === 0) {
    return (
      <div className={styles.panelEmpty}>
        <p>La pizarra está vacía.</p>
        <span>Aquí aparecerán las fórmulas, pasos, tablas y gráficos de la sesión.</span>
      </div>
    );
  }

  const renderBlocks = (blocks: BoardBlockData[]): JSX.Element => (
    <div className={styles.boardGroupBlocks}>
      {blocks.map((block) => (
        <BoardBlock
          block={block}
          highlighted={block.id === highlightedId}
          key={block.id}
          onAskTutor={onAskTutor}
          onOpenInPython={onOpenInPython}
        />
      ))}
    </div>
  );

  return (
    <>
      <div className={styles.panelActions}>
        <span>{blockCount} {blockCount === 1 ? 'bloque' : 'bloques'}</span>
        <button onClick={() => downloadMarkdown(title, boardMarkdown(title, board.blocks))} type="button">
          Exportar Markdown
        </button>
      </div>
      {blockCount >= FILTER_MIN_BLOCKS && (
        <div className={styles.boardFilters}>
          <input
            aria-label="Buscar en la pizarra"
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Buscar…"
            type="search"
            value={query}
          />
          <select aria-label="Filtrar por tipo" onChange={(event) => setKind(event.target.value)} value={kind}>
            <option value={ALL_KINDS}>Todo</option>
            {kinds.map((item) => <option key={item} value={item}>{kindText[item]}</option>)}
          </select>
        </div>
      )}
      <div className={styles.boardList} ref={list}>
        {visibleBlocks.length === 0 && <p className={styles.usageEmpty}>Sin resultados.</p>}
        {hasTopics
          ? groupByTopic(visibleBlocks).map((group) => (
            <details className={styles.boardGroup} key={group.key} open>
              <summary>{group.label} <span>{group.blocks.length}</span></summary>
              {renderBlocks(group.blocks)}
            </details>
          ))
          : visibleBlocks.length > 0 && renderBlocks(visibleBlocks)}
      </div>
    </>
  );
}
