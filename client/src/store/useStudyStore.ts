import { create } from 'zustand';
import { createJSONStorage, persist } from 'zustand/middleware';
import {
  backendConnections,
  deleteChat,
  fetchChats,
  fetchChatSnapshot,
  setAuthToken as setBackendAuthToken,
} from '../services/backend';
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
  ServerChatMessage,
  StudySession,
  Theme,
  UsageTotals,
} from '../types';

const DEFAULT_BACKEND_URL = 'ws://127.0.0.1:8000';
export const DEFAULT_SIDE_PANEL_WIDTH = 348;
const EMPTY_BOARD: Board = { blocks: [], revision: 0 };
const MAX_PYTHON_CODE_CHARS = 20_000;
const TURN_POLL_INTERVAL_MS = 3_000;
const TURN_POLL_MAX_ATTEMPTS = 60;
const CONTEXT_APPROVED_TEXT = 'Contexto aprobado. El equipo de tutores está listo.';
const TITLE_MAX_CHARS = 42;
const PROVISIONAL_PREFIX = 'local-';

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
  createdAt: Date.now(),
  updatedAt: Date.now(),
  documentCount: 0,
});

// Mensaje que se muestra al instante; el id local lo distingue de los que guardó el servidor.
const createMessage = (role: ChatMessage['role'], content: string, agent?: string): ChatMessage => ({
  id: `${PROVISIONAL_PREFIX}${createId()}`,
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

const toChatMessage = (message: ServerChatMessage): ChatMessage => ({
  id: message.id,
  role: message.role,
  content: message.content,
  agent: message.agent ?? undefined,
  createdAt: Date.parse(message.created_at),
});

// La lista del servidor manda; solo se conserva el mensaje del usuario que el servidor aún no guardó.
const messagesFromServer = (
  server: ServerChatMessage[],
  local: ChatMessage[],
  keepPending: boolean,
): ChatMessage[] => {
  const messages = server.map(toChatMessage);
  if (!keepPending) return messages;
  const countUsers = (list: ChatMessage[]): number => list.filter((message) => message.role === 'user').length;
  const pending = local.filter((message) => message.role === 'user' && message.id.startsWith(PROVISIONAL_PREFIX));
  const missing = countUsers(local) - countUsers(messages);
  return missing > 0 ? [...messages, ...pending.slice(-missing)] : messages;
};

interface PersistedState {
  activeSessionId: string;
  theme: Theme;
  backendUrl: string;
  authToken: string;
  docsPanelOpen: boolean;
  sidePanelOpen: boolean;
  sidePanelWidth: number;
  sidePanelTab: SidePanelTab;
  pythonCode: Record<string, string | undefined>;
}

type SessionsStatus = 'loading' | 'ready' | 'error';

interface StudyState extends PersistedState {
  // La fuente de verdad es el servidor: ni la lista ni los mensajes se guardan en el navegador.
  sessions: StudySession[];
  sessionsStatus: SessionsStatus;
  sessionsError: string | null;
  messages: Record<string, ChatMessage[] | undefined>;
  view: AppView;
  draftSession: StudySession | null;
  connectionStates: Record<string, ConnectionStatus>;
  errors: Record<string, string | undefined>;
  graphActivities: Record<string, GraphActivity[] | undefined>;
  // La fuente de verdad es el servidor: no se guarda en el navegador.
  boards: Record<string, Board | undefined>;
  usage: Record<string, UsageTotals | undefined>;
  composerDraft: ComposerDraft | null;
  // Modo profundo por sesión; se apaga al recargar para no dejar turnos caros activados.
  deepModes: Record<string, boolean | undefined>;
  toggleDeepMode: (sessionId: string) => void;
  setDocsPanelOpen: (open: boolean) => void;
  setSidePanelOpen: (open: boolean) => void;
  setSidePanelWidth: (width: number) => void;
  setSidePanelTab: (tab: SidePanelTab) => void;
  setPythonCode: (sessionId: string, code: string) => void;
  openInPython: (sessionId: string, code: string) => void;
  setComposerDraft: (sessionId: string, content: string) => void;
  clearComposerDraft: () => void;
  loadSessions: () => Promise<void>;
  createSession: () => void;
  deleteSession: (sessionId: string) => Promise<void>;
  selectSession: (sessionId: string) => void;
  goHome: () => void;
  setTheme: (theme: Theme) => void;
  setBackendUrl: (backendUrl: string) => void;
  setAuthToken: (authToken: string) => void;
  sendMessage: (content: string) => void;
  receiveEvent: (sessionId: string, event: BackendEvent) => void;
  resyncSession: (sessionId: string) => Promise<void>;
  setConnectionStatus: (sessionId: string, status: ConnectionStatus) => void;
  setError: (sessionId: string, message: string) => void;
  clearError: (sessionId: string) => void;
}

// Cada carga reemplaza a la anterior: una respuesta vieja no pisa a una más nueva.
let sessionsRequest = 0;

export const useStudyStore = create<StudyState>()(
  persist(
    (set, get) => ({
      sessions: [],
      sessionsStatus: 'loading',
      sessionsError: null,
      messages: {},
      activeSessionId: '',
      view: 'home',
      draftSession: null,
      theme: 'dark',
      backendUrl: DEFAULT_BACKEND_URL,
      authToken: '',
      docsPanelOpen: true,
      sidePanelOpen: true,
      sidePanelWidth: DEFAULT_SIDE_PANEL_WIDTH,
      sidePanelTab: 'board',
      pythonCode: {},
      connectionStates: {},
      errors: {},
      graphActivities: {},
      boards: {},
      usage: {},
      composerDraft: null,
      deepModes: {},
      toggleDeepMode: (sessionId) => set((state) => ({
        deepModes: { ...state.deepModes, [sessionId]: !state.deepModes[sessionId] },
      })),
      setDocsPanelOpen: (docsPanelOpen) => set({ docsPanelOpen }),
      setSidePanelOpen: (sidePanelOpen) => set({ sidePanelOpen }),
      setSidePanelWidth: (sidePanelWidth) => set({ sidePanelWidth }),
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
      loadSessions: async () => {
        sessionsRequest += 1;
        const request = sessionsRequest;
        set({ sessionsStatus: 'loading' });
        try {
          const fetched = await fetchChats(get().backendUrl);
          if (request !== sessionsRequest) return;
          set((state) => {
            // Una sesión con el primer turno en vuelo puede no estar todavía en el servidor.
            const inFlight = state.sessions.filter((session) =>
              state.connectionStates[session.id] === 'connecting' && !fetched.some((item) => item.id === session.id));
            return {
              sessions: [...inFlight, ...fetched],
              sessionsStatus: 'ready',
              sessionsError: null,
            };
          });
        } catch (error: unknown) {
          if (request !== sessionsRequest) return;
          set({
            sessionsStatus: 'error',
            sessionsError: error instanceof Error ? error.message : 'No se pudo conectar con el backend.',
          });
        }
      },
      createSession: () => {
        const session = createSession();
        set((state) => ({
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
          const messages = { ...state.messages };
          const pythonCode = { ...state.pythonCode };
          delete connectionStates[sessionId];
          delete errors[sessionId];
          delete graphActivities[sessionId];
          delete boards[sessionId];
          delete usage[sessionId];
          delete messages[sessionId];
          delete pythonCode[sessionId];

          return {
            sessions,
            messages,
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
        // Los mensajes y la pizarra viven en el servidor: se piden al abrir la sesión.
        void get().resyncSession(sessionId);
      },
      goHome: () => {
        set((state) => ({
          view: 'home',
          activeSessionId: state.draftSession ? '' : state.activeSessionId,
          draftSession: state.draftSession ? null : state.draftSession,
        }));
        void get().loadSessions();
      },
      setTheme: (theme) => set({ theme }),
      setBackendUrl: (backendUrl) => set({ backendUrl }),
      setAuthToken: (authToken) => {
        setBackendAuthToken(authToken);
        set({ authToken });
      },
      sendMessage: (rawContent) => {
        const content = rawContent.trim();
        const state = get();
        const existing = state.sessions.find((candidate) => candidate.id === state.activeSessionId);
        const session = existing ?? (state.draftSession?.id === state.activeSessionId ? state.draftSession : undefined);
        if (!session || !content || state.connectionStates[session.id] === 'connecting') return;

        const history = state.messages[session.id] ?? [];
        const firstUserMessage = !history.some((message) => message.role === 'user');
        const updatedSession: StudySession = {
          ...session,
          title: firstUserMessage ? content.slice(0, TITLE_MAX_CHARS) : session.title,
          updatedAt: Date.now(),
        };
        const graphActivities = { ...state.graphActivities };
        delete graphActivities[session.id];
        set((current) => ({
          sessions: existing
            ? current.sessions.map((candidate) => candidate.id === session.id ? updatedSession : candidate)
            : [updatedSession, ...current.sessions],
          messages: { ...current.messages, [session.id]: [...history, createMessage('user', content)] },
          draftSession: existing ? current.draftSession : null,
          connectionStates: { ...current.connectionStates, [session.id]: 'connecting' },
          errors: { ...current.errors, [session.id]: undefined },
          graphActivities,
        }));

        const deep = session.phase === 'study' && Boolean(state.deepModes[session.id]);
        backendConnections.send(session.id, state.backendUrl, content, deep, {
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

          let nextSession: StudySession;
          let message: ChatMessage;
          if (event.type === 'human_input_request') {
            nextSession = { ...session, phase: 'clarification', updatedAt: Date.now() };
            message = createMessage('assistant', event.content, 'Preparación');
          } else if (event.type === 'context_processed') {
            const backendTitle = event.title_session?.trim();
            nextSession = {
              ...session,
              title: backendTitle ? backendTitle : session.title,
              phase: 'study',
              updatedAt: Date.now(),
            };
            message = createMessage('system', CONTEXT_APPROVED_TEXT);
          } else {
            nextSession = { ...session, phase: 'study', updatedAt: Date.now() };
            message = createMessage('assistant', event.content, 'Supervisor');
          }

          return {
            sessions: state.sessions.map((candidate) => candidate.id === sessionId ? nextSession : candidate),
            messages: { ...state.messages, [sessionId]: [...(state.messages[sessionId] ?? []), message] },
            connectionStates: { ...state.connectionStates, [sessionId]: 'connected' },
            errors: { ...state.errors, [sessionId]: undefined },
            graphActivities: state.graphActivities[sessionId]
              ? {
                  ...state.graphActivities,
                  [sessionId]: setActivityStatus(state.graphActivities[sessionId] ?? [], 'complete'),
                }
              : state.graphActivities,
          };
        });

        // La versión del servidor reemplaza lo mostrado al instante y trae los parches que se perdieron.
        void get().resyncSession(sessionId);
        void get().loadSessions();
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
          const status = state.connectionStates[sessionId];
          const keepPending = status === 'connecting' || status === 'error';
          return {
            sessions: state.sessions.map((session) => session.id === sessionId && session.phase !== snapshot.phase
              ? { ...session, phase: snapshot.phase }
              : session),
            messages: {
              ...state.messages,
              [sessionId]: messagesFromServer(snapshot.messages, state.messages[sessionId] ?? [], keepPending),
            },
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
        activeSessionId: state.activeSessionId,
        theme: state.theme,
        backendUrl: state.backendUrl,
        authToken: state.authToken,
        docsPanelOpen: state.docsPanelOpen,
        sidePanelOpen: state.sidePanelOpen,
        sidePanelWidth: state.sidePanelWidth,
        sidePanelTab: state.sidePanelTab,
        pythonCode: state.pythonCode,
      }),
      // Las sesiones ahora viven en el servidor: lo que se guardó antes en el navegador se descarta.
      version: 1,
      migrate: (persisted) => {
        const { sessions: _sessions, ...preferences } = persisted as Partial<PersistedState> & { sessions?: unknown };
        return preferences;
      },
      merge: (persisted, current) => {
        const saved = persisted as Partial<PersistedState>;
        setBackendAuthToken(saved.authToken ?? '');
        return {
          ...current,
          ...saved,
          draftSession: null,
          view: 'home',
          connectionStates: {},
          errors: {},
          graphActivities: {},
          boards: {},
          usage: {},
          messages: {},
          deepModes: {},
        };
      },
    },
  ),
);
