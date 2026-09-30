// Phones: the nav stays pinned, then tucks away while you scroll down and
// returns when you scroll up. Publishes its height as --nav-h so pinned
// section headings can ride just below it.
function init(): void {
  const nav = document.querySelector<HTMLElement>('nav');
  if (!nav) return;
  const mq = matchMedia('(max-width: 720px)');
  const root = document.documentElement;
  const measure = () => root.style.setProperty('--nav-h', `${nav.offsetHeight}px`);
  measure();
  window.addEventListener('resize', measure);

  let lastY = window.scrollY;
  window.addEventListener('scroll', () => {
    const y = window.scrollY;
    if (!mq.matches || y < 80) nav.classList.remove('is-tucked');
    else if (y > lastY + 6) nav.classList.add('is-tucked');
    else if (y < lastY - 6) nav.classList.remove('is-tucked');
    lastY = y;
  }, { passive: true });
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
else init();
