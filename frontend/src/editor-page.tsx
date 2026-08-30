import {
  AlertCircle,
  AudioLines,
  ArrowLeft,
  Check,
  Cog,
  FileText,
  FileVideo,
  GripVertical,
  LoaderCircle,
  RotateCcw,
  Redo2,
  Save,
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
} from "./editor-types";
import { api, formatTime, parseTime } from "./editor-types";
import { LyricsImportDialog } from "./lyrics-import-dialog";
import { TimelineCanvas } from "./timeline-canvas";
import "./editor.css";

function settingsHref() {
  return `#/settings?returnTo=${encodeURIComponent(location.hash.slice(1) || "/projects")}`;
}
function clone<T>(value: T): T {
  return structuredClone(value);
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

function UnitEditDialog({
  unit,
  onClose,
  onSave,
}: {
  unit: LyricUnit;
  onClose: () => void;
  onSave: (patch: Partial<LyricUnit>) => void;
}) {
  const [surface, setSurface] = useState(unit.surface);
  const [ruby, setRuby] = useState(unit.ruby || "");
  const [ruby2, setRuby2] = useState(unit.ruby_2 || "");
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
        <label className="field-label">
          Ruby
          <input
            value={ruby}
            onChange={(event) => setRuby(event.target.value)}
          />
        </label>
        <label className="field-label">
          第二 Ruby
          <input
            value={ruby2}
            onChange={(event) => setRuby2(event.target.value)}
          />
        </label>
        <div className="dialog-actions">
          <button className="button text" onClick={onClose}>
            取消
          </button>
          <button
            className="button filled"
            disabled={!surface}
            onClick={() =>
              onSave({
                surface,
                ruby: ruby || null,
                ruby_2: ruby2 || null,
                ruby_source: ruby || ruby2 ? "manual" : "none",
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

function UnitInspector({
  unit,
  onCommit,
}: {
  unit: LyricUnit;
  onCommit: (patch: Partial<LyricUnit>) => void;
}) {
  const [start, setStart] = useState(formatTime(unit.start_ms));
  const [end, setEnd] = useState(formatTime(unit.end_ms));
  const [surface, setSurface] = useState(unit.surface);
  const [ruby, setRuby] = useState(unit.ruby || "");
  useEffect(() => {
    setStart(formatTime(unit.start_ms));
    setEnd(formatTime(unit.end_ms));
    setSurface(unit.surface);
    setRuby(unit.ruby || "");
  }, [unit]);
  function apply() {
    const startMs = parseTime(start),
      endMs = parseTime(end);
    if (startMs === null || endMs === null || endMs <= startMs || !surface)
      return;
    onCommit({
      start_ms: startMs,
      end_ms: endMs,
      surface,
      ruby: ruby || null,
      ruby_source: ruby ? "manual" : "none",
      timing_source: "manual",
      timing_confidence: 1,
    });
  }
  return (
    <section className="unit-inspector">
      <div className="section-title">
        <h3>所选单元</h3>
        <span>
          {unit.start_ms === null || unit.end_ms === null
            ? "未设置时间"
            : unit.timing_source === "estimated"
              ? "估算时间"
              : "精确时间"}
          {unit.ruby ? ` · Ruby ${unit.ruby_source || "manual"}` : " · 未注音"}
        </span>
      </div>
      <label className="field-label">
        正文
        <input
          value={surface}
          onChange={(event) => setSurface(event.target.value)}
          onKeyDown={(event) => event.key === "Enter" && apply()}
        />
      </label>
      <label className="field-label">
        Ruby
        <input
          value={ruby}
          onChange={(event) => setRuby(event.target.value)}
          onKeyDown={(event) => event.key === "Enter" && apply()}
        />
      </label>
      <div className="time-fields">
        <label className="field-label">
          开始
          <input
            inputMode="decimal"
            value={start}
            onChange={(event) => setStart(event.target.value)}
            onKeyDown={(event) => event.key === "Enter" && apply()}
          />
        </label>
        <label className="field-label">
          结束
          <input
            inputMode="decimal"
            value={end}
            onChange={(event) => setEnd(event.target.value)}
            onKeyDown={(event) => event.key === "Enter" && apply()}
          />
        </label>
      </div>
      <button className="button tonal full" onClick={apply}>
        <Check size={16} />
        应用精确值
      </button>
    </section>
  );
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
  const [importOpen, setImportOpen] = useState(false),
    [isPlaying, setIsPlaying] = useState(false);
  const [uploading, setUploading] = useState(false),
    [uploadProgress, setUploadProgress] = useState(0);
  const [jobs, setJobs] = useState<AnalysisJob[]>([]),
    [analysisStarting, setAnalysisStarting] = useState(false);
  const [pronunciationRunning, setPronunciationRunning] = useState(false);
  const [lineMenu, setLineMenu] = useState<{ lineId: string; x: number; y: number } | null>(null);
  const uploadRequest = useRef<XMLHttpRequest | null>(null),
    videoRef = useRef<HTMLVideoElement>(null);
  const appliedJobs = useRef(new Set<string>());
  const appliedVocalWaveforms = useRef(new Set<string>());

  useEffect(() => {
    void Promise.all([
      api<Project>(`/projects/${id}`),
      api<{ revision: number; document: ProjectDocument }>(
        `/projects/${id}/document`,
      ),
    ])
      .then(([loadedProject, loaded]) => {
        projectRef.current = loadedProject;
        documentRef.current = loaded.document;
        setProject(loadedProject);
        setDocument(loaded.document);
      })
      .catch(() => setError("无法打开工程，请确认后端服务已启动。"));
  }, [id]);

  const reloadFromServer = useCallback(async () => {
    if (dirtyRef.current) return;
    const [loadedProject, loaded] = await Promise.all([
      api<Project>(`/projects/${id}`),
      api<{ revision: number; document: ProjectDocument }>(`/projects/${id}/document`),
    ]);
    projectRef.current = loadedProject;
    documentRef.current = loaded.document;
    setProject(loadedProject);
    setDocument(loaded.document);
  }, [id]);

  useEffect(() => {
    let disposed = false;
    const poll = async () => {
      try {
        const loaded = await api<AnalysisJob[]>(`/jobs?project_id=${encodeURIComponent(id)}&limit=12`);
        if (disposed) return;
        setJobs(loaded);
        const completed = loaded.find((job) => job.status === "SUCCEEDED" && !appliedJobs.current.has(job.id));
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
        if (completed) {
          appliedJobs.current.add(completed.id);
          await reloadFromServer();
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
    setSelectedId(line.units[0].id);
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
    setSelectedId(result.document.lyrics.lines[0]?.units[0]?.id || null);
    if (projectRef.current) {
      const next = { ...projectRef.current, revision: result.revision };
      projectRef.current = next;
      setProject(next);
    }
    if (result.job) setJobs((current) => [result.job!, ...current]);
  }

  async function startTranscription(lineIds?: string[]) {
    if (!documentRef.current?.media.video_filename) {
      setError("请先上传视频，再运行 Whisper 识别。");
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

  async function runPronunciation(mode: "local" | "ai", lineIds?: string[]) {
    if (!documentRef.current?.lyrics.lines.length) return;
    setPronunciationRunning(true);
    setError(null);
    try {
      const revision = await saveNow();
      const unitIds = selectedId && !lineIds?.length ? [selectedId] : [];
      const result = await api<{ revision: number; document: ProjectDocument; summary?: { fallback_reason?: string } }>(
        `/projects/${id}/pronunciation/${mode}`,
        { method: "POST", body: JSON.stringify({ revision, line_ids: lineIds || [], unit_ids: unitIds, mode, overwrite_policy: "unlocked_only" }) },
      );
      const before = documentRef.current;
      if (before) {
        historyRef.current.past.push(before);
        historyRef.current.past = historyRef.current.past.slice(-200);
        historyRef.current.future = [];
        setHistoryVersion((value) => value + 1);
      }
      documentRef.current = result.document;
      setDocument(result.document);
      if (projectRef.current) {
        const next = { ...projectRef.current, revision: result.revision };
        projectRef.current = next;
        setProject(next);
      }
      markDirty(false);
      if (result.summary?.fallback_reason) setError(`AI 不可用，已使用本地注音：${result.summary.fallback_reason}`);
    } catch (reason) {
      const status = (reason as Error & { status?: number })?.status;
      setError(status === 409 ? "工程版本已变化，请保存后重试。" : "注音失败，现有歌词和 Ruby 未被覆盖。 ");
    } finally {
      setPronunciationRunning(false);
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
  const activeJob = jobs.find((job) => ["QUEUED", "PREPARING", "RUNNING"].includes(job.status));
  const visibleJob = activeJob || jobs[0] || null;
  const selected =
    lines
      .flatMap((line) => line.units.map((unit) => ({ line, unit })))
      .find(({ unit }) => unit.id === selectedId) || null;
  const editing = editingId
    ? lines
        .flatMap((line) => line.units)
        .find((unit) => unit.id === editingId) || null
    : null;
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
            <button className="button tonal" disabled={!lines.length || pronunciationRunning} onClick={() => void runPronunciation("local")} title="为未锁定单元生成本地日语读音">
              <WandSparkles size={17} />
              {pronunciationRunning ? "注音中" : "本地注音"}
            </button>
            <button className="button tonal" disabled={!lines.length || pronunciationRunning} onClick={() => void runPronunciation("ai")} title="使用当前 AI profile 生成读音，失败时按设置降级">
              <WandSparkles size={17} />AI 注音
            </button>
            <button
              className="button tonal"
              disabled={!hasVideo || !lines.length || Boolean(activeJob) || analysisStarting}
              onClick={() => void startTranscription()}
            >
              {analysisStarting || activeJob ? <LoaderCircle className="spin" size={17} /> : <WandSparkles size={17} />}
              {activeJob ? "分析中" : "全曲分析"}
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
              <strong>{visibleJob.type === "VOCAL_SEPARATION" ? "KARA2 双 stem" : "KARA2 + Whisper 对齐"}</strong>
              <span>{visibleJob.error_message || visibleJob.message || visibleJob.stage}</span>
            </div>
            <div className="analysis-progress" aria-label={`进度 ${Math.round(visibleJob.progress * 100)}%`}>
              <i style={{ width: `${Math.max(2, visibleJob.progress * 100)}%` }} />
            </div>
            <b>{Math.round(visibleJob.progress * 100)}%</b>
            {visibleJob.status === "FAILED" && (
              <button className="icon-button compact" title="重试任务" onClick={() => void retryJob(visibleJob.id)}>
                <RotateCcw size={16} />
              </button>
            )}
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
                      setLineMenu({ lineId: line.id, x: event.clientX, y: event.clientY });
                    }}
                    onDragStart={(event) => {
                      event.dataTransfer.effectAllowed = "move";
                      event.dataTransfer.setData(
                        "application/x-nicokara-lyric-line",
                        line.id,
                      );
                      event.dataTransfer.setData("text/plain", line.id);
                      setSelectedId(line.units[0]?.id || null);
                    }}
                    onClick={() => setSelectedId(line.units[0]?.id || null)}
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
            {selected && (
              <UnitInspector
                unit={selected.unit}
                onCommit={(patch) =>
                  updateSelected(
                    selected.line.id,
                    selected.unit.id,
                    patch,
                    true,
                  )
                }
              />
            )}
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
          onSelect={setSelectedId}
          onSeek={seek}
          onSeekBy={seekBy}
          onTogglePlayback={togglePlayback}
          onBeginEdit={beginEdit}
          onUpdateUnit={(lineId, unitId, patch) =>
            updateSelected(lineId, unitId, patch)
          }
          onOpenEditor={setEditingId}
          onDropLine={placeLine}
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
      {lineMenu && (() => {
        const index = lines.findIndex((line) => line.id === lineMenu.lineId);
        const remaining = lines.slice(index).map((line) => line.id);
        return <div className="line-context-menu" style={{ left: lineMenu.x, top: lineMenu.y }} onPointerDown={(event) => event.stopPropagation()}>
          <strong>Whisper 重新识别</strong>
          <button onClick={() => { setLineMenu(null); void startTranscription([lineMenu.lineId]); }}>当前行</button>
          <button onClick={() => { setLineMenu(null); void startTranscription(remaining); }}>从此处到结尾</button>
          <button onClick={() => { setLineMenu(null); void startTranscription(); }}>全曲</button>
        </div>;
      })()}
    </>
  );
}
