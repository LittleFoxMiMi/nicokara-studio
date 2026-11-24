import type { LyricLine, LyricUnit, RubyTiming } from "./editor-types";

export function rubyGroupEnd(units: LyricUnit[], index: number): number {
  const first = units[index];
  let covered = Array.from(first.surface).length;
  let end = index + 1;
  while (covered < Number(first.ruby_span || 1) && end < units.length) {
    covered += Array.from(units[end++].surface).length;
  }
  if (!first.ruby_span && first.ruby) {
    while (end < units.length && units[end].ruby === first.ruby && units[end].ruby_2 === first.ruby_2) end++;
  }
  return end;
}

export function timedBounds(units: LyricUnit[]) {
  const timed = units.filter((unit) => unit.start_ms !== null && unit.end_ms !== null);
  return timed.length ? {
    start_ms: Math.min(...timed.map((unit) => unit.start_ms!)),
    end_ms: Math.max(...timed.map((unit) => unit.end_ms!)),
  } : null;
}

export function rubyTiming(unit: LyricUnit, start: number, end: number): RubyTiming[] | undefined {
  const timing = unit.ruby_timing;
  if (!timing?.length || !unit.ruby || timing.map((part) => part.text).join("") !== unit.ruby) return undefined;
  let cursor = start;
  for (const part of timing) {
    if (!part.text || !Number.isFinite(part.start_ms) || !Number.isFinite(part.end_ms) ||
        part.start_ms < cursor || part.end_ms < part.start_ms || part.end_ms > end) return undefined;
    cursor = part.end_ms;
  }
  return timing;
}

export function initialRubyTiming(unit: LyricUnit, start: number, end: number): RubyTiming[] {
  const existing = rubyTiming(unit, start, end);
  if (existing) return existing;
  const morae = unit.ruby?.match(/.[ゃゅょぁぃぅぇぉゎャュョァィゥェォヮ]*/gu) || [];
  return morae.map((text, index) => ({
    text,
    start_ms: Math.round(start + (end - start) * index / morae.length),
    end_ms: Math.round(start + (end - start) * (index + 1) / morae.length),
  }));
}

export function retimeRubyGroup(line: LyricLine, unitId: string, start: number, end: number, timing?: RubyTiming[]): LyricLine {
  const index = line.units.findIndex((unit) => unit.id === unitId);
  if (index < 0) return line;
  const memberEnd = rubyGroupEnd(line.units, index);
  const members = line.units.slice(index, memberEnd);
  const old = timedBounds(members);
  if (!old) return line;
  const scale = (value: number) => Math.round(start + (value - old.start_ms) * (end - start) / Math.max(1, old.end_ms - old.start_ms));
  const existing = rubyTiming(members[0], old.start_ms, old.end_ms);
  const units = line.units.map((unit, i) => {
    if (i < index || i >= memberEnd) return unit;
    const patch = i === index ? {
      ruby_timing: timing ?? existing?.map((part) => ({ ...part, start_ms: scale(part.start_ms), end_ms: scale(part.end_ms) })),
    } : {};
    if (start === old.start_ms && end === old.end_ms) return { ...unit, ...patch };
    return {
      ...unit,
      ...patch,
      start_ms: i === index ? start : unit.start_ms === null ? null : scale(unit.start_ms),
      end_ms: i === memberEnd - 1 ? end : unit.end_ms === null ? null : scale(unit.end_ms),
      timing_source: "manual",
      timing_confidence: 1,
    };
  });
  return { ...line, ...timedBounds(units), units };
}
