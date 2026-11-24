import { usePreferences } from "../preferences";
import { useEffect, useMemo, useRef, useState, type CSSProperties, type RefObject } from "react";
import type { LyricLine } from "../editor-types";
import { rubyProgress, unitProgress } from "./progress";
import { rubyTiming } from "../ruby-timing";
import { subtitleSurfaceTiming } from "./surface-timing";
import { activeSubtitleSlots, scheduleSubtitleSlots } from "./line-slots";
import { layoutSubtitleLine } from "./layout";
import { normalizeSubtitleStyle, type SubtitleStyle } from "./style-schema";
import "./subtitle.css";

export function SubtitleDomRenderer({ lines, currentMs, style: rawStyle, className = "", mediaRef, isPlaying = false }: { lines: LyricLine[]; currentMs: number; style?: Partial<SubtitleStyle>; className?: string; mediaRef?: RefObject<HTMLVideoElement | null>; isPlaying?: boolean }) {
  const { t } = usePreferences();
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
  const entryBuffer = style.showProgressDots ? 3500 + style.fadeInMs : 2000;
  const schedule = useMemo(() => scheduleSubtitleSlots(lines, entryBuffer), [lines, entryBuffer]);
  const visible = activeSubtitleSlots(schedule, renderMs);
  const outline = makeStroke(style.outlineColor, style.outlineWidth);
  function highlight(text: string, progress: number, key?: string) {
    return <span className="subtitle-char-mask" key={key}>
      <span className="subtitle-char-layer" style={{ color: style.textColor, textShadow: outline }}>{text}</span>
      <span className="subtitle-char-layer subtitle-char-active" aria-hidden="true" style={{
        color: style.activeColor,
        textShadow: outline,
        clipPath: `inset(-50% ${100 - progress * 100}% -50% ${progress <= 0 ? "100%" : `-${style.outlineWidth}px`})`,
      }}>{text}</span>
    </span>;
  }
  return <div ref={layerRef} className={`subtitle-layer ${className}`} style={{ fontFamily: style.fontFamily, color: style.textColor, "--subtitle-outline": `${style.outlineWidth}px ${style.outlineColor}`, "--subtitle-shadow": `0 2px ${style.shadowBlur}px ${style.shadowColor}`, "--subtitle-letter-spacing": String(style.letterSpacing) } as CSSProperties} aria-label={t("字幕预览")}>
    {visible.map((line) => {
      const layout = layoutSubtitleLine(line, style, viewport, renderMs);
      if (!layout) return null;
      const isLeft = line.slot === "left";
      const lineLevelTiming = line.timing_precision === "line";
      const surfaceTiming = subtitleSurfaceTiming(line);
      return <div className={`subtitle-line ${isLeft ? "subtitle-slot-left" : "subtitle-slot-right"}`} key={`${line.id}-${line.slot}`} style={{ opacity: layout.opacity, top: `${(isLeft ? style.line1Y : style.line2Y) * 100}%`, left: isLeft ? `${style.line1X * 100}%` : "auto", right: isLeft ? "auto" : `${style.line2Right * 100}%`, transform: "translateY(-50%)", maxWidth: `${(1 - style.safeAreaLeft - style.safeAreaRight) * 100}%` }}>
        {layout.rows.map((row, rowIndex) => <div className="subtitle-row" key={`${line.id}-${rowIndex}`}>
          {row.units.map((unit) => {
            const timing = !lineLevelTiming && unit.start_ms !== null && unit.end_ms !== null
              ? rubyTiming(unit, unit.start_ms, unit.end_ms) : undefined;
            const members = unit.groupUnits?.length ? unit.groupUnits : [unit];
            return <span className="subtitle-unit" key={unit.id}>
              <span className="subtitle-ruby" style={{ fontSize: `${layout.fontSize * style.rubyScale}px`, textShadow: outline }}>
                {timing ? highlight(unit.ruby || "", rubyProgress(timing, renderMs)) : unit.ruby || ""}
              </span>
              <span className="subtitle-ruby subtitle-ruby-2" style={{ fontSize: `${layout.fontSize * style.ruby2Scale}px`, textShadow: outline }}>{unit.ruby_2 || ""}</span>
              <span className="subtitle-surface" style={{ fontSize: `${layout.fontSize}px` }}>
                {timing ? highlight(unit.surface, rubyProgress(timing, renderMs)) : members.flatMap((member) =>
                  surfaceTiming.get(member.id)?.map((part, index) => highlight(
                    part.text, unitProgress(part.timing.start, part.timing.end, renderMs), `${member.id}-${index}`,
                  )) ?? [])}
              </span>
            </span>;
          })}
        </div>)}
        {style.showProgressDots && <span className="subtitle-progress-dots">••••</span>}
      </div>;
    })}
  </div>;
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
