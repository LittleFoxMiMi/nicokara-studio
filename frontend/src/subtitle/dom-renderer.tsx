import { useEffect, useMemo, useRef, useState, type CSSProperties, type RefObject } from "react";
import type { LyricLine } from "../editor-types";
import { unitProgress } from "./progress";
import { layoutSubtitleLine } from "./layout";
import { normalizeSubtitleStyle, type SubtitleStyle } from "./style-schema";
import "./subtitle.css";

export function SubtitleDomRenderer({ lines, currentMs, style: rawStyle, className = "", mediaRef, isPlaying = false }: { lines: LyricLine[]; currentMs: number; style?: Partial<SubtitleStyle>; className?: string; mediaRef?: RefObject<HTMLVideoElement | null>; isPlaying?: boolean }) {
  const style = useMemo(() => normalizeSubtitleStyle(rawStyle as Record<string, unknown>), [rawStyle]);
  const layerRef = useRef<HTMLDivElement>(null);
  const [viewport, setViewport] = useState({ width: 1000, height: 600 });
  const [playbackMs, setPlaybackMs] = useState(currentMs);
  useEffect(() => {
    if (!isPlaying) setPlaybackMs(currentMs);
  }, [currentMs, isPlaying]);
  useEffect(() => {
    if (!isPlaying || !mediaRef) return;
    let frame = 0;
    let lastPaint = -Infinity;
    const tick = (now: number) => {
      const video = mediaRef.current;
      if (video && now - lastPaint >= 33) {
        lastPaint = now;
        setPlaybackMs(video.currentTime * 1000);
      }
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [isPlaying, mediaRef]);
  useEffect(() => {
    const element = layerRef.current;
    if (!element) return;
    const update = () => setViewport({ width: Math.max(1, element.clientWidth), height: Math.max(1, element.clientHeight) });
    update();
    const observer = new ResizeObserver(update);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  const renderMs = isPlaying ? playbackMs : currentMs;
  const visible = activeSlots(lines, renderMs);
  const outline = makeStroke(style.outlineColor, style.outlineWidth);
  return <div ref={layerRef} className={`subtitle-layer ${className}`} style={{ fontFamily: style.fontFamily, color: style.textColor, "--subtitle-outline": `${style.outlineWidth}px ${style.outlineColor}`, "--subtitle-shadow": `0 2px ${style.shadowBlur}px ${style.shadowColor}`, "--subtitle-letter-spacing": String(style.letterSpacing) } as CSSProperties} aria-label="字幕预览">
    {visible.map((line) => {
      const layout = layoutSubtitleLine(line, style, viewport, renderMs);
      if (!layout) return null;
      const isLeft = line.slot === "left";
      const lineLevelTiming = line.timing_precision === "line";
      const characterOffsets = new Map<string, number>();
      let characterCount = 0;
      line.units.forEach((sourceUnit) => {
        characterOffsets.set(sourceUnit.id, characterCount);
        characterCount += Array.from(sourceUnit.surface).length;
      });
      return <div className={`subtitle-line ${isLeft ? "subtitle-slot-left" : "subtitle-slot-right"}`} key={`${line.id}-${line.slot}`} style={{ opacity: layout.opacity, top: `${(isLeft ? style.line1Y : style.line2Y) * 100}%`, left: isLeft ? `${style.line1X * 100}%` : "auto", right: isLeft ? "auto" : `${style.line2Right * 100}%`, transform: "translateY(-50%)", maxWidth: `${(1 - style.safeAreaLeft - style.safeAreaRight) * 100}%` }}>
        {layout.rows.map((row, rowIndex) => <div className="subtitle-row" key={`${line.id}-${rowIndex}`}>
          {row.units.map((unit) => { const members = unit.groupUnits?.length ? unit.groupUnits : [unit]; return <span className="subtitle-unit" key={unit.id}><span className="subtitle-ruby" style={{ fontSize: `${layout.fontSize * style.rubyScale}px`, textShadow: outline }}>{unit.ruby || ""}</span><span className="subtitle-ruby subtitle-ruby-2" style={{ fontSize: `${layout.fontSize * style.ruby2Scale}px`, textShadow: outline }}>{unit.ruby_2 || ""}</span><span className="subtitle-surface" style={{ fontSize: `${layout.fontSize}px` }}>{members.flatMap((member) => { const memberOffset = characterOffsets.get(member.id) || 0; const surfaces = lineLevelTiming ? Array.from(member.surface) : [member.surface]; return surfaces.map((surface, charIndex) => { const progress = lineLevelTiming ? fallbackMemberProgress(line, memberOffset + charIndex, characterCount, renderMs) : member.start_ms !== null && member.end_ms !== null ? unitProgress(member.start_ms, member.end_ms, renderMs) : fallbackMemberProgress(line, charIndex, surfaces.length, renderMs); return <span className="subtitle-char-mask" key={`${member.id}-${charIndex}`}><span className="subtitle-char-layer" style={{ color: style.textColor, textShadow: outline }}>{surface}</span><span className="subtitle-char-layer subtitle-char-active" style={{ color: style.activeColor, textShadow: outline, clipPath: `inset(-50% ${100 - progress * 100}% -50% ${progress <= 0 ? "100%" : `-${style.outlineWidth}px`})` }}>{surface}</span></span>; }); })}</span></span>; })}
        </div>)}
        {style.showProgressDots && <span className="subtitle-progress-dots">••••</span>}
      </div>;
    })}
  </div>;
}

type SlotLine = LyricLine & { slot: "left" | "right"; entry_ms: number; display_end_ms?: number };
function activeSlots(lines: LyricLine[], currentMs: number): SlotLine[] {
  const sorted = [...lines].sort((a, b) => a.order - b.order);
  const paragraphs: { line: LyricLine; entry_ms: number; lineInParagraph: number }[][] = [];
  let paragraph = 0;
  let lineInParagraph = 0;
  let paragraphStart = 0;
  for (let index = 0; index < sorted.length; index += 1) {
    const line = sorted[index];
    const previous = sorted[index - 1];
    if (previous && previous.end_ms !== null && line.start_ms !== null && line.start_ms - (previous.end_ms ?? line.start_ms) > 4000) {
      paragraph += 1;
      lineInParagraph = 0;
    }
    if (lineInParagraph === 0) {
      paragraphStart = line.start_ms ?? 0;
      paragraphs[paragraph] = [];
    }
    const entryMs = lineInParagraph < 2 ? paragraphStart - 2000 : (line.start_ms ?? 0) - 2000;
    paragraphs[paragraph].push({ line, entry_ms: entryMs, lineInParagraph });
    lineInParagraph += 1;
  }
  const output: SlotLine[] = [];
  for (const paragraphLines of paragraphs) {
    if (!paragraphLines?.length) continue;
    const firstStart = paragraphLines[0].line.start_ms;
    const lastEnd = paragraphLines[paragraphLines.length - 1].line.end_ms;
    if (firstStart === null || lastEnd === null || currentMs < firstStart - 2000 || currentMs > lastEnd + 2000) continue;
    // Slots stay fixed. The sentence in the right slot reaching one third
    // replaces the finished left sentence with the following line; then the
    // left sentence reaching one third advances the right slot, and so on.
    let leftIndex = 0;
    let rightIndex = paragraphLines.length > 1 ? 1 : -1;
    let triggerIndex = rightIndex;
    while (triggerIndex >= 0 && triggerIndex + 1 < paragraphLines.length) {
      const trigger = paragraphLines[triggerIndex].line;
      if (trigger.start_ms === null || trigger.end_ms === null) break;
      const switchAt = trigger.end_ms > trigger.start_ms ? trigger.start_ms + (trigger.end_ms - trigger.start_ms) / 3 : trigger.start_ms;
      if (currentMs < switchAt) break;
      // Persist the actual replacement moment on the incoming line. Keeping
      // this value stable is important: deriving it from currentMs would
      // restart the fade-in on every animation frame.
      const incoming = paragraphLines[triggerIndex + 1];
      if (incoming) incoming.entry_ms = switchAt;
      if (triggerIndex === rightIndex) {
        leftIndex = triggerIndex + 1;
        triggerIndex = leftIndex;
      } else {
        rightIndex = triggerIndex + 1;
        triggerIndex = rightIndex;
      }
    }
    const selectedRecords: [number, 0 | 1][] = [[leftIndex, 0], ...(rightIndex >= 0 ? [[rightIndex, 1] as [number, 0 | 1]] : [])];
    for (const [recordIndex, slot] of selectedRecords) {
      const record = paragraphLines[recordIndex];
      if (!record || record.line.start_ms === null || record.line.end_ms === null) continue;
      const isLast = recordIndex === paragraphLines.length - 1;
      if (isLast && currentMs > record.line.end_ms + 2000) continue;
      // Keep a completed non-final line visible until the alternating slot
      // trigger replaces it. This prevents a blank slot between sentences.
      output.push({ ...record.line, entry_ms: record.entry_ms, display_end_ms: isLast ? record.line.end_ms : Number.POSITIVE_INFINITY, slot: slot === 0 ? "left" : "right" });
    }
    break;
  }
  return output;
}

function makeStroke(color: string, width: number): string {
  if (!color || width <= 0) return "none";
  const cacheKey = `${color}|${width}`;
  const cached = strokeCache.get(cacheKey);
  if (cached) return cached;
  const shadows: string[] = [];
  for (let radius = 1; radius <= width; radius += 1) {
    for (let step = 0; step < 16; step += 1) {
      const angle = (Math.PI * 2 * step) / 16;
      shadows.push(`${(Math.cos(angle) * radius).toFixed(1)}px ${(Math.sin(angle) * radius).toFixed(1)}px 0 ${color}`);
    }
  }
  const value = shadows.join(",");
  strokeCache.set(cacheKey, value);
  return value;
}

const strokeCache = new Map<string, string>();

function fallbackMemberProgress(line: LyricLine, index: number, count: number, currentMs: number): number {
  if (line.start_ms === null || line.end_ms === null || count <= 0) return 0;
  const span = line.end_ms - line.start_ms;
  return unitProgress(line.start_ms + span * index / count, line.start_ms + span * (index + 1) / count, currentMs);
}
