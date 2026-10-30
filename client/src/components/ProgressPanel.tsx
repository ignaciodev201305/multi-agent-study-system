import { useEffect, useState } from 'react';
import { fetchChatProgress } from '../services/backend';
import type { ChatProgress, ProgressStatus, ProgressTopic } from '../types';
import styles from '../App.module.css';

interface ProgressPanelProps {
  sessionId: string;
  backendUrl: string;
  isBusy: boolean;
}

const statusText: Record<ProgressStatus, string> = {
  dominado: 'Dominado',
  en_progreso: 'En progreso',
  con_dificultad: 'Con dificultad',
};

const reviewText = (topic: ProgressTopic): string => {
  const dueAt = new Date(topic.due_at);
  if (dueAt.getTime() <= Date.now()) return 'Repaso pendiente';
  return `Repaso: ${dueAt.toLocaleDateString('es', { day: '2-digit', month: 'short' })}`;
};

export function ProgressPanel({ sessionId, backendUrl, isBusy }: ProgressPanelProps): JSX.Element | null {
  const [progress, setProgress] = useState<ChatProgress | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Se refresca al abrir el panel y cuando empieza o termina un turno.
  useEffect(() => {
    let cancelled = false;
    fetchChatProgress(backendUrl, sessionId)
      .then((result) => {
        if (cancelled) return;
        setProgress(result);
        setError(null);
      })
      .catch((reason: unknown) => {
        if (!cancelled) setError(reason instanceof Error ? reason.message : 'No se pudo consultar el progreso.');
      });
    return () => {
      cancelled = true;
    };
  }, [backendUrl, isBusy, sessionId]);

  if (error) return <p className={styles.panelError} role="alert">{error}</p>;
  if (!progress) return null;

  if (progress.syllabus.length === 0) {
    return (
      <div className={styles.panelEmpty}>
        <p>Aún no hay temario.</p>
        <span>Aparecerá cuando el contexto de la sesión esté listo.</span>
      </div>
    );
  }

  const topics = new Map(progress.topics.map((topic) => [topic.topic_number, topic]));

  return (
    <ol className={styles.progressList}>
      {progress.syllabus.map((item, index) => {
        const number = index + 1;
        const topic = topics.get(number);
        const evidence = topic?.reviews[0]?.evidence;
        return (
          <li className={styles.progressItem} key={number}>
            <div className={styles.progressHeader}>
              <span className={styles.progressNumber}>{number}</span>
              <span className={styles.progressTopic}>{item}</span>
            </div>
            <div className={styles.progressMeta}>
              <span className={styles.progressBadge} data-status={topic?.status ?? 'pending'}>
                {topic ? statusText[topic.status] : 'Sin evaluar'}
              </span>
              {topic && <span>{reviewText(topic)}</span>}
            </div>
            {evidence && <p className={styles.progressEvidence}>{evidence}</p>}
          </li>
        );
      })}
    </ol>
  );
}
