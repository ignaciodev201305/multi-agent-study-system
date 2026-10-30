import type { BoardBlock } from '../types';

// Fuente Markdown de una fórmula, para mostrarla y para exportarla.
export const mathMarkdown = (latex: string): string => `$$\n${latex}\n$$`;

const tableCell = (text: string): string => text.replace(/\|/g, '\\|').replace(/\s*\n\s*/g, ' ');

const tableRow = (cells: string[]): string => `| ${cells.map(tableCell).join(' | ')} |`;

const codeFence = (code: string): string => {
  let fence = '```';
  while (code.includes(fence)) fence += '`';
  return fence;
};

const blockMarkdown = (block: BoardBlock): string => {
  switch (block.kind) {
    case 'markdown':
      return block.text;
    case 'math':
      return mathMarkdown(block.latex);
    case 'steps':
      return block.steps
        .map((step, index) => {
          const line = `${index + 1}. ${step.text}`;
          return step.latex ? `${line}\n\n${mathMarkdown(step.latex)}` : line;
        })
        .join('\n\n');
    case 'table':
      return [
        tableRow(block.headers),
        tableRow(block.headers.map(() => '---')),
        ...block.rows.map(tableRow),
      ].join('\n');
    case 'code': {
      const fence = codeFence(block.code);
      return `${fence}${block.language}\n${block.code}\n${fence}`;
    }
    case 'plot': {
      const functions = block.functions
        .map((item) => `- ${item.label ? `${item.label}: ` : ''}\`${item.expression}\``)
        .join('\n');
      return `Gráfico en x entre ${block.x_min} y ${block.x_max}:\n\n${functions}`;
    }
  }
};

export const boardMarkdown = (title: string, blocks: BoardBlock[]): string => {
  const sections = blocks.map((block) => {
    const content = blockMarkdown(block);
    return block.title ? `## ${block.title}\n\n${content}` : content;
  });
  return `${[`# ${title}`, ...sections].join('\n\n')}\n`;
};

export const downloadMarkdown = (title: string, content: string): void => {
  const slug = title.replace(/[^\p{L}\p{N}]+/gu, '-').replace(/^-|-$/g, '').slice(0, 60);
  const url = URL.createObjectURL(new Blob([content], { type: 'text/markdown;charset=utf-8' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = `${slug || 'pizarra'}.md`;
  link.click();
  URL.revokeObjectURL(url);
};
