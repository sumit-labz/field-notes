// Stage mode — "spotlight monologue". A full-screen dark stage where the
// narration's words step into a spotlight exactly as they're spoken (word
// timings from scripts/generate_word_timings.py). One sentence stands in the
// light at a time; the one before it drifts up into the dark. Tap the stage
// to pause/resume; ✕, Esc, or a swipe down closes it and returns the reader
// to the paragraph the voice had reached.
type Word = [text: string, start: number, end: number, para: number];
interface StageData { title: string; duration: number; paras: string[]; words: Word[] }
interface Line { start: number; end: number; words: Word[] }

const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;
const MAX_WORDS = 14; // a line has to fit a phone screen in the spotlight

function buildLines(words: Word[]): Line[] {
  const lines: Line[] = [];
  let cur: Word[] = [];
  const flush = () => {
    if (cur.length) lines.push({ start: cur[0][1], end: cur[cur.length - 1][2], words: cur });
    cur = [];
  };
  words.forEach((w, i) => {
    cur.push(w);
    const next = words[i + 1];
    const endsSentence = /[.!?…]["”’')\]]*$/.test(w[0]);
    const softBreak = /[,;:—–]["”’')\]]*$/.test(w[0]) && cur.length >= 8;
    if (!next || next[3] !== w[3] || endsSentence || softBreak || cur.length >= MAX_WORDS) flush();
  });
  return lines;
}

const fmt = (t: number) => `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, '0')}`;

function init(): void {
  const dataEl = document.getElementById('stage-data');
  const audio = document.querySelector<HTMLAudioElement>('[data-listen] audio');
  const playBtn = document.querySelector<HTMLElement>('[data-listen] .listen-play');
  if (!dataEl || !audio || !playBtn) return;
  const data = JSON.parse(dataEl.textContent || '{}') as StageData;
  if (!data.words?.length) return;
  const lines = buildLines(data.words);

  let stage: HTMLElement | null = null;
  let raf = 0;
  let shown = -2; // -1 = the title card
  let hideBarTimer = 0;

  // Start directly (the player's own listeners keep its UI in sync via the
  // play/pause events). A first play on a fresh page can be aborted by the
  // browser while it switches from preload=metadata to full buffering —
  // retry once instead of leaving the stage silent.
  const play = () => {
    if (!audio.paused) return;
    audio.play().catch((err: DOMException) => {
      if (err.name === 'AbortError') window.setTimeout(() => audio.play().catch(() => {}), 200);
    });
  };
  const pause = () => { if (!audio.paused) playBtn.click(); };

  function lineAt(t: number): number {
    if (t < lines[0].start - 0.05) return -1;
    let lo = 0, hi = lines.length - 1;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (lines[mid].start - 0.05 <= t) lo = mid; else hi = mid - 1;
    }
    return lo;
  }

  function renderLine(idx: number): void {
    if (!stage) return;
    const holder = stage.querySelector<HTMLElement>('.stage__lines')!;
    // the line on stage steps back into the dark…
    holder.querySelectorAll('.stage__line.is-gone').forEach((el) => el.remove());
    holder.querySelectorAll('.stage__line.is-prev').forEach((el) => el.classList.replace('is-prev', 'is-gone'));
    holder.querySelectorAll('.stage__line.is-now').forEach((el) => el.classList.replace('is-now', 'is-prev'));
    // …and the next one walks into the light
    const el = document.createElement('p');
    el.className = 'stage__line is-now';
    if (idx < 0) {
      el.classList.add('stage__line--title');
      el.textContent = data.title;
    } else {
      lines[idx].words.forEach((w, k) => {
        const span = document.createElement('span');
        span.className = 'stage__word';
        span.textContent = w[0];
        span.dataset.s = String(w[1]);
        el.appendChild(span);
        if (k < lines[idx].words.length - 1) el.appendChild(document.createTextNode(' '));
      });
    }
    holder.appendChild(el);
    shown = idx;
  }

  function tick(): void {
    if (!stage) return;
    const t = audio.currentTime;
    const idx = lineAt(t);
    if (idx !== shown) renderLine(idx);
    stage.querySelectorAll<HTMLElement>('.stage__line.is-now .stage__word:not(.is-lit)').forEach((w) => {
      if (Number(w.dataset.s) <= t + 0.04) w.classList.add('is-lit');
    });
    const dur = audio.duration || data.duration || 1;
    stage.style.setProperty('--progress', String(Math.min(1, t / dur)));
    stage.querySelector('.stage__time')!.textContent = `${fmt(t)} / ${fmt(dur)}`;
    stage.classList.toggle('is-paused', audio.paused);
    raf = requestAnimationFrame(tick);
  }

  function showBar(): void {
    if (!stage) return;
    stage.classList.add('show-bar');
    clearTimeout(hideBarTimer);
    hideBarTimer = window.setTimeout(() => { if (!audio.paused) stage?.classList.remove('show-bar'); }, 2600);
  }

  function build(): HTMLElement {
    const el = document.createElement('div');
    el.className = 'stage';
    el.setAttribute('role', 'dialog');
    el.setAttribute('aria-modal', 'true');
    el.setAttribute('aria-label', `${data.title} — on stage`);
    el.innerHTML = `
      <div class="stage__curtain stage__curtain--l" aria-hidden="true"></div>
      <div class="stage__curtain stage__curtain--r" aria-hidden="true"></div>
      <div class="stage__beam" aria-hidden="true"></div>
      <div class="stage__dust" aria-hidden="true">${'<i></i>'.repeat(reduceMotion ? 0 : 16)}</div>
      <div class="stage__floor" aria-hidden="true"></div>
      <div class="stage__lines" aria-live="off"></div>
      <button type="button" class="stage__close" aria-label="Leave the stage">✕</button>
      <div class="stage__bar">
        <button type="button" class="stage__btn" data-act="back" aria-label="Back 10 seconds">−10</button>
        <button type="button" class="stage__btn stage__btn--play" data-act="toggle" aria-label="Play or pause">
          <svg class="i-play" width="20" height="20" viewBox="0 0 24 24"><path d="M7 4.5v15l12-7.5z" fill="currentColor"/></svg>
          <svg class="i-pause" width="20" height="20" viewBox="0 0 24 24"><path d="M7 4h3.5v16H7zM13.5 4H17v16h-3.5z" fill="currentColor"/></svg>
        </button>
        <button type="button" class="stage__btn" data-act="fwd" aria-label="Forward 10 seconds">+10</button>
        <span class="stage__time">0:00</span>
        <button type="button" class="stage__btn stage__rate" data-act="rate" aria-label="Playback speed"></button>
        <span class="stage__progress" aria-hidden="true"><i></i></span>
      </div>`;
    el.querySelectorAll<HTMLElement>('.stage__dust i').forEach((d, k) => {
      // seeded, not random: the same motes drift the same way every time
      const h = (k * 2654435761) >>> 0;
      d.style.setProperty('--x', `${30 + (h % 40)}%`);
      d.style.setProperty('--y', `${20 + ((h >> 8) % 55)}%`);
      d.style.setProperty('--d', `${9 + ((h >> 16) % 9)}s`);
      d.style.setProperty('--delay', `-${(h >> 4) % 12}s`);
    });
    const rateBtn = el.querySelector<HTMLElement>('.stage__rate')!;
    const engineRate = document.querySelector<HTMLElement>('[data-listen] .listen-rate');
    rateBtn.textContent = engineRate?.textContent ?? '1×';

    el.addEventListener('click', (e) => {
      const target = e.target as HTMLElement;
      const act = target.closest<HTMLElement>('[data-act]')?.dataset.act;
      showBar();
      if (target.closest('.stage__close')) { close(); return; }
      if (act === 'back') audio.currentTime = Math.max(0, audio.currentTime - 10);
      else if (act === 'fwd') audio.currentTime = Math.min(audio.duration || 0, audio.currentTime + 10);
      else if (act === 'rate') { engineRate?.click(); rateBtn.textContent = engineRate?.textContent ?? ''; }
      else if (act === 'toggle' || !target.closest('.stage__bar')) {
        if (audio.paused) play(); else pause();
      }
      if (act === 'back' || act === 'fwd') shown = -2; // force a redraw at the new spot
    });

    let y0 = 0;
    el.addEventListener('touchstart', (e) => { y0 = e.touches[0].clientY; }, { passive: true });
    el.addEventListener('touchend', (e) => { if (e.changedTouches[0].clientY - y0 > 110) close(); }, { passive: true });
    el.addEventListener('mousemove', showBar);
    return el;
  }

  function onKey(e: KeyboardEvent): void {
    if (e.key === 'Escape') close();
    else if (e.key === ' ') { e.preventDefault(); if (audio.paused) play(); else pause(); showBar(); }
    else if (e.key === 'ArrowLeft') { audio.currentTime = Math.max(0, audio.currentTime - 10); shown = -2; showBar(); }
    else if (e.key === 'ArrowRight') { audio.currentTime += 10; shown = -2; showBar(); }
  }

  function onFullscreenChange(): void {
    // the system back gesture leaves fullscreen first — take that as "close"
    if (!document.fullscreenElement && stage?.dataset.fs === '1') close();
  }

  function open(): void {
    if (stage) return;
    stage = build();
    document.body.appendChild(stage);
    document.documentElement.classList.add('stage-open');
    document.addEventListener('keydown', onKey);
    document.addEventListener('fullscreenchange', onFullscreenChange);
    stage.requestFullscreen?.().then(() => { if (stage) stage.dataset.fs = '1'; }).catch(() => { /* iOS: stays a fixed overlay */ });
    shown = -2;
    requestAnimationFrame(() => stage?.classList.add('is-open'));
    stage.querySelector<HTMLElement>('.stage__close')?.focus({ preventScroll: true });
    showBar();
    // start from the top if narration hasn't begun (or already finished)
    if (audio.ended || audio.currentTime === 0) audio.currentTime = 0;
    play();
    raf = requestAnimationFrame(tick);
  }

  function close(): void {
    if (!stage) return;
    cancelAnimationFrame(raf);
    const el = stage;
    stage = null;
    document.removeEventListener('keydown', onKey);
    document.removeEventListener('fullscreenchange', onFullscreenChange);
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    document.documentElement.classList.remove('stage-open');
    el.classList.remove('is-open');
    window.setTimeout(() => el.remove(), reduceMotion ? 0 : 450);

    // back to the paragraph the voice had reached
    const t = audio.currentTime;
    const word = data.words.find((w) => w[2] >= t) ?? data.words[data.words.length - 1];
    const head = (data.paras[word[3]] ?? '').slice(0, 30).trim();
    const para = Array.from(document.querySelectorAll<HTMLElement>('main.reading > p'))
      .find((p) => (p.textContent ?? '').trim().startsWith(head));
    para?.scrollIntoView({ block: 'center', behavior: 'auto' });
    document.querySelector<HTMLElement>('[data-stage-open]')?.focus({ preventScroll: true });
  }

  document.querySelectorAll<HTMLElement>('[data-stage-open]').forEach((b) => b.addEventListener('click', open));
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
else init();
