import { memo, useMemo } from 'react';
import ReactMarkdown from 'react-markdown';
import type { Components } from 'react-markdown';
import rehypeKatex from 'rehype-katex';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import 'katex/dist/katex.min.css';
import { blockIdFromHref, remarkBlockLinks } from '../services/blockLinks';
import styles from '../App.module.css';

interface MarkdownMessageProps {
  content: string;
  // Convierte las referencias [[b3]] en enlaces a la pizarra.
  onBlockLink?: (blockId: string) => void;
}

const BASE_PLUGINS = [remarkGfm, remarkMath];
const LINK_PLUGINS = [...BASE_PLUGINS, remarkBlockLinks];
const REHYPE_PLUGINS = [rehypeKatex];

// Procesar Markdown y KaTeX es caro: solo se repite si cambia el texto.
export const MarkdownMessage = memo(function MarkdownMessage({ content, onBlockLink }: MarkdownMessageProps): JSX.Element {
  const components = useMemo<Components | undefined>(() => {
    if (!onBlockLink) return undefined;
    return {
      a: ({ href, children }) => {
        const blockId = blockIdFromHref(href);
        if (!blockId) return <a href={href}>{children}</a>;
        return <button className={styles.blockLink} onClick={() => onBlockLink(blockId)} type="button">{children}</button>;
      },
    };
  }, [onBlockLink]);

  return (
    <div className={styles.markdownContent}>
      <ReactMarkdown
        components={components}
        rehypePlugins={REHYPE_PLUGINS}
        remarkPlugins={onBlockLink ? LINK_PLUGINS : BASE_PLUGINS}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
});
