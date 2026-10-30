import { create } from 'zustand';
import { createJSONStorage, persist } from 'zustand/middleware';
import { backendConnections, deleteChat } from '../services/backend';
import type {
  AppView,
  BackendEvent,
  ChatMessage,
  ConnectionStatus,
  GraphActivity,
  StudySession,
  Theme,
  UploadedDocument,
} from '../types';

const DEFAULT_BACKEND_URL = 'ws://127.0.0.1:8000';

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

interface PersistedState {
  sessions: StudySession[];
  activeSessionId: string;
  theme: Theme;
  backendUrl: string;
}

interface StudyState extends PersistedState {
  view: AppView;
  draftSession: StudySession | null;
  connectionStates: Record<string, ConnectionStatus>;
  errors: Record<string, string | undefined>;
  graphActivities: Record<string, GraphActivity[] | undefined>;
  createSession: () => void;
  deleteSession: (sessionId: string) => Promise<void>;
  selectSession: (sessionId: string) => void;
  goHome: () => void;
  setTheme: (theme: Theme) => void;
  setBackendUrl: (backendUrl: string) => void;
  addDocument: (sessionId: string, document: UploadedDocument) => void;
  sendMessage: (content: string) => void;
  receiveEvent: (sessionId: string, event: BackendEvent) => void;
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
      connectionStates: {},
      errors: {},
      graphActivities: {},
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
          delete connectionStates[sessionId];
          delete errors[sessionId];
          delete graphActivities[sessionId];

          return {
            sessions,
            activeSessionId,
            draftSession: isDiscardingDraft ? null : state.draftSession,
            view: activeSessionId === '' ? 'home' : state.view,
            connectionStates,
            errors,
            graphActivities,
          };
        });
      },
      selectSession: (sessionId) => set({ activeSessionId: sessionId, view: 'session' }),
      goHome: () => set((state) => ({
        view: 'home',
        activeSessionId: state.draftSession ? '' : state.activeSessionId,
        draftSession: state.draftSession ? null : state.draftSession,
      })),
      setTheme: (theme) => set({ theme }),
      setBackendUrl: (backendUrl) => set({ backendUrl }),
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
          onEvent: (event) => get().receiveEvent(session.id, event),
          onError: (message) => get().setError(session.id, message),
        });
      },
      receiveEvent: (sessionId, event) => {
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
      },
      setConnectionStatus: (sessionId, status) => set((state) => ({
        connectionStates: { ...state.connectionStates, [sessionId]: status },
      })),
      setError: (sessionId, message) => set((state) => {
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
      }),
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
      }),
      merge: (persisted, current) => {
        const saved = persisted as Partial<PersistedState>;
        const sessions = normalizeSessions(saved.sessions ?? []);
        const activeSessionId = sessions.some((session) => session.id === saved.activeSessionId)
          ? (saved.activeSessionId ?? '')
          : (sessions[0]?.id ?? '');
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
        };
      },
    },
  ),
);
