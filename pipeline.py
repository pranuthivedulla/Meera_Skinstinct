"""The chain: score -> feed -> research -> link check -> draft -> voice check.

Five steps, five files, and the drafting step and the checking step are two
separate model calls. A drafter that grades its own draft passes itself.

Two of the five spend nothing and cannot hallucinate: the link check is a
measurement, and the parsers below are plain text matching.

THE THRESHOLD IS APPLIED IN CODE, NOT BY THE MODEL. The scorer returns a
number and a reason; draft_threshold() decides what happens to it. A model
asked to both score and decide will drift its scores to match the decision it
has already made.

PARSER CONTRACTS - change the prompt and you must change the parser:
  01-rank.md      writes  | NOTE <id> | <0-5> | YES/THIN/NO x3 | <reason> |
                  parse_scores() reads the id, the digit and the reason.
  04-voice-check  writes  VERDICT: PASS  and  FAILED CHECKS: 3, 7
                  parse_verdict() reads both.
  03/05           write   ## POST  and  ## SOURCES
                  split_post() reads those two headings.
"""

import os
import re
import time
from pathlib import Path

import feeds
import links
import store
import style
from gemini import call_model

ROOT = Path(__file__).resolve().parent
PROMPTS = ROOT / "prompts"
VOICE_FILE = ROOT / "voice" / "meera-pillai-voice.md"


def window_months():
    try:
        return int(os.environ.get("WINDOW_MONTHS", "6"))
    except ValueError:
        return 6


def draft_threshold():
    """A note scoring this or higher gets drafted. Her decision, 23 September
    2026: 0-5 with the line at 3. Recorded in README as a disagreement - a
    yes/no count is checkable in five seconds and a 3 is not."""
    try:
        return int(os.environ.get("MIN_SCORE_TO_DRAFT", "3"))
    except ValueError:
        return 3


def voice():
    return VOICE_FILE.read_text(encoding="utf-8")


CORPUS = ROOT / "corpus"


def corpus(kind):
    """Her published pieces. corpus/linkedin/ constrains novelty;
    corpus/newsletters/ does not - a newsletter idea said on LinkedIn reaches
    a different audience. See import_corpus.py."""
    folder = CORPUS / kind
    if not folder.exists():
        return "(none given - parameter A is a guess)", 0
    pieces = sorted(folder.glob("*.md")) + sorted(folder.glob("*.txt"))
    if not pieces:
        return "(none given - parameter A is a guess)", 0
    out = []
    for piece in pieces:
        body = piece.read_text(encoding="utf-8").strip()
        out.append(f"--- {piece.stem} ---\n{body}")
    return "\n\n".join(out), len(pieces)


def _prompt(name, **fields):
    text = (PROMPTS / name).read_text(encoding="utf-8")
    fields.setdefault("TODAY", time.strftime("%d %B %Y"))
    fields.setdefault("WINDOW", str(window_months()))
    low, high = style.word_target()
    fields.setdefault("WORDS_MIN", str(low))
    fields.setdefault("WORDS_MAX", str(high))
    for key, value in fields.items():
        text = text.replace("{" + key + "}", str(value))
    missing = re.findall(r"\{([A-Z_]+)\}", text)
    if missing:
        raise RuntimeError(f"{name} has unfilled placeholders: {sorted(set(missing))}")
    return text


# --- step 0: voice note -> text ---------------------------------------------

def transcribe(audio_bytes, mime_type):
    """A voice note becomes a note like any other. No search: transcription is
    a reading of the audio, and a model that can search can 'correct' what she
    actually said into something it found on the web."""
    text, _ = call_model(_prompt("00-transcribe.md"), search=False,
                         audio=(audio_bytes, mime_type))
    return text.strip()


# --- step 1: score ----------------------------------------------------------

def score(notes):
    """No search. Scoring is a judgement about text already in hand, against
    her three parameters and her published LinkedIn posts. A scorer that can
    search wanders off into research."""
    listing = "\n\n".join(f"NOTE {n['id']} (added {n['added']}):\n{n['text']}"
                          for n in notes)
    linkedin, n_linkedin = corpus("linkedin")
    newsletters, _ = corpus("newsletters")
    text, _ = call_model(_prompt("01-rank.md", NOTES=listing,
                                 LINKEDIN_CORPUS=linkedin,
                                 NEWSLETTER_CORPUS=newsletters), search=False)
    return text, n_linkedin


