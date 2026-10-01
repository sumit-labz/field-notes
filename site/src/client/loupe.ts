// The loupe: pick a print up off the table and look at the whole thing.
// Feed photos are cropped, photocopied and taped down — material, not
// documents — so every one carries a small magnifier that lifts the original
// (uncropped, in colour) into a dialog, with its caption and a way into the
// post. One shared <dialog>, built on first use. Esc, the close button or a
// click outside puts it back down.
let dialog: HTMLDialogElement | null = null;
let returnFocus: HTMLElement | null = null;

function build(): HTMLDialogElement {
  const d = document.createElement('dialog');
  d.className = 'loupe';
  d.setAttribute('aria-label', 'Photograph');
  d.innerHTML = `
    <figure class="loupe__sheet">
      <img class="loupe__img" alt="" />
      <figcaption class="loupe__cap">
        <span class="loupe__title"></span>
        <a class="loupe__go" href="#">read the post →</a>
      </figcaption>
    </figure>
    <button type="button" class="loupe__close" aria-label="Put it back">×</button>`;
  d.addEventListener('click', (e) => {
    // a click on the backdrop (the dialog box itself, outside the sheet)
    if (e.target === d || (e.target as Element).closest('.loupe__close')) close();
  });
  d.addEventListener('close', () => {
    document.documentElement.classList.remove('loupe-open');
    returnFocus?.focus({ preventScroll: true });
  });
  document.body.appendChild(d);
  return d;
}

function close(): void {
  if (!dialog?.open) return;
  const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (reduce) { dialog.close(); return; }
  dialog.classList.add('is-closing');
  setTimeout(() => { dialog?.classList.remove('is-closing'); dialog?.close(); }, 220);
}

function open(btn: HTMLElement): void {
  dialog ??= build();
  const img = dialog.querySelector<HTMLImageElement>('.loupe__img')!;
  img.src = btn.dataset.loupe ?? '';
  if (btn.dataset.loupeW && btn.dataset.loupeH) {
    img.width = Number(btn.dataset.loupeW);
    img.height = Number(btn.dataset.loupeH);
  }
  dialog.querySelector('.loupe__title')!.textContent = btn.dataset.loupeTitle ?? '';
  const go = dialog.querySelector<HTMLAnchorElement>('.loupe__go')!;
  go.href = btn.dataset.loupeHref ?? '#';
  go.hidden = !btn.dataset.loupeHref;
  returnFocus = btn;
  document.documentElement.classList.add('loupe-open');
  dialog.showModal();
}

document.addEventListener('click', (e) => {
  const btn = (e.target as Element).closest<HTMLElement>('[data-loupe]');
  if (!btn) return;
  e.preventDefault();
  e.stopPropagation();
  open(btn);
});
