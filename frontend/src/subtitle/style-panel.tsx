import { Palette, Save } from "lucide-react";
import type { SubtitleStyle } from "./style-schema";

export type StylePreset = { id: string; name: string; style: Partial<SubtitleStyle> };

export function SubtitleStylePanel({ style, presets, onChange, onApplyPreset, onSavePreset }: { style: SubtitleStyle; presets: StylePreset[]; onChange: (patch: Partial<SubtitleStyle>) => void; onApplyPreset: (preset: StylePreset) => void; onSavePreset: (name: string) => void }) {
  return <section className="subtitle-style-panel">
    <div className="section-title"><h3><Palette size={16} />字幕样式</h3><button className="icon-button compact" title="保存样式预设" onClick={() => { const name = window.prompt("预设名称", "我的样式"); if (name?.trim()) onSavePreset(name.trim()); }}><Save size={15} /></button></div>
    <label className="field-label">预设<select value="" onChange={(event) => { const preset = presets.find((item) => item.id === event.target.value); if (preset) onApplyPreset(preset); }}><option value="">选择样式预设</option>{presets.map((preset) => <option value={preset.id} key={preset.id}>{preset.name}</option>)}</select></label>
    <div className="style-grid">
      <label className="field-label">字体<input value={style.fontFamily} onChange={(event) => onChange({ fontFamily: event.target.value })} /></label>
      <label className="field-label">字号<input type="number" min="10" max="180" value={style.fontSizeMax} onChange={(event) => onChange({ fontSizeMax: Number(event.target.value) })} /></label>
      <label className="field-label">最小字号<input type="number" min="10" max="180" value={style.fontSizeMin} onChange={(event) => onChange({ fontSizeMin: Number(event.target.value) })} /></label>
      <label className="field-label">换行<select value={style.maxLines} onChange={(event) => onChange({ maxLines: Number(event.target.value) === 2 ? 2 : 1 })}><option value="1">单行</option><option value="2">最多两行</option></select></label>
      <label className="field-label">Ruby 比例<input type="number" min="0.2" max="0.8" step="0.01" value={style.rubyScale} onChange={(event) => onChange({ rubyScale: Number(event.target.value) })} /></label>
      <label className="field-label">字间距<input type="number" min="-0.1" max="0.4" step="0.01" value={style.letterSpacing} onChange={(event) => onChange({ letterSpacing: Number(event.target.value) })} /></label>
      <label className="field-label">左槽 X<input type="range" min="0.04" max="0.4" step="0.01" value={style.line1X} onChange={(event) => onChange({ line1X: Number(event.target.value) })} /></label>
      <label className="field-label">左槽 Y<input type="range" min="0.1" max="0.85" step="0.01" value={style.line1Y} onChange={(event) => onChange({ line1Y: Number(event.target.value) })} /></label>
      <label className="field-label">右槽距右<input type="range" min="0.04" max="0.4" step="0.01" value={style.line2Right} onChange={(event) => onChange({ line2Right: Number(event.target.value) })} /></label>
      <label className="field-label">右槽 Y<input type="range" min="0.15" max="0.92" step="0.01" value={style.line2Y} onChange={(event) => onChange({ line2Y: Number(event.target.value) })} /></label>
    </div>
    <div className="style-colors"><label className="field-label">未唱颜色<input type="color" value={style.textColor} onChange={(event) => onChange({ textColor: event.target.value })} /></label><label className="field-label">走字颜色<input type="color" value={style.activeColor} onChange={(event) => onChange({ activeColor: event.target.value })} /></label><label className="field-label">描边<input type="color" value={style.outlineColor} onChange={(event) => onChange({ outlineColor: event.target.value })} /></label></div>
  </section>;
}
