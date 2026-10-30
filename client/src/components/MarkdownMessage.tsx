import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import styles from '../App.module.css';

interface MarkdownMessageProps {
  content: string;
}

export function MarkdownMessage({ content }: MarkdownMessageProps): JSX.Element {
  return (
    <div className={styles.markdownContent}>
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
    </div>
  );
}
