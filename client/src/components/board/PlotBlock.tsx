import { memo, useId, useMemo, useState } from 'react';
import type { PointerEvent } from 'react';
import type { BoardBlockContent, PlotFunction } from '../../types';
import styles from '../../App.module.css';

type PlotContent = Extract<BoardBlockContent, { kind: 'plot' }>;

interface PlotBlockProps {
  block: PlotContent;
  title?: string;
}

interface Range {
  min: number;
  max: number;
}

const WIDTH = 320;
const HEIGHT = 200;
const MARGIN = { top: 8, right: 10, bottom: 20, left: 36 };
const PLOT_WIDTH = WIDTH - MARGIN.left - MARGIN.right;
const PLOT_HEIGHT = HEIGHT - MARGIN.top - MARGIN.bottom;
const TICK_COUNT = 5;
const PADDING_RATIO = 0.06;
// Si el rango total supera este múltiplo del central, hay una asíntota aplastando la curva.
const OUTLIER_RATIO = 5;

const clamp = (value: number, min: number, max: number): number => Math.min(Math.max(value, min), max);

const round = (value: number): number => Math.round(value * 100) / 100;

const formatNumber = (value: number): string => {
  const magnitude = Math.abs(value);
  if (magnitude >= 1_000_000) return `${Number((value / 1_000_000).toPrecision(3))}M`;
  if (magnitude >= 1000) return `${Number((value / 1000).toPrecision(3))}K`;
  return String(Number(value.toPrecision(4)));
};

const niceTicks = ({ min, max }: Range): number[] => {
  const rawStep = (max - min) / TICK_COUNT;
  const power = 10 ** Math.floor(Math.log10(rawStep));
  const multiple = [1, 2, 5, 10].find((candidate) => candidate * power >= rawStep) ?? 10;
  const step = multiple * power;
  const ticks: number[] = [];
  for (let index = Math.ceil(min / step); index * step <= max; index += 1) {
    ticks.push(Number((index * step).toPrecision(10)));
  }
  return ticks;
};

const autoRange = (functions: PlotFunction[]): Range => {
  const values = functions
    .flatMap((item) => item.points.map(([, y]) => y))
    .filter((y): y is number => y !== null)
    .sort((a, b) => a - b);
  if (values.length === 0) return { min: -1, max: 1 };

  const low = values[Math.floor((values.length - 1) * 0.02)];
  const high = values[Math.ceil((values.length - 1) * 0.98)];
  const min = values[0];
  const max = values[values.length - 1];
  const range = max - min > (high - low) * OUTLIER_RATIO && high > low ? { min: low, max: high } : { min, max };
  if (range.min === range.max) return { min: range.min - 1, max: range.max + 1 };
  const padding = (range.max - range.min) * PADDING_RATIO;
  return { min: range.min - padding, max: range.max + padding };
};

