import type {
  BackendDocument,
  BackendEvent,
  Board,
  BoardBlock,
  BoardPatch,
  ChatProgress,
  ChatSnapshot,
  DocumentProcessingStatus,
  DocumentType,
  DueReview,
  ModelInfo,
  ModelRole,
  ResolvedModels,
  ServerChatMessage,
  SessionUsage,
  StudyPhase,
  StudySession,
  ThinkingMetadata,
  UsageTotals,
} from '../types';

export type { DocumentType };

export interface UploadedPdf {
  chat_id: string;
  document_id: string;
  document_type: DocumentType;
  original_filename: string | null;
  size_bytes: number;
  sha256: string;
  status: DocumentProcessingStatus;
  chunks: number;
  error: string | null;
}

interface ChatPdfsResponse {
  chat_id: string;
  documents: BackendDocument[];
}

export type BackendCallbacks = {
  onOpen: () => void;
  // Se llama cuando hay que recuperar respuestas del servidor.
  onResync: () => void;
  onEvent: (event: BackendEvent) => void;
  onError: (message: string) => void;
};

const INACTIVITY_TIMEOUT_MS = 120_000;
// El token viaja como subprotocolo porque el navegador no permite cabeceras en el WebSocket.
const WS_APP_SUBPROTOCOL = 'study.v1';

let authToken = '';

export const setAuthToken = (token: string): void => {
  authToken = token.trim();
};

const authHeaders = (): Record<string, string> =>
  authToken ? { Authorization: `Bearer ${authToken}` } : {};

const websocketProtocols = (): string[] =>
  authToken ? [WS_APP_SUBPROTOCOL, `token.${authToken}`] : [WS_APP_SUBPROTOCOL];
// Código con que el servidor cierra una conexión reemplazada por otra más nueva.
const WS_CLOSE_SUPERSEDED = 4000;
const WS_CLOSE_POLICY_VIOLATION = 1008;
// Errores tras los que la conexión sigue abierta y se puede volver a enviar.
const RECOVERABLE_ERROR_CODES = new Set(['invalid_message', 'turn_in_progress', 'message_too_long', 'rate_limited']);
// Agrupa los fragmentos: renderizar la sesión con cada uno bloquea la interfaz.
const THINKING_FLUSH_MS = 100;

type ThinkingEvent = Extract<BackendEvent, { type: 'thinking' }>;

const websocketUrl = (baseUrl: string, sessionId: string): string => {
  let url: URL;
  try {
    url = new URL(baseUrl.trim());
  } catch {
    throw new Error('La dirección del backend no es válida.');
  }
  if (url.protocol === 'http:') url.protocol = 'ws:';
  if (url.protocol === 'https:') url.protocol = 'wss:';
  if (url.protocol !== 'ws:' && url.protocol !== 'wss:') {
    throw new Error('La dirección debe comenzar con ws://, wss://, http:// o https://.');
  }
  url.pathname = `${url.pathname.replace(/\/+$/, '')}/ws/${encodeURIComponent(sessionId)}`;
  return url.toString();
};

const httpUrl = (baseUrl: string, path: string): string => {
  let url: URL;
  try {
    url = new URL(baseUrl.trim());
  } catch {
    throw new Error('La dirección del backend no es válida.');
  }

  if (url.protocol === 'ws:') url.protocol = 'http:';
  if (url.protocol === 'wss:') url.protocol = 'https:';
  if (url.protocol !== 'http:' && url.protocol !== 'https:') {
    throw new Error('La dirección debe comenzar con http://, https://, ws:// o wss://.');
  }

  url.pathname = `${url.pathname.replace(/\/+$/, '')}${path}`;
  url.search = '';
  return url.toString();
};

const uploadUrl = (baseUrl: string, sessionId: string): string =>
  httpUrl(baseUrl, `/chats/${encodeURIComponent(sessionId)}/pdfs`);

const chatUrl = (baseUrl: string, sessionId: string): string =>
  httpUrl(baseUrl, `/chats/${encodeURIComponent(sessionId)}`);

