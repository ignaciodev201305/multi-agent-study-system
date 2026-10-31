import { create } from 'zustand';
import { createJSONStorage, persist } from 'zustand/middleware';
import { backendConnections, deleteChat, fetchChatSnapshot, setAuthToken as setBackendAuthToken } from '../services/backend';
import type {
  AppView,
  BackendEvent,
  Board,
  BoardPatch,
  ChatMessage,
  ChatSnapshot,
  ComposerDraft,
  ConnectionStatus,
  GraphActivity,
  SidePanelTab,
  StudySession,
  Theme,
  UploadedDocument,
  UsageTotals,
} from '../types';

const DEFAULT_BACKEND_URL = 'ws://127.0.0.1:8000';
const EMPTY_BOARD: Board = { blocks: [], revision: 0 };
const MAX_PYTHON_CODE_CHARS = 20_000;
const TURN_POLL_INTERVAL_MS = 3_000;
const TURN_POLL_MAX_ATTEMPTS = 60;

// Seguimiento por sesión de un turno que corre sin socket, por ejemplo tras recargar.
const turnPolls = new Map<string, { timer: ReturnType<typeof setTimeout>; attempts: number }>();

const stopTurnPoll = (sessionId: string): void => {
  const poll = turnPolls.get(sessionId);
  if (poll) clearTimeout(poll.timer);
  turnPolls.delete(sessionId);
};

const createId = (): string => crypto.randomUUID();

const createSession = (): StudySession => ({
  id: createId(),
  title: 'Nueva sesión',
  phase: 'setup',
  messages: [],
  documents: [],
  updatedAt: Date.now(),
});

const createMessage = (role: ChatMessage['role'], content: string, agent?: string): ChatMessage => ({
  id: createId(),
  role,
  content,
  agent,
  createdAt: Date.now(),
});

const setActivityStatus = (activities: GraphActivity[], status: GraphActivity['status']): GraphActivity[] =>
  activities.map((activity) => ({ ...activity, status }));

const applyBoardPatch = (board: Board, patch: BoardPatch): Board => {
  if (patch.op === 'clear') return { blocks: [], revision: patch.revision };
  if (patch.op === 'remove') {
    return {
      blocks: board.blocks.filter((block) => block.id !== patch.block_id),
      revision: patch.revision,
    };
  }
  const exists = board.blocks.some((block) => block.id === patch.block.id);
  return {
    blocks: exists
      ? board.blocks.map((block) => block.id === patch.block.id ? patch.block : block)
      : [...board.blocks, patch.block],
    revision: patch.revision,
  };
};

// Agrega las respuestas guardadas en el servidor que no llegaron por el socket.
const mergeSnapshot = (session: StudySession, snapshot: ChatSnapshot): StudySession => {
  const messages = [...session.messages];
  let changed = false;
  const localAssistant = new Set(
    messages.filter((message) => message.role === 'assistant').map((message) => message.content),
  );

  let previousUser: string | undefined;
  for (const serverMessage of snapshot.messages) {
    if (serverMessage.role === 'user') {
      previousUser = serverMessage.content;
      continue;
    }
    if (localAssistant.has(serverMessage.content)) continue;
    // Se inserta tras el mensaje del usuario que lo originó.
    let insertAt = messages.length;
    if (previousUser !== undefined) {
      for (let index = messages.length - 1; index >= 0; index -= 1) {
        if (messages[index].role === 'user' && messages[index].content === previousUser) {
          insertAt = index + 1;
          break;
        }
      }
    }
    messages.splice(insertAt, 0, createMessage('assistant', serverMessage.content, 'Supervisor'));
    localAssistant.add(serverMessage.content);
    changed = true;
  }

  let phase = session.phase;
  if (snapshot.phase === 'study' && phase !== 'study') {
    messages.push(createMessage('system', 'Contexto aprobado. El equipo de tutores está listo.'));
    phase = 'study';
    changed = true;
  } else if (snapshot.phase === 'clarification' && snapshot.pending_question && !localAssistant.has(snapshot.pending_question)) {
    messages.push(createMessage('assistant', snapshot.pending_question, 'Preparación'));
    phase = 'clarification';
    changed = true;
  }

  return changed ? { ...session, phase, messages, updatedAt: Date.now() } : session;
};

interface PersistedState {
  sessions: StudySession[];
  activeSessionId: string;
  theme: Theme;
  backendUrl: string;
  authToken: string;
  sidePanelOpen: boolean;
  sidePanelTab: SidePanelTab;
  pythonCode: Record<string, string | undefined>;
}

