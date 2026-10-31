import { useEffect, useState } from 'react';
import { fetchModelSettings, updateModelSetting } from '../services/backend';
import type { ModelRole, ModelSource, ResolvedModels } from '../types';
import { ModelPicker } from './ModelPicker';
import styles from '../App.module.css';

interface ModelSettingsPanelProps {
  backendUrl: string;
  // Sin sesión se edita la configuración global.
  sessionId?: string;
  onClose: () => void;
}

const roles: { id: ModelRole; label: string; hint: string }[] = [
  { id: 'supervisor', label: 'Profesor principal', hint: 'Conduce la sesión' },
  { id: 'subagent_light', label: 'Especialistas ligeros', hint: 'Explicador, evaluador y documentos' },
  { id: 'subagent_heavy', label: 'Especialistas exigentes', hint: 'Ejercicios, investigación y código' },
  { id: 'context_classifier', label: 'Clasificador de contexto', hint: 'Prepara la sesión' },
  { id: 'factory', label: 'Planificador', hint: 'Escribe el temario' },
  { id: 'summarizer', label: 'Resúmenes', hint: 'Historial y PDFs' },
];

const sourceText: Record<ModelSource, string> = {
  session: 'Sesión',
  global: 'Global',
  env: 'Por defecto',
  default: 'Por defecto',
};

const errorText = (reason: unknown, fallback: string): string => (reason instanceof Error ? reason.message : fallback);

export function ModelSettingsPanel({ backendUrl, sessionId, onClose }: ModelSettingsPanelProps): JSX.Element {
  const [models, setModels] = useState<ResolvedModels | null>(null);
  const [picking, setPicking] = useState<ModelRole | null>(null);
  const [error, setError] = useState<string | null>(null);
  const ownSource: ModelSource = sessionId ? 'session' : 'global';

  useEffect(() => {
    let cancelled = false;
    fetchModelSettings(backendUrl, sessionId)
      .then((result) => {
        if (!cancelled) setModels(result);
      })
      .catch((reason: unknown) => {
        if (!cancelled) setError(errorText(reason, 'No se pudieron consultar los modelos.'));
      });
    return () => {
      cancelled = true;
    };
  }, [backendUrl, sessionId]);

  useEffect(() => {
    const handleKey = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [onClose]);

  const save = async (role: ModelRole, model: string | null): Promise<void> => {
    setError(null);
    try {
      setModels(await updateModelSetting(backendUrl, sessionId, role, model));
      setPicking(null);
    } catch (reason: unknown) {
      setError(errorText(reason, 'No se pudo guardar el modelo.'));
    }
  };

  return (
    <div className={styles.modelOverlay} onClick={onClose} role="presentation">
      <section
        aria-label="Modelos"
        aria-modal="true"
        className={styles.modelDialog}
        onClick={(event) => event.stopPropagation()}
        role="dialog"
      >
        <header className={styles.modelDialogHeader}>
          <div>
            <h2>{sessionId ? 'Modelos de esta sesión' : 'Modelos globales'}</h2>
            <p>{sessionId ? 'Aplican desde el próximo mensaje.' : 'Se usan en las sesiones sin ajuste propio.'}</p>
          </div>
          <button aria-label="Cerrar" className={styles.iconButton} onClick={onClose} title="Cerrar" type="button">
            <svg aria-hidden="true" viewBox="0 0 16 16" fill="none">
              <path d="m4.5 4.5 7 7m0-7-7 7" stroke="currentColor" strokeLinecap="round" strokeWidth="1.5" />
            </svg>
          </button>
        </header>
        {error && <p className={styles.panelError} role="alert">{error}</p>}
        <ul className={styles.modelRoles}>
          {roles.map((role) => {
            const entry = models?.[role.id];
            return (
              <li className={styles.modelRole} key={role.id}>
                <div className={styles.modelRoleRow}>
                  <div className={styles.modelRoleInfo}>
                    <span className={styles.modelRoleName}>{role.label}</span>
                    <span className={styles.modelHint}>{role.hint}</span>
                    <span className={styles.modelRoleId}>{entry?.model ?? '…'}</span>
                  </div>
                  <div className={styles.modelRoleActions}>
                    {entry && <span className={styles.progressBadge} data-status={entry.source === 'session' ? 'dominado' : 'pending'}>{sourceText[entry.source]}</span>}
                    <button className={styles.themeButton} disabled={!entry} onClick={() => setPicking(picking === role.id ? null : role.id)} type="button">
                      Cambiar
                    </button>
                    {entry && (entry.source === ownSource || entry.unavailable.length > 0) && (
                      <button className={styles.themeButton} onClick={() => void save(role.id, null)} type="button">
                        Restablecer
                      </button>
                    )}
                  </div>
                </div>
                {entry && entry.unavailable.length > 0 && (
                  <p className={styles.modelUnavailable}>No disponible: {entry.unavailable.join(', ')}. Se usa el modelo indicado.</p>
                )}
                {picking === role.id && entry && (
                  <ModelPicker
                    backendUrl={backendUrl}
                    current={entry.model}
                    onCancel={() => setPicking(null)}
                    onSelect={(model) => void save(role.id, model)}
                    role={role.id}
                  />
                )}
              </li>
            );
          })}
        </ul>
      </section>
    </div>
  );
}
