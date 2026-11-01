import { memo, useEffect, useId, useMemo, useRef, useState } from 'react';
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

const MIN_WIDTH = 260;
const MIN_HEIGHT = 200;
const MAX_HEIGHT = 380;
const HEIGHT_RATIO = 0.625;
const MARGIN = { top: 8, right: 10, bottom: 20, left: 36 };
const MIN_TICKS = 5;
// Espacio aproximado que necesita cada marca del eje.
const PIXELS_PER_TICK = 64;
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
  const chart = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(MIN_WIDTH);
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);
  const height = clamp(Math.round(width * HEIGHT_RATIO), MIN_HEIGHT, MAX_HEIGHT);
  const plotWidth = width - MARGIN.left - MARGIN.right;
  const plotHeight = height - MARGIN.top - MARGIN.bottom;
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
  const xRange: Range = { min: block.x_min, max: block.x_max };
  const yRange = useMemo<Range>(() => {
    const automatic = autoRange(block.functions);
    return { min: block.y_min ?? automatic.min, max: block.y_max ?? automatic.max };
  }, [block.functions, block.y_min, block.y_max]);

  const scaleX = (x: number): number => MARGIN.left + ((x - xRange.min) / (xRange.max - xRange.min)) * plotWidth;
  const scaleY = (y: number): number => MARGIN.top + ((yRange.max - y) / (yRange.max - yRange.min)) * plotHeight;

  const labels = block.functions.map((item) => item.label ?? item.expression);
  const pointCount = block.functions[0]?.points.length ?? 0;
  const hoverX = hoverIndex === null ? undefined : block.functions[0]?.points[hoverIndex]?.[0];
  const showLegend = block.functions.length > 1 || !title;

  const handlePointerMove = (event: PointerEvent<SVGSVGElement>): void => {
    if (pointCount === 0) return;
    const bounds = event.currentTarget.getBoundingClientRect();
    const position = ((event.clientX - bounds.left) / bounds.width) * width;
    const ratio = clamp((position - MARGIN.left) / plotWidth, 0, 1);
    setHoverIndex(Math.round(ratio * (pointCount - 1)));
  };

  return (
    <figure className={styles.plot}>
      <div className={styles.plotChart} ref={chart}>
        <svg
          aria-label={`Gráfico de ${labels.join(', ')} con x entre ${block.x_min} y ${block.x_max}`}
          onPointerLeave={() => setHoverIndex(null)}
          onPointerMove={handlePointerMove}
          role="img"
          viewBox={`0 0 ${width} ${height}`}
        >
          <defs>
            <clipPath id={clipId}>
              <rect height={plotHeight} width={plotWidth} x={MARGIN.left} y={MARGIN.top} />
            </clipPath>
          </defs>
          {niceTicks(yRange, yTicks).map((tick) => (
            <g key={tick}>
              <line className={tick === 0 ? styles.plotAxis : styles.plotGrid} x1={MARGIN.left} x2={width - MARGIN.right} y1={scaleY(tick)} y2={scaleY(tick)} />
              <text className={styles.plotTick} textAnchor="end" x={MARGIN.left - 5} y={scaleY(tick) + 3}>{formatNumber(tick)}</text>
            </g>
          ))}
          {niceTicks(xRange, xTicks).map((tick) => (
            <g key={tick}>
              {tick === 0 && <line className={styles.plotAxis} x1={scaleX(0)} x2={scaleX(0)} y1={MARGIN.top} y2={height - MARGIN.bottom} />}
              <text className={styles.plotTick} textAnchor="middle" x={scaleX(tick)} y={height - 6}>{formatNumber(tick)}</text>
            </g>
          ))}
          <g clipPath={`url(#${clipId})`}>
            {block.vlines?.map((x) => (
              <line className={styles.plotAsymptote} key={x} x1={scaleX(x)} x2={scaleX(x)} y1={MARGIN.top} y2={height - MARGIN.bottom} />
            ))}
            {block.functions.map((item, index) => (
              <path className={styles.plotLine} d={linePaths(item, yRange, scaleX, scaleY)} key={index} style={{ stroke: `var(--series-${index + 1})` }} />
            ))}
            {block.points?.map((point, index) => (
              <g key={index}>
                <circle className={styles.plotMark} cx={scaleX(point.x)} cy={scaleY(point.y)} r={3.5} />
                {point.label && <text className={styles.plotMarkLabel} x={scaleX(point.x) + 6} y={scaleY(point.y) - 6}>{point.label}</text>}
              </g>
            ))}
          </g>
          {hoverIndex !== null && hoverX !== undefined && (
            <g>
              <line className={styles.plotCrosshair} x1={scaleX(hoverX)} x2={scaleX(hoverX)} y1={MARGIN.top} y2={height - MARGIN.bottom} />
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
            data-side={scaleX(hoverX) > width / 2 ? 'left' : 'right'}
            style={{ left: `${(scaleX(hoverX) / width) * 100}%` }}
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
