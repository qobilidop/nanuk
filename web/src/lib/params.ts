export interface Params {
  program: string | null;
  preset: string | null;
  packet: string | null;
}
export function parseParams(_search: string): Params {
  return { program: null, preset: null, packet: null };
}
