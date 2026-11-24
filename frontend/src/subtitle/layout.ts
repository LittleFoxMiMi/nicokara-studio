import type { LyricLine, LyricUnit } from "../editor-types";
import { rubyGroupEnd, timedBounds } from "../ruby-timing";
import { measureUnit } from "./measure";
import type { SubtitleStyle } from "./style-schema";

export type LayoutUnit = LyricUnit & { width: number; progress: number; groupUnits?: LyricUnit[] };
export type LayoutRow = { units: LayoutUnit[]; width: number };
export type SubtitleLayout = { rows: LayoutRow[]; fontSize: number; width: number; height: number; x: number; y: number; opacity: number };

type StaticLayout = { rows: LayoutRow[]; fontSize: number; width: number; height: number; x: number; y: number };
const layoutCache = new Map<string, StaticLayout>();

function groupUnits(units: LyricUnit[]): LyricUnit[] {
  const groups: LyricUnit[] = [];
  for (let index = 0; index < units.length;) {
    const first = units[index];
    const members = units.slice(index, rubyGroupEnd(units, index));
    groups.push({ ...first, ...timedBounds(members), surface: members.map((unit) => unit.surface).join(""), __groupUnits: members } as LyricUnit & { __groupUnits: LyricUnit[] });
    index += members.length;
  }
  return groups;
}

function rowsFor(units: LyricUnit[], fontSize: number, style: SubtitleStyle, maxWidth: number): LayoutRow[] {
  const groups = groupUnits(units);
  if (style.wrapMode === "none") return [{ units: groups.map((unit) => ({ ...unit, groupUnits: (unit as LyricUnit & { __groupUnits?: LyricUnit[] }).__groupUnits, width: measureUnit(unit, fontSize, style), progress: 0 })), width: groups.reduce((sum, unit) => sum + measureUnit(unit, fontSize, style), 0) }];
  const rows: LayoutRow[] = [];
  let current: LayoutUnit[] = [];
  let width = 0;
  for (const unit of groups) {
    const layoutUnit = { ...unit, groupUnits: (unit as LyricUnit & { __groupUnits?: LyricUnit[] }).__groupUnits, width: measureUnit(unit, fontSize, style), progress: 0 };
    if (current.length && width + layoutUnit.width > maxWidth) {
      rows.push({ units: current, width });
      current = [];
      width = 0;
    }
    current.push(layoutUnit);
    width += layoutUnit.width;
  }
  if (current.length) rows.push({ units: current, width });
  return rows;
}

export function layoutSubtitleLine(line: LyricLine, style: SubtitleStyle, viewport: { width: number; height: number }, currentMs = 0): SubtitleLayout | null {
  if (!line.units.length) return null;
  const maxWidth = Math.max(1, viewport.width * (1 - style.safeAreaLeft - style.safeAreaRight));
  const key = [line.id, line.timing_precision, line.start_ms, line.end_ms, Math.round(maxWidth), viewport.height, style.fontFamily, style.fontSizeMin, style.fontSizeMax,
    style.fontWeight, style.maxLines, style.safeAreaLeft, style.safeAreaRight, style.rubyScale, style.ruby2Scale,
    style.rubyGap, style.lineGap, style.letterSpacing, style.wrapMode, style.positionY,
    JSON.stringify(line.units.map((unit) => [unit.id, unit.surface, unit.ruby, unit.ruby_2, unit.ruby_span, unit.start_ms, unit.end_ms, unit.timing_source, unit.ruby_timing]))].join(";");
  let cached = layoutCache.get(key);
  if (!cached) {
    let low = style.fontSizeMin;
    let high = style.fontSizeMax;
    let best = low;
    for (let i = 0; i < 9; i += 1) {
      const candidate = Math.round((low + high) / 2);
      const rows = rowsFor(line.units, candidate, style, maxWidth);
      if (rows.length <= style.maxLines && Math.max(...rows.map((row) => row.width)) <= maxWidth) {
        best = candidate;
        low = candidate + 1;
      } else high = candidate - 1;
    }
    const rows = rowsFor(line.units, best, style, maxWidth);
    const actualWidth = Math.min(maxWidth, Math.max(...rows.map((row) => row.width)));
    const lineHeight = best * (1 + style.rubyScale + style.rubyGap);
    const height = rows.length * lineHeight + Math.max(0, rows.length - 1) * best * style.lineGap;
    cached = { rows, fontSize: best, width: actualWidth, height, x: (viewport.width - actualWidth) / 2, y: viewport.height * style.positionY - height / 2 };
    layoutCache.set(key, cached);
    if (layoutCache.size > 600) layoutCache.delete(layoutCache.keys().next().value as string);
  }
  const timedStart = (line as LyricLine & { entry_ms?: number }).entry_ms ?? line.start_ms ?? 0;
  const timedEnd = (line as LyricLine & { display_end_ms?: number }).display_end_ms ?? line.end_ms ?? timedStart + 1;
  const fadeIn = style.fadeInMs ? Math.min(1, Math.max(0, (currentMs - timedStart) / style.fadeInMs)) : 1;
  const walkEnd = (line as LyricLine & { walk_end_ms?: number }).walk_end_ms ?? line.end_ms ?? timedEnd;
  const fadeOutDuration = Math.min(style.fadeOutMs, Math.max(0, timedEnd - walkEnd));
  const fadeOut = fadeOutDuration ? Math.min(1, Math.max(0, (timedEnd - currentMs) / fadeOutDuration)) : 1;
  return { ...cached, opacity: Math.min(fadeIn, fadeOut) };
}

export function activeLine(lines: LyricLine[], currentMs: number): LyricLine | null {
  return lines.find((line) => line.start_ms !== null && line.end_ms !== null && currentMs >= line.start_ms && currentMs <= line.end_ms) || null;
}
