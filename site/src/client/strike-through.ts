import { wobblePath } from '../lib/strike';

// Draws the abandoned-journey strike-through per rendered line, using
// Range.getClientRects() to find where text actually wrapped. This can only
// be known after layout — a build-time SVG has no idea how a title will
// wrap on a given viewport — so titles render as plain, fully-wrapping,
// unstruck text by default, and this enhances them once it can measure.
// If measurement isn't possible for any reason, the title simply stays
// unstruck: no scrolling, no truncation, the title is never sacrificed for
// the mark.
const SELECTOR = '[data-strike-seed]';
const SVG_NS = 'http://www.w3.org/2000/svg';

function draw(container: HTMLElement): void {
  const textEl = container.querySelector<HTMLElement>('[data-strike-text]');
  const svg = container.querySelector<SVGSVGElement>('svg.strike-overlay');
  if (!textEl || !svg) return;

  const range = document.createRange();
  range.selectNodeContents(textEl);
  const rects = Array.from(range.getClientRects());
  // Can't measure yet: leave the CSS line-through baseline in place so the
  // title still reads as struck rather than plain.
  if (rects.length === 0) {
    container.classList.remove('is-wobbled');
    return;
  }

  // The overlay is positioned against its containing block. A struck title
  // is its own (relative, inline-block) box; a phrase struck inside running
  // text (the home tagline) is a static inline span that may wrap, so the
  // overlay spans the parent block instead — measuring against the inline
  // span would anchor to its first line fragment and shift every stroke.
  const frame = getComputedStyle(container).position === 'static' && container.parentElement
    ? container.parentElement
    : container;
  const containerRect = frame.getBoundingClientRect();
  const seed = container.dataset.strikeSeed ?? '';

  while (svg.firstChild) svg.removeChild(svg.firstChild);
  svg.setAttribute('width', String(containerRect.width));
  svg.setAttribute('height', String(containerRect.height));
  svg.setAttribute('viewBox', `0 0 ${containerRect.width} ${containerRect.height}`);

  rects.forEach((rect, lineIndex) => {
    const offsetX = rect.left - containerRect.left;
    const offsetY = rect.top - containerRect.top;
    const d = wobblePath(`${seed}-${lineIndex}`, rect.width, rect.height);
    const path = document.createElementNS(SVG_NS, 'path');
    path.setAttribute('d', d);
    path.setAttribute('transform', `translate(${offsetX.toFixed(1)}, ${offsetY.toFixed(1)})`);
    svg.appendChild(path);
    // dash length + line index drive the self-drawing stroke in global.css
    path.style.setProperty('--len', String(Math.ceil(path.getTotalLength())));
    path.style.setProperty('--line', String(lineIndex));
  });

  // wobble drawn — drop the plain CSS line so the two marks don't stack
  container.classList.add('is-wobbled');
}

function drawAll(): void {
  document.querySelectorAll<HTMLElement>(SELECTOR).forEach((el) => {
    try {
      draw(el);
    } catch {
      // leave this title unstruck rather than half-drawn
    }
  });
}

// Once a struck title scrolls into view, let the pen cross it out.
const drawObserver =
  'IntersectionObserver' in window
    ? new IntersectionObserver(
        (entries) => {
          entries.forEach((e) => {
            if (!e.isIntersecting) return;
            (e.target as HTMLElement).classList.add('is-drawn');
            drawObserver?.unobserve(e.target);
          });
        },
        { threshold: 0.6 },
      )
    : null;

function init(): void {
  drawAll();
  document.querySelectorAll<HTMLElement>(SELECTOR).forEach((el) => {
    if (drawObserver) drawObserver.observe(el);
    else el.classList.add('is-drawn');
  });

  if (document.fonts?.ready) {
    // web fonts can swap in after first paint and reflow the text, moving
    // line breaks — redraw once layout has settled on the real fonts
    document.fonts.ready.then(drawAll).catch(() => {});
  }

  let resizeTimer: number | undefined;
  window.addEventListener('resize', () => {
    window.clearTimeout(resizeTimer);
    resizeTimer = window.setTimeout(drawAll, 150);
  });
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
