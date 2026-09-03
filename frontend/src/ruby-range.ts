import type { LyricLine, LyricUnit, ProjectDocument } from "./editor-types";

export type RubyRange = {
  start: number;
  end: number;
  ruby: string;
  ruby2: string;
};

export function lineCharacterCount(line: LyricLine): number {
  return line.units.reduce((total, unit) => total + Array.from(unit.surface).length, 0);
}

export function unitCharacterRange(line: LyricLine, unitId: string): { start: number; end: number } | null {
  let start = 0;
  for (const unit of line.units) {
    const end = start + Array.from(unit.surface).length;
    if (unit.id === unitId) return { start, end };
    start = end;
  }
  return null;
}

export function rubyRanges(line: LyricLine): RubyRange[] {
  const ranges: RubyRange[] = [];
  const total = lineCharacterCount(line);
  let offset = 0;
  let coveredUntil = 0;
  for (let index = 0; index < line.units.length; index += 1) {
    const unit = line.units[index];
    const surfaceLength = Array.from(unit.surface).length;
    const ruby = unit.ruby?.trim() || "";
    const ruby2 = unit.ruby_2?.trim() || "";
    if (offset >= coveredUntil && (ruby || ruby2)) {
      let end = offset + Math.max(surfaceLength, Number(unit.ruby_span || 1));
      if (!unit.ruby_span && ruby) {
        let nextOffset = offset + surfaceLength;
        for (let nextIndex = index + 1; nextIndex < line.units.length; nextIndex += 1) {
          const next = line.units[nextIndex];
          if (next.ruby !== unit.ruby) break;
          nextOffset += Array.from(next.surface).length;
          end = nextOffset;
        }
      }
      coveredUntil = Math.min(total, end);
      ranges.push({ start: offset, end: coveredUntil, ruby, ruby2 });
    }
    offset += surfaceLength;
  }
  return ranges;
}

function splitLineAtOffsets(
  line: LyricLine,
  offsets: Set<number>,
  createId: () => string,
): LyricLine {
  let lineOffset = 0;
  const units = line.units.flatMap((original): LyricUnit[] => {
    const characters = Array.from(original.surface);
    const localCuts = [...offsets]
      .filter((offset) => offset > lineOffset && offset < lineOffset + characters.length)
      .map((offset) => offset - lineOffset)
      .sort((left, right) => left - right);
    const boundaries = [0, ...localCuts, characters.length];
    const duration = original.start_ms !== null && original.end_ms !== null
      ? original.end_ms - original.start_ms
      : null;
    const chunks = boundaries.slice(0, -1).map((start, index): LyricUnit => {
      const end = boundaries[index + 1];
      const first = index === 0;
      const unit: LyricUnit = {
        ...original,
        id: first ? original.id : createId(),
        surface: characters.slice(start, end).join(""),
        start_ms: duration === null ? original.start_ms : Math.round((original.start_ms as number) + duration * start / characters.length),
        end_ms: duration === null ? original.end_ms : Math.round((original.start_ms as number) + duration * end / characters.length),
      };
      if (!first) {
        unit.ruby = null;
        unit.ruby_2 = null;
        unit.ruby_source = "none";
        unit.ruby_span = undefined;
        unit.alignment_reading = undefined;
      }
      return unit;
    });
    lineOffset += characters.length;
    return chunks;
  });
  return { ...line, units };
}

export function updateRubyRange(
  document: ProjectDocument,
  lineId: string,
  start: number,
  end: number,
  ruby: string,
  ruby2: string,
  replacedRange: { start: number; end: number } | null,
  createId: () => string,
): ProjectDocument {
  return {
    ...document,
    lyrics: {
      ...document.lyrics,
      lines: document.lyrics.lines.map((line) => {
        if (line.id !== lineId) return line;
        const total = lineCharacterCount(line);
        if (!total) return line;
        const nextStart = Math.max(0, Math.min(total - 1, Math.floor(start)));
        const nextEnd = Math.max(nextStart + 1, Math.min(total, Math.ceil(end)));
        const previousGroups = rubyRanges(line);
        const splitOffsets = new Set([nextStart, nextEnd]);
        if (replacedRange) {
          splitOffsets.add(replacedRange.start);
          splitOffsets.add(replacedRange.end);
        }
        const rebuilt = splitLineAtOffsets(line, splitOffsets, createId);
        let offset = 0;
        const units = rebuilt.units.map((unit) => {
          const unitStart = offset;
          offset += Array.from(unit.surface).length;
          const belongsToClearedGroup = previousGroups.some((group) => {
            const overlapsNext = group.start < nextEnd && group.end > nextStart;
            const isReplaced = replacedRange && group.start === replacedRange.start && group.end === replacedRange.end;
            return unitStart >= group.start && unitStart < group.end && (overlapsNext || isReplaced);
          });
          if (!belongsToClearedGroup || (!unit.ruby && !unit.ruby_2 && !unit.ruby_span)) return unit;
          return { ...unit, ruby: null, ruby_2: null, ruby_span: undefined, ruby_source: "none" };
        });
        offset = 0;
        const reading = ruby.trim();
        const secondReading = ruby2.trim();
        const updated = units.map((unit) => {
          const unitStart = offset;
          offset += Array.from(unit.surface).length;
          if (unitStart !== nextStart || (!reading && !secondReading)) return unit;
          return {
            ...unit,
            ruby: reading || null,
            ruby_2: secondReading || null,
            ruby_span: nextEnd - nextStart,
            ruby_source: "manual",
            alignment_reading: undefined,
          };
        });
        return { ...rebuilt, units: updated };
      }),
    },
  };
}
