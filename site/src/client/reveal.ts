// Scroll reveal: elements marked [data-reveal] settle into place (fade + small
// rise, see global.css) as they enter the viewport, staggered by their order
// within the batch. Content is only hidden once <html> carries .js, which the
// inline head script sets, so a no-JS reader always sees everything.
const AUTO = '.pull-quote, figure, .ai-quote, .md-fragment, .post-return';
const MAX_STAGGER = 6;

function init(): void {
  document.querySelectorAll<HTMLElement>(AUTO).forEach((el) => el.setAttribute('data-reveal', ''));
  const els = Array.from(document.querySelectorAll<HTMLElement>('[data-reveal]'));

  if (!('IntersectionObserver' in window)) {
    els.forEach((el) => el.classList.add('is-in'));
    return;
  }

  const io = new IntersectionObserver(
    (entries) => {
      let i = 0;
      entries.forEach((e) => {
        if (!e.isIntersecting) return;
        const el = e.target as HTMLElement;
        el.style.setProperty('--i', String(Math.min(i++, MAX_STAGGER)));
        el.classList.add('is-in');
        io.unobserve(el);
      });
    },
    { rootMargin: '0px 0px -8% 0px', threshold: 0 },
  );
  els.forEach((el) => io.observe(el));
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
