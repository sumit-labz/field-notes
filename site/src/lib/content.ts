// site/src/lib/content.ts
// The redesigned site's data assembly. Everything Astro only knows from raw
// content collections or files, condensed into one place so the pages stay
// small and honest. Nothing here is user-authored prose reflowed by a system.

import type { CollectionEntry } from 'astro:content';
import { mediaUrl } from './media-url';
import { getImageDimensions } from './image-dimensions';

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
    const key = fragment?.data?.media?.[0];
    if (!key) continue;
    const src = mediaUrl(key);
    try {
      const { width, height } = await getImageDimensions(src);
      return { src, width, height };
    } catch {
      // media gone from R2 — try the next fragment, admit a placeholder
      continue;
    }
  }
  return null;
}

export function dateLine(date: Date): string {
  return date.toISOString().slice(0, 10);
}