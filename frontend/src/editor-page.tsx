import { AppHeader } from "./app-header";
import { usePreferences } from "./preferences";
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
  Pencil,
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
import { replaceLyricLineText } from "./lyric-line-edit";
import { LyricsImportDialog } from "./lyrics-import-dialog";
import { RubyRangeEditor } from "./ruby-range-editor";
import { lineCharacterCount, rubyRanges, unitCharacterRange, updateRubyRange } from "./ruby-range";
import { initialRubyTiming, retimeRubyGroup, rubyGroupEnd, timedBounds } from "./ruby-timing";
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
        const units = line.units.map((unit) => {
          if (unit.id !== unitId) return unit;
          const rubyChanged = ("ruby" in patch && patch.ruby !== unit.ruby) || ("surface" in patch && patch.surface !== unit.surface);
          return { ...unit, ...patch, ...(rubyChanged ? { ruby_timing: undefined } : {}) };
        });
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
            if (unit.id === firstId) return { ...unit, ruby, ruby_2: ruby2 || null, ruby_span: rubySpan, ruby_timing: undefined, ruby_source: "manual" };
            if (selected.has(unit.id)) return { ...unit, ruby: null, ruby_2: null, ruby_span: undefined, ruby_timing: undefined, ruby_source: "manual" };
            if (cleared.has(unit.id)) return { ...unit, ruby: null, ruby_2: null, ruby_span: undefined, ruby_timing: undefined, ruby_source: "none" };
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
          ruby_timing: undefined,
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
            ruby_timing: fullyTimed ? unit.ruby_timing?.map((part) => ({
              ...part,
              start_ms: Math.round(startMs + placedSpan * (part.start_ms - originalStart) / span),
              end_ms: Math.round(startMs + placedSpan * (part.end_ms - originalStart) / span),
            })) : undefined,
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
            ruby_timing: first ? original.ruby_timing : undefined,
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
  const { t, detail } = usePreferences();
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
        <div><h2 id="unit-split-title">{t("拆分 Unit")}</h2><p className="muted">{unit.surface}</p></div>
        <button className="icon-button" type="button" title={t("撤回上一次拆分")} aria-label={t("撤回上一次拆分")} disabled={!history.length} onClick={undoLocal}><Undo2 size={19} /></button>
        <button className="icon-button" type="button" title={t("关闭")} onClick={onClose}><X size={19} /></button>
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
      <div className="unit-split-summary"><span>{selectedRange ? t("已选 {0} 字", selectedRange.end - selectedRange.start) : t("未选择")}</span><strong>{ranges.length} {t("Units")}</strong></div>
      {confirmError && <div className="unit-split-error" role="alert"><AlertCircle size={16} />{detail(confirmError)}</div>}
      <div className="dialog-actions unit-split-actions">
        <button className="button tonal" type="button" disabled={!selectedRange || selectedRange.end - selectedRange.start < 2 || alreadyGrouped} onClick={groupSelection}><Scissors size={17} />{t("拆分")}</button>
        <button className="button filled" type="button" onClick={confirmSplit}><Check size={17} />{t("确定")}</button>
        <button className="button text" type="button" onClick={onClose}>{t("取消")}</button>
      </div>
    </section>
  </div>;
}

