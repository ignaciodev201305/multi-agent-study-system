import { useEffect, useState } from 'react';
import { fetchChatUsage } from '../services/backend';
import { formatCost, formatPercent, formatTokens } from '../services/usageFormat';
import type { SessionUsage, UsageTotals } from '../types';
import styles from '../App.module.css';

interface UsagePanelProps {
  backendUrl: string;
  sessionId: string;
  usage?: UsageTotals;
  onClose: () => void;
}

const roleText: Record<string, string> = {
  supervisor: 'Profesor principal',
  subagent_light: 'Especialistas ligeros',
  subagent_heavy: 'Especialistas exigentes',
  context_classifier: 'Clasificador de contexto',
  factory: 'Planificador',
  summarizer: 'Resúmenes',
};

const contextText = (detail: SessionUsage): string => {
  const limit = detail.context_limit;
  if (!limit) return formatTokens(detail.context_used);
  return `${formatTokens(detail.context_used)} / ${formatTokens(limit)} (${formatPercent(detail.context_used / limit)})`;
};

export function UsagePanel({ backendUrl, sessionId, usage, onClose }: UsagePanelProps): JSX.Element {
  const [detail, setDetail] = useState<SessionUsage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const totalTokens = usage?.total_tokens;

  // Se vuelve a pedir cuando un turno en curso registra otra llamada.
  useEffect(() => {
    let cancelled = false;
    fetchChatUsage(backendUrl, sessionId)
      .then((result) => {
        if (!cancelled) setDetail(result);
      })
      .catch((reason: unknown) => {
        if (!cancelled) setError(reason instanceof Error ? reason.message : 'No se pudo consultar el uso de la sesión.');
      });
    return () => {
      cancelled = true;
    };
  }, [backendUrl, sessionId, totalTokens]);

  useEffect(() => {
    const handleKey = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [onClose]);

  return (
    <div className={styles.modelOverlay} onClick={onClose} role="presentation">
      <section
        aria-label="Uso de la sesión"
        aria-modal="true"
        className={styles.modelDialog}
        onClick={(event) => event.stopPropagation()}
        role="dialog"
      >
        <header className={styles.modelDialogHeader}>
          <div>
            <h2>Uso de la sesión</h2>
            <p>No incluye embeddings ni búsquedas web.</p>
          </div>
          <button aria-label="Cerrar" className={styles.iconButton} onClick={onClose} title="Cerrar" type="button">
            <svg aria-hidden="true" viewBox="0 0 16 16" fill="none">
              <path d="m4.5 4.5 7 7m0-7-7 7" stroke="currentColor" strokeLinecap="round" strokeWidth="1.5" />
            </svg>
          </button>
        </header>
        {error && <p className={styles.panelError} role="alert">{error}</p>}
        <div className={styles.usageBody}>
          {detail && detail.total_tokens === 0 && <p className={styles.usageEmpty}>Sin uso registrado</p>}
          {detail && detail.total_tokens > 0 && (
            <>
              <dl className={styles.usageStats}>
                <div><dt>Contexto</dt><dd>{contextText(detail)}</dd></div>
                <div><dt>Entrada</dt><dd>{formatTokens(detail.input_tokens)}</dd></div>
                <div><dt>Salida</dt><dd>{formatTokens(detail.output_tokens)}</dd></div>
                <div><dt>Caché</dt><dd>{formatTokens(detail.cached_tokens)}</dd></div>
                <div><dt>Razonamiento</dt><dd>{formatTokens(detail.reasoning_tokens)}</dd></div>
                <div><dt>Costo total</dt><dd>{formatCost(detail.cost_usd, detail.cost_estimated, detail.cost_unknown)}</dd></div>
                <div><dt>Último turno</dt><dd>{formatCost(detail.last_turn_cost_usd)}</dd></div>
              </dl>
              {detail.cost_estimated && (
                <p className={styles.modelHint}>≈ Costo estimado con los precios del catálogo; no descuenta la caché y puede quedar alto.</p>
              )}
              <div className={styles.usageTableWrap}>
                <table className={styles.usageTable}>
                  <thead>
                    <tr>
                      <th scope="col">Rol y modelo</th>
                      <th scope="col">Llamadas</th>
                      <th scope="col">Entrada</th>
                      <th scope="col">Salida</th>
                      <th scope="col">Costo</th>
                    </tr>
                  </thead>
                  <tbody>
                    {detail.by_model.map((row) => (
                      <tr key={`${row.role}:${row.model}`}>
                        <td>
                          <span className={styles.modelRoleName}>{roleText[row.role] ?? row.role}</span>
                          <br />
                          <span className={styles.modelRoleId}>{row.model}</span>
                        </td>
                        <td>{row.calls}</td>
                        <td>{formatTokens(row.input_tokens)}</td>
                        <td>{formatTokens(row.output_tokens)}</td>
                        <td>{formatCost(row.cost_usd, row.cost_estimated, row.cost_unknown)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>
      </section>
    </div>
  );
}
