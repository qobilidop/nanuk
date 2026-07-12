import type { Divergence, TraceJson, TraceRecord } from './types';

/** One scrubber phase: a parser run, or the MAP leg of a composed run.
 * A parser program yields one phase; a MAP program yields two (the baked
 * l2l3l4 PP phase, then the MAP itself) or one when the parser gated. */
export interface Phase {
  label: string;
  kind: 'parser' | 'map';
  /** true when this phase's program is the one shown in the panes */
  inPanes: boolean;
  trace: TraceJson;
}

export interface Active {
  phase: Phase;
  record: TraceRecord;
  /** step index local to the phase */
  local: number;
}

export function totalSteps(phases: Phase[]): number {
  return phases.reduce((n, p) => n + p.trace.steps, 0);
}

/** Phase-start offsets after the first phase (slider tick positions). */
export function phaseBoundaries(phases: Phase[]): number[] {
  const out: number[] = [];
  let base = 0;
  for (const p of phases.slice(0, -1)) {
    base += p.trace.steps;
    out.push(base);
  }
  return out;
}

/** Resolve a global step index to its phase and record; null when there
 * are no phases or no records (defensive: budget-0 traces don't occur). */
export function activeAt(phases: Phase[], step: number): Active | null {
  let s = Math.max(0, step);
  for (const p of phases) {
    if (s < p.trace.records.length) {
      return { phase: p, record: p.trace.records[s], local: s };
    }
    s -= p.trace.records.length;
  }
  const last = phases[phases.length - 1];
  const rec = last?.trace.records[last.trace.records.length - 1];
  return rec ? { phase: last, record: rec, local: last.trace.records.length - 1 } : null;
}

/** First divergence across phases, as a global step index. */
export function globalDivergence(
  phases: Phase[],
): (Divergence & { phaseLabel: string }) | null {
  let base = 0;
  for (const p of phases) {
    const d = p.trace.divergence;
    if (d) return { step: base + d.step, what: d.what, phaseLabel: p.label };
    base += p.trace.steps;
  }
  return null;
}
