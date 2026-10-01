import { hashString } from './hash';

// A small set of self-descriptions for the post-end byline — absurd, a little
// surreal, from inside the studio (the About stack's voice, gone art-absurd) — not a single
// fixed tagline, so returning to the site doesn't mean reading the same line
// under every post. Picked deterministically per post (by slug), so a given
// post always shows the same line across builds — never Math.random().
const LINES = [
  'Paints with a keyboard. Types with a brush.',
  'Currently arguing with a blank page. The page is winning.',
  'Makes things, then asks them what they are.',
  'Collects unfinished things. This is one of them.',
  'Believes a crooked line is still a line.',
  'Runs a studio with no walls and too many tabs.',
  'Once framed a mistake. It looked better than the plan.',
  'Sometimes the drawing draws back.',
];

export function authorLineFor(seed: string): string {
  const h = Math.abs(hashString(seed));
  return LINES[h % LINES.length];
}
