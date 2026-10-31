import { useEffect, useRef, useState } from 'react';
import type { ChatMessage, GraphActivity, StudyPhase } from '../types';
import { MarkdownMessage } from './MarkdownMessage';
import styles from '../App.module.css';

// Abrir una sesión cuesta unos 13 ms por mensaje: se renderizan solo los últimos.
const VISIBLE_MESSAGES_STEP = 20;

const activityStatusText: Record<GraphActivity['status'], string> = {
  streaming: 'En curso',
  complete: 'Completado',
  error: 'Interrumpido',
};

const activityGraphText: Record<GraphActivity['graph'], string> = {
  context: 'Grafo de contexto',
  session: 'Grafo de estudio',
};

interface ConversationProps {
  title: string;
  phase: StudyPhase;
  messages: ChatMessage[];
  isThinking: boolean;
  graphActivities: GraphActivity[];
  error?: string;
  onDismissError: () => void;
}

export function Conversation({
  title,
  phase,
  messages,
  isThinking,
  graphActivities,
  error,
  onDismissError,
}: ConversationProps): JSX.Element {
  const scrollContainer = useRef<HTMLElement>(null);
  const latestActivity = graphActivities[graphActivities.length - 1];
  const [visibleCount, setVisibleCount] = useState(VISIBLE_MESSAGES_STEP);
  const visibleMessages = messages.slice(-visibleCount);

  useEffect(() => {
    const element = scrollContainer.current;
    if (element) element.scrollTop = element.scrollHeight;
  }, [messages.length, isThinking, graphActivities]);

  return (
    <main className={styles.conversation} ref={scrollContainer}>
      {error && (
        <div className={styles.errorBanner} role="alert">
          <span>{error}</span>
          <button aria-label="Cerrar aviso" onClick={onDismissError} type="button">Cerrar</button>
        </div>
      )}

      {messages.length === 0 ? (
        <section className={styles.welcomePanel}>
          <div className={styles.welcomeOverline}>SESIÓN DE ESTUDIO</div>
          <h1>¿Qué quieres estudiar?</h1>
          <p>Describe tu objetivo y tu nivel. Si hace falta, te haré algunas preguntas antes de empezar.</p>
          <div className={styles.suggestions} aria-label="Sugerencias">
            <span>Un tema que quieras aprender</span>
            <span>Tu nivel actual</span>
            <span>Qué quieres poder hacer</span>
          </div>
        </section>
      ) : (
        <div className={styles.messageColumn} aria-label={`Conversación: ${title}`}>
          {messages.length > visibleCount && (
            <button
              className={styles.loadEarlier}
              onClick={() => setVisibleCount((count) => count + VISIBLE_MESSAGES_STEP)}
              type="button"
            >
              Ver anteriores
            </button>
          )}
          {visibleMessages.map((message) => (
            <article
              className={`${styles.message} ${message.role === 'user' ? styles.messageUser : message.role === 'assistant' ? styles.messageAssistant : styles.messageSystem}`}
              key={message.id}
            >
              {message.role !== 'system' && (
                <div className={styles.messageMeta}>
                  <span>{message.role === 'user' ? 'Tú' : message.agent ?? 'Asistente'}</span>
                  {message.role === 'assistant' && <span>{phase === 'clarification' ? 'Contexto' : 'Agente'}</span>}
                </div>
              )}
              {message.role === 'assistant' ? (
                <MarkdownMessage content={message.content} />
              ) : message.role === 'system' ? (
                <div className={styles.systemMessage}>{message.content}</div>
              ) : (
                <div className={styles.userMessageContent}>{message.content}</div>
              )}
            </article>
          ))}
          {latestActivity && (
            <details className={styles.activityDetails}>
              <summary className={styles.activitySummary}>
                <span className={styles.activityDot} data-status={latestActivity.status} />
                <span>{activityGraphText[latestActivity.graph]}</span>
                <span className={styles.activitySummaryNode}>{latestActivity.node}</span>
                {latestActivity.step !== undefined && <span>Paso {latestActivity.step}</span>}
                <span>{activityStatusText[latestActivity.status]}</span>
                <span className={styles.activityToggle}>
                  <span className={styles.activityShow}>Ver pasos</span>
                  <span className={styles.activityHide}>Ocultar pasos</span>
                </span>
              </summary>
              <div className={styles.activityList}>
                {graphActivities.map((activity) => (
                  <section className={styles.activityItem} key={activity.id}>
                    <div className={styles.activityItemHeader}>
                      <span>{activity.node}</span>
                      <span>
                        {activity.step === undefined ? '' : `Paso ${activity.step} · `}
                        {activityStatusText[activity.status]}
                      </span>
                    </div>
                    <div aria-live="off" className={styles.graphActivity}>{activity.content}</div>
                  </section>
                ))}
              </div>
            </details>
          )}
          {isThinking && !latestActivity && (
            <div className={styles.thinkingIndicator} role="status" aria-live="polite">
              <span className={styles.thinkingDots} aria-hidden="true"><i /><i /><i /></span>
              <span>{phase === 'study' ? 'Ejecutando el grafo de estudio' : 'Preparando el contexto'}</span>
            </div>
          )}
        </div>
      )}
    </main>
  );
}
