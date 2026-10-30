import { useState } from 'react';
import styles from '../../App.module.css';

interface CodeBlockProps {
  code: string;
  language: string;
  onOpenInPython?: () => void;
}

const COPIED_MESSAGE_MS = 1500;

export function CodeBlock({ code, language, onOpenInPython }: CodeBlockProps): JSX.Element {
  const [copied, setCopied] = useState(false);

  const copy = async (): Promise<void> => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      window.setTimeout(() => setCopied(false), COPIED_MESSAGE_MS);
    } catch {
      setCopied(false);
    }
  };

  return (
    <div className={styles.boardCode}>
      <div className={styles.boardCodeBar}>
        <span>{language}</span>
        <span className={styles.boardCodeActions}>
          {onOpenInPython && <button onClick={onOpenInPython} type="button">Abrir en Python</button>}
          <button onClick={() => void copy()} type="button">{copied ? 'Copiado' : 'Copiar'}</button>
        </span>
      </div>
      <pre><code>{code}</code></pre>
    </div>
  );
}
