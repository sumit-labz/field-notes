---
description: Transcribe the latest voice note, run the clean-verbatim pass (docs/editing/clean-verbatim.md), grade the cover photo, and publish it as a blog post.
argument-hint: [--audio <id>] [--cover <id>] [--journey <slug>] [--obsession <slug>] [--title "My Title"]
allowed-tools: Bash, Read, Write, Skill
---

# Publish last audio → blog post

Turn a recorded voice note into a published Field Notes post, at the `tidied`
(clean verbatim) stage. Arguments (all optional): `$ARGUMENTS`

- `--audio <id>` — the audio fragment to publish. If omitted, use the **newest**
  fragment with `type: audio` whose `consumed_by` is `null`.
- `--cover <id>` — a photo fragment to use as the hero/opener. If omitted,
  default to `2026-09-13-103613`.
- `--journey <slug>` — the journey to file the post under (e.g. `publish`,
  `monologue`). Pass straight through to publish_post's `--journey`.
- `--obsession <slug>` — an optional cross-tag identity (e.g. `writing`,
  `cinema`). Pass straight through to publish_post's `--obsession`.
- `--title "..."` — the post title. If omitted, generate a short, honest title
  from the transcript (the publish script will fall back to the first sentence
  if you pass none).

Use the venv Python at `scripts/.venv/Scripts/python.exe` for every script.
Work from a scratch dir; do not leave temp files in the repo.

## Steps — do these in order, stop and report on any error

1. **Resolve the audio id.** If `--audio` was given, use it. Otherwise find the
   newest audio fragment not yet consumed:
   ```bash
   grep -rl "type: audio" fragments/ | while read f; do grep -q "consumed_by: null" "$f" && grep -h "^id:" "$f"; done | sort | tail -1
   ```
   (Take the last id. If none, tell the user there is no unconsumed voice note and stop.)

2. **Transcribe** the audio to a raw transcript:
   ```bash
   scripts/.venv/Scripts/python.exe scripts/transcribe.py --id <AUDIO_ID> --json
   ```
   Save the `text` field verbatim to a scratch file `raw.txt` — do not alter
   it; it is kept as `transcripts/<id>.txt` (not shown on the site).

3. **Clean-verbatim pass** on `raw.txt` — the house editing standard for voice
   notes, defined in `docs/editing/clean-verbatim.md` (fillers, false starts,
   warm-up and crutch connectives out; the speaker's words, order, doubts and
   tangents stay). Run the script rather than editing by hand, so every path
   applies the identical rules:
   ```bash
   scripts/.venv/Scripts/python.exe scripts/tidy_transcript.py --file raw.txt --out body.txt --json
   ```
   This maps to the `◎ tidied` stage. If it fails, fall back to `raw.txt` as
   the body and publish with `--stage raw` — never lose the capture. Show the
   before/after word counts and the cost from its JSON in your reply.

4. **Grade the cover photo** (fresh from its original, teal_orange):
   ```bash
   scripts/.venv/Scripts/python.exe scripts/apply_cinematic_grade.py <COVER_ID> 0 teal_orange --json
   ```
   If it reports the cover fragment or its media is missing, report it and
   continue without a cover (pass no `--cover-id` in step 5).

5. **Publish** in one commit (writes the transcript, the post, stamps the audio
   fragment consumed, commits and pushes):
   ```bash
   scripts/.venv/Scripts/python.exe scripts/publish_post.py \
     --audio-id <AUDIO_ID> \
     --body-file body.txt \
     --transcript-file raw.txt \
     --cover-id <COVER_ID> \
     --stage tidied \
     [--journey <JOURNEY>] [--obsession <OBSESSION>] \
     [--title "<TITLE if provided or generated>"] \
     --json
   ```
   publish_post.py auto-tags the post from `config/tags.yml` (scripts/suggest_tags.py);
   a tagging failure publishes untagged, never blocks.

6. **Report** the result: the returned `slug`, the `tags` applied, the local path `posts/<slug>.md`,
   and the live path `/posts/<slug>/`. Check `branch` and `will_deploy` in the
   JSON — `will_deploy` is only true when `pushed` is true AND `branch` is
   `main` (the only branch `.github/workflows/build.yml` deploys from). If
   `will_deploy` is false, say so explicitly: the commit landed on branch
   `<branch>`, not `main`, so the site will NOT rebuild until that branch is
   merged into `main`. Never say "the site will rebuild" unless `will_deploy`
   is true. End your reply with a single line exactly:
   `PUBLISHED: <slug>`
   so an automated caller can parse it.
