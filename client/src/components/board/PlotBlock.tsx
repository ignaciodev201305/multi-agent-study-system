import { memo, useEffect, useId, useMemo, useRef, useState } from 'react';
import type { PointerEvent } from 'react';
import type { BoardBlockContent, ChartMark, PlotMark } from '../../types';
import styles from '../../App.module.css';

type PlotContent = Extract<BoardBlockContent, { kind: 'plot' | 'chart' }>;

interface PlotBlockProps {
  block: PlotContent;
  title?: string;
}

interface Range {
  min: number;
  max: number;
}

interface Series {
  label: string;
  mark: ChartMark;
  // Cada punto es [x, y]; un y nulo corta la línea.
  points: [number, number | null][];
}

// Forma común de un gráfico de funciones (plot) y de uno de datos (chart).
interface PlotView {
  series: Series[];
  xRange: Range;
  yMin?: number;
  yMax?: number;
  marks?: PlotMark[];
  vlines?: number[];
  xLabel?: string;
  yLabel?: string;
}

const MIN_WIDTH = 260;
const MIN_HEIGHT = 200;
const MAX_HEIGHT = 380;
const HEIGHT_RATIO = 0.625;
const MARGIN = { top: 8, right: 10, bottom: 20, left: 36 };
// Espacio extra que ocupa el nombre de un eje.
const AXIS_LABEL_SPACE = 14;
const MIN_TICKS = 5;
// Espacio aproximado que necesita cada marca del eje.
const PIXELS_PER_TICK = 64;
const PADDING_RATIO = 0.06;
// Si el rango total supera este múltiplo del central, hay una asíntota aplastando la curva.
const OUTLIER_RATIO = 5;
// Parte del intervalo que ocupa cada barra.
const BAR_FILL = 0.9;

const clamp = (value: number, min: number, max: number): number => Math.min(Math.max(value, min), max);

const round = (value: number): number => Math.round(value * 100) / 100;

const formatNumber = (value: number): string => {
  const magnitude = Math.abs(value);
  if (magnitude >= 1_000_000) return `${Number((value / 1_000_000).toPrecision(3))}M`;
  if (magnitude >= 1000) return `${Number((value / 1000).toPrecision(3))}K`;
  return String(Number(value.toPrecision(4)));
};

const niceTicks = ({ min, max }: Range, count: number): number[] => {
  const rawStep = (max - min) / count;
  const power = 10 ** Math.floor(Math.log10(rawStep));
  const multiple = [1, 2, 5, 10].find((candidate) => candidate * power >= rawStep) ?? 10;
  const step = multiple * power;
  const ticks: number[] = [];
  for (let index = Math.ceil(min / step); index * step <= max; index += 1) {
    ticks.push(Number((index * step).toPrecision(10)));
  }
  return ticks;
};

// Separación mínima entre los x de una serie: el ancho de sus barras.
const barStep = (points: Series['points']): number => {
  const xs = points.map(([x]) => x).sort((a, b) => a - b);
  let step = Infinity;
  for (let index = 1; index < xs.length; index += 1) {
    if (xs[index] > xs[index - 1]) step = Math.min(step, xs[index] - xs[index - 1]);
  }
  return Number.isFinite(step) ? step : 1;
};

const toView = (block: PlotContent): PlotView => {
  if (block.kind === 'plot') {
    return {
      series: block.functions.map((item) => ({ label: item.label ?? item.expression, mark: 'line', points: item.points })),
      xRange: { min: block.x_min, max: block.x_max },
      yMin: block.y_min,
      yMax: block.y_max,
      marks: block.points,
      vlines: block.vlines,
    };
  }
  const series = block.series.map((item, index): Series => ({
    label: item.label ?? `Serie ${index + 1}`,
    mark: item.mark,
    points: item.points,
  }));
  const xs = series.flatMap((item) => item.points.map(([x]) => x));
  const min = Math.min(...xs);
  const max = Math.max(...xs);
  const halfBar = Math.max(0, ...series.filter((item) => item.mark === 'bar').map((item) => barStep(item.points) / 2));
  const padding = halfBar || (max - min) * PADDING_RATIO || 1;
  return { series, xRange: { min: min - padding, max: max + padding }, xLabel: block.x_label, yLabel: block.y_label };
};

const autoRange = (series: Series[]): Range => {
  const values = series
    .flatMap((item) => item.points.map(([, y]) => y))
    .filter((y): y is number => y !== null)
    .sort((a, b) => a - b);
  if (values.length === 0) return { min: -1, max: 1 };

  const low = values[Math.floor((values.length - 1) * 0.02)];
  const high = values[Math.ceil((values.length - 1) * 0.98)];
  const min = values[0];
  const max = values[values.length - 1];
  const range = max - min > (high - low) * OUTLIER_RATIO && high > low ? { min: low, max: high } : { min, max };
  // Las barras nacen en cero, así que el eje debe incluirlo.
  if (series.some((item) => item.mark === 'bar')) {
    range.min = Math.min(range.min, 0);
    range.max = Math.max(range.max, 0);
  }
  if (range.min === range.max) return { min: range.min - 1, max: range.max + 1 };
  const padding = (range.max - range.min) * PADDING_RATIO;
  return { min: range.min - padding, max: range.max + padding };
};

