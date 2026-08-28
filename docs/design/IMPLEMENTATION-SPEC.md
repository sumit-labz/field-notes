# field-notes v0 — implementation spec + staged prompts

_Technical spec for the redesign. Commit-safe. Feed this to your coding agent (OpenCode) and run the staged prompts one at a time on a `redesign/v0` branch. Publishable after Stage 1; every later stage ships independently._

## Principles the build must hold
- **No motion.** No CSS transitions, no scroll animation, no hover motion. Static only.
- **No skeuomorphism.** No paper texture, tape, grain, drop shadows, rounded corners.
- **Irregularity only from data/hash or a real scanned mark** — never decorative.
- Deterministic tilt hashed from id (keep existing behavior). Fonts unchanged: Archivo (labels/uppercase), Instrument Sans (body), Space Mono (time/ids), Caveat (margin voice, used sparingly).

## Material tokens (CSS variables — add to global stylesheet)
```css
--ground:#F4F0E6;   /* paper (ground) */
--paper:#FAF8F1;    /* inserted / scan sheet — barely perceptible, one surface max */
--ink:#181714;      /* assertion — what happened */
--graphite:#4E4A42; /* thinking — annotation, practice, marginalia (replaces old metadata grey) */
--ghost:#898277;    /* the past — faded archive (use INSTEAD of opacity) */
--line:#C8C1B3;     /* soft paper boundary */
--highlight:#D8AE3E;/* significance / connection — "marked because it mattered". never UI. */
/* --correction:#963F32  RESERVED — not used in v0 */
```
Borders come in three weights: **hard** (1px `--ink`, rare, structural) · **soft** (1px `--line`, informational) · **none** (whitespace = same thought). Yellow appears only on connections / marked things, never as a button, nav, or hover color.

## Schema (frontmatter — add with defaults, NO content backfill)
- `Journey.path`: `practice | learn | create` — default `create`
- `Post.status`: `in_progress | published` — default `published`
- `Post.kind`: `post | insight` — default `post`
- Identity displays in the UI as **"Obsessions"** (keep schema/route name `identity`). Four: making images, making things move, making sound, making tools.

## Routes
- `/` — terms (one-liner) → journeys (arced only) → feed
- `/posts/[slug]` — can be in_progress
- `/identities` + `/identities/[slug]` — label "Obsessions"; Practice/Learn/Create streams
- `/journeys/[slug]` — opener → posts oldest-first
- `/about` — NEW: dated append-only stack, ink-aging
- `/coordinates` — NEW: posts where kind=insight

## Rules
- Homepage hero = journeys with path in {learn, create}, ordered by last activity. **Practice-path journeys excluded from hero** (they'd top the feed daily). Practice shows on Obsession pages + feed.
- Older/receding content uses `--graphite` then `--ghost`, never opacity.
- `in_progress` posts are shown honestly with a small Space Mono "in progress" mark.

---

## Staged prompts (run one at a time; commit after each)

**Stage 1 — schema + tokens (publishable immediately)**
> Add three optional frontmatter fields with defaults: Journey.path (practice|learn|create, default create), Post.status (in_progress|published, default published), Post.kind (post|insight, default post). Do NOT modify existing content files — rely on defaults. Add the CSS material tokens above to the global stylesheet; replace existing metadata-grey with --graphite and any opacity-based "faded/old" styling with --ghost. Build and confirm the site still renders.

**Stage 2 — homepage**
> On `/`, add the one-liner "An archive of things I'm exploring. Some of it goes nowhere." as the first element in Archivo. Below it, show journeys whose path is learn or create, ordered by most recent activity (exclude practice-path), as journey cards: identity label, title with status mark, one image, one line. Keep the existing feed below. Keep hashed tilt. No animation.

**Stage 3 — Obsessions + streams**
> Rename the "identities" section label to "Obsessions" in the UI only (keep route/schema name `identity`). On `/identities/[slug]`, group that identity's journeys into three streams — Practice, Learn, Create — by Journey.path; within each show status (active plain, paused bracketed, completed tick, abandoned struck-through with its reason). Use placeholder SVG marks (see references/field-notes-m2.html).

**Stage 4 — About**
> Create `/about` as a dated, append-only stack from a list of dated markdown entries, newest first. Newest entry full-weight in --ink; older entries in --graphite then --ghost (not opacity), each with a Space Mono date. Seed with the single entry at the bottom of this file.

**Stage 5 — Coordinates (Insights)**
> Create `/coordinates` listing all posts where kind=insight, newest first, spaced with whitespace (no dividers), each with an × mark, the realization, optional body, and provenance (which journey it came from). An insight is a Post with kind:insight and no required fragments.

**Stage 6 — placeholder marks + in_progress**
> Add placeholder SVG marks for the four statuses (tick, bracket, strike, plain) and three modes (tally, underline, filled), chosen deterministically by id. Where Post.status is in_progress, show a small honest "in progress" mark in Space Mono / --graphite. No hover, no motion. (Marks approximated from references/*.html; real scanned marks replace these later.)

**Stage 7 — review + deploy**
> Remove 20%: soften unnecessary 1px-ink borders to --line or drop them, cut redundant labels and decoration. Build; merge to main so GitHub Pages deploys.

---

## About v0 — seed entry (edit freely later; the stack is append-only)
> **2026 · 08 — current.** A developer who paints. Drifting from myth toward something more Kafkaesque and absurd. Building a tool out of what I learned the year I got fired — which keeps eating the painting. Currently: mostly making tools and images, sound just came back, film's quiet.

## v0.1+ (after you're live — non-blocking)
Real scanned marks + handwriting (swap placeholders) · mode-as-layout-grammar · thought-correction + living margin (authoring conventions) · image states + content-breaks-palette · highlighter lineage edges (when emerged_from data exists) · RSS/syndication · tags · convergence mind-map.
