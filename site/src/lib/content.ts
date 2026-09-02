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

// Ordering signal for the homepage hero: the most recent post in a journey,
// else when the journey itself started. Practice-path journeys are handled by
// the caller (excluded from the hero, shown on Obsession pages + feed).
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

// The journey's newest post (by published date) — the homepage card leads
// with THIS post's title + excerpt rather than the journey's own opening
// note, so the card reflects what's actually new and a long opening note no
// longer dominates it. Null when the journey has no posts yet.
export function journeyLeadPost(journey: Journey, posts: Post[]): Post | null {
  return (
    posts
      .filter((p) => p.data.journey === journey.data.slug)
      .sort((a, b) => b.data.published.valueOf() - a.data.published.valueOf())[0] ?? null
  );
}

// Plain-text first line of a post's body, for the card excerpt — strips
// fragment markers ({{fragment:ID}}) and light markdown emphasis so it reads
// as prose rather than markup. Empty for a fragments-only post with no body.
export function postExcerpt(post: Post): string {
  const withoutMarkers = post.body?.replace(/\{\{fragment:[a-zA-Z0-9-]+\}\}/g, ' ') ?? '';
  const firstBlock =
    withoutMarkers
      .split(/\n\s*\n/)
      .map((p) => p.trim())
      .find(Boolean) ?? '';
  return firstBlock.replace(/[*_`]/g, '');
}

// The lead image for a card: the first media-bearing fragment of the journey's
// newest post, tilted deterministically on render. Falls back to null (card
// renders an empty sheet) rather than failing the homepage on a stale media
// key — post pages still fail loudly via the Fragment component.
export async function journeyLeadImage(
  journey: Journey,
  posts: Post[],
  fragments: Map<string, Fragment>
): Promise<{ src: string; width: number; height: number } | null> {
  const newest = posts
    .filter((p) => p.data.journey === journey.data.slug)
    .sort((a, b) => b.data.published.valueOf() - a.data.published.valueOf())[0];
  if (!newest) return null;

  for (const id of newest.data.fragments) {
    const fragment = fragments.get(id);
    const key = fragment?.data?.media?.find((k) => IMAGE_RE.test(k));
    if (!key) continue;
    const image = await resolveImage(key);
    if (image) return image;
    // media gone from R2/repo — try the next fragment, admit a placeholder
  }
  return null;
}

// The lead image for a single post: the first image-bearing fragment it
// references, in fragment order. Same fallback contract as journeyLeadImage —
// null on a stale/missing key, never a failed build.
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