interface StudyState extends PersistedState {
  view: AppView;
  draftSession: StudySession | null;
  connectionStates: Record<string, ConnectionStatus>;
  errors: Record<string, string | undefined>;
  graphActivities: Record<string, GraphActivity[] | undefined>;
  // La fuente de verdad es el servidor: no se guarda en el navegador.
  boards: Record<string, Board | undefined>;
  usage: Record<string, UsageTotals | undefined>;
  composerDraft: ComposerDraft | null;
  setSidePanelOpen: (open: boolean) => void;
  setSidePanelTab: (tab: SidePanelTab) => void;
  setPythonCode: (sessionId: string, code: string) => void;
  openInPython: (sessionId: string, code: string) => void;
  setComposerDraft: (sessionId: string, content: string) => void;
  clearComposerDraft: () => void;
  createSession: () => void;
  deleteSession: (sessionId: string) => Promise<void>;
  selectSession: (sessionId: string) => void;
  goHome: () => void;
  setTheme: (theme: Theme) => void;
  setBackendUrl: (backendUrl: string) => void;
  setAuthToken: (authToken: string) => void;
  addDocument: (sessionId: string, document: UploadedDocument) => void;
  sendMessage: (content: string) => void;
  receiveEvent: (sessionId: string, event: BackendEvent) => void;
  resyncSession: (sessionId: string) => Promise<void>;
  setConnectionStatus: (sessionId: string, status: ConnectionStatus) => void;
  setError: (sessionId: string, message: string) => void;
  clearError: (sessionId: string) => void;
}

const normalizeSessions = (sessions: StudySession[]): StudySession[] =>
  sessions.map((session) => ({ ...session, documents: session.documents ?? [] }));

