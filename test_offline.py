#!/usr/bin/env python3
"""Offline checks. No API key, no live call, no Telegram, no money spent.

    python test_offline.py

Every model call is stubbed. What this proves is the wiring: the prompts have
no unfilled placeholders, the parsers read back what the prompts promise to
write, the link check downgrades what it cannot open, and a full run writes
every step to disk in order.

What it cannot prove is voice quality or that the research is any good. Only a
live run against a real note shows that.
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# Point the store at a throwaway directory BEFORE anything imports it.
import store  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="skinstinct-test-"))
store.DATA = TMP
store.DRAFTS = TMP / "drafts"
store.NOTES_FILE = TMP / "notes.json"
store.STATE_FILE = TMP / "state.json"

import gemini  # noqa: E402
import links  # noqa: E402
import pipeline  # noqa: E402
import bot  # noqa: E402

PASS, FAIL = [], []


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append(name)
    print(f"  {'ok  ' if condition else 'FAIL'} {name}" + (f"  - {detail}" if detail and not condition else ""))


# --- stubs ------------------------------------------------------------------

CALLS = []

RANKING = """## Ranking

| Rank | Note | Verdict | Why |
|---|---|---|---|
| 1 | NOTE 2 | POSTABLE | names a specific SPF testing figure, test 1 |
| 2 | NOTE 1 | NOT POSTABLE | fails test 4, needs our own return rate |
| 3 | NOTE 99 | POSTABLE | a note that does not exist |

## What the top note needs

Public reapplication data.
"""

RESEARCH = """## What is current

| Fact | Figure with unit | Source URL | Date published | Tier | In window? |
|---|---|---|---|---|---|
| Test density | 2 mg/cm2 | https://example.com/standard | 2026-04-01 | 1 | yes |
| Dead one | 40% | https://example.com/gone | 2026-05-02 | 3 | yes |

## The mechanism

INFERENCE this is a stub.

## What I could not find

Indian reapplication data.

## What would make this post wrong

Nothing, it is a stub.
"""

DRAFT = """## POST

SPF ratings are tested at 2 mg per square centimetre. Most people apply a
quarter of that. [DATA NEEDED: our own return rate] is the figure I still owe
you. What does the brand test at?

## SOURCES

| Sentence | Source URL | Date |
|---|---|---|
| SPF ratings are tested at | https://example.com/standard | 2026-04-01 |
"""

CHECK_OUTPUT = """## Voice check

| # | Question | YES/NO | Evidence |
|---|---|---|---|
| 1 | opening | YES | quoted |
| 6 | sources load | NO | example.com/gone is DEAD |

## Verdict

VERDICT: FAIL
FAILED CHECKS: 6

## What she should look at first

