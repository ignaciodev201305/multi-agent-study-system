import { useState } from 'react';
import type { BoardBlockContent } from '../../types';
import { MarkdownMessage } from '../MarkdownMessage';
import styles from '../../App.module.css';

type ExerciseContent = Extract<BoardBlockContent, { kind: 'exercise' }>;

interface ExerciseBlockProps {
  blockId: string;
  block: ExerciseContent;
  onAskTutor: (message: string) => void;
}

// La solución se oculta solo en pantalla: sirve para practicar, no para evaluar.
export function ExerciseBlock({ blockId, block, onAskTutor }: ExerciseBlockProps): JSX.Element {
  const [hintsShown, setHintsShown] = useState(0);
  const [solutionShown, setSolutionShown] = useState(false);
  const [answer, setAnswer] = useState('');
  const hints = block.hints ?? [];

  return (
    <div className={styles.boardExercise}>
      <MarkdownMessage content={block.statement} />
      {hints.slice(0, hintsShown).map((hint, index) => (
        <div className={styles.boardExerciseHint} key={index}>
          <span>Pista {index + 1}</span>
          <MarkdownMessage content={hint} />
        </div>
      ))}
      {solutionShown && (
        <div className={styles.boardExerciseSolution}>
          <span>Solución</span>
          <MarkdownMessage content={block.solution} />
        </div>
      )}
      <textarea
        aria-label="Tu respuesta"
        onChange={(event) => setAnswer(event.target.value)}
        placeholder="Escribe tu respuesta…"
        rows={2}
        value={answer}
      />
      <div className={styles.boardExerciseActions}>
        {hintsShown < hints.length && (
          <button onClick={() => setHintsShown(hintsShown + 1)} type="button">
            Ver pista ({hintsShown + 1}/{hints.length})
          </button>
        )}
        <button onClick={() => setSolutionShown(!solutionShown)} type="button">
          {solutionShown ? 'Ocultar solución' : 'Ver solución'}
        </button>
        <button
          disabled={!answer.trim()}
          onClick={() => onAskTutor(`Mi respuesta al ejercicio [[${blockId}]]: ${answer.trim()}`)}
          type="button"
        >
          Enviar al tutor
        </button>
      </div>
    </div>
  );
}
