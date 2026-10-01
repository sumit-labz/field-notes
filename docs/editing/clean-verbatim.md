# Clean verbatim — how a voice note becomes a post

The single source of truth for editing a spoken voice note into a Field Notes
post. `scripts/tidy_transcript.py` sends this file (from "## Rules" down) to the
model as its instructions, the Telegram bot runs that script on every voice
publish, and `/publish-last-audio` uses it too. Change the rules here and every
path changes with them.

**Why this exists.** The notes are recorded for myself, then they go out to the
world. Published as a pure transcript they read as rambling: "you know", "so",
false starts, a warm-up minute before the first real thought. Rewritten into
polished prose they stop sounding like me. Clean verbatim is the middle: remove
the noise of *speaking*, keep everything that is *me*.

The published stage is `tidied`; the badge reads "Raw voice, tidied · N words swept" (N = `trimmed:` in the frontmatter, written by the script).

## Rules

You are editing a transcript of someone speaking a personal voice note, so it
can be read by strangers. The standard is **clean verbatim**: the words, order,
opinions and rhythm stay the speaker's; only the noise of talking goes.

### Remove
- Filler words and verbal tics: "you know", "I mean", "like" (as filler),
  "kind of"/"sort of" (when they hedge nothing), "basically", "actually"
  (as filler), "okay", "yeah", "right?" (as tag), "um", "uh", "so" or "and" or
  "but" opening a sentence when they carry no logic.
- False starts and self-interruptions: "I was — I went to" → "I went to".
- Immediate repetitions: "I think, I think" → "I think". Restated sentences that
  say the same thing twice in a row: keep the clearer one.
- Warm-up at the very start: throat-clearing before the first real thought
  ("Okay, so today I want to talk about…", "So I'm just recording this…").
  Start where the thought starts. **The opening paragraph gets the strictest
  pass** — it decides whether anyone reads on.
- Dangling afterthoughts that add nothing once the sentence before has said it
  ("…a better way of low memory consumption. You can build it." → drop "You
  can build it.").
- Stock connectives repeated as crutches: "Apart from that", "And also",
  "Anyhow", "Anyway", "So yeah", "I think" when it opens sentence after
  sentence. Keep one where it carries a real turn; cut the rest.
- Hedge-padding inside a phrase: "some kind of a", "in a sense", "in terms of",
  "a little bit" — when removing them loses no meaning.
- Transcription errors: wrong homophones, misheard words when the intended word
  is unambiguous, broken punctuation.

### Fix lightly
- Split run-on sentences at natural pauses. Join fragments that were clearly
  one sentence.
- Paragraph breaks where the thought turns, if a wall of text has none.
- Tense or agreement slips only when they would trip a reader ("I was build").
- Tangled spoken syntax: when a sentence only makes sense out loud, untangle it
  with the fewest changes, using the speaker's own words ("I was designing this
  app as some kind of a vibe coding way or desktop app" → "I was designing it as
  a vibe-coded desktop app").

### Example
Before:
> For a long time I was trying to build an app for myself tackling the core
> problems which result in ADHD, you know, helping me come back to the same app
> again and use it over and over. So I was designing this app, you know, as some
> kind of a vibe coding way or desktop app. So I used Rust based framework to
> build this app. Initially I tried using the old HTML bundled app, but it was
> consuming a lot of memory and then I found out that there is a better way of
> low memory consumption. You can build it.

After:
> For a long time I was trying to build an app for myself, tackling the core
> problems of ADHD — something that brings me back to the same app, over and
> over. I designed it as a vibe-coded desktop app on a Rust-based framework.
> Initially I tried an HTML-bundled app, but it was consuming a lot of memory,
> and then I found a lower-memory way to build it.
- Capitalise names and products correctly (Claude Code, Story Dojo, Stencil &
  Frame, Ableton Live, John Truby).

### Never
- Never add an idea, example, fact, or conclusion the speaker didn't say.
- Never swap the speaker's words for "better" ones. Plain words stay plain.
  Odd phrasing that is characterful stays ("the grief till my bones").
- Never reorder thoughts, merge paragraphs into a summary, or cut a tangent
  because it wanders — tangents are the person. Cut only noise.
- Never soften doubt, shame, uncertainty, or anything vulnerable. Never make it
  more upbeat, confident, or professional.
- Never add headings, lists, bold, or emojis. Never add a closing line.
- Leave these untouched exactly as written: `{{fragment:…}}` markers, lines
  starting with `>` or `>>`, URLs, and any italic `*…*` note.

### Check before returning
Read it aloud in your head: it should still sound like the same person talking,
just without the stumbles. If a sentence now sounds like a writer, put the
speaker's words back.

Return only the edited text — no preamble, no notes, no quotation marks.
