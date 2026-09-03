import { Check, Trash2 } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { LyricLine } from "./editor-types";
import { rubyRanges, type RubyRange } from "./ruby-range";

const CELL_WIDTH = 40;

type Selection = { start: number; end: number };
type BracketDrag = { group: RubyRange; edge: "start" | "end"; start: number; end: number };

export function RubyRangeEditor({
  line,
  rangeStart,
  rangeEnd,
  title,
  status,
  onApply,
}: {
  line: LyricLine;
  rangeStart: number;
  rangeEnd: number;
  title: string;
  status: string;
  onApply: (start: number, end: number, ruby: string, ruby2: string, replacedRange: Selection | null) => void;
}) {
  const allCharacters = useMemo(() => Array.from(line.units.map((unit) => unit.surface).join("")), [line.units]);
  const start = Math.max(0, Math.min(allCharacters.length, rangeStart));
  const end = Math.max(start, Math.min(allCharacters.length, rangeEnd));
  const characters = allCharacters.slice(start, end);
  const groups = useMemo(
    () => rubyRanges(line).filter((group) => group.start >= start && group.end <= end),
    [end, line, start],
  );
  const [selection, setSelection] = useState<Selection | null>(null);
  const [reading, setReading] = useState("");
  const [dragPreview, setDragPreview] = useState<BracketDrag | null>(null);
  const selectionAnchor = useRef<number | null>(null);

  useEffect(() => {
    setSelection(null);
    setReading("");
    setDragPreview(null);
  }, [line.id, start, end]);

  const selectedGroup = selection
    ? groups.find((group) => group.start === selection.start && group.end === selection.end) || null
    : null;

  function select(next: Selection) {
    setSelection(next);
    const group = groups.find((candidate) => candidate.start === next.start && candidate.end === next.end);
    setReading(group?.ruby || "");
  }

  function characterIndexAt(clientX: number, track: HTMLElement): number {
    const rect = track.getBoundingClientRect();
    return Math.max(start, Math.min(end - 1, start + Math.floor((clientX - rect.left) / CELL_WIDTH)));
  }

  function finishBracketDrag(event: React.PointerEvent<HTMLButtonElement>) {
    const drag = dragPreview;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    setDragPreview(null);
    if (!drag || (drag.start === drag.group.start && drag.end === drag.group.end)) return;
    onApply(drag.start, drag.end, drag.group.ruby, drag.group.ruby2, drag.group);
    setSelection({ start: drag.start, end: drag.end });
    setReading(drag.group.ruby);
  }

  if (!characters.length) return null;
  const visibleGroups = dragPreview
    ? groups.map((group) => group === dragPreview.group ? { ...group, start: dragPreview.start, end: dragPreview.end } : group)
    : groups;

  return <section className="ruby-range-editor" aria-label="Ruby 范围编辑">
    <div className="section-title">
      <h3>{title}</h3>
      <span>{status}</span>
    </div>
    <div className="ruby-range-scroller">
      <div
        className="ruby-range-track"
        style={{ width: `${characters.length * CELL_WIDTH}px` }}
        onPointerDown={(event) => {
          const element = (event.target as HTMLElement).closest<HTMLElement>("[data-ruby-character]");
          if (!element) return;
          const index = Number(element.dataset.rubyCharacter);
          event.currentTarget.setPointerCapture(event.pointerId);
          selectionAnchor.current = index;
          select({ start: index, end: index + 1 });
        }}
        onPointerMove={(event) => {
          if (selectionAnchor.current === null) return;
          const index = characterIndexAt(event.clientX, event.currentTarget);
          select({ start: Math.min(selectionAnchor.current, index), end: Math.max(selectionAnchor.current, index) + 1 });
        }}
        onPointerUp={(event) => {
          if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
          selectionAnchor.current = null;
        }}
        onPointerCancel={() => { selectionAnchor.current = null; }}
      >
        <div className="ruby-bracket-layer">
          {visibleGroups.map((group) => {
            const original = groups.find((candidate) => candidate.start === group.start && candidate.end === group.end) || dragPreview?.group || group;
            return <div
              className="ruby-range-bracket"
              key={`${original.start}-${original.end}-${original.ruby}-${original.ruby2}`}
              style={{ left: `${(group.start - start) * CELL_WIDTH}px`, width: `${(group.end - group.start) * CELL_WIDTH}px` }}
            >
              <button className="ruby-bracket-label" type="button" title={group.ruby2 ? `${group.ruby} / ${group.ruby2}` : group.ruby} onClick={() => select(group)}>{group.ruby || group.ruby2}</button>
              {(["start", "end"] as const).map((edge) => <button
                key={edge}
                className={`ruby-bracket-handle ${edge}`}
                type="button"
                title={edge === "start" ? "拖动 Ruby 左边界" : "拖动 Ruby 右边界"}
                aria-label={edge === "start" ? "拖动 Ruby 左边界" : "拖动 Ruby 右边界"}
                onPointerDown={(event) => {
                  event.stopPropagation();
                  event.currentTarget.setPointerCapture(event.pointerId);
                  setDragPreview({ group: original, edge, start: original.start, end: original.end });
                }}
                onPointerMove={(event) => {
                  if (!event.currentTarget.hasPointerCapture(event.pointerId)) return;
                  const track = event.currentTarget.closest<HTMLElement>(".ruby-range-track");
                  if (!track) return;
                  const rawBoundary = start + Math.round((event.clientX - track.getBoundingClientRect().left) / CELL_WIDTH);
                  setDragPreview((current) => !current ? current : current.edge === "start"
                    ? { ...current, start: Math.max(start, Math.min(current.end - 1, rawBoundary)) }
                    : { ...current, end: Math.max(current.start + 1, Math.min(end, rawBoundary)) });
                }}
                onPointerUp={finishBracketDrag}
                onPointerCancel={(event) => {
                  if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
                  setDragPreview(null);
                }}
              />)}
            </div>;
          })}
        </div>
        <div className="ruby-character-row">
          {characters.map((character, index) => {
            const absoluteIndex = start + index;
            const selected = selection && absoluteIndex >= selection.start && absoluteIndex < selection.end;
            return <button
              type="button"
              tabIndex={-1}
              className={`ruby-character ${selected ? "selected" : ""}`}
              data-ruby-character={absoluteIndex}
              key={absoluteIndex}
            >{character}</button>;
          })}
        </div>
      </div>
    </div>
    <div className="ruby-range-selection-state">{selection ? `已选 ${selection.end - selection.start} 字` : `共 ${characters.length} 字`}</div>
    <div className="ruby-range-actions">
      <label className="field-label">
        Ruby
        <input
          value={reading}
          placeholder="Ruby"
          disabled={!selection}
          onChange={(event) => setReading(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && selection && reading.trim()) onApply(selection.start, selection.end, reading, selectedGroup?.ruby2 || "", selectedGroup);
          }}
        />
      </label>
      <button
        className="icon-button ruby-range-confirm"
        type="button"
        title={selectedGroup ? "更新 Ruby" : "添加 Ruby"}
        disabled={!selection || !reading.trim()}
        onClick={() => selection && onApply(selection.start, selection.end, reading, selectedGroup?.ruby2 || "", selectedGroup)}
      ><Check size={18} /></button>
      <button
        className="icon-button"
        type="button"
        title="删除所选 Ruby"
        disabled={!selectedGroup}
        onClick={() => {
          if (!selectedGroup) return;
          onApply(selectedGroup.start, selectedGroup.end, "", "", selectedGroup);
          setReading("");
        }}
      ><Trash2 size={17} /></button>
    </div>
  </section>;
}
