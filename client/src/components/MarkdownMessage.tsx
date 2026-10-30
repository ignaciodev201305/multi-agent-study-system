import { memo } from 'react';
import ReactMarkdown from 'react-markdown';
import rehypeKatex from 'rehype-katex';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import 'katex/dist/katex.min.css';
import styles from '../App.module.css';

interface MarkdownMessageProps {
  content: string;
}

// Procesar Markdown y KaTeX es caro: solo se repite si cambia el texto.
export const MarkdownMessage = memo(function MarkdownMessage({ content }: MarkdownMessageProps): JSX.Element {
  return (
    <div className={styles.markdownContent}>
      <ReactMarkdown remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={[rehypeKatex]}>
        {content}
      </ReactMarkdown>
    </div>
  );
});
