import {
  AlertCircle,
  AudioLines,
  ArrowLeft,
  Check,
  Cog,
  Download,
  FileText,
  FileVideo,
  GripVertical,
  LoaderCircle,
  RotateCcw,
  Redo2,
  Save,
  Scissors,
  Square,
  Trash2,
  Undo2,
  Upload,
  WandSparkles,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type {
  AnalysisJob,
  LyricLine,
  LyricUnit,
  Project,
  ProjectDocument,
  ProjectLanguage,
} from "./editor-types";
import { api, formatTime, parseTime } from "./editor-types";
import { LyricsImportDialog } from "./lyrics-import-dialog";
import { RubyRangeEditor } from "./ruby-range-editor";
import { lineCharacterCount, rubyRanges, unitCharacterRange, updateRubyRange } from "./ruby-range";
import { TimelineCanvas } from "./timeline-canvas";
import { SubtitleDomRenderer } from "./subtitle/dom-renderer";
import { normalizeSubtitleStyle, type SubtitleStyle } from "./subtitle/style-schema";
import { SubtitleStylePanel, type StylePreset } from "./subtitle/style-panel";
import "./editor.css";

function settingsHref() {
  return `#/settings?returnTo=${encodeURIComponent(location.hash.slice(1) || "/projects")}`;
}
function clone<T>(value: T): T {
  return structuredClone(value);
}

function createClientId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  if (typeof crypto !== "undefined" && typeof crypto.getRandomValues === "function") {
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = Array.from(bytes, (value) => value.toString(16).padStart(2, "0"));
    return `${hex.slice(0, 4).join("")}-${hex.slice(4, 6).join("")}-${hex.slice(6, 8).join("")}-${hex.slice(8, 10).join("")}-${hex.slice(10).join("")}`;
  }
  return `local-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}

function updateUnit(
  document: ProjectDocument,
  lineId: string,
  unitId: string,
  patch: Partial<LyricUnit>,
): ProjectDocument {
  return {
    ...document,
    lyrics: {
      ...document.lyrics,
      lines: document.lyrics.lines.map((line) => {
        if (line.id !== lineId) return line;
        const units = line.units.map((unit) =>
          unit.id === unitId ? { ...unit, ...patch } : unit,
        );
        const timed = units.filter(
          (unit) => unit.start_ms !== null && unit.end_ms !== null,
        );
        return {
          ...line,
          units,
          start_ms: timed.length
            ? Math.min(...timed.map((unit) => unit.start_ms as number))
            : null,
          end_ms: timed.length
            ? Math.max(...timed.map((unit) => unit.end_ms as number))
            : null,
        };
      }),
    },
  };
}

function updateLineTiming(
  document: ProjectDocument,
  lineId: string,
  startMs: number,
  endMs: number,
): ProjectDocument {
  return {
    ...document,
    lyrics: {
      ...document.lyrics,
      lines: document.lyrics.lines.map((line) =>
        line.id === lineId
          ? {
              ...line,
              start_ms: startMs,
              end_ms: endMs,
              timing_source: "manual",
              timing_precision: "line",
            }
          : line,
      ),
    },
  };
}

function collapseLineTiming(document: ProjectDocument, lineId: string): ProjectDocument {
  return {
    ...document,
    lyrics: {
      ...document.lyrics,
      lines: document.lyrics.lines.map((line) => {
        if (line.id !== lineId) return line;
        const timed = line.units.filter((unit) => unit.start_ms !== null && unit.end_ms !== null);
        return {
          ...line,
          start_ms: line.start_ms ?? (timed.length ? Math.min(...timed.map((unit) => unit.start_ms as number)) : null),
          end_ms: line.end_ms ?? (timed.length ? Math.max(...timed.map((unit) => unit.end_ms as number)) : null),
          timing_source: "manual",
          timing_precision: "line",
        };
      }),
    },
  };
}

function updateRubyGroup(
  document: ProjectDocument,
  lineId: string,
  selectedIds: string[],
  ruby: string,
  ruby2: string,
  rubySpan: number,
  clearIds: string[],
): ProjectDocument {
  const selected = new Set(selectedIds);
  const cleared = new Set(clearIds);
  const firstId = selectedIds[0];
  return {
    ...document,
    lyrics: {
      ...document.lyrics,
      lines: document.lyrics.lines.map((line) => {
        if (line.id !== lineId) return line;
        return {
          ...line,
          units: line.units.map((unit) => {
            if (unit.id === firstId) return { ...unit, ruby, ruby_2: ruby2 || null, ruby_span: rubySpan, ruby_source: "manual" };
            if (selected.has(unit.id)) return { ...unit, ruby: null, ruby_2: null, ruby_span: undefined, ruby_source: "manual" };
            if (cleared.has(unit.id)) return { ...unit, ruby: null, ruby_2: null, ruby_span: undefined, ruby_source: "none" };
            return unit;
          }),
        };
      }),
    },
  };
}

function updateProjectLanguage(document: ProjectDocument, language: ProjectLanguage): ProjectDocument {
  const analysis = { ...((document as ProjectDocument & { analysis?: Record<string, unknown> }).analysis || {}) };
  delete analysis.pronunciation;
  delete analysis.global_alignment;
  delete analysis.alignment;
  delete analysis.fa_kara;
  return {
    ...document,
    project: { ...document.project, language },
    analysis,
    pronunciation: undefined,
    lyrics: {
      ...document.lyrics,
      lines: document.lyrics.lines.map((line) => ({
        ...line,
        units: line.units.map((unit) => ({
          ...unit,
          ruby: null,
          ruby_2: null,
          ruby_span: undefined,
          ruby_source: "none",
          alignment_reading: undefined,
        })),
      })),
    },
  } as ProjectDocument;
}

type MissingRubyReport = { unitIds: string[]; lines: { lineIndex: number; characters: string }[] };
type FullAnalysisResume = { request: Record<string, unknown>; steps: string[] };
type MissingRubyDialogState = MissingRubyReport & { resume?: FullAnalysisResume };
type RubyEditorTarget =
  | { kind: "line"; lineId: string }
  | { kind: "unit"; lineId: string; unitId: string; start: number; end: number };

function missingJapaneseRuby(document: ProjectDocument): MissingRubyReport {
  const unitIds: string[] = [];
  const lines: { lineIndex: number; characters: string }[] = [];
  document.lyrics.lines.forEach((line, lineIndex) => {
    const missingCharacters: string[] = [];
    let index = 0;
    while (index < line.units.length) {
      const unit = line.units[index];
      const surface = unit.surface || "";
      if (unit.ruby?.trim()) {
        let covered = Array.from(surface).length;
        let memberEnd = index + 1;
        const span = Math.max(1, Number(unit.ruby_span || 1));
        while (covered < span && memberEnd < line.units.length) {
          covered += Array.from(line.units[memberEnd].surface || "").length;
          memberEnd += 1;
        }
        index = memberEnd;
        continue;
      }
      const kanji = Array.from(surface).filter((character) => /[一-龯々]/.test(character)).join("");
      if (kanji) {
        missingCharacters.push(...Array.from(kanji));
        unitIds.push(unit.id);
      }
      index += 1;
    }
    if (missingCharacters.length) {
      lines.push({ lineIndex, characters: Array.from(new Set(missingCharacters)).join("、") });
    }
  });
  return { unitIds, lines };
}

function placeLineAt(
  document: ProjectDocument,
  lineId: string,
  requestedStartMs: number,
  timelineDurationMs: number,
): ProjectDocument {
  return {
    ...document,
    lyrics: {
      ...document.lyrics,
      lines: document.lyrics.lines.map((line) => {
        if (line.id !== lineId || !line.units.length) return line;
        const fullyTimed = line.units.every(
          (unit) => unit.start_ms !== null && unit.end_ms !== null,
        );
        const originalStart = fullyTimed
          ? Math.min(...line.units.map((unit) => unit.start_ms as number))
          : 0;
        const originalEnd = fullyTimed
          ? Math.max(...line.units.map((unit) => unit.end_ms as number))
          : 3000;
        const span = Math.max(200, originalEnd - originalStart);
        const startMs = Math.max(
          0,
          Math.min(timelineDurationMs - Math.min(span, timelineDurationMs), requestedStartMs),
        );
        const endMs = Math.min(timelineDurationMs, startMs + span);
        const placedSpan = endMs - startMs;
        const units = line.units.map((unit, index) => {
          const relativeStart = fullyTimed
            ? ((unit.start_ms as number) - originalStart) / span
            : index / line.units.length;
          const relativeEnd = fullyTimed
            ? ((unit.end_ms as number) - originalStart) / span
            : (index + 1) / line.units.length;
          return {
            ...unit,
            start_ms: Math.round(startMs + placedSpan * relativeStart),
            end_ms: Math.round(startMs + placedSpan * relativeEnd),
            timing_source: fullyTimed || line.units.length === 1 ? "manual" : "estimated",
            timing_confidence: fullyTimed || line.units.length === 1 ? 1 : null,
          };
        });
        return {
          ...line,
          units,
          start_ms: units[0].start_ms,
          end_ms: units[units.length - 1].end_ms,
          timing_source: "manual",
          timing_precision: line.units.length === 1 ? "line" : line.timing_precision,
        };
      }),
    },
  };
}

type UnitSplitRange = { start: number; end: number };

function mergeSplitRange(ranges: UnitSplitRange[], start: number, end: number): UnitSplitRange[] {
  const next: UnitSplitRange[] = [];
  let inserted = false;
  for (const range of ranges) {
    if (range.end <= start || range.start >= end) {
      next.push(range);
      continue;
    }
    if (range.start < start) next.push({ start: range.start, end: start });
    if (!inserted) {
      next.push({ start, end });
      inserted = true;
    }
    if (range.end > end) next.push({ start: end, end: range.end });
  }
  return next.sort((left, right) => left.start - right.start);
}

function splitLyricUnit(
  document: ProjectDocument,
  lineId: string,
  unitId: string,
  ranges: UnitSplitRange[],
): ProjectDocument {
  return {
    ...document,
    lyrics: {
      ...document.lyrics,
      lines: document.lyrics.lines.map((line) => {
        if (line.id !== lineId) return line;
        const original = line.units.find((unit) => unit.id === unitId);
        if (!original) return line;
        const characters = Array.from(original.surface);
        const duration = original.start_ms !== null && original.end_ms !== null
          ? original.end_ms - original.start_ms
          : null;
        const splitUnits = ranges.map((range, index): LyricUnit => {
          const first = index === 0;
          const hasRuby = Boolean(original.ruby || original.ruby_2);
          const unit: LyricUnit = {
            ...original,
            id: first ? original.id : createClientId(),
            surface: characters.slice(range.start, range.end).join(""),
            start_ms: duration === null ? null : Math.round((original.start_ms as number) + duration * range.start / characters.length),
            end_ms: duration === null ? null : Math.round((original.start_ms as number) + duration * range.end / characters.length),
            timing_source: ranges.length > 1 ? "estimated" : original.timing_source,
            timing_confidence: ranges.length > 1 ? null : original.timing_confidence,
            ruby: first ? original.ruby : null,
            ruby_2: first ? original.ruby_2 : null,
            ruby_source: first ? original.ruby_source : "none",
          };
          delete unit.alignment_reading;
          if (first && hasRuby) unit.ruby_span = Math.max(Number(original.ruby_span || 1), characters.length);
          else if (!first) unit.ruby_span = undefined;
          return unit;
        });
        const units = line.units.flatMap((unit) => unit.id === unitId ? splitUnits : [unit]);
        return {
          ...line,
          units,
          timing_precision: ranges.length > 1 ? "phrase" : line.timing_precision,
        };
      }),
    },
  };
}

function UnitSplitDialog({ unit, onClose, onConfirm }: {
  unit: LyricUnit;
  onClose: () => void;
  onConfirm: (ranges: UnitSplitRange[]) => void;
}) {
  const characters = useMemo(() => Array.from(unit.surface), [unit.surface]);
  const [ranges, setRanges] = useState<UnitSplitRange[]>(() => characters.map((_, index) => ({ start: index, end: index + 1 })));
  const [history, setHistory] = useState<UnitSplitRange[][]>([]);
  const [selection, setSelection] = useState<UnitSplitRange | null>(null);
  const [confirmError, setConfirmError] = useState<string | null>(null);
  const dragAnchorRef = useRef<number | null>(null);
  const selectedRange = selection
    ? { start: Math.min(selection.start, selection.end), end: Math.max(selection.start, selection.end) + 1 }
    : null;
  const alreadyGrouped = selectedRange
    ? ranges.some((range) => range.start === selectedRange.start && range.end === selectedRange.end)
    : false;

  function characterIndexAt(clientX: number, clientY: number): number | null {
    const element = document.elementFromPoint(clientX, clientY)?.closest<HTMLElement>("[data-split-index]");
    if (!element) return null;
    const index = Number(element.dataset.splitIndex);
    return Number.isInteger(index) ? index : null;
  }
  function groupSelection() {
    if (!selectedRange || selectedRange.end - selectedRange.start < 2 || alreadyGrouped) return;
    setHistory((current) => [...current, ranges]);
    setRanges(mergeSplitRange(ranges, selectedRange.start, selectedRange.end));
    setSelection(null);
  }
  function undoLocal() {
    const previous = history[history.length - 1];
    if (!previous) return;
    setRanges(previous);
    setHistory((current) => current.slice(0, -1));
    setSelection(null);
  }
  function confirmSplit() {
    setConfirmError(null);
    try {
      onConfirm(ranges);
    } catch (reason) {
      setConfirmError(reason instanceof Error ? reason.message : "无法应用拆分，请重试。");
    }
  }

  return <div className="scrim">
    <section className="dialog unit-split-dialog" role="dialog" aria-modal="true" aria-labelledby="unit-split-title">
      <div className="dialog-head">
        <div><h2 id="unit-split-title">拆分 Unit</h2><p className="muted">{unit.surface}</p></div>
        <button className="icon-button" type="button" title="撤回上一次拆分" aria-label="撤回上一次拆分" disabled={!history.length} onClick={undoLocal}><Undo2 size={19} /></button>
        <button className="icon-button" type="button" title="关闭" onClick={onClose}><X size={19} /></button>
      </div>
      <div
        className="unit-split-grid"
        onPointerDown={(event) => {
          const index = characterIndexAt(event.clientX, event.clientY);
          if (index === null) return;
          event.currentTarget.setPointerCapture(event.pointerId);
          dragAnchorRef.current = index;
          setSelection({ start: index, end: index });
        }}
        onPointerMove={(event) => {
          if (dragAnchorRef.current === null) return;
          const index = characterIndexAt(event.clientX, event.clientY);
          if (index !== null) setSelection({ start: dragAnchorRef.current, end: index });
        }}
        onPointerUp={(event) => {
          if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
          dragAnchorRef.current = null;
        }}
        onPointerCancel={() => { dragAnchorRef.current = null; }}
      >
        {ranges.map((range, groupIndex) => <span className={`unit-split-group ${range.end - range.start > 1 ? "combined" : ""}`} key={`${range.start}-${range.end}`}>
          {characters.slice(range.start, range.end).map((character, offset) => {
            const index = range.start + offset;
            const selected = selectedRange && index >= selectedRange.start && index < selectedRange.end;
            return <button type="button" tabIndex={-1} className={`unit-split-character ${selected ? "selected" : ""}`} data-split-index={index} key={`${groupIndex}-${index}`}>{character}</button>;
          })}
        </span>)}
      </div>
      <div className="unit-split-summary"><span>{selectedRange ? `已选 ${selectedRange.end - selectedRange.start} 字` : "未选择"}</span><strong>{ranges.length} Units</strong></div>
      {confirmError && <div className="unit-split-error" role="alert"><AlertCircle size={16} />{confirmError}</div>}
      <div className="dialog-actions unit-split-actions">
        <button className="button tonal" type="button" disabled={!selectedRange || selectedRange.end - selectedRange.start < 2 || alreadyGrouped} onClick={groupSelection}><Scissors size={17} />拆分</button>
        <button className="button filled" type="button" onClick={confirmSplit}><Check size={17} />确定</button>
        <button className="button text" type="button" onClick={onClose}>取消</button>
      </div>
    </section>
  </div>;
}

function UnitEditDialog({
  unit,
  rubyEnabled,
  onClose,
  onSave,
}: {
  unit: LyricUnit;
  rubyEnabled: boolean;
  onClose: () => void;
  onSave: (patch: Partial<LyricUnit>) => void;
}) {
  const [surface, setSurface] = useState(unit.surface);
  const [ruby, setRuby] = useState(unit.ruby || "");
  const [ruby2, setRuby2] = useState(unit.ruby_2 || "");
  const [start, setStart] = useState(formatTime(unit.start_ms));
  const [end, setEnd] = useState(formatTime(unit.end_ms));
  const startMs = parseTime(start);
  const endMs = parseTime(end);
  const timingValid = startMs !== null && endMs !== null && endMs > startMs;
  return (
    <div className="scrim">
      <section className="dialog unit-dialog" role="dialog" aria-modal="true">
        <div className="dialog-head">
          <h2>编辑歌词单元</h2>
          <button className="icon-button" title="关闭" onClick={onClose}>
            <X size={20} />
          </button>
        </div>
        <label className="field-label">
          正文
          <input
            autoFocus
            value={surface}
            onChange={(event) => setSurface(event.target.value)}
          />
        </label>
        {rubyEnabled && <label className="field-label">
          Ruby
          <input
            value={ruby}
            onChange={(event) => setRuby(event.target.value)}
          />
        </label>}
        {rubyEnabled && <label className="field-label">
          第二 Ruby
          <input
            value={ruby2}
            onChange={(event) => setRuby2(event.target.value)}
          />
        </label>}
        <div className="time-fields">
          <label className="field-label">
            开始
            <input
              inputMode="decimal"
              value={start}
              onChange={(event) => setStart(event.target.value)}
            />
          </label>
          <label className="field-label">
            结束
            <input
              inputMode="decimal"
              value={end}
              onChange={(event) => setEnd(event.target.value)}
            />
          </label>
        </div>
        <div className="dialog-actions">
          <button className="button text" onClick={onClose}>
            取消
          </button>
          <button
            className="button filled"
            disabled={!surface || !timingValid}
            onClick={() =>
              onSave({
                surface,
                ruby: rubyEnabled && ruby ? ruby : null,
                ruby_2: rubyEnabled && ruby2 ? ruby2 : null,
                ruby_source: rubyEnabled && (ruby || ruby2) ? "manual" : "none",
                alignment_reading: undefined,
                start_ms: startMs,
                end_ms: endMs,
                timing_source: "manual",
                timing_confidence: 1,
              })
            }
          >
            <Check size={17} />
            应用
          </button>
        </div>
      </section>
    </div>
  );
}

function ExportDialog({
  hasVideo,
  jobs,
  onClose,
  onStart,
  onCancel,
  onDelete,
}: {
  hasVideo: boolean;
  jobs: AnalysisJob[];
  onClose: () => void;
  onStart: (payload: { format: "mp4" | "webm" | "krl"; audio_track: "on_vocal" | "off_vocal" }) => void;
  onCancel: (jobId: string) => void;
  onDelete: (jobId: string) => void;
}) {
  const [format, setFormat] = useState<"mp4" | "webm" | "krl">("mp4");
  const [audioTrack, setAudioTrack] = useState<"on_vocal" | "off_vocal">("on_vocal");
  const exportJobs = jobs.filter((job) => job.type === "EXPORT");
  const active = exportJobs.find((job) => ["QUEUED", "PREPARING", "RUNNING"].includes(job.status));
  return <div className="scrim">
    <section className="dialog export-dialog" role="dialog" aria-modal="true" aria-labelledby="export-title">
      <div className="dialog-head"><div><h2 id="export-title">导出工程</h2><p className="muted">视频使用 Kirakara 原生渲染；KRL 保留时间、Ruby、角色与当前字幕样式。</p></div><button className="icon-button" title="关闭" onClick={onClose}><X size={18} /></button></div>
      <div className="export-form">
        <label className="field-label">输出格式<select value={format} onChange={(event) => setFormat(event.target.value as typeof format)}><option value="mp4">MP4 · H.264 视频</option><option value="webm">WebM · VP9 视频</option><option value="krl">KRL · Kirakara 工程</option></select></label>
        {format !== "krl" && <label className="field-label">音轨<select value={audioTrack} onChange={(event) => setAudioTrack(event.target.value as typeof audioTrack)}><option value="on_vocal">ON VOCAL · 原始音轨</option><option value="off_vocal">OFF VOCAL · 导出时生成人声分离伴奏</option></select></label>}
        {!hasVideo && format !== "krl" && <p className="form-hint error-text">视频导出需要先上传视频。</p>}
      </div>
      <div className="dialog-actions"><button className="button text" onClick={onClose}>关闭</button><button className="button filled" disabled={Boolean(active) || (format !== "krl" && !hasVideo)} onClick={() => onStart({ format, audio_track: audioTrack })}><Download size={17} />开始导出</button></div>
      {active && <div className="export-active"><LoaderCircle className="spin" size={17} /><span>{active.message || "服务端导出中"} · {Math.round(active.progress * 100)}%</span><div className="analysis-progress export-progress"><i style={{ width: `${Math.max(2, active.progress * 100)}%` }} /></div><button className="icon-button compact" title="取消导出" onClick={() => onCancel(active.id)}><Square size={15} /></button></div>}
      <div className="export-history"><h3>导出历史</h3>{exportJobs.filter((job) => job.status === "SUCCEEDED").length === 0 ? <p className="muted">暂无可下载文件</p> : exportJobs.filter((job) => job.status === "SUCCEEDED").map((job) => { const jobFormat = String(job.request?.format || "mp4"); return <div className="export-history-row" key={job.id}><span><strong>{String(job.result?.filename || "导出文件")}</strong><small>{jobFormat.toUpperCase()} · {jobFormat === "krl" ? "Kirakara 工程" : job.request?.audio_track === "off_vocal" ? "OFF VOCAL" : "ON VOCAL"} · {new Date(job.created_at).toLocaleString()}</small></span><a className="icon-button" title="下载" href={`/api/projects/${job.project_id}/exports/${job.id}/download`}><Download size={17} /></a><button className="icon-button" title="删除导出记录" onClick={() => onDelete(job.id)}><Trash2 size={17} /></button></div>; })}</div>
    </section>
  </div>;
}

export function EditorPage({ id }: { id: string }) {
  const [project, setProject] = useState<Project | null>(null);
  const [document, setDocument] = useState<ProjectDocument | null>(null);
  const projectRef = useRef<Project | null>(null),
    documentRef = useRef<ProjectDocument | null>(null);
  const dirtyRef = useRef(false),
    savePromiseRef = useRef<Promise<void> | null>(null);
  const historyRef = useRef<{
    past: ProjectDocument[];
    future: ProjectDocument[];
  }>({ past: [], future: [] });
  const [historyVersion, setHistoryVersion] = useState(0);
  const [dirty, setDirty] = useState(false),
    [saving, setSaving] = useState(false),
    [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null),
    [editingId, setEditingId] = useState<string | null>(null);
  const [rubyEditorTarget, setRubyEditorTarget] = useState<RubyEditorTarget | null>(null);
  const [importOpen, setImportOpen] = useState(false),
    [isPlaying, setIsPlaying] = useState(false);
  const [currentMs, setCurrentMs] = useState(0);
  const [uploading, setUploading] = useState(false),
    [uploadProgress, setUploadProgress] = useState(0);
  const [jobs, setJobs] = useState<AnalysisJob[]>([]),
    [analysisStarting, setAnalysisStarting] = useState(false);
  const [pronunciationRunning, setPronunciationRunning] = useState(false);
  const [fullAnalysisOpen, setFullAnalysisOpen] = useState(false);
  const [exportOpen, setExportOpen] = useState(false);
  const [workflowNotice, setWorkflowNotice] = useState<string | null>(null);
  const [pendingLanguage, setPendingLanguage] = useState<ProjectLanguage | null>(null);
  const [missingRubyDialog, setMissingRubyDialog] = useState<MissingRubyDialogState | null>(null);
  const [alignmentBackend, setAlignmentBackend] = useState<"fa_kara" | "stable_ts">("fa_kara");
  const [faKaraModel, setFaKaraModel] = useState<"mms" | "yohane">("mms");
  const [stylePresets, setStylePresets] = useState<StylePreset[]>([]);
  const [fullSteps, setFullSteps] = useState<Record<string, boolean>>({ separation: true, transcription: true, pronunciation: true, global_alignment: true, alignment: true });
  const [lineMenu, setLineMenu] = useState<{ lineId: string; unitId: string | null; x: number; y: number } | null>(null);
  const [splitTarget, setSplitTarget] = useState<{ lineId: string; unitId: string } | null>(null);
  const uploadRequest = useRef<XMLHttpRequest | null>(null),
    videoRef = useRef<HTMLVideoElement>(null);
  const appliedJobs = useRef(new Set<string>());
  const appliedVocalWaveforms = useRef(new Set<string>());
  const pendingFullResume = useRef<{ pronunciationJobId: string; resume: FullAnalysisResume } | null>(null);

  const subtitleStyle = useMemo(() => normalizeSubtitleStyle((document?.styles || {}) as Record<string, unknown>), [document?.styles]);
  useEffect(() => {
    void Promise.all([
      api<Project>(`/projects/${id}`),
      api<{ revision: number; document: ProjectDocument }>(
        `/projects/${id}/document`,
      ),
      api<Record<string, unknown>>("/settings"),
      api<StylePreset[]>("/settings/subtitle-style-presets"),
    ])
      .then(([loadedProject, loaded, settings, loadedStylePresets]) => {
        projectRef.current = loadedProject;
        documentRef.current = loaded.document;
        setProject(loadedProject);
        setDocument(loaded.document);
        setAlignmentBackend(settings.alignment_backend === "stable_ts" ? "stable_ts" : "fa_kara");
        setFaKaraModel(settings.fa_kara_model === "yohane" ? "yohane" : "mms");
        setStylePresets(loadedStylePresets);
      })
      .catch(() => setError("无法打开工程，请确认后端服务已启动。"));
  }, [id]);

  const reloadFromServer = useCallback(async (): Promise<boolean> => {
    if (dirtyRef.current) return false;
    const [loadedProject, loaded] = await Promise.all([
      api<Project>(`/projects/${id}`),
      api<{ revision: number; document: ProjectDocument }>(`/projects/${id}/document`),
    ]);
    projectRef.current = loadedProject;
    documentRef.current = loaded.document;
    setProject(loadedProject);
    setDocument(loaded.document);
    return true;
  }, [id]);

  useEffect(() => {
    let disposed = false;
    const poll = async () => {
      try {
        const loaded = await api<AnalysisJob[]>(`/jobs?project_id=${encodeURIComponent(id)}&limit=12`);
        if (disposed) return;
        setJobs(loaded);
        const terminal = loaded.find(
          (job) => ["SUCCEEDED", "FAILED", "CANCELED"].includes(job.status) && !appliedJobs.current.has(job.id),
        );
        const separated = loaded.find(
          (job) =>
            job.status === "RUNNING" &&
            ["TRANSCRIBING", "ALIGNING"].includes(job.stage) &&
            !appliedVocalWaveforms.current.has(job.id),
        );
        if (separated) {
          appliedVocalWaveforms.current.add(separated.id);
          await reloadFromServer();
        }
        if (terminal && await reloadFromServer()) {
          appliedJobs.current.add(terminal.id);
          const currentDocument = documentRef.current;
          const pending = pendingFullResume.current;
          if (pending?.pronunciationJobId === terminal.id) {
            pendingFullResume.current = null;
            if (terminal.status === "SUCCEEDED" && currentDocument) {
              const report = missingJapaneseRuby(currentDocument);
              if (report.lines.length) {
                setMissingRubyDialog({ ...report, resume: pending.resume });
              } else {
                try {
                  const revision = projectRef.current?.revision;
                  if (!revision) throw new Error("missing_revision");
                  const resumed = await api<AnalysisJob>(`/projects/${id}/analysis`, {
                    method: "POST",
                    body: JSON.stringify({ ...pending.resume.request, revision, steps: pending.resume.steps }),
                  });
                  setJobs((current) => [resumed, ...current.filter((item) => item.id !== resumed.id)]);
                } catch {
                  setError("本地注音已完成，但无法继续全曲分析，请重新打开全曲分析。 ");
                }
              }
            }
          } else if (terminal.type === "FULL_ANALYSIS" && terminal.error_code === "missing_ruby" && currentDocument) {
            const report = missingJapaneseRuby(currentDocument);
            const requestedSteps = Array.isArray(terminal.request?.steps) ? terminal.request.steps.filter((step): step is string => typeof step === "string") : [];
            const steps = requestedSteps.filter((step) => ["global_alignment", "alignment", "fa_kara"].includes(step));
            if (report.lines.length && steps.length) {
              setMissingRubyDialog({ ...report, resume: { request: terminal.request || {}, steps } });
            }
          }
        }
      } catch {
        // Project loading already reports connectivity failures.
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 1400);
    return () => {
      disposed = true;
      window.clearInterval(timer);
    };
  }, [id, reloadFromServer]);
  useEffect(() => {
    if (!lineMenu) return;
    const close = () => setLineMenu(null);
    addEventListener("pointerdown", close);
    return () => removeEventListener("pointerdown", close);
  }, [lineMenu]);

  const markDirty = useCallback((value: boolean) => {
    dirtyRef.current = value;
    setDirty(value);
  }, []);
  const replaceDocument = useCallback(
    (next: ProjectDocument, record = true) => {
      const current = documentRef.current;
      if (record && current) {
        historyRef.current.past.push(current);
        historyRef.current.past = historyRef.current.past.slice(-200);
        historyRef.current.future = [];
        setHistoryVersion((value) => value + 1);
      }
      documentRef.current = next;
      setDocument(next);
      markDirty(true);
    },
    [markDirty],
  );
  const beginEdit = useCallback(() => {
    const current = documentRef.current;
    if (!current) return;
    historyRef.current.past.push(current);
    historyRef.current.past = historyRef.current.past.slice(-200);
    historyRef.current.future = [];
    setHistoryVersion((value) => value + 1);
  }, []);

  const saveNow = useCallback(async (): Promise<number> => {
    if (savePromiseRef.current) {
      await savePromiseRef.current;
      if (dirtyRef.current) return saveNow();
      return projectRef.current?.revision || 0;
    }
    const snapshot = documentRef.current,
      activeProject = projectRef.current;
    if (!snapshot || !activeProject || !dirtyRef.current)
      return activeProject?.revision || 0;
    setSaving(true);
    markDirty(false);
    setError(null);
    const operation = api<{ revision: number }>(`/projects/${id}/document`, {
      method: "PUT",
      body: JSON.stringify({
        revision: activeProject.revision,
        document: snapshot,
      }),
    })
      .then((result) => {
        const nextProject = {
          ...activeProject,
          revision: result.revision,
          updated_at: new Date().toISOString(),
        };
        projectRef.current = nextProject;
        setProject(nextProject);
        if (documentRef.current !== snapshot) markDirty(true);
      })
      .catch((reason: Error & { status?: number }) => {
        markDirty(true);
        setError(
          reason.status === 409
            ? "工程已在其他窗口更新，当前修改尚未保存。"
            : "自动保存失败，请检查后端服务。 ",
        );
        throw reason;
      })
      .finally(() => {
        savePromiseRef.current = null;
        setSaving(false);
      });
    savePromiseRef.current = operation;
    await operation;
    return projectRef.current?.revision || 0;
  }, [id, markDirty]);

  useEffect(() => {
    if (!dirty) return;
    const timer = window.setTimeout(() => {
      void saveNow().catch(() => undefined);
    }, 900);
    return () => window.clearTimeout(timer);
  }, [dirty, document, saveNow]);
  useEffect(() => {
    const beforeUnload = (event: BeforeUnloadEvent) => {
      if (dirtyRef.current) event.preventDefault();
    };
    addEventListener("beforeunload", beforeUnload);
    return () => removeEventListener("beforeunload", beforeUnload);
  }, []);

  const undo = useCallback(() => {
    const previous = historyRef.current.past.pop(),
      current = documentRef.current;
    if (!previous || !current) return;
    historyRef.current.future.push(current);
    documentRef.current = previous;
    setDocument(previous);
    markDirty(true);
    setHistoryVersion((value) => value + 1);
  }, [markDirty]);
  const redo = useCallback(() => {
    const next = historyRef.current.future.pop(),
      current = documentRef.current;
    if (!next || !current) return;
    historyRef.current.past.push(current);
    documentRef.current = next;
    setDocument(next);
    markDirty(true);
    setHistoryVersion((value) => value + 1);
  }, [markDirty]);
  const togglePlayback = useCallback(() => {
    const video = videoRef.current;
    if (!video) return;
    if (video.paused) void video.play().catch(() => undefined);
    else video.pause();
  }, []);
  useEffect(() => {
    const keydown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (
        event.code === "Space" &&
        !event.repeat &&
        !target?.closest("input, textarea, select, [contenteditable='true']")
      ) {
        event.preventDefault();
        togglePlayback();
        return;
      }
      if (!(event.ctrlKey || event.metaKey)) return;
      if (event.key.toLowerCase() === "z") {
        event.preventDefault();
        event.shiftKey ? redo() : undo();
      } else if (event.key.toLowerCase() === "y") {
        event.preventDefault();
        redo();
      }
    };
    addEventListener("keydown", keydown);
    return () => removeEventListener("keydown", keydown);
  }, [redo, togglePlayback, undo]);

  function updateSelected(
    lineId: string,
    unitId: string,
    patch: Partial<LyricUnit>,
    record = false,
  ) {
    const current = documentRef.current;
    if (current)
      replaceDocument(updateUnit(current, lineId, unitId, patch), record);
  }
  function selectUnit(unitId: string | null, lineLevel = false) {
    setSelectedId(unitId);
    if (!unitId) {
      setRubyEditorTarget(null);
      return;
    }
    const line = documentRef.current?.lyrics.lines.find((candidate) => candidate.units.some((unit) => unit.id === unitId));
    if (!line) {
      setRubyEditorTarget(null);
      return;
    }
    if (lineLevel) {
      setRubyEditorTarget({ kind: "line", lineId: line.id });
      return;
    }
    const range = unitCharacterRange(line, unitId);
    setRubyEditorTarget(range ? { kind: "unit", lineId: line.id, unitId, ...range } : null);
  }
  function selectLine(line: LyricLine) {
    setSelectedId(line.units[0]?.id || null);
    setRubyEditorTarget({ kind: "line", lineId: line.id });
  }
  function updateSubtitleStyle(patch: Partial<SubtitleStyle>) {
    const current = documentRef.current;
    if (!current) return;
    replaceDocument({ ...current, styles: { ...(current.styles || {}), ...patch } }, true);
  }
  function changeLanguage(language: ProjectLanguage) {
    const current = documentRef.current;
    if (!current || (current.project.language || "jp") === language) return;
    setPendingLanguage(language);
  }
  function confirmLanguageChange() {
    const current = documentRef.current;
    if (!current || !pendingLanguage) return;
    replaceDocument(updateProjectLanguage(current, pendingLanguage), true);
    setPendingLanguage(null);
  }
  function requireJapaneseRuby(): boolean {
    const current = documentRef.current;
    if (!current || current.project.language === "cn") return true;
    const report = missingJapaneseRuby(current);
    if (report.lines.length) {
      setMissingRubyDialog(report);
      return false;
    }
    return true;
  }
  async function saveStylePreset(name: string) {
    try {
      const saved = await api<StylePreset>("/settings/subtitle-style-presets", { method: "POST", body: JSON.stringify({ name, style: { ...subtitleStyle } }) });
      setStylePresets((current) => [saved, ...current.filter((preset) => preset.id !== saved.id)]);
    } catch {
      setError("无法保存字幕样式预设，请确认后端已启动。");
    }
  }
  async function deleteStylePreset(preset: StylePreset) {
    if (!window.confirm(`删除字幕样式预设“${preset.name}”？`)) return;
    try {
      await api(`/settings/subtitle-style-presets/${preset.id}`, { method: "DELETE" });
      setStylePresets((current) => current.filter((item) => item.id !== preset.id));
    } catch {
      setError("无法删除字幕样式预设，请稍后重试。");
    }
  }
  function seek(ms: number) {
    const video = videoRef.current;
    if (!video) return;
    const maximum = Number.isFinite(video.duration) ? video.duration * 1000 : ms;
    video.currentTime = Math.max(0, Math.min(maximum, ms)) / 1000;
  }
  function seekBy(deltaMs: number) {
    const video = videoRef.current;
    if (video) seek(video.currentTime * 1000 + deltaMs);
  }
  function placeLine(lineId: string, startMs: number) {
    const current = documentRef.current;
    if (!current) return;
    const line = current.lyrics.lines.find((candidate) => candidate.id === lineId);
    if (!line?.units.length) return;
    replaceDocument(placeLineAt(current, lineId, startMs, durationMs), true);
    selectLine(line);
    seek(startMs);
  }
  async function importLyrics(
    content: string,
    filename: string | null,
    format: "auto" | "text" | "lrc" | "krl",
  ) {
    const revision = await saveNow(),
      before = documentRef.current;
    if (!before) return;
    const result = await api<{ revision: number; document: ProjectDocument; job?: AnalysisJob | null }>(
      `/projects/${id}/lyrics/import`,
      {
        method: "POST",
        body: JSON.stringify({ revision, content, filename, format }),
      },
    );
    historyRef.current.past.push(before);
    historyRef.current.future = [];
    setHistoryVersion((value) => value + 1);
    documentRef.current = result.document;
    setDocument(result.document);
    markDirty(false);
    const firstLine = result.document.lyrics.lines[0];
    setSelectedId(firstLine?.units[0]?.id || null);
    setRubyEditorTarget(firstLine ? { kind: "line", lineId: firstLine.id } : null);
    if (projectRef.current) {
      const next = { ...projectRef.current, revision: result.revision };
      projectRef.current = next;
      setProject(next);
    }
    if (result.job) setJobs((current) => [result.job!, ...current]);
  }

  async function startTranscription(lineIds?: string[]) {
    if (!documentRef.current?.media.video_filename) {
      setWorkflowNotice("请先上传视频，再运行 Whisper 人声粗识别。");
      return;
    }
    if (documentRef.current.media.waveform_source !== "vocals") {
      setWorkflowNotice("请先完成人声分离，再运行 Whisper 人声粗识别。");
      return;
    }
    setAnalysisStarting(true);
    setError(null);
    try {
      const revision = await saveNow();
      const scopedLines = lineIds?.length
        ? documentRef.current?.lyrics.lines.filter((candidate) => lineIds.includes(candidate.id)) || []
        : [];
      const starts = scopedLines.map((line) => line.start_ms).filter((value): value is number => value !== null);
      const ends = scopedLines.map((line) => line.end_ms).filter((value): value is number => value !== null);
      const job = await api<AnalysisJob>(`/projects/${id}/transcribe`, {
        method: "POST",
        body: JSON.stringify({
          revision,
          line_ids: lineIds || [],
          start_ms: starts.length ? Math.min(...starts) : null,
          end_ms: ends.length ? Math.max(...ends) : null,
          preserve_line_anchors: documentRef.current?.lyrics.source_type === "lrc",
          overwrite_policy: "unlocked_only",
        }),
      });
      setJobs((current) => [job, ...current.filter((item) => item.id !== job.id)]);
    } catch (reason) {
      const status = (reason as Error & { status?: number })?.status;
      setError(status === 409 ? "工程版本已变化，请保存后重试。" : "无法启动分析任务，请检查后端能力和素材。 ");
    } finally {
      setAnalysisStarting(false);
    }
  }

  async function retryJob(jobId: string) {
    const job = await api<AnalysisJob>(`/jobs/${jobId}/retry`, { method: "POST" });
    setJobs((current) => [job, ...current]);
  }

  async function cancelJob(jobId: string) {
    try {
      const canceled = await api<AnalysisJob>(`/jobs/${jobId}/cancel`, { method: "POST" });
      setJobs((current) => current.map((job) => job.id === canceled.id ? canceled : job));
    } catch {
      setError("无法取消任务，请稍后重试。");
    }
  }

  async function deleteExport(jobId: string) {
    if (!window.confirm("删除这条导出记录及文件？")) return;
    try {
      await api(`/projects/${id}/exports/${jobId}`, { method: "DELETE" });
      setJobs((current) => current.filter((job) => job.id !== jobId));
    } catch (reason) {
      const status = (reason as Error & { status?: number })?.status;
      setError(status === 409 ? "导出仍在运行，完成或取消后才能删除。" : "无法删除导出记录。");
    }
  }

  async function startExport(payload: { format: "mp4" | "webm" | "krl"; audio_track: "on_vocal" | "off_vocal" }) {
    setError(null);
    try {
      const revision = await saveNow();
      const job = await api<AnalysisJob>(`/projects/${id}/export`, { method: "POST", body: JSON.stringify({ revision, ...payload }) });
      setJobs((current) => [job, ...current.filter((item) => item.id !== job.id)]);
    } catch (reason) {
      const status = (reason as Error & { status?: number })?.status;
      setError(status === 409 ? "工程版本已变化，请保存后重试。" : payload.format === "krl" ? "无法导出 KRL 工程文件，请确认后端已启动。" : "无法启动服务端导出，请确认后端已启动并安装 Chrome。 ");
    }
  }

  async function runPronunciation(mode: "local" | "ai", lineIds?: string[], targetUnitIds?: string[]): Promise<AnalysisJob | null> {
    if (!documentRef.current?.lyrics.lines.length || documentRef.current.project.language === "cn") return null;
    setPronunciationRunning(true);
    setError(null);
    try {
      const revision = await saveNow();
      const unitIds = targetUnitIds || (selectedId && !lineIds?.length ? [selectedId] : []);
      const job = await api<AnalysisJob>(`/projects/${id}/pronunciation-job`, {
        method: "POST",
        body: JSON.stringify({ revision, line_ids: lineIds || [], unit_ids: unitIds, mode, overwrite_policy: "unlocked_only" }),
      });
      setJobs((current) => [job, ...current.filter((item) => item.id !== job.id)]);
      return job;
    } catch (reason) {
      const status = (reason as Error & { status?: number })?.status;
      setError(status === 409 ? "工程版本已变化，请保存后重试。" : "注音失败，现有歌词和 Ruby 未被覆盖。 ");
      return null;
    } finally {
      setPronunciationRunning(false);
    }
  }

  async function fillMissingRubyAndResume() {
    const dialog = missingRubyDialog;
    if (!dialog) return;
    setMissingRubyDialog(null);
    const job = await runPronunciation("local", undefined, dialog.unitIds);
    if (job && dialog.resume) {
      pendingFullResume.current = { pronunciationJobId: job.id, resume: dialog.resume };
    }
  }

  async function startGlobalAlignment() {
    if (documentRef.current?.project.language === "cn") {
      setWorkflowNotice("中文工程使用 FA-Kara 和拼音进行对齐。");
      return;
    }
    if (!requireJapaneseRuby()) return;
    if (!documentRef.current?.media.video_filename) {
      setWorkflowNotice("请先上传视频，再进行 stable-ts 全局对齐。");
      return;
    }
    const analysisDocument = documentRef.current as ProjectDocument & { analysis?: Record<string, { status?: string }>; pronunciation?: { last_run?: { mode?: string } } };
    const analysis = analysisDocument.analysis || {};
    const pronunciationDone = analysisDocument.project.language === "cn" || analysis.pronunciation?.status === "completed"
      || ["local", "ai", "local_fallback"].includes(String(analysisDocument.pronunciation?.last_run?.mode || ""))
      || analysisDocument.lyrics.lines.every((line) => line.units.every((unit) => !unit.surface.match(/[一-龯]/) || unit.ruby));
    if (documentRef.current.media.waveform_source !== "vocals") {
      setWorkflowNotice("请先完成人声分离，再进行 stable-ts 全局对齐。");
      return;
    }
    if (analysis.transcription?.status !== "completed") {
      setWorkflowNotice("请先完成 Whisper 人声粗识别，再进行 stable-ts 全局对齐。");
      return;
    }
    if (!pronunciationDone) {
      setWorkflowNotice("请先完成 AI 注音或本地注音，再进行 stable-ts 全局对齐。");
      return;
    }
    setAnalysisStarting(true);
    setError(null);
    try {
      const revision = await saveNow();
      const job = await api<AnalysisJob>(`/projects/${id}/align-global`, {
        method: "POST",
        body: JSON.stringify({ revision, line_ids: [], overwrite_policy: "unlocked_only" }),
      });
      setJobs((current) => [job, ...current.filter((item) => item.id !== job.id)]);
    } catch (reason) {
      const status = (reason as Error & { status?: number })?.status;
      setError(status === 409 ? "工程版本已变化，请保存后重试。" : "无法启动 stable-ts 全局对齐，请先完成 Whisper 粗识别和注音。 ");
    } finally {
      setAnalysisStarting(false);
    }
  }

  async function startAlignment(lineIds?: string[]) {
    if (documentRef.current?.project.language === "cn") {
      setWorkflowNotice("中文工程使用 FA-Kara 和拼音进行对齐。");
      return;
    }
    if (!requireJapaneseRuby()) return;
    if (!documentRef.current?.media.video_filename) {
      setWorkflowNotice("请先上传视频，再进行 stable-ts 词/短语精修。");
      return;
    }
    const analysis = (documentRef.current as ProjectDocument & { analysis?: Record<string, { status?: string }> }).analysis || {};
    const scopedLine = lineIds?.length === 1
      ? documentRef.current.lyrics.lines.find((line) => line.id === lineIds[0])
      : null;
    if (analysis.global_alignment?.status !== "completed" && !(scopedLine && scopedLine.start_ms !== null && scopedLine.end_ms !== null)) {
      setWorkflowNotice("请先完成 stable-ts 全局对齐，再运行词/短语精修。");
      return;
    }
    setAnalysisStarting(true);
    setError(null);
    try {
      const revision = await saveNow();
      const job = await api<AnalysisJob>(`/projects/${id}/align`, {
        method: "POST",
        body: JSON.stringify({ revision, line_ids: lineIds || [], overwrite_policy: "unlocked_only" }),
      });
      setJobs((current) => [job, ...current.filter((item) => item.id !== job.id)]);
    } catch (reason) {
      const status = (reason as Error & { status?: number })?.status;
      setError(status === 409 ? "工程版本已变化，请保存后重试。" : "无法启动 stable-ts 词/短语精修，请先完成全局对齐。 ");
    } finally {
      setAnalysisStarting(false);
    }
  }

  function collapseLine(lineId: string) {
    const current = documentRef.current;
    if (!current) return;
    beginEdit();
    replaceDocument(collapseLineTiming(current, lineId), false);
    const line = current.lyrics.lines.find((candidate) => candidate.id === lineId);
    if (line) selectLine(line);
  }

  async function startFaKara(lineIds?: string[]) {
    if (!requireJapaneseRuby()) return;
    if (!documentRef.current?.media.video_filename) {
      setWorkflowNotice("请先上传视频，再进行 FA-Kara 对齐。");
      return;
    }
    if (documentRef.current.media.waveform_source !== "vocals") {
      setWorkflowNotice("请先完成人声分离，再进行 FA-Kara 对齐。");
      return;
    }
    const analysisDocument = documentRef.current as ProjectDocument & { analysis?: Record<string, { status?: string }>; pronunciation?: { last_run?: { mode?: string } } };
    const analysis = analysisDocument.analysis || {};
    if (analysis.transcription?.status !== "completed") {
      setWorkflowNotice("请先完成 Whisper 人声粗识别，再进行 FA-Kara 对齐。");
      return;
    }
    const pronunciationDone = analysisDocument.project.language === "cn" || analysis.pronunciation?.status === "completed"
      || ["local", "ai", "local_fallback"].includes(String(analysisDocument.pronunciation?.last_run?.mode || ""))
      || analysisDocument.lyrics.lines.every((line) => line.units.every((unit) => !unit.surface.match(/[一-龯]/) || unit.ruby));
    if (!pronunciationDone) {
      setWorkflowNotice("请先完成 AI 注音或本地注音，再进行 FA-Kara 对齐。");
      return;
    }
    setAnalysisStarting(true);
    setError(null);
    try {
      const revision = await saveNow();
      const job = await api<AnalysisJob>(`/projects/${id}/fa-kara`, {
        method: "POST",
        body: JSON.stringify({ revision, line_ids: lineIds || [], overwrite_policy: "unlocked_only", model: faKaraModel }),
      });
      setJobs((current) => [job, ...current.filter((item) => item.id !== job.id)]);
    } catch (reason) {
      const status = (reason as Error & { status?: number })?.status;
      setError(status === 409 ? "工程版本已变化，请保存后重试。" : "无法启动 FA-Kara 对齐，请先完成粗识别和 AI 注音。 ");
    } finally {
      setAnalysisStarting(false);
    }
  }

  async function startFullAnalysis() {
    setFullAnalysisOpen(false);
    setAnalysisStarting(true);
    setError(null);
    try {
      const revision = await saveNow();
      const language = documentRef.current?.project.language === "cn" ? "cn" : "jp";
      const selectedBackend = language === "cn" ? "fa_kara" : alignmentBackend;
      const stepOrder = selectedBackend === "fa_kara"
        ? (language === "cn" ? ["separation", "transcription", "fa_kara"] : ["separation", "transcription", "pronunciation", "fa_kara"])
        : ["separation", "transcription", "pronunciation", "global_alignment", "alignment"];
      const steps = stepOrder.filter((key) => fullSteps[key]);
      const job = await api<AnalysisJob>(`/projects/${id}/analysis`, {
        method: "POST",
        body: JSON.stringify({ revision, steps, alignment_backend: selectedBackend, fa_kara_model: faKaraModel, line_ids: [], unit_ids: [], overwrite_policy: "unlocked_only", preserve_line_anchors: documentRef.current?.lyrics.source_type === "lrc" }),
      });
      setJobs((current) => [job, ...current.filter((item) => item.id !== job.id)]);
    } catch (reason) {
      const status = (reason as Error & { status?: number })?.status;
      setError(status === 409 ? "工程版本已变化，请保存后重试。" : (reason as Error)?.message?.includes("不能跳过") || (reason as Error)?.message?.includes("前置") ? "不能跳过未完成的流程，请保留所有未完成步骤。" : "无法启动全曲分析，请检查素材和前置流程。 ");
    } finally {
      setAnalysisStarting(false);
    }
  }

  async function openFullAnalysis() {
    setAnalysisStarting(true);
    setError(null);
    try {
      if (dirtyRef.current) await saveNow();
      const loadedJobs = await api<AnalysisJob[]>(`/jobs?project_id=${encodeURIComponent(id)}&limit=12`);
      await reloadFromServer();
      setJobs(loadedJobs);
      const language = documentRef.current?.project.language === "cn" ? "cn" : "jp";
      const selectedBackend = language === "cn" ? "fa_kara" : alignmentBackend;
      setFullSteps(selectedBackend === "fa_kara"
        ? (language === "cn" ? { separation: true, transcription: true, fa_kara: true } : { separation: true, transcription: true, pronunciation: true, fa_kara: true })
        : { separation: true, transcription: true, pronunciation: true, global_alignment: true, alignment: true });
      setFullAnalysisOpen(true);
    } catch (reason) {
      const status = (reason as Error & { status?: number })?.status;
      setError(status === 409 ? "工程版本已变化，请保存或刷新后重试。" : "无法同步全曲分析状态，请确认后端已启动。 ");
    } finally {
      setAnalysisStarting(false);
    }
  }

  async function uploadVideo(file: File) {
    if (!file.name.toLowerCase().endsWith(".mp4")) {
      setError("当前仅支持 MP4 视频。");
      return;
    }
    try {
      await saveNow();
    } catch {
      return;
    }
    setError(null);
    setUploadProgress(0);
    setUploading(true);
    const data = new FormData();
    data.append("video", file);
    const request = new XMLHttpRequest();
    uploadRequest.current = request;
    request.open("POST", `/api/projects/${id}/video`);
    request.upload.onprogress = (event) =>
      event.lengthComputable &&
      setUploadProgress(Math.round((event.loaded / event.total) * 100));
    request.onload = () => {
      uploadRequest.current = null;
      if (request.status >= 200 && request.status < 300) {
        const result = JSON.parse(request.responseText) as {
          revision: number;
          media: Record<string, unknown>;
          job?: AnalysisJob | null;
        };
        const current = documentRef.current;
        if (current) {
          const nextDocument = {
            ...current,
            media: { ...current.media, ...result.media },
          };
          documentRef.current = nextDocument;
          setDocument(nextDocument);
        }
        if (projectRef.current) {
          const nextProject = {
            ...projectRef.current,
            revision: result.revision,
          };
          projectRef.current = nextProject;
          setProject(nextProject);
        }
        setUploadProgress(100);
        if (result.job) setJobs((current) => [result.job!, ...current]);
      } else setError("视频上传或媒体处理失败。");
      setUploading(false);
    };
    request.onerror = () => {
      setError("无法连接到后端服务。");
      setUploading(false);
    };
    request.onabort = () => {
      setError("视频上传已取消。");
      setUploading(false);
    };
    request.send(data);
  }

  if (!project || !document)
    return <div className="loading">{error || "正在打开工程…"}</div>;
  const durationMs = Math.max(
    typeof document.media.duration_ms === "number"
      ? document.media.duration_ms
      : 0,
    ...document.lyrics.lines.map((line) => line.end_ms || 0),
    60_000,
  );
  const hasVideo = Boolean(document.media.video_filename),
    lines = document.lyrics.lines;
  const hasLyrics = lines.some((line) => line.units.some((unit) => Boolean(unit.surface)));
  const language: ProjectLanguage = document.project.language === "cn" ? "cn" : "jp";
  const effectiveAlignmentBackend = language === "cn" ? "fa_kara" : alignmentBackend;
  const activeJob = jobs.find((job) => ["QUEUED", "PREPARING", "RUNNING"].includes(job.status));
  const visibleJob = activeJob || jobs[0] || null;
  const editing = editingId
    ? lines
        .flatMap((line) => line.units)
        .find((unit) => unit.id === editingId) || null
    : null;
  const rubyTargetLine = rubyEditorTarget
    ? lines.find((line) => line.id === rubyEditorTarget.lineId) || null
    : null;
  const rubyTargetRange = rubyTargetLine && rubyEditorTarget
    ? rubyEditorTarget.kind === "line"
      ? { start: 0, end: lineCharacterCount(rubyTargetLine), title: "整行 Ruby" }
      : (() => {
          const total = lineCharacterCount(rubyTargetLine);
          const baseStart = Math.max(0, Math.min(total, rubyEditorTarget.start));
          const baseEnd = Math.max(baseStart, Math.min(total, rubyEditorTarget.end));
          const overlappingGroups = rubyRanges(rubyTargetLine).filter((group) => group.start < baseEnd && group.end > baseStart);
          return {
            start: overlappingGroups.reduce((value, group) => Math.min(value, group.start), baseStart),
            end: overlappingGroups.reduce((value, group) => Math.max(value, group.end), baseEnd),
            title: "所选 Unit 的 Ruby",
          };
        })()
    : null;
  const rubyTargetStatus = rubyTargetLine && rubyEditorTarget
    ? (() => {
        const targetUnit = rubyEditorTarget.kind === "unit"
          ? rubyTargetLine.units.find((unit) => unit.id === rubyEditorTarget.unitId) || null
          : null;
        const timing = rubyEditorTarget.kind === "line"
          ? rubyTargetLine.start_ms === null || rubyTargetLine.end_ms === null
            ? "未设置时间"
            : rubyTargetLine.timing_precision === "line" ? "行级时间" : "精确时间"
          : targetUnit?.start_ms === null || targetUnit?.end_ms === null
            ? "未设置时间"
            : targetUnit?.timing_source === "estimated" ? "估算时间" : "精确时间";
        const rangeGroups = rubyRanges(rubyTargetLine).filter((group) => rubyTargetRange && group.start < rubyTargetRange.end && group.end > rubyTargetRange.start);
        const sources = new Set<string>();
        let offset = 0;
        rubyTargetLine.units.forEach((unit) => {
          if (rangeGroups.some((group) => group.start === offset)) sources.add(unit.ruby_source || "manual");
          offset += Array.from(unit.surface).length;
        });
        const ruby = sources.size === 0 ? "未注音" : sources.size === 1 ? `Ruby ${[...sources][0]}` : "Ruby 混合来源";
        return `${timing} · ${ruby}`;
      })()
    : "";
  const sourceLabel =
    document.lyrics.source_type === "krl"
      ? "逐字 KRL"
      : document.lyrics.source_type === "lrc"
        ? "普通 LRC"
        : document.lyrics.source_type === "text"
          ? "纯文本"
          : "未添加";
  void historyVersion;

  return (
    <>
      <header className="topbar">
        <a href="#/projects" className="brand">
          <span className="brand-mark">N</span>
          <span>Nicokara Studio</span>
        </a>
        <span className="top-title">{project.name}</span>
      </header>
      <main className="editor phase2-editor">
        <div className="editor-head">
          <a className="back-link" href="#/projects">
            <ArrowLeft size={18} />
            项目列表
          </a>
          <div className="editor-actions">
            <button
              className="icon-button"
              title="撤销"
              disabled={!historyRef.current.past.length}
              onClick={undo}
            >
              <Undo2 size={19} />
            </button>
            <button
              className="icon-button"
              title="重做"
              disabled={!historyRef.current.future.length}
              onClick={redo}
            >
              <Redo2 size={19} />
            </button>
            <span className={`save-state ${dirty || error ? "pending" : ""}`}>
              <Save size={15} />
              {error
                ? "未保存"
                : saving
                  ? "保存中"
                  : dirty
                    ? "等待保存"
                    : "已保存"}
            </span>
            <button
              className="button tonal"
              onClick={() => setImportOpen(true)}
            >
              <FileText size={17} />
              {lines.length ? "替换歌词" : "添加歌词"}
            </button>
            <button className="button tonal" disabled={language === "cn" || !hasLyrics || pronunciationRunning || Boolean(activeJob) || analysisStarting} onClick={() => void runPronunciation("local")} title={language === "cn" ? "中文工程不需要注音" : "为未锁定单元生成本地日语读音"}>
              <WandSparkles size={17} />
              {pronunciationRunning ? "注音中" : "本地注音"}
            </button>
            <button className="button tonal" disabled={language === "cn" || !hasLyrics || pronunciationRunning || Boolean(activeJob) || analysisStarting} onClick={() => void runPronunciation("ai")} title={language === "cn" ? "中文工程不需要注音" : "使用 Whisper 粗识别结果生成读音；缺少结果时会先完成人声分离和粗识别"}>
              <WandSparkles size={17} />AI 注音
            </button>
            <button className="button tonal" disabled={!hasVideo || !lines.length || Boolean(activeJob) || analysisStarting} onClick={() => void startTranscription()} title="仅运行 Whisper 人声粗识别">
              <AudioLines size={17} />粗识别
            </button>
            {effectiveAlignmentBackend === "stable_ts" ? <>
              <button className="button tonal" disabled={!hasVideo || !lines.length || Boolean(activeJob) || analysisStarting} onClick={() => void startGlobalAlignment()} title="使用 AI 注音完整歌词生成行级时间">
                <AudioLines size={17} />全局对齐
              </button>
              <button className="button tonal" disabled={!hasVideo || !lines.length || Boolean(activeJob) || analysisStarting} onClick={() => void startAlignment()} title="在全局对齐行范围内生成词/短语时间">
                <AudioLines size={17} />词/短语精修
              </button>
            </> : <button className="button tonal" disabled={!hasVideo || !lines.length || Boolean(activeJob) || analysisStarting} onClick={() => void startFaKara()} title={`使用 FA-Kara ${faKaraModel === "yohane" ? "YoHane 微调模型" : "MMS_FA 基座模型"}对齐`}>
              <AudioLines size={17} />FA-Kara 对齐
            </button>}
            <button
              className="button tonal"
              disabled={!hasVideo || !lines.length || Boolean(activeJob) || analysisStarting}
              onClick={() => void openFullAnalysis()}
            >
              <WandSparkles size={17} />全曲分析
            </button>
            <button className="button filled" disabled={!lines.length || Boolean(activeJob) || analysisStarting} onClick={() => setExportOpen(true)} title="导出 Kirakara 完整效果">
              <Download size={17} />导出
            </button>
            <label className={`button tonal ${uploading ? "disabled" : ""}`}>
              <Upload size={17} />
              {hasVideo ? "替换视频" : "选择视频"}
              <input
                type="file"
                accept="video/mp4,.mp4"
                hidden
                disabled={uploading}
                onChange={(event) =>
                  event.target.files?.[0] &&
                  void uploadVideo(event.target.files[0])
                }
              />
            </label>
          </div>
        </div>
        {error && (
          <div className="editor-error">
            <AlertCircle size={17} />
            {error}
          </div>
        )}
        {visibleJob && (
          <section className={`analysis-status ${visibleJob.status.toLowerCase()}`}>
            <span className="analysis-icon">
              {activeJob ? <LoaderCircle className="spin" size={18} /> : visibleJob.status === "FAILED" ? <AlertCircle size={18} /> : <AudioLines size={18} />}
            </span>
            <div className="analysis-copy">
              <strong>{visibleJob.type === "EXPORT" ? "Kirakara 服务端导出" : visibleJob.type === "VOCAL_SEPARATION" ? "人声分离" : visibleJob.type === "PRONUNCIATION" ? (Array.isArray(visibleJob.request?.steps) && visibleJob.request.steps.includes("transcription") ? "人声分离 + Whisper + AI 注音" : "注音") : visibleJob.type === "FA_KARA_ALIGNMENT" ? "FA-Kara 对齐" : visibleJob.type === "FULL_ANALYSIS" && visibleJob.request?.alignment_backend === "fa_kara" ? "人声分离 + Whisper + FA-Kara" : "人声分离 + Whisper 对齐"}</strong>
              <span>{visibleJob.error_message || visibleJob.message || visibleJob.stage}</span>
            </div>
            <div className="analysis-steps">
              {(visibleJob.steps?.length ? visibleJob.steps : [{ key: visibleJob.stage, label: visibleJob.type, status: activeJob ? "running" : "completed", progress: visibleJob.progress }]).map((step) => (
                <div className="analysis-step" key={step.key}>
                  <span>{step.label}</span>
                  <div className="analysis-progress" aria-label={`${step.label} ${Math.round(step.progress * 100)}%`}><i style={{ width: `${Math.max(step.progress ? 2 : 0, step.progress * 100)}%` }} /></div>
                  <b>{Math.round(step.progress * 100)}%</b>
                </div>
              ))}
            </div>
            {visibleJob.status === "FAILED" && (
              <button className="icon-button compact" title="重试任务" onClick={() => void retryJob(visibleJob.id)}>
                <RotateCcw size={16} />
              </button>
            )}
            {activeJob && <button className="icon-button compact" title="取消任务" onClick={() => void cancelJob(activeJob.id)}><Square size={16} /></button>}
          </section>
        )}
        <div className="workspace-main">
          <section
            className="video-workspace"
            onDragOver={(event) => event.preventDefault()}
            onDrop={(event) => {
              event.preventDefault();
              const file = event.dataTransfer.files[0];
              if (file) void uploadVideo(file);
            }}
          >
            {hasVideo ? (
              <video
                ref={videoRef}
                poster={`/api/projects/${id}/thumbnail`}
                src={`/api/projects/${id}/video`}
                onPlay={() => setIsPlaying(true)}
                onPause={() => setIsPlaying(false)}
                onEnded={() => setIsPlaying(false)}
                onTimeUpdate={(event) => setCurrentMs(event.currentTarget.currentTime * 1000)}
              />
            ) : (
              <div className="drop-placeholder">
                <FileVideo size={46} />
                <h2>添加视频</h2>
                <label className="button filled">
                  <Upload size={17} />
                  选择 MP4
                  <input
                    type="file"
                    accept="video/mp4,.mp4"
                    hidden
                    onChange={(event) =>
                      event.target.files?.[0] &&
                      void uploadVideo(event.target.files[0])
                    }
                  />
                </label>
              </div>
            )}
            {hasVideo && <SubtitleDomRenderer lines={lines} currentMs={currentMs} style={subtitleStyle} mediaRef={videoRef} isPlaying={isPlaying} />}
            {uploading && (
              <div className="media-progress">
                <LoaderCircle className="spin" size={17} />
                <span>正在处理视频</span>
                <div className="progress-track">
                  <div
                    className="progress-value"
                    style={{ width: `${uploadProgress}%` }}
                  />
                </div>
                <strong>{uploadProgress}%</strong>
                <button
                  className="icon-button compact"
                  title="取消上传"
                  onClick={() => uploadRequest.current?.abort()}
                >
                  <X size={16} />
                </button>
              </div>
            )}
          </section>
          <aside className="lyrics-panel">
            <section className="project-fields">
              <div className="section-title">
                <h3>工程信息</h3>
              </div>
              <label className="field-label">
                项目名称
                <input
                  value={document.project.name}
                  onFocus={beginEdit}
                  onChange={(event) =>
                    replaceDocument(
                      {
                        ...documentRef.current!,
                        project: {
                          ...documentRef.current!.project,
                          name: event.target.value,
                        },
                      },
                      false,
                    )
                  }
                />
              </label>
              <label className="field-label">
                歌词语言
                <select value={language} onChange={(event) => changeLanguage(event.target.value as ProjectLanguage)}>
                  <option value="jp">JP · 日语</option>
                  <option value="cn">CN · 中文</option>
                </select>
              </label>
              <div className="field-row">
                <label className="field-label">
                  歌曲名
                  <input
                    value={document.project.title}
                    onFocus={beginEdit}
                    onChange={(event) =>
                      replaceDocument(
                        {
                          ...documentRef.current!,
                          project: {
                            ...documentRef.current!.project,
                            title: event.target.value,
                          },
                        },
                        false,
                      )
                    }
                  />
                </label>
                <label className="field-label">
                  歌手
                  <input
                    value={document.project.artist}
                    onFocus={beginEdit}
                    onChange={(event) =>
                      replaceDocument(
                        {
                          ...documentRef.current!,
                          project: {
                            ...documentRef.current!.project,
                            artist: event.target.value,
                          },
                        },
                        false,
                      )
                    }
                  />
                </label>
              </div>
            </section>
            <div className="lyrics-head">
              <div>
                <h2>歌词</h2>
                <span>
                  {sourceLabel} · {lines.length} 行
                </span>
              </div>
              <button
                className="icon-button"
                title={lines.length ? "替换歌词" : "添加歌词"}
                onClick={() => setImportOpen(true)}
              >
                <FileText size={19} />
              </button>
            </div>
            <div className="lyrics-list">
              {lines.map((line) => {
                const surface = line.units.map((unit) => unit.surface).join("");
                const active = line.units.some(
                  (unit) => unit.id === selectedId,
                );
                return (
                  <button
                    key={line.id}
                    className={`lyric-line ${active ? "active" : ""}`}
                    draggable
                    title="拖到时间轴设置时间"
                    onContextMenu={(event) => {
                      event.preventDefault();
                      if (!hasVideo || activeJob) return;
                      setLineMenu({ lineId: line.id, unitId: null, x: event.clientX, y: event.clientY });
                    }}
                    onDragStart={(event) => {
                      event.dataTransfer.effectAllowed = "move";
                      event.dataTransfer.setData(
                        "application/x-nicokara-lyric-line",
                        line.id,
                      );
                      event.dataTransfer.setData("text/plain", line.id);
                      selectLine(line);
                    }}
                    onClick={() => selectLine(line)}
                  >
                    <span className="line-index">
                      {String(line.order + 1).padStart(2, "0")}
                    </span>
                    <span className="line-copy">
                      <strong>{surface}</strong>
                      <small>
                        {formatTime(line.start_ms)}
                        {line.timing_precision === "line" ? " · 行级时间" : ""}
                        {line.units.some((unit) => unit.timing_confidence !== null && unit.timing_confidence < 0.55) ? " · 低置信度" : ""}
                      </small>
                    </span>
                    <GripVertical className="line-drag-handle" size={16} />
                  </button>
                );
              })}
              {!lines.length && (
                <div className="lyrics-empty">
                  <FileText size={28} />
                  <span>尚未添加歌词</span>
                </div>
              )}
            </div>
            {language === "jp" && rubyTargetLine && rubyTargetRange && rubyTargetRange.end > rubyTargetRange.start && (
              <RubyRangeEditor
                line={rubyTargetLine}
                rangeStart={rubyTargetRange.start}
                rangeEnd={rubyTargetRange.end}
                title={rubyTargetRange.title}
                status={rubyTargetStatus}
                onApply={(start, end, ruby, ruby2, replacedRange) => {
                  const current = documentRef.current;
                  if (!current) return;
                  replaceDocument(updateRubyRange(current, rubyTargetLine.id, start, end, ruby, ruby2, replacedRange, createClientId), true);
                }}
              />
            )}
            <SubtitleStylePanel style={subtitleStyle} presets={stylePresets} onChange={updateSubtitleStyle} onApplyPreset={(preset) => updateSubtitleStyle(preset.style)} onSavePreset={(name) => void saveStylePreset(name)} onDeletePreset={(preset) => void deleteStylePreset(preset)} />
          </aside>
        </div>
        <TimelineCanvas
          projectId={id}
          waveformSource={String(document.media.waveform_source || "source")}
          lines={lines}
          durationMs={durationMs}
          mediaRef={videoRef}
          hasVideo={hasVideo}
          isPlaying={isPlaying}
          selectedId={selectedId}
          onSelect={selectUnit}
          onSeek={seek}
          onSeekBy={seekBy}
          onTogglePlayback={togglePlayback}
          onBeginEdit={beginEdit}
          onUpdateUnit={(lineId, unitId, patch) =>
            updateSelected(lineId, unitId, patch)
          }
          onUpdateLine={(lineId, startMs, endMs) => {
            const current = documentRef.current;
            if (current) replaceDocument(updateLineTiming(current, lineId, startMs, endMs), false);
          }}
          onUpdateRubyGroup={(lineId, unitIds, ruby, ruby2, rubySpan, clearUnitIds) => {
            const current = documentRef.current;
            if (current) replaceDocument(updateRubyGroup(current, lineId, unitIds, ruby, ruby2, rubySpan, clearUnitIds), true);
          }}
          rubyEnabled={language === "jp"}
          onOpenEditor={setEditingId}
          onDropLine={placeLine}
          onOpenContextMenu={(lineId, unitId, lineLevel, x, y) => {
            if (!activeJob) setLineMenu({ lineId, unitId: lineLevel ? null : unitId, x, y });
          }}
        />
      </main>
      <a className="fab" title="设置" href={settingsHref()}>
        <Cog size={22} />
      </a>
      {importOpen && (
        <LyricsImportDialog
          projectId={id}
          onClose={() => setImportOpen(false)}
          onImport={importLyrics}
        />
      )}
      {editing && (
        <UnitEditDialog
          unit={editing}
          rubyEnabled={language === "jp"}
          onClose={() => setEditingId(null)}
          onSave={(patch) => {
            const owner = lines.find((line) =>
              line.units.some((unit) => unit.id === editing.id),
            );
            if (owner) updateSelected(owner.id, editing.id, patch, true);
            setEditingId(null);
          }}
        />
      )}
      {splitTarget && (() => {
        const target = lines.find((line) => line.id === splitTarget.lineId)?.units.find((unit) => unit.id === splitTarget.unitId);
        if (!target) return null;
        return <UnitSplitDialog unit={target} onClose={() => setSplitTarget(null)} onConfirm={(ranges) => {
          const current = documentRef.current;
          const owner = current?.lyrics.lines.find((line) => line.id === splitTarget.lineId);
          if (!current || !owner?.units.some((unit) => unit.id === splitTarget.unitId)) {
            throw new Error("工程内容已发生变化，请关闭后重新打开拆分页面。");
          }
          replaceDocument(splitLyricUnit(current, splitTarget.lineId, splitTarget.unitId, ranges), true);
          selectUnit(splitTarget.unitId);
          setSplitTarget(null);
        }} />;
      })()}
      {lineMenu && (() => {
        const index = lines.findIndex((line) => line.id === lineMenu.lineId);
        const menuLine = lines[index];
        const menuUnit = lineMenu.unitId ? menuLine?.units.find((unit) => unit.id === lineMenu.unitId) : null;
        return <div className="line-context-menu" style={{ left: lineMenu.x, top: lineMenu.y }} onPointerDown={(event) => event.stopPropagation()}>
          <strong>时间单元</strong>
          <button disabled={menuLine?.timing_precision === "line"} onClick={() => { setLineMenu(null); collapseLine(lineMenu.lineId); }}>还原为整句时间单元</button>
          <button disabled={menuLine?.start_ms === null || menuLine?.end_ms === null} onClick={() => { setLineMenu(null); effectiveAlignmentBackend === "fa_kara" ? void startFaKara([lineMenu.lineId]) : void startAlignment([lineMenu.lineId]); }}>
            {effectiveAlignmentBackend === "fa_kara" ? "用 FA-Kara 重新识别此句" : "用 stable-ts 重新识别此句"}
          </button>
          {menuUnit && Array.from(menuUnit.surface).length > 1 && <button onClick={() => { setLineMenu(null); setSplitTarget({ lineId: lineMenu.lineId, unitId: menuUnit.id }); }}><Scissors size={15} />拆分此 Unit</button>}
        </div>;
      })()}
      {fullAnalysisOpen && (
        <div className="scrim">
          <section className="dialog full-analysis-dialog" role="dialog" aria-modal="true" aria-labelledby="full-analysis-title">
            <div className="dialog-head"><div><h2 id="full-analysis-title">确认全曲分析</h2><p className="muted">将按勾选顺序执行；跳过未完成流程会被后端拒绝。</p></div><button className="icon-button" title="关闭" onClick={() => setFullAnalysisOpen(false)}><X size={18} /></button></div>
            <div className="pipeline-preview">
              {(effectiveAlignmentBackend === "fa_kara" ? (language === "cn" ? [
                ["separation", "人声分离", "生成 Whisper / 对齐使用的 vocals"],
                ["transcription", "Whisper 人声粗识别", "保存实际演唱的 segment 文本和粗时间"],
                ["fa_kara", `FA-Kara 对齐 · ${faKaraModel === "yohane" ? "YoHane" : "MMS_FA"}`, "使用中文拼音生成词/字级时间"],
              ] : [
                ["separation", "人声分离", "生成 Whisper / 对齐使用的 vocals"],
                ["transcription", "Whisper 人声粗识别", "保存实际演唱的 segment 文本和粗时间"],
                ["pronunciation", "AI 注音", "结合完整歌词与 Whisper segment 生成 Ruby"],
                ["fa_kara", `FA-Kara 对齐 · ${faKaraModel === "yohane" ? "YoHane" : "MMS_FA"}`, "一次生成行级与词/短语时间"],
              ]) : [
                ["separation", "人声分离", "生成 Whisper / 对齐使用的 vocals"],
                ["transcription", "Whisper 人声粗识别", "保存实际演唱的 segment 文本和粗时间"],
                ["pronunciation", "AI 注音", "结合完整歌词与 Whisper segment 生成 Ruby"],
                ["global_alignment", "stable-ts 全局对齐", "只写入可单独观察的行级时间"],
                ["alignment", "stable-ts 词/短语精修", "在全局对齐行范围内写入 unit 时间"],
              ]).map(([key, label, description]) => {
                const analysis = (document as ProjectDocument & { analysis?: Record<string, { status?: string }> }).analysis || {};
                const complete = key === "separation" ? document.media.waveform_source === "vocals" : key === "transcription" ? analysis.transcription?.status === "completed" : key === "pronunciation" ? Boolean(analysis.pronunciation?.status === "completed" || document.lyrics.lines.every((line) => line.units.every((unit) => !unit.surface.match(/[一-龯]/) || unit.ruby))) : key === "global_alignment" ? analysis.global_alignment?.status === "completed" : key === "alignment" ? analysis.alignment?.status === "completed" : analysis.fa_kara?.status === "completed";
                return <label className="pipeline-step" key={key}><input type="checkbox" checked={Boolean(fullSteps[key])} onChange={(event) => setFullSteps((current) => ({ ...current, [key]: event.target.checked }))} /><span><strong>{label}</strong><small>{complete ? "已完成，可跳过" : description}</small></span><em className={complete ? "complete" : "pending"}>{complete ? "已完成" : "待执行"}</em></label>;
              })}
            </div>
            <div className="dialog-actions"><button className="button text" onClick={() => setFullAnalysisOpen(false)}>取消</button><button className="button filled" disabled={!Object.values(fullSteps).some(Boolean)} onClick={() => void startFullAnalysis()}><WandSparkles size={17} />开始分析</button></div>
          </section>
        </div>
      )}
      {exportOpen && <ExportDialog hasVideo={hasVideo} jobs={jobs} onClose={() => setExportOpen(false)} onStart={(payload) => void startExport(payload)} onCancel={(jobId) => void cancelJob(jobId)} onDelete={(jobId) => void deleteExport(jobId)} />}
      {workflowNotice && (
        <div className="scrim">
          <section className="dialog workflow-notice" role="alertdialog" aria-modal="true" aria-labelledby="workflow-notice-title">
            <div className="dialog-head"><h2 id="workflow-notice-title">暂时无法开始</h2><button className="icon-button" title="关闭" onClick={() => setWorkflowNotice(null)}><X size={18} /></button></div>
            <p className="workflow-notice-copy">{workflowNotice}</p>
            <div className="dialog-actions"><button className="button filled" onClick={() => setWorkflowNotice(null)}>知道了</button></div>
          </section>
        </div>
      )}
      {pendingLanguage && (
        <div className="scrim">
          <section className="dialog workflow-notice" role="alertdialog" aria-modal="true" aria-labelledby="language-change-title">
            <div className="dialog-head"><h2 id="language-change-title">确认切换歌词语言</h2><button className="icon-button" title="关闭" onClick={() => setPendingLanguage(null)}><X size={18} /></button></div>
            <p className="workflow-notice-copy">切换语言会清除当前工程的 Ruby、Ruby 范围和相关分析结果。</p>
            <div className="dialog-actions"><button className="button text" onClick={() => setPendingLanguage(null)}>取消</button><button className="button filled" onClick={confirmLanguageChange}><Check size={17} />切换并清除 Ruby</button></div>
          </section>
        </div>
      )}
      {missingRubyDialog && (
        <div className="scrim">
          <section className="dialog workflow-notice" role="alertdialog" aria-modal="true" aria-labelledby="missing-ruby-title">
            <div className="dialog-head"><h2 id="missing-ruby-title">{missingRubyDialog.resume ? "全曲分析需要补充注音" : "无法开始对齐"}</h2><button className="icon-button" title="关闭" onClick={() => setMissingRubyDialog(null)}><X size={18} /></button></div>
            <p className="workflow-notice-copy">{missingRubyDialog.resume ? "AI 注音完成后，以下日文汉字仍未注音。取消将中断本次全曲分析；使用本地注音后会继续对齐。" : "以下日文汉字尚未注音："}</p>
            <ul className="missing-ruby-list">{missingRubyDialog.lines.map((item) => <li key={item.lineIndex}>第 {item.lineIndex + 1} 行：{item.characters}</li>)}</ul>
            <div className="dialog-actions"><button className="button text" onClick={() => setMissingRubyDialog(null)}>{missingRubyDialog.resume ? "取消全曲分析" : "取消"}</button><button className="button filled" onClick={() => void fillMissingRubyAndResume()}><WandSparkles size={17} />使用本地注音</button></div>
          </section>
        </div>
      )}
    </>
  );
}
