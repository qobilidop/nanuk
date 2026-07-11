import { StateEffect, StateField } from '@codemirror/state';
import { Decoration, EditorView, type DecorationSet } from '@codemirror/view';
import type { LineRange } from '../types';

export interface NamedRange {
  name: string;
  range: LineRange;
}
export interface Region {
  name: string;
  from: number;
  to: number;
}

/** Convert 1-based inclusive line ranges to character regions of `doc`. */
export function lineRangesToRegions(doc: string, ranges: NamedRange[]): Region[] {
  const lines = doc.split('\n');
  const starts: number[] = [0];
  for (const line of lines) starts.push(starts[starts.length - 1] + line.length + 1);
  // A trailing newline produces a final empty line; don't clamp into it.
  const lastLine = Math.max(1, lines.length - (lines[lines.length - 1] === '' ? 1 : 0));
  const docLen = doc.length;
  return ranges.map(({ name, range: [lo, hi] }) => {
    const l = Math.max(1, Math.min(lo, lastLine));
    const h = Math.max(l, Math.min(hi, lastLine));
    return {
      name,
      from: Math.min(starts[l - 1], docLen),
      to: Math.min(starts[h - 1] + lines[h - 1].length, docLen),
    };
  });
}

export function stateAtLine(ranges: NamedRange[], line: number): string | null {
  for (const { name, range: [lo, hi] } of ranges) {
    if (line >= lo && line <= hi) return name;
  }
  return null;
}

export const setHighlightRegion = StateEffect.define<Region | null>();

const stateHighlight = Decoration.mark({ class: 'cm-state-hl' });

export const highlightField = StateField.define<DecorationSet>({
  create: () => Decoration.none,
  update(deco, tr) {
    deco = deco.map(tr.changes);
    for (const e of tr.effects) {
      if (e.is(setHighlightRegion)) {
        deco =
          e.value && e.value.to > e.value.from
            ? Decoration.set([stateHighlight.range(e.value.from, e.value.to)])
            : Decoration.none;
      }
    }
    return deco;
  },
  provide: (f) => EditorView.decorations.from(f),
});
