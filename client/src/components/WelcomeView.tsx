import { useEffect, useState } from 'react';
import { fetchDueReviews } from '../services/backend';
import { ModelSettingsPanel } from './ModelSettingsPanel';
import type { DueReview, StudyPhase, StudySession, Theme } from '../types';
import styles from '../App.module.css';

interface WelcomeViewProps {
  sessions: StudySession[];
  sessionsStatus: 'loading' | 'ready' | 'error';
  sessionsError: string | null;
  theme: Theme;
  backendUrl: string;
  authToken: string;
  onCreateSession: () => void;
  onOpenSession: (sessionId: string) => void;
  onDeleteSession: (sessionId: string) => Promise<void>;
  onRetryLoad: () => void;
  onBackendUrlChange: (url: string) => void;
  onAuthTokenChange: (token: string) => void;
  onToggleTheme: () => void;
  usesLocalToken: boolean;
}

const phaseText: Record<StudyPhase, string> = {
  setup: 'Preparación',
  clarification: 'Aclaraciones',
  study: 'En estudio',
};

// Evita una consulta por tecla al escribir la dirección o el token.
const DUE_REVIEWS_DELAY_MS = 400;

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
  sessionsStatus,
  sessionsError,
  theme,
  backendUrl,
  authToken,
  onCreateSession,
  onOpenSession,
  onDeleteSession,
  onRetryLoad,
  onBackendUrlChange,
  onAuthTokenChange,
  onToggleTheme,
  usesLocalToken,
}: WelcomeViewProps): JSX.Element {
  const [deletingSessionId, setDeletingSessionId] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [dueReviews, setDueReviews] = useState<DueReview[]>([]);
  const [modelsOpen, setModelsOpen] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const timer = window.setTimeout(() => {
      fetchDueReviews(backendUrl)
        .then((reviews) => {
          if (!cancelled) setDueReviews(reviews);
        })
        .catch(() => {
          // Sin backend, la sección simplemente no aparece.
          if (!cancelled) setDueReviews([]);
        });
    }, DUE_REVIEWS_DELAY_MS);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [authToken, backendUrl]);

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
          <img className={styles.brandMark} src="/logo.png" alt="" />
          <span>Estudio Multiagente</span>
        </div>
        <div className={styles.topbarActions}>
          <button className={styles.themeButton} onClick={() => setModelsOpen(true)} type="button">
            Modelos
          </button>
          <button className={styles.themeButton} onClick={onToggleTheme} type="button">
            {theme === 'dark' ? 'Tema claro' : 'Tema oscuro'}
          </button>
        </div>
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
            {usesLocalToken ? (
              <span className={styles.backendLabel}>Token: tomado del .env local</span>
            ) : (
              <>
                <label className={styles.backendLabel} htmlFor="backend-token">Token del servidor (APP_TOKEN)</label>
                <input
                  autoCapitalize="off"
                  autoComplete="off"
                  id="backend-token"
                  onChange={(event) => onAuthTokenChange(event.target.value)}
                  spellCheck={false}
                  type="password"
                  value={authToken}
                />
              </>
            )}
          </div>
        </section>

        <section className={styles.recentPanel} aria-label="Sesiones recientes">
          {dueReviews.length > 0 && (
            <div className={styles.dueSection}>
              <div className={styles.recentHeader}>
                <h2>Para repasar hoy</h2>
                <span className={styles.recentCount}>{dueReviews.length}</span>
              </div>
              <ul className={styles.recentList}>
                {dueReviews.map((review) => (
                  <li className={styles.recentRow} key={`${review.chat_id}-${review.topic_number}`}>
                    <button className={styles.recentButton} onClick={() => onOpenSession(review.chat_id)} type="button">
                      <span className={styles.recentTitle}>{review.topic}</span>
                      <span className={styles.recentMeta}>{review.chat_title}</span>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div className={styles.recentHeader}>
            <h2>Recientes</h2>
            <span className={styles.recentCount}>{sessions.length}</span>
          </div>

          {deleteError && <p className={styles.uploadError} role="alert">{deleteError}</p>}

          {sessionsStatus === 'error' ? (
            <div className={styles.emptyState}>
              <p>No se pudieron cargar las sesiones.</p>
              {sessionsError && <span>{sessionsError}</span>}
              <button className={styles.themeButton} onClick={onRetryLoad} type="button">Reintentar</button>
            </div>
          ) : sessions.length === 0 ? (
            <div className={styles.emptyState}>
              <p>{sessionsStatus === 'loading' ? 'Cargando sesiones…' : 'Aún no tienes sesiones.'}</p>
              {sessionsStatus === 'ready' && <span>Crea una para empezar a estudiar.</span>}
            </div>
          ) : (
            <ul className={styles.recentList}>
              {sessions.map((session) => (
                <li className={styles.recentRow} key={session.id}>
                  <button className={styles.recentButton} onClick={() => onOpenSession(session.id)} type="button">
                    <span className={styles.recentTitle}>{session.title}</span>
                    <span className={styles.recentMeta}>
                      <span className={styles.phaseBadge} data-phase={session.phase}>{phaseText[session.phase]}</span>
                      <span>{session.documentCount} documentos</span>
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
      {modelsOpen && <ModelSettingsPanel backendUrl={backendUrl} onClose={() => setModelsOpen(false)} />}
    </div>
  );
}
