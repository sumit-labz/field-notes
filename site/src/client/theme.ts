// Light/dark toggle. The initial theme is set inline in <head> (before paint);
// this only handles switching and remembering the reader's choice.
const KEY = 'fn-theme';
// light → black → navy → light. Navy is a tone of dark (data-theme="dark"
// + data-tone="navy"), so every dark-mode rule applies to it as well.
type Theme = 'light' | 'dark' | 'navy';
const ORDER: Theme[] = ['light', 'dark', 'navy'];
const META: Record<Theme, string> = { light: '#E4E8E9', dark: '#141414', navy: '#0F1A2B' };
const NAME: Record<Theme, string> = { light: 'light', dark: 'black', navy: 'dark blue' };

export function currentTheme(): Theme {
  const d = document.documentElement;
  if (d.dataset.theme !== 'dark') return 'light';
  return d.dataset.tone === 'navy' ? 'navy' : 'dark';
}

function applyTheme(t: Theme): void {
  const d = document.documentElement;
  d.dataset.theme = t === 'navy' ? 'dark' : t;
  if (t === 'navy') d.dataset.tone = 'navy';
  else delete d.dataset.tone;
  const meta = document.querySelector<HTMLMetaElement>('meta[name=theme-color]');
  if (meta) meta.content = META[t];
  syncLabels();
}

export function setTheme(t: Theme): void {
  const apply = () => applyTheme(t);
  try { localStorage.setItem(KEY, t); } catch { /* private mode: session-only */ }
  // a soft crossfade between the two papers where supported
  const vt = (document as Document & { startViewTransition?: (cb: () => void) => unknown }).startViewTransition;
  if (vt && !matchMedia('(prefers-reduced-motion: reduce)').matches) vt.call(document, apply);
  else apply();
}

function nextTheme(): Theme {
  return ORDER[(ORDER.indexOf(currentTheme()) + 1) % ORDER.length];
}

function syncLabels(): void {
  const next = NAME[nextTheme()];
  document.querySelectorAll<HTMLElement>('[data-theme-toggle]').forEach((b) => {
    b.setAttribute('aria-label', `Switch to ${next} background`);
    b.title = `Switch to ${next} background`;
  });
}

// With no saved choice, keep following the system setting live (desktop:
// switching the OS to dark at sunset flips the site too; phones stay dark).
function followSystem(): void {
  const sys = matchMedia('(prefers-color-scheme: dark)');
  sys.addEventListener?.('change', () => {
    let saved: string | null = null;
    try { saved = localStorage.getItem(KEY); } catch { /* none */ }
    if (saved || matchMedia('(max-width: 720px)').matches) return;
    applyTheme(sys.matches ? 'dark' : 'light');
  });
}

function init(): void {
  syncLabels();
  followSystem();
  document.addEventListener('click', (e) => {
    const btn = (e.target as Element).closest('[data-theme-toggle]');
    if (!btn) return;
    setTheme(nextTheme());
  });
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
else init();