export const uploadPdf = async (
  baseUrl: string,
  sessionId: string,
  file: File,
  documentType: DocumentType,
): Promise<UploadedPdf> => {
  const formData = new FormData();
  formData.append('file', file);
  formData.append('document_type', documentType);

  let response: Response;
  try {
    response = await fetch(uploadUrl(baseUrl, sessionId), {
      method: 'POST',
      body: formData,
      headers: authHeaders(),
    });
  } catch {
    throw new Error('No se pudo conectar con el backend para subir el PDF.');
  }

  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = typeof payload === 'object' && payload !== null && 'detail' in payload
      ? (payload as { detail?: unknown }).detail
      : undefined;
    throw new Error(typeof detail === 'string' ? detail : 'No se pudo subir el PDF.');
  }

  return payload as UploadedPdf;
};

export const listChatPdfs = async (
  baseUrl: string,
  sessionId: string,
): Promise<BackendDocument[]> => {
  let response: Response;
  try {
    response = await fetch(
      httpUrl(baseUrl, `/chats/${encodeURIComponent(sessionId)}/pdfs`),
      { cache: 'no-store', headers: authHeaders() },
    );
  } catch {
    throw new Error('No se pudo consultar el estado de los documentos.');
  }

  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = typeof payload === 'object' && payload !== null && 'detail' in payload
      ? (payload as { detail?: unknown }).detail
      : undefined;
    throw new Error(typeof detail === 'string' ? detail : 'No se pudo consultar el estado de los documentos.');
  }

  if (typeof payload !== 'object' || payload === null || !('documents' in payload)) {
    throw new Error('El backend devolvió una lista de documentos inválida.');
  }
  const documents = (payload as ChatPdfsResponse).documents;
  if (!Array.isArray(documents)) {
    throw new Error('El backend devolvió una lista de documentos inválida.');
  }
  return documents;
};

const STUDY_PHASES: readonly string[] = ['setup', 'clarification', 'study'];

const parseChat = (value: unknown): StudySession | null => {
  if (!isRecord(value)) return null;
  const { chat_id: id, title, phase, created_at: createdAt, updated_at: updatedAt, document_count: documentCount } = value;
  if (
    typeof id !== 'string' || typeof title !== 'string' || typeof phase !== 'string' || !STUDY_PHASES.includes(phase)
    || typeof createdAt !== 'string' || typeof updatedAt !== 'string' || typeof documentCount !== 'number'
  ) return null;
  return {
    id,
    title,
    phase: phase as StudyPhase,
    createdAt: Date.parse(createdAt),
    updatedAt: Date.parse(updatedAt),
    documentCount,
  };
};

export const fetchChats = async (baseUrl: string): Promise<StudySession[]> => {
  let response: Response;
  try {
    response = await fetch(httpUrl(baseUrl, '/chats'), { cache: 'no-store', headers: authHeaders() });
  } catch {
    throw new Error('No se pudo conectar con el backend. Comprueba que esté iniciado.');
  }
  if (response.status === 401) throw new Error('El backend rechazó el token. Revísalo en «Token del servidor».');
  if (response.status === 429) throw new Error('Demasiados intentos con un token inválido. Espera un minuto y reintenta.');
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok || !isRecord(payload) || !Array.isArray(payload.chats)) {
    throw new Error('No se pudieron consultar las sesiones. Revisa la dirección y el token del backend.');
  }
  return payload.chats.map(parseChat).filter((chat): chat is StudySession => chat !== null);
};

const parseServerMessage = (value: unknown): ServerChatMessage | null => {
  if (!isRecord(value)) return null;
  const { id, role, agent, content, created_at: createdAt } = value;
  if (
    typeof id !== 'string' || typeof content !== 'string' || typeof createdAt !== 'string'
    || (role !== 'user' && role !== 'assistant' && role !== 'system')
    || (agent !== null && typeof agent !== 'string')
  ) return null;
  return { id, role, agent, content, created_at: createdAt };
};

export const fetchChatSnapshot = async (
  baseUrl: string,
  sessionId: string,
): Promise<ChatSnapshot> => {
  let response: Response;
  try {
    response = await fetch(
      httpUrl(baseUrl, `/chats/${encodeURIComponent(sessionId)}/messages`),
      { cache: 'no-store', headers: authHeaders() },
    );
  } catch {
    throw new Error('No se pudo consultar el historial de la sesión.');
  }
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok || !isRecord(payload) || !Array.isArray(payload.messages)) {
    throw new Error('No se pudo consultar el historial de la sesión.');
  }
  const snapshot = payload as unknown as ChatSnapshot;
  return {
    ...snapshot,
    messages: payload.messages.map(parseServerMessage).filter((message): message is ServerChatMessage => message !== null),
    board: parseBoard(snapshot.board),
    usage: parseUsageTotals(snapshot.usage),
  };
};

