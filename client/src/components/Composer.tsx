import { useRef } from 'react';
import styles from '../App.module.css';

interface ComposerProps {
  phase: 'setup' | 'clarification' | 'study';
  isBusy: boolean;
  onSend: (content: string) => void;
}

const placeholders: Record<ComposerProps['phase'], string> = {
  setup: 'Describe qué quieres estudiar…',
  clarification: 'Responde a las preguntas…',
  study: 'Escribe un mensaje…',
};

export function Composer({ phase, isBusy, onSend }: ComposerProps): JSX.Element {
  const input = useRef<HTMLTextAreaElement>(null);

  const submit = (): void => {
    const value = input.current?.value ?? '';
    if (!value.trim() || isBusy) return;
    onSend(value);
    if (input.current) {
      input.current.value = '';
      input.current.style.height = 'auto';
    }
  };

  return (
    <form
      className={styles.composerWrap}
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
    >
      <div className={styles.composerBox}>
        <textarea
          aria-label="Mensaje"
          data-composer="true"
          disabled={isBusy}
          onInput={(event) => {
            const target = event.currentTarget;
            target.style.height = 'auto';
            target.style.height = `${Math.min(target.scrollHeight, 180)}px`;
          }}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey) {
              event.preventDefault();
              submit();
            }
          }}
          placeholder={placeholders[phase]}
          ref={input}
          rows={1}
        />
        <button className={styles.sendButton} disabled={isBusy} type="submit" aria-label="Enviar mensaje">
          {isBusy ? (
            <span className={styles.buttonSpinner} aria-hidden="true" />
          ) : (
            <svg aria-hidden="true" viewBox="0 0 20 20" fill="none">
              <path d="M10 15V5M5.5 9.5 10 5l4.5 4.5" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.7" />
            </svg>
          )}
        </button>
      </div>
      <div className={styles.composerHint}>
        <span>Enter para enviar · Shift + Enter para salto de línea</span>
        {phase === 'study' && <span>Respuestas en Markdown</span>}
      </div>
    </form>
  );
}
