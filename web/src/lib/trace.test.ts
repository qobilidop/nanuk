import { describe, expect, it } from 'vitest';
import {
  activeAt,
  globalDivergence,
  phaseBoundaries,
  totalSteps,
  type Phase,
} from './trace';
import type { TraceJson, TraceRecord } from './types';

function rec(step: number, state: string): TraceRecord {
  return {
    step,
    pc: step,
    asm_line: step + 2,
    regs: ['0x0', '0x0', '0x0', '0x0'],
    reg_names: {},
    state,
    ir_line: step + 1,
    op_label: `op${step}`,
    values: {},
    cursor: 0,
  };
}

function traceOf(n: number, state: string, divergeAt: number | null = null): TraceJson {
  return {
    steps: n,
    records: Array.from({ length: n }, (_, i) => rec(i, state)),
    divergence: divergeAt === null ? null : { step: divergeAt, what: 'cursor' },
    result_match: divergeAt === null,
  };
}

function phase(label: string, n: number, divergeAt: number | null = null): Phase {
  return { label, kind: 'parser', inPanes: true, trace: traceOf(n, label, divergeAt) };
}

describe('trace phase helpers', () => {
  const phases = [phase('pp', 3), phase('map', 2)];

  it('totals and boundaries', () => {
    expect(totalSteps(phases)).toBe(5);
    expect(phaseBoundaries(phases)).toEqual([3]);
    expect(phaseBoundaries([phase('one', 4)])).toEqual([]);
  });

  it('resolves global steps to phase-local records', () => {
    expect(activeAt(phases, 0)!).toMatchObject({ local: 0 });
    expect(activeAt(phases, 2)!.phase.label).toBe('pp');
    expect(activeAt(phases, 3)!.phase.label).toBe('map');
    expect(activeAt(phases, 3)!.local).toBe(0);
    expect(activeAt(phases, 4)!.local).toBe(1);
  });

  it('clamps past-the-end and negative steps', () => {
    expect(activeAt(phases, 99)!.phase.label).toBe('map');
    expect(activeAt(phases, 99)!.local).toBe(1);
    expect(activeAt(phases, -5)!.local).toBe(0);
    expect(activeAt([], 0)).toBeNull();
  });

  it('reports the first divergence with a global step', () => {
    expect(globalDivergence(phases)).toBeNull();
    const diverged = [phase('pp', 3), phase('map', 2, 1)];
    expect(globalDivergence(diverged)).toMatchObject({
      step: 4,
      what: 'cursor',
      phaseLabel: 'map',
    });
  });
});
