import { hashString } from './hash';

// A small set of self-descriptions for the post-end byline, in the same
// self-deprecating, plain-spoken voice as the About stack — not a single
// fixed tagline, so returning to the site doesn't mean reading the same line
// under every post. Picked deterministically per post (by slug), so a given
// post always shows the same line across builds — never Math.random().
const LINES = [
  'A guy who allowed himself to wander.',
  'Still not sure if this is a practice or an escape.',
  'A developer who kept getting distracted by the paint.',
  'Half discipline, half hyperfixation.',
  'Building the tool, then painting past it.',
];

export function authorLineFor(seed: string): string {
  const h = Math.abs(hashString(seed));
  return LINES[h % LINES.length];
}