export const fetchChatUsage = async (baseUrl: string, sessionId: string): Promise<SessionUsage> => {
  let response: Response;
  try {
    response = await fetch(
      httpUrl(baseUrl, `/chats/${encodeURIComponent(sessionId)}/usage`),
      { cache: 'no-store', headers: authHeaders() },
    );
  } catch {
    throw new Error('No se pudo consultar el uso de la sesión.');
  }
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok || !parseUsageTotals(payload) || !Array.isArray((payload as SessionUsage).by_model)) {
    throw new Error('No se pudo consultar el uso de la sesión.');
  }
  return payload as SessionUsage;
};

export const fetchChatProgress = async (baseUrl: string, sessionId: string): Promise<ChatProgress> => {
  let response: Response;
  try {
    response = await fetch(
      httpUrl(baseUrl, `/chats/${encodeURIComponent(sessionId)}/progress`),
      { cache: 'no-store', headers: authHeaders() },
    );
  } catch {
    throw new Error('No se pudo consultar el progreso de la sesión.');
  }
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok || !isRecord(payload) || !Array.isArray(payload.syllabus) || !Array.isArray(payload.topics)) {
    throw new Error('No se pudo consultar el progreso de la sesión.');
  }
  return payload as unknown as ChatProgress;
};

export const fetchDueReviews = async (baseUrl: string): Promise<DueReview[]> => {
  let response: Response;
  try {
    response = await fetch(httpUrl(baseUrl, '/reviews/due'), { cache: 'no-store', headers: authHeaders() });
  } catch {
    throw new Error('No se pudieron consultar los repasos.');
  }
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok || !isRecord(payload) || !Array.isArray(payload.reviews)) {
    throw new Error('No se pudieron consultar los repasos.');
  }
  return payload.reviews.filter(
    (review): review is DueReview => isRecord(review) && typeof review.chat_id === 'string'
      && typeof review.chat_title === 'string' && typeof review.topic === 'string'
      && typeof review.topic_number === 'number',
  );
};

const requestModels = async <T>(url: string, init: RequestInit, key: string, fallback: string): Promise<T> => {
  let response: Response;
  try {
    response = await fetch(url, { cache: 'no-store', ...init, headers: { ...authHeaders(), ...init.headers } });
  } catch {
    throw new Error(fallback);
  }
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok || !isRecord(payload) || !(key in payload)) {
    const detail = isRecord(payload) ? payload.detail : undefined;
    throw new Error(typeof detail === 'string' ? detail : fallback);
  }
  return payload[key] as T;
};

const modelSettingsUrl = (baseUrl: string, sessionId?: string): string =>
  sessionId ? `${chatUrl(baseUrl, sessionId)}/models` : httpUrl(baseUrl, '/models/settings');

export const fetchModels = (baseUrl: string, role: ModelRole, query: string): Promise<ModelInfo[]> => {
  const params = new URLSearchParams({ role, q: query });
  return requestModels(`${httpUrl(baseUrl, '/models')}?${params.toString()}`, {}, 'models', 'No se pudo consultar el catálogo de modelos.');
};

export const fetchModelSettings = (baseUrl: string, sessionId?: string): Promise<ResolvedModels> =>
  requestModels(modelSettingsUrl(baseUrl, sessionId), {}, 'models', 'No se pudieron consultar los modelos.');

export const updateModelSetting = (
  baseUrl: string,
  sessionId: string | undefined,
  role: ModelRole,
  model: string | null,
): Promise<ResolvedModels> => requestModels(
  modelSettingsUrl(baseUrl, sessionId),
  {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ role, model }),
  },
  'models',
  'No se pudo guardar el modelo.',
);

export const updateEffortSetting = (
  baseUrl: string,
  sessionId: string | undefined,
  role: ModelRole,
  effort: string | null,
): Promise<ResolvedModels> => requestModels(
  sessionId ? `${chatUrl(baseUrl, sessionId)}/effort` : httpUrl(baseUrl, '/models/effort'),
  {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ role, effort }),
  },
  'models',
  'No se pudo guardar el nivel de razonamiento.',
);

