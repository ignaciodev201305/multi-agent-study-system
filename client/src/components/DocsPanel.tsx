import { useRef, useState } from 'react';
import { listChatPdfs, uploadPdf } from '../services/backend';
import type { DocumentProcessingStatus, DocumentType, UploadedDocument } from '../types';
import styles from '../App.module.css';

interface DocsPanelProps {
  sessionId: string;
  backendUrl: string;
  documents: UploadedDocument[];
  locked?: boolean;
  onUploaded: (document: UploadedDocument) => void;
  // Procesar un documento gasta aunque falle.
  onProcessed: () => void;
}

const typeLabel: Record<DocumentType, string> = {
  rag: 'Preguntas (RAG)',
  study_map: 'Mapa de estudio',
};

const ACCEPTED = 'application/pdf,.pdf';
const STATUS_POLL_INTERVAL_MS = 1500;
const processingStatusText: Record<DocumentProcessingStatus, string> = {
  queued: 'En cola',
  processing: 'Procesando…',
  ready: 'Listo',
  failed: 'Falló el procesamiento',
};

export function DocsPanel({ sessionId, backendUrl, documents, locked = false, onUploaded, onProcessed }: DocsPanelProps): JSX.Element {
  const fileInput = useRef<HTMLInputElement>(null);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [documentType, setDocumentType] = useState<DocumentType>('rag');
  const [uploading, setUploading] = useState(false);
  const [uploadStatus, setUploadStatus] = useState<DocumentProcessingStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isDragging, setDragging] = useState(false);

  const pickFile = (file: File | null): void => {
    setError(null);
    setUploadStatus(null);
    if (file && !file.name.toLowerCase().endsWith('.pdf')) {
      setSelectedFile(null);
      setError('Solo se aceptan archivos PDF.');
      return;
    }
    setSelectedFile(file);
  };

  const submit = async (): Promise<void> => {
    if (!selectedFile || uploading) return;
    setUploading(true);
    setError(null);
    try {
      const uploaded = await uploadPdf(backendUrl, sessionId, selectedFile, documentType);
      let status = uploaded.status;
      let processingError = uploaded.error;
      setUploadStatus(status);

      while (status === 'queued' || status === 'processing') {
        await new Promise<void>((resolve) => window.setTimeout(resolve, STATUS_POLL_INTERVAL_MS));
        const documents = await listChatPdfs(backendUrl, sessionId);
        const current = documents.find((document) => document.document_id === uploaded.document_id);
        if (!current) throw new Error('No se encontró el documento en el catálogo del backend.');
        status = current.status;
        processingError = current.error;
        setUploadStatus(status);
      }

      if (status === 'failed') {
        throw new Error(processingError || 'No se pudo procesar el PDF.');
      }
      if (status !== 'ready') {
        throw new Error('El backend devolvió un estado de procesamiento no válido.');
      }

      onUploaded({
        id: uploaded.document_id,
        filename: uploaded.original_filename ?? selectedFile.name,
        documentType: uploaded.document_type,
        uploadedAt: Date.now(),
      });
      setSelectedFile(null);
      if (fileInput.current) fileInput.current.value = '';
    } catch (uploadError: unknown) {
      setError(uploadError instanceof Error ? uploadError.message : 'No se pudo subir el PDF.');
    } finally {
      setUploading(false);
      setUploadStatus(null);
      onProcessed();
    }
  };

  return (
    <aside className={styles.docsPanel}>
      <div className={styles.docsHeader}>
        <span className={styles.docsTitle}>Material</span>
        <span className={styles.docsCount}>{documents.length}</span>
      </div>

      <div className={styles.uploadCard}>
        {locked ? (
          <p className={styles.uploadLocked}>Envía tu primer mensaje para habilitar la subida de material.</p>
        ) : (
          <>
            <label
              className={styles.dropzone}
              data-dragging={isDragging || undefined}
              onDragLeave={() => setDragging(false)}
              onDragOver={(event) => {
                event.preventDefault();
                setDragging(true);
              }}
              onDrop={(event) => {
                event.preventDefault();
                setDragging(false);
                pickFile(event.dataTransfer.files?.[0] ?? null);
              }}
            >
              <input
                accept={ACCEPTED}
                disabled={uploading}
                ref={fileInput}
                type="file"
                onChange={(event) => pickFile(event.currentTarget.files?.[0] ?? null)}
              />
              <span className={styles.dropzoneBadge} aria-hidden="true">PDF</span>
              <span className={styles.dropzoneText}>
                {selectedFile ? selectedFile.name : 'Elige o arrastra un PDF'}
              </span>
            </label>

            <div className={styles.typeToggle} role="group" aria-label="Uso del documento">
              <button
                aria-pressed={documentType === 'rag'}
                className={styles.typeOption}
                data-active={documentType === 'rag' || undefined}
                disabled={uploading}
                onClick={() => setDocumentType('rag')}
                type="button"
              >
                Preguntas
              </button>
              <button
                aria-pressed={documentType === 'study_map'}
                className={styles.typeOption}
                data-active={documentType === 'study_map' || undefined}
                disabled={uploading}
                onClick={() => setDocumentType('study_map')}
                type="button"
              >
                Mapa
              </button>
            </div>

            <button
              className={styles.uploadCta}
              disabled={!selectedFile || uploading}
              onClick={() => void submit()}
              type="button"
            >
              {uploading ? (uploadStatus ? processingStatusText[uploadStatus] : 'Subiendo…') : 'Procesar'}
            </button>

            {uploading && uploadStatus && (
              <p aria-live="polite" role="status">Estado del documento: {processingStatusText[uploadStatus]}</p>
            )}
            {error && <p className={styles.uploadError} role="alert">{error}</p>}
          </>
        )}
      </div>

      <div className={styles.docsListHeader}>Procesados</div>
      {documents.length === 0 ? (
        <p className={styles.docsEmpty}>Aún no has subido material a esta sesión.</p>
      ) : (
        <ul className={styles.docsList}>
          {documents.map((document) => (
            <li className={styles.docItem} key={document.id}>
              <span className={styles.docIcon} aria-hidden="true">
                <svg viewBox="0 0 16 16" fill="none">
                  <path d="M4 1.8h5l3 3v9.4H4z" stroke="currentColor" strokeLinejoin="round" strokeWidth="1.1" />
                  <path d="M9 1.8v3h3" stroke="currentColor" strokeLinejoin="round" strokeWidth="1.1" />
                </svg>
              </span>
              <span className={styles.docInfo}>
                <span className={styles.docName} title={document.filename}>{document.filename}</span>
                <span className={styles.docMeta}>{typeLabel[document.documentType]}</span>
              </span>
            </li>
          ))}
        </ul>
      )}
    </aside>
  );
}