const linePaths = (
  item: Series,
  yRange: Range,
  scaleX: (x: number) => number,
  scaleY: (y: number) => number,
): string => {
  const commands: string[] = [];
  let previous: number | null = null;
  for (const [x, y] of item.points) {
    // Un salto de un extremo al otro del rango es una asíntota, no parte de la curva.
    const jumps = previous !== null && y !== null
      && ((previous > yRange.max && y < yRange.min) || (previous < yRange.min && y > yRange.max));
    if (y !== null) commands.push(`${previous === null || jumps ? 'M' : 'L'}${round(scaleX(x))} ${round(scaleY(y))}`);
    previous = y;
  }
  return commands.join(' ');
};

const nearestIndex = (points: Series['points'], x: number): number => {
  let best = 0;
  for (let index = 1; index < points.length; index += 1) {
    if (Math.abs(points[index][0] - x) < Math.abs(points[best][0] - x)) best = index;
  }
  return best;
};

export const PlotBlock = memo(function PlotBlock({ block, title }: PlotBlockProps): JSX.Element {
  const clipId = `plot-${useId().replace(/:/g, '')}`;
  const chart = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(MIN_WIDTH);
  const [hoverX, setHoverX] = useState<number | null>(null);
  const view = useMemo(() => toView(block), [block]);
  const { series, xRange } = view;
  const margin = {
    ...MARGIN,
    top: MARGIN.top + (view.yLabel ? AXIS_LABEL_SPACE : 0),
    bottom: MARGIN.bottom + (view.xLabel ? AXIS_LABEL_SPACE : 0),
  };
  const height = clamp(Math.round(width * HEIGHT_RATIO), MIN_HEIGHT, MAX_HEIGHT);
  const plotWidth = width - margin.left - margin.right;
  const plotHeight = height - margin.top - margin.bottom;
  const xTicks = Math.max(MIN_TICKS, Math.floor(plotWidth / PIXELS_PER_TICK));
  const yTicks = Math.max(MIN_TICKS, Math.floor(plotHeight / PIXELS_PER_TICK));

  // Se dibuja al ancho real para que el texto y las líneas no crezcan con el panel.
  useEffect(() => {
    const element = chart.current;
    if (!element) return undefined;
    const observer = new ResizeObserver(([entry]) => {
      setWidth(Math.max(MIN_WIDTH, Math.round(entry.contentRect.width)));
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  const yRange = useMemo<Range>(() => {
    const automatic = autoRange(series);
    return { min: view.yMin ?? automatic.min, max: view.yMax ?? automatic.max };
  }, [series, view.yMin, view.yMax]);

  const scaleX = (x: number): number => margin.left + ((x - xRange.min) / (xRange.max - xRange.min)) * plotWidth;
  const scaleY = (y: number): number => margin.top + ((yRange.max - y) / (yRange.max - yRange.min)) * plotHeight;
  const baseline = scaleY(clamp(0, yRange.min, yRange.max));

  const labels = series.map((item) => item.label);
  const showLegend = series.length > 1 || !title;
  // Valor de cada serie en el punto más cercano al cursor.
  const hoverValues = hoverX === null ? [] : series.map((item) => item.points[nearestIndex(item.points, hoverX)]?.[1] ?? null);

  const handlePointerMove = (event: PointerEvent<SVGSVGElement>): void => {
    const reference = series[0]?.points ?? [];
    if (reference.length === 0) return;
    const bounds = event.currentTarget.getBoundingClientRect();
    const position = ((event.clientX - bounds.left) / bounds.width) * width;
    const ratio = clamp((position - margin.left) / plotWidth, 0, 1);
    const x = xRange.min + ratio * (xRange.max - xRange.min);
    setHoverX(reference[nearestIndex(reference, x)][0]);
  };

  return (
    <figure className={styles.plot}>
      <div className={styles.plotChart} ref={chart}>
        <svg
          aria-label={`Gráfico de ${labels.join(', ')} con x entre ${formatNumber(xRange.min)} y ${formatNumber(xRange.max)}`}
          onPointerCancel={() => setHoverX(null)}
          onPointerDown={handlePointerMove}
          onPointerLeave={() => setHoverX(null)}
          onPointerMove={handlePointerMove}
          role="img"
          viewBox={`0 0 ${width} ${height}`}
        >
          <defs>
            <clipPath id={clipId}>
              <rect height={plotHeight} width={plotWidth} x={margin.left} y={margin.top} />
            </clipPath>
          </defs>
          {niceTicks(yRange, yTicks).map((tick) => (
            <g key={tick}>
              <line className={tick === 0 ? styles.plotAxis : styles.plotGrid} x1={margin.left} x2={width - margin.right} y1={scaleY(tick)} y2={scaleY(tick)} />
              <text className={styles.plotTick} textAnchor="end" x={margin.left - 5} y={scaleY(tick) + 3}>{formatNumber(tick)}</text>
            </g>
          ))}
          {niceTicks(xRange, xTicks).map((tick) => (
            <g key={tick}>
              {tick === 0 && <line className={styles.plotAxis} x1={scaleX(0)} x2={scaleX(0)} y1={margin.top} y2={margin.top + plotHeight} />}
              <text className={styles.plotTick} textAnchor="middle" x={scaleX(tick)} y={margin.top + plotHeight + 14}>{formatNumber(tick)}</text>
            </g>
          ))}
          {view.yLabel && <text className={styles.plotAxisLabel} x={4} y={11}>{view.yLabel}</text>}
          {view.xLabel && (
            <text className={styles.plotAxisLabel} textAnchor="middle" x={margin.left + plotWidth / 2} y={height - 4}>{view.xLabel}</text>
          )}
          <g clipPath={`url(#${clipId})`}>
            {view.vlines?.map((x) => (
              <line className={styles.plotAsymptote} key={x} x1={scaleX(x)} x2={scaleX(x)} y1={margin.top} y2={margin.top + plotHeight} />
            ))}
            {series.map((item, index) => {
              const color = `var(--series-${index + 1})`;
              if (item.mark === 'line') {
                return <path className={styles.plotLine} d={linePaths(item, yRange, scaleX, scaleY)} key={index} style={{ stroke: color }} />;
              }
              if (item.mark === 'scatter') {
                return (
                  <g key={index} style={{ fill: color }}>
                    {item.points.map(([x, y], pointIndex) => (
                      y === null ? null : <circle className={styles.plotScatter} cx={round(scaleX(x))} cy={round(scaleY(y))} key={pointIndex} r={3} />
                    ))}
                  </g>
                );
              }
              const barWidth = Math.max(1, (barStep(item.points) / (xRange.max - xRange.min)) * plotWidth * BAR_FILL);
              return (
                <g key={index} style={{ fill: color }}>
                  {item.points.map(([x, y], pointIndex) => (
                    y === null ? null : (
                      <rect
                        className={styles.plotBar}
                        height={round(Math.abs(scaleY(y) - baseline))}
                        key={pointIndex}
                        width={round(barWidth)}
                        x={round(scaleX(x) - barWidth / 2)}
                        y={round(Math.min(scaleY(y), baseline))}
                      />
                    )
                  ))}
                </g>
              );
            })}
            {view.marks?.map((point, index) => (
              <g key={index}>
                <circle className={styles.plotMark} cx={scaleX(point.x)} cy={scaleY(point.y)} r={3.5} />
                {point.label && <text className={styles.plotMarkLabel} x={scaleX(point.x) + 6} y={scaleY(point.y) - 6}>{point.label}</text>}
              </g>
            ))}
          </g>
          {hoverX !== null && (
            <g>
              <line className={styles.plotCrosshair} x1={scaleX(hoverX)} x2={scaleX(hoverX)} y1={margin.top} y2={margin.top + plotHeight} />
              {hoverValues.map((y, index) => {
                if (y === null || y < yRange.min || y > yRange.max) return null;
                return <circle className={styles.plotDot} cx={scaleX(hoverX)} cy={scaleY(y)} key={index} r={4} style={{ fill: `var(--series-${index + 1})` }} />;
              })}
            </g>
          )}
        </svg>
        {hoverX !== null && (
          <div
            className={styles.plotTooltip}
            data-side={scaleX(hoverX) > width / 2 ? 'left' : 'right'}
            style={{ left: `${(scaleX(hoverX) / width) * 100}%` }}
          >
            <div className={styles.plotTooltipTitle}>{view.xLabel ?? 'x'} = {formatNumber(hoverX)}</div>
            {hoverValues.map((y, index) => (
              <div className={styles.plotTooltipRow} key={index}>
                <i style={{ background: `var(--series-${index + 1})` }} />
                <strong>{y === null ? 'sin valor' : formatNumber(y)}</strong>
                <span>{labels[index]}</span>
              </div>
            ))}
          </div>
        )}
      </div>
      {showLegend && (
        <figcaption className={styles.plotLegend}>
          {labels.map((label, index) => (
            <span key={index}>
              <i style={{ background: `var(--series-${index + 1})` }} />
              {label}
            </span>
          ))}
        </figcaption>
      )}
    </figure>
  );
});
