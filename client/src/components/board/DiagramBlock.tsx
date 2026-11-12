import { memo, useEffect, useId, useState } from 'react';
import { useStudyStore } from '../../store/useStudyStore';
import styles from '../../App.module.css';

interface DiagramBlockProps {
  source: string;
  title?: string;
}

type DiagramState = { status: 'loading' } | { status: 'ready'; url: string } | { status: 'error' };

export const DiagramBlock = memo(function DiagramBlock({ source, title }: DiagramBlockProps): JSX.Element {
  const renderId = `diagram-${useId().replace(/:/g, '')}`;
  const theme = useStudyStore((state) => state.theme);
  const [state, setState] = useState<DiagramState>({ status: 'loading' });
  const [zoomed, setZoomed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setState({ status: 'loading' });
    // Mermaid pesa cientos de KB: se descarga solo cuando aparece un diagrama.
    void import('mermaid')
      .then(async ({ default: mermaid }) => {
        mermaid.initialize({
          startOnLoad: false,
          securityLevel: 'strict',
          theme: theme === 'dark' ? 'dark' : 'default',
          htmlLabels: false,
          flowchart: { htmlLabels: false },
        });
        // Se valida antes de dibujar para que un texto inválido no deje restos en la página.
        if (!(await mermaid.parse(source, { suppressErrors: true }))) throw new Error('invalid');
        const { svg } = await mermaid.render(renderId, source);
        // Como imagen, el SVG no puede ejecutar scripts ni tocar la página.
        if (!cancelled) setState({ status: 'ready', url: `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}` });
      })
      .catch(() => {
        if (!cancelled) setState({ status: 'error' });
      });
    return () => {
      cancelled = true;
    };
  }, [renderId, source, theme]);

  if (state.status === 'error') {
    return (
      <div className={styles.diagram}>
        <p className={styles.diagramNote}>No se pudo dibujar el diagrama; este es su texto:</p>
        <pre className={styles.diagramSource}>{source}</pre>
      </div>
    );
  }
  if (state.status === 'loading') return <p className={styles.diagramNote}>Dibujando diagrama…</p>;
  return (
    <button
      aria-label={zoomed ? 'Ajustar el diagrama al ancho' : 'Ver el diagrama a tamaño real'}
      className={styles.diagram}
      data-zoomed={zoomed ? true : undefined}
      onClick={() => setZoomed(!zoomed)}
      type="button"
    >
      <img alt={title ? `Diagrama: ${title}` : 'Diagrama'} src={state.url} />
    </button>
  );
});
