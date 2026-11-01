interface MarkdownNode {
  type: string;
  value?: string;
  url?: string;
  children?: MarkdownNode[];
}

const BLOCK_REFERENCE = /\[\[(b\d+)\]\]/g;
const BLOCK_LINK_PREFIX = '#board-';
// Dentro de estos nodos un enlace nuevo quedaría anidado.
const SKIPPED_PARENTS = new Set(['link', 'linkReference']);

export const blockIdFromHref = (href?: string): string | undefined =>
  href?.startsWith(BLOCK_LINK_PREFIX) ? href.slice(BLOCK_LINK_PREFIX.length) : undefined;

const splitReferences = (value: string): MarkdownNode[] => {
  const nodes: MarkdownNode[] = [];
  let position = 0;
  for (const match of value.matchAll(BLOCK_REFERENCE)) {
    if (match.index > position) nodes.push({ type: 'text', value: value.slice(position, match.index) });
    nodes.push({
      type: 'link',
      url: `${BLOCK_LINK_PREFIX}${match[1]}`,
      children: [{ type: 'text', value: match[1] }],
    });
    position = match.index + match[0].length;
  }
  if (position < value.length) nodes.push({ type: 'text', value: value.slice(position) });
  return nodes;
};

// Solo cambia nodos de texto: el código y las fórmulas son nodos de otro tipo.
export const replaceBlockReferences = (node: MarkdownNode): void => {
  if (!node.children || SKIPPED_PARENTS.has(node.type)) return;
  node.children = node.children.flatMap((child) => {
    if (child.type === 'text' && child.value) return splitReferences(child.value);
    replaceBlockReferences(child);
    return [child];
  });
};

export const remarkBlockLinks = () => replaceBlockReferences;
