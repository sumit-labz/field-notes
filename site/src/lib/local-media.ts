// Resolver for locally-committed fragment media (video/audio).
//
// Photo fragments store an R2 object key in `media[]` (e.g. "f/2026-08-18/…webp")
// and are served from R2 via lib/media-url.ts. Video and audio fragments are
// NOT uploaded to R2 — the Telegram bot saves them as-is under <repo>/media/
// and stores a repo-relative path in `media[]` (e.g. "media/video/2026-…mp4").
//
// The discriminator between the two is the "media/" prefix: an R2 key never
// starts with it. This keeps existing photo fragments untouched (no new
// frontmatter field) while letting the renderer pick the right source.
//
// Vite eagerly globs the whole media/ tree as URL imports so the files are
// emitted into the build (dist/_astro/…) and served in dev. The glob is
// relative to this file: lib → src → site → repo, then into media/.

const modules = import.meta.glob<string>('../../../media/**/*', {
  eager: true,
  query: '?url',
  import: 'default',
});

// Map each frontmatter-style path ("media/video/x.mp4") to its emitted URL.
const byRepoPath = new Map<string, string>();
for (const [globKey, url] of Object.entries(modules)) {
  // globKey looks like "../../../media/video/x.mp4"; strip the leading ../ hops
  // down to the "media/…" tail that matches what frontmatter stores.
  const idx = globKey.indexOf('media/');
  if (idx === -1) continue;
  byRepoPath.set(globKey.slice(idx), url as string);
}

/** True when a media[] entry is a locally-committed file rather than an R2 key. */
export function isLocalMedia(key: string): boolean {
  return key.startsWith('media/');
}

/**
 * URL for a locally-committed media path ("media/video/x.mp4"), or null if the
 * file isn't present on disk (a fragment referencing missing media). The
 * internal inbox surfaces the missing case rather than failing the build.
 */
export function localMediaUrl(key: string): string | null {
  return byRepoPath.get(key) ?? null;
}
