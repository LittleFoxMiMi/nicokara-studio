import type { LyricLine, LyricUnit } from "./editor-types";
import { rubyGroupEnd, timedBounds } from "./ruby-timing";
import { isSilentPunctuation } from "./subtitle/progress";

const kana = /^[\u3041-\u3096\u309d-\u309f\u30a1-\u30fa\u30fc-\u30ff]$/u;
const kanaSuffix = /^[ゃゅょぁぃぅぇぉゎゕゖャュョァィゥェォヮヵヶーっッ]$/u;

export function replaceLyricLineText(line: LyricLine, text: string, createId: () => string): LyricLine {
  const surface = text.trim();
  const oldSurface = line.units.map((unit) => unit.surface).join("");
  if (!surface || surface === oldSurface) return line;

  const before = Array.from(oldSurface);
  const after = Array.from(surface);
  const annotations: { start: number; end: number; unit: LyricUnit; text: string[] }[] = [];
  const roles = line.units.flatMap((unit) => Array.from(unit.surface, () => unit.roles));
  for (let index = 0, offset = 0; index < line.units.length;) {
    const unit = line.units[index];
    const endIndex = rubyGroupEnd(line.units, index);
    const length = line.units.slice(index, endIndex).reduce((sum, member) => sum + Array.from(member.surface).length, 0);
    const end = offset + length;
    if ((unit.ruby || unit.ruby_2) && length) {
      annotations.push({ start: offset, end, unit, text: before.slice(offset, end) });
    }
    index = endIndex;
    offset = end;
  }

  const candidates = annotations.flatMap((annotation, index) => {
    const matches: { index: number; start: number; end: number; context: number; distance: number }[] = [];
    const length = annotation.text.length;
    for (let start = 0; start + length <= after.length; start++) {
      if (!annotation.text.every((character, i) => after[start + i] === character)) continue;
      const end = start + length;
      // Neighbouring text distinguishes different readings of a repeated word.
      let left = 0;
      while (start > left && annotation.start > left && after[start - left - 1] === before[annotation.start - left - 1]) left++;
      let right = 0;
      while (end + right < after.length && annotation.end + right < before.length && after[end + right] === before[annotation.end + right]) right++;
      matches.push({ index, start, end, context: left + right, distance: Math.abs(start - annotation.start) });
    }
    return matches;
  });
  candidates.sort((a, b) => (b.end - b.start) - (a.end - a.start) || b.context - a.context || a.distance - b.distance);
  const occupied = Array.from(after, () => false);
  const used = new Set<number>();
  const retained = new Map<number, { unit: LyricUnit; end: number }>();
  for (const match of candidates) {
    if (used.has(match.index) || occupied.slice(match.start, match.end).some(Boolean)) continue;
    used.add(match.index);
    occupied.fill(true, match.start, match.end);
    retained.set(match.start, { unit: annotations[match.index].unit, end: match.end });
  }

  let prefix = 0;
  while (prefix < Math.min(before.length, after.length) && before[prefix] === after[prefix]) prefix++;
  let suffix = 0;
  while (suffix < Math.min(before.length, after.length) - prefix && before[before.length - suffix - 1] === after[after.length - suffix - 1]) suffix++;
  const inheritedRole = roles[Math.min(prefix, roles.length - 1)] ?? [];
  const rolesAt = (index: number) => {
    if (index < prefix) return roles[index];
    if (index >= after.length - suffix) return roles[before.length - after.length + index];
    return inheritedRole;
  };
  const untimed = { start_ms: null, end_ms: null, timing_source: "none", timing_confidence: null, locked: false };
  const units: LyricUnit[] = [];
  for (let start = 0; start < after.length;) {
    const ruby = retained.get(start);
    let end = ruby?.end ?? start + 1;
    if (ruby) {
      units.push({
        ...ruby.unit,
        ...untimed,
        surface: after.slice(start, end).join(""),
        ruby_span: end - start,
        ruby_timing: undefined,
        alignment_reading: undefined,
      });
    } else {
      const segmentRoles = rolesAt(start);
      const isKana = kana.test(after[start]);
      const isPunctuation = (index: number) => isSilentPunctuation(after[index], after[index - 1], after[index + 1]);
      while (end < after.length && !occupied[end] && !isPunctuation(start) && !isPunctuation(end)) {
        if (isKana ? !kanaSuffix.test(after[end]) : kana.test(after[end])) break;
        const nextRoles = rolesAt(end);
        if (nextRoles.length !== segmentRoles.length || nextRoles.some((role, index) => role !== segmentRoles[index])) break;
        end++;
      }
      units.push({
        ...untimed,
        id: createId(),
        surface: after.slice(start, end).join(""),
        ruby: null,
        ruby_source: "none",
        roles: segmentRoles,
      });
    }
    start = end;
  }
  const oldBounds = timedBounds(line.units);
  return {
    ...line,
    units,
    start_ms: line.start_ms ?? oldBounds?.start_ms ?? null,
    end_ms: line.end_ms ?? oldBounds?.end_ms ?? null,
    timing_source: "manual",
    timing_precision: "line",
  };
}