function LineEditDialog({ line, onClose, onSave }: {
  line: LyricLine;
  onClose: () => void;
  onSave: (text: string) => void;
}) {
  const { t } = usePreferences();
  const original = line.units.map((unit) => unit.surface).join("");
  const [text, setText] = useState(original);
  const valid = Boolean(text.trim()) && text.trim() !== original;
  return <div className="scrim">
    <form className="dialog unit-dialog" role="dialog" aria-modal="true" aria-labelledby="line-edit-title"
      onSubmit={(event) => { event.preventDefault(); if (valid) onSave(text); }}
      onKeyDown={(event) => {
        if (event.key === "Escape") onClose();
        if ((event.ctrlKey || event.metaKey) && ["z", "y"].includes(event.key.toLowerCase())) event.stopPropagation();
      }}>
      <div className="dialog-head">
        <div>
          <h2 id="line-edit-title">{t("修改歌词")}</h2>
          <p className="muted">{t("保存后将使用整句时间，保留仍在句中的词组注音。")}</p>
        </div>
        <button type="button" className="icon-button" title={t("关闭")} onClick={onClose}><X size={20} /></button>
      </div>
      <label className="field-label">
        {t("歌词正文")}
        <input autoFocus value={text} onChange={(event) => setText(event.target.value)} />
      </label>
      <div className="dialog-actions">
        <button type="button" className="button text" onClick={onClose}>{t("取消")}</button>
        <button type="submit" className="button filled" disabled={!valid}><Check size={17} />{t("保存")}</button>
      </div>
    </form>
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
  const { t } = usePreferences();
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
          <h2>{t("编辑歌词单元")}</h2>
          <button className="icon-button" title={t("关闭")} onClick={onClose}>
            <X size={20} />
          </button>
        </div>
        <label className="field-label">
          {t("正文")}
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
          {t("第二 Ruby")}
          <input
            value={ruby2}
            onChange={(event) => setRuby2(event.target.value)}
          />
        </label>}
        <div className="time-fields">
          <label className="field-label">
            {t("开始")}
            <input
              inputMode="decimal"
              value={start}
              onChange={(event) => setStart(event.target.value)}
            />
          </label>
          <label className="field-label">
            {t("结束")}
            <input
              inputMode="decimal"
              value={end}
              onChange={(event) => setEnd(event.target.value)}
            />
          </label>
        </div>
        <div className="dialog-actions">
          <button className="button text" onClick={onClose}>
            {t("取消")}
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
            {t("应用")}
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
  const { t, language, detail } = usePreferences();
  const [format, setFormat] = useState<"mp4" | "webm" | "krl">("mp4");
  const [audioTrack, setAudioTrack] = useState<"on_vocal" | "off_vocal">("on_vocal");
  const exportJobs = jobs.filter((job) => job.type === "EXPORT");
  const active = exportJobs.find((job) => ["QUEUED", "PREPARING", "RUNNING"].includes(job.status));
  return <div className="scrim">
    <section className="dialog export-dialog" role="dialog" aria-modal="true" aria-labelledby="export-title">
      <div className="dialog-head"><div><h2 id="export-title">{t("导出工程")}</h2><p className="muted">{t("视频使用 Kirakara 原生渲染；KRL 保留时间、Ruby、角色与当前字幕样式。")}</p></div><button className="icon-button" title={t("关闭")} onClick={onClose}><X size={18} /></button></div>
      <div className="export-form">
        <label className="field-label">{t("输出格式")}<select value={format} onChange={(event) => setFormat(event.target.value as typeof format)}><option value="mp4">{t("MP4 · H.264 视频")}</option><option value="webm">{t("WebM · VP9 视频")}</option><option value="krl">{t("KRL · Kirakara 工程")}</option></select></label>
        {format !== "krl" && <label className="field-label">{t("音轨")}<select value={audioTrack} onChange={(event) => setAudioTrack(event.target.value as typeof audioTrack)}><option value="on_vocal">{t("ON VOCAL · 原始音轨")}</option><option value="off_vocal">{t("OFF VOCAL · 导出时生成人声分离伴奏")}</option></select></label>}
        {!hasVideo && format !== "krl" && <p className="form-hint error-text">{t("视频导出需要先上传视频。")}</p>}
      </div>
      <div className="dialog-actions"><button className="button text" onClick={onClose}>{t("关闭")}</button><button className="button filled" disabled={Boolean(active) || (format !== "krl" && !hasVideo)} onClick={() => onStart({ format, audio_track: audioTrack })}><Download size={17} />{t("开始导出")}</button></div>
      {active && <div className="export-active"><LoaderCircle className="spin" size={17} /><span>{active.message ? detail(active.message) : t("服务端导出中")} · {Math.round(active.progress * 100)}%</span><div className="analysis-progress export-progress"><i style={{ width: `${Math.max(2, active.progress * 100)}%` }} /></div><button className="icon-button compact" title={t("取消导出")} onClick={() => onCancel(active.id)}><Square size={15} /></button></div>}
      <div className="export-history"><h3>{t("导出历史")}</h3>{exportJobs.filter((job) => job.status === "SUCCEEDED").length === 0 ? <p className="muted">{t("暂无可下载文件")}</p> : exportJobs.filter((job) => job.status === "SUCCEEDED").map((job) => { const jobFormat = String(job.request?.format || "mp4"); return <div className="export-history-row" key={job.id}><span><strong>{String(job.result?.filename || t("导出文件"))}</strong><small>{jobFormat.toUpperCase()} · {jobFormat === "krl" ? t("Kirakara 工程") : job.request?.audio_track === "off_vocal" ? "OFF VOCAL" : "ON VOCAL"} · {new Date(job.created_at).toLocaleString(language)}</small></span><a className="icon-button" title={t("下载")} href={`/api/projects/${job.project_id}/exports/${job.id}/download`}><Download size={17} /></a><button className="icon-button" title={t("删除导出记录")} onClick={() => onDelete(job.id)}><Trash2 size={17} /></button></div>; })}</div>
    </section>
  </div>;
}