export const deleteChat = async (baseUrl: string, sessionId: string): Promise<void> => {
  const target = chatUrl(baseUrl, sessionId);

  let response: Response;
  try {
    response = await fetch(target, { method: 'DELETE', headers: authHeaders() });
  } catch {
    throw new Error('No se pudo conectar con el backend para eliminar la sesión.');
  }

  if (!response.ok) {
    const payload: unknown = await response.json().catch(() => null);
    const detail = typeof payload === 'object' && payload !== null && 'detail' in payload
      ? (payload as { detail?: unknown }).detail
      : undefined;
    throw new Error(typeof detail === 'string' ? detail : 'No se pudo eliminar la sesión.');
  }
};

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

const isStringList = (value: unknown): value is string[] =>
  Array.isArray(value) && value.every((item) => typeof item === 'string');

const isPlotMark = (value: unknown): boolean =>
  isRecord(value) && typeof value.x === 'number' && typeof value.y === 'number';

const isBoardBlock = (value: unknown): value is BoardBlock => {
  if (!isRecord(value) || typeof value.id !== 'string' || typeof value.author !== 'string') return false;
  switch (value.kind) {
    case 'markdown':
      return typeof value.text === 'string';
    case 'math':
      return typeof value.latex === 'string';
    case 'steps':
      return Array.isArray(value.steps)
        && value.steps.every((step) => isRecord(step) && typeof step.text === 'string');
    case 'table':
      return isStringList(value.headers) && Array.isArray(value.rows) && value.rows.every(isStringList);
    case 'code':
      return typeof value.code === 'string' && (value.language === 'python' || value.language === 'text');
    case 'plot':
      return typeof value.x_min === 'number' && typeof value.x_max === 'number'
        && (value.points === undefined || (Array.isArray(value.points) && value.points.every(isPlotMark)))
        && (value.vlines === undefined || (Array.isArray(value.vlines) && value.vlines.every((x) => typeof x === 'number')))
        && Array.isArray(value.functions)
        && value.functions.every((item) => isRecord(item)
          && typeof item.expression === 'string' && Array.isArray(item.points));
    case 'diagram':
      return typeof value.mermaid === 'string';
    case 'chart':
      return Array.isArray(value.series) && value.series.length > 0
        && value.series.every((item) => isRecord(item)
          && (item.mark === 'line' || item.mark === 'scatter' || item.mark === 'bar')
          && Array.isArray(item.points) && item.points.length > 0
          && item.points.every((point) => Array.isArray(point) && typeof point[0] === 'number' && typeof point[1] === 'number'));
    case 'exercise':
      return typeof value.statement === 'string' && typeof value.solution === 'string'
        && (value.hints === undefined || isStringList(value.hints));
    default:
      return false;
  }
};

const parseBoard = (value: unknown): Board | undefined => {
  if (!isRecord(value) || !Array.isArray(value.blocks) || typeof value.revision !== 'number') return undefined;
  return { blocks: value.blocks.filter(isBoardBlock), revision: value.revision };
};

const parseBoardPatch = (value: object, content: string): BackendEvent | null => {
  const { revision, op, block, block_id: blockId } = value as Record<string, unknown>;
  if (typeof revision !== 'number') return null;
  if (op === 'upsert' && isBoardBlock(block)) return { type: 'board_patch', content, revision, op, block };
  // Un tipo desconocido no corta la sesión; el salto de revisión pide resincronizar.
  if (op === 'upsert') return { type: 'status', content };
  if (op === 'remove' && typeof blockId === 'string') {
    return { type: 'board_patch', content, revision, op, block_id: blockId };
  }
  if (op === 'clear') return { type: 'board_patch', content, revision, op };
  return null;
};

const isNumberOrNull = (value: unknown): value is number | null => typeof value === 'number' || value === null;

