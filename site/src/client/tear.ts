// A journey that changed its name: the old label is torn off when the title
// scrolls into view, revealing the new one underneath. Hovering re-pastes the
// old label and tears it again.
function init(): void {
  const titles = document.querySelectorAll<HTMLElement>('[data-tear]');
  if (!titles.length) return;
  const tear = (el: HTMLElement) => el.classList.add('is-torn');
  if (matchMedia('(prefers-reduced-motion: reduce)').matches) {
    titles.forEach(tear);
    return;
  }
  const io = new IntersectionObserver((entries) => {
    entries.forEach((e) => {
      if (!e.isIntersecting) return;
      const el = e.target as HTMLElement;
      setTimeout(() => tear(el), 900);
      io.unobserve(el);
    });
  }, { threshold: 0.6 });
  titles.forEach((el) => {
    io.observe(el);
    el.addEventListener('mouseenter', () => {
      if (!el.classList.contains('is-torn')) return;
      el.classList.remove('is-torn');
      void el.offsetWidth; // restart the animation
      setTimeout(() => tear(el), 650);
    });
  });
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
else init();
