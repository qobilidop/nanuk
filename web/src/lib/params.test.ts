import { describe, expect, it } from 'vitest';
import { parseParams } from './params';

describe('parseParams', () => {
  it('reads program, preset, packet', () => {
    expect(parseParams('?program=nanukproto&preset=qinq&packet=aabb')).toEqual({
      program: 'nanukproto',
      preset: 'qinq',
      packet: 'aabb',
    });
  });
  it('rejects unknown program names', () => {
    expect(parseParams('?program=evil').program).toBeNull();
  });
  it('handles empty search', () => {
    expect(parseParams('')).toEqual({ program: null, preset: null, packet: null });
  });
});
