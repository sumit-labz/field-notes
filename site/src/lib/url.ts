// This site deploys to a GitHub Pages project page (sumit-labz.github.io/
// field-notes), not a domain root, so every internal link needs the
// configured base prefix. Astro does not rewrite hardcoded href strings
// itself — only import.meta.env.BASE_URL reflects astro.config.mjs's
// `base` — so every internal <a href> in this project goes through here
// rather than being written as a plain root-relative string.
export function withBase(path: string): string {
  const base = import.meta.env.BASE_URL.replace(/\/$/, '');
  return path === '/' ? `${base}/` : `${base}${path}`;
}