_SCORE_ROW = re.compile(
    r"^\s*\|\s*NOTE\s*(\d+)\s*\|\s*([0-5])\s*\|"      # id, score
    r"\s*(\w+)\s*\|\s*(\w+)\s*\|\s*(\w+)\s*\|"        # A, B, C
    r"([^|]*)\|",                                     # reason
    re.I | re.M)


def parse_scores(text, valid_ids):
    """[{id, score, a, b, c, reason}], highest first. Unknown ids are dropped -
    a model that invents NOTE 99 must not be able to queue a draft for it. A
    row whose score will not parse is dropped rather than defaulted, because a
    default here silently drafts or silently rejects."""
    rows, seen = [], set()
    for m in _SCORE_ROW.finditer(text):
        note_id = int(m.group(1))
        if note_id in seen or note_id not in valid_ids:
            continue
        seen.add(note_id)
        rows.append({
            "id": note_id,
            "score": int(m.group(2)),
            "a": m.group(3).upper(),
            "b": m.group(4).upper(),
            "c": m.group(5).upper(),
            "reason": m.group(6).strip(),
        })
    rows.sort(key=lambda r: r["score"], reverse=True)
    return rows


def decide(rows):
    """(drafted, rejected). The threshold is applied HERE, in code - the model
    scores, it does not decide."""
    cut = draft_threshold()
    return ([r for r in rows if r["score"] >= cut],
            [r for r in rows if r["score"] < cut])


# --- step 2: the industry feed, then research + link check ------------------

def feed(note_text, progress=lambda msg: None):
    """Mechanical. RSS items carry their own publication dates, so recency is
    a fact here rather than something a model claims."""
    days = window_months() * 30
    items, errors = feeds.gather(note_text, days=days)
    citable = len([i for i in items if i["citable"]])
    progress(f"{len(items)} feed item(s), {citable} citable")
    return feeds.report(items, errors, days)


def research(note_text, feed_report):
    text, cites = call_model(_prompt("02-research.md", NOTE=note_text,
                                     FEEDS=feed_report), search=True)
    if cites:
        text += ("\n\n## Sources the model cited\n\n"
                 + "\n".join(f"- [{t}]({u})" for u, t in cites.items()))
    return text


def link_check(research_text):
    results = links.check_all(links.extract_urls(research_text))
    return links.report(results), results


# --- step 3: draft, and step 4: check it ------------------------------------

def draft(note_text, research_text, link_report):
    text, _ = call_model(_prompt("03-draft.md", VOICE=voice(), NOTE=note_text,
                                 RESEARCH=research_text, LINKCHECK=link_report),
                         search=False)
    return text


def revise(note_text, research_text, link_report, current_draft, instruction):
    text, _ = call_model(_prompt("05-revise.md", VOICE=voice(), NOTE=note_text,
                                 RESEARCH=research_text, LINKCHECK=link_report,
                                 DRAFT=current_draft, INSTRUCTION=instruction),
                         search=False)
    return text


def style_report(draft_text):
    """Mechanical. Counts the draft against her own published posts - no model
    call, no opinion. Register is what a yes/no question about wording cannot
    catch, and it is what both live drafts got wrong."""
    post, _ = split_post(draft_text)
    return style.report(post)


def voice_check(draft_text, link_report, style_text):
    """Separate call, no search, and it is handed the draft only - not the
    prompt that produced it. A grader sharing the writer's instructions shares
    its blind spots."""
    text, _ = call_model(_prompt("04-voice-check.md", VOICE=voice(),
                                 DRAFT=draft_text, LINKCHECK=link_report,
                                 STYLE=style_text),
                         search=False)
    return text


_VERDICT = re.compile(r"^VERDICT:\s*(PASS|FAIL)", re.I | re.M)
_FAILED = re.compile(r"^FAILED CHECKS:\s*(.+)$", re.I | re.M)


def parse_verdict(text):
    """(verdict, failed_checks). An unparseable check is a FAIL - silence from
    a grader is not a pass."""
    m = _VERDICT.search(text)
    verdict = m.group(1).upper() if m else "UNREADABLE"
    f = _FAILED.search(text)
    failed = f.group(1).strip() if f else ""
    if failed.lower() in ("none", "none.", "-"):
        failed = ""
    return ("FAIL" if verdict == "UNREADABLE" else verdict), failed


