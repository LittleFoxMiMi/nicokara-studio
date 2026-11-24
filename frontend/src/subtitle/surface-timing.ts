import type { LyricLine, LyricUnit } from "../editor-types";
import { rubyGroupEnd, rubyTiming, timedBounds } from "../ruby-timing";
import { isSilentPunctuation } from "./progress";

type Window = { start: number; end: number };
type SurfacePart = { text: string; silent: boolean; timing: Window };

export function subtitleSurfaceTiming(line: LyricLine): Map<string, SurfacePart[]> {
  const units = line.units;
  const text = units.flatMap((unit) => Array.from(unit.surface));
  const punctuation = text.map((character, index) => isSilentPunctuation(character, text[index - 1], text[index + 1]));
  const silent = punctuation.some(Boolean) ? punctuation.map((flag, index) => flag || /^\s$/u.test(text[index])) : punctuation;
  const groups: { members: LyricUnit[]; ruby: boolean }[] = [];
  let offset = 0;
  for (let index = 0; index < units.length;) {
    const end = rubyGroupEnd(units, index);
    const members = units.slice(index, end);
    const ruby = Boolean(units[index].ruby || units[index].ruby_2);
    const count = members.reduce((sum, member) => sum + Array.from(member.surface).length, 0);
    if (ruby) silent.fill(false, offset, offset + count);
    groups.push({ members, ruby });
    offset += count;
    index = end;
  }
  const lineStart = line.start_ms ?? 0;
  const lineEnd = line.end_ms ?? lineStart;
  const lineLevel = line.timing_precision === "line";
  const spokenCount = Math.max(1, silent.filter((value) => !value).length);
  const result = new Map<string, SurfacePart[]>();
  const parts: SurfacePart[] = [];
  offset = 0;
  let spokenOffset = 0;
  for (const { members, ruby } of groups) {
    const bounds = timedBounds(members);
    const first = members[0];
    const reading = !lineLevel && ruby && bounds ? rubyTiming(first, bounds.start_ms, bounds.end_ms) : undefined;
    for (const member of members) {
      const characters = Array.from(member.surface);
      const flags = silent.slice(offset, offset + characters.length);
      offset += characters.length;
      const count = flags.filter((value) => !value).length;
      const original = !lineLevel && member.start_ms !== null && member.end_ms !== null;
      const start = reading?.[0].start_ms ?? (original ? member.start_ms! : lineStart + (lineEnd - lineStart) * spokenOffset / spokenCount);
      const end = reading?.[reading.length - 1].end_ms ?? (original ? member.end_ms! : lineStart + (lineEnd - lineStart) * (spokenOffset + count) / spokenCount);
      spokenOffset += count;
      const memberParts: SurfacePart[] = [];
      let spoken = 0;
      characters.forEach((character, index) => {
        const part = { text: character, silent: flags[index], timing: {
          start: start + (end - start) * spoken / Math.max(1, count),
          end: start + (end - start) * (spoken + Number(!flags[index])) / Math.max(1, count),
        } };
        if (!part.silent) spoken++;
        memberParts.push(part);
        parts.push(part);
      });
      // Keep an ordinary word/unit together in the preview to preserve spacing.
      result.set(member.id, !lineLevel && count === characters.length && characters.length
        ? [{ text: member.surface, silent: false, timing: { start, end } }] : memberParts);
    }
  }
  for (let index = 0; index < parts.length;) {
    if (!parts[index].silent) { index++; continue; }
    let end = index + 1;
    while (end < parts.length && parts[end].silent) end++;
    const previous = parts[index - 1]?.timing.end;
    const next = parts[end]?.timing.start;
    const startMs = previous ?? next ?? lineStart;
    const endMs = Math.max(startMs, next ?? startMs);
    const width = Math.max(1, parts.slice(index, end).filter((part) => !/^\s$/u.test(part.text)).length);
    let cursor = 0;
    for (let i = index; i < end; i++) {
      const start = startMs + (endMs - startMs) * cursor / width;
      if (!/^\s$/u.test(parts[i].text)) cursor++;
      parts[i].timing = { start, end: startMs + (endMs - startMs) * cursor / width };
    }
    index = end;
  }
  return result;
}
