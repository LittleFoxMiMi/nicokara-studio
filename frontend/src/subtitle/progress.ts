export function unitProgress(startMs: number | null, endMs: number | null, currentMs: number): number {
  if (startMs === null || endMs === null) return 0;
  // Kirakara treats a zero-length timestamp as an instantaneous syllable:
  // it becomes fully colored once the playhead reaches that timestamp.
  if (endMs <= startMs) return currentMs >= endMs ? 1 : 0;
  return Math.max(0, Math.min(1, (currentMs - startMs) / (endMs - startMs)));
}

export function lineProgress(units: { start_ms: number | null; end_ms: number | null }[], currentMs: number): number {
  const timed = units.filter((unit) => unit.start_ms !== null && unit.end_ms !== null);
  if (!timed.length) return 0;
  const start = Math.min(...timed.map((unit) => unit.start_ms as number));
  const end = Math.max(...timed.map((unit) => unit.end_ms as number));
  return unitProgress(start, end, currentMs);
}

// Each Ruby unit advances only during its own window. Once it ends, its
// width stays filled until the next unit starts, including across silent gaps.
export function rubyProgress(parts: { text: string; start_ms: number; end_ms: number }[], currentMs: number): number {
  let total = 0;
  let filled = 0;
  for (const part of parts) {
    const width = Array.from(part.text).length;
    total += width;
    filled += width * unitProgress(part.start_ms, part.end_ms, currentMs);
  }
  return total ? filled / total : 0;
}

export function isSilentPunctuation(character: string, previous = "", next = ""): boolean {
  if (/[’']/.test(character) && /^[A-Za-z]$/.test(previous) && /^[A-Za-z]$/.test(next)) return false;
  if (/[.,]/.test(character) && /^\p{Nd}$/u.test(previous) && /^\p{Nd}$/u.test(next)) return false;
  return /^[\p{P}~～]$/u.test(character);
}
