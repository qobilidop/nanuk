/** 1-based inclusive line range. */
export type LineRange = [number, number];

export interface OpProvenance {
  label: string; // human label, e.g. "eth.dst" or "dispatch eth.ethertype"
  ir_line: number; // 1-based line in ir_text
  asm_lines: number[]; // 1-based lines in asm_text (may be empty: re-anchor mark)
}

export interface StateProvenance {
  name: string;
  edsl: LineRange | null; // null if the state fn wasn't found in the source
  ir: LineRange;
  asm: LineRange;
  ops: OpProvenance[];
}

export interface BridgeError {
  kind: 'syntax' | 'compile' | 'runtime' | 'no_build_ir' | 'bad_hex' | 'no_program';
  message: string;
  line: number | null; // 1-based line in the eDSL source, when known
}

export type ProgramKind = 'parser' | 'map';

export interface CompileOk {
  ok: true;
  kind: ProgramKind;
  ir_text: string;
  asm_text: string;
  states: StateProvenance[];
}
export interface CompileFail {
  ok: false;
  error: BridgeError;
}
export type CompileResult = CompileOk | CompileFail;

export interface ParseResultJson {
  verdict: 0 | 1 | 2;
  error: number;
  payload_offset: number;
  steps: number;
  hdr_present: number[];
  hdr_offset: number[];
  smd: number[];
}
export interface MapResultJson {
  gated: false;
  verdict: 0 | 1 | 2; // sent | drop | error
  error: number;
  egress: number; // port bitmap
  delta: number; // signed head delta
  steps: number;
  frame: string | null; // transmitted frame, hex
}
export interface MapGatedJson {
  gated: true; // the parser refused the packet; the MAP never ran
  pp_verdict: number;
  pp_error: number;
}
/** One machine step: the ISS record joined with its covering interp event.
 * Register and value contents are hex strings (64-bit values overflow JS
 * numbers). MAP records add window-write and lookup effects. */
export interface TraceRecord {
  step: number;
  pc: number;
  asm_line: number | null;
  regs: [string, string, string, string];
  reg_names: Record<string, string>; // live value name per register (no r3)
  state: string;
  ir_line: number | null;
  op_label: string;
  values: Record<string, string>;
  cursor: number | null; // parser phases only
  writes?: [number, string][]; // MAP: [window addr, hex bytes]
  lookup?: [number, string, boolean, string] | null; // [table, key, hit, action]
}

export interface Divergence {
  step: number;
  what: string;
}

/** A recorded two-level execution; divergence null = levels agree. */
export interface TraceJson {
  steps: number;
  records: TraceRecord[];
  divergence: Divergence | null;
  result_match: boolean;
}

/** Composed run: PP phase then MAP phase (map null when the parser gated). */
export interface MapTraceJson {
  pp: TraceJson;
  map: TraceJson | null;
}

export interface RunOkParser {
  ok: true;
  kind: 'parser';
  result: ParseResultJson;
  trace: TraceJson;
}
export interface RunOkMap {
  ok: true;
  kind: 'map';
  result: MapResultJson | MapGatedJson;
  trace: MapTraceJson;
}
export type RunOk = RunOkParser | RunOkMap;
export interface RunFail {
  ok: false;
  error: BridgeError;
}
export type RunResult = RunOk | RunFail;
