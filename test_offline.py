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

import feeds  # noqa: E402
import style  # noqa: E402
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

SCORING = """## Scores

| Note | Score | A | B | C | Reason |
|---|---|---|---|---|---|
| NOTE 2 | 4 | YES | YES | THIN | emotion named, misconception only half-stated |
| NOTE 1 | 2 | NO | YES | NO | restates linkedin_post_001 on niacinamide |
| NOTE 99 | 5 | YES | YES | YES | a note that does not exist |

## Corpus note

Checked against 4 LinkedIn posts.
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
    if "scoring a founder" in prompt:
        return SCORING, {}
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


def fake_gather(note_text, days=180):
    """No network. The real fetch is exercised by hand, not by this suite -
    a test that depends on what Google News happens to be carrying today is a
    test that fails for reasons that are nothing to do with this code."""
    return ([{"title": "A real article", "link": "https://pub.example/a",
              "date": "2026-09-01", "source": "The Hindu", "citable": True}], [])


SENT = []


def fake_send(chat_id, text, plain=False):
    SENT.append(text)


pipeline.call_model = fake_call
REAL_CHECK_LINK = links.check_link   # kept so the skip rule can be tested for real
links.check_link = fake_check_link
bot.send = fake_send
feeds.gather = fake_gather
pipeline.VOICE_FILE = ROOT / "voice" / "meera-pillai-voice.md"


# --- checks -----------------------------------------------------------------

def test_prompts_fill():
    print("\nprompts")
    check("voice file is present and non-trivial",
          pipeline.VOICE_FILE.exists() and len(pipeline.voice()) > 10_000)
    every = {
        "01-rank.md": {"NOTES": "x", "LINKEDIN_CORPUS": "c", "NEWSLETTER_CORPUS": "n"},
        "02-research.md": {"NOTE": "x", "FEEDS": "f"},
        "03-draft.md": {"VOICE": "v", "NOTE": "n", "RESEARCH": "r", "LINKCHECK": "l"},
        "04-voice-check.md": {"VOICE": "v", "DRAFT": "d", "LINKCHECK": "l",
                              "STYLE": "s"},
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
          f"{pipeline.window_months()} months" in
          pipeline._prompt("02-research.md", NOTE="x", FEEDS="f"))


def test_score_parser():
    print("\nscore parser")
    rows = pipeline.parse_scores(SCORING, {1, 2})
    check("reads two real rows", len(rows) == 2, str(rows))
    check("highest score first", [r["score"] for r in rows] == [4, 2])
    check("the three parameter columns are kept",
          rows[0]["a"] == "YES" and rows[0]["c"] == "THIN")
    check("the one-line reason is kept", "half-stated" in rows[0]["reason"])
    check("an invented NOTE 99 is dropped", all(r["id"] != 99 for r in rows))
    check("a table it cannot read returns nothing, rather than guessing",
          pipeline.parse_scores("no table here", {1, 2}) == [])


def test_threshold():
    print("\nthreshold (applied in code, not by the model)")
    rows = pipeline.parse_scores(SCORING, {1, 2})
    os.environ["MIN_SCORE_TO_DRAFT"] = "3"
    drafted, rejected = pipeline.decide(rows)
    check("4 of 5 is drafted", [r["id"] for r in drafted] == [2])
    check("2 of 5 is rejected, with its reason kept for the message back",
          [r["id"] for r in rejected] == [1] and rejected[0]["reason"])
    os.environ["MIN_SCORE_TO_DRAFT"] = "5"
    check("raising the threshold rejects everything", pipeline.decide(rows)[0] == [])
    os.environ["MIN_SCORE_TO_DRAFT"] = "3"
    check("exactly 3 drafts rather than falling in the gap",
          pipeline.decide([{"id": 9, "score": 3, "a": "YES", "b": "YES",
                            "c": "NO", "reason": "x"}])[0] != [])


def test_corpus():
    print("\ncorpus")
    linkedin, n_li = pipeline.corpus("linkedin")
    news, n_nl = pipeline.corpus("newsletters")
    check("her LinkedIn posts are loaded", n_li >= 4, str(n_li))
    check("her newsletters are loaded", n_nl >= 11, str(n_nl))
    check("the two are kept apart", linkedin != news)
    check("a missing corpus says so rather than passing silently",
          "guess" in pipeline.corpus("nonexistent")[0])
    prompt = pipeline._prompt("01-rank.md", NOTES="n", LINKEDIN_CORPUS=linkedin,
                              NEWSLETTER_CORPUS=news)
    check("only LinkedIn posts constrain novelty, per her decision",
          "ONLY those" in prompt and "do NOT constrain" in prompt)
    check("the real corpus reaches the prompt", "niacinamide" in prompt.lower())


def test_feeds():
    print("\nfeeds (parsing only, no network)")
    rss = b"""<?xml version="1.0"?><rss version="2.0"><channel>
    <item><title>A real article</title><link>https://pub.example/a</link>
    <pubDate>Mon, 01 Sep 2026 10:00:00 GMT</pubDate><source>The Hindu</source></item>
    </channel></rss>"""
    items = feeds._items(rss, citable=True)
    check("an RSS item is parsed", len(items) == 1)
    check("its date comes from the feed, not a model", items[0]["date"] == "2026-09-01")
    check("a direct feed item is citable", items[0]["citable"] is True)
    google = feeds._items(rss, citable=False)
    check("a Google News item is marked lead-only", google[0]["citable"] is False)
    report = feeds.report(google, ["somefeed: URLError down"], 180)
    check("the report forbids citing a lead", "Do not cite it" in report)
    check("a feed that is down is reported, not hidden", "URLError" in report)
    check("keywords drop stopwords",
          "the" not in feeds.keywords("the sunscreen is the problem"))


def test_verdict_parser():
    print("\nvoice-check parser")
    verdict, failed = pipeline.parse_verdict(CHECK_OUTPUT)
    check("reads FAIL", verdict == "FAIL")
    check("reads which checks failed", failed == "6", failed)
    check("a PASS with no failures reads clean",
          pipeline.parse_verdict("VERDICT: PASS\nFAILED CHECKS: none") == ("PASS", ""))
    check("an unreadable check counts as FAIL, not PASS",
          pipeline.parse_verdict("the model rambled")[0] == "FAIL")

    rows = pipeline.failed_checks(CHECK_OUTPUT)
    check("a failed number arrives with what it means, not bare",
          rows == [(6, "sources load", "example.com/gone is DEAD")], str(rows))
    check("passing rows are not reported as failures",
          all(n != 1 for n, _, _ in rows))
    check("nothing failed reads back as nothing",
          pipeline.failed_checks("| 1 | anything | YES | fine |") == [])


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
    for name in ("note", "01a-feed", "02-research", "02a-link-check",
                 "03-draft-v1", "03a-style-v1", "04-voice-check-v1"):
        check(f"{name}.md written", (d / f"{name}.md").exists())
    check("exactly three model calls: research, draft, check", len(CALLS) == 3,
          str(len(CALLS)))
    check("the feed step is mechanical - it spends no model call",
          (d / "01a-feed.md").exists())
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


def test_style_measurement():
    print("\nstyle measurement (mechanical)")
    her = (ROOT / "corpus" / "linkedin" / "linkedin_post_002.md")
    if her.exists():
        check("her own published post passes its own test",
              style.failures(her.read_text(encoding="utf-8")) == [])
    corporate = ("The implementation of the standardisation requirement "
                 "necessitates consideration of the bioavailability "
                 "documentation and the substantiation of every "
                 "characterisation. ") * 8
    off = [label for label, *_ in style.failures(corporate)]
    check("consultancy prose is caught",
          any("abstract" in l for l in off) and any("11 letters" in l for l in off),
          str(off))
    m = style.measure("Short one. This sentence is a great deal longer than "
                      "the first one was, by some margin indeed.")
    check("sentences are counted", m["sentences"] == 2)
    check("short sentences are counted", m["short_pct"] == 50.0)
    check("a [DATA NEEDED] gap does not inflate the word count",
          style.measure("a [DATA NEEDED: x] b")["words"] == 3)
    os.environ["TARGET_WORDS_MIN"], os.environ["TARGET_WORDS_MAX"] = "300", "450"
    check("the word target is a setting, not taken from her corpus",
          style.word_target() == (300, 450))
    rows, _ = style.compare("word " * 600)
    check("over the word target is TOO HIGH",
          any(k == "words" and v == "TOO HIGH" for k, _, _, _, v in rows))
    check("no corpus means no invented range",
          style.bands() is not None)


def test_voice_notes():
    print("\nvoice notes")
    import json as _json
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["body"] = _json.loads(req.data.decode("utf-8"))
        raise gemini.ModelError("stopped before the network")

    real = gemini.urllib.request.urlopen
    gemini.urllib.request.urlopen = fake_urlopen
    os.environ.setdefault("GEMINI_API_KEY", "test-key-not-real")
    try:
        gemini.call_model("transcribe this", search=False,
                          audio=(b"fake ogg bytes", "audio/ogg"))
    except gemini.ModelError:
        pass
    finally:
        gemini.urllib.request.urlopen = real

    body = captured.get("body", {})
    parts = body.get("input")
    check("audio makes input a list of parts, not a string", isinstance(parts, list))
    check("the prompt goes first as text",
          parts and parts[0].get("type") == "text")
    check("the audio part carries a mime type Telegram actually sends",
          parts and parts[1].get("mime_type") == "audio/ogg")
    check("the audio is base64, not raw bytes",
          parts and isinstance(parts[1].get("data"), str))
    check("transcription never turns search on", "tools" not in body)

    prompt = pipeline._prompt("00-transcribe.md")
    check("the transcriber is told not to guess an ingredient name",
          "[unclear]" in prompt)
    check("silence has a defined answer, so it cannot invent one",
          "NO SPEECH" in prompt)


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
        test_score_parser()
        test_threshold()
        test_corpus()
        test_feeds()
        test_verdict_parser()
        test_post_split()
        test_link_check()
        test_full_run()
        test_bot_surface()
        test_style_measurement()
        test_voice_notes()
        test_no_linkedin_posting()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        for name in FAIL:
            print(f"  FAILED: {name}")
        sys.exit(1)
