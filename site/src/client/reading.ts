// Post-page reading aids (mobile-first, all progressive):
//  - the paragraph being narrated lights up and the page follows it; once
//    listening has started, tapping a paragraph seeks the audio there
//  - text size stepper (remembered per reader)
//  - "N min left" in the reading pill
//  - swipe left/right to the next/previous post in the journey
//  - the nav tucks away while scrolling down on phones
const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;

function paragraphs(): HTMLElement[] {
  return Array.from(document.querySelectorAll<HTMLElement>('main.reading > p:not([class])'));
}

// --- narration follow -------------------------------------------------------
// Narration has no word timings, so position is estimated from character
// share: paragraph i starts at (chars before i / total chars) of the audio.
// The TTS reads at a near-constant pace, so this lands within a sentence.
function setupFollow(): void {
  const audio = document.querySelector<HTMLAudioElement>('[data-listen] audio');
  const paras = paragraphs();
  if (!audio || paras.length === 0) return;

  const lengths = paras.map((p) => (p.textContent ?? '').trim().length);
  const total = lengths.reduce((a, b) => a + b, 0) || 1;
  const starts: number[] = [];
  lengths.reduce((acc, len) => (starts.push(acc / total), acc + len), 0);

  let current = -1;
  let started = false;
  let userScrollAt = 0;
  let programmatic = false;
  window.addEventListener('scroll', () => { if (!programmatic) userScrollAt = Date.now(); }, { passive: true });

  const setCurrent = (i: number) => {
    if (i === current) return;
    paras[current]?.removeAttribute('data-speaking');
    current = i;
    const el = paras[i];
    if (!el) return;
    el.setAttribute('data-speaking', '');
    // follow along, unless the reader has scrolled away on purpose recently
    if (!audio.paused && Date.now() - userScrollAt > 4000) {
      programmatic = true;
      el.scrollIntoView({ block: 'center', behavior: reduceMotion ? 'auto' : 'smooth' });
      window.setTimeout(() => (programmatic = false), 900);
    }
  };

  audio.addEventListener('play', () => {
    started = true;
    document.body.classList.add('is-listening');
  });
  audio.addEventListener('timeupdate', () => {
    if (!audio.duration) return;
    const f = audio.currentTime / audio.duration;
    let i = 0;
    while (i + 1 < starts.length && starts[i + 1] <= f) i++;
    setCurrent(i);
  });
  audio.addEventListener('ended', () => {
    paras[current]?.removeAttribute('data-speaking');
    current = -1;
    document.body.classList.remove('is-listening');
  });

  paras.forEach((p, i) => {
    p.addEventListener('click', (e) => {
      if (!started || !audio.duration) return;
      if ((e.target as Element).closest('a')) return;
      if (String(window.getSelection() ?? '').length > 0) return; // selecting text, not seeking
      audio.currentTime = starts[i] * audio.duration + 0.05;
      userScrollAt = 0;
      if (audio.paused) document.querySelector<HTMLElement>('[data-listen] .listen-play')?.click();
    });
  });
}

// --- text size ----------------------------------------------------------------
const SIZES = [1, 1.12, 1.25, 0.9];
function setupTextSize(): void {
  const btn = document.querySelector<HTMLElement>('[data-pill-size]');
  if (!btn) return;
  const root = document.documentElement;
  const label = () => btn.setAttribute('aria-label', `Text size ${Math.round(Number(root.style.getPropertyValue('--read-scale') || 1) * 100)}%`);
  label();
  btn.addEventListener('click', () => {
    const cur = Number(root.style.getPropertyValue('--read-scale') || 1);
    const next = SIZES[(SIZES.indexOf(cur) + 1) % SIZES.length] ?? 1;
    root.style.setProperty('--read-scale', String(next));
    try { localStorage.setItem('fn-text', String(next)); } catch { /* session only */ }
    label();
  });
}

// --- time left ------------------------------------------------------------------
function setupTimeLeft(): void {
  const el = document.querySelector<HTMLElement>('[data-pill-left]');
  const main = document.querySelector<HTMLElement>('main.reading');
  if (!el || !main) return;
  const words = Number(el.dataset.words || 0);
  if (words < 150) return; // under a minute: nothing worth counting down
  const update = () => {
    const r = main.getBoundingClientRect();
    const read = Math.min(1, Math.max(0, (window.innerHeight - r.top) / r.height));
    // same rounding as the "N min read" line, so the two never disagree
    const mins = Math.max(1, Math.round((words * (1 - read)) / 220));
    el.textContent = read > 0.97 ? 'done' : `${mins} min left`;
  };
  update();
  window.addEventListener('scroll', () => requestAnimationFrame(update), { passive: true });
}

// --- swipe between posts ------------------------------------------------------------
function setupSwipe(): void {
  const nav = document.querySelector<HTMLElement>('[data-journey-nav]');
  if (!nav || !matchMedia('(pointer: coarse)').matches) return;
  const prev = nav.dataset.prev;
  const next = nav.dataset.next;
  if (!prev && !next) return;

  let x0 = 0, y0 = 0, t0 = 0, ignore = false;
  document.addEventListener('touchstart', (e) => {
    const t = e.touches[0];
    x0 = t.clientX; y0 = t.clientY; t0 = Date.now();
    // leave edge swipes to the browser's own back/forward gesture, and
    // never hijack gestures on media, pre/code, or the audio seek track
    ignore = e.touches.length > 1 || x0 < 24 || x0 > window.innerWidth - 24 ||
      !!(e.target as Element).closest('img, video, pre, .listen, .read-pill, input');
  }, { passive: true });
  document.addEventListener('touchend', (e) => {
    if (ignore) return;
    const t = e.changedTouches[0];
    const dx = t.clientX - x0, dy = t.clientY - y0;
    if (Date.now() - t0 > 600 || Math.abs(dx) < 90 || Math.abs(dy) > Math.abs(dx) * 0.5) return;
    if (String(window.getSelection() ?? '').length > 0) return;
    const target = dx < 0 ? next : prev;
    if (!target) return;
    document.body.classList.add(dx < 0 ? 'swipe-next' : 'swipe-prev');
    location.href = target;
  }, { passive: true });
}

// --- nav tucks away on phones -------------------------------------------------------
function setupNavTuck(): void {
  const nav = document.querySelector<HTMLElement>('nav');
  if (!nav) return;
  const mq = matchMedia('(max-width: 720px)');
  let lastY = window.scrollY;
  window.addEventListener('scroll', () => {
    const y = window.scrollY;
    if (!mq.matches || y < 80) nav.classList.remove('is-tucked');
    else if (y > lastY + 6) nav.classList.add('is-tucked');
    else if (y < lastY - 6) nav.classList.remove('is-tucked');
    lastY = y;
  }, { passive: true });
}

function init(): void {
  setupFollow();
  setupTextSize();
  setupTimeLeft();
  setupSwipe();
  setupNavTuck();
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
else init();
