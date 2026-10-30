export type StudyPhase = 'setup' | 'clarification' | 'study';
export type ConnectionStatus = 'idle' | 'connecting' | 'connected' | 'error';
export type Theme = 'dark' | 'light';
export type AppView = 'home' | 'session';
export type DocumentType = 'rag' | 'study_map';
export type DocumentProcessingStatus = 'queued' | 'processing' | 'ready' | 'failed';

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

export type BackendEvent =
  | { type: 'thinking'; content: string; metadata?: ThinkingMetadata }
  | { type: 'human_input_request'; content: string }
  | { type: 'context_processed'; content: string; title_session?: string }
  | { type: 'error'; code: string; content: string; retryable: boolean }
  | { type: 'agent_response'; content: string };
