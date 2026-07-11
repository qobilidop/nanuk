export interface Params {
  program: string | null; // validated: 'l2l3l4' | 'nanukproto'
  preset: string | null; // preset name, resolved against presets.json later
  packet: string | null; // raw hex, validated by the bridge on run
}

const PROGRAMS = new Set(['l2l3l4', 'nanukproto']);

export function parseParams(search: string): Params {
  const q = new URLSearchParams(search);
  const program = q.get('program');
  return {
    program: program && PROGRAMS.has(program) ? program : null,
    preset: q.get('preset'),
    packet: q.get('packet'),
  };
}
