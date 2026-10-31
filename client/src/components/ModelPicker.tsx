import { useEffect, useState } from 'react';
import { fetchModels } from '../services/backend';
import type { ModelInfo, ModelRole } from '../types';
import styles from '../App.module.css';

interface ModelPickerProps {
  backendUrl: string;
  role: ModelRole;
  current: string;
  onSelect: (modelId: string) => void;
  onCancel: () => void;
}

const SEARCH_DELAY_MS = 250;
const MAX_VISIBLE_MODELS = 60;

const formatPrice = (price: number | null): string => (price === null ? '?' : `$${Number(price.toFixed(2))}`);

const formatContext = (tokens: number | null): string => {
  if (!tokens) return 'contexto desconocido';
  return tokens >= 1_000_000 ? `${Number((tokens / 1_000_000).toFixed(1))} M` : `${Math.round(tokens / 1000)} K`;
};

export function ModelPicker({ backendUrl, role, current, onSelect, onCancel }: ModelPickerProps): JSX.Element {
  const [query, setQuery] = useState('');
  const [models, setModels] = useState<ModelInfo[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const timer = window.setTimeout(() => {
      fetchModels(backendUrl, role, query)
        .then((result) => {
          if (cancelled) return;
          setModels(result);
          setError(null);
        })
        .catch((reason: unknown) => {
          if (!cancelled) setError(reason instanceof Error ? reason.message : 'No se pudo consultar el catálogo.');
        });
    }, SEARCH_DELAY_MS);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [backendUrl, query, role]);

  return (
    <div className={styles.modelPicker}>
      <div className={styles.modelPickerSearch}>
        <input
          aria-label="Buscar modelo"
          autoFocus
          maxLength={100}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Buscar por nombre o id…"
          spellCheck={false}
          value={query}
        />
        <button className={styles.themeButton} onClick={onCancel} type="button">Cancelar</button>
      </div>
      {error && <p className={styles.panelError} role="alert">{error}</p>}
      {models && models.length === 0 && <p className={styles.modelHint}>Ningún modelo coincide con la búsqueda.</p>}
      {models && models.length > 0 && (
        <ul className={styles.modelList}>
          {models.slice(0, MAX_VISIBLE_MODELS).map((model) => (
            <li key={model.id}>
              <button
                className={styles.modelOption}
                data-current={model.id === current ? true : undefined}
                onClick={() => onSelect(model.id)}
                type="button"
              >
                <span className={styles.modelOptionName}>{model.name}</span>
                <span className={styles.modelOptionId}>{model.id}</span>
                <span className={styles.modelOptionMeta}>
                  {formatPrice(model.prompt_price)} / {formatPrice(model.completion_price)} por millón · {formatContext(model.context_length)}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {models && models.length > MAX_VISIBLE_MODELS && (
        <p className={styles.modelHint}>Se muestran {MAX_VISIBLE_MODELS} de {models.length}. Busca para acotar.</p>
      )}
    </div>
  );
}
