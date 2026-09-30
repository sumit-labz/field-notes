// Light/dark toggle. The initial theme is set inline in <head> (before paint);
// this only handles switching and remembering the reader's choice.
const KEY = 'fn-theme';
type Theme = 'light' | 'dark';

export function currentTheme(): Theme {
  return document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light';
}

export function setTheme(t: Theme): void {
  const d = document.documentElement;
  const apply = () => {
    d.dataset.theme = t;
    const meta = document.querySelector<HTMLMetaElement>('meta[name=theme-color]');
    if (meta) meta.content = t === 'dark' ? '#141414' : '#E4E8E9';
    syncLabels();
  };
  try { localStorage.setItem(KEY, t); } catch { /* private mode: session-only */ }
  // a soft crossfade between the two papers where supported
  const vt = (document as Document & { startViewTransition?: (cb: () => void) => unknown }).startViewTransition;
  if (vt && !matchMedia('(prefers-reduced-motion: reduce)').matches) vt.call(document, apply);
  else apply();
}

function syncLabels(): void {
  const next = currentTheme() === 'dark' ? 'light' : 'dark';
  document.querySelectorAll<HTMLElement>('[data-theme-toggle]').forEach((b) => {
    b.setAttribute('aria-label', `Switch to ${next} reading`);
    b.title = `Switch to ${next} reading`;
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
    document.documentElement.dataset.theme = sys.matches ? 'dark' : 'light';
    syncLabels();
  });
}

function init(): void {
  syncLabels();
  followSystem();
  document.addEventListener('click', (e) => {
    const btn = (e.target as Element).closest('[data-theme-toggle]');
    if (!btn) return;
    setTheme(currentTheme() === 'dark' ? 'light' : 'dark');
  });
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
else init();
