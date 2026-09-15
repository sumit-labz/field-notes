---
description: Execute a raw free-text instruction against the Field Notes repo autonomously — no confirmation, called from Telegram's /command1.
argument-hint: <free text instruction>
allowed-tools: Bash, Read, Write, Edit, Glob, Grep, Skill
---

# command1 — raw instruction executor

Sumit pasted this instruction via Telegram, with nobody there to answer follow-up
questions: `$ARGUMENTS`

You are running unattended (`claude -p`, no human in the loop until after you're
done). Make the safest reasonable interpretation and **act** — don't stop to ask.
If something is genuinely ambiguous, pick the least destructive reading, do that,
and say so plainly in your final report rather than guessing at the risky one.

## Scope — what you may do without hesitation

- Edit content: `posts/*.md`, `fragments/**/*.md`, `journeys/*.md`,
  `identities/*.md`, post/fragment frontmatter (cover, title, journey,
  obsession, made_with, etc).
- Run existing helper scripts in `scripts/` (transcribe.py, publish_post.py,
  apply_cinematic_grade.py, delete_fragment.py, etc.) via
  `scripts/.venv/Scripts/python.exe`, the same way the other slash commands do.
- Edit `site/src/**` (components, styles, pages) for a requested UI/content fix.
- Attach or swap a photo as a post's cover, using an existing fragment id or one
  found by inspecting `fragments/`.
- `git add` / `git commit` / `git push` (regular push, current branch) — this is
  required, not optional; see below.

## Never do, even if the instruction seems to ask for it

- `git push --force`, `git reset --hard`, `git rebase`, rewriting history.
- Touch `.env`, any secret/token, or anything under `.github/workflows`.
- Edit `scripts/bot.py` or `scripts/ingest.py`, or the Windows Task Scheduler
  job — that's the live process running you; changing it out from under itself
  is how you corrupt the one thing keeping this whole pipeline alive.
- Outright delete a post or fragment file. If asked to remove something, prefer
  unpublishing (`status: in_progress` or similar) or ask via the report instead
  of deleting — deletion is one-way and you're the only one watching.
- Anything that isn't obviously about *this repo's content* (no unrelated
  system changes, no network calls beyond git push, no touching files outside
  the repo).

If the instruction plainly asks for something in this "never" list, don't do the
destructive part — do whatever safe subset you can, and say in the report
exactly what you skipped and why.

## Always finish with a commit

Every run that changes anything ends in exactly one git commit (squash your own
edits into one; don't leave partial state). This commit is the entire audit
trail — nobody is reviewing the change before it lands, so the message must
actually explain what changed and why, the way you would for any other commit
here (see recent `git log` for tone/format). Push it.

If you changed nothing (instruction was a no-op, already true, or you skipped
it as unsafe), don't commit — say so in the report instead.

## Report — last line must be machine-parseable

End your reply with exactly one line, last:

```
DONE: <one-line plain-English summary>
```

Examples: `DONE: swapped the cover on 2026-09-15-so-it-all-started... to fragment 2026-09-15-075039`,
`DONE: no change — post already has that title`,
`DONE: skipped — instruction asked to force-push, did the content edit but left history alone`.

Keep the summary short enough for a Telegram message; put any longer
explanation in the commit message, not here.
