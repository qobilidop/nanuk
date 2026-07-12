import type { CompileResult, RunResult } from './types';

// Matches both the npm package's and the CDN module's loadPyodide.
export type LoadPyodideFn = (opts?: { indexURL?: string }) => Promise<any>;

export interface InitOpts {
  loadPyodide: LoadPyodideFn;
  indexURL?: string;
  /** Wheel URLs or emfs: paths, dependency-first (nanuk-ir before nanuk-lang). */
  wheelUrls: string[];
  bridgeSource: string;
  onStatus?: (msg: string) => void;
}

export interface NanukRuntime {
  compile(source: string): CompileResult;
  run(packetHex: string): RunResult;
}

export async function initRuntime(opts: InitOpts): Promise<NanukRuntime> {
  const status = opts.onStatus ?? (() => {});
  status('loading Python runtime…');
  const py = await opts.loadPyodide({ indexURL: opts.indexURL });
  status('installing Nanuk packages…');
  await py.loadPackage('micropip');
  const micropip = py.pyimport('micropip');
  for (const url of opts.wheelUrls) {
    await micropip.install(url); // resolves protobuf from PyPI as a dep
  }
  status('loading bridge…');
  py.runPython(opts.bridgeSource);
  const compileFn = py.globals.get('compile_source');
  const runFn = py.globals.get('run_packet');
  status('ready');
  return {
    compile: (source) => JSON.parse(compileFn(source)) as CompileResult,
    run: (packetHex) => JSON.parse(runFn(packetHex)) as RunResult,
  };
}
