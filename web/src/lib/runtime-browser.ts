import { PYODIDE_CDN } from './pyodide-version';
import { initRuntime, type NanukRuntime } from './py';

const BASE = import.meta.env.BASE_URL;

export async function initBrowserRuntime(
  onStatus: (msg: string) => void,
): Promise<NanukRuntime> {
  const [{ loadPyodide }, manifest, bridgeSource] = await Promise.all([
    import(/* @vite-ignore */ `${PYODIDE_CDN}pyodide.mjs`),
    fetch(`${BASE}wheels/manifest.json`).then((r) => r.json()),
    fetch(`${BASE}bridge.py`).then((r) => r.text()),
  ]);
  const wheelUrls = (manifest.wheels as string[])
    .sort() // nanuk.ir before nanuk.lang, dependency-first
    .map((w) => new URL(`${BASE}wheels/${w}`, location.origin).href);
  return initRuntime({
    loadPyodide,
    indexURL: PYODIDE_CDN,
    wheelUrls,
    bridgeSource,
    onStatus,
  });
}
