import type { SubtitleLayout } from "./layout";
import type { SubtitleStyle } from "./style-schema";

export function renderSubtitleCanvas(ctx: CanvasRenderingContext2D, layout: SubtitleLayout, style: SubtitleStyle, currentMs = 0): void {
  ctx.save();
  ctx.globalAlpha = layout.opacity;
  ctx.textAlign = "left";
  ctx.textBaseline = "alphabetic";
  ctx.lineJoin = "round";
  const lineHeight = layout.fontSize * (1 + style.rubyScale + style.rubyGap);
  let y = layout.y + layout.fontSize;
  for (const row of layout.rows) {
    let x = layout.x + (layout.width - row.width) / 2;
    for (const unit of row.units) {
      const progress = unit.start_ms !== null && unit.end_ms !== null && unit.end_ms > unit.start_ms ? Math.max(0, Math.min(1, (currentMs - unit.start_ms) / (unit.end_ms - unit.start_ms))) : 0;
      ctx.font = `${style.fontWeight} ${layout.fontSize}px ${style.fontFamily}`;
      ctx.strokeStyle = style.outlineColor;
      ctx.lineWidth = style.outlineWidth * 2;
      ctx.fillStyle = progress > 0 ? style.activeColor : style.textColor;
      ctx.strokeText(unit.surface, x, y);
      ctx.fillText(unit.surface, x, y);
      if (unit.ruby) { ctx.font = `${style.fontWeight} ${layout.fontSize * style.rubyScale}px ${style.fontFamily}`; ctx.fillStyle = style.textColor; ctx.fillText(unit.ruby, x, y - layout.fontSize * (1 + style.rubyGap)); }
      x += unit.width;
    }
    y += lineHeight;
  }
  ctx.restore();
}
