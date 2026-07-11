import { writable } from 'svelte/store';

/** Name of the parser state under the cursor in any pane, or null. */
export const hoveredState = writable<string | null>(null);
