import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { ArrowLeft, Save } from "lucide-react";
import { api } from "./editor-types";
import { EditorPage } from "./editor-page";
import { ProjectList } from "./project-list";
import "./styles.css";
import "./upload.css";
import "./material-overrides.css";

function SettingsPage() {
  const params = new URLSearchParams(location.hash.split("?")[1] || "");
  const returnTo = params.get("returnTo") || "/projects";
  const [values, setValues] = useState<Record<string, unknown>>({ autosave_interval_seconds: 15, theme: "system", font_family: "Noto Sans JP", font_size_max: 64 });
  const [saved, setSaved] = useState(false);
  useEffect(() => { void api<Record<string, unknown>>("/settings").then((loaded) => setValues((current) => ({ ...current, ...loaded }))); }, []);
  async function saveSettings() { await api("/settings", { method: "PUT", body: JSON.stringify({ values }) }); setSaved(true); window.setTimeout(() => setSaved(false), 1800); }
  return <><header className="topbar"><a href="#/projects" className="brand"><span className="brand-mark">N</span><span>Nicokara Studio</span></a><span className="top-title">设置</span></header><main className="settings-layout"><nav className="settings-nav"><a className="back-link" href={`#${returnTo}`}><ArrowLeft size={18} />返回</a><p className="eyebrow">设置</p>{["常规", "AI 模型与 API", "注音与提示词", "人声分离", "Whisper", "时间轴与打轴", "字幕与样式", "导出", "存储与任务", "诊断与关于"].map((label, index) => <button className={index === 0 ? "nav-item active" : "nav-item"} key={label}>{label}</button>)}</nav><section className="settings-content"><div className="page-heading"><div><h1>设置</h1><p className="muted">全局默认只影响新工程，已有工程保留自己的配置快照。</p></div><button className="button filled" onClick={() => void saveSettings()}><Save size={17} />{saved ? "已保存" : "保存更改"}</button></div><div className="settings-section"><h2>常规</h2><div className="settings-grid"><label>自动保存间隔（秒）<input type="number" min="5" max="300" value={String(values.autosave_interval_seconds)} onChange={(event) => setValues({ ...values, autosave_interval_seconds: Number(event.target.value) })} /><small>范围 5–300 秒 · 全局设置</small></label><label>主题<select value={String(values.theme)} onChange={(event) => setValues({ ...values, theme: event.target.value })}><option value="system">跟随系统</option><option value="light">浅色</option><option value="dark">深色</option></select><small>全局设置</small></label></div></div><div className="settings-section"><h2>字幕与样式</h2><div className="settings-grid"><label>字体<input value={String(values.font_family)} onChange={(event) => setValues({ ...values, font_family: event.target.value })} /><small>新工程默认字体</small></label><label>最大字号<input type="number" min="12" max="180" value={String(values.font_size_max)} onChange={(event) => setValues({ ...values, font_size_max: Number(event.target.value) })} /><small>范围 12–180 px</small></label></div></div></section></main></>;
}

function App() {
  const [hash, setHash] = useState(location.hash);
  useEffect(() => { const update = () => setHash(location.hash); addEventListener("hashchange", update); return () => removeEventListener("hashchange", update); }, []);
  const path = hash.replace(/^#/, "") || "/projects";
  if (path.startsWith("/settings")) return <SettingsPage />;
  const match = path.match(/^\/projects\/([^/]+)\/editor/);
  if (match) return <EditorPage id={match[1]} />;
  return <ProjectList />;
}

createRoot(document.getElementById("root")!).render(<React.StrictMode><App /></React.StrictMode>);
