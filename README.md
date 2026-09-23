# Meera / Skinstinct — notes in, LinkedIn post out

A Telegram bot. Meera sends rough notes as she thinks of them, typed or as
voice notes. When she asks, it scores every note out of 5 against three
parameters she set, sends back a short reason for anything it will not draft,
pulls the current industry news from RSS, researches the top note, drafts it in
her voice, checks the draft against her voice specification in a **separate**
model call, and hands the text back for her to post.

It does not post to LinkedIn. It hands her text; she posts it.

It does not invent Skinstinct's numbers. Where a post needs one, the draft
carries `[DATA NEEDED: ...]` and she fills it.

---

## Setup

```bash
pip --version          # nothing to install: standard library only
cp .env.example .env
```

Put two values in `.env`:

- `TELEGRAM_BOT_TOKEN` — from [@BotFather](https://t.me/BotFather): `/newbot`, then copy the token.
- `GEMINI_API_KEY` — a Google AI Studio key.

Then:

```bash
python bot.py
```

It long-polls, so there is no webhook, no public URL and nothing to deploy. It
works while the script is running. Close the terminal and the bot stops; notes
already sent are safe on disk and waiting.

**Lock it to her account.** On first run `ALLOWED_USER_IDS` is empty and anyone
who finds the bot can spend your Gemini key. Send `/whoami`, put the id in
`.env`, restart.

Offline checks, no key and no live call needed:

```bash
python test_offline.py
```

---

## What she types

| | |
|---|---|
| any text | saved as a note. Nothing is spent, nothing is drafted. |
| a voice note | transcribed, transcript shown to her, then saved as a note |
| `/draft` | scores every open note, rejects below the threshold with a reason, researches and drafts the top one |
| `/draft 7` | skips the scoring and drafts note 7 |
| `/notes` | the open notes |
| `/skip 3` | takes note 3 out of the scoring |
| `/revise cut the third paragraph` | a new version, v1 kept |
| `/post` | prints the current draft again, clean, to copy |
| `/approve` | writes the final text to `data/drafts/<id>/APPROVED.md` |

---

## The chain

```
0  TRANSCRIBE    voice note -> text, shown to her before it is saved            no search
1  SCORE         every open note 0-5 against her three parameters               no search
1a THRESHOLD     below 3 -> a reason back, no draft            IN CODE, no model call
1b FEED          Google News + her own RSS, dates from the feed  mechanical, no model call
2  RESEARCH      the current web, starting from the feed                        search on
2a LINK CHECK    every cited URL fetched for real                               no model call
3  DRAFT         her voice, spending the research                               no search
4  VOICE CHECK   17 yes/no questions: her checklist, plus hook and dates        no search
5  REVISE        only what she asked to change, then re-checked                 no search
```

### The three parameters (hers, 23 September 2026)

**A. A new angle** — not a version of something she has already posted **on
LinkedIn**. Newsletters do not count against it: a newsletter goes to
subscribers, a LinkedIn post to industry peers, so the same idea on LinkedIn
reaches a new audience. `corpus/linkedin/` constrains this; `corpus/newsletters/`
does not.

**B. A named emotion** — the note points at a real experience, observation or
incident *and* says what it made her feel. "Annoyed that the label says 10%"
has it; "labels say 10%" does not. It is an input test: the emotion decides
what she noticed, it does not make the post emotive.

**C. A common misconception, corrected** — something consumers widely get wrong
about how a product or ingredient is used, with something specific to teach.

5 = all three, specific. 4 = all three, one thin. 3 = two. 2 = one. 1 = a
subject but none of the three. 0 = nothing, or it repeats a published LinkedIn
post. Anything repeating a LinkedIn post is capped at 2.

Five design decisions worth knowing before changing anything:

**Drafting and checking are two separate calls.** The checker is handed the
draft and the link report, never the prompt that produced the draft. A grader
sharing the writer's instructions shares its blind spots.

**The link check is a measurement, and it outranks the research.** The research
step reports its own sources; this step opens them. A claim whose only source
returns 404 is dropped from the draft, not softened. This exists because in the
sibling project a live run cited two dead links and a `.jpg` as the source of a
quote, and the model-based verifier marked all three VERIFIED.

**Scoring, transcribing and checking cannot search.** A scorer that can search
wanders into research, a transcriber that can search 'corrects' what she said
into something it found on the web, and a grader that can search finds new
reasons to pass.

**The threshold is applied in code, never by the model.** The scorer returns a
number and a reason; `decide()` acts on it. A model asked to both score and
decide drifts its scores to match the decision it already made.

**The voice check is still yes/no.** PASS or FAIL on 17 questions, never a
score. 15 come from her voice specification; 16 and 17 are hers, added 23
September 2026 - the post must OPEN on a current dated industry fact with the
recency visible, and every dated figure must be inside the window or say how
old it is.

**On the 0-5 note score, I disagreed and was overruled.** `CLAUDE.md` in the
sibling project says grading is yes/no and records that a 1-5 rubric was tried
there and rejected: a YES is checkable in five seconds, a 3 is not, and models
cluster everything on 3 and 4 so the threshold does the real work while the
number looks like it is. Her call, 23 September 2026. The mitigations are that
each score carries its three YES/THIN/NO columns and a one-line reason, so a
number never arrives alone, and that the threshold lives in `.env`.

**Nothing is spent until she types `/draft`.** Notes are free.

### Parser contracts

Prompt text and Python regexes agree on three formats. Change one, change both
— they are marked in `pipeline.py`:

- `prompts/01-rank.md` writes `| NOTE 7 | 4 | YES | YES | THIN | why |` → `parse_scores()`
- `prompts/04-voice-check.md` writes `VERDICT: PASS` and `FAILED CHECKS: 3, 7` → `parse_verdict()`
- `prompts/03-draft.md` and `05-revise.md` write `## POST` and `## SOURCES` → `split_post()`

An unreadable verdict is treated as FAIL. Silence from a grader is not a pass.

---

## Files

```
bot.py              Telegram long-poll loop, commands, voice-note handling
feeds.py            Google News + her RSS feeds, mechanical, no model call
import_corpus.py    her published pieces from the seed-data PDF into corpus/
corpus/linkedin/    4 published posts - novelty IS checked against these
corpus/newsletters/ 11 newsletters - novelty is NOT checked against these
pipeline.py         the five steps, and the parsers
gemini.py           the only place that talks to the model
links.py            mechanical link checking, no model call
store.py            notes and drafts as files on disk
prompts/            one file per step
voice/              her voice specification, 15 published pieces distilled
data/               notes.json, state.json, drafts/ — gitignored, never committed
test_offline.py     80 checks, every model call stubbed
```

Every step of every draft is written to disk as it completes, so when a draft
comes out wrong, "which step went wrong" is answerable by opening a file.

---

## What this is not

- **It does not publish.** No LinkedIn API, no OAuth, no posting under her name.
- **LinkedIn only.** Her newsletter format (a "Hi," opening, signed "Meera",
  a subject line) is in the voice file but no step writes one yet.
- **Text and voice notes.** Photos and videos are refused. A voice note is
  transcribed and the transcript is shown to her before it is saved, because
  an ingredient name heard wrong becomes a post built on the wrong ingredient.
- **One person.** `ALLOWED_USER_IDS` is a list, but drafts are keyed per chat
  with no notion of who owns a note.

## Status

Built 23 September 2026, redrawn the same day to her workflow: voice notes,
0-5 scoring against her three parameters, RSS before research, a reason back
for every rejected note. `test_offline.py` passes 80 checks with every model
call stubbed — that proves the wiring, the parsers, the threshold, the audio
payload shape and the link-check rules.

**Not yet run live.** Telegram delivery is confirmed working; no Gemini call
has gone through this. The Google News fetch has been run by hand against three
realistic notes and returned real dated items. Two things only a live run can show: whether Gemini holds her
voice when the specification is handed to it as prompt text rather than run as
a skill, and whether the research step finds anything current worth building a
post on. Expect the first live drafts to need the voice checklist to fail
loudly before they pass.

The recency window is 6 months, set by `WINDOW_MONTHS` in `.env` and
substituted into every prompt. It is not hardcoded anywhere.
