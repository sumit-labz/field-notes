import { imageSize } from 'image-size';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

// Fragment frontmatter (SPEC.md §3.1) stores only R2 object keys, not
// dimensions — ingest.py doesn't write them and isn't being touched here.
// width/height attributes still need real numbers to prevent layout shift,
// so the build fetches each referenced image once and reads them directly.
// This also doubles as the first real check that R2_PUBLIC_BASE is correct
// and the object actually exists: a bad URL fails the build, not the page.
const cache = new Map<string, { width: number; height: number }>();

export async function getImageDimensions(url: string): Promise<{ width: number; height: number }> {
  const cached = cache.get(url);
  if (cached) return cached;

  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(
      `Could not fetch ${url} to read its dimensions (${response.status} ${response.statusText}). ` +
        `Check R2_PUBLIC_BASE and that the object exists in the bucket.`
    );
  }

  const buffer = new Uint8Array(await response.arrayBuffer());
  const { width, height } = imageSize(buffer);
  if (!width || !height) {
    throw new Error(`Could not read image dimensions from ${url}`);
  }

  const result = { width, height };
  cache.set(url, result);
  return result;
}

// site/src/lib -> repo root, matching lib/local-media.ts's glob base.
const repoRoot = fileURLToPath(new URL('../../../', import.meta.url));

// Local media (committed to the repo, not R2 — see lib/local-media.ts) is read
// straight off disk instead of over HTTP: during SSG there's no server to
// fetch a build-emitted asset URL from, and reading the file is simpler and
// faster anyway. `key` is the repo-relative path from fragment frontmatter
// (e.g. "media/photo/2026-08-31-193317.webp").
const localCache = new Map<string, { width: number; height: number }>();

export function getLocalImageDimensions(key: string): { width: number; height: number } {
  const cached = localCache.get(key);
  if (cached) return cached;

  const buffer = readFileSync(repoRoot + key);
  const { width, height } = imageSize(buffer);
  if (!width || !height) {
    throw new Error(`Could not read image dimensions from ${key}`);
  }

  const result = { width, height };
  localCache.set(key, result);
  return result;
}