The dead source.
"""


def fake_call(prompt, search=True, **kw):
    CALLS.append({"search": search, "prompt": prompt})
    if "ranking a founder" in prompt:
        return RANKING, {}
    if "You are researching ONE note" in prompt:
        return RESEARCH, {"https://example.com/standard": "A standard"}
    if "checking a draft against a voice specification" in prompt:
        return CHECK_OUTPUT, {}
    if "Revise a LinkedIn post" in prompt:
        return DRAFT.replace("Most people apply a quarter", "Most apply a quarter"), {}
    if "Write ONE LinkedIn post" in prompt:
        return DRAFT, {}
    raise AssertionError("a prompt reached the model that no stub recognises")


def fake_check_link(url, timeout=None):
    if url.endswith("/gone"):
        return {"url": url, "state": "DEAD", "code": 404, "date": None,
                "note": "page does not exist"}
    if url.endswith(".jpg"):
        return {"url": url, "state": "LOADS", "code": 200, "date": None,
                "note": "not a readable web page (image/jpeg)"}
    return {"url": url, "state": "LOADS", "code": 200, "date": "2026-04-01", "note": ""}


SENT = []


def fake_send(chat_id, text, plain=False):
    SENT.append(text)


pipeline.call_model = fake_call
REAL_CHECK_LINK = links.check_link   # kept so the skip rule can be tested for real
links.check_link = fake_check_link
bot.send = fake_send
pipeline.VOICE_FILE = ROOT / "voice" / "meera-pillai-voice.md"


# --- checks -----------------------------------------------------------------

def test_prompts_fill():
    print("\nprompts")
    check("voice file is present and non-trivial",
          pipeline.VOICE_FILE.exists() and len(pipeline.voice()) > 10_000)
    every = {
        "01-rank.md": {"NOTES": "x"},
        "02-research.md": {"NOTE": "x"},
        "03-draft.md": {"VOICE": "v", "NOTE": "n", "RESEARCH": "r", "LINKCHECK": "l"},
        "04-voice-check.md": {"VOICE": "v", "DRAFT": "d", "LINKCHECK": "l"},
        "05-revise.md": {"VOICE": "v", "NOTE": "n", "RESEARCH": "r",
                         "LINKCHECK": "l", "DRAFT": "d", "INSTRUCTION": "i"},
    }
    for name, fields in every.items():
        try:
            text = pipeline._prompt(name, **fields)
            check(f"{name} fills with no placeholder left", "{" not in text
                  or not any(c.isupper() for c in text.split("{")[-1][:20]))
        except Exception as e:  # noqa: BLE001
            check(f"{name} fills with no placeholder left", False, str(e))
    check("the window is substituted, not hardcoded",
          f"{pipeline.window_months()} months" in pipeline._prompt("02-research.md", NOTE="x"))


def test_ranking_parser():
    print("\nranking parser")
    rows = pipeline.parse_ranking(RANKING, {1, 2})
    check("reads two real rows", len(rows) == 2, str(rows))
    check("top row is note 2 and POSTABLE", rows[0][0] == 2 and rows[0][1] is True)
    check("NOT POSTABLE is not read as POSTABLE", rows[1][1] is False)
    check("an invented NOTE 99 is dropped", all(r[0] != 99 for r in rows))
    check("a table it cannot read returns nothing, rather than guessing",
          pipeline.parse_ranking("no table here", {1, 2}) == [])


def test_verdict_parser():
    print("\nvoice-check parser")
    verdict, failed = pipeline.parse_verdict(CHECK_OUTPUT)
    check("reads FAIL", verdict == "FAIL")
    check("reads which checks failed", failed == "6", failed)
    check("a PASS with no failures reads clean",
          pipeline.parse_verdict("VERDICT: PASS\nFAILED CHECKS: none") == ("PASS", ""))
    check("an unreadable check counts as FAIL, not PASS",
          pipeline.parse_verdict("the model rambled")[0] == "FAIL")


def test_post_split():
    print("\ndraft splitting")
    post, sources = pipeline.split_post(DRAFT)
    check("the post excludes the SOURCES table", "Source URL" not in post)
    check("the sources table is kept separately", "example.com/standard" in sources)
    check("a missing heading returns the whole output rather than nothing",
          pipeline.split_post("just text")[0] == "just text")
    check("placeholders are found", pipeline.placeholders(post) ==
          ["[DATA NEEDED: our own return rate]"])
    check("word count is sane", 20 < pipeline.word_count(post) < 60,
          str(pipeline.word_count(post)))


def test_link_check():
    print("\nlink check")
    results = links.check_all(["https://example.com/standard",
                              "https://example.com/gone",
                              "https://example.com/photo.jpg"])
    states = {r["url"].rsplit("/", 1)[-1]: r["state"] for r in results}
    check("a 404 is DEAD", states["gone"] == "DEAD")
    check("a real page LOADS", states["standard"] == "LOADS")
    # The real function, not the stub: this rule must hold without a network.
    redirect = REAL_CHECK_LINK("https://vertexaisearch.cloud.google.com/grounding/x")
    check("a search redirect is never fetched", redirect["state"] == "NOT CHECKED")
    usable = links.usable(results)
    check("a .jpg does not count as a usable source",
          all(".jpg" not in r["url"] for r in usable))
    report = links.report(results)
    check("the report names the dead link", "example.com/gone" in
          report.split("Dead links")[1])
    check("urls are extracted from prose",
          links.extract_urls("see https://a.com/x, and https://a.com/x again")
          == ["https://a.com/x"])


def test_full_run():
    print("\na whole run")
    CALLS.clear()
    note = store.add_note("SPF is tested at a density nobody applies", 1)
    draft_id, version = pipeline.run_note(note)
    d = store.draft_dir(draft_id)
    for name in ("note", "02-research", "02a-link-check", "03-draft-v1",
                 "04-voice-check-v1"):
        check(f"{name}.md written", (d / f"{name}.md").exists())
    check("exactly three model calls: research, draft, check", len(CALLS) == 3,
          str(len(CALLS)))
    check("only the research call may search",
          [c["search"] for c in CALLS] == [True, False, False])
    check("the drafting call is handed the link check",
          "Link check (mechanical" in CALLS[1]["prompt"])
    check("the checking call never sees the drafting prompt",
          "Write ONE LinkedIn post" not in CALLS[2]["prompt"])
    check("the note is marked drafted, so it is not ranked again",
          store.get_note(note["id"])["status"] == store.DRAFTED)
    check("it is off the open list", store.open_notes() == [])

    new_version = pipeline.run_revision(draft_id, version, "make it shorter")
    check("a revision is v2, and v1 is kept", new_version == 2
          and (d / "03-draft-v1.md").exists() and (d / "03-draft-v2.md").exists())
    check("the revision is re-checked, not assumed to still pass",
          (d / "04-voice-check-v2.md").exists())


def test_bot_surface():
    print("\nbot")
    SENT.clear()
    bot.handle({"chat": {"id": 7}, "from": {"id": 7}, "text": "a plain note"})
    check("a plain message is saved as a note, and spends nothing",
          any("Noted as" in s for s in SENT))
    SENT.clear()
    bot.handle({"chat": {"id": 7}, "from": {"id": 7}, "text": "/notes"})
    check("/notes lists it", any("open note" in s for s in SENT))
    SENT.clear()
    bot.handle({"chat": {"id": 7}, "from": {"id": 7}, "text": "/revise shorter"})
    check("/revise with no draft open refuses rather than crashing",
          any("No draft open" in s for s in SENT))
    SENT.clear()
    bot.handle({"chat": {"id": 7}, "from": {"id": 7}, "text": "/nonsense"})
    check("an unknown command is answered, not ignored",
          any("do not know" in s for s in SENT))

    os.environ["ALLOWED_USER_IDS"] = "42"
    check("an unlisted user is refused", not bot.allowed(7))
    check("a listed user is allowed", bot.allowed(42))
    del os.environ["ALLOWED_USER_IDS"]
    check("an empty allow-list lets her in on first run", bot.allowed(7))

    long_text = "\n\n".join(["para " * 200] * 10)
    chunks = bot._chunks(long_text)
    check("a long post is split under Telegram's limit",
          all(len(c) <= bot.TELEGRAM_LIMIT for c in chunks) and len(chunks) > 1)
    check("nothing is dropped in the split",
          sum(c.count("para") for c in chunks) == long_text.count("para"))
    check("a draft is sent as a copyable block", bot.code("a < b").startswith("<pre>")
          and "&lt;" in bot.code("a < b"))


def test_no_linkedin_posting():
    print("\nboundaries")
    source = "\n".join((ROOT / f).read_text(encoding="utf-8")
                       for f in ("bot.py", "pipeline.py", "gemini.py", "links.py"))
    check("nothing in the code talks to LinkedIn",
          "linkedin.com/v2" not in source and "api.linkedin" not in source)
    check("the only model endpoint is Gemini's",
          "generativelanguage.googleapis.com" in source
          and "api.anthropic.com" not in source)
    check("no API key is hardcoded",
          "AIza" not in source and "AQ." not in source)


if __name__ == "__main__":
    try:
        test_prompts_fill()
        test_ranking_parser()
        test_verdict_parser()
        test_post_split()
        test_link_check()
        test_full_run()
        test_bot_surface()
        test_no_linkedin_posting()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        for name in FAIL:
            print(f"  FAILED: {name}")
        sys.exit(1)
