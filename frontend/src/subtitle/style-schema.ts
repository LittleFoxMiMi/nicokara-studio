export type SubtitleStyle = {
  fontFamily: string;
  fontSizeMin: number;
  fontSizeMax: number;
  fontWeight: number;
  maxLines: 1 | 2;
  safeAreaLeft: number;
  safeAreaRight: number;
  safeAreaTop: number;
  safeAreaBottom: number;
  rubyScale: number;
  ruby2Scale: number;
  rubyGap: number;
  lineGap: number;
  letterSpacing: number;
  textColor: string;
  activeColor: string;
  outlineColor: string;
  outlineWidth: number;
  shadowColor: string;
  shadowBlur: number;
  slot: "bottom" | "top";
  positionY: number;
  line1X: number;
  line1Y: number;
  line2Right: number;
  line2Y: number;
  wrapMode: "unit" | "none";
  fadeInMs: number;
  fadeOutMs: number;
  showProgressDots: boolean;
};

export const SUBTITLE_FONT_OPTIONS = [
  { label: "Noto Sans JP", value: "Noto Sans JP, Google Sans, sans-serif" },
  { label: "Noto Serif JP", value: "Noto Serif JP, serif" },
  { label: "游ゴシック", value: '"Yu Gothic", YuGothic, sans-serif' },
  { label: "游明朝", value: '"Yu Mincho", YuMincho, serif' },
  { label: "メイリオ", value: "Meiryo, sans-serif" },
  { label: "MS ゴシック", value: '"MS Gothic", monospace' },
] as const;

export function normalizeSubtitleFontFamily(value: unknown): string {
  const family = String(value || "").trim();
  if (family === "Noto Sans JP") return SUBTITLE_FONT_OPTIONS[0].value;
  return family || SUBTITLE_FONT_OPTIONS[0].value;
}

export const DEFAULT_SUBTITLE_STYLE: SubtitleStyle = {
  fontFamily: "Noto Sans JP, Google Sans, sans-serif",
  fontSizeMin: 24,
  fontSizeMax: 60,
  fontWeight: 600,
  maxLines: 1,
  safeAreaLeft: 0.08,
  safeAreaRight: 0.08,
  safeAreaTop: 0.08,
  safeAreaBottom: 0.12,
  rubyScale: 0.42,
  ruby2Scale: 0.32,
  rubyGap: 0.18,
  lineGap: 0.18,
  // Kirakara's 9px spacing at the default 64px font size, expressed as em.
  letterSpacing: 0.14,
  textColor: "#ffffff",
  activeColor: "#c79af6",
  outlineColor: "#000000",
  outlineWidth: 3,
  shadowColor: "#00000099",
  shadowBlur: 8,
  slot: "bottom",
  positionY: 0.84,
  line1X: 0.04,
  line1Y: 0.597,
  line2Right: 0.04,
  line2Y: 0.782,
  wrapMode: "unit",
  fadeInMs: 100,
  fadeOutMs: 140,
  showProgressDots: true,
};

const numberFields = new Set<keyof SubtitleStyle>([
  "fontSizeMin", "fontSizeMax", "fontWeight", "safeAreaLeft", "safeAreaRight",
  "safeAreaTop", "safeAreaBottom", "rubyScale", "ruby2Scale", "rubyGap", "lineGap",
  "letterSpacing", "outlineWidth", "shadowBlur", "positionY", "line1X", "line1Y", "line2Right", "line2Y", "fadeInMs", "fadeOutMs",
]);

export function normalizeSubtitleStyle(input: Record<string, unknown> | null | undefined): SubtitleStyle {
  const source = input || {};
  const style = { ...DEFAULT_SUBTITLE_STYLE } as SubtitleStyle;
  for (const key of Object.keys(DEFAULT_SUBTITLE_STYLE) as (keyof SubtitleStyle)[]) {
    const value = source[key as string];
    if (value === undefined) continue;
    if (numberFields.has(key)) {
      const number = Number(value);
      if (Number.isFinite(number)) (style[key] as number) = number;
    } else if (key === "maxLines") {
      style.maxLines = Number(value) >= 2 ? 2 : 1;
    } else if (key === "slot") {
      style.slot = value === "top" ? "top" : "bottom";
    } else if (key === "wrapMode") {
      style.wrapMode = value === "none" ? "none" : "unit";
    } else if (key === "showProgressDots") {
      style.showProgressDots = Boolean(value);
    } else if (typeof value === "string") {
      (style[key] as string) = value;
    }
  }
  style.fontSizeMin = Math.max(10, Math.min(style.fontSizeMin, style.fontSizeMax));
  style.fontSizeMax = Math.max(style.fontSizeMin, style.fontSizeMax);
  style.maxLines = style.maxLines === 2 ? 2 : 1;
  return style;
}

export function styleToDocument(style: SubtitleStyle): Record<string, unknown> {
  return { ...style };
}
