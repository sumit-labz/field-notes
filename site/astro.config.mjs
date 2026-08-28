import { defineConfig, envField } from 'astro/config';
import { fileURLToPath } from 'node:url';

// The repo root, one level above this Astro project (site/). Committed media
// for video/audio fragments lives at <repo>/media/ (not in site/public/ and
// not on R2) — see scripts/ingest.py. The internal fragments inbox imports
// those files as assets, so Vite's dev server needs filesystem access to the
// parent directory. There is no root package.json/lockfile, so Vite's own
// workspace-root detection stops at site/ and would 403 the parent otherwise.
const repoRoot = fileURLToPath(new URL('..', import.meta.url));

export default defineConfig({
  vite: {
    server: {
      fs: {
        allow: [repoRoot],
      },
    },
  },
  // Project page at sumit-labz.github.io/field-notes, not a user/org root
  // page — every internal link must go through lib/url.ts's withBase() to
  // pick up this prefix, since Astro doesn't rewrite hardcoded hrefs itself.
  site: 'https://sumit-labz.github.io',
  base: '/field-notes',
  env: {
    schema: {
      // Base URL fragment media is served from (SPEC.md §4 secrets:
      // R2_PUBLIC_BASE). Required at build time — the build fetches every
      // referenced image to read its real dimensions, so a missing or wrong
      // value fails the build loudly rather than shipping a broken <img>.
      R2_PUBLIC_BASE: envField.string({ context: 'server', access: 'public' }),
    },
  },
});
