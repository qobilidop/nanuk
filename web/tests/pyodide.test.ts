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
    expect(names.length).toBe(1); // the single Nanuk wheel (no [rtl] extra)
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
      expect(run.kind).toBe('parser');
      if (run.kind === 'parser') {
        expect(run.result.verdict).toBe(0);
        expect(run.result.md.slice(1, 4)).toEqual([0xaabb, 0xccdd, 0xee01]);
        // v2 debugger trace: aligned, complete, and agreeing.
        expect(run.trace.steps).toBe(run.result.steps);
        expect(run.trace.records.length).toBe(run.trace.steps);
        expect(run.trace.divergence).toBeNull();
        expect(run.trace.result_match).toBe(true);
      }
    }

    // MAP program: composed parser -> MAP run with the demo FDB.
    const mapSource = readFileSync(join(WEB, 'src', 'programs', 'map_l2fwd.py'), 'utf8');
    const mapCompiled = runtime.compile(mapSource);
    expect(mapCompiled.ok).toBe(true);
    if (mapCompiled.ok) expect(mapCompiled.kind).toBe('map');
    const mapRun = runtime.run(plain.hex);
    expect(mapRun.ok).toBe(true);
    if (mapRun.ok && mapRun.kind === 'map' && !mapRun.result.gated) {
      expect(mapRun.result.verdict).toBe(0);
      expect(mapRun.result.egress).toBe(0x4); // demo FDB: ...ee:01 -> port 2
      expect(mapRun.result.frame).toBe(plain.hex);
      expect(mapRun.trace.map).not.toBeNull();
      expect(mapRun.trace.map!.records.length).toBe(mapRun.trace.map!.steps);
      expect(mapRun.trace.pp.divergence).toBeNull();
      expect(mapRun.trace.map!.divergence).toBeNull();
    } else {
      throw new Error('MAP run was gated or failed');
    }

    // SIIT translator: composed SIIT-parser -> SIIT-MAP run, translating a
    // v4 UDP packet to v6 (RFC 6052). Asserts the exact reference bytes.
    const siitSource = readFileSync(join(WEB, 'src', 'programs', 'siit.py'), 'utf8');
    const siitCompiled = runtime.compile(siitSource);
    expect(siitCompiled.ok).toBe(true);
    if (siitCompiled.ok) expect(siitCompiled.kind).toBe('map');
    const udp46 = presets.find((p: any) => p.name === 'udp46_len25_ttl64');
    const udp46Out =
      'aabbccddee01aabbccddee0286dd600000000021113f0064ff9b0000000000000000c63364020064ff9b0000000000000000cb007107303900350021cc6f000102030405060708090a0b0c0d0e0f101112131415161718';
    const siitRun = runtime.run(udp46.hex);
    expect(siitRun.ok).toBe(true);
    if (siitRun.ok && siitRun.kind === 'map' && !siitRun.result.gated) {
      expect(siitRun.result.verdict).toBe(0); // sent
      expect(siitRun.result.frame).toBe(udp46Out);
      expect(siitRun.trace.map).not.toBeNull();
      expect(siitRun.trace.pp.divergence).toBeNull();
      expect(siitRun.trace.map!.divergence).toBeNull();
      expect(siitRun.trace.map!.result_match).toBe(true);
    } else {
      throw new Error('SIIT run was gated or failed');
    }
  });
});
