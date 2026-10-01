// /how: a reel, not a scroll. Scenes sit side by side and play left to
// right on their own (~7s each) with a story-style progress strip. Tap the
// right/left edge, swipe, or use the arrow keys to move; hold or press pause
// to stop. Each scene's animation replays whenever it comes back on screen.
const DUR = 7000;

function init(): void {
  const reel = document.querySelector<HTMLElement>('[data-reel]');
  const film = reel?.querySelector<HTMLElement>('[data-film]');
  if (!reel || !film) return;
  const scenes = Array.from(film.querySelectorAll<HTMLElement>('[data-scene]'));
  const bar = reel.querySelector<HTMLElement>('[data-reel-bar]')!;
  const count = reel.querySelector<HTMLElement>('[data-reel-count]');
  const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
  bar.style.setProperty('--dur', `${DUR}ms`);
  bar.replaceChildren(...scenes.map(() => { const i = document.createElement('i'); i.append(document.createElement('b')); return i; }));
  const segs = Array.from(bar.children) as HTMLElement[];

  let idx = 0;
  let timer = 0;
  let paused = reduced;
  reel.classList.toggle('is-paused', paused);

  const mark = (n: number) => {
    segs.forEach((s, k) => {
      s.classList.toggle('done', k < n);
      s.classList.remove('now');
    });
    void bar.offsetWidth; // restart the fill
    segs[n]?.classList.add('now');
    if (count) count.textContent = `${String(n + 1).padStart(2, '0')} / ${String(scenes.length).padStart(2, '0')}`;
  };
  const schedule = () => {
    clearTimeout(timer);
    if (paused || idx >= scenes.length - 1) return;
    timer = window.setTimeout(() => go(idx + 1), DUR);
  };
  const show = (n: number) => {
    if (n !== idx) scenes[idx]?.classList.remove('is-on');
    idx = n;
    const s = scenes[n];
    s.classList.remove('is-on');
    void s.offsetWidth; // replay its animation
    s.classList.add('is-on');
    s.scrollTop = 0;
    mark(n);
    schedule();
  };
  const go = (n: number) => {
    n = Math.max(0, Math.min(scenes.length - 1, n));
    film.scrollTo({ left: n * film.clientWidth, behavior: reduced ? 'auto' : 'smooth' });
    show(n);
  };
  const setPaused = (p: boolean) => {
    paused = p;
    reel.classList.toggle('is-paused', p);
    reel.querySelector('[data-reel-play]')?.setAttribute('aria-label', p ? 'Play' : 'Pause');
    schedule();
  };

  // swipes and trackpads: whichever scene settles in view becomes current
  let settle = 0;
  film.addEventListener('scroll', () => {
    clearTimeout(settle);
    settle = window.setTimeout(() => {
      const n = Math.round(film.scrollLeft / film.clientWidth);
      if (n !== idx) show(n);
    }, 140);
  }, { passive: true });

  reel.querySelectorAll('[data-reel-next]').forEach((b) => b.addEventListener('click', () => go(idx + 1)));
  reel.querySelectorAll('[data-reel-prev]').forEach((b) => b.addEventListener('click', () => go(idx - 1)));
  reel.querySelector('[data-reel-play]')?.addEventListener('click', () => setPaused(!paused));
  document.addEventListener('keydown', (e) => {
    if ((e.target as HTMLElement).closest('input, textarea')) return;
    if (e.key === 'ArrowRight') { e.preventDefault(); go(idx + 1); }
    if (e.key === 'ArrowLeft') { e.preventDefault(); go(idx - 1); }
  });
  // hold on the film to pause, like a story
  let held = false;
  const down = () => { if (!paused) { held = true; reel.classList.add('is-paused'); clearTimeout(timer); } };
  const up = () => { if (held) { held = false; reel.classList.remove('is-paused'); schedule(); } };
  film.addEventListener('pointerdown', down);
  ['pointerup', 'pointercancel', 'pointerleave'].forEach((ev) => film.addEventListener(ev, up));
  // don't play to an empty room
  document.addEventListener('visibilitychange', () => (document.hidden ? clearTimeout(timer) : schedule()));

  show(0);
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
else init();
