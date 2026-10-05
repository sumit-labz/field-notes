// Controlled tag vocabulary (config/tags.yml at the repo root). Tags are a
// second lens across journeys; posts may only carry slugs listed there — the
// posts schema enforces it, so a typo fails the build instead of minting a tag.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

export interface Tag {
  slug: string;
  label: string;
  note: string;
}

const file = fileURLToPath(new URL('../../../config/tags.yml', import.meta.url));

// The file is one flow-map per line: `slug: { label: X, note: Y }`.
export const TAGS: Tag[] = readFileSync(file, 'utf-8')
  .split('\n')
  .map((line) => line.match(/^([a-z0-9-]+):\s*\{\s*label:\s*([^,]+?)\s*,\s*note:\s*(.+?)\s*\}\s*$/))
  .filter((m): m is RegExpMatchArray => m !== null)
  .map(([, slug, label, note]) => ({ slug, label, note }));

export const TAG_SLUGS = new Set(TAGS.map((t) => t.slug));

export function tagBySlug(slug: string): Tag | undefined {
  return TAGS.find((t) => t.slug === slug);
}
