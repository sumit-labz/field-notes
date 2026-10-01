// /how: each scene rolls when it comes on screen (.is-on, once), and carries
// --p, its progress through the viewport (0 entering → 1 leaving), for the
// parallax numbers and the clarity word that sharpens as you scroll.
function init(): void {
  const scenes = Array.from(document.querySelectorAll<HTMLElement>('[data-scene]'));
  if (!scenes.length) return;

  const io = new IntersectionObserver((entries) => {
    entries.forEach((e) => {
      if (!e.isIntersecting) return;
      e.target.classList.add('is-on');
      io.unobserve(e.target);
    });
  }, { threshold: 0.35 });
  scenes.forEach((s) => io.observe(s));

  if (matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  let ticking = false;
  const update = () => {
    const vh = window.innerHeight;
    scenes.forEach((s) => {
      const r = s.getBoundingClientRect();
      if (r.bottom < -vh || r.top > vh * 2) return;
      const p = Math.min(1, Math.max(0, (vh - r.top) / (vh + r.height)));
      s.style.setProperty('--p', p.toFixed(3));
    });
    ticking = false;
  };
  window.addEventListener('scroll', () => {
    if (!ticking) { ticking = true; requestAnimationFrame(update); }
  }, { passive: true });
  update();
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
else init();
