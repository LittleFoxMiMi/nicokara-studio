import {
  FastForward,
  Minus,
  Pause,
  Play,
  Plus,
  Rewind,
  ZoomIn,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { RefObject } from "react";
import type { LyricLine, LyricUnit, Waveform } from "./editor-types";
import { api, formatTime } from "./editor-types";

type Hit = {
  unit: LyricUnit;
  lineId: string;
  lineLevel: boolean;
  x1: number;
  x2: number;
  y1: number;
  y2: number;
  rubyGroup?: RubyGroup;
};
type RubyGroup = {
  lineId: string;
  ruby: string;
  startIndex: number;
  endIndex: number;
  start: number;
  end: number;
  x1: number;
  x2: number;
  y1: number;
  y2: number;
  units: LyricUnit[];
};
type Drag = {
  hit: Hit;
  mode: "move" | "start" | "end" | "ruby-start" | "ruby-end";
  clientX: number;
  start: number;
  end: number;
  previewStart: number;
  previewEnd: number;
  began: boolean;
  rubyGroup?: RubyGroup;
  previewRubyStart?: number;
  previewRubyEnd?: number;
};

const MAX_CANVAS_DIMENSION = 32760;
const CANVAS_TILE_WIDTH = 12000;

export function TimelineCanvas({
  projectId,
  waveformSource,
  lines,
  durationMs,
  mediaRef,
  hasVideo,
  isPlaying,
  selectedId,
  onSelect,
  onSeek,
  onSeekBy,
  onTogglePlayback,
  onBeginEdit,
  onUpdateUnit,
  onUpdateLine,
  onUpdateRubyGroup,
  onOpenEditor,
  onDropLine,
}: {
  projectId: string;
  waveformSource: string;
  lines: LyricLine[];
  durationMs: number;
  mediaRef: RefObject<HTMLVideoElement | null>;
  hasVideo: boolean;
  isPlaying: boolean;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  onSeek: (ms: number) => void;
  onSeekBy: (deltaMs: number) => void;
  onTogglePlayback: () => void;
  onBeginEdit: () => void;
  onUpdateUnit: (
    lineId: string,
    unitId: string,
    patch: Partial<LyricUnit>,
  ) => void;
  onUpdateLine: (lineId: string, startMs: number, endMs: number) => void;
  onUpdateRubyGroup: (lineId: string, unitIds: string[], ruby: string, rubySpan: number, clearUnitIds: string[]) => void;
  onOpenEditor: (id: string) => void;
  onDropLine: (lineId: string, startMs: number) => void;
}) {
  const canvasRefs = useRef<(HTMLCanvasElement | null)[]>([]);
  const contentRef = useRef<HTMLDivElement>(null);
  const scrollerRef = useRef<HTMLDivElement>(null);
  const playheadRef = useRef<HTMLDivElement>(null);
  const dragPreviewRef = useRef<HTMLDivElement>(null);
  const dropMarkerRef = useRef<HTMLDivElement>(null);
  const timecodeRef = useRef<HTMLOutputElement>(null);
  const hitsRef = useRef<Hit[]>([]);
  const rubyGroupsRef = useRef<RubyGroup[]>([]);
  const dragRef = useRef<Drag | null>(null);
  const zoomAnchorRef = useRef<{ timeMs: number; screenX: number } | null>(null);
  const [waveform, setWaveform] = useState<Waveform | null>(null);
  const [waveformError, setWaveformError] = useState(false);
  const [rubyAdjustEnabled, setRubyAdjustEnabled] = useState(false);
  const [zoom, setZoom] = useState(80);
  const width = Math.max(900, Math.ceil((durationMs / 1000) * zoom));
  const height = 260;
  const tileCount = Math.max(1, Math.ceil(width / CANVAS_TILE_WIDTH));
  const timedUnits = useMemo(
    () =>
      lines
        .flatMap((line) => {
          if (line.timing_precision === "line" && line.start_ms !== null && line.end_ms !== null && line.units.length) {
            return [{
              line,
              lineLevel: true,
              unit: {
                ...line.units[0],
                surface: line.units.map((unit) => unit.surface).join(""),
                start_ms: line.start_ms,
                end_ms: line.end_ms,
                timing_source: line.timing_source,
                timing_confidence: null,
              },
            }];
          }
          return line.units.map((unit) => ({ line, unit, lineLevel: false }));
        })
        .filter(({ unit }) => unit.start_ms !== null && unit.end_ms !== null),
    [lines],
  );
  const lineRubyGroups = useMemo(() => lines.flatMap((line) => {
    if (line.timing_precision === "line") return [];
    const groups: RubyGroup[] = [];
    for (let index = 0; index < line.units.length;) {
      const first = line.units[index];
      const span = Math.max(1, Number(first.ruby_span || 1));
      let endIndex = Math.min(line.units.length, index + span);
      if (!first.ruby_span && first.ruby) {
        while (endIndex < line.units.length && line.units[endIndex].ruby === first.ruby) endIndex += 1;
      }
      if (first.ruby && line.units.slice(index, endIndex).some((unit) => unit.start_ms !== null && unit.end_ms !== null)) {
        const timed = line.units.slice(index, endIndex).filter((unit) => unit.start_ms !== null && unit.end_ms !== null);
        const start = Math.min(...timed.map((unit) => unit.start_ms as number));
        const end = Math.max(...timed.map((unit) => unit.end_ms as number));
        groups.push({ lineId: line.id, ruby: first.ruby, startIndex: index, endIndex, start, end, x1: 0, x2: 0, y1: 0, y2: 0, units: line.units });
      }
      index = Math.max(index + 1, endIndex);
    }
    return groups;
  }), [lines]);

  useEffect(() => {
    let active = true;
    const load = () => {
      if (!hasVideo) return;
      setWaveformError(false);
      void api<Waveform>(`/projects/${projectId}/waveform`)
        .then((value) => active && setWaveform(value))
        .catch(() => active && setWaveformError(true));
    };
    load();
    document.addEventListener("loadedmetadata", load, true);
    return () => {
      active = false;
      document.removeEventListener("loadedmetadata", load, true);
    };
  }, [hasVideo, projectId, waveformSource]);

  useEffect(() => {
    let frame = 0;
    const update = () => {
      const currentMs = (mediaRef.current?.currentTime || 0) * 1000;
      const x = (currentMs / 1000) * zoom;
      if (playheadRef.current)
        playheadRef.current.style.transform = `translate3d(${x}px,0,0)`;
      if (timecodeRef.current)
        timecodeRef.current.value = formatTime(currentMs);
      const scroller = scrollerRef.current;
      if (isPlaying && scroller && currentMs > 0 && x >= scroller.scrollLeft + scroller.clientWidth - 6) {
        scroller.scrollLeft = Math.min(Math.max(0, width - scroller.clientWidth), x);
      }
      frame = requestAnimationFrame(update);
    };
    frame = requestAnimationFrame(update);
    return () => cancelAnimationFrame(frame);
  }, [isPlaying, mediaRef, width, zoom]);

  useEffect(() => {
    const anchor = zoomAnchorRef.current;
    const scroller = scrollerRef.current;
    if (!anchor || !scroller) return;
    const target = (anchor.timeMs / 1000) * zoom - anchor.screenX;
    const maxScroll = Math.max(0, width - scroller.clientWidth);
    scroller.scrollLeft = Math.max(0, Math.min(maxScroll, target));
    zoomAnchorRef.current = null;
  }, [width, zoom]);

  function changeZoom(nextZoom: number) {
    const next = Math.max(30, Math.min(180, nextZoom));
    if (next === zoom) return;
    const scroller = scrollerRef.current;
    if (scroller && scroller.clientWidth > 0) {
      const currentMs = (mediaRef.current?.currentTime || 0) * 1000;
      const playheadX = (currentMs / 1000) * zoom;
      const visibleStart = scroller.scrollLeft;
      const visibleEnd = visibleStart + scroller.clientWidth;
      const playheadVisible = playheadX >= visibleStart && playheadX <= visibleEnd;
      const screenX = playheadVisible ? playheadX - visibleStart : scroller.clientWidth / 2;
      const anchorMs = playheadVisible
        ? currentMs
        : ((visibleStart + scroller.clientWidth / 2) / zoom) * 1000;
      zoomAnchorRef.current = { timeMs: anchorMs, screenX };
    }
    setZoom(next);
  }

  useEffect(() => {
    const canvases = canvasRefs.current.slice(0, tileCount).filter((canvas): canvas is HTMLCanvasElement => Boolean(canvas));
    if (!canvases.length) return;
    const ratio = window.devicePixelRatio || 1;
    const hits: Hit[] = [];
    const hitKeys = new Set<string>();
    for (let tileIndex = 0; tileIndex < canvases.length; tileIndex += 1) {
      const canvas = canvases[tileIndex];
      const offset = tileIndex * CANVAS_TILE_WIDTH;
      const tileWidth = Math.min(CANVAS_TILE_WIDTH, width - offset);
      const tileRatio = Math.min(ratio, MAX_CANVAS_DIMENSION / tileWidth, MAX_CANVAS_DIMENSION / height);
      canvas.width = Math.round(tileWidth * tileRatio);
      canvas.height = Math.round(height * tileRatio);
      canvas.style.width = `${tileWidth}px`;
      canvas.style.height = `${height}px`;
      const context = canvas.getContext("2d");
      if (!context) continue;
      context.setTransform(1, 0, 0, 1, 0, 0);
      context.clearRect(0, 0, canvas.width, canvas.height);
      context.setTransform(tileRatio, 0, 0, tileRatio, -offset * tileRatio, 0);
      context.fillStyle = "#fff";
    context.fillRect(0, 0, width, height);

    const seconds = durationMs / 1000;
    const tickSeconds = zoom >= 70 ? 1 : zoom >= 40 ? 2 : 5;
    context.font = "11px Google Sans, sans-serif";
    context.textBaseline = "top";
    for (let second = 0; second <= seconds; second += tickSeconds) {
      const x = second * zoom;
      const major = second % 5 === 0;
      context.strokeStyle = major ? "#b9c0c7" : "#e3e7eb";
      context.lineWidth = major ? 1 : 0.5;
      context.beginPath();
      context.moveTo(x + 0.5, major ? 24 : 30);
      context.lineTo(x + 0.5, height);
      context.stroke();
      if (major || zoom >= 70) {
        context.fillStyle = "#687078";
        context.fillText(formatTime(second * 1000).slice(0, 5), x + 5, 7);
      }
    }

    const rubyGroups = lineRubyGroups.map((group) => {
      const lane = lines.find((line) => line.id === group.lineId)?.order ?? 0;
      const y1 = 40 + (lane % 2) * 43 - 4;
      const y2 = y1 + 43;
      const next = { ...group, x1: (group.start / 1000) * zoom - 3, x2: (group.end / 1000) * zoom + 3, y1, y2 };
      context.save();
      context.strokeStyle = "#e37400";
      context.lineWidth = 2;
      context.beginPath();
      context.moveTo(next.x1 + 6, y1); context.lineTo(next.x1, y1); context.lineTo(next.x1, y2); context.lineTo(next.x1 + 6, y2);
      context.moveTo(next.x2 - 6, y1); context.lineTo(next.x2, y1); context.lineTo(next.x2, y2); context.lineTo(next.x2 - 6, y2);
      context.stroke();
      context.restore();
      return next;
    });
    rubyGroupsRef.current = rubyGroups;
    for (const line of lines) {
      const timed = line.timing_precision === "line" && line.start_ms !== null && line.end_ms !== null
        ? [{ start_ms: line.start_ms, end_ms: line.end_ms }]
        : line.units.filter((unit) => unit.start_ms !== null && unit.end_ms !== null);
      if (!timed.length) continue;
      const start = Math.min(...timed.map((unit) => unit.start_ms as number));
      const end = Math.max(...timed.map((unit) => unit.end_ms as number));
      const lane = line.order % 2;
      const y1 = 36 + lane * 43;
      const y2 = y1 + 43;
      const x1 = (start / 1000) * zoom - 7;
      const x2 = (end / 1000) * zoom + 7;
      context.save();
      context.strokeStyle = "#1a73e8";
      context.lineWidth = 2;
      context.beginPath();
      context.moveTo(x1 + 8, y1); context.lineTo(x1, y1); context.lineTo(x1, y2); context.lineTo(x1 + 8, y2);
      context.moveTo(x2 - 8, y1); context.lineTo(x2, y1); context.lineTo(x2, y2); context.lineTo(x2 - 8, y2);
      context.stroke();
      context.restore();
    }
    for (const { line, unit, lineLevel } of timedUnits) {
      const start = unit.start_ms as number;
      const end = unit.end_ms as number;
      const x1 = (start / 1000) * zoom;
      const x2 = Math.max(x1 + 5, (end / 1000) * zoom);
      const lane = line.order % 2;
      const y1 = 40 + lane * 43;
      const y2 = y1 + 35;
      const selected = unit.id === selectedId;
      const lowConfidence = unit.timing_confidence !== null && unit.timing_confidence < 0.55;
      context.fillStyle = selected
        ? "#0b57d0"
        : lowConfidence
          ? "#ffecb5"
        : unit.timing_source === "estimated"
          ? "#fef3c7"
          : "#d3e3fd";
      context.strokeStyle = selected
        ? "#0842a0"
        : lowConfidence
          ? "#c58a00"
        : unit.timing_source === "estimated"
          ? "#f9ab00"
          : "#7baaf7";
      context.lineWidth = selected ? 2 : 1;
      context.beginPath();
      context.roundRect(x1 + 1, y1, Math.max(3, x2 - x1 - 2), y2 - y1, 5);
      context.fill();
      context.stroke();
      context.save();
      context.beginPath();
      context.rect(x1 + 5, y1 + 3, Math.max(0, x2 - x1 - 10), y2 - y1 - 6);
      context.clip();
      context.fillStyle = selected ? "#fff" : "#1f1f1f";
      context.font = "12px Noto Sans JP, sans-serif";
      context.textBaseline = "middle";
      context.fillText(unit.surface, x1 + 7, y1 + 18);
      context.restore();
      const hitKey = `${line.id}:${unit.id}`;
      if (!hitKeys.has(hitKey)) {
        hitKeys.add(hitKey);
        hits.push({
          unit,
          lineId: line.id,
          lineLevel,
          x1: lineLevel ? x1 - 7 : x1,
          x2: lineLevel ? x2 + 7 : x2,
          y1,
          y2,
          rubyGroup: rubyGroups.find((group) => group.lineId === line.id && group.start <= start && group.end >= end),
        });
      }
    }

    const waveTop = 132;
    const waveHeight = 108;
    const center = waveTop + waveHeight / 2;
    context.fillStyle = "#f2f7f5";
    context.fillRect(0, waveTop, width, waveHeight);
    context.strokeStyle = "#d5e4df";
    context.beginPath();
    context.moveTo(0, center + 0.5);
    context.lineTo(width, center + 0.5);
    context.stroke();
    if (waveform?.peaks.length) {
      const maxAmplitude = Math.max(
        ...waveform.peaks.flatMap(([min, max]) => [
          Math.abs(min),
          Math.abs(max),
        ]),
        0.01,
      );
      const gain = Math.min(6, 0.92 / maxAmplitude);
      context.strokeStyle = "#087f5b";
      context.lineWidth = Math.max(
        1,
        Math.min(2, width / waveform.peaks.length),
      );
      context.beginPath();
      waveform.peaks.forEach(([min, max], index) => {
        const sampleMs =
          (index / Math.max(1, waveform.peaks.length - 1)) *
          waveform.duration_ms;
        const x = (sampleMs / 1000) * zoom;
        context.moveTo(x, center + min * gain * waveHeight * 0.48);
        context.lineTo(x, center + max * gain * waveHeight * 0.48);
      });
      context.stroke();
    } else {
      context.fillStyle = waveformError ? "#b3261e" : "#7b838c";
      context.font = "12px Google Sans, sans-serif";
      context.textAlign = "center";
      context.fillText(
        waveformError
          ? "波形生成失败"
          : hasVideo
            ? "正在生成波形"
            : "添加视频后显示波形",
        Math.min(width / 2, 450),
        center - 6,
      );
      context.textAlign = "start";
    }
    }
    hitsRef.current = hits;
  }, [
    durationMs,
    hasVideo,
    height,
    selectedId,
    timedUnits,
    waveform,
    waveformError,
    width,
    zoom,
    lineRubyGroups,
    lines,
  ]);

  function point(event: React.PointerEvent<HTMLCanvasElement>) {
    const rect = event.currentTarget.getBoundingClientRect();
    const offset = Number(event.currentTarget.dataset.timelineOffset || 0);
    return { x: event.clientX - rect.left + offset, y: event.clientY - rect.top };
  }
  function hitAt(x: number, y: number) {
    return [...hitsRef.current]
      .reverse()
      .find((hit) => x >= hit.x1 - 4 && x <= hit.x2 + 4 && y >= hit.y1 - 4 && y <= hit.y2 + 4);
  }
  function pointerDown(event: React.PointerEvent<HTMLCanvasElement>) {
    const { x, y } = point(event);
    const rubyEdge = rubyAdjustEnabled && rubyGroupsRef.current.find((group) => y >= group.y1 - 8 && y <= group.y2 + 8 && (Math.abs(x - group.x1) <= 8 || Math.abs(x - group.x2) <= 8));
    if (rubyEdge) {
      event.currentTarget.setPointerCapture(event.pointerId);
      dragRef.current = {
        hit: { unit: rubyEdge.units[rubyEdge.startIndex], lineId: rubyEdge.lineId, lineLevel: false, x1: rubyEdge.x1, x2: rubyEdge.x2, y1: rubyEdge.y1, y2: rubyEdge.y2 },
        mode: Math.abs(x - rubyEdge.x1) <= 8 ? "ruby-start" : "ruby-end",
        clientX: event.clientX,
        start: rubyEdge.start,
        end: rubyEdge.end,
        previewStart: rubyEdge.start,
        previewEnd: rubyEdge.end,
        began: false,
        rubyGroup: rubyEdge,
        previewRubyStart: rubyEdge.startIndex,
        previewRubyEnd: rubyEdge.endIndex,
      };
      return;
    }
    const hit = hitAt(x, y);
    if (!hit || hit.unit.start_ms === null || hit.unit.end_ms === null) {
      onSelect(null);
      onSeek(Math.max(0, Math.min(durationMs, (x / zoom) * 1000)));
      return;
    }
    onSelect(hit.unit.id);
    event.currentTarget.setPointerCapture(event.pointerId);
    const edge = 7;
    dragRef.current = {
      hit,
      mode:
        Math.abs(x - hit.x1) <= edge
          ? "start"
          : Math.abs(x - hit.x2) <= edge
            ? "end"
            : "move",
      clientX: event.clientX,
      start: hit.unit.start_ms,
      end: hit.unit.end_ms,
      previewStart: hit.unit.start_ms,
      previewEnd: hit.unit.end_ms,
      began: false,
    };
  }
  function showDragPreview(drag: Drag, start: number, end: number) {
    const preview = dragPreviewRef.current;
    if (!preview) return;
    preview.hidden = false;
    preview.textContent = drag.mode.startsWith("ruby-") ? drag.rubyGroup?.ruby || "Ruby" : drag.hit.unit.surface;
    preview.style.width = `${Math.max(5, ((end - start) / 1000) * zoom)}px`;
    preview.style.height = `${drag.hit.y2 - drag.hit.y1}px`;
    preview.style.transform = `translate3d(${(start / 1000) * zoom + 1}px,${drag.hit.y1}px,0)`;
  }
  function hideDragPreview() {
    if (dragPreviewRef.current) dragPreviewRef.current.hidden = true;
  }
  function pointerMove(event: React.PointerEvent<HTMLCanvasElement>) {
    const drag = dragRef.current;
    if (!drag) return;
    const delta =
      Math.round(((event.clientX - drag.clientX) / zoom) * 100) * 10;
    if (!delta) return;
    drag.began = true;
    if (drag.mode.startsWith("ruby-") && drag.rubyGroup) {
      const line = lines.find((candidate) => candidate.id === drag.rubyGroup?.lineId);
      if (!line) return;
      const x = point(event).x;
      const nearest = (mode: "start" | "end") => {
        let best = mode === "start" ? drag.rubyGroup!.startIndex : drag.rubyGroup!.endIndex - 1;
        let distance = Infinity;
        line.units.forEach((unit, index) => {
          const value = mode === "start" ? unit.start_ms : unit.end_ms;
          if (value === null) return;
          const nextDistance = Math.abs((value / 1000) * zoom - x);
          if (nextDistance < distance) { distance = nextDistance; best = index; }
        });
        return best;
      };
      if (drag.mode === "ruby-start") drag.previewRubyStart = Math.min(nearest("start"), (drag.previewRubyEnd ?? drag.rubyGroup.endIndex) - 1);
      else drag.previewRubyEnd = Math.max(nearest("end") + 1, (drag.previewRubyStart ?? drag.rubyGroup.startIndex) + 1);
      drag.began = true;
      const startUnit = line.units[drag.previewRubyStart ?? drag.rubyGroup.startIndex];
      const endUnit = line.units[(drag.previewRubyEnd ?? drag.rubyGroup.endIndex) - 1];
      if (startUnit?.start_ms !== null && endUnit?.end_ms !== null) showDragPreview(drag, startUnit.start_ms, endUnit.end_ms);
      return;
    }
    let start = drag.start;
    let end = drag.end;
    if (drag.mode === "move") {
      const span = end - start;
      start = Math.max(0, Math.min(durationMs - span, start + delta));
      end = start + span;
    }
    if (drag.mode === "start")
      start = Math.max(0, Math.min(end - 20, start + delta));
    if (drag.mode === "end")
      end = Math.min(durationMs, Math.max(start + 20, end + delta));
    drag.previewStart = start;
    drag.previewEnd = end;
    showDragPreview(drag, start, end);
  }
  function finishPointerDrag(
    event: React.PointerEvent<HTMLCanvasElement>,
    commit: boolean,
  ) {
    const drag = dragRef.current;
    if (drag && event.currentTarget.hasPointerCapture(event.pointerId))
      event.currentTarget.releasePointerCapture(event.pointerId);
    hideDragPreview();
    dragRef.current = null;
    if (!drag?.began || !commit) return;
    if (drag.mode.startsWith("ruby-") && drag.rubyGroup) {
      const line = lines.find((candidate) => candidate.id === drag.rubyGroup?.lineId);
      const startIndex = drag.previewRubyStart ?? drag.rubyGroup.startIndex;
      const endIndex = drag.previewRubyEnd ?? drag.rubyGroup.endIndex;
      if (line && endIndex > startIndex) {
        onBeginEdit();
        const selectedIds = line.units.slice(startIndex, endIndex).map((unit) => unit.id);
        const oldIds = line.units.slice(drag.rubyGroup.startIndex, drag.rubyGroup.endIndex).map((unit) => unit.id);
        const clearIds = oldIds.filter((id) => !selectedIds.includes(id));
        onUpdateRubyGroup(line.id, selectedIds, drag.rubyGroup.ruby, endIndex - startIndex, clearIds);
      }
      return;
    }
    onBeginEdit();
    if (drag.hit.lineLevel) {
      onUpdateLine(drag.hit.lineId, drag.previewStart, drag.previewEnd);
      return;
    }
    onUpdateUnit(drag.hit.lineId, drag.hit.unit.id, {
      start_ms: drag.previewStart,
      end_ms: drag.previewEnd,
      timing_source: "manual",
      timing_confidence: 1,
    });
  }
  function doubleClick(event: React.MouseEvent<HTMLCanvasElement>) {
    const { x, y } = point(
      event as unknown as React.PointerEvent<HTMLCanvasElement>,
    );
    const hit = hitAt(x, y);
    if (hit && !hit.lineLevel) {
      onSelect(hit.unit.id);
      onOpenEditor(hit.unit.id);
    }
  }

  function dragPosition(clientX: number) {
    const content = contentRef.current;
    if (!content) return null;
    return Math.max(0, Math.min(width, clientX - content.getBoundingClientRect().left));
  }
  function showDropMarker(clientX: number) {
    const x = dragPosition(clientX);
    if (x === null || !dropMarkerRef.current) return;
    dropMarkerRef.current.hidden = false;
    dropMarkerRef.current.style.transform = `translate3d(${x}px,0,0)`;
  }
  function hideDropMarker() {
    if (dropMarkerRef.current) dropMarkerRef.current.hidden = true;
  }
  function dropLine(event: React.DragEvent<HTMLDivElement>) {
    event.preventDefault();
    hideDropMarker();
    const lineId = event.dataTransfer.getData(
      "application/x-nicokara-lyric-line",
    ) || event.dataTransfer.getData("text/plain");
    const x = dragPosition(event.clientX);
    if (!lineId || x === null) return;
    const startMs = Math.round(((x / zoom) * 1000) / 10) * 10;
    onDropLine(lineId, Math.min(durationMs, startMs));
  }

  return (
    <section className="timeline-workspace" aria-label="歌词时间轴">
      <div className="timeline-toolbar">
        <div className="timeline-title">
          <strong>时间轴</strong>
          <span>{timedUnits.length} 个时间单元</span>
          <button
            type="button"
            className={`button timeline-ruby-toggle ${rubyAdjustEnabled ? "filled" : "tonal"}`}
            aria-pressed={rubyAdjustEnabled}
            title={rubyAdjustEnabled ? "关闭 Ruby 范围调整，恢复 unit 时间拖动" : "启用后拖动橙色括号调整 Ruby 范围"}
            onClick={() => setRubyAdjustEnabled((value) => !value)}
          >
            Ruby调整
          </button>
        </div>
        <div className="transport-controls">
          <button
            className="icon-button compact"
            title="后退 5 秒"
            disabled={!hasVideo}
            onClick={() => onSeekBy(-5000)}
          >
            <Rewind size={17} />
          </button>
          <button
            className="icon-button transport-primary"
            title={isPlaying ? "暂停" : "播放"}
            disabled={!hasVideo}
            onClick={onTogglePlayback}
          >
            {isPlaying ? (
              <Pause size={19} fill="currentColor" />
            ) : (
              <Play size={19} fill="currentColor" />
            )}
          </button>
          <button
            className="icon-button compact"
            title="前进 5 秒"
            disabled={!hasVideo}
            onClick={() => onSeekBy(5000)}
          >
            <FastForward size={17} />
          </button>
          <output ref={timecodeRef} className="timeline-timecode">
            00:00.000
          </output>
        </div>
        <div className="zoom-control">
          <button
            className="icon-button compact"
            title="缩小"
            onClick={() => changeZoom(zoom - 10)}
          >
            <Minus size={16} />
          </button>
          <ZoomIn size={16} />
          <input
            aria-label="时间轴缩放"
            type="range"
            min="30"
            max="180"
            step="10"
            value={zoom}
            onChange={(event) => changeZoom(Number(event.target.value))}
          />
          <button
            className="icon-button compact"
            title="放大"
            onClick={() => changeZoom(zoom + 10)}
          >
            <Plus size={16} />
          </button>
        </div>
      </div>
      <div
        className="timeline-scroller"
        ref={scrollerRef}
        onDragOver={(event) => {
          if (!event.dataTransfer.types.some((type) =>
            ["application/x-nicokara-lyric-line", "text/plain"].includes(type),
          ))
            return;
          event.preventDefault();
          event.dataTransfer.dropEffect = "move";
          showDropMarker(event.clientX);
        }}
        onDragLeave={(event) => {
          if (!event.currentTarget.contains(event.relatedTarget as Node | null))
            hideDropMarker();
        }}
        onDrop={dropLine}
      >
        <div className="timeline-content" ref={contentRef} style={{ width, height }}>
          {Array.from({ length: tileCount }, (_, tileIndex) => {
            const offset = tileIndex * CANVAS_TILE_WIDTH;
            const tileWidth = Math.min(CANVAS_TILE_WIDTH, width - offset);
            return <canvas
              key={offset}
              ref={(element) => { canvasRefs.current[tileIndex] = element; }}
              className="timeline-canvas-tile"
              data-timeline-offset={offset}
              style={{ left: `${offset}px`, width: `${tileWidth}px`, height: `${height}px` }}
              onPointerDown={pointerDown}
              onPointerMove={pointerMove}
              onPointerUp={(event) => finishPointerDrag(event, true)}
              onPointerCancel={(event) => finishPointerDrag(event, false)}
              onDoubleClick={doubleClick}
            />;
          })}
          <div
            ref={dragPreviewRef}
            className="timeline-drag-preview"
            hidden
            aria-hidden="true"
          />
          <div
            ref={playheadRef}
            className="timeline-playhead"
            aria-hidden="true"
          >
            <i />
          </div>
          <div
            ref={dropMarkerRef}
            className="timeline-drop-marker"
            hidden
            aria-hidden="true"
          />
        </div>
      </div>
    </section>
  );
}
