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
// Two serving mechanisms, because the inbox is a live tool in dev but a static
// export when exposed:
//   - DEV: the openrouter-dev plugin serves /__localmedia/<path> straight from
//     disk per request. New files pulled/ingested while `npm run dev` is running
//     appear immediately — no restart. (import.meta.glob can't do this: it
//     snapshots the folder at server start, so freshly added media 404s.)
//   - BUILD: Vite eagerly globs the media/ tree so the files are emitted into
//     the static output (dist/_astro/…) for an EXPOSE_INTERNAL=true deploy.

const isDev = import.meta.env.DEV;

// Build-time asset map (only consulted in a production build). Kept out of the
// dev path so it doesn't matter that it's a start-time snapshot there.
const modules = import.meta.glob<string>('../../../media/**/*', {
  eager: true,
  query: '?url',
  import: 'default',
});
const byRepoPath = new Map<string, string>();
for (const [globKey, url] of Object.entries(modules)) {
  const idx = globKey.indexOf('media/');
  if (idx === -1) continue;
  byRepoPath.set(globKey.slice(idx), url as string);
}

/** True when a media[] entry is a locally-committed file rather than an R2 key. */
export function isLocalMedia(key: string): boolean {
  return key.startsWith('media/');
}

/**
 * URL for a locally-committed media path ("media/video/x.mp4"). In dev this is
 * the always-fresh middleware URL (the file is checked/streamed at request
 * time). In a build it's the emitted asset URL, or null if the file wasn't
 * present at build time (the inbox surfaces that rather than failing the build).
 */
export function localMediaUrl(key: string): string | null {
  if (isDev) {
    // Root-relative (not base-prefixed); the dev middleware matches this path
    // directly. Encode each segment but keep the slashes.
    return '/__localmedia/' + key.split('/').map(encodeURIComponent).join('/');
  }
  return byRepoPath.get(key) ?? null;
}