const parseUsageTotals = (value: unknown): UsageTotals | undefined => {
  if (!isRecord(value)) return undefined;
  const {
    context_used: contextUsed,
    context_limit: contextLimit,
    total_tokens: totalTokens,
    cost_usd: costUsd,
    cost_estimated: costEstimated,
    cost_unknown: costUnknown,
  } = value;
  if (
    typeof contextUsed !== 'number' || !isNumberOrNull(contextLimit) || typeof totalTokens !== 'number'
    || !isNumberOrNull(costUsd) || typeof costEstimated !== 'boolean' || typeof costUnknown !== 'boolean'
  ) return undefined;
  return {
    context_used: contextUsed,
    context_limit: contextLimit,
    total_tokens: totalTokens,
    cost_usd: costUsd,
    cost_estimated: costEstimated,
    cost_unknown: costUnknown,
  };
};

// El servidor manda node y step como campos del evento.
const parseThinkingMetadata = (record: Record<string, unknown>): ThinkingMetadata | undefined => {
  const node = typeof record.node === 'string' ? record.node : undefined;
  const step = typeof record.step === 'number' ? record.step : undefined;
  return node || step !== undefined ? { node, step } : undefined;
};

const parseBackendEvent = (value: unknown): BackendEvent | null => {
  if (typeof value !== 'object' || value === null || !('type' in value) || !('content' in value)) {
    return null;
  }

  const eventType = value.type;
  const content = value.content;
  if (typeof content !== 'string') return null;

  if (eventType === 'thinking') {
    return { type: 'thinking', content, metadata: parseThinkingMetadata(value as Record<string, unknown>) };
  }
  if (eventType === 'human_input_request') return { type: 'human_input_request', content };
  if (eventType === 'context_processed') {
    const rawTitle = (value as { title_session?: unknown }).title_session;
    const titleSession = typeof rawTitle === 'string' && rawTitle.trim() ? rawTitle.trim() : undefined;
    return { type: 'context_processed', content, title_session: titleSession };
  }
  if (eventType === 'error') {
    const rawCode = (value as { code?: unknown }).code;
    const retryable = (value as { retryable?: unknown }).retryable;
    if (typeof rawCode !== 'string' || typeof retryable !== 'boolean') return null;
    return { type: 'error', code: rawCode, content, retryable };
  }
  if (eventType === 'agent_response') return { type: 'agent_response', content };
  if (eventType === 'status') return { type: 'status', content };
  if (eventType === 'board_patch') return parseBoardPatch(value, content);
  if (eventType === 'usage') {
    const totals = parseUsageTotals(value);
    return totals ? { type: 'usage', content, ...totals } : null;
  }

  return null;
};

export class BackendConnection {
  private readonly socket: WebSocket;
  private timeout: ReturnType<typeof setTimeout> | undefined;
  private pendingThinking: ThinkingEvent | null = null;
  private thinkingTimer: ReturnType<typeof setTimeout> | undefined;
  private hasFailed = false;

  constructor(url: string, private readonly callbacks: BackendCallbacks) {
    this.socket = new WebSocket(url, websocketProtocols());
    this.socket.addEventListener('open', this.handleOpen);
    this.socket.addEventListener('message', this.handleMessage);
    this.socket.addEventListener('error', this.handleError);
    this.socket.addEventListener('close', this.handleClose);
  }

  get isUsable(): boolean {
    return !this.hasFailed && (this.socket.readyState === WebSocket.CONNECTING || this.socket.readyState === WebSocket.OPEN);
  }

  send(content: string, deep: boolean): void {
    if (this.socket.readyState !== WebSocket.OPEN) {
      this.fail('La conexión con el backend no está disponible.');
      return;
    }
    this.socket.send(JSON.stringify({ content, deep }));
    this.restartTimeout();
  }

  sendWhenReady(content: string, deep: boolean): void {
    if (this.socket.readyState === WebSocket.OPEN) {
      this.send(content, deep);
      return;
    }
    if (this.socket.readyState === WebSocket.CONNECTING) {
      this.socket.addEventListener('open', () => this.send(content, deep), { once: true });
    }
  }

  close(): void {
    this.hasFailed = true;
    this.clearTimeout();
    clearTimeout(this.thinkingTimer);
    this.pendingThinking = null;
    this.socket.close();
  }

  private readonly handleOpen = (): void => {
    if (this.hasFailed) return;
    this.callbacks.onOpen();
    this.callbacks.onResync();
  };

