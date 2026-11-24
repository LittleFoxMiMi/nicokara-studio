import type { LyricLine } from "../editor-types";
import { subtitleSurfaceTiming } from "./surface-timing";

export type SlotLine = LyricLine & { slot: "left" | "right"; entry_ms: number; walk_end_ms: number; display_end_ms: number };

export function scheduleSubtitleSlots(lines: LyricLine[], entryBufferMs: number): SlotLine[] {
  const timed = lines
    .filter((line): line is LyricLine & { start_ms: number; end_ms: number } => line.start_ms !== null && line.end_ms !== null)
    .sort((a, b) => a.order - b.order)
    .map((line) => {
      const parts = [...subtitleSurfaceTiming(line).values()].flat();
      const spoken = parts.filter((part) => !part.silent && part.text.trim());
      return {
        line,
        start: spoken[0]?.timing.start ?? line.start_ms,
        end: spoken[spoken.length - 1]?.timing.end ?? line.end_ms,
      };
    });
  const schedule: SlotLine[] = [];
  let paragraphStart = 0;
  for (let index = 0; index < timed.length; index++) {
    const line = timed[index];
    const previous = timed[index - 1];
    if (!previous || line.start - previous.end > entryBufferMs + 2000) paragraphStart = index;
    const next = timed[index + 1];
    const nextInParagraph = next && next.start - line.end <= entryBufferMs + 2000;
    const nextMidpoint = nextInParagraph ? (next.start + next.end) / 2 : Infinity;
    const displayEnd = Math.max(line.end, Math.min(line.end + 1000, nextMidpoint));
    const indexInParagraph = index - paragraphStart;
    const entry = indexInParagraph < 2 ? timed[paragraphStart].start - entryBufferMs : schedule[index - 2].display_end_ms;
    schedule.push({ ...line.line, entry_ms: entry, walk_end_ms: line.end, display_end_ms: displayEnd, slot: indexInParagraph % 2 === 0 ? "left" : "right" });
  }
  return schedule;
}

export function activeSubtitleSlots(schedule: SlotLine[], currentMs: number): SlotLine[] {
  let left: SlotLine | undefined;
  let right: SlotLine | undefined;
  for (const line of schedule) {
    if (currentMs < line.entry_ms || currentMs >= line.display_end_ms) continue;
    if (line.slot === "left") left = line;
    else right = line;
  }
  return [left, right].filter((line): line is SlotLine => line !== undefined);
}
