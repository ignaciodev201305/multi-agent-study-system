import { useEffect } from 'react';
import { SessionView } from './components/SessionView';
import { WelcomeView } from './components/WelcomeView';
import { useStudyStore } from './store/useStudyStore';
import styles from './App.module.css';
import type { Board, GraphActivity } from './types';

const EMPTY_GRAPH_ACTIVITIES: GraphActivity[] = [];
const EMPTY_BOARD: Board = { blocks: [], revision: 0 };

export default function App(): JSX.Element {
  const sessions = useStudyStore((state) => state.sessions);
  const activeSessionId = useStudyStore((state) => state.activeSessionId);
  const view = useStudyStore((state) => state.view);
  const draftSession = useStudyStore((state) => state.draftSession);
  const theme = useStudyStore((state) => state.theme);
  const backendUrl = useStudyStore((state) => state.backendUrl);
  const authToken = useStudyStore((state) => state.authToken);
  const connectionStates = useStudyStore((state) => state.connectionStates);
  const errors = useStudyStore((state) => state.errors);
  const graphActivities = useStudyStore((state) => state.graphActivities[state.activeSessionId] ?? EMPTY_GRAPH_ACTIVITIES);
  const board = useStudyStore((state) => state.boards[state.activeSessionId] ?? EMPTY_BOARD);
  const usage = useStudyStore((state) => state.usage[state.activeSessionId]);
  const sidePanelOpen = useStudyStore((state) => state.sidePanelOpen);
  const sidePanelWidth = useStudyStore((state) => state.sidePanelWidth);
  const sidePanelTab = useStudyStore((state) => state.sidePanelTab);
  const setSidePanelOpen = useStudyStore((state) => state.setSidePanelOpen);
  const setSidePanelWidth = useStudyStore((state) => state.setSidePanelWidth);
  const setSidePanelTab = useStudyStore((state) => state.setSidePanelTab);
  const pythonCode = useStudyStore((state) => state.pythonCode[state.activeSessionId] ?? '');
  const composerDraft = useStudyStore((state) => state.composerDraft);
  const setPythonCode = useStudyStore((state) => state.setPythonCode);
  const openInPython = useStudyStore((state) => state.openInPython);
  const setComposerDraft = useStudyStore((state) => state.setComposerDraft);
  const clearComposerDraft = useStudyStore((state) => state.clearComposerDraft);
  const createSession = useStudyStore((state) => state.createSession);
  const deleteSession = useStudyStore((state) => state.deleteSession);
  const selectSession = useStudyStore((state) => state.selectSession);
  const goHome = useStudyStore((state) => state.goHome);
  const setTheme = useStudyStore((state) => state.setTheme);
  const setBackendUrl = useStudyStore((state) => state.setBackendUrl);
  const setAuthToken = useStudyStore((state) => state.setAuthToken);
  const addDocument = useStudyStore((state) => state.addDocument);
  const sendMessage = useStudyStore((state) => state.sendMessage);
  const clearError = useStudyStore((state) => state.clearError);
  const resyncSession = useStudyStore((state) => state.resyncSession);
  const activeSession = sessions.find((session) => session.id === activeSessionId)
    ?? (draftSession && draftSession.id === activeSessionId ? draftSession : undefined);
  const isSessionView = view === 'session' && Boolean(activeSession);

  useEffect(() => {
    const handleShortcut = (event: KeyboardEvent): void => {
      const modifier = event.ctrlKey || event.metaKey;
      if (!modifier) return;

      if (event.key.toLowerCase() === 'n') {
        event.preventDefault();
        createSession();
      } else if (event.key.toLowerCase() === 'l') {
        event.preventDefault();
        document.querySelector<HTMLTextAreaElement>('[data-composer="true"]')?.focus();
      } else if (/^[1-9]$/.test(event.key)) {
        const target = sessions[Number(event.key) - 1];
        if (target) {
          event.preventDefault();
          selectSession(target.id);
        }
      }
    };

    window.addEventListener('keydown', handleShortcut);
    return () => window.removeEventListener('keydown', handleShortcut);
  }, [createSession, selectSession, sessions]);

  return (
    <div className={styles.root} data-theme={theme}>
      {isSessionView && activeSession ? (
        <SessionView
          backendUrl={backendUrl}
          board={board}
          composerDraft={composerDraft?.sessionId === activeSession.id ? composerDraft.content : undefined}
          error={errors[activeSession.id]}
          graphActivities={graphActivities}
          isBusy={(connectionStates[activeSession.id] ?? 'idle') === 'connecting'}
          onBack={goHome}
          onComposerDraftApplied={clearComposerDraft}
          onDismissError={() => clearError(activeSession.id)}
          onDocumentProcessed={() => void resyncSession(activeSession.id)}
          onOpenInPython={(code) => openInPython(activeSession.id, code)}
          onPythonCodeChange={(code) => setPythonCode(activeSession.id, code)}
          onSend={sendMessage}
          onShareWithTutor={(message) => setComposerDraft(activeSession.id, message)}
          onSidePanelOpenChange={setSidePanelOpen}
          onSidePanelTabChange={setSidePanelTab}
          onSidePanelWidthChange={setSidePanelWidth}
          onToggleTheme={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
          onUploaded={(document) => addDocument(activeSession.id, document)}
          pythonCode={pythonCode}
          session={activeSession}
          sidePanelOpen={sidePanelOpen}
          sidePanelTab={sidePanelTab}
          sidePanelWidth={sidePanelWidth}
          status={connectionStates[activeSession.id] ?? 'idle'}
          theme={theme}
          usage={usage}
        />
      ) : (
        <WelcomeView
          authToken={authToken}
          backendUrl={backendUrl}
          onCreateSession={createSession}
          onDeleteSession={deleteSession}
          onOpenSession={selectSession}
          onToggleTheme={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
          onAuthTokenChange={setAuthToken}
          onBackendUrlChange={setBackendUrl}
          sessions={sessions}
          theme={theme}
        />
      )}
    </div>
  );
}
