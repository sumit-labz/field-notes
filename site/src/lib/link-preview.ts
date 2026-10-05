// Link preview cards. A post block made only of link lines — `https://...` or
// `Label: https://...` — renders as cards instead of a bare URL. Data comes
// from config/link-previews.json, filled once by scripts/link_previews.py
// (thumbnails live in R2 under l/). Nothing is fetched at build time; a link
// missing from the cache still gets a card, from its label and domain.
import cache from '../../../config/link-previews.json';
import { mediaUrl } from './media-url';

interface Preview {
  title: string | null;
  description: string | null;
  site: string;
  image: string | null;
}

const previews = cache as Record<string, Preview>;
const TRACKING = /^(utm_.*|igsh|igsi|fbclid|gclid|si|ref|ref_src)$/i;
const LINK_LINE = /^(?:(.+?):\s+)?(https?:\/\/\S+)$/;

// Mirrors clean_url() in scripts/link_previews.py — the cache key.
export function cleanUrl(raw: string): string {
  try {
    const u = new URL(raw);
    [...u.searchParams.keys()].forEach((k) => TRACKING.test(k) && u.searchParams.delete(k));
    u.hash = '';
    return u.toString();
  } catch {
    return raw;
  }
}

function escapeHtml(text: string): string {
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// Social titles arrive as `Name on Instagram: "caption\n.\n#tags"` — keep the
// first real line, unquoted, short.
function tidyTitle(title: string): string {
  const first = title.split('\n')[0].replace(/^(.+? on [A-Za-z]+):\s*"?/, '').replace(/["\s]+$/, '').trim();
  return first.length > 90 ? `${first.slice(0, 88).trimEnd()}…` : first;
}

export function isLinkBlock(block: string): boolean {
  return block.split('\n').every((line) => LINK_LINE.test(line.trim()));
}

export function linkCards(block: string): string {
  return block
    .split('\n')
    .map((line) => {
      const [, label, raw] = line.trim().match(LINK_LINE)!;
      const url = cleanUrl(raw);
      const p = previews[url];
      const domain = new URL(url).hostname.replace(/^www\./, '');
      const title = p?.title ? tidyTitle(p.title) : null;
      const img = p?.image
        ? `<img class="link-card__img" src="${mediaUrl(p.image)}" alt="" loading="lazy" />`
        : '';
      return `<a class="link-card${img ? '' : ' link-card--bare'}" href="${escapeHtml(url)}" rel="noopener" target="_blank">${img}<span class="link-card__text">${
        label ? `<span class="link-card__label">${escapeHtml(label)}</span>` : ''
      }${title ? `<span class="link-card__title">${escapeHtml(title)}</span>` : ''}<span class="link-card__site">${escapeHtml(
        p?.site && p.site !== domain ? `${p.site} · ${domain}` : domain
      )} ↗</span></span></a>`;
    })
    .join('\n');
}
