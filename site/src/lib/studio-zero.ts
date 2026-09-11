// Studio Zero — the independent creative practice that draws on every
// obsession. Unlike a journey (which sits inside one obsession), it spans all
// of them, so it lives at its own top-level route rather than in the content
// collections. Its "pages" are the works/experiments under the practice;
// posts will accumulate here over time.

export type StudioZeroPage = {
  slug: string;
  title: string;
  body: string;
  inProgress?: boolean;
};

export const studioZero = {
  title: 'Studio Zero',
  tagline: 'building an independent creative practice',
};

export const studioZeroPages: StudioZeroPage[] = [
  {
    slug: 'distribution-first-design',
    title: 'Distribution First Design',
    body: 'Currently working on this design.',
    inProgress: true,
  },
];
