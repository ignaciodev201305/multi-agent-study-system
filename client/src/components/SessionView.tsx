import { Composer } from './Composer';
import { Conversation } from './Conversation';
import { DocsPanel } from './DocsPanel';
import { StudySidePanel } from './StudySidePanel';
import type {
  Board,
  ConnectionStatus,
  GraphActivity,
  SidePanelTab,
  StudyPhase,
  StudySession,
  Theme,
  UploadedDocument,
} from '../types';
import styles from '../App.module.css';

interface SessionViewProps {
  session: StudySession;
  backendUrl: string;
  status: ConnectionStatus;
  isBusy: boolean;
  error?: string;
  graphActivities: GraphActivity[];
  theme: Theme;
  board: Board;
  sidePanelOpen: boolean;
  sidePanelTab: SidePanelTab;
  onToggleTheme: () => void;
  onBack: () => void;
  onSend: (content: string) => void;
  onDismissError: () => void;
  onUploaded: (document: UploadedDocument) => void;
  onSidePanelOpenChange: (open: boolean) => void;
  onSidePanelTabChange: (tab: SidePanelTab) => void;
}

const phaseText: Record<StudyPhase, string> = {
  setup: 'Preparación',
  clarification: 'Aclaraciones',
  study: 'En estudio',
};

export function SessionView({
  session,
  backendUrl,
  status,
  isBusy,
  error,
  graphActivities,
  theme,
  board,
  sidePanelOpen,
  sidePanelTab,
  onToggleTheme,
  onBack,
  onSend,
  onDismissError,
  onUploaded,
  onSidePanelOpenChange,
  onSidePanelTabChange,
}: SessionViewProps): JSX.Element {
  return (
    <div className={styles.appShell}>
      <DocsPanel
        backendUrl={backendUrl}
        documents={session.documents}
        locked={!session.messages.some((message) => message.role === 'user')}
        onUploaded={onUploaded}
        sessionId={session.id}
      />
      <section className={styles.workspace}>
        <header className={styles.topbar}>
          <div className={styles.topbarLeft}>
            <button className={styles.iconButton} onClick={onBack} title="Volver a las sesiones" type="button" aria-label="Volver a las sesiones">
              <svg aria-hidden="true" viewBox="0 0 16 16" fill="none">
                <path d="M10 3.5 5.5 8l4.5 4.5" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" />
              </svg>
            </button>
            <div className={styles.topbarTitle}>
              <span>{session.title}</span>
              <span className={styles.phaseLabel} data-status={status}>{phaseText[session.phase]}</span>
            </div>
          </div>
          <div className={styles.topbarActions}>
            <button
              aria-expanded={sidePanelOpen}
              className={styles.themeButton}
              onClick={() => onSidePanelOpenChange(!sidePanelOpen)}
              type="button"
            >
              Pizarra{board.blocks.length > 0 ? ` (${board.blocks.length})` : ''}
            </button>
            <button className={styles.themeButton} onClick={onToggleTheme} type="button">
              {theme === 'dark' ? 'Tema claro' : 'Tema oscuro'}
            </button>
          </div>
        </header>
        <Conversation
          error={error}
          graphActivities={graphActivities}
          isThinking={isBusy}
          messages={session.messages}
          onDismissError={onDismissError}
          phase={session.phase}
          title={session.title}
        />
        <Composer isBusy={isBusy} onSend={onSend} phase={session.phase} />
      </section>
      {sidePanelOpen && (
        <StudySidePanel
          backendUrl={backendUrl}
          board={board}
          isBusy={isBusy}
          key={session.id}
          onClose={() => onSidePanelOpenChange(false)}
          onTabChange={onSidePanelTabChange}
          sessionId={session.id}
          tab={sidePanelTab}
          title={session.title}
        />
      )}
    </div>
  );
}