export function EditorPage({ id }: { id: string }) {
  const { t, detail } = usePreferences();
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
  const [expandedRubyId, setExpandedRubyId] = useState<string | null>(null);
  const [rubyPartTarget, setRubyPartTarget] = useState<{ lineId: string; unitId: string; index: number } | null>(null);
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
  const [lineMenu, setLineMenu] = useState<{ lineId: string; unitId: string | null; fromLyrics?: boolean; x: number; y: number } | null>(null);
  const [editingLineId, setEditingLineId] = useState<string | null>(null);
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
    if (!window.confirm(t("删除字幕样式预设“{0}”？", preset.name))) return;
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
    if (!window.confirm(t("删除这条导出记录及文件？"))) return;
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
    return <><AppHeader title={project?.name} /><div className="loading">{error ? detail(error) : t("正在打开工程…")}</div></>;
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
      ? { start: 0, end: lineCharacterCount(rubyTargetLine), title: t("整行 Ruby") }
      : (() => {
          const total = lineCharacterCount(rubyTargetLine);
          const baseStart = Math.max(0, Math.min(total, rubyEditorTarget.start));
          const baseEnd = Math.max(baseStart, Math.min(total, rubyEditorTarget.end));
          const overlappingGroups = rubyRanges(rubyTargetLine).filter((group) => group.start < baseEnd && group.end > baseStart);
          return {
            start: overlappingGroups.reduce((value, group) => Math.min(value, group.start), baseStart),
            end: overlappingGroups.reduce((value, group) => Math.max(value, group.end), baseEnd),
            title: t("所选 Unit 的 Ruby"),
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
            ? t("未设置时间")
            : rubyTargetLine.timing_precision === "line" ? t("行级时间") : t("精确时间")
          : targetUnit?.start_ms === null || targetUnit?.end_ms === null
            ? t("未设置时间")
            : targetUnit?.timing_source === "estimated" ? t("估算时间") : t("精确时间");
        const rangeGroups = rubyRanges(rubyTargetLine).filter((group) => rubyTargetRange && group.start < rubyTargetRange.end && group.end > rubyTargetRange.start);
        const sources = new Set<string>();
        let offset = 0;
        rubyTargetLine.units.forEach((unit) => {
          if (rangeGroups.some((group) => group.start === offset)) sources.add(unit.ruby_source || "manual");
          offset += Array.from(unit.surface).length;
        });
        const ruby = sources.size === 0 ? t("未注音") : sources.size === 1 ? `Ruby ${[...sources][0]}` : t("Ruby 混合来源");
        return `${timing} · ${ruby}`;
      })()
    : "";
  const sourceLabel =
    document.lyrics.source_type === "krl"
      ? t("逐字 KRL")
      : document.lyrics.source_type === "lrc"
        ? t("普通 LRC")
        : document.lyrics.source_type === "text"
          ? t("纯文本")
          : t("未添加");
  void historyVersion;

  return (
    <>
      <AppHeader title={project.name} />
      <main className="editor phase2-editor">
        <div className="editor-head">
          <a className="back-link" href="#/projects">
            <ArrowLeft size={18} />
            {t("项目列表")}
          </a>
          <div className="editor-actions">
            <button
              className="icon-button"
              title={t("撤销")}
              disabled={!historyRef.current.past.length}
              onClick={undo}
            >
              <Undo2 size={19} />
            </button>
            <button
              className="icon-button"
              title={t("重做")}
              disabled={!historyRef.current.future.length}
              onClick={redo}
            >
              <Redo2 size={19} />
            </button>
            <span className={`save-state ${dirty || error ? "pending" : ""}`}>
              <Save size={15} />
              {error
                ? t("未保存")
                : saving
                  ? t("保存中")
                  : dirty
                    ? t("等待保存")
                    : t("已保存")}
            </span>
            <button
              className="button tonal"
              onClick={() => setImportOpen(true)}
            >
              <FileText size={17} />
              {lines.length ? t("替换歌词") : t("添加歌词")}
            </button>
            <button className="button tonal" disabled={language === "cn" || !hasLyrics || pronunciationRunning || Boolean(activeJob) || analysisStarting} onClick={() => void runPronunciation("local")} title={language === "cn" ? t("中文工程不需要注音") : t("为未锁定单元生成本地日语读音")}>
              <WandSparkles size={17} />
              {pronunciationRunning ? t("注音中") : t("本地注音")}
            </button>
            <button className="button tonal" disabled={language === "cn" || !hasLyrics || pronunciationRunning || Boolean(activeJob) || analysisStarting} onClick={() => void runPronunciation("ai")} title={language === "cn" ? t("中文工程不需要注音") : t("使用 Whisper 粗识别结果生成读音；缺少结果时会先完成人声分离和粗识别")}>
              <WandSparkles size={17} />{t("AI 注音")}
            </button>
            <button className="button tonal" disabled={!hasVideo || !lines.length || Boolean(activeJob) || analysisStarting} onClick={() => void startTranscription()} title={t("仅运行 Whisper 人声粗识别")}>
              <AudioLines size={17} />{t("粗识别")}
            </button>
            {effectiveAlignmentBackend === "stable_ts" ? <>
              <button className="button tonal" disabled={!hasVideo || !lines.length || Boolean(activeJob) || analysisStarting} onClick={() => void startGlobalAlignment()} title={t("使用 AI 注音完整歌词生成行级时间")}>
                <AudioLines size={17} />{t("全局对齐")}
              </button>
              <button className="button tonal" disabled={!hasVideo || !lines.length || Boolean(activeJob) || analysisStarting} onClick={() => void startAlignment()} title={t("在全局对齐行范围内生成词/短语时间")}>
                <AudioLines size={17} />{t("词/短语精修")}
              </button>
            </> : <button className="button tonal" disabled={!hasVideo || !lines.length || Boolean(activeJob) || analysisStarting} onClick={() => void startFaKara()} title={t("使用 FA-Kara {0}对齐", t(faKaraModel === "yohane" ? "YoHane 微调模型" : "MMS_FA 基座模型"))}>
              <AudioLines size={17} />{t("FA-Kara 对齐")}
            </button>}
            <button
              className="button tonal"
              disabled={!hasVideo || !lines.length || Boolean(activeJob) || analysisStarting}
              onClick={() => void openFullAnalysis()}
            >
              <WandSparkles size={17} />{t("全曲分析")}
            </button>
            <button className="button filled" disabled={!lines.length || Boolean(activeJob) || analysisStarting} onClick={() => setExportOpen(true)} title={t("导出 Kirakara 完整效果")}>
              <Download size={17} />{t("导出")}
            </button>
            <label className={`button tonal ${uploading ? "disabled" : ""}`}>
              <Upload size={17} />
              {hasVideo ? t("替换视频") : t("选择视频")}
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
            {detail(error)}
          </div>
        )}
        {visibleJob && (
          <section className={`analysis-status ${visibleJob.status.toLowerCase()}`}>
            <span className="analysis-icon">
              {activeJob ? <LoaderCircle className="spin" size={18} /> : visibleJob.status === "FAILED" ? <AlertCircle size={18} /> : <AudioLines size={18} />}
            </span>
            <div className="analysis-copy">
              <strong>{visibleJob.type === "EXPORT" ? t("Kirakara 服务端导出") : visibleJob.type === "VOCAL_SEPARATION" ? t("人声分离") : visibleJob.type === "PRONUNCIATION" ? (Array.isArray(visibleJob.request?.steps) && visibleJob.request.steps.includes("transcription") ? t("人声分离 + Whisper + AI 注音") : t("注音")) : visibleJob.type === "FA_KARA_ALIGNMENT" ? t("FA-Kara 对齐") : visibleJob.type === "FULL_ANALYSIS" && visibleJob.request?.alignment_backend === "fa_kara" ? t("人声分离 + Whisper + FA-Kara") : t("人声分离 + Whisper 对齐")}</strong>
              <span>{detail(visibleJob.error_message || visibleJob.message || visibleJob.stage)}</span>
            </div>
            <div className="analysis-steps">
              {(visibleJob.steps?.length ? visibleJob.steps : [{ key: visibleJob.stage, label: visibleJob.type, status: activeJob ? "running" : "completed", progress: visibleJob.progress }]).map((step) => (
                <div className="analysis-step" key={step.key}>
                  <span>{detail(step.label)}</span>
                  <div className="analysis-progress" aria-label={`${detail(step.label)} ${Math.round(step.progress * 100)}%`}><i style={{ width: `${Math.max(step.progress ? 2 : 0, step.progress * 100)}%` }} /></div>
                  <b>{Math.round(step.progress * 100)}%</b>
                </div>
              ))}
            </div>
            {visibleJob.status === "FAILED" && (
              <button className="icon-button compact" title={t("重试任务")} onClick={() => void retryJob(visibleJob.id)}>
                <RotateCcw size={16} />
              </button>
            )}
            {activeJob && <button className="icon-button compact" title={t("取消任务")} onClick={() => void cancelJob(activeJob.id)}><Square size={16} /></button>}
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
                <h2>{t("添加视频")}</h2>
                <label className="button filled">
                  <Upload size={17} />
                  {t("选择 MP4")}
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
                <span>{t("正在处理视频")}</span>
                <div className="progress-track">
                  <div
                    className="progress-value"
                    style={{ width: `${uploadProgress}%` }}
                  />
                </div>
                <strong>{uploadProgress}%</strong>
                <button
                  className="icon-button compact"
                  title={t("取消上传")}
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
                <h3>{t("工程信息")}</h3>
              </div>
              <label className="field-label">
                {t("项目名称")}
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
                {t("歌词语言")}
                <select value={language} onChange={(event) => changeLanguage(event.target.value as ProjectLanguage)}>
                  <option value="jp">{t("JP · 日语")}</option>
                  <option value="cn">{t("CN · 中文")}</option>
                </select>
              </label>
              <div className="field-row">
                <label className="field-label">
                  {t("歌曲名")}
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
                  {t("歌手")}
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
                <h2>{t("歌词")}</h2>
                <span>
                  {sourceLabel} · {t("{0} 行", lines.length)}
                </span>
              </div>
              <button
                className="icon-button"
                title={lines.length ? t("替换歌词") : t("添加歌词")}
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
                    title={t("右键修改歌词，或拖到时间轴设置时间")}
                    onContextMenu={(event) => {
                      event.preventDefault();
                      if (activeJob) return;
                      setLineMenu({ lineId: line.id, unitId: null, fromLyrics: true, x: event.clientX, y: event.clientY });
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
                        {line.timing_precision === "line" ? t("· 行级时间") : ""}
                        {line.units.some((unit) => unit.timing_confidence !== null && unit.timing_confidence < 0.55) ? t("· 低置信度") : ""}
                      </small>
                    </span>
                    <GripVertical className="line-drag-handle" size={16} />
                  </button>
                );
              })}
              {!lines.length && (
                <div className="lyrics-empty">
                  <FileText size={28} />
                  <span>{t("尚未添加歌词")}</span>
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
          expandedRubyId={expandedRubyId}
          onUpdateRubyTiming={(lineId, unitId, start, end, timing) => {
            const current = documentRef.current;
            if (!current) return;
            const updated = current.lyrics.lines.map((line) => line.id === lineId ? retimeRubyGroup(line, unitId, start, end, timing) : line);
            replaceDocument({ ...current, lyrics: { ...current.lyrics, lines: updated } }, false);
          }}
          onOpenRubyPart={(lineId, unitId, index) => setRubyPartTarget({ lineId, unitId, index })}
          rubyEnabled={language === "jp"}
          onOpenEditor={setEditingId}
          onDropLine={placeLine}
          onOpenContextMenu={(lineId, unitId, lineLevel, x, y) => {
            if (!activeJob) setLineMenu({ lineId, unitId: lineLevel ? null : unitId, x, y });
          }}
        />
      </main>
      <a className="fab" title={t("设置")} href={settingsHref()}>
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
            throw new Error(t("工程内容已发生变化，请关闭后重新打开拆分页面。"));
          }
          replaceDocument(splitLyricUnit(current, splitTarget.lineId, splitTarget.unitId, ranges), true);
          selectUnit(splitTarget.unitId);
          setSplitTarget(null);
        }} />;
      })()}
      {rubyPartTarget && (() => {
        const line = lines.find((item) => item.id === rubyPartTarget.lineId);
        const unitIndex = line?.units.findIndex((item) => item.id === rubyPartTarget.unitId) ?? -1;
        if (!line || unitIndex < 0) return null;
        const unit = line.units[unitIndex];
        const bounds = timedBounds(line.units.slice(unitIndex, rubyGroupEnd(line.units, unitIndex)));
        if (!bounds) return null;
        const timing = initialRubyTiming(unit, bounds.start_ms, bounds.end_ms);
        const index = rubyPartTarget.index;
        const part = timing[index];
        if (!part) return null;
        const min = timing[index - 1]?.end_ms ?? bounds.start_ms;
        const max = timing[index + 1]?.start_ms ?? bounds.end_ms;
        return <div className="scrim">
          <form className="dialog" role="dialog" aria-modal="true" aria-labelledby="ruby-timing-title" onSubmit={(event) => {
            event.preventDefault();
            const data = new FormData(event.currentTarget);
            const start = Number(data.get("start"));
            const end = Number(data.get("end"));
            if (end < start) {
              const field = event.currentTarget.elements.namedItem("end") as HTMLInputElement;
              field.setCustomValidity(t("结束时间不能早于开始时间"));
              field.reportValidity();
              return;
            }
            const current = documentRef.current;
            if (!current) return;
            const edited = timing.map((item, i) => i === index ? { ...item, start_ms: start, end_ms: end } : item);
            const updated = current.lyrics.lines.map((item) => item.id === line.id
              ? retimeRubyGroup(item, unit.id, bounds.start_ms, bounds.end_ms, edited) : item);
            replaceDocument({ ...current, lyrics: { ...current.lyrics, lines: updated } }, true);
            setRubyPartTarget(null);
          }} onInput={(event) => (event.currentTarget.elements.namedItem("end") as HTMLInputElement).setCustomValidity("")}>
            <div className="dialog-head"><h2 id="ruby-timing-title">{t("微调 Ruby「{0}」", part.text)}</h2></div>
            <label className="field-label">{t("开始时间（ms）")}<input type="number" name="start" autoFocus required min={min} max={max} defaultValue={part.start_ms} /></label>
            <label className="field-label">{t("结束时间（ms）")}<input type="number" name="end" required min={min} max={max} defaultValue={part.end_ms} /></label>
            <div className="dialog-actions">
              <button type="button" className="button outlined" onClick={() => setRubyPartTarget(null)}>{t("取消")}</button>
              <button className="button filled" type="submit">{t("保存")}</button>
            </div>
          </form>
        </div>;
      })()}
      {editingLineId && (() => {
        const line = lines.find((item) => item.id === editingLineId);
        if (!line) return null;
        return <LineEditDialog key={line.id} line={line} onClose={() => setEditingLineId(null)} onSave={(text) => {
          const current = documentRef.current;
          if (!current) return;
          const source = current.lyrics.lines.find((item) => item.id === line.id);
          if (!source) return;
          const updated = replaceLyricLineText(source, text, createClientId);
          if (updated !== source) {
            const lines = current.lyrics.lines.map((item) => item.id === updated.id ? updated : item);
            replaceDocument({ ...current, lyrics: { ...current.lyrics, lines } }, true);
            if (source.units.some((unit) => unit.id === expandedRubyId)) setExpandedRubyId(null);
            selectLine(updated);
          }
          setEditingLineId(null);
        }} />;
      })()}
      {lineMenu && (() => {
        const menuLine = lines.find((line) => line.id === lineMenu.lineId);
        if (!menuLine) return null;
        const menuUnit = lineMenu.unitId ? menuLine?.units.find((unit) => unit.id === lineMenu.unitId) : null;
        return <div className="line-context-menu" style={{ left: Math.min(lineMenu.x, window.innerWidth - 250), top: Math.min(lineMenu.y, window.innerHeight - 176) }} onPointerDown={(event) => event.stopPropagation()}>
          <strong>{lineMenu.fromLyrics ? t("歌词") : t("时间单元")}</strong>
          {lineMenu.fromLyrics && <button onClick={() => {
            setLineMenu(null);
            setEditingLineId(menuLine.id);
          }}><Pencil size={15} />{t("修改歌词")}</button>}
          {language === "jp" && menuUnit?.ruby && menuLine?.timing_precision !== "line" && (
            <button onClick={() => {
              setExpandedRubyId(expandedRubyId === menuUnit.id ? null : menuUnit.id);
              setLineMenu(null);
            }}>{expandedRubyId === menuUnit.id ? t("还原为汉字词组") : t("微调 Ruby 时间轴")}</button>
          )}
          <button disabled={menuLine.timing_precision === "line"} onClick={() => { setLineMenu(null); collapseLine(lineMenu.lineId); }}>{t("还原为整句时间单元")}</button>
          <button disabled={!hasVideo || menuLine.start_ms === null || menuLine.end_ms === null} onClick={() => { setLineMenu(null); effectiveAlignmentBackend === "fa_kara" ? void startFaKara([lineMenu.lineId]) : void startAlignment([lineMenu.lineId]); }}>
            {effectiveAlignmentBackend === "fa_kara" ? t("用 FA-Kara 重新识别此句") : t("用 stable-ts 重新识别此句")}
          </button>
          {menuUnit && Array.from(menuUnit.surface).length > 1 && <button onClick={() => { setLineMenu(null); setSplitTarget({ lineId: lineMenu.lineId, unitId: menuUnit.id }); }}><Scissors size={15} />{t("拆分此 Unit")}</button>}
        </div>;
      })()}
      {fullAnalysisOpen && (
        <div className="scrim">
          <section className="dialog full-analysis-dialog" role="dialog" aria-modal="true" aria-labelledby="full-analysis-title">
            <div className="dialog-head"><div><h2 id="full-analysis-title">{t("确认全曲分析")}</h2><p className="muted">{t("将按勾选顺序执行；跳过未完成流程会被后端拒绝。")}</p></div><button className="icon-button" title={t("关闭")} onClick={() => setFullAnalysisOpen(false)}><X size={18} /></button></div>
            <div className="pipeline-preview">
              {(effectiveAlignmentBackend === "fa_kara" ? (language === "cn" ? [
                ["separation", t("人声分离"), t("生成 Whisper / 对齐使用的 vocals")],
                ["transcription", t("Whisper 人声粗识别"), t("保存实际演唱的 segment 文本和粗时间")],
                ["fa_kara", t("FA-Kara 对齐 · {0}", faKaraModel === "yohane" ? "YoHane" : "MMS_FA"), t("使用中文拼音生成词/字级时间")],
              ] : [
                ["separation", t("人声分离"), t("生成 Whisper / 对齐使用的 vocals")],
                ["transcription", t("Whisper 人声粗识别"), t("保存实际演唱的 segment 文本和粗时间")],
                ["pronunciation", t("AI 注音"), t("结合完整歌词与 Whisper segment 生成 Ruby")],
                ["fa_kara", t("FA-Kara 对齐 · {0}", faKaraModel === "yohane" ? "YoHane" : "MMS_FA"), t("一次生成行级与词/短语时间")],
              ]) : [
                ["separation", t("人声分离"), t("生成 Whisper / 对齐使用的 vocals")],
                ["transcription", t("Whisper 人声粗识别"), t("保存实际演唱的 segment 文本和粗时间")],
                ["pronunciation", t("AI 注音"), t("结合完整歌词与 Whisper segment 生成 Ruby")],
                ["global_alignment", t("stable-ts 全局对齐"), t("只写入可单独观察的行级时间")],
                ["alignment", t("stable-ts 词/短语精修"), t("在全局对齐行范围内写入 unit 时间")],
              ]).map(([key, label, description]) => {
                const analysis = (document as ProjectDocument & { analysis?: Record<string, { status?: string }> }).analysis || {};
                const complete = key === "separation" ? document.media.waveform_source === "vocals" : key === "transcription" ? analysis.transcription?.status === "completed" : key === "pronunciation" ? Boolean(analysis.pronunciation?.status === "completed" || document.lyrics.lines.every((line) => line.units.every((unit) => !unit.surface.match(/[一-龯]/) || unit.ruby))) : key === "global_alignment" ? analysis.global_alignment?.status === "completed" : key === "alignment" ? analysis.alignment?.status === "completed" : analysis.fa_kara?.status === "completed";
                return <label className="pipeline-step" key={key}><input type="checkbox" checked={Boolean(fullSteps[key])} onChange={(event) => setFullSteps((current) => ({ ...current, [key]: event.target.checked }))} /><span><strong>{label}</strong><small>{complete ? t("已完成，可跳过") : description}</small></span><em className={complete ? "complete" : "pending"}>{complete ? t("已完成") : t("待执行")}</em></label>;
              })}
            </div>
            <div className="dialog-actions"><button className="button text" onClick={() => setFullAnalysisOpen(false)}>{t("取消")}</button><button className="button filled" disabled={!Object.values(fullSteps).some(Boolean)} onClick={() => void startFullAnalysis()}><WandSparkles size={17} />{t("开始分析")}</button></div>
          </section>
        </div>
      )}
      {exportOpen && <ExportDialog hasVideo={hasVideo} jobs={jobs} onClose={() => setExportOpen(false)} onStart={(payload) => void startExport(payload)} onCancel={(jobId) => void cancelJob(jobId)} onDelete={(jobId) => void deleteExport(jobId)} />}
      {workflowNotice && (
        <div className="scrim">
          <section className="dialog workflow-notice" role="alertdialog" aria-modal="true" aria-labelledby="workflow-notice-title">
            <div className="dialog-head"><h2 id="workflow-notice-title">{t("暂时无法开始")}</h2><button className="icon-button" title={t("关闭")} onClick={() => setWorkflowNotice(null)}><X size={18} /></button></div>
            <p className="workflow-notice-copy">{detail(workflowNotice)}</p>
            <div className="dialog-actions"><button className="button filled" onClick={() => setWorkflowNotice(null)}>{t("知道了")}</button></div>
          </section>
        </div>
      )}
      {pendingLanguage && (
        <div className="scrim">
          <section className="dialog workflow-notice" role="alertdialog" aria-modal="true" aria-labelledby="language-change-title">
            <div className="dialog-head"><h2 id="language-change-title">{t("确认切换歌词语言")}</h2><button className="icon-button" title={t("关闭")} onClick={() => setPendingLanguage(null)}><X size={18} /></button></div>
            <p className="workflow-notice-copy">{t("切换语言会清除当前工程的 Ruby、Ruby 范围和相关分析结果。")}</p>
            <div className="dialog-actions"><button className="button text" onClick={() => setPendingLanguage(null)}>{t("取消")}</button><button className="button filled" onClick={confirmLanguageChange}><Check size={17} />{t("切换并清除 Ruby")}</button></div>
          </section>
        </div>
      )}
      {missingRubyDialog && (
        <div className="scrim">
          <section className="dialog workflow-notice" role="alertdialog" aria-modal="true" aria-labelledby="missing-ruby-title">
            <div className="dialog-head"><h2 id="missing-ruby-title">{missingRubyDialog.resume ? t("全曲分析需要补充注音") : t("无法开始对齐")}</h2><button className="icon-button" title={t("关闭")} onClick={() => setMissingRubyDialog(null)}><X size={18} /></button></div>
            <p className="workflow-notice-copy">{missingRubyDialog.resume ? t("AI 注音完成后，以下日文汉字仍未注音。取消将中断本次全曲分析；使用本地注音后会继续对齐。") : t("以下日文汉字尚未注音：")}</p>
            <ul className="missing-ruby-list">{missingRubyDialog.lines.map((item) => <li key={item.lineIndex}>{t("第 {0} 行：{1}", item.lineIndex + 1, item.characters)}</li>)}</ul>
            <div className="dialog-actions"><button className="button text" onClick={() => setMissingRubyDialog(null)}>{missingRubyDialog.resume ? t("取消全曲分析") : t("取消")}</button><button className="button filled" onClick={() => void fillMissingRubyAndResume()}><WandSparkles size={17} />{t("使用本地注音")}</button></div>
          </section>
        </div>
      )}
    </>
  );
}
