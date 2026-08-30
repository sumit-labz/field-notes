import { defineConfig, envField } from 'astro/config';
import { fileURLToPath } from 'node:url';
import { rm } from 'node:fs/promises';
import { openrouterDevPlugin } from './src/dev/openrouter-plugin.mjs';

// The repo root, one level above this Astro project (site/). Committed media
// for video/audio fragments lives at <repo>/media/ (not in site/public/ and
// not on R2) — see scripts/ingest.py. The internal fragments inbox imports
// those files as assets, so Vite's dev server needs filesystem access to the
// parent directory. There is no root package.json/lockfile, so Vite's own
// workspace-root detection stops at site/ and would 403 the parent otherwise.
const repoRoot = fileURLToPath(new URL('..', import.meta.url));

// The /internal/* fragment inbox is a LOCAL tool. By default it is stripped
// from the production build entirely, so it is never deployed to GitHub Pages
// and is genuinely private (a 404 for everyone) — it only exists under
// `npm run dev`. Setting EXPOSE_INTERNAL=true keeps it in the build, so it can
// be deployed on demand (e.g. while travelling). The build.yml workflow exposes
// this as a manual "Run workflow" toggle; a normal push re-privatises it.
// Note: even when exposed it is view-only — the Transcribe/Fix/Delete endpoints
// are dev-server middleware and do not exist in a static deploy.
function internalRoutePrivacy() {
  return {
    name: 'field-notes:internal-privacy',
    hooks: {
      'astro:build:done': async ({ dir, logger }) => {
        if (process.env.EXPOSE_INTERNAL === 'true') {
          logger.warn('EXPOSE_INTERNAL=true — /internal/* IS included in this build (view-only).');
          return;
        }
        await rm(new URL('internal/', dir), { recursive: true, force: true });
        logger.info('/internal/* stripped from the build (local-only). Set EXPOSE_INTERNAL=true to include it.');
      },
    },
  };
}

export default defineConfig({
  integrations: [internalRoutePrivacy()],
  vite: {
    // openrouterDevPlugin runs ONLY under `npm run dev` (apply: 'serve') and
    // powers the internal inbox's Transcribe / Fix-grammar buttons. It reads
    // OPENROUTER_API_KEY from site/.env server-side — the key never reaches the
    // browser and is never in the static build. See src/dev/openrouter-plugin.mjs.
    plugins: [openrouterDevPlugin()],
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
