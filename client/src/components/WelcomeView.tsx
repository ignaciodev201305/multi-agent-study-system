import { useState } from 'react';
import type { StudyPhase, StudySession, Theme } from '../types';
import styles from '../App.module.css';

interface WelcomeViewProps {
  sessions: StudySession[];
  theme: Theme;
  backendUrl: string;
  onCreateSession: () => void;
  onOpenSession: (sessionId: string) => void;
  onDeleteSession: (sessionId: string) => Promise<void>;
  onBackendUrlChange: (url: string) => void;
  onToggleTheme: () => void;
}

const phaseText: Record<StudyPhase, string> = {
  setup: 'Preparación',
  clarification: 'Aclaraciones',
  study: 'En estudio',
};

const formatDate = (timestamp: number): string => {
  const date = new Date(timestamp);
  const today = new Date();
  const isToday = date.toDateString() === today.toDateString();
  return isToday
    ? date.toLocaleTimeString('es', { hour: '2-digit', minute: '2-digit' })
    : date.toLocaleDateString('es', { day: '2-digit', month: 'short' });
};

export function WelcomeView({
  sessions,
  theme,
  backendUrl,
  onCreateSession,
  onOpenSession,
  onDeleteSession,
  onBackendUrlChange,
  onToggleTheme,
}: WelcomeViewProps): JSX.Element {
  const orderedSessions = [...sessions].sort((a, b) => b.updatedAt - a.updatedAt);
  const [deletingSessionId, setDeletingSessionId] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const handleDelete = async (session: StudySession): Promise<void> => {
    const confirmed = window.confirm(`¿Borrar la sesión «${session.title}»? Esta acción no se puede deshacer.`);
    if (!confirmed || deletingSessionId) return;

    setDeletingSessionId(session.id);
    setDeleteError(null);
    try {
      await onDeleteSession(session.id);
    } catch (error: unknown) {
      setDeleteError(error instanceof Error ? error.message : 'No se pudo eliminar la sesión.');
    } finally {
      setDeletingSessionId(null);
    }
  };

  return (
    <div className={styles.homeShell}>
      <header className={styles.homeHeader}>
        <div className={styles.brand}>
          <span className={styles.brandMark} aria-hidden="true">EM</span>
          <span>Estudio Multiagente</span>
        </div>
        <button className={styles.themeButton} onClick={onToggleTheme} type="button">
          {theme === 'dark' ? 'Tema claro' : 'Tema oscuro'}
        </button>
      </header>

      <div className={styles.homeBody}>
        <section className={styles.homeIntro}>
          <div className={styles.homeOverline}>SISTEMA DE ESTUDIO MULTIAGENTE</div>
          <h1 className={styles.homeTitle}>Tus sesiones de estudio</h1>
          <p className={styles.homeText}>
            Abre una sesión para continuar donde la dejaste o crea una nueva.
            Cada sesión guarda su conversación, su contexto y el material que subas.
          </p>
          <button className={styles.primaryButton} onClick={onCreateSession} type="button">
            <span aria-hidden="true">+</span>
            Nueva sesión
            <kbd>Ctrl N</kbd>
          </button>

          <div className={styles.homeBackend}>
            <label className={styles.backendLabel} htmlFor="backend-url">Backend WebSocket</label>
            <input
              autoCapitalize="off"
              autoComplete="url"
              id="backend-url"
              onChange={(event) => onBackendUrlChange(event.target.value)}
              spellCheck={false}
              type="url"
              value={backendUrl}
            />
          </div>
        </section>

        <section className={styles.recentPanel} aria-label="Sesiones recientes">
          <div className={styles.recentHeader}>
            <h2>Recientes</h2>
            <span className={styles.recentCount}>{sessions.length}</span>
          </div>

          {deleteError && <p className={styles.uploadError} role="alert">{deleteError}</p>}

          {orderedSessions.length === 0 ? (
            <div className={styles.emptyState}>
              <p>Aún no tienes sesiones.</p>
              <span>Crea una para empezar a estudiar.</span>
            </div>
          ) : (
            <ul className={styles.recentList}>
              {orderedSessions.map((session) => (
                <li className={styles.recentRow} key={session.id}>
                  <button className={styles.recentButton} onClick={() => onOpenSession(session.id)} type="button">
                    <span className={styles.recentTitle}>{session.title}</span>
                    <span className={styles.recentMeta}>
                      <span className={styles.phaseBadge} data-phase={session.phase}>{phaseText[session.phase]}</span>
                      <span>{session.messages.length} mensajes</span>
                      <span>{session.documents.length} documentos</span>
                      <span className={styles.recentDate}>{formatDate(session.updatedAt)}</span>
                    </span>
                  </button>
                  <button
                    aria-label={`Borrar sesión ${session.title}`}
                    className={styles.deleteSessionButton}
                    disabled={deletingSessionId === session.id}
                    onClick={() => void handleDelete(session)}
                    title="Borrar sesión"
                    type="button"
                  >
                    {deletingSessionId === session.id ? '…' : '×'}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}
