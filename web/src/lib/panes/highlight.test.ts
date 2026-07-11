import { describe, expect, it } from 'vitest';
import { lineRangesToRegions, stateAtLine } from './highlight';

const ranges = [
  { name: 'start', range: [1, 3] as [number, number] },
  { name: 'vlan', range: [5, 6] as [number, number] },
];

describe('provenance range helpers', () => {
  it('maps doc lines to char regions', () => {
    const doc = 'a\nbb\nccc\n\ndd\ne\n';
    const regions = lineRangesToRegions(doc, ranges);
    expect(regions[0]).toEqual({ name: 'start', from: 0, to: 8 }); // "a\nbb\nccc"
    expect(regions[1]).toEqual({ name: 'vlan', from: 10, to: 14 }); // "dd\ne"
  });
  it('finds the state at a line', () => {
    expect(stateAtLine(ranges, 2)).toBe('start');
    expect(stateAtLine(ranges, 4)).toBeNull();
    expect(stateAtLine(ranges, 6)).toBe('vlan');
  });
  it('clamps ranges past the end of the doc', () => {
    const regions = lineRangesToRegions('a\nb\n', [{ name: 's', range: [1, 99] }]);
    expect(regions[0].to).toBe(3);
  });
});
