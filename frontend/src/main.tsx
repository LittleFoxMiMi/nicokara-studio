import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { ArrowLeft, Cpu, KeyRound, Save, Send } from "lucide-react";
import { api } from "./editor-types";
import { EditorPage } from "./editor-page";
import { ProjectList } from "./project-list";
import "./styles.css";
import "./upload.css";
import "./material-overrides.css";

type Capabilities = {
  separator: { available: boolean; version: string | null; devices: string[]; providers: string[]; default_model: string; models: { name: string; size: number }[] };
  whisper: { available: boolean; version: string | null; devices: string[]; models: string[]; default_model: string; installed_models: string[] };
};
type AIProfile = { id: string; name: string; api_format: string; base_url: string; model: string; temperature: number; max_tokens: number; timeout_seconds: number; max_chars_per_request: number; retry_count: number; thinking_effort: "off" | "minimal" | "low" | "medium" | "high" | "xhigh"; thinking_enabled: boolean; custom_payload: Record<string, unknown>; has_api_key: boolean; api_key_suffix: string | null };
type PromptPreset = { id: string; name: string; system_prompt: string; user_template: string; builtin?: boolean };
type EditableProfile = Omit<Partial<AIProfile>, "thinking_effort"> & { thinking_effort?: string; api_key?: string };

const thinkingOptions = [
  ["off", "关闭"],
  ["minimal", "Minimal"],
  ["low", "Low"],
  ["medium", "Medium"],
  ["high", "High"],
  ["xhigh", "XHigh"],
] as const;

function AIProfilesSection({ profiles, profile, setProfile, saveProfile, testProfile, removeProfile, message }: { profiles: AIProfile[]; profile: EditableProfile; setProfile: React.Dispatch<React.SetStateAction<EditableProfile>>; saveProfile: () => void; testProfile: () => void; removeProfile: () => void; message: string | null }) {
  const thinkingEffortValue = profile.api_format === "openai_chat" ? "off" : String(profile.thinking_effort || "off");
  const thinkingHint = profile.api_format === "openai_chat" ? "OpenAI Chat Completions 不同模型的思考参数不统一，请查阅模型官方 API 文档，在 Custom Payload 中手动配置。" : profile.api_format === "anthropic_messages" ? "Claude Messages 将使用 thinking adaptive 和对应 effort。" : "OpenAI Responses 将使用 reasoning.effort。";
  const handleThinkingChange = (event: React.ChangeEvent<HTMLSelectElement>) => {
    const effort = event.target.value;
    setProfile({ ...profile, thinking_effort: effort, thinking_enabled: effort !== "off" });
  };
  return <>
    <div className="settings-section"><div className="section-title"><h2>AI profiles</h2><button className="button tonal" onClick={() => setProfile({ name: "新 profile", api_format: "openai_chat", base_url: "", model: "", api_key: "", temperature: 0.3, max_tokens: 4096, timeout_seconds: 180, max_chars_per_request: 1200, retry_count: 2, thinking_effort: "off", thinking_enabled: false, custom_payload: {} })}>新建 profile</button></div><div className="profile-list">{profiles.map((item) => <button key={item.id} className={`profile-row ${profile.id === item.id ? "active" : ""}`} onClick={() => setProfile({ ...item, api_key: "" })}><KeyRound size={16} /><span><strong>{item.name}</strong><small>{item.api_format} · {item.model} · {item.has_api_key ? "已配置密钥" : "未配置密钥"}</small></span></button>)}</div></div>
    <div className="settings-section"><h2>{profile.name || "新 profile"}</h2><div className="settings-grid">
      <label>名称<input value={String(profile.name || "")} onChange={(event) => setProfile({ ...profile, name: event.target.value })} /></label>
      <label>API 格式<select value={String(profile.api_format || "openai_chat")} onChange={(event) => setProfile({ ...profile, api_format: event.target.value })}><option value="openai_chat">OpenAI Chat Completions</option><option value="openai_responses">OpenAI Responses</option><option value="anthropic_messages">Anthropic Messages</option></select></label>
      <label>Model<input value={String(profile.model || "")} onChange={(event) => setProfile({ ...profile, model: event.target.value })} /></label>
      <label>Base URL<input value={String(profile.base_url || "")} placeholder="https://api.openai.com/v1" onChange={(event) => setProfile({ ...profile, base_url: event.target.value })} /></label>
      <label>API Key（留空不替换）<input type="password" value={String(profile.api_key || "")} placeholder={profile.has_api_key ? "已配置 · 末尾 4 位已隐藏" : "sk-…"} onChange={(event) => setProfile({ ...profile, api_key: event.target.value })} /></label>
      <label>Temperature<input type="number" min="0" max="2" step="0.1" value={String(profile.temperature ?? 0.3)} onChange={(event) => setProfile({ ...profile, temperature: Number(event.target.value) })} /></label>
      <label>Max tokens<input type="number" min="0" max="65535" value={String(profile.max_tokens ?? 4096)} onChange={(event) => setProfile({ ...profile, max_tokens: Number(event.target.value) })} /></label>
      <label>Timeout（秒）<input type="number" min="1" max="3600" value={String(profile.timeout_seconds ?? 180)} onChange={(event) => setProfile({ ...profile, timeout_seconds: Number(event.target.value) })} /></label>
      <label>每次请求最大字数<input type="number" min="100" max="20000" value={String(profile.max_chars_per_request ?? 1200)} onChange={(event) => setProfile({ ...profile, max_chars_per_request: Number(event.target.value) })} /><small>按完整歌词行分批，超长单行不会截断</small></label>
      <label>失败重试次数<input type="number" min="0" max="10" value={String(profile.retry_count ?? 2)} onChange={(event) => setProfile({ ...profile, retry_count: Number(event.target.value) })} /><small>每批首次失败后的额外尝试次数</small></label>
      <label>思考强度<select value={thinkingEffortValue} disabled={profile.api_format === "openai_chat"} onChange={handleThinkingChange}>{thinkingOptions.map(([value, text]) => <option key={value} value={value}>{text}</option>)}</select><small>{thinkingHint}</small></label>
    </div><label>Custom Payload（JSON）<textarea rows={7} value={JSON.stringify(profile.custom_payload || {}, null, 2)} onChange={(event) => { try { setProfile({ ...profile, custom_payload: JSON.parse(event.target.value) }); } catch { /* 保留编辑中的无效 JSON，保存时由后端提示 */ } }} /></label><div className="dialog-actions"><button className="button filled" onClick={saveProfile}><Save size={16} />保存 profile</button><button className="button tonal" disabled={!profile.id} onClick={testProfile}><Send size={16} />测试连接</button><button className="button text danger" disabled={!profile.id} onClick={removeProfile}>删除</button></div>{message && <p className="muted">{message}</p>}</div>
  </>;
}

