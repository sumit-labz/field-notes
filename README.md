# field-notes

An archive of things I'm exploring. Fragments in, journeys out.

I capture small things all week from my phone — a photo of what's on the table,
a line of text, a scan of a notebook page. At the end of the week some of those
fragments become a post. Posts belong to journeys. Journeys belong to identities.

Journeys get abandoned. Those stay visible, with the reason attached. The
abandoned ones are usually the interesting part.

## How it works

```
phone → Telegram bot → GitHub Actions → this repo (text) + Cloudflare R2 (images)
                                              ↓
                                        GitHub Pages
```

Text and structure live in Git as markdown with YAML frontmatter. Images live in
object storage and are referenced by key. The site is static.

Nothing here depends on a platform I can't leave. The whole archive is plain
files.

## Structure

| Directory | Contents |
|---|---|
| `fragments/` | Raw captures, one file per moment, dated folders |
| `posts/` | Published units, assembled from fragments |
| `journeys/` | Explorations, with status and genealogy |
| `identities/` | The five territories |
| `scripts/` | Ingest and verification |
| `site/` | Astro static site |

`SPEC.md` is the build contract.

## Licensing

This repo contains two different kinds of thing, under two different licenses.

**Code** — everything in `scripts/` and `site/` — is MIT. Take it, fork it, build
your own. See `LICENSE`.

**Content** — everything in `fragments/`, `posts/`, `journeys/`, `identities/`,
and all associated media — is CC BY-NC 4.0. You may share and adapt it with
attribution, for non-commercial purposes. See `CONTENT-LICENSE`.

The system is free. The journal is mine.

## Running it

```bash
cd site
npm install
npm run dev
```

The site builds from the markdown files in this repo. Steps 1–4 of `SPEC.md` work
with hand-written sample content and no infrastructure at all.

## Why this exists

AI made polished output cheap. What it didn't make cheap is a record of someone
actually thinking over time — the dead ends, the changes of mind, the thing
abandoned in April that came back in November.

This is that record.
