import type { BoardBlock } from '../types';

// Fuente Markdown de una fórmula, para mostrarla y para exportarla.
export const mathMarkdown = (latex: string): string => `$$\n${latex}\n$$`;

const tableCell = (text: string): string => text.replace(/\|/g, '\\|').replace(/\s*\n\s*/g, ' ');

const tableRow = (cells: string[]): string => `| ${cells.map(tableCell).join(' | ')} |`;

// La cerca crece si el texto ya contiene una, para que no pueda cerrarse antes de tiempo.
export const fenced = (language: string, text: string): string => {
  let fence = '```';
  while (text.includes(fence)) fence += '`';
  return `${fence}${language}\n${text}\n${fence}`;
};

export const blockMarkdown = (block: BoardBlock): string => {
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
    case 'code':
      return fenced(block.language, block.code);
    case 'plot': {
      const functions = block.functions
        .map((item) => `- ${item.label ? `${item.label}: ` : ''}\`${item.expression}\``)
        .join('\n');
      const marks = (block.points ?? [])
        .map((point) => `- ${point.label ? `${point.label}: ` : ''}(${point.x}, ${point.y})`)
        .join('\n');
      return `Gráfico en x entre ${block.x_min} y ${block.x_max}:\n\n${functions}${marks ? `\n\nPuntos:\n\n${marks}` : ''}`;
    }
    case 'diagram':
      return fenced('mermaid', block.mermaid);
    case 'chart': {
      const axes = [block.x_label, block.y_label].filter(Boolean).join(' / ');
      const series = block.series
        .map((item, index) => `- ${item.label ?? `Serie ${index + 1}`}: ${item.points.length} puntos`)
        .join('\n');
      return `Gráfico de datos${axes ? ` (${axes})` : ''}:\n\n${series}`;
    }
    case 'exercise': {
      const hints = (block.hints ?? []).map((hint, index) => `Pista ${index + 1}: ${hint}`);
      return [block.statement, ...hints, `Solución: ${block.solution}`].join('\n\n');
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
