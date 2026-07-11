// The one true integration risk: wheels + protobuf + bridge inside a real
// Pyodide. Runs in Node via the npm pyodide package; needs network (PyPI
// for protobuf, CDN for micropip). ~1-2 min cold.
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { loadPyodide } from 'pyodide';
import { initRuntime } from '../src/lib/py';

const WEB = join(__dirname, '..');
const WHEELS = join(WEB, 'public', 'wheels');

describe.skipIf(process.env.NANUK_SKIP_PYODIDE === '1')('pyodide integration', () => {
  it('compiles and runs the default program end to end', async () => {
    const py = await loadPyodide();
    py.FS.mkdir('/wheels');
    const names = readdirSync(WHEELS).filter((f) => f.endsWith('.whl')).sort();
    expect(names.length).toBe(2);
    for (const name of names) {
      py.FS.writeFile(`/wheels/${name}`, readFileSync(join(WHEELS, name)));
    }
    const runtime = await initRuntime({
      loadPyodide: async () => py, // reuse the loaded instance
      wheelUrls: names.map((n) => `emfs:/wheels/${n}`),
      bridgeSource: readFileSync(join(WEB, 'py', 'bridge.py'), 'utf8'),
    });
    const source = readFileSync(join(WEB, 'src', 'programs', 'l2l3l4.py'), 'utf8');
    const compiled = runtime.compile(source);
    expect(compiled.ok).toBe(true);
    if (!compiled.ok) return;
    expect(compiled.states.map((s) => s.name)).toContain('udp_hdr');

    const presets = JSON.parse(readFileSync(join(WEB, 'public', 'presets.json'), 'utf8'));
    const plain = presets.find((p: any) => p.name === 'plain_ipv4_udp');
    const run = runtime.run(plain.hex);
    expect(run.ok).toBe(true);
    if (run.ok) {
      expect(run.result.verdict).toBe(0);
      expect(run.result.smd.slice(0, 3)).toEqual([0xaabb, 0xccdd, 0xee01]);
    }
  });
});
