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

export interface CompileOk {
  ok: true;
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
export interface RunOk {
  ok: true;
  result: ParseResultJson;
}
export interface RunFail {
  ok: false;
  error: BridgeError;
}
export type RunResult = RunOk | RunFail;
