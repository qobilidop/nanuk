<script lang="ts">
  import { onMount } from 'svelte';
  import type { NanukRuntime } from './py';
  import type { RunOk, RunResult, BridgeError } from './types';
  import ResultView from './ResultView.svelte';
  import MapResultView from './MapResultView.svelte';

  let { runtime, ready, initialPacket, initialPreset, onRun, runOut, runError, cursorByte }: {
    runtime: NanukRuntime | null; ready: boolean;
    initialPacket: string | null; initialPreset: string | null;
    onRun: (out: RunResult) => void;
    runOut: RunOk | null; runError: BridgeError | null;
    cursorByte: number | null;
  } = $props();

  interface Preset { name: string; hex: string; note: string }
  let presets: Preset[] = $state([]);
  // svelte-ignore state_referenced_locally -- deliberate: the URL param seeds the initial value only
  let packetHex = $state(initialPacket ?? '');
  let selected: string | null = $state(null);

  onMount(async () => {
    presets = await fetch(`${import.meta.env.BASE_URL}presets.json`).then((r) => r.json());
    if (!initialPacket && initialPreset) {
      const p = presets.find((p) => p.name === initialPreset);
      if (p) {
        packetHex = p.hex;
        selected = p.name;
      }
    }
  });

  function run() {
    if (!runtime) return;
    onRun(runtime.run(packetHex));
  }

  /** Byte chunks of the current packet, for the cursor view. */
  const bytes = $derived.by(() => {
    const cleaned = packetHex.replace(/\s+/g, '');
    return cleaned.length % 2 === 0 ? (cleaned.match(/.{2}/g) ?? []) : [];
  });
</script>

<div class="panel">
  <h2>packet</h2>
  <div class="chips">
    {#each presets as p}
      <button class="chip" class:selected={selected === p.name} title={p.note}
        onclick={() => { packetHex = p.hex; selected = p.name; if (ready) run(); }}>
        {p.name}</button>
    {/each}
  </div>
  <textarea rows="4" bind:value={packetHex} oninput={() => (selected = null)}
    placeholder="hex bytes, e.g. aabbccddee01…" spellcheck="false"></textarea>
  <button class="run" disabled={!ready || !packetHex.trim()} onclick={run}>
    Run packet
  </button>
  {#if runOut && cursorByte !== null && bytes.length}
    <div class="cursorview">
      <span class="caption">
        parser cursor @ {cursorByte}{cursorByte >= bytes.length ? ' (payload start — past the shown bytes)' : ''}
      </span>
      <code>
        {#each bytes as b, i}<span class:at={i === cursorByte}>{b}</span>{/each}
      </code>
    </div>
  {/if}
  {#if runError}<p class="error">{runError.message}</p>{/if}
  {#if runOut?.kind === 'parser'}<ResultView result={runOut.result} />{/if}
  {#if runOut?.kind === 'map'}<MapResultView result={runOut.result} />{/if}
</div>

<style>
  .panel { padding: 0.8rem; display: flex; flex-direction: column; gap: 0.6rem; }
  h2 { margin: 0; font-size: 0.75rem; text-transform: uppercase;
       letter-spacing: 0.08em; color: var(--fg-muted); }
  .chips { display: flex; flex-wrap: wrap; gap: 0.3rem; }
  .chip { font-size: 0.75rem; padding: 0.15rem 0.5rem; border-radius: 999px;
          border: 1px solid var(--border); background: none; color: var(--fg);
          cursor: pointer; }
  .chip:hover { border-color: var(--accent); color: var(--accent); }
  .chip.selected { background: var(--accent); border-color: var(--accent); color: #fff; }
  textarea { font-family: var(--font-mono); font-size: 0.8rem;
             background: var(--bg-inset); color: var(--fg);
             border: 1px solid var(--border); border-radius: 4px; padding: 0.4rem; }
  .run { padding: 0.4rem; border-radius: 4px; border: none; font-weight: 600;
         background: var(--accent); color: #fff; cursor: pointer; }
  .run:disabled { opacity: 0.5; cursor: default; }
  .error { color: var(--err); font-size: 0.85rem; margin: 0; }
  .cursorview .caption {
    font-size: 0.7rem; font-weight: 600; color: var(--fg-muted);
    display: block;
  }
  .cursorview code {
    display: block; margin-top: 0.2rem; padding: 0.4rem;
    background: var(--bg-inset); border: 1px solid var(--border);
    border-radius: 4px; font-size: 0.75rem; word-break: break-all;
    line-height: 1.5;
  }
  .cursorview code span { margin-right: 0.35em; }
  .cursorview code span.at {
    background: var(--exec-hl); outline: 1px solid var(--ok);
    border-radius: 2px; font-weight: 700;
  }
</style>
