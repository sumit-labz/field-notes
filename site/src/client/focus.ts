// Touch screens have no hover, so on a phone "looking closely" is scrolling:
// whichever print crosses the middle band of the screen gets .is-focus — a
// photocopy gives up its toner, a graded print develops to full colour —
// and loses it again as it scrolls away. With a mouse, :hover does the same.
function init(): void {
  if (matchMedia('(hover: hover) and (pointer: fine)').matches) return;
  const arts = document.querySelectorAll<HTMLElement>('.art--xerox, [class*="grade--"]');
  if (!arts.length) return;
  const io = new IntersectionObserver((entries) => {
    entries.forEach((e) => e.target.classList.toggle('is-focus', e.isIntersecting));
  }, { rootMargin: '-38% 0px -38% 0px' });
  arts.forEach((a) => io.observe(a));
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
else init();
