// A post either began as a spoken recording that got transcribed (a
// "transcript" file on record), or was typed by hand from the start. This is
// the one place that names the two categories, so the grid, the post page,
// and the footer all agree on the same label + icon.
export type PostOrigin = 'voice' | 'written';

export const ORIGIN_LABEL: Record<PostOrigin, string> = {
  voice: 'Voice Note',
  written: 'Written',
};

export const ORIGIN_TOOLTIP: Record<PostOrigin, string> = {
  voice: 'Voice Note — started as spoken, recorded voice, then transcribed',
  written: 'Written — typed by hand from the start',
};

export function originFor(hasTranscript: boolean): PostOrigin {
  return hasTranscript ? 'voice' : 'written';
}