  private readonly handleMessage = (message: MessageEvent<string>): void => {
    let event: BackendEvent | null = null;
    try {
      event = parseBackendEvent(JSON.parse(message.data) as unknown);
    } catch {
      this.fail('El backend devolvió una respuesta que no es JSON válido.');
      return;
    }

    if (!event) {
      this.fail('El backend devolvió un mensaje con formato no reconocido.');
      return;
    }

    if (event.type === 'thinking') {
      this.restartTimeout();
      this.queueThinking(event);
      return;
    }

    // Lo acumulado debe llegar antes que el evento que sigue.
    this.flushThinking();

    if (event.type === 'error') {
      this.callbacks.onEvent(event);
      if (RECOVERABLE_ERROR_CODES.has(event.code)) {
        this.restartTimeout();
      } else {
        this.hasFailed = true;
        this.clearTimeout();
        if (this.socket.readyState === WebSocket.OPEN) this.socket.close();
      }
      return;
    }

    if (event.type === 'status') {
      // El turno sigue vivo aunque no emita tokens.
      this.restartTimeout();
      if (event.content === 'previous_turn_finished') this.callbacks.onResync();
      return;
    }

    // Estos eventos llegan a mitad de turno y no lo terminan.
    if (event.type === 'board_patch' || event.type === 'usage') {
      this.restartTimeout();
    } else {
      this.clearTimeout();
    }

    this.callbacks.onEvent(event);
  };

  private queueThinking(event: ThinkingEvent): void {
    const pending = this.pendingThinking;
    if (pending && pending.metadata?.node === event.metadata?.node && pending.metadata?.step === event.metadata?.step) {
      pending.content += event.content;
      return;
    }
    this.flushThinking();
    this.pendingThinking = { ...event };
    this.thinkingTimer = setTimeout(() => this.flushThinking(), THINKING_FLUSH_MS);
  }

  private flushThinking(): void {
    clearTimeout(this.thinkingTimer);
    const pending = this.pendingThinking;
    this.pendingThinking = null;
    if (pending) this.callbacks.onEvent(pending);
  }

  private readonly handleError = (): void => {
    this.fail('No se pudo conectar con el backend. Comprueba que el servicio esté iniciado.');
  };

  private readonly handleClose = (event: CloseEvent): void => {
    if (event.code === WS_CLOSE_POLICY_VIOLATION && event.reason === 'unauthorized') {
      this.fail('El backend rechazó el token. Revisa el token local en la pantalla de inicio.');
      return;
    }
    if (event.code === WS_CLOSE_SUPERSEDED) {
      // Otra conexión de esta sesión la reemplazó; no es un error.
      this.flushThinking();
      this.hasFailed = true;
      this.clearTimeout();
      return;
    }
    if (!this.hasFailed) this.fail('La conexión con el backend se cerró.');
  };

  private restartTimeout(): void {
    this.clearTimeout();
    this.timeout = setTimeout(() => {
      this.fail('El backend tardó demasiado en responder.');
      this.socket.close();
    }, INACTIVITY_TIMEOUT_MS);
  }

  private clearTimeout(): void {
    if (this.timeout) clearTimeout(this.timeout);
    this.timeout = undefined;
  }

  private fail(message: string): void {
    if (this.hasFailed) return;
    this.flushThinking();
    this.hasFailed = true;
    this.clearTimeout();
    this.callbacks.onError(message);
  }
}

export class BackendConnections {
  private readonly connections = new Map<string, { url: string; connection: BackendConnection }>();

  send(sessionId: string, baseUrl: string, content: string, deep: boolean, callbacks: BackendCallbacks): void {
    let targetUrl: string;
    try {
      targetUrl = websocketUrl(baseUrl, sessionId);
    } catch (error: unknown) {
      callbacks.onError(error instanceof Error ? error.message : 'La dirección del backend no es válida.');
      return;
    }

    const current = this.connections.get(sessionId);
    if (current?.url === targetUrl && current.connection.isUsable) {
      current.connection.sendWhenReady(content, deep);
      return;
    }

    try {
      const connection = new BackendConnection(targetUrl, callbacks);
      this.connections.set(sessionId, { url: targetUrl, connection });
      connection.sendWhenReady(content, deep);
    } catch {
      callbacks.onError('No se pudo abrir la conexión WebSocket con el backend.');
    }
  }

  remove(sessionId: string): void {
    const entry = this.connections.get(sessionId);
    if (!entry) return;
    this.connections.delete(sessionId);
    entry.connection.close();
  }
}

export const backendConnections = new BackendConnections();
