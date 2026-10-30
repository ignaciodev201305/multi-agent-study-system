import { mathMarkdown } from '../../services/boardExport';
import type { BoardBlock as BoardBlockData } from '../../types';
import { MarkdownMessage } from '../MarkdownMessage';
import { CodeBlock } from './CodeBlock';
import { PlotBlock } from './PlotBlock';
import styles from '../../App.module.css';

interface BoardBlockProps {
  block: BoardBlockData;
}

const authorLabel = (author: string): string => author.replace(/_/g, ' ');

function BlockContent({ block }: BoardBlockProps): JSX.Element {
  switch (block.kind) {
    case 'markdown':
      return <MarkdownMessage content={block.text} />;
    case 'math':
      return <MarkdownMessage content={mathMarkdown(block.latex)} />;
    case 'steps':
      return (
        <ol className={styles.boardSteps}>
          {block.steps.map((step, index) => (
            <li key={index}>
              <MarkdownMessage content={step.text} />
              {step.latex && <MarkdownMessage content={mathMarkdown(step.latex)} />}
            </li>
          ))}
        </ol>
      );
    case 'table':
      return (
        <div className={styles.boardTable}>
          <table>
            <thead>
              <tr>
                {block.headers.map((header, index) => (
                  <th key={index}><MarkdownMessage content={header} /></th>
                ))}
              </tr>
            </thead>
            <tbody>
              {block.rows.map((row, rowIndex) => (
                <tr key={rowIndex}>
                  {row.map((cell, cellIndex) => (
                    <td key={cellIndex}><MarkdownMessage content={cell} /></td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    case 'code':
      return <CodeBlock code={block.code} language={block.language} />;
    case 'plot':
      return <PlotBlock block={block} title={block.title} />;
  }
}

export function BoardBlock({ block }: BoardBlockProps): JSX.Element {
  return (
    <article className={styles.boardBlock}>
      <header className={styles.boardBlockHeader}>
        <span className={styles.boardBlockTitle}>{block.title}</span>
        <span className={styles.boardBlockAuthor}>{authorLabel(block.author)}</span>
      </header>
      <BlockContent block={block} />
    </article>
  );
}
