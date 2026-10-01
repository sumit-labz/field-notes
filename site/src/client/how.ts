// /how: a vertical film that scrolls one scene at a time (the page snaps, see
// how.astro). A scene plays its animation when it fills the screen and resets
// when it leaves, so coming back plays it again. The dots on the right show
// where you are and jump to any scene.
function init(): void {
  const film = document.querySelector<HTMLElement>('[data-film]');
  if (!film) return;
  const scenes = Array.from(film.querySelectorAll<HTMLElement>('[data-scene]'));
  const dotsNav = document.querySelector<HTMLElement>('[data-film-dots]');
  const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;

  const dots = scenes.map((s, i) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.setAttribute('aria-label', `Scene ${i + 1}`);
    b.addEventListener('click', () => s.scrollIntoView({ behavior: reduced ? 'auto' : 'smooth' }));
    dotsNav?.append(b);
    return b;
  });

  const io = new IntersectionObserver((entries) => {
    entries.forEach((e) => {
      const s = e.target as HTMLElement;
      const i = scenes.indexOf(s);
      if (e.isIntersecting) {
        s.classList.add('is-on');
        dots.forEach((d, k) => d.classList.toggle('is-here', k === i));
      } else if (!reduced) {
        s.classList.remove('is-on');
      }
    });
  }, { threshold: 0.4 });
  scenes.forEach((s) => io.observe(s));

  // one wheel nudge = one scene (a phone flick already does this natively)
  let cooldown = 0;
  const step = (dir: number) => {
    const cur = Math.max(0, dots.findIndex((d) => d.classList.contains('is-here')));
    const next = cur + dir;
    if (next < 0) return false;
    if (next >= scenes.length) return false; // past the end: let the page reach the footer
    scenes[next].scrollIntoView({ behavior: reduced ? 'auto' : 'smooth' });
    return true;
  };
  window.addEventListener('wheel', (e) => {
    if (Math.abs(e.deltaY) < 4 || e.ctrlKey) return;
    const now = performance.now();
    if (now < cooldown) { e.preventDefault(); return; }
    if (step(e.deltaY > 0 ? 1 : -1)) { e.preventDefault(); cooldown = now + 750; }
  }, { passive: false });

  // arrow keys / page keys move a whole scene
  document.addEventListener('keydown', (e) => {
    if (!['ArrowDown', 'ArrowUp', 'PageDown', 'PageUp'].includes(e.key)) return;
    const cur = dots.findIndex((d) => d.classList.contains('is-here'));
    const next = cur + (e.key === 'ArrowDown' || e.key === 'PageDown' ? 1 : -1);
    if (next < 0 || next >= scenes.length) return;
    e.preventDefault();
    scenes[next].scrollIntoView({ behavior: reduced ? 'auto' : 'smooth' });
  });
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
else init();
