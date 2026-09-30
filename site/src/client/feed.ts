// Home feed motion that needs script (the rest is scroll-driven CSS):
//  - the opening line writes itself in, word by word, then the pen strikes
//    "Some of it goes nowhere." (the strike itself is strike-through.ts)
//  - pull-to-shuffle on touch screens: pull down at the top of the feed and
//    every card lifts and drops back onto the desk
const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;

function inkWrite(): void {
  const el = document.querySelector<HTMLElement>('[data-ink-write]');
  if (!el || reduceMotion) return;
  let i = 0;
  const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
  const nodes: Text[] = [];
  while (walker.nextNode()) nodes.push(walker.currentNode as Text);
  nodes.forEach((node) => {
    // the struck phrase fades in whole: splitting it would give the strike
    // one wobble per word instead of one pen stroke per line
    const struck = node.parentElement?.closest<HTMLElement>('[data-strike-text]');
    if (struck) {
      struck.classList.add('ink-phrase');
      struck.style.setProperty('--w', String(i++));
      return;
    }
    const parts = (node.textContent ?? '').split(/(\s+)/);
    if (!parts.some((p) => p.trim())) return;
    const frag = document.createDocumentFragment();
    parts.forEach((part) => {
      if (!part.trim()) { frag.appendChild(document.createTextNode(part)); return; }
      const span = document.createElement('span');
      span.className = 'ink-word';
      span.style.setProperty('--w', String(i++));
      span.textContent = part;
      frag.appendChild(span);
    });
    node.replaceWith(frag);
  });
  el.style.setProperty('--words', String(i));
  el.classList.add('is-writing');
}

function redrop(): void {
  const cards = Array.from(document.querySelectorAll<HTMLElement>('.post-thumb[data-reveal]'));
  let n = 0;
  cards.forEach((card) => {
    const r = card.getBoundingClientRect();
    if (r.bottom < 0 || r.top > window.innerHeight) return; // only what's on screen
    card.classList.remove('is-in');
    card.style.setProperty('--i', String(Math.min(n++, 6)));
    void card.offsetWidth; // restart the landing animation
    card.classList.add('is-in');
  });
}

function pullToShuffle(): void {
  if (reduceMotion || !matchMedia('(pointer: coarse)').matches) return;
  const main = document.querySelector<HTMLElement>('main');
  if (!main) return;
  // our pull replaces the browser's pull-to-refresh on the feed only
  document.documentElement.style.overscrollBehaviorY = 'contain';

  const THRESHOLD = 90;
  let y0 = 0, pulling = false, dy = 0;
  document.addEventListener('touchstart', (e) => {
    pulling = window.scrollY <= 0 && e.touches.length === 1;
    y0 = e.touches[0].clientY;
    dy = 0;
  }, { passive: true });
  document.addEventListener('touchmove', (e) => {
    if (!pulling) return;
    dy = e.touches[0].clientY - y0;
    if (dy <= 0 || window.scrollY > 0) { pulling = false; main.style.translate = ''; return; }
    const pull = Math.min(dy * 0.4, 70);
    main.style.transition = 'none';
    main.style.translate = `0 ${pull}px`;
    main.classList.toggle('pull-armed', dy > THRESHOLD);
  }, { passive: true });
  document.addEventListener('touchend', () => {
    if (!pulling) return;
    pulling = false;
    main.style.transition = 'translate 360ms cubic-bezier(.2,.7,.2,1)';
    main.style.translate = '';
    main.classList.remove('pull-armed');
    if (dy > THRESHOLD) redrop();
  }, { passive: true });
}

function init(): void {
  inkWrite();
  pullToShuffle();
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
else init();