const settingsSections = [
  ["general", "常规"], ["ai", "AI 模型与 API"], ["prompts", "注音与提示词"],
  ["separation", "人声分离"], ["whisper", "Whisper"], ["timeline", "时间轴与打轴"],
  ["subtitles", "字幕与样式"], ["export", "导出"], ["storage", "存储与任务"], ["diagnostics", "诊断与关于"],
] as const;

function SettingsPage() {
  const params = new URLSearchParams(location.hash.split("?")[1] || "");
  const returnTo = params.get("returnTo") || "/projects";
  const [active, setActive] = useState("general");
  const [values, setValues] = useState<Record<string, unknown>>({
    autosave_interval_seconds: 15, theme: "system", font_family: "Noto Sans JP", font_size_max: 64,
    separator_device: "auto", separator_model: "UVR_MDXNET_KARA_2.onnx",
    whisper_model: "small", whisper_device: "cpu", whisper_compute_type: "int8",
    alignment_backend: "fa_kara", fa_kara_model: "mms",
    stable_ts_token_step: 100, stable_ts_segment_padding_seconds: 2,
    proxy_enabled: true, proxy_url: "http://127.0.0.1:10808",
  });
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [profiles, setProfiles] = useState<AIProfile[]>([]);
  const [profile, setProfile] = useState<EditableProfile>({ name: "默认注音", api_format: "openai_chat", base_url: "http://127.0.0.1:1234/v1", model: "local-model", temperature: 0.2, max_tokens: 2000, timeout_seconds: 45, max_chars_per_request: 1200, retry_count: 2, thinking_effort: "off", thinking_enabled: false, custom_payload: {}, api_key: "" });
  const [prompts, setPrompts] = useState<PromptPreset[]>([]);
  const [prompt, setPrompt] = useState<PromptPreset>({ id: "", name: "我的注音提示词", system_prompt: "", user_template: "" });
  const [profileMessage, setProfileMessage] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  useEffect(() => {
    void Promise.all([api<Record<string, unknown>>("/settings"), api<Capabilities>("/settings/capabilities"), api<AIProfile[]>("/settings/ai-profiles"), api<PromptPreset[]>("/settings/prompt-presets")])
      .then(([loaded, detected, loadedProfiles, loadedPrompts]) => { setValues((current) => ({ ...current, ...loaded })); setCapabilities(detected); setProfiles(loadedProfiles); if (loadedProfiles[0]) setProfile({ ...loadedProfiles[0], api_key: "" }); setPrompts(loadedPrompts); if (loadedPrompts[0]) setPrompt(loadedPrompts[0]); });
  }, []);
  async function saveSettings() { await api("/settings", { method: "PUT", body: JSON.stringify({ values }) }); setSaved(true); window.setTimeout(() => setSaved(false), 1800); }
  async function saveProfileWithBatchSettings() {
    const effort = (profile.thinking_effort || "off") as AIProfile["thinking_effort"];
    const payload = {
      name: profile.name || "未命名 profile",
      api_format: profile.api_format || "openai_chat",
      base_url: profile.base_url || "",
      model: profile.model || "",
      api_key: profile.api_key || null,
      temperature: Number(profile.temperature ?? 0.2),
      max_tokens: Number(profile.max_tokens ?? 2000),
      timeout_seconds: Number(profile.timeout_seconds ?? 45),
      max_chars_per_request: Number(profile.max_chars_per_request ?? 1200),
      retry_count: Number(profile.retry_count ?? 2),
      thinking_effort: effort,
      thinking_enabled: effort !== "off",
      custom_payload: profile.custom_payload || {},
    };
    const savedProfile = profile.id
      ? await api<AIProfile>(`/settings/ai-profiles/${profile.id}`, { method: "PUT", body: JSON.stringify(payload) })
      : await api<AIProfile>("/settings/ai-profiles", { method: "POST", body: JSON.stringify(payload) });
    setProfiles((current) => [savedProfile, ...current.filter((item) => item.id !== savedProfile.id)]);
    setProfile({ ...savedProfile, api_key: "" });
    setValues((current) => ({ ...current, default_ai_profile_id: savedProfile.id }));
    await api("/settings", { method: "PUT", body: JSON.stringify({ values: { default_ai_profile_id: savedProfile.id } }) });
    setProfileMessage("profile 已保存并设为默认");
  }
  async function saveProfile() { const effort = (profile.thinking_effort || "off") as AIProfile["thinking_effort"]; const payload = { name: profile.name || "未命名 profile", api_format: profile.api_format || "openai_chat", base_url: profile.base_url || "", model: profile.model || "", api_key: profile.api_key || null, temperature: Number(profile.temperature ?? 0.2), max_tokens: Number(profile.max_tokens ?? 2000), timeout_seconds: Number(profile.timeout_seconds ?? 45), thinking_effort: effort, thinking_enabled: effort !== "off", custom_payload: profile.custom_payload || {} }; const savedProfile = profile.id ? await api<AIProfile>(`/settings/ai-profiles/${profile.id}`, { method: "PUT", body: JSON.stringify(payload) }) : await api<AIProfile>("/settings/ai-profiles", { method: "POST", body: JSON.stringify(payload) }); setProfiles((current) => [savedProfile, ...current.filter((item) => item.id !== savedProfile.id)]); setProfile({ ...savedProfile, api_key: "" }); setValues((current) => ({ ...current, default_ai_profile_id: savedProfile.id })); await api("/settings", { method: "PUT", body: JSON.stringify({ values: { default_ai_profile_id: savedProfile.id } }) }); setProfileMessage("profile 已保存并设为默认"); }
  async function testProfile() { if (!profile.id) { setProfileMessage("请先保存 profile"); return; } const result = await api<{ elapsed_ms: number }>(`/settings/ai-profiles/${profile.id}/test`, { method: "POST" }); setProfileMessage(`连接成功 · ${result.elapsed_ms} ms`); }
  async function removeProfile() { if (!profile.id || !window.confirm("删除这个 AI profile？")) return; await api(`/settings/ai-profiles/${profile.id}`, { method: "DELETE" }); const remaining = profiles.filter((item) => item.id !== profile.id); setProfiles(remaining); setProfile(remaining[0] ? { ...remaining[0], api_key: "" } : { name: "默认注音", api_format: "openai_chat", base_url: "", model: "", api_key: "" }); }
  async function savePrompt() { const payload = { name: prompt.name, system_prompt: prompt.system_prompt, user_template: prompt.user_template }; const savedPrompt = prompt.id && prompt.id !== "builtin-default" ? await api<PromptPreset>(`/settings/prompt-presets/${prompt.id}`, { method: "PUT", body: JSON.stringify(payload) }) : await api<PromptPreset>("/settings/prompt-presets", { method: "POST", body: JSON.stringify(payload) }); setPrompts((current) => [savedPrompt, ...current.filter((item) => item.id !== savedPrompt.id)]); setPrompt(savedPrompt); await api("/settings", { method: "PUT", body: JSON.stringify({ values: { pronunciation_system_prompt: savedPrompt.system_prompt, pronunciation_user_template: savedPrompt.user_template } }) }); setProfileMessage("提示词预设已保存并设为默认"); }
  const label = settingsSections.find(([id]) => id === active)?.[1] || "设置";
  return <>
    <header className="topbar"><a href="#/projects" className="brand"><span className="brand-mark">N</span><span>Nicokara Studio</span></a><span className="top-title">设置</span></header>
    <main className="settings-layout">
      <nav className="settings-nav"><a className="back-link" href={`#${returnTo}`}><ArrowLeft size={18} />返回</a><p className="eyebrow">设置</p>{settingsSections.map(([id, text]) => <button className={active === id ? "nav-item active" : "nav-item"} key={id} onClick={() => setActive(id)}>{text}</button>)}</nav>
      <section className="settings-content">
        <div className="page-heading"><div><h1>{label}</h1><p className="muted">全局默认用于后续任务；工程与单次操作参数优先。</p></div><button className="button filled" onClick={() => void saveSettings()}><Save size={17} />{saved ? "已保存" : "保存更改"}</button></div>
        {active === "general" && <><div className="settings-section"><h2>应用行为</h2><div className="settings-grid"><label>自动保存间隔（秒）<input type="number" min="5" max="300" value={String(values.autosave_interval_seconds)} onChange={(event) => setValues({ ...values, autosave_interval_seconds: Number(event.target.value) })} /><small>范围 5–300 秒 · 全局设置</small></label><label>主题<select value={String(values.theme)} onChange={(event) => setValues({ ...values, theme: event.target.value })}><option value="system">跟随系统</option><option value="light">浅色</option><option value="dark">深色</option></select><small>全局设置</small></label></div></div><div className="settings-section"><h2>网络代理</h2><div className="settings-grid"><label className="checkbox-field"><input type="checkbox" checked={Boolean(values.proxy_enabled)} onChange={(event) => setValues({ ...values, proxy_enabled: event.target.checked })} />启用代理<small>用于下载 KARA2、Whisper 模型和联网服务</small></label><label>代理地址<input value={String(values.proxy_url)} disabled={!values.proxy_enabled} placeholder="http://127.0.0.1:10808" onChange={(event) => setValues({ ...values, proxy_url: event.target.value })} /><small>支持 HTTP/HTTPS 代理地址，不会写入工程文件</small></label></div></div></>}
        {active === "separation" && <>
          <div className="capability-banner"><Cpu size={20} /><div><strong>{capabilities?.separator.available ? `audio-separator ${capabilities.separator.version}` : "分离运行时不可用"}</strong><span>设备能力由后端实时检测。</span></div></div>
          <div className="settings-section"><h2>KARA2</h2><div className="settings-grid"><label>计算设备<select value={String(values.separator_device)} onChange={(event) => setValues({ ...values, separator_device: event.target.value })}><option value="auto">自动</option>{capabilities?.separator.devices.map((device) => <option key={device} value={device}>{device === "directml" ? "DirectML" : device.toUpperCase()}</option>)}</select><small>只列出本机真实可用设备</small></label><label>模型<input value={String(values.separator_model)} onChange={(event) => setValues({ ...values, separator_model: event.target.value })} /><small>{capabilities?.separator.models.length ? `已安装 ${capabilities.separator.models.length} 个模型` : "模型未安装时首次任务会报告明确错误"}</small></label></div></div>
        </>}
        {active === "whisper" && <>
          <div className="capability-banner"><Cpu size={20} /><div><strong>{capabilities?.whisper.available ? `faster-whisper ${capabilities.whisper.version}` : "Whisper 运行时不可用"}</strong><span>Whisper 使用 CPU。</span></div></div>
          <div className="settings-section"><h2>识别与对齐默认值</h2><div className="settings-grid"><label>Whisper 模型<select value={String(values.whisper_model)} onChange={(event) => setValues({ ...values, whisper_model: event.target.value })}>{capabilities?.whisper.models.map((model) => <option key={model}>{model}</option>)}</select><small>{capabilities?.whisper.installed_models.length ? `本地缓存 ${capabilities.whisper.installed_models.length} 个模型` : "首次任务通过代理按需下载到工程存储"}</small></label><label>计算设备<select value="cpu" disabled><option value="cpu">CPU</option></select><small>当前版本固定使用 CPU</small></label><label>Compute type<select value={String(values.whisper_compute_type)} onChange={(event) => setValues({ ...values, whisper_compute_type: event.target.value })}><option value="int8">int8</option><option value="float32">float32</option></select><small>推荐 int8</small></label><label>对齐后端<select value={String(values.alignment_backend || "fa_kara")} onChange={(event) => setValues({ ...values, alignment_backend: event.target.value })}><option value="fa_kara">FA-Kara</option><option value="stable_ts">stable-ts</option></select><small>Whisper 粗识别在两种流程中都会运行</small></label>{String(values.alignment_backend || "fa_kara") === "fa_kara" ? <label>FA-Kara 模型<select value={String(values.fa_kara_model || "mms")} onChange={(event) => setValues({ ...values, fa_kara_model: event.target.value })}><option value="mms">MMS_FA 基座模型</option><option value="yohane">YoHane 日语卡拉 OK 微调模型</option></select><small>YoHane 首次使用时通过代理下载模型</small></label> : <><label>stable-ts token-step<input type="number" min="0" max="442" step="1" value={String(values.stable_ts_token_step ?? 100)} onChange={(event) => setValues({ ...values, stable_ts_token_step: Number(event.target.value) })} /><small>范围 0–442；0 使用最大窗口模式，仅用于全局 align()</small></label><label>词/短语精修 segment 扩展（秒）<input type="number" min="0" max="30" step="0.1" value={String(values.stable_ts_segment_padding_seconds ?? 2)} onChange={(event) => setValues({ ...values, stable_ts_segment_padding_seconds: Number(event.target.value) })} /><small>在每行首尾各扩展指定秒数后调用 align_words()</small></label></>}</div></div>
        </>}
        {active === "subtitles" && <div className="settings-section"><h2>字幕与样式</h2><div className="settings-grid"><label>字体<input value={String(values.font_family)} onChange={(event) => setValues({ ...values, font_family: event.target.value })} /><small>新工程默认字体</small></label><label>最大字号<input type="number" min="12" max="180" value={String(values.font_size_max)} onChange={(event) => setValues({ ...values, font_size_max: Number(event.target.value) })} /><small>范围 12–180 px</small></label></div></div>}
        {!(["general", "ai", "prompts", "separation", "whisper", "subtitles"].includes(active)) && <div className="settings-section settings-placeholder"><h2>{label}</h2><p>该分类将在对应开发阶段开放，当前不会写入占位配置。</p></div>}
        {active === "ai" && <AIProfilesSection profiles={profiles} profile={profile} setProfile={setProfile} saveProfile={() => void saveProfileWithBatchSettings()} testProfile={() => void testProfile()} removeProfile={() => void removeProfile()} message={profileMessage} />}
        {active === "prompts" && <div className="settings-section"><h2>注音提示词预设</h2><label>系统提示词<textarea rows={6} value={prompt.system_prompt} onChange={(event) => setPrompt({ ...prompt, system_prompt: event.target.value })} /></label><label>用户模板<textarea rows={8} value={prompt.user_template} onChange={(event) => setPrompt({ ...prompt, user_template: event.target.value })} /></label><button className="button filled" onClick={() => void savePrompt()} disabled={!prompt.system_prompt || !prompt.user_template}><Save size={16} />保存提示词</button></div>}
      </section>
    </main>
  </>;
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
