// Floating reading pill (post pages): listen / share / theme / top.
// It tucks away while you scroll down into the text and comes back when you
// scroll up or reach the end — the page belongs to the words while reading.
function init(): void {
  const pill = document.querySelector<HTMLElement>('[data-read-pill]');
  if (!pill) return;

  // Listen proxies the post's own narration player, so there is one audio
  // element and one source of truth for play state.
  const listen = document.querySelector<HTMLElement>('[data-listen]');
  const listenBtn = pill.querySelector<HTMLElement>('[data-pill-listen]');
  if (listen && listenBtn) {
    listenBtn.addEventListener('click', () => listen.querySelector<HTMLElement>('.listen-play')?.click());
    const sync = () => pill.classList.toggle('is-playing', listen.classList.contains('playing'));
    new MutationObserver(sync).observe(listen, { attributes: true, attributeFilter: ['class'] });
    sync();

    // The pill is the player now: elapsed / total in place of "Listen",
    // a hairline of progress along its top edge, and the speed toggle —
    // all mirrored from the one narration engine in [data-listen].
    const audio = listen.querySelector('audio');
    const label = pill.querySelector<HTMLElement>('[data-pill-listen-label]');
    const bar = pill.querySelector<HTMLElement>('[data-pill-progress] i');
    const rate = pill.querySelector<HTMLElement>('[data-pill-rate]');
    const engineRate = listen.querySelector<HTMLElement>('.listen-rate');
    const fmt = (t: number) => `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, '0')}`;
    if (audio && label) {
      const tick = () => {
        const started = audio.currentTime > 0 || !audio.paused;
        label.textContent = started && isFinite(audio.duration)
          ? `${fmt(audio.currentTime)} / ${fmt(audio.duration)}`
          : isFinite(audio.duration) ? `Listen · ${Math.max(1, Math.round(audio.duration / 60))} min` : 'Listen';
        if (bar) bar.style.transform = `scaleX(${audio.duration ? audio.currentTime / audio.duration : 0})`;
        pill.classList.toggle('has-started', started);
      };
      ['timeupdate', 'loadedmetadata', 'play', 'pause', 'ended'].forEach((ev) => audio.addEventListener(ev, tick));
      tick();
    }
    rate?.addEventListener('click', () => {
      engineRate?.click();
      rate.textContent = engineRate?.textContent ?? rate.textContent;
    });
  }

  const shareBtn = pill.querySelector<HTMLElement>('[data-pill-share]');
  const shareLabel = pill.querySelector<HTMLElement>('[data-pill-share-label]');
  shareBtn?.addEventListener('click', async () => {
    const data = { title: document.title, url: location.href };
    try {
      if (navigator.share) { await navigator.share(data); return; }
      await navigator.clipboard.writeText(location.href);
      if (shareLabel) {
        shareLabel.textContent = 'Copied';
        setTimeout(() => (shareLabel.textContent = 'Share'), 1600);
      }
    } catch { /* dismissed share sheet — nothing to do */ }
  });

  pill.querySelector('[data-pill-top]')?.addEventListener('click', () =>
    window.scrollTo({ top: 0, behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' }),
  );

  let lastY = window.scrollY;
  let ticking = false;
  const update = () => {
    const y = window.scrollY;
    const nearEnd = window.innerHeight + y >= document.documentElement.scrollHeight - 80;
    const goingDown = y > lastY + 4;
    const goingUp = y < lastY - 4;
    // never tuck the player away while it's playing
    if (nearEnd || y < 120 || goingUp || pill.classList.contains('is-playing')) pill.classList.remove('is-hidden');
    else if (goingDown) pill.classList.add('is-hidden');
    lastY = y;
    ticking = false;
  };
  window.addEventListener('scroll', () => {
    if (!ticking) { ticking = true; requestAnimationFrame(update); }
  }, { passive: true });
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
else init();
