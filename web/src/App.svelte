<script lang="ts">
  import { onMount } from 'svelte';
  import CodePane from './lib/panes/CodePane.svelte';
  import DebuggerPanel from './lib/DebuggerPanel.svelte';
  import PacketPanel from './lib/PacketPanel.svelte';
  import { initBrowserRuntime } from './lib/runtime-browser';
  import type { NanukRuntime } from './lib/py';
  import type { CompileOk, BridgeError, RunOk, RunResult } from './lib/types';
  import type { NamedRange } from './lib/panes/highlight';
  import { activeAt, type Phase } from './lib/trace';
  import { parseParams } from './lib/params';
  import l2l3l4Src from './programs/l2l3l4.py?raw';
  import nanukprotoSrc from './programs/nanukproto.py?raw';
  import mapL2fwdSrc from './programs/map_l2fwd.py?raw';
  import siitSrc from './programs/siit.py?raw';

  const params = parseParams(location.search);
  let runtime: NanukRuntime | null = $state(null);
  let status = $state('starting…');
  const PROGRAM_SOURCES: Record<string, string> = {
    l2l3l4: l2l3l4Src,
    nanukproto: nanukprotoSrc,
    map_l2fwd: mapL2fwdSrc,
    siit: siitSrc,
  };
  let programName = $state(params.program ?? 'l2l3l4');
  let source = $state(PROGRAM_SOURCES[params.program ?? 'l2l3l4']);

  function selectProgram(name: string) {
    programName = name;
    source = PROGRAM_SOURCES[name];
    recompile(source);
  }
  let compiled: CompileOk | null = $state(null);
  let compileError: BridgeError | null = $state(null);
  let runOut: RunOk | null = $state(null);
  let runError: BridgeError | null = $state(null);
  let currentStep = $state(0);

  function recompile(src: string) {
    if (!runtime) return;
    const result = runtime.compile(src);
    if (result.ok) {
      compiled = result;
      compileError = null;
      // The program changed: any recorded trace points at stale lines.
      runOut = null;
      runError = null;
    } else {
      compileError = result.error;
    }
  }

  function onRun(out: RunResult) {
    if (out.ok) {
      runOut = out;
      runError = null;
    } else {
      runError = out.error;
      runOut = null;
    }
    currentStep = 0;
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

  // --- Debugger (v2): phases, the active record, pane highlighting -------
  const phases: Phase[] = $derived.by(() => {
    if (!runOut) return [];
    if (runOut.kind === 'parser') {
      return [{ label: programName, kind: 'parser', inPanes: true, trace: runOut.trace }];
    }
    const t = runOut.trace;
    const out: Phase[] = [
      { label: 'baked l2l3l4 parser', kind: 'parser', inPanes: false, trace: t.pp },
    ];
    if (t.map) out.push({ label: 'map', kind: 'map', inPanes: true, trace: t.map });
    return out;
  });
  const active = $derived(phases.length ? activeAt(phases, currentStep) : null);
  const execIr = $derived(active?.phase.inPanes ? active.record.ir_line : null);
  const execAsm = $derived(active?.phase.inPanes ? active.record.asm_line : null);
  const execEdsl = $derived.by(() => {
    if (!active?.phase.inPanes || !compiled) return null;
    const st = compiled.states.find((s) => s.name === active.record.state);
    return st?.edsl ? st.edsl[0] : null;
  });
  const cursorByte = $derived(
    active?.phase.kind === 'parser' ? active.record.cursor : null,
  );
  const gatedNote = $derived.by(() => {
    const out = runOut;
    if (!out || out.kind !== 'map' || !out.result.gated) return null;
    return 'the parser refused this packet — the MAP phase never ran';
  });
</script>

<div class="app">
  <header class="top">
    <a class="brand" href="/nanuk/">Nanuk</a>
    <span class="title">playground</span>
    <select class="program" value={programName}
      onchange={(e) => selectProgram(e.currentTarget.value)}>
      <option value="l2l3l4">l2l3l4 (parser)</option>
      <option value="nanukproto">nanukproto (parser)</option>
      <option value="map_l2fwd">l2 forward (MAP)</option>
      <option value="siit">SIIT translator (MAP)</option>
    </select>
    <span class="status" class:ready={status === 'ready'}>{status}</span>
  </header>
  <main>
    <div class="work">
      <div class="panes">
        <div class="edsl-col">
          <CodePane title="Nanuk lang" paneKey="lang" doc={source} editable python
            ranges={edslRanges} {onEdit} execLine={execEdsl} />
          {#if compileError}
            <div class="banner" role="alert">
              <strong>{compileError.kind}</strong>
              {#if compileError.line}(line {compileError.line}){/if}:
              {compileError.message}
            </div>
          {/if}
        </div>
        <CodePane title="Nanuk IR" paneKey="ir" doc={compiled?.ir_text ?? ''}
          editable={false} python={false} ranges={irRanges} execLine={execIr} />
        <CodePane title="Nanuk asm" paneKey="asm" doc={compiled?.asm_text ?? ''}
          editable={false} python={false} ranges={asmRanges} execLine={execAsm} />
      </div>
      <aside>
        <PacketPanel {runtime} ready={compiled !== null} {programName}
          initialPacket={params.packet} initialPreset={params.preset}
          {onRun} {runOut} {runError} {cursorByte} />
      </aside>
    </div>
    {#if phases.length}
      <DebuggerPanel {phases} step={currentStep}
        onStep={(n) => (currentStep = n)} {gatedNote} />
    {/if}
  </main>
</div>

<style>
  .app { height: 100%; display: flex; flex-direction: column; }
  .top {
    display: flex; align-items: baseline; gap: 0.6rem;
    padding: 0.4rem 0.8rem; border-bottom: 1px solid var(--border);
  }
  .brand { font-weight: 700; color: var(--accent); text-decoration: none; }
  .program {
    font-size: 0.8rem; background: var(--bg-inset); color: var(--fg);
    border: 1px solid var(--border); border-radius: 4px; padding: 0.1rem 0.3rem;
  }
  .status { margin-left: auto; font-size: 0.8rem; color: var(--fg-muted); }
  .status.ready { color: var(--ok); }
  main { flex: 1; display: flex; flex-direction: column; min-height: 0; }
  .work { flex: 1; display: flex; min-height: 0; }
  .panes {
    flex: 1; display: grid; grid-template-columns: 1.2fr 1fr 1fr;
    /* Pin the single row to the container height — an auto row sizes to
       the editors' content and silently grows past the viewport. */
    grid-template-rows: minmax(0, 1fr);
    gap: 1px; background: var(--border); min-width: 0;
  }
  .edsl-col { display: flex; flex-direction: column; min-width: 0; background: var(--bg); }
  /* The pane must shrink to leave room for the error banner, not push it
     below the fold. */
  .edsl-col > :global(.pane) { flex: 1; min-height: 0; }
  .banner {
    padding: 0.5rem 0.8rem; font-size: 0.85rem;
    background: var(--err-bg); color: var(--err); border-top: 1px solid var(--border);
  }
  aside { width: 20rem; border-left: 1px solid var(--border); overflow: auto; }
</style>