_CHECK_ROW = re.compile(
    r"^\s*\|\s*(\d{1,2})\s*\|([^|]+)\|\s*(YES|NO)\s*\|([^|]*)\|",
    re.I | re.M)


def failed_checks(text):
    """[(number, question, evidence)] for every row that answered NO.

    Read back out of the checker's own table rather than from a list kept
    here. A hardcoded number -> meaning map would go stale the first time a
    question is added, and then a failure would be explained as the wrong
    thing - worse than a bare number."""
    out = []
    for m in _CHECK_ROW.finditer(text):
        if m.group(3).upper() != "NO":
            continue
        question = " ".join(m.group(2).split()).strip()
        evidence = " ".join(m.group(4).split()).strip()
        out.append((int(m.group(1)), question, evidence))
    return out


def split_post(text):
    """The post body alone, for pasting. Falls back to the whole output rather
    than losing her draft to a missing heading."""
    m = re.search(r"^##\s*POST\s*$(.*?)(?=^##\s|\Z)", text, re.S | re.M | re.I)
    post = m.group(1).strip() if m else text.strip()
    s = re.search(r"^##\s*SOURCES\s*$(.*?)(?=^##\s|\Z)", text, re.S | re.M | re.I)
    return post, (s.group(1).strip() if s else "")


def word_count(post):
    return len(re.findall(r"[\w'-]+", post))


def placeholders(post):
    return re.findall(r"\[DATA NEEDED:[^\]]*\]", post)


# --- the whole run for one note ---------------------------------------------

def run_note(note, progress=lambda msg: None, score_row=None):
    """Feed -> research -> link check -> draft -> voice check. Returns
    (draft_id, version). Every step is written to disk as it completes, so a
    crash loses at most one step and she can still read what came back."""
    draft_id = store.new_draft(note)
    if score_row:
        store.save_step(draft_id, "01-score", (
            f"# Score {score_row['score']}/5\n\n"
            f"| A new angle | An emotion named | A misconception |\n|---|---|---|\n"
            f"| {score_row['a']} | {score_row['b']} | {score_row['c']} |\n\n"
            f"{score_row['reason']}\n"))

    progress("reading the industry feed ...")
    feed_report = feed(note["text"], progress)
    store.save_step(draft_id, "01a-feed", feed_report)

    progress("researching the web ...")
    research_text = research(note["text"], feed_report)
    store.save_step(draft_id, "02-research", research_text)

    progress("fetching every link it cited ...")
    link_report, results = link_check(research_text)
    store.save_step(draft_id, "02a-link-check", link_report)
    dead = len([r for r in results if r["state"] == "DEAD"])
    loads = len(links.usable(results))
    progress(f"{loads} link(s) opened, {dead} dead")

    progress("drafting ...")
    draft_text = draft(note["text"], research_text, link_report)
    store.save_step(draft_id, "03-draft-v1", draft_text)

    progress("measuring it against her published posts ...")
    style_text = style_report(draft_text)
    store.save_step(draft_id, "03a-style-v1", style_text)

    progress("checking it against her voice ...")
    check_text = voice_check(draft_text, link_report, style_text)
    store.save_step(draft_id, "04-voice-check-v1", check_text)

    store.set_status(note["id"], store.DRAFTED)
    return draft_id, 1


def run_revision(draft_id, version, instruction, progress=lambda msg: None):
    note_text = store.read_step(draft_id, "note") or ""
    research_text = store.read_step(draft_id, "02-research") or ""
    link_report = store.read_step(draft_id, "02a-link-check") or ""
    current = store.read_step(draft_id, f"03-draft-v{version}") or ""

    progress("revising ...")
    new_text = revise(note_text, research_text, link_report, current, instruction)
    new_version = version + 1
    store.save_step(draft_id, f"03-draft-v{new_version}", new_text)

    style_text = style_report(new_text)
    store.save_step(draft_id, f"03a-style-v{new_version}", style_text)

    progress("re-checking the voice ...")
    check_text = voice_check(new_text, link_report, style_text)
    store.save_step(draft_id, f"04-voice-check-v{new_version}", check_text)
    return new_version
