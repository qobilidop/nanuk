<script lang="ts">
  import { onMount } from 'svelte';
  import CodePane from './lib/panes/CodePane.svelte';
  import PacketPanel from './lib/PacketPanel.svelte';
  import { initBrowserRuntime } from './lib/runtime-browser';
  import type { NanukRuntime } from './lib/py';
  import type { CompileOk, BridgeError } from './lib/types';
  import type { NamedRange } from './lib/panes/highlight';
  import { parseParams } from './lib/params';
  import l2l3l4Src from './programs/l2l3l4.py?raw';
  import nanukprotoSrc from './programs/nanukproto.py?raw';

  const params = parseParams(location.search);
  let runtime: NanukRuntime | null = $state(null);
  let status = $state('starting…');
  let source = $state(params.program === 'nanukproto' ? nanukprotoSrc : l2l3l4Src);
  let compiled: CompileOk | null = $state(null);
  let compileError: BridgeError | null = $state(null);

  function recompile(src: string) {
    if (!runtime) return;
    const result = runtime.compile(src);
    if (result.ok) {
      compiled = result;
      compileError = null;
    } else {
      compileError = result.error;
    }
  }

  let timer: ReturnType<typeof setTimeout>;
  function onEdit(src: string) {
    source = src;
    clearTimeout(timer);
    timer = setTimeout(() => recompile(src), 300);
  }

  onMount(async () => {
    try {
      runtime = await initBrowserRuntime((s) => (status = s));
      recompile(source);
    } catch (e) {
      status = `failed to load: ${e}`;
    }
  });

  const edslRanges: NamedRange[] = $derived.by(() =>
    compiled
      ? compiled.states.filter((s) => s.edsl)
          .map((s) => ({ name: s.name, range: s.edsl! }))
      : [],
  );
  const irRanges: NamedRange[] = $derived.by(() =>
    compiled ? compiled.states.map((s) => ({ name: s.name, range: s.ir })) : [],
  );
  const asmRanges: NamedRange[] = $derived.by(() =>
    compiled ? compiled.states.map((s) => ({ name: s.name, range: s.asm })) : [],
  );
</script>

<div class="app">
  <header class="top">
    <a class="brand" href="/nanuk/">nanuk</a>
    <span class="title">playground</span>
    <span class="status" class:ready={status === 'ready'}>{status}</span>
  </header>
  <main>
    <div class="panes">
      <div class="edsl-col">
        <CodePane title="eDSL (Python)" doc={source} editable python
          ranges={edslRanges} {onEdit} />
        {#if compileError}
          <div class="banner" role="alert">
            <strong>{compileError.kind}</strong>
            {#if compileError.line}(line {compileError.line}){/if}:
            {compileError.message}
          </div>
        {/if}
      </div>
      <CodePane title="nanuk IR" doc={compiled?.ir_text ?? ''} editable={false}
        python={false} ranges={irRanges} />
      <CodePane title="assembly" doc={compiled?.asm_text ?? ''} editable={false}
        python={false} ranges={asmRanges} />
    </div>
    <aside>
      <PacketPanel {runtime} ready={compiled !== null}
        initialPacket={params.packet} initialPreset={params.preset} />
    </aside>
  </main>
</div>

<style>
  .app { height: 100%; display: flex; flex-direction: column; }
  .top {
    display: flex; align-items: baseline; gap: 0.6rem;
    padding: 0.4rem 0.8rem; border-bottom: 1px solid var(--border);
  }
  .brand { font-weight: 700; color: var(--accent); text-decoration: none; }
  .status { margin-left: auto; font-size: 0.8rem; color: var(--fg-muted); }
  .status.ready { color: var(--ok); }
  main { flex: 1; display: flex; min-height: 0; }
  .panes {
    flex: 1; display: grid; grid-template-columns: 1.2fr 1fr 1fr;
    gap: 1px; background: var(--border); min-width: 0;
  }
  .edsl-col { display: flex; flex-direction: column; min-width: 0; background: var(--bg); }
  .banner {
    padding: 0.5rem 0.8rem; font-size: 0.85rem;
    background: var(--err-bg); color: var(--err); border-top: 1px solid var(--border);
  }
  aside { width: 20rem; border-left: 1px solid var(--border); overflow: auto; }
</style>
