import type { SubtitleStyle } from "./style-schema";

export type TextMetricsLike = { width: number; ascent: number; descent: number };

let canvasContext: CanvasRenderingContext2D | null | undefined;
function context(): CanvasRenderingContext2D | null {
  if (canvasContext !== undefined) return canvasContext;
  if (typeof document === "undefined") return (canvasContext = null);
  const canvas = document.createElement("canvas");
  return (canvasContext = canvas.getContext("2d"));
}

export function measureText(text: string, fontSize: number, style: SubtitleStyle, ruby = false): TextMetricsLike {
  const size = ruby ? fontSize * style.rubyScale : fontSize;
  const font = `${style.fontWeight} ${size}px ${style.fontFamily}`;
  const ctx = context();
  if (ctx) {
    ctx.font = font;
    const metrics = ctx.measureText(text);
    return { width: metrics.width, ascent: metrics.actualBoundingBoxAscent || size * 0.8, descent: metrics.actualBoundingBoxDescent || size * 0.2 };
  }
  return { width: Array.from(text).length * size, ascent: size * 0.8, descent: size * 0.2 };
}

export function measureUnit(unit: { surface: string; ruby?: string | null; ruby_2?: string | null }, fontSize: number, style: SubtitleStyle): number {
  const surface = measureText(unit.surface, fontSize, style).width + Math.max(0, Array.from(unit.surface).length - 1) * style.letterSpacing * fontSize;
  const ruby = unit.ruby ? measureText(unit.ruby, fontSize, style, true).width : 0;
  const ruby2 = unit.ruby_2 ? measureText(unit.ruby_2, fontSize, style, true).width * (style.ruby2Scale / style.rubyScale) : 0;
  return Math.max(surface, ruby, ruby2) + style.letterSpacing * fontSize;
}
