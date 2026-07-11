<script lang="ts">
  import type { NanukRuntime } from './py';
  let { runtime, ready, initialPacket, initialPreset }: {
    runtime: NanukRuntime | null; ready: boolean;
    initialPacket: string | null; initialPreset: string | null;
  } = $props();
  void runtime; void ready; void initialPacket; void initialPreset;
</script>

<p class="ph">packet panel: Task 7</p>

<style>
  .ph { padding: 0.8rem; color: var(--fg-muted); }
</style>
