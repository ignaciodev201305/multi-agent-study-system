import { contextLevel, formatCost, formatPercent, formatTokens } from '../services/usageFormat';
import type { UsageTotals } from '../types';
import styles from '../App.module.css';

interface UsageIndicatorProps {
  usage?: UsageTotals;
  onOpen: () => void;
}

export function UsageIndicator({ usage, onOpen }: UsageIndicatorProps): JSX.Element {
  if (!usage || usage.total_tokens === 0) {
    return (
      <button className={styles.usageIndicator} onClick={onOpen} type="button">
        <span className={styles.usageText}>Sin uso registrado</span>
      </button>
    );
  }

  const limit = usage.context_limit;
  const ratio = limit ? usage.context_used / limit : null;
  const cost = formatCost(usage.cost_usd, usage.cost_estimated, usage.cost_unknown);

  return (
    <button aria-label={`Uso de la sesión: ${cost}`} className={styles.usageIndicator} onClick={onOpen} type="button">
      <span className={styles.usageText}>
        <span className={styles.usageContext}>
          {formatTokens(usage.context_used)}
          {limit ? ` / ${formatTokens(limit)}` : ''}
          {ratio !== null ? ` · ${formatPercent(ratio)}` : ''}
          {' · '}
        </span>
        <span>{cost}</span>
      </span>
      {ratio !== null && (
        <span className={styles.usageBar}>
          <span data-level={contextLevel(ratio)} style={{ width: `${Math.min(ratio, 1) * 100}%` }} />
        </span>
      )}
    </button>
  );
}
