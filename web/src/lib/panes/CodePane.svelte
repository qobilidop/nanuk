<script lang="ts">
  import { onDestroy, onMount } from 'svelte';
  import { EditorState } from '@codemirror/state';
  import { EditorView, keymap, lineNumbers } from '@codemirror/view';
  import { defaultKeymap } from '@codemirror/commands';
  import { python } from '@codemirror/lang-python';
  import { hoveredState, setHovered } from '../stores';
  import {
    highlightField, lineRangesToRegions, setHighlightRegion, stateAtLine,
    type NamedRange,
  } from './highlight';

  let {
    title, paneKey, doc, editable, python: isPython, ranges, onEdit,
  }: {
    title: string; paneKey: string; doc: string; editable: boolean;
    python: boolean; ranges: NamedRange[]; onEdit?: (doc: string) => void;
  } = $props();

  let host: HTMLDivElement;
  let view: EditorView | undefined;

  onMount(() => {
    view = new EditorView({
      parent: host,
      state: EditorState.create({
        doc,
        extensions: [
          lineNumbers(),
          keymap.of(defaultKeymap),
          ...(isPython ? [python()] : []),
          ...(editable ? [] : [EditorState.readOnly.of(true)]),
          highlightField,
          EditorView.updateListener.of((u) => {
            if (u.docChanged && onEdit) onEdit(u.state.doc.toString());
          }),
          EditorView.domEventHandlers({
            mousemove(event, v) {
              const pos = v.posAtCoords({ x: event.clientX, y: event.clientY });
              const name =
                pos == null ? null : stateAtLine(ranges, v.state.doc.lineAt(pos).number);
              setHovered(name ? { name, origin: paneKey } : null);
            },
            mouseleave() { setHovered(null); },
          }),
        ],
      }),
    });
    return () => view?.destroy();
  });

  // External doc replacement (IR/asm panes after recompile).
  $effect(() => {
    if (view && !editable && view.state.doc.toString() !== doc) {
      view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: doc } });
    }
  });

  // Apply the shared hover highlight to this pane's matching region, and
  // scroll it into view — but never scroll the pane the mouse is in
  // (that would yank the text out from under the cursor).
  const unsub = hoveredState.subscribe((hover) => {
    if (!view) return;
    const region = hover
      ? lineRangesToRegions(view.state.doc.toString(), ranges)
          .find((r) => r.name === hover.name) ?? null
      : null;
    const effects: import('@codemirror/state').StateEffect<unknown>[] = [
      setHighlightRegion.of(region),
    ];
    if (region && hover && hover.origin !== paneKey) {
      effects.push(EditorView.scrollIntoView(region.from, { y: 'center' }));
    }
    view.dispatch({ effects });
  });
  onDestroy(unsub);
</script>

<section class="pane">
  <header>{title}</header>
  <div class="editor" bind:this={host}></div>
</section>

<style>
  .pane { display: flex; flex-direction: column; min-width: 0; min-height: 0; background: var(--bg); }
  header {
    font: 600 0.75rem/1.8 var(--font-ui); text-transform: uppercase;
    letter-spacing: 0.08em; color: var(--fg-muted);
    border-bottom: 1px solid var(--border); padding: 0 0.5rem;
  }
  .editor { flex: 1; overflow: auto; }
  .editor :global(.cm-editor) { height: 100%; font-size: 0.85rem; }
  .editor :global(.cm-state-hl) { background: var(--hl); }
</style>
