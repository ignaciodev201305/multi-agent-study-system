import { useEffect, useState } from 'react';
import { SessionView } from './components/SessionView';
import { WelcomeView } from './components/WelcomeView';
import { useStudyStore } from './store/useStudyStore';
import styles from './App.module.css';
import type { Board, ChatMessage, GraphActivity } from './types';

const EMPTY_GRAPH_ACTIVITIES: GraphActivity[] = [];
const EMPTY_MESSAGES: ChatMessage[] = [];
const EMPTY_BOARD: Board = { blocks: [], revision: 0 };
const LOCAL_HOSTS = new Set(['127.0.0.1', 'localhost', '[::1]']);
// Evita una consulta por tecla al escribir la dirección o el token.
const SESSIONS_RELOAD_DELAY_MS = 400;

// El token del .env local solo vale para un backend en esta misma máquina.
const isLocalBackend = (backendUrl: string): boolean => {
  try {
    return LOCAL_HOSTS.has(new URL(backendUrl).hostname);
  } catch {
    return false;
  }
};

export default function App(): JSX.Element {
  const sessions = useStudyStore((state) => state.sessions);
  const sessionsStatus = useStudyStore((state) => state.sessionsStatus);
  const sessionsError = useStudyStore((state) => state.sessionsError);
  const messages = useStudyStore((state) => state.messages[state.activeSessionId] ?? EMPTY_MESSAGES);
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
  const docsPanelOpen = useStudyStore((state) => state.docsPanelOpen);
  const sidePanelOpen = useStudyStore((state) => state.sidePanelOpen);
  const sidePanelWidth = useStudyStore((state) => state.sidePanelWidth);
  const sidePanelTab = useStudyStore((state) => state.sidePanelTab);
  const setDocsPanelOpen = useStudyStore((state) => state.setDocsPanelOpen);
  const setSidePanelOpen = useStudyStore((state) => state.setSidePanelOpen);
  const setSidePanelWidth = useStudyStore((state) => state.setSidePanelWidth);
  const setSidePanelTab = useStudyStore((state) => state.setSidePanelTab);
  const pythonCode = useStudyStore((state) => state.pythonCode[state.activeSessionId] ?? '');
  const composerDraft = useStudyStore((state) => state.composerDraft);
  const setPythonCode = useStudyStore((state) => state.setPythonCode);
  const openInPython = useStudyStore((state) => state.openInPython);
  const setComposerDraft = useStudyStore((state) => state.setComposerDraft);
  const clearComposerDraft = useStudyStore((state) => state.clearComposerDraft);
  const deepModes = useStudyStore((state) => state.deepModes);
  const toggleDeepMode = useStudyStore((state) => state.toggleDeepMode);
  const createSession = useStudyStore((state) => state.createSession);
  const deleteSession = useStudyStore((state) => state.deleteSession);
  const selectSession = useStudyStore((state) => state.selectSession);
  const goHome = useStudyStore((state) => state.goHome);
  const setTheme = useStudyStore((state) => state.setTheme);
  const setBackendUrl = useStudyStore((state) => state.setBackendUrl);
  const setAuthToken = useStudyStore((state) => state.setAuthToken);
  const loadSessions = useStudyStore((state) => state.loadSessions);
  const sendMessage = useStudyStore((state) => state.sendMessage);
  const clearError = useStudyStore((state) => state.clearError);
  const resyncSession = useStudyStore((state) => state.resyncSession);
  const activeSession = sessions.find((session) => session.id === activeSessionId)
    ?? (draftSession && draftSession.id === activeSessionId ? draftSession : undefined);
  const isSessionView = view === 'session' && Boolean(activeSession);
  const [localToken, setLocalToken] = useState('');
  const usesLocalToken = localToken !== '' && isLocalBackend(backendUrl);

  useEffect(() => {
    void window.desktop?.getLocalToken().then(setLocalToken);
  }, []);

  useEffect(() => {
    if (usesLocalToken && authToken !== localToken) setAuthToken(localToken);
  }, [authToken, localToken, setAuthToken, usesLocalToken]);

  useEffect(() => {
    const timer = window.setTimeout(() => void loadSessions(), SESSIONS_RELOAD_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [authToken, backendUrl, loadSessions]);

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
          docsPanelOpen={docsPanelOpen}
          error={errors[activeSession.id]}
          graphActivities={graphActivities}
          deepMode={Boolean(deepModes[activeSession.id])}
          isBusy={(connectionStates[activeSession.id] ?? 'idle') === 'connecting'}
          onBack={goHome}
          onComposerDraftApplied={clearComposerDraft}
          onToggleDeepMode={() => toggleDeepMode(activeSession.id)}
          onDismissError={() => clearError(activeSession.id)}
          onDocsPanelOpenChange={setDocsPanelOpen}
          onDocumentProcessed={() => void resyncSession(activeSession.id)}
          onOpenInPython={(code) => openInPython(activeSession.id, code)}
          onPythonCodeChange={(code) => setPythonCode(activeSession.id, code)}
          onSend={sendMessage}
          onShareWithTutor={(message) => setComposerDraft(activeSession.id, message)}
          onSidePanelOpenChange={setSidePanelOpen}
          onSidePanelTabChange={setSidePanelTab}
          onSidePanelWidthChange={setSidePanelWidth}
          onToggleTheme={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
          messages={messages}
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
          onRetryLoad={() => void loadSessions()}
          sessions={sessions}
          sessionsError={sessionsError}
          sessionsStatus={sessionsStatus}
          theme={theme}
          usesLocalToken={usesLocalToken}
        />
      )}
    </div>
  );
}
