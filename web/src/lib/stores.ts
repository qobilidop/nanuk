import { get, writable } from 'svelte/store';

/** A hover event: which parser state, and which pane the mouse is in. */
export interface Hover {
  name: string;
  origin: string;
}

/** Parser state under the cursor in any pane, or null. */
export const hoveredState = writable<Hover | null>(null);

/** Set the hover, deduplicating identical consecutive values (mousemove
 * fires constantly; subscribers scroll panes and must not be spammed). */
export function setHovered(next: Hover | null): void {
  const cur = get(hoveredState);
  if (cur === null && next === null) return;
  if (cur && next && cur.name === next.name && cur.origin === next.origin) return;
  hoveredState.set(next);
}
