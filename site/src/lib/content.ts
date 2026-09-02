// site/src/lib/content.ts
// The redesigned site's data assembly. Everything Astro only knows from raw
// content collections or files, condensed into one place so the pages stay
// small and honest. Nothing here is user-authored prose reflowed by a system.

import type { CollectionEntry } from 'astro:content';
import { mediaUrl } from './media-url';
import { isLocalMedia, localMediaUrl } from './local-media';
import { getImageDimensions, getLocalImageDimensions } from './image-dimensions';

const IMAGE_RE = /\.(webp|jpe?g|png|gif|avif)$/i;

// A media[] entry is EITHER an R2 object key OR a repo-relative local path
// (photos from the web inbox's record/upload/paste tool — lib/local-media.ts).
async function resolveImage(
  key: string
): Promise<{ src: string; width: number; height: number } | null> {
  try {
    if (isLocalMedia(key)) {
      const src = localMediaUrl(key);
      if (!src) return null;
      return { src, ...getLocalImageDimensions(key) };
    }
    const src = mediaUrl(key);
    return { src, ...(await getImageDimensions(src)) };
  } catch {
    return null;
  }
}

type Journey = CollectionEntry<'journeys'>;
type Post = CollectionEntry<'posts'>;
type Fragment = CollectionEntry<'fragments'>;

// Ordering signal for the homepage: the most recent post in a journey, else
// when the journey itself started. Every journey (any path) ranks by this —
// a journey occupies one slot regardless of how often it's posted to, so
// there's no reason to exclude any path from the ranking.
export function journeyLastActivity(journey: Journey, posts: Post[]): Date {
  const postDates = posts
    .filter((p) => p.data.journey === journey.data.slug)
    .map((p) => p.data.published.valueOf());
  if (postDates.length === 0) return journey.data.started;
  return new Date(Math.max(...postDates));
}

// The one-line voice of a card: the real abandonment reason (dry) if
// abandoned, else the opener's first line. Never a generated summary.
export function journeyLeadLine(journey: Journey): string {
  if (journey.data.status === 'abandoned' && journey.data.reason) {
    return journey.data.reason;
  }
  const firstLine = (journey.body ?? '')
    .split(/\n/)
    .map((s) => s.trim())
    .find(Boolean);
  return firstLine ?? '';
}

// A journey's posts, newest-or-oldest first, optionally capped. Homepage
// sections want newest-first + a per-journey cap (discovery: what's fresh);
// the journey detail page wants all of them oldest-first (reading the arc
// from the start) — see SPEC.md's "/journeys/[slug] — posts oldest-first".
export function postsForJourney(
  journey: Journey,
  posts: Post[],
  opts: { limit?: number; order?: 'newest' | 'oldest' } = {}
): Post[] {
  const { limit, order = 'newest' } = opts;
  const sign = order === 'newest' ? -1 : 1;
  const sorted = posts
    .filter((p) => p.data.journey === journey.data.slug)
    .sort((a, b) => sign * (a.data.published.valueOf() - b.data.published.valueOf()));
  return limit ? sorted.slice(0, limit) : sorted;
}

// Plain-text first line of a post's body, for its card — strips fragment
// markers ({{fragment:ID}}) and light markdown emphasis so it reads as prose
// rather than markup. Empty for a fragments-only post with no body.
export function postExcerpt(post: Post): string {
  const withoutMarkers = post.body?.replace(/\{\{fragment:[a-zA-Z0-9-]+\}\}/g, ' ') ?? '';
  const firstBlock =
    withoutMarkers
      .split(/\n\s*\n/)
      .map((p) => p.trim())
      .find(Boolean) ?? '';
  return firstBlock.replace(/[*_`]/g, '');
}

// A short teaser (2-3 lines) from the post's excerpt, for the homepage grid —
// a full first paragraph reads as a wall of text and gives away the post
// rather than pulling the reader in. Cuts at the last whole word before
// maxChars, never mid-word.
export function postTeaser(post: Post, maxChars = 170): string {
  const excerpt = postExcerpt(post);
  if (excerpt.length <= maxChars) return excerpt;
  const cut = excerpt.slice(0, maxChars);
  const lastSpace = cut.lastIndexOf(' ');
  return (lastSpace > 0 ? cut.slice(0, lastSpace) : cut).trimEnd() + '…';
}

// The lead image for a single post: the first image-bearing fragment it
// references, in fragment order. Falls back to null (card renders an empty
// sheet) rather than failing the build on a stale media key.
export async function postLeadImage(
  post: Post,
  fragments: Map<string, Fragment>
): Promise<{ src: string; width: number; height: number } | null> {
  for (const id of post.data.fragments) {
    const fragment = fragments.get(id);
    const key = fragment?.data?.media?.find((k) => IMAGE_RE.test(k));
    if (!key) continue;
    const image = await resolveImage(key);
    if (image) return image;
  }
  return null;
}

export function dateLine(date: Date): string {
  return date.toISOString().slice(0, 10);
}