const linePaths = (
  item: PlotFunction,
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

export const PlotBlock = memo(function PlotBlock({ block, title }: PlotBlockProps): JSX.Element {
  const clipId = `plot-${useId().replace(/:/g, '')}`;
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);
  const xRange: Range = { min: block.x_min, max: block.x_max };
  const yRange = useMemo<Range>(() => {
    const automatic = autoRange(block.functions);
    return { min: block.y_min ?? automatic.min, max: block.y_max ?? automatic.max };
  }, [block.functions, block.y_min, block.y_max]);

  const scaleX = (x: number): number => MARGIN.left + ((x - xRange.min) / (xRange.max - xRange.min)) * PLOT_WIDTH;
  const scaleY = (y: number): number => MARGIN.top + ((yRange.max - y) / (yRange.max - yRange.min)) * PLOT_HEIGHT;

  const labels = block.functions.map((item) => item.label ?? item.expression);
  const pointCount = block.functions[0]?.points.length ?? 0;
  const hoverX = hoverIndex === null ? undefined : block.functions[0]?.points[hoverIndex]?.[0];
  const showLegend = block.functions.length > 1 || !title;

  const handlePointerMove = (event: PointerEvent<SVGSVGElement>): void => {
    if (pointCount === 0) return;
    const bounds = event.currentTarget.getBoundingClientRect();
    const position = ((event.clientX - bounds.left) / bounds.width) * WIDTH;
    const ratio = clamp((position - MARGIN.left) / PLOT_WIDTH, 0, 1);
    setHoverIndex(Math.round(ratio * (pointCount - 1)));
  };

  return (
    <figure className={styles.plot}>
      <div className={styles.plotChart}>
        <svg
          aria-label={`Gráfico de ${labels.join(', ')} con x entre ${block.x_min} y ${block.x_max}`}
          onPointerLeave={() => setHoverIndex(null)}
          onPointerMove={handlePointerMove}
          role="img"
          viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        >
          <defs>
            <clipPath id={clipId}>
              <rect height={PLOT_HEIGHT} width={PLOT_WIDTH} x={MARGIN.left} y={MARGIN.top} />
            </clipPath>
          </defs>
          {niceTicks(yRange).map((tick) => (
            <g key={tick}>
              <line className={tick === 0 ? styles.plotAxis : styles.plotGrid} x1={MARGIN.left} x2={WIDTH - MARGIN.right} y1={scaleY(tick)} y2={scaleY(tick)} />
              <text className={styles.plotTick} textAnchor="end" x={MARGIN.left - 5} y={scaleY(tick) + 3}>{formatNumber(tick)}</text>
            </g>
          ))}
          {niceTicks(xRange).map((tick) => (
            <g key={tick}>
              {tick === 0 && <line className={styles.plotAxis} x1={scaleX(0)} x2={scaleX(0)} y1={MARGIN.top} y2={HEIGHT - MARGIN.bottom} />}
              <text className={styles.plotTick} textAnchor="middle" x={scaleX(tick)} y={HEIGHT - 6}>{formatNumber(tick)}</text>
            </g>
          ))}
          <g clipPath={`url(#${clipId})`}>
            {block.functions.map((item, index) => (
              <path className={styles.plotLine} d={linePaths(item, yRange, scaleX, scaleY)} key={index} style={{ stroke: `var(--series-${index + 1})` }} />
            ))}
          </g>
          {hoverIndex !== null && hoverX !== undefined && (
            <g>
              <line className={styles.plotCrosshair} x1={scaleX(hoverX)} x2={scaleX(hoverX)} y1={MARGIN.top} y2={HEIGHT - MARGIN.bottom} />
              {block.functions.map((item, index) => {
                const y = item.points[hoverIndex]?.[1];
                if (y === null || y === undefined || y < yRange.min || y > yRange.max) return null;
                return <circle className={styles.plotDot} cx={scaleX(hoverX)} cy={scaleY(y)} key={index} r={4} style={{ fill: `var(--series-${index + 1})` }} />;
              })}
            </g>
          )}
        </svg>
        {hoverIndex !== null && hoverX !== undefined && (
          <div
            className={styles.plotTooltip}
            data-side={scaleX(hoverX) > WIDTH / 2 ? 'left' : 'right'}
            style={{ left: `${(scaleX(hoverX) / WIDTH) * 100}%` }}
          >
            <div className={styles.plotTooltipTitle}>x = {formatNumber(hoverX)}</div>
            {block.functions.map((item, index) => {
              const y = item.points[hoverIndex]?.[1];
              return (
                <div className={styles.plotTooltipRow} key={index}>
                  <i style={{ background: `var(--series-${index + 1})` }} />
                  <strong>{y === null || y === undefined ? 'sin valor' : formatNumber(y)}</strong>
                  <span>{labels[index]}</span>
                </div>
              );
            })}
          </div>
        )}
      </div>
      {showLegend && (
        <figcaption className={styles.plotLegend}>
          {block.functions.map((item, index) => (
            <span key={index}>
              <i style={{ background: `var(--series-${index + 1})` }} />
              {labels[index]}
            </span>
          ))}
        </figcaption>
      )}
    </figure>
  );
});
