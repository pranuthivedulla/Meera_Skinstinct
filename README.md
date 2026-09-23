# Meera / Skinstinct — notes in, LinkedIn post out

A Telegram bot. Meera sends rough notes as she thinks of them. When she asks,
it ranks the notes, says which ones can become a post and why the rest cannot,
researches the best one against the current public web, drafts it in her voice,
checks the draft against her voice specification in a **separate** model call,
and hands the text back for her to post.

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
| `/draft` | ranks every open note, then researches and drafts the top one |
| `/draft 7` | skips the ranking and drafts note 7 |
| `/notes` | the open notes |
| `/skip 3` | takes note 3 out of the ranking |
| `/revise cut the third paragraph` | a new version, v1 kept |
| `/post` | prints the current draft again, clean, to copy |
| `/approve` | writes the final text to `data/drafts/<id>/APPROVED.md` |

---

## The chain

```
1  RANK          every open note, POSTABLE or NOT POSTABLE, with the reason     no search
2  RESEARCH      the current web: regulation, data, consumer behaviour          search on
2a LINK CHECK    every cited URL fetched for real                               no model call
3  DRAFT         her voice, spending the research                               no search
4  VOICE CHECK   15 yes/no questions against her own checklist                  no search
5  REVISE        only what she asked to change, then re-checked                 no search
```

Five design decisions worth knowing before changing anything:

**Drafting and checking are two separate calls.** The checker is handed the
draft and the link report, never the prompt that produced the draft. A grader
sharing the writer's instructions shares its blind spots.

**The link check is a measurement, and it outranks the research.** The research
step reports its own sources; this step opens them. A claim whose only source
returns 404 is dropped from the draft, not softened. This exists because in the
sibling project a live run cited two dead links and a `.jpg` as the source of a
quote, and the model-based verifier marked all three VERIFIED.

**Ranking and checking cannot search.** A ranker that can search wanders into
research; a grader that can search finds new reasons to pass.

**Verdicts are yes/no.** PASS or FAIL, POSTABLE or NOT POSTABLE. Never a score.
A yes/no is checkable in five seconds; a 7/10 is not.

**Nothing is spent until she types `/draft`.** Notes are free.

### Parser contracts

Prompt text and Python regexes agree on three formats. Change one, change both
— they are marked in `pipeline.py`:

- `prompts/01-rank.md` writes `| 1 | NOTE 7 | POSTABLE | why |` → `parse_ranking()`
- `prompts/04-voice-check.md` writes `VERDICT: PASS` and `FAILED CHECKS: 3, 7` → `parse_verdict()`
- `prompts/03-draft.md` and `05-revise.md` write `## POST` and `## SOURCES` → `split_post()`

An unreadable verdict is treated as FAIL. Silence from a grader is not a pass.

---

## Files

```
bot.py              Telegram long-poll loop and commands
pipeline.py         the five steps, and the parsers
gemini.py           the only place that talks to the model
links.py            mechanical link checking, no model call
store.py            notes and drafts as files on disk
prompts/            one file per step
voice/              her voice specification, 15 published pieces distilled
data/               notes.json, state.json, drafts/ — gitignored, never committed
test_offline.py     53 checks, every model call stubbed
```

Every step of every draft is written to disk as it completes, so when a draft
comes out wrong, "which step went wrong" is answerable by opening a file.

---

## What this is not

- **It does not publish.** No LinkedIn API, no OAuth, no posting under her name.
- **LinkedIn only.** Her newsletter format (a "Hi," opening, signed "Meera",
  a subject line) is in the voice file but no step writes one yet.
- **Text only.** Photos and voice notes are refused. A URL inside a note is
  passed to the researcher as a hint.
- **One person.** `ALLOWED_USER_IDS` is a list, but drafts are keyed per chat
  with no notion of who owns a note.

## Status

Built 23 September 2026. `test_offline.py` passes 53 checks with every model
call stubbed — that proves the wiring, the parsers and the link-check rules.

**Not yet run live.** No real Telegram message and no real Gemini call has gone
through this. Two things only a live run can show: whether Gemini holds her
voice when the specification is handed to it as prompt text rather than run as
a skill, and whether the research step finds anything current worth building a
post on. Expect the first live drafts to need the voice checklist to fail
loudly before they pass.

The recency window is 6 months, set by `WINDOW_MONTHS` in `.env` and
substituted into every prompt. It is not hardcoded anywhere.
