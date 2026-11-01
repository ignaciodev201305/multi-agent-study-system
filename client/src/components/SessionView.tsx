import { useCallback, useEffect, useState } from 'react';
import { Composer } from './Composer';
import { Conversation } from './Conversation';
import { DocsPanel } from './DocsPanel';
import { ModelSettingsPanel } from './ModelSettingsPanel';
import { StudySidePanel } from './StudySidePanel';
import { UsageIndicator } from './UsageIndicator';
import { UsagePanel } from './UsagePanel';
import type {
  BlockFocus,
  Board,
  ConnectionStatus,
  GraphActivity,
  SidePanelTab,
  StudyPhase,
  StudySession,
  Theme,
  UploadedDocument,
  UsageTotals,
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
  onUploaded: (document: UploadedDocument) => void;
  onDocumentProcessed: () => void;
  onSidePanelOpenChange: (open: boolean) => void;
  onSidePanelTabChange: (tab: SidePanelTab) => void;
  onSidePanelWidthChange: (width: number) => void;
  onPythonCodeChange: (code: string) => void;
  onOpenInPython: (code: string) => void;
  onShareWithTutor: (message: string) => void;
  onComposerDraftApplied: () => void;
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
  onUploaded,
  onDocumentProcessed,
  onSidePanelOpenChange,
  onSidePanelTabChange,
  onSidePanelWidthChange,
  onPythonCodeChange,
  onOpenInPython,
  onShareWithTutor,
  onComposerDraftApplied,
}: SessionViewProps): JSX.Element {
  const [modelsOpen, setModelsOpen] = useState(false);
  const [usageOpen, setUsageOpen] = useState(false);
  const [panelExpanded, setPanelExpanded] = useState(false);
  const [focusRequest, setFocusRequest] = useState<BlockFocus | null>(null);
  const focused = panelExpanded && sidePanelOpen;

  // Estable para que el chat no vuelva a procesar su Markdown.
  const openBlock = useCallback((blockId: string): void => {
    onSidePanelOpenChange(true);
    onSidePanelTabChange('board');
    setFocusRequest({ blockId });
  }, [onSidePanelOpenChange, onSidePanelTabChange]);
  const dialogOpen = modelsOpen || usageOpen;

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
        documents={session.documents}
        locked={!session.messages.some((message) => message.role === 'user')}
        onClose={() => onDocsPanelOpenChange(false)}
        onProcessed={onDocumentProcessed}
        onUploaded={onUploaded}
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
                Material{session.documents.length > 0 ? ` (${session.documents.length})` : ''}
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
          messages={session.messages}
          onBlockLink={openBlock}
          onDismissError={onDismissError}
          phase={session.phase}
          title={session.title}
        />
        <Composer
          draft={composerDraft}
          isBusy={isBusy}
          onDraftApplied={onComposerDraftApplied}
          onSend={onSend}
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
