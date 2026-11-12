import { useCallback, useEffect, useState } from 'react';
import { listChatPdfs } from '../services/backend';
import { Composer } from './Composer';
import { Conversation } from './Conversation';
import { DocsPanel } from './DocsPanel';
import { ModelSettingsPanel } from './ModelSettingsPanel';
import { StudySidePanel } from './StudySidePanel';
import { UsageIndicator } from './UsageIndicator';
import { UsagePanel } from './UsagePanel';
import type {
  BackendDocument,
  BlockFocus,
  Board,
  ChatMessage,
  ConnectionStatus,
  GraphActivity,
  SidePanelTab,
  StudyPhase,
  StudySession,
  Theme,
  UsageTotals,
} from '../types';
import styles from '../App.module.css';

interface SessionViewProps {
  session: StudySession;
  messages: ChatMessage[];
  backendUrl: string;
  status: ConnectionStatus;
  isBusy: boolean;
  deepMode: boolean;
  error?: string;
  graphActivities: GraphActivity[];
  theme: Theme;
  board: Board;
  usage?: UsageTotals;
  docsPanelOpen: boolean;
  sidePanelOpen: boolean;
  sidePanelWidth: number;
  sidePanelTab: SidePanelTab;
  pythonCode: string;
  composerDraft?: string;
  onToggleTheme: () => void;
  onBack: () => void;
  onSend: (content: string) => void;
  onDismissError: () => void;
  onDocsPanelOpenChange: (open: boolean) => void;
  onDocumentProcessed: () => void;
  onSidePanelOpenChange: (open: boolean) => void;
  onSidePanelTabChange: (tab: SidePanelTab) => void;
  onSidePanelWidthChange: (width: number) => void;
  onPythonCodeChange: (code: string) => void;
  onOpenInPython: (code: string) => void;
  onShareWithTutor: (message: string) => void;
  onComposerDraftApplied: () => void;
  onToggleDeepMode: () => void;
}

const phaseText: Record<StudyPhase, string> = {
  setup: 'Preparación',
  clarification: 'Aclaraciones',
  study: 'En estudio',
};

export function SessionView({
  session,
  messages,
  backendUrl,
  status,
  isBusy,
  deepMode,
  error,
  graphActivities,
  theme,
  board,
  usage,
  docsPanelOpen,
  sidePanelOpen,
  sidePanelWidth,
  sidePanelTab,
  pythonCode,
  composerDraft,
  onToggleTheme,
  onBack,
  onSend,
  onDismissError,
  onDocsPanelOpenChange,
  onDocumentProcessed,
  onSidePanelOpenChange,
  onSidePanelTabChange,
  onSidePanelWidthChange,
  onPythonCodeChange,
  onOpenInPython,
  onShareWithTutor,
  onComposerDraftApplied,
  onToggleDeepMode,
}: SessionViewProps): JSX.Element {
  const [modelsOpen, setModelsOpen] = useState(false);
  const [usageOpen, setUsageOpen] = useState(false);
  const [panelExpanded, setPanelExpanded] = useState(false);
  const [focusRequest, setFocusRequest] = useState<BlockFocus | null>(null);
  const [documents, setDocuments] = useState<BackendDocument[]>([]);
  const focused = panelExpanded && sidePanelOpen;

  // Estable para que el chat no vuelva a procesar su Markdown.
  const openBlock = useCallback((blockId: string): void => {
    onSidePanelOpenChange(true);
    onSidePanelTabChange('board');
    setFocusRequest({ blockId });
  }, [onSidePanelOpenChange, onSidePanelTabChange]);
  const dialogOpen = modelsOpen || usageOpen;

  const refreshDocuments = useCallback((): void => {
    listChatPdfs(backendUrl, session.id).then(setDocuments).catch(() => undefined);
  }, [backendUrl, session.id]);

  useEffect(() => setDocuments([]), [session.id]);

  useEffect(() => refreshDocuments(), [docsPanelOpen, refreshDocuments]);

  useEffect(() => {
    if (!focused || dialogOpen) return undefined;
    const handleKey = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') setPanelExpanded(false);
    };
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [focused, dialogOpen]);

  return (
    <div
      className={styles.appShell}
      data-docs-closed={docsPanelOpen ? undefined : true}
      data-focused={focused ? true : undefined}
    >
      <DocsPanel
        backendUrl={backendUrl}
        documents={documents}
        locked={!messages.some((message) => message.role === 'user')}
        onClose={() => onDocsPanelOpenChange(false)}
        onProcessed={onDocumentProcessed}
        onUploaded={refreshDocuments}
        open={docsPanelOpen}
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
            <UsageIndicator onOpen={() => setUsageOpen(true)} usage={usage} />
            <button className={styles.themeButton} onClick={() => setModelsOpen(true)} type="button">
              Modelos
            </button>
            {!focused && (
              <button
                aria-expanded={docsPanelOpen}
                className={styles.themeButton}
                onClick={() => onDocsPanelOpenChange(!docsPanelOpen)}
                type="button"
              >
                Material{documents.length > 0 ? ` (${documents.length})` : ''}
              </button>
            )}
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
          key={session.id}
          error={error}
          graphActivities={graphActivities}
          isThinking={isBusy}
          messages={messages}
          onBlockLink={openBlock}
          onDismissError={onDismissError}
          phase={session.phase}
          title={session.title}
        />
        <Composer
          deepMode={deepMode}
          draft={composerDraft}
          isBusy={isBusy}
          onDraftApplied={onComposerDraftApplied}
          onSend={onSend}
          onToggleDeepMode={onToggleDeepMode}
          phase={session.phase}
        />
      </section>
      {sidePanelOpen && (
        <StudySidePanel
          backendUrl={backendUrl}
          board={board}
          focusRequest={focusRequest}
          focused={focused}
          isBusy={isBusy}
          key={session.id}
          onClose={() => {
            setPanelExpanded(false);
            onSidePanelOpenChange(false);
          }}
          onFocusedChange={setPanelExpanded}
          onOpenInPython={onOpenInPython}
          onPythonCodeChange={onPythonCodeChange}
          onShareWithTutor={onShareWithTutor}
          onTabChange={onSidePanelTabChange}
          onWidthChange={onSidePanelWidthChange}
          pythonCode={pythonCode}
          sessionId={session.id}
          tab={sidePanelTab}
          title={session.title}
          width={sidePanelWidth}
        />
      )}
      {usageOpen && (
        <UsagePanel backendUrl={backendUrl} onClose={() => setUsageOpen(false)} sessionId={session.id} usage={usage} />
      )}
      {modelsOpen && (
        <ModelSettingsPanel backendUrl={backendUrl} onClose={() => setModelsOpen(false)} sessionId={session.id} />
      )}
    </div>
  );
}
