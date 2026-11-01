export type StudyPhase = 'setup' | 'clarification' | 'study';
export type ConnectionStatus = 'idle' | 'connecting' | 'connected' | 'error';
export type Theme = 'dark' | 'light';
export type AppView = 'home' | 'session';
export type DocumentType = 'rag' | 'study_map';
export type DocumentProcessingStatus = 'queued' | 'processing' | 'ready' | 'failed';
export type SidePanelTab = 'board' | 'progress' | 'python';
export type ProgressStatus = 'dominado' | 'en_progreso' | 'con_dificultad';

export interface BackendDocument {
  document_id: string;
  chat_id: string;
  original_filename: string;
  document_type: DocumentType;
  size_bytes: number;
  status: DocumentProcessingStatus;
  chunks: number;
  error: string | null;
  created_at: string;
  updated_at: string;
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  agent?: string;
  createdAt: number;
}

export interface UploadedDocument {
  id: string;
  filename: string;
  documentType: DocumentType;
  uploadedAt: number;
}

export interface StudySession {
  id: string;
  title: string;
  phase: StudyPhase;
  messages: ChatMessage[];
  documents: UploadedDocument[];
  updatedAt: number;
}

export interface GraphActivity {
  id: string;
  graph: 'context' | 'session';
  node: string;
  step?: number;
  content: string;
  status: 'streaming' | 'complete' | 'error';
}

export interface ThinkingMetadata {
  node?: string;
  step?: number;
}

export interface BoardStep {
  text: string;
  latex?: string;
}

export interface PlotFunction {
  expression: string;
  label?: string;
  // Cada punto es [x, y]; un y nulo corta la línea.
  points: [number, number | null][];
}

export interface PlotMark {
  x: number;
  y: number;
  label?: string;
}

export type BoardBlockContent =
  | { kind: 'markdown'; text: string }
  | { kind: 'math'; latex: string }
  | { kind: 'steps'; steps: BoardStep[] }
  | { kind: 'table'; headers: string[]; rows: string[][] }
  | { kind: 'code'; language: 'python' | 'text'; code: string }
  | {
    kind: 'plot';
    functions: PlotFunction[];
    x_min: number;
    x_max: number;
    y_min?: number;
    y_max?: number;
    points?: PlotMark[];
    vlines?: number[];
  }
  | { kind: 'exercise'; statement: string; hints?: string[]; solution: string };

export type BoardBlock = BoardBlockContent & {
  id: string;
  author: string;
  title?: string;
  topic_number?: number;
  topic?: string;
};

// Cada pedido es un objeto nuevo para poder repetir el mismo bloque.
export interface BlockFocus {
  blockId: string;
}

export interface Board {
  blocks: BoardBlock[];
  revision: number;
}

export type BoardPatch =
  | { type: 'board_patch'; content: string; revision: number; op: 'upsert'; block: BoardBlock }
  | { type: 'board_patch'; content: string; revision: number; op: 'remove'; block_id: string }
  | { type: 'board_patch'; content: string; revision: number; op: 'clear' };

export type ModelRole = 'supervisor' | 'subagent_light' | 'subagent_heavy' | 'context_classifier' | 'factory' | 'summarizer';
export type ModelSource = 'session' | 'global' | 'env' | 'default';

export interface ModelInfo {
  id: string;
  name: string;
  context_length: number | null;
  prompt_price: number | null;
  completion_price: number | null;
}

export interface ResolvedModel {
  model: string;
  source: ModelSource;
  unavailable: string[];
}

export type ResolvedModels = Record<ModelRole, ResolvedModel>;

export interface ComposerDraft {
  sessionId: string;
  content: string;
}

export interface PythonWorkerRequest {
  id: number;
  code: string;
}

export type PythonWorkerMessage =
  | { type: 'started'; id: number }
  | { type: 'result'; id: number; stdout: string; stderr: string; error: string | null; figures: string[] };

export interface ProgressReview {
  status: ProgressStatus;
  evidence: string;
  created_at: string;
}

export interface ProgressTopic {
  topic_number: number;
  topic: string;
  status: ProgressStatus;
  box: number;
  due_at: string;
  updated_at: string;
  reviews: ProgressReview[];
}

export interface ChatProgress {
  chat_id: string;
  syllabus: string[];
  topics: ProgressTopic[];
}

export interface DueReview {
  chat_id: string;
  topic_number: number;
  topic: string;
  status: ProgressStatus;
  due_at: string;
}

export interface UsageTotals {
  context_used: number;
  // Nulo si no se conoce la ventana de contexto del modelo.
  context_limit: number | null;
  total_tokens: number;
  // Nulo si ninguna llamada tiene costo conocido.
  cost_usd: number | null;
  cost_estimated: boolean;
  cost_unknown: boolean;
}

export interface ModelUsage {
  role: string;
  model: string;
  calls: number;
  input_tokens: number;
  output_tokens: number;
  cached_tokens: number;
  reasoning_tokens: number;
  cost_usd: number | null;
  cost_estimated: boolean;
  cost_unknown: boolean;
}

export interface SessionUsage extends UsageTotals {
  input_tokens: number;
  output_tokens: number;
  cached_tokens: number;
  reasoning_tokens: number;
  last_turn_cost_usd: number | null;
  by_model: ModelUsage[];
}

export type BackendEvent =
  | BoardPatch
  | ({ type: 'usage'; content: string } & UsageTotals)
  | { type: 'thinking'; content: string; metadata?: ThinkingMetadata }
  | { type: 'human_input_request'; content: string }
  | { type: 'context_processed'; content: string; title_session?: string }
  | { type: 'error'; code: string; content: string; retryable: boolean }
  | { type: 'agent_response'; content: string }
  | { type: 'status'; content: string };

export interface ServerChatMessage {
  role: 'user' | 'assistant';
  content: string;
}

export interface ChatSnapshot {
  chat_id: string;
  phase: StudyPhase;
  pending_question: string | null;
  turn_active?: boolean;
  messages: ServerChatMessage[];
  board?: Board;
  usage?: UsageTotals;
}
