<script lang="ts">
  import {
    activeAt, globalDivergence, phaseBoundaries, totalSteps, type Phase,
  } from './trace';

  let { phases, step, onStep, gatedNote = null }: {
    phases: Phase[];
    step: number;
    onStep: (n: number) => void;
    gatedNote?: string | null;
  } = $props();

  const total = $derived(totalSteps(phases));
  const active = $derived(activeAt(phases, step));
  const divergence = $derived(globalDivergence(phases));
  const ticks = $derived(phaseBoundaries(phases));

  let playing = $state(false);
  $effect(() => {
    if (!playing) return;
    const t = setInterval(() => {
      if (step >= total - 1) {
        playing = false;
      } else {
        onStep(step + 1);
      }
    }, 200);
    return () => clearInterval(t);
  });
  // A new run resets the scrubber; stop any stale playback.
  $effect(() => {
    void phases;
    playing = false;
  });

  function go(n: number) {
    playing = false;
    onStep(Math.max(0, Math.min(n, total - 1)));
  }

  function onKey(e: KeyboardEvent) {
    if (e.key === 'ArrowLeft') { go(step - 1); e.preventDefault(); }
    if (e.key === 'ArrowRight') { go(step + 1); e.preventDefault(); }
  }

  const REGS = ['r0', 'r1', 'r2', 'r3'] as const;
</script>

<!-- svelte-ignore a11y_no_noninteractive_element_interactions -- the strip
  hosts focusable transport controls; ←/→ stepping is a convenience on top -->
<section class="debugger" tabindex="-1" onkeydown={onKey} aria-label="execution debugger">
  <div class="transport">
    <button title="first step" onclick={() => go(0)} disabled={step === 0}>⏮</button>
    <button title="previous step (←)" onclick={() => go(step - 1)} disabled={step === 0}>◀</button>
    <button title={playing ? 'pause' : 'play'} class="play"
      onclick={() => (playing = !playing)} disabled={step >= total - 1 && !playing}>
      {playing ? '❚❚' : '▶︎'}
    </button>
    <button title="next step (→)" onclick={() => go(step + 1)} disabled={step >= total - 1}>▶</button>
    <button title="last step" onclick={() => go(total - 1)} disabled={step >= total - 1}>⏭</button>
    <div class="slider">
      <input type="range" min="0" max={Math.max(0, total - 1)} value={step}
        oninput={(e) => go(e.currentTarget.valueAsNumber)} aria-label="step" />
      {#each ticks as t}
        <span class="tick" style="left: {(t / Math.max(1, total - 1)) * 100}%"
          title="phase boundary"></span>
      {/each}
    </div>
    <span class="readout">
      step {step + 1} / {total}
      {#if active && phases.length > 1}· {active.phase.label}{/if}
    </span>
    {#if divergence}
      <button class="badge diverged" onclick={() => go(divergence.step)}
        title="jump to the diverging step">
        levels diverged at step {divergence.step + 1} ({divergence.what}) — this is a Nanuk bug
      </button>
    {:else}
      <span class="badge agree" title="IR interpreter and ISS agree at every step">
        levels agree
      </span>
    {/if}
  </div>

  {#if gatedNote}
    <p class="gated">{gatedNote}</p>
  {/if}

  {#if active}
    {@const rec = active.record}
    <div class="cards">
      <div class="card">
        <h3>IR — {rec.state}{#if !active.phase.inPanes}&nbsp;<span class="dim">({active.phase.label})</span>{/if}</h3>
        <p class="op">{rec.op_label || '…'}</p>
        {#if Object.keys(rec.values).length}
          <table><tbody>
            {#each Object.entries(rec.values) as [name, v]}
              <tr><td>{name}</td><td>{v}</td></tr>
            {/each}
          </tbody></table>
        {/if}
      </div>
      <div class="card">
        <h3>ASM — pc {rec.pc}</h3>
        <table><tbody>
          {#each REGS as r, i}
            <tr>
              <td>{r}</td>
              <td>{rec.regs[i]}</td>
              <td class="dim">{rec.reg_names[r] ?? ''}</td>
            </tr>
          {/each}
          {#if rec.cursor !== null}
            <tr><td>cursor</td><td>{rec.cursor}</td><td class="dim">byte offset</td></tr>
          {/if}
        </tbody></table>
        {#if rec.writes?.length}
          <p class="effect">
            writes: {#each rec.writes as [addr, data]}<code>[{addr}]={data}</code>{/each}
          </p>
        {/if}
        {#if rec.lookup}
          <p class="effect">
            lookup t{rec.lookup[0]}[{rec.lookup[1]}] →
            {rec.lookup[2] ? `hit ${rec.lookup[3]}` : 'miss'}
          </p>
        {/if}
      </div>
    </div>
  {/if}
</section>

<style>
  .debugger {
    border-top: 1px solid var(--border);
    padding: 0.4rem 0.8rem 0.6rem;
    background: var(--bg);
    outline: none;
  }
  .transport { display: flex; align-items: center; gap: 0.4rem; flex-wrap: wrap; }
  .transport button {
    background: var(--bg-inset); color: var(--fg);
    border: 1px solid var(--border); border-radius: 4px;
    padding: 0.1rem 0.45rem; cursor: pointer; font-size: 0.8rem;
  }
  .transport button:disabled { opacity: 0.4; cursor: default; }
  .transport button.play { color: var(--accent); font-weight: 700; }
  .slider { position: relative; flex: 1; min-width: 8rem; display: flex; }
  .slider input { width: 100%; accent-color: var(--accent); }
  .tick {
    position: absolute; top: 0; bottom: 0; width: 2px;
    background: var(--warn); pointer-events: none;
  }
  .readout { font-size: 0.8rem; color: var(--fg-muted); white-space: nowrap; }
  .badge {
    font-size: 0.75rem; font-weight: 700; border-radius: 999px;
    padding: 0.1rem 0.6rem; border: none;
  }
  .badge.agree { background: var(--ok); color: #fff; }
  .badge.diverged { background: var(--err); color: #fff; cursor: pointer; }
  .gated { font-size: 0.8rem; color: var(--warn); margin: 0.3rem 0 0; }
  .cards {
    display: grid; grid-template-columns: 1fr 1fr; gap: 0.8rem;
    margin-top: 0.5rem;
  }
  .card {
    background: var(--bg-inset); border: 1px solid var(--border);
    border-radius: 6px; padding: 0.4rem 0.6rem;
    min-height: 6.5rem; overflow: auto; font-size: 0.8rem;
  }
  .card h3 {
    margin: 0 0 0.2rem; font-size: 0.7rem; text-transform: uppercase;
    letter-spacing: 0.08em; color: var(--fg-muted);
  }
  .op { margin: 0 0 0.2rem; font-family: var(--font-mono); }
  .card table { border-collapse: collapse; }
  .card td {
    font-family: var(--font-mono); padding: 0.05rem 0.6rem 0.05rem 0;
  }
  .dim { color: var(--fg-muted); }
  .effect { margin: 0.2rem 0 0; font-size: 0.75rem; }
  .effect code { margin-right: 0.4rem; }
</style>
