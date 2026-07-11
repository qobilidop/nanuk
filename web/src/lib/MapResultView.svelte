<script lang="ts">
  import type { MapResultJson, MapGatedJson } from './types';
  let { result }: { result: MapResultJson | MapGatedJson } = $props();

  const VERDICTS = ['sent', 'drop', 'error'] as const;
  const ERRORS = [
    'none', 'window violation', 'step budget', 'illegal', 'pc range',
    'header absent', 'send range',
  ];
  const ports = (bitmap: number) =>
    [0, 1, 2, 3].filter((p) => bitmap & (1 << p));
  const frameChunks = (hex: string) => hex.match(/.{1,2}/g) ?? [];
</script>

<div class="result">
  {#if result.gated}
    <p>
      <span class="badge v1">gated</span>
      <span class="meta">
        the parser {result.pp_verdict === 2 ? 'errored on' : 'dropped'} this
        packet — the MAP never ran
      </span>
    </p>
  {:else}
    <p>
      <span class="badge v{result.verdict}">{VERDICTS[result.verdict]}</span>
      {#if result.verdict === 2}<span class="err">{ERRORS[result.error]}</span>{/if}
      <span class="meta">{result.steps} steps</span>
    </p>
    {#if result.verdict === 0}
      <table>
        <tbody>
          <tr>
            <td>egress</td>
            <td>
              {#if result.egress === 0}(no ports){:else}
                port{ports(result.egress).length > 1 ? 's' : ''}
                {ports(result.egress).join(', ')}
              {/if}
              <span class="dim">bitmap 0b{result.egress.toString(2).padStart(4, '0')}</span>
            </td>
          </tr>
          <tr>
            <td>delta</td>
            <td>
              {result.delta > 0 ? `+${result.delta} (prepended)` :
               result.delta < 0 ? `${result.delta} (stripped)` : '0'}
            </td>
          </tr>
        </tbody>
      </table>
      {#if result.frame}
        <div class="frame">
          <span class="caption">transmitted frame ({result.frame.length / 2} bytes)</span>
          <code>
            {#each frameChunks(result.frame) as byte, i}<span
              class:headroom={result.delta > 0 && i < result.delta}>{byte}</span
            >{/each}
          </code>
        </div>
      {/if}
    {/if}
  {/if}
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
  td { border-top: 1px solid var(--border); padding: 0.15rem 0.3rem; font-family: var(--font-mono); }
  .dim { color: var(--fg-muted); margin-left: 0.4rem; }
  .frame { margin-top: 0.6rem; }
  .caption { font-weight: 600; color: var(--fg-muted); display: block; }
  code {
    display: block; margin-top: 0.3rem; padding: 0.4rem;
    background: var(--bg-inset); border: 1px solid var(--border);
    border-radius: 4px; font-size: 0.75rem; word-break: break-all;
    line-height: 1.5;
  }
  code span { margin-right: 0.35em; }
  code span.headroom { color: var(--accent); font-weight: 700; }
</style>
