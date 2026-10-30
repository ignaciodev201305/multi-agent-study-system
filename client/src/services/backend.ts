import type {
  BackendDocument,
  BackendEvent,
  ChatSnapshot,
  DocumentProcessingStatus,
  DocumentType,
  ThinkingMetadata,
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
  // El servidor puede haber terminado un turno sin poder enviarlo: hay que resincronizar.
  onResync: () => void;
  onEvent: (event: BackendEvent) => void;
  onError: (message: string) => void;
};

const INACTIVITY_TIMEOUT_MS = 120_000;
// Subprotocolo de la app; el token viaja como un segundo subprotocolo "token.<APP_TOKEN>"
// porque el navegador no permite cabeceras en el WebSocket.
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
  if (!response.ok || typeof payload !== 'object' || payload === null || !('messages' in payload)) {
    throw new Error('No se pudo consultar el historial de la sesión.');
  }
  return payload as ChatSnapshot;
};

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

const parseThinkingMetadata = (value: unknown): ThinkingMetadata | undefined => {
  if (typeof value === 'string') {
    const nodeMatch = value.match(/(?:langgraph_node|node)['"]?\s*:\s*['"]([^'"]+)['"]/);
    const stepMatch = value.match(/(?:langgraph_step|step)['"]?\s*:\s*(\d+)/);
    const metadata: ThinkingMetadata = {};
    if (nodeMatch) metadata.node = nodeMatch[1];
    if (stepMatch) metadata.step = Number(stepMatch[1]);
    return metadata.node || metadata.step !== undefined ? metadata : undefined;
  }

  if (typeof value !== 'object' || value === null || Array.isArray(value)) return undefined;
  const record = value as Record<string, unknown>;
  const node = typeof record.langgraph_node === 'string'
    ? record.langgraph_node
    : typeof record.node === 'string' ? record.node : undefined;
  const rawStep = record.langgraph_step ?? record.step;
  const step = typeof rawStep === 'number'
    ? rawStep
    : typeof rawStep === 'string' && /^\d+$/.test(rawStep) ? Number(rawStep) : undefined;

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
    let thinkingContent = content;
    let metadata = parseThinkingMetadata(value);
    if (!metadata && 'metadata' in value) metadata = parseThinkingMetadata(value.metadata);

    if (!metadata) {
      const separator = thinkingContent.indexOf('\n');
      if (separator !== -1) {
        metadata = parseThinkingMetadata(thinkingContent.slice(0, separator));
        if (metadata) thinkingContent = thinkingContent.slice(separator + 1);
      }
    }

    return { type: 'thinking', content: thinkingContent, metadata };
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
  // Compatibilidad temporal con backends que todavía envían el evento anterior.
  if (eventType === 'context_failed') {
    return { type: 'error', code: 'context_failed', content, retryable: true };
  }
  if (eventType === 'agent_response') return { type: 'agent_response', content };
  if (eventType === 'status') return { type: 'status', content };

  return null;
};

export class BackendConnection {
  private readonly socket: WebSocket;
  private timeout: ReturnType<typeof setTimeout> | undefined;
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

  send(content: string): void {
    if (this.socket.readyState !== WebSocket.OPEN) {
      this.fail('La conexión con el backend no está disponible.');
      return;
    }
    this.socket.send(JSON.stringify({ content }));
    this.restartTimeout();
  }

  sendWhenReady(content: string): void {
    if (this.socket.readyState === WebSocket.OPEN) {
      this.send(content);
      return;
    }
    if (this.socket.readyState === WebSocket.CONNECTING) {
      this.socket.addEventListener('open', () => this.send(content), { once: true });
    }
  }

  close(): void {
    this.hasFailed = true;
    this.clearTimeout();
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
      // Heartbeat o espera del turno anterior: el turno sigue vivo.
      this.restartTimeout();
      if (event.content === 'previous_turn_finished') this.callbacks.onResync();
      return;
    }

    if (event.type === 'thinking') {
      this.restartTimeout();
    } else {
      this.clearTimeout();
    }

    this.callbacks.onEvent(event);
  };

  private readonly handleError = (): void => {
    this.fail('No se pudo conectar con el backend. Comprueba que el servicio esté iniciado.');
  };

  private readonly handleClose = (event: CloseEvent): void => {
    if (event.code === WS_CLOSE_POLICY_VIOLATION && event.reason === 'unauthorized') {
      this.fail('El backend rechazó el token. Revisa el token local en la pantalla de inicio.');
      return;
    }
    if (event.code === WS_CLOSE_SUPERSEDED) {
      // Otra conexión de esta sesión tomó el relevo; no es un error.
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
    this.hasFailed = true;
    this.clearTimeout();
    this.callbacks.onError(message);
  }
}

export class BackendConnections {
  private readonly connections = new Map<string, { url: string; connection: BackendConnection }>();

  send(sessionId: string, baseUrl: string, content: string, callbacks: BackendCallbacks): void {
    let targetUrl: string;
    try {
      targetUrl = websocketUrl(baseUrl, sessionId);
    } catch (error: unknown) {
      callbacks.onError(error instanceof Error ? error.message : 'La dirección del backend no es válida.');
      return;
    }

    const current = this.connections.get(sessionId);
    if (current?.url === targetUrl && current.connection.isUsable) {
      current.connection.sendWhenReady(content);
      return;
    }

    try {
      const connection = new BackendConnection(targetUrl, callbacks);
      this.connections.set(sessionId, { url: targetUrl, connection });
      connection.sendWhenReady(content);
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
