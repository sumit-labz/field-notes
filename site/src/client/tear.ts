// A journey that changed its name: the old label is torn off each time the
// title comes into view, revealing the new one underneath. It re-pastes
// itself once the title leaves the screen, so scrolling back plays it again
// (phones have no hover). Desktop hover replays it too.
function init(): void {
  const titles = document.querySelectorAll<HTMLElement>('[data-tear]');
  if (!titles.length) return;
  if (matchMedia('(prefers-reduced-motion: reduce)').matches) {
    titles.forEach((el) => el.classList.add('is-torn'));
    return;
  }
  const timers = new WeakMap<HTMLElement, number>();
  const play = (el: HTMLElement) => {
    clearTimeout(timers.get(el));
    el.classList.remove('is-torn', 'is-ripping');
    void el.offsetWidth;
    // hold on the old name, tug at it, then rip
    timers.set(el, window.setTimeout(() => {
      el.classList.add('is-ripping');
      timers.set(el, window.setTimeout(() => el.classList.add('is-torn'), 450));
    }, 1100));
  };
  const reset = (el: HTMLElement) => {
    clearTimeout(timers.get(el));
    el.classList.remove('is-torn', 'is-ripping');
  };
  const io = new IntersectionObserver((entries) => {
    entries.forEach((e) => {
      const el = e.target as HTMLElement;
      if (e.isIntersecting) play(el);
      else reset(el);
    });
  }, { threshold: 0, rootMargin: '-20% 0px -20% 0px' });
  titles.forEach((el) => {
    io.observe(el);
    el.addEventListener('mouseenter', () => { if (el.classList.contains('is-torn')) play(el); });
  });
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
else init();
