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
  x1: number;
  x2: number;
  y1: number;
  y2: number;
};
type Drag = {
  hit: Hit;
  mode: "move" | "start" | "end";
  clientX: number;
  start: number;
  end: number;
  previewStart: number;
  previewEnd: number;
  began: boolean;
};

const MAX_CANVAS_DIMENSION = 32760;

export function TimelineCanvas({
  projectId,
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
  onOpenEditor,
  onDropLine,
}: {
  projectId: string;
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
  onOpenEditor: (id: string) => void;
  onDropLine: (lineId: string, startMs: number) => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const scrollerRef = useRef<HTMLDivElement>(null);
  const playheadRef = useRef<HTMLDivElement>(null);
  const dragPreviewRef = useRef<HTMLDivElement>(null);
  const dropMarkerRef = useRef<HTMLDivElement>(null);
  const timecodeRef = useRef<HTMLOutputElement>(null);
  const hitsRef = useRef<Hit[]>([]);
  const dragRef = useRef<Drag | null>(null);
  const [waveform, setWaveform] = useState<Waveform | null>(null);
  const [waveformError, setWaveformError] = useState(false);
  const [zoom, setZoom] = useState(80);
  const width = Math.max(900, Math.ceil((durationMs / 1000) * zoom));
  const height = 260;
  const timedUnits = useMemo(
    () =>
      lines
        .flatMap((line) => line.units.map((unit) => ({ line, unit })))
        .filter(({ unit }) => unit.start_ms !== null && unit.end_ms !== null),
    [lines],
  );

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
  }, [hasVideo, projectId]);

  useEffect(() => {
    let frame = 0;
    const update = () => {
      const currentMs = (mediaRef.current?.currentTime || 0) * 1000;
      if (playheadRef.current)
        playheadRef.current.style.transform = `translate3d(${(currentMs / 1000) * zoom}px,0,0)`;
      if (timecodeRef.current)
        timecodeRef.current.value = formatTime(currentMs);
      frame = requestAnimationFrame(update);
    };
    frame = requestAnimationFrame(update);
    return () => cancelAnimationFrame(frame);
  }, [mediaRef, zoom]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ratio = Math.min(
      window.devicePixelRatio || 1,
      MAX_CANVAS_DIMENSION / width,
      MAX_CANVAS_DIMENSION / height,
    );
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    const context = canvas.getContext("2d");
    if (!context) return;
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.clearRect(0, 0, width, height);
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

    const hits: Hit[] = [];
    for (const { line, unit } of timedUnits) {
      const start = unit.start_ms as number;
      const end = unit.end_ms as number;
      const x1 = (start / 1000) * zoom;
      const x2 = Math.max(x1 + 5, (end / 1000) * zoom);
      const lane = line.order % 2;
      const y1 = 40 + lane * 43;
      const y2 = y1 + 35;
      const selected = unit.id === selectedId;
      context.fillStyle = selected
        ? "#0b57d0"
        : unit.timing_source === "estimated"
          ? "#fef3c7"
          : "#d3e3fd";
      context.strokeStyle = selected
        ? "#0842a0"
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
      hits.push({ unit, lineId: line.id, x1, x2, y1, y2 });
    }
    hitsRef.current = hits;

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
  ]);

  function point(event: React.PointerEvent<HTMLCanvasElement>) {
    const rect = event.currentTarget.getBoundingClientRect();
    return { x: event.clientX - rect.left, y: event.clientY - rect.top };
  }
  function hitAt(x: number, y: number) {
    return [...hitsRef.current]
      .reverse()
      .find((hit) => x >= hit.x1 && x <= hit.x2 && y >= hit.y1 && y <= hit.y2);
  }
  function pointerDown(event: React.PointerEvent<HTMLCanvasElement>) {
    const { x, y } = point(event);
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
    preview.textContent = drag.hit.unit.surface;
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
    onBeginEdit();
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
    if (hit) {
      onSelect(hit.unit.id);
      onOpenEditor(hit.unit.id);
    }
  }

  function dragPosition(clientX: number) {
    const canvas = canvasRef.current;
    if (!canvas) return null;
    return Math.max(0, Math.min(width, clientX - canvas.getBoundingClientRect().left));
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
            onClick={() => setZoom(Math.max(30, zoom - 10))}
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
            onChange={(event) => setZoom(Number(event.target.value))}
          />
          <button
            className="icon-button compact"
            title="放大"
            onClick={() => setZoom(Math.min(180, zoom + 10))}
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
        <div className="timeline-content" style={{ width, height }}>
          <canvas
            ref={canvasRef}
            onPointerDown={pointerDown}
            onPointerMove={pointerMove}
            onPointerUp={(event) => finishPointerDrag(event, true)}
            onPointerCancel={(event) => finishPointerDrag(event, false)}
            onDoubleClick={doubleClick}
          />
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
