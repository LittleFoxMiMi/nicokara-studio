import { FileText, Upload, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api } from "./editor-types";

type ImportFormat = "auto" | "text" | "lrc" | "krl";
type Detection = { format: Exclude<ImportFormat, "auto">; confidence: number; reasons: string[] };

const labels = { text: "纯文本", lrc: "普通 LRC", krl: "逐字 LRC / KRL" };

export function LyricsImportDialog({
  projectId,
  onClose,
  onImport,
}: {
  projectId: string;
  onClose: () => void;
  onImport: (content: string, filename: string | null, format: ImportFormat) => Promise<void>;
}) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const detectionTimerRef = useRef<number | null>(null);
  const detectionVersionRef = useRef(0);
  const [filename, setFilename] = useState<string | null>(null);
  const [format, setFormat] = useState<ImportFormat>("auto");
  const [detection, setDetection] = useState<Detection | null>(null);
  const [hasContent, setHasContent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    return () => {
      detectionVersionRef.current += 1;
      if (detectionTimerRef.current !== null)
        window.clearTimeout(detectionTimerRef.current);
    };
  }, []);

  function scheduleDetection(content: string, sourceFilename: string | null) {
    const version = ++detectionVersionRef.current;
    if (detectionTimerRef.current !== null)
      window.clearTimeout(detectionTimerRef.current);
    const present = Boolean(content.trim());
    setHasContent((current) => (current === present ? current : present));
    if (!present) {
      setDetection(null);
      return;
    }
    detectionTimerRef.current = window.setTimeout(() => {
      void api<Detection>(`/projects/${projectId}/lyrics/detect`, {
        method: "POST",
        body: JSON.stringify({ content, filename: sourceFilename }),
      })
        .then((result) => {
          if (detectionVersionRef.current === version) setDetection(result);
        })
        .catch(() => {
          if (detectionVersionRef.current === version) setDetection(null);
        });
    }, 400);
  }

  async function readFile(file?: File) {
    if (!file) return;
    const content = await file.text();
    setFilename(file.name);
    if (textareaRef.current) textareaRef.current.value = content;
    scheduleDetection(content, file.name);
  }

  async function submit() {
    const content = textareaRef.current?.value || "";
    if (!content.trim()) return;
    setBusy(true); setError(null);
    try { await onImport(content, filename, format); onClose(); }
    catch (reason) { setError(reason instanceof Error && reason.message.includes("revision_conflict") ? "工程已在其他窗口更新，请刷新后重试。" : "歌词导入失败，请检查格式。"); }
    finally { setBusy(false); }
  }

  return <div className="scrim" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
    <section className="dialog import-dialog" role="dialog" aria-modal="true" aria-labelledby="lyrics-import-title">
      <div className="dialog-head"><div><h2 id="lyrics-import-title">添加歌词</h2>{detection && <span className="detection-chip">已识别：{labels[detection.format]}</span>}</div><button className="icon-button" title="关闭" aria-label="关闭" onClick={onClose}><X size={20} /></button></div>
      <label className="file-picker"><Upload size={18} /><span>{filename || "选择 TXT、LRC 或 KRL"}</span><input type="file" accept=".txt,.lrc,.krl,text/plain" hidden onChange={(event) => void readFile(event.target.files?.[0])} /></label>
      <label className="field-label">歌词内容<textarea ref={textareaRef} autoFocus spellCheck={false} onInput={(event) => { const value = event.currentTarget.value; setFilename(null); scheduleDetection(value, null); }} placeholder="在这里粘贴歌词" /></label>
      <label className="field-label">导入格式<select value={format} onChange={(event) => setFormat(event.target.value as ImportFormat)}><option value="auto">自动检测{detection ? `（${labels[detection.format]}）` : ""}</option><option value="text">纯文本</option><option value="lrc">普通 LRC</option><option value="krl">逐字 LRC / KRL</option></select></label>
      {error && <div className="inline-error" role="alert">{error}</div>}
      <div className="dialog-actions"><button className="button text" onClick={onClose}>取消</button><button className="button filled" disabled={!hasContent || busy} onClick={() => void submit()}><FileText size={17} />{busy ? "正在导入" : "导入歌词"}</button></div>
    </section>
  </div>;
}
