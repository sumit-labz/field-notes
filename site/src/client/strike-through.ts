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
  if (rects.length === 0) return;

  const containerRect = container.getBoundingClientRect();
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
  });
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

function init(): void {
  drawAll();

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
