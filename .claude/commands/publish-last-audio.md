---
description: Transcribe the latest voice note, run the self-editing-pass raw cleanup, grade the cover photo, and publish it as a blog post.
argument-hint: [--audio <id>] [--cover <id>] [--title "My Title"]
allowed-tools: Bash, Read, Write, Skill
---

# Publish last audio → blog post

Turn a recorded voice note into a published Field Notes post, in `raw` (Cleanup)
mode. Arguments (all optional): `$ARGUMENTS`

- `--audio <id>` — the audio fragment to publish. If omitted, use the **newest**
  fragment with `type: audio` whose `consumed_by` is `null`.
- `--cover <id>` — a photo fragment to use as the hero/opener. If omitted,
  default to `2026-09-13-103613`.
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
   Save the `text` field verbatim to a scratch file `raw.txt`. This is the
   "before cleanup" record — do not alter it.

3. **Run the self-editing-pass skill in CLEANUP / `raw` mode** on `raw.txt`.
   Invoke the `self-editing-pass` skill and follow its Cleanup mode exactly:
   fix only unambiguous mistakes (typos, obvious homophones, sentence
   capitals/end punctuation, paragraph breaks where a wall of text has none).
   **Do not reword, restructure, or smooth anything** — the raw voice stays.
   This maps to the `◉ raw` stage. Save the result to a scratch file `body.txt`.
   Keep the fix list visible in your reply so nothing crept in.

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
     --stage raw \
     [--title "<TITLE if provided or generated>"] \
     --json
   ```

6. **Report** the result: the returned `slug`, the local path `posts/<slug>.md`,
   and the live path `/posts/<slug>/`. If `pushed` is true, say the site will
   rebuild. End your reply with a single line exactly:
   `PUBLISHED: <slug>`
   so an automated caller can parse it.
