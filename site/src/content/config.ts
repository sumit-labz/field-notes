import { defineCollection, z } from 'astro:content';
import { glob } from 'astro/loaders';

// Content lives at the repo root (fragments/, posts/, journeys/, identities/),
// not inside site/ — see SPEC.md section 2. The glob loader reads it in place
// so the archive stays a plain directory of markdown, independent of the site.
// `base` is resolved relative to the Astro project root (site/), one level
// up from here, not relative to this config file.
const repoRoot = '../';

const fragments = defineCollection({
  loader: glob({ pattern: '**/*.md', base: `${repoRoot}fragments` }),
  schema: z.object({
    id: z.string(),
    captured_at: z.coerce.date(),
    type: z.enum(['photo', 'text', 'video', 'audio']),
    // omitted for text fragments
    media: z.array(z.string()).optional(),
    journey: z.string().nullable(),
    spark: z.boolean(),
    consumed_by: z.string().nullable(),
    source: z.literal('telegram'),
  }),
});

const posts = defineCollection({
  loader: glob({ pattern: '*.md', base: `${repoRoot}posts` }),
  schema: z.object({
    slug: z.string(),
    title: z.string(),
    published: z.coerce.date(),
    journey: z.string().nullable(),
    // ordered; controls render order
    fragments: z.array(z.string()),
  }),
});

const journeys = defineCollection({
  loader: glob({ pattern: '*.md', base: `${repoRoot}journeys` }),
  schema: z
    .object({
      slug: z.string(),
      title: z.string(),
      identity: z.string(),
      status: z.enum(['active', 'paused', 'abandoned', 'completed']),
      started: z.coerce.date(),
      ended: z.coerce.date().optional(),
      emerged_from: z.string().nullable(),
      reason: z.string().optional(),
    })
    .superRefine((journey, ctx) => {
      // SPEC.md 3.3: reason is REQUIRED when status is abandoned. Build fails without it.
      if (journey.status === 'abandoned' && !journey.reason) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ['reason'],
          message: `journey "${journey.slug}" is abandoned but has no "reason". reason is required for abandoned journeys.`,
        });
      }
      // SPEC.md 3.3: ended is required if abandoned or completed.
      if ((journey.status === 'abandoned' || journey.status === 'completed') && !journey.ended) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ['ended'],
          message: `journey "${journey.slug}" has status "${journey.status}" but no "ended" date. ended is required for abandoned or completed journeys.`,
        });
      }
    }),
});

const identities = defineCollection({
  loader: glob({ pattern: '*.md', base: `${repoRoot}identities` }),
  schema: z.object({
    slug: z.string(),
    title: z.string(),
    order: z.number(),
  }),
});

export const collections = { fragments, posts, journeys, identities };