export const useStudyStore = create<StudyState>()(
  persist(
    (set, get) => ({
      sessions: [],
      activeSessionId: '',
      view: 'home',
      draftSession: null,
      theme: 'dark',
      backendUrl: DEFAULT_BACKEND_URL,
      authToken: '',
      sidePanelOpen: true,
      sidePanelTab: 'board',
      pythonCode: {},
      connectionStates: {},
      errors: {},
      graphActivities: {},
      boards: {},
      usage: {},
      composerDraft: null,
      setSidePanelOpen: (sidePanelOpen) => set({ sidePanelOpen }),
      setSidePanelTab: (sidePanelTab) => set({ sidePanelTab }),
      setPythonCode: (sessionId, code) => set((state) => ({
        pythonCode: { ...state.pythonCode, [sessionId]: code.slice(0, MAX_PYTHON_CODE_CHARS) },
      })),
      openInPython: (sessionId, code) => set((state) => ({
        pythonCode: { ...state.pythonCode, [sessionId]: code.slice(0, MAX_PYTHON_CODE_CHARS) },
        sidePanelOpen: true,
        sidePanelTab: 'python',
      })),
      setComposerDraft: (sessionId, content) => set({ composerDraft: { sessionId, content } }),
      clearComposerDraft: () => set({ composerDraft: null }),
      createSession: () => {
        const session = createSession();
        set((state) => ({
          sessions: state.sessions,
          draftSession: session,
          activeSessionId: session.id,
          view: 'session',
          errors: { ...state.errors, [session.id]: undefined },
        }));
      },
      deleteSession: async (sessionId) => {
        const { backendUrl } = get();
        stopTurnPoll(sessionId);
        backendConnections.remove(sessionId);
        await deleteChat(backendUrl, sessionId);
        set((state) => {
          const sessions = state.sessions.filter((session) => session.id !== sessionId);
          if (sessions.length === state.sessions.length) return state;

          const activeSessionId = sessions.some((session) => session.id === state.activeSessionId)
            ? state.activeSessionId
            : (sessions[0]?.id ?? '');
          const isDiscardingDraft = activeSessionId === '' && state.draftSession?.id === sessionId;
          const connectionStates = { ...state.connectionStates };
          const errors = { ...state.errors };
          const graphActivities = { ...state.graphActivities };
          const boards = { ...state.boards };
          const usage = { ...state.usage };
          const pythonCode = { ...state.pythonCode };
          delete connectionStates[sessionId];
          delete errors[sessionId];
          delete graphActivities[sessionId];
          delete boards[sessionId];
          delete usage[sessionId];
          delete pythonCode[sessionId];

          return {
            sessions,
            activeSessionId,
            draftSession: isDiscardingDraft ? null : state.draftSession,
            view: activeSessionId === '' ? 'home' : state.view,
            connectionStates,
            errors,
            graphActivities,
            boards,
            usage,
            pythonCode,
          };
        });
      },
      selectSession: (sessionId) => {
        set({ activeSessionId: sessionId, view: 'session' });
        // La pizarra no se guarda en el navegador: se pide al abrir la sesión.
        void get().resyncSession(sessionId);
      },
      goHome: () => set((state) => ({
        view: 'home',
        activeSessionId: state.draftSession ? '' : state.activeSessionId,
        draftSession: state.draftSession ? null : state.draftSession,
      })),
      setTheme: (theme) => set({ theme }),
      setBackendUrl: (backendUrl) => set({ backendUrl }),
      setAuthToken: (authToken) => {
        setBackendAuthToken(authToken);
        set({ authToken });
      },
      addDocument: (sessionId, document) => set((state) => ({
        sessions: state.sessions.map((session) => session.id === sessionId
          ? {
              ...session,
              documents: session.documents.some((item) => item.id === document.id)
                ? session.documents
                : [...session.documents, document],
              updatedAt: Date.now(),
            }
          : session),
      })),
      sendMessage: (rawContent) => {
        const content = rawContent.trim();
        const state = get();
        const existing = state.sessions.find((candidate) => candidate.id === state.activeSessionId);
        const session = existing ?? (state.draftSession?.id === state.activeSessionId ? state.draftSession : undefined);
        if (!session || !content || state.connectionStates[session.id] === 'connecting') return;

        const firstUserMessage = !session.messages.some((message) => message.role === 'user');
        const userMessage = createMessage('user', content);
        const updatedSession: StudySession = {
          ...session,
          title: firstUserMessage ? content.slice(0, 42) : session.title,
          messages: [...session.messages, userMessage],
          updatedAt: Date.now(),
        };
        const graphActivities = { ...state.graphActivities };
        delete graphActivities[session.id];
        set((current) => ({
          sessions: existing
            ? current.sessions.map((candidate) => candidate.id === session.id ? updatedSession : candidate)
            : [updatedSession, ...current.sessions],
          draftSession: existing ? current.draftSession : null,
          connectionStates: { ...current.connectionStates, [session.id]: 'connecting' },
          errors: { ...current.errors, [session.id]: undefined },
          graphActivities,
        }));

        backendConnections.send(session.id, state.backendUrl, content, {
          onOpen: () => get().setConnectionStatus(session.id, 'connecting'),
          onResync: () => void get().resyncSession(session.id),
          onEvent: (event) => get().receiveEvent(session.id, event),
          onError: (message) => get().setError(session.id, message),
        });
      },
      receiveEvent: (sessionId, event) => {
        if (event.type === 'status') return;
        if (event.type === 'thinking') {
          if (!event.content) return;
          set((state) => {
            const session = state.sessions.find((item) => item.id === sessionId);
            if (!session) return state;
            const activities = [...(state.graphActivities[sessionId] ?? [])];
            const lastActivity = activities[activities.length - 1];
            const graph = session.phase === 'study' ? 'session' : 'context';
            const node = event.metadata?.node ?? 'Grafo';
            const step = event.metadata?.step;
            if (lastActivity && lastActivity.graph === graph && lastActivity.node === node && lastActivity.step === step) {
              activities[activities.length - 1] = {
                ...lastActivity,
                content: `${lastActivity.content}${event.content}`,
                status: 'streaming',
              };
            } else {
              activities.push({
                id: createId(),
                graph,
                node,
                step,
                content: event.content,
                status: 'streaming',
              });
            }
            return {
              graphActivities: { ...state.graphActivities, [sessionId]: activities },
            };
          });
          return;
        }

        if (event.type === 'error') {
          get().setError(sessionId, event.content);
          return;
        }

        if (event.type === 'board_patch') {
          const board = get().boards[sessionId] ?? EMPTY_BOARD;
          if (event.revision !== board.revision + 1) {
            void get().resyncSession(sessionId);
            return;
          }
          set((state) => ({
            boards: { ...state.boards, [sessionId]: applyBoardPatch(board, event) },
          }));
          return;
        }

        if (event.type === 'usage') {
          const { type: _type, content: _content, ...totals } = event;
          set((state) => ({ usage: { ...state.usage, [sessionId]: totals } }));
          return;
        }

        set((state) => {
          const session = state.sessions.find((candidate) => candidate.id === sessionId);
          if (!session) return state;

          let nextSession = session;
          let status: ConnectionStatus = 'connected';
          let error: string | undefined;
          const graphActivities = state.graphActivities[sessionId]
            ? {
                ...state.graphActivities,
                [sessionId]: setActivityStatus(state.graphActivities[sessionId] ?? [], 'complete'),
              }
            : state.graphActivities;

          if (event.type === 'human_input_request') {
            nextSession = {
              ...session,
              phase: 'clarification',
              messages: [...session.messages, createMessage('assistant', event.content, 'Preparación')],
              updatedAt: Date.now(),
            };
          } else if (event.type === 'context_processed') {
            if (event.content.toLowerCase().includes('fallo')) {
              status = 'error';
              error = 'No se pudo preparar el contexto de estudio.';
            } else {
              const backendTitle = event.title_session?.trim();
              nextSession = {
                ...session,
                title: backendTitle ? backendTitle : session.title,
                phase: 'study',
                messages: [...session.messages, createMessage('system', 'Contexto aprobado. El equipo de tutores está listo.')],
                updatedAt: Date.now(),
              };
            }
          } else {
            nextSession = {
              ...session,
              phase: 'study',
              messages: [...session.messages, createMessage('assistant', event.content, 'Supervisor')],
              updatedAt: Date.now(),
            };
          }

          return {
            sessions: state.sessions.map((candidate) => candidate.id === sessionId ? nextSession : candidate),
            connectionStates: { ...state.connectionStates, [sessionId]: status },
            errors: { ...state.errors, [sessionId]: error },
            graphActivities,
          };
        });

        // Recupera los parches que se perdieron durante el turno.
        if (event.type === 'agent_response') void get().resyncSession(sessionId);
      },
      resyncSession: async (sessionId) => {
        let snapshot: ChatSnapshot;
        try {
          snapshot = await fetchChatSnapshot(get().backendUrl, sessionId);
        } catch {
          // Si falla, el siguiente turno vuelve a intentarlo.
          stopTurnPoll(sessionId);
          return;
        }
        set((state) => {
          const local = state.boards[sessionId];
          // Durante el turno, el checkpoint va por detrás de los parches ya aplicados.
          const isBehind = state.connectionStates[sessionId] === 'connecting'
            && local !== undefined
            && snapshot.board !== undefined
            && snapshot.board.revision < local.revision;
          // Los tokens solo crecen: un total menor es una lectura anterior al último evento.
          const usageIsBehind = (snapshot.usage?.total_tokens ?? 0) < (state.usage[sessionId]?.total_tokens ?? 0);
          return {
            sessions: state.sessions.map((session) => session.id === sessionId
              ? mergeSnapshot(session, snapshot)
              : session),
            boards: snapshot.board && !isBehind
              ? { ...state.boards, [sessionId]: snapshot.board }
              : state.boards,
            usage: snapshot.usage && !usageIsBehind
              ? { ...state.usage, [sessionId]: snapshot.usage }
              : state.usage,
          };
        });

        // Con socket conectado, el resultado llega por sus eventos.
        const attempts = (turnPolls.get(sessionId)?.attempts ?? 0) + 1;
        stopTurnPoll(sessionId);
        const state = get();
        if (
          snapshot.turn_active
          && state.connectionStates[sessionId] !== 'connecting'
          && state.sessions.some((session) => session.id === sessionId)
          && attempts <= TURN_POLL_MAX_ATTEMPTS
        ) {
          const timer = setTimeout(() => void get().resyncSession(sessionId), TURN_POLL_INTERVAL_MS);
          turnPolls.set(sessionId, { timer, attempts });
        }
      },
      setConnectionStatus: (sessionId, status) => set((state) => ({
        connectionStates: { ...state.connectionStates, [sessionId]: status },
      })),
      setError: (sessionId, message) => {
        set((state) => {
          const graphActivities = state.graphActivities[sessionId]
            ? {
                ...state.graphActivities,
                [sessionId]: setActivityStatus(state.graphActivities[sessionId] ?? [], 'error'),
              }
            : state.graphActivities;
          return {
            connectionStates: { ...state.connectionStates, [sessionId]: 'error' },
            errors: { ...state.errors, [sessionId]: message },
            graphActivities,
          };
        });
        // Revierte los parches de un turno fallido.
        void get().resyncSession(sessionId);
      },
      clearError: (sessionId) => set((state) => ({
        errors: { ...state.errors, [sessionId]: undefined },
      })),
    }),
    {
      name: 'estudio-multiagente',
      storage: createJSONStorage(() => localStorage),
      partialize: (state): PersistedState => ({
        sessions: state.sessions,
        activeSessionId: state.activeSessionId,
        theme: state.theme,
        backendUrl: state.backendUrl,
        authToken: state.authToken,
        sidePanelOpen: state.sidePanelOpen,
        sidePanelTab: state.sidePanelTab,
        pythonCode: state.pythonCode,
      }),
      merge: (persisted, current) => {
        const saved = persisted as Partial<PersistedState>;
        const sessions = normalizeSessions(saved.sessions ?? []);
        const activeSessionId = sessions.some((session) => session.id === saved.activeSessionId)
          ? (saved.activeSessionId ?? '')
          : (sessions[0]?.id ?? '');
        setBackendAuthToken(saved.authToken ?? '');
        return {
          ...current,
          ...saved,
          sessions,
          activeSessionId,
          draftSession: null,
          view: 'home',
          connectionStates: {},
          errors: {},
          graphActivities: {},
          boards: {},
          usage: {},
        };
      },
    },
  ),
);
