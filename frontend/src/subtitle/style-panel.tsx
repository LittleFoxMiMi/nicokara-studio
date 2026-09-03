import { Check, ChevronDown, Palette, Save, Trash2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { normalizeSubtitleFontFamily, SUBTITLE_FONT_OPTIONS, type SubtitleStyle } from "./style-schema";

export type StylePreset = { id: string; name: string; style: Partial<SubtitleStyle> };

export function SubtitleStylePanel({ style, presets, onChange, onApplyPreset, onSavePreset, onDeletePreset }: { style: SubtitleStyle; presets: StylePreset[]; onChange: (patch: Partial<SubtitleStyle>) => void; onApplyPreset: (preset: StylePreset) => void; onSavePreset: (name: string) => void; onDeletePreset: (preset: StylePreset) => void }) {
  const [presetOpen, setPresetOpen] = useState(false);
  const [selectedPresetId, setSelectedPresetId] = useState<string | null>(null);
  const presetMenuRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!presetOpen) return;
    const close = (event: PointerEvent) => {
      if (!presetMenuRef.current?.contains(event.target as Node)) setPresetOpen(false);
    };
    addEventListener("pointerdown", close);
    return () => removeEventListener("pointerdown", close);
  }, [presetOpen]);
  const selectedPreset = presets.find((preset) => preset.id === selectedPresetId);
  const selectedFont = normalizeSubtitleFontFamily(style.fontFamily);
  return <section className="subtitle-style-panel">
    <div className="section-title"><h3><Palette size={16} />字幕样式</h3><button className="icon-button compact" title="保存样式预设" onClick={() => { const name = window.prompt("预设名称", "我的样式"); if (name?.trim()) onSavePreset(name.trim()); }}><Save size={15} /></button></div>
    <div className="field-label">预设<div className="style-preset-select" ref={presetMenuRef}>
      <button className="style-preset-trigger" type="button" aria-haspopup="listbox" aria-expanded={presetOpen} onClick={() => setPresetOpen((open) => !open)}><span>{selectedPreset?.name || "选择样式预设"}</span><ChevronDown size={16} /></button>
      {presetOpen && <div className="style-preset-menu" role="listbox">{presets.length ? presets.map((preset) => <div className="style-preset-option" key={preset.id}>
        <button type="button" role="option" aria-selected={selectedPresetId === preset.id} onClick={() => { setSelectedPresetId(preset.id); onApplyPreset(preset); setPresetOpen(false); }}>{selectedPresetId === preset.id && <Check size={15} />}<span>{preset.name}</span></button>
        <button className="style-preset-delete" type="button" title={`删除样式预设“${preset.name}”`} aria-label={`删除样式预设“${preset.name}”`} onClick={() => onDeletePreset(preset)}><Trash2 size={15} /></button>
      </div>) : <p className="style-preset-empty">暂无已保存样式</p>}</div>}
    </div></div>
    <div className="style-grid">
      <label className="field-label">字体<select value={selectedFont} onChange={(event) => onChange({ fontFamily: event.target.value })}>{!SUBTITLE_FONT_OPTIONS.some((option) => option.value === selectedFont) && <option value={selectedFont}>{selectedFont}</option>}{SUBTITLE_FONT_OPTIONS.map((option) => <option value={option.value} key={option.value}>{option.label}</option>)}</select></label>
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
