import { defineConfig, envField } from 'astro/config';

export default defineConfig({
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
