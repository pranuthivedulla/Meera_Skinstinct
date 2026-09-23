"""The chain: rank -> research -> link check -> draft -> voice check.

Five steps, five files, and the drafting step and the checking step are two
separate model calls. A drafter that grades its own draft passes itself.

Two of the five spend nothing and cannot hallucinate: the link check is a
measurement, and the parsers below are plain text matching.

PARSER CONTRACTS - change the prompt and you must change the parser:
  01-rank.md      writes  | <rank> | NOTE <id> | POSTABLE | <why> |
                  parse_ranking() reads NOTE <id> and the exact strings
                  POSTABLE / NOT POSTABLE.
  04-voice-check  writes  VERDICT: PASS  and  FAILED CHECKS: 3, 7
                  parse_verdict() reads both.
  03/05           write   ## POST  and  ## SOURCES
                  split_post() reads those two headings.
"""

import os
import re
import time
from pathlib import Path

import links
import store
from gemini import call_model

ROOT = Path(__file__).resolve().parent
PROMPTS = ROOT / "prompts"
VOICE_FILE = ROOT / "voice" / "meera-pillai-voice.md"


def window_months():
    try:
        return int(os.environ.get("WINDOW_MONTHS", "6"))
    except ValueError:
        return 6


def voice():
    return VOICE_FILE.read_text(encoding="utf-8")


def _prompt(name, **fields):
    text = (PROMPTS / name).read_text(encoding="utf-8")
    fields.setdefault("TODAY", time.strftime("%d %B %Y"))
    fields.setdefault("WINDOW", str(window_months()))
    for key, value in fields.items():
        text = text.replace("{" + key + "}", str(value))
    missing = re.findall(r"\{([A-Z_]+)\}", text)
    if missing:
        raise RuntimeError(f"{name} has unfilled placeholders: {sorted(set(missing))}")
    return text


# --- step 1: rank -----------------------------------------------------------

def rank(notes):
    """No search. Ranking is a judgement about text already in hand; a ranker
    that can search is a ranker that wanders off into research."""
    listing = "\n\n".join(f"NOTE {n['id']} (added {n['added']}):\n{n['text']}"
                          for n in notes)
    text, _ = call_model(_prompt("01-rank.md", NOTES=listing), search=False)
    return text


_RANK_ROW = re.compile(
    r"^\s*\|[^|]*\|\s*NOTE\s*(\d+)\s*\|\s*(NOT POSTABLE|POSTABLE)\s*\|([^|]*)\|",
    re.I | re.M)


def parse_ranking(text, valid_ids):
    """Rows in order, as (note_id, postable, why). Unknown ids are dropped - a
    model that invents NOTE 99 must not be able to queue a draft for it."""
    rows = []
    seen = set()
    for m in _RANK_ROW.finditer(text):
        note_id = int(m.group(1))
        if note_id in seen or note_id not in valid_ids:
            continue
        seen.add(note_id)
        rows.append((note_id, m.group(2).upper() == "POSTABLE", m.group(3).strip()))
    return rows


# --- step 2: research + link check ------------------------------------------

def research(note_text):
    text, cites = call_model(_prompt("02-research.md", NOTE=note_text), search=True)
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


def voice_check(draft_text, link_report):
    """Separate call, no search, and it is handed the draft only - not the
    prompt that produced it. A grader sharing the writer's instructions shares
    its blind spots."""
    text, _ = call_model(_prompt("04-voice-check.md", VOICE=voice(),
                                 DRAFT=draft_text, LINKCHECK=link_report),
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

def run_note(note, progress=lambda msg: None):
    """Research -> link check -> draft -> voice check. Returns (draft_id,
    version). Every step is written to disk as it completes, so a crash loses
    at most one step and she can still read what came back."""
    draft_id = store.new_draft(note)

    progress("researching the web ...")
    research_text = research(note["text"])
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

    progress("checking it against her voice ...")
    check_text = voice_check(draft_text, link_report)
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

    progress("re-checking the voice ...")
    check_text = voice_check(new_text, link_report)
    store.save_step(draft_id, f"04-voice-check-v{new_version}", check_text)
    return new_version
