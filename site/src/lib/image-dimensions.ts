import { imageSize } from 'image-size';

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
