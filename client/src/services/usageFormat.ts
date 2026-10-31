const decimal = new Intl.NumberFormat('es', { maximumFractionDigits: 1 });
const integer = new Intl.NumberFormat('es');
const percent = new Intl.NumberFormat('es', { style: 'percent', maximumFractionDigits: 0 });
const smallCost = new Intl.NumberFormat('es', { minimumFractionDigits: 4, maximumFractionDigits: 4 });
const largeCost = new Intl.NumberFormat('es', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

// Desde este nivel de uso la barra de contexto cambia de color.
const WARNING_RATIO = 0.7;
const ALERT_RATIO = 0.9;

export type ContextLevel = 'normal' | 'warning' | 'alert';

export const formatTokens = (tokens: number): string => {
  if (tokens >= 1_000_000) return `${decimal.format(tokens / 1_000_000)}M`;
  if (tokens >= 1_000) return `${decimal.format(tokens / 1_000)}k`;
  return integer.format(tokens);
};

export const formatPercent = (ratio: number): string => percent.format(ratio);

const formatAmount = (cost: number): string => `$${(cost < 1 ? smallCost : largeCost).format(cost)}`;

// Un costo parcial conserva lo conocido y avisa que falta una parte.
export const formatCost = (cost: number | null, estimated = false, unknown = false): string => {
  if (cost === null) return '$?';
  return `${estimated ? '≈' : ''}${formatAmount(cost)}${unknown ? ' + $?' : ''}`;
};

export const contextLevel = (ratio: number): ContextLevel => {
  if (ratio >= ALERT_RATIO) return 'alert';
  return ratio >= WARNING_RATIO ? 'warning' : 'normal';
};
