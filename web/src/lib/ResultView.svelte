<script lang="ts">
  import type { ParseResultJson } from './types';
  let { result }: { result: ParseResultJson } = $props();

  const VERDICTS = ['accept', 'drop', 'error'] as const;
  const ERRORS = ['none', 'header violation', 'step budget', 'illegal', 'pc range', 'smd range'];
  const hex = (v: number) => '0x' + v.toString(16).padStart(4, '0');
</script>

<div class="result">
  <p>
    <span class="badge v{result.verdict}">{VERDICTS[result.verdict]}</span>
    {#if result.verdict === 2}<span class="err">{ERRORS[result.error]}</span>{/if}
    <span class="meta">payload@{result.payload_offset} · {result.steps} steps</span>
  </p>
  <table>
    <caption>headers</caption>
    <tbody>
      {#each result.hdr_present as present, id}
        {#if present}
          <tr><td>hdr[{id}]</td><td>offset {result.hdr_offset[id]}</td></tr>
        {/if}
      {/each}
    </tbody>
  </table>
  <table>
    <caption>SMD</caption>
    <tbody>
      {#each result.smd as slot, i}
        <tr class:zero={slot === 0}><td>[{i}]</td><td>{hex(slot)}</td></tr>
      {/each}
    </tbody>
  </table>
</div>

<style>
  .result { font-size: 0.85rem; }
  .badge { padding: 0.1rem 0.5rem; border-radius: 999px; font-weight: 700; color: #fff; }
  .badge.v0 { background: var(--ok); }
  .badge.v1 { background: var(--warn); }
  .badge.v2 { background: var(--err); }
  .err { color: var(--err); margin-left: 0.4rem; }
  .meta { color: var(--fg-muted); margin-left: 0.4rem; }
  table { width: 100%; border-collapse: collapse; margin-top: 0.6rem; }
  caption { text-align: left; font-weight: 600; color: var(--fg-muted); }
  td { border-top: 1px solid var(--border); padding: 0.15rem 0.3rem; font-family: var(--font-mono); }
  tr.zero td { color: var(--fg-muted); }
</style>
