"""Does the draft read like her? Measured, not judged. No model call.

Built 23 September 2026 after two live drafts came back "too corporate". The
voice checker had passed 13 of 15 questions on one of them, because register
is not a thing a yes/no question about wording catches.

Measuring her four published LinkedIn posts against those drafts showed the
difference precisely, and it was not length:

                        her posts      the drafts
    avg sentence        15.3-17.3 w    15.9 and 20.5
    short sentences     21-35%         19% and 14%
    abstract nouns      1.4-4.3%       7.3% and 6.2%
    long words          3.7-8.3%       10.8% and 9.6%

She writes at about the same sentence length with HALF the abstract nouns,
and lands on a short flat sentence far more often. "Transdermal flux governs
bioavailability" is the failure; "The checklist registers presence." is her.

The bands below are computed from corpus/linkedin/ at runtime, so they are
her actual numbers rather than numbers written down once and left to rot. Add
a post to the corpus and the target moves with her.
"""

import os
import re
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CORPUS = ROOT / "corpus" / "linkedin"

# Endings that mark a noun made out of a verb or an adjective. A high rate of
# these is the single clearest difference between her prose and a model's.
_ABSTRACT = re.compile(r"(tion|ment|ity|ance|ence|ised|ized|isation|ization)$")
LONG_WORD = 11
SHORT_SENTENCE = 8


def measure(text):
    text = re.sub(r"\[DATA NEEDED:[^\]]*\]", "X", text or "")
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if len(s.strip()) > 3]
    lengths = [len(re.findall(r"[\w'-]+", s)) for s in sentences] or [0]
    words = re.findall(r"[A-Za-z'-]+", text.lower()) or [""]
    return {
        "words": len(re.findall(r"[\w'-]+", text)),
        "paragraphs": len([p for p in text.split("\n\n") if p.strip()]),
        "sentences": len(sentences),
        "avg_sentence": statistics.mean(lengths),
        "short_pct": 100 * sum(1 for n in lengths if n <= SHORT_SENTENCE) / len(lengths),
        "abstract_pct": 100 * sum(1 for w in words if _ABSTRACT.search(w)) / len(words),
        "long_pct": 100 * sum(1 for w in words if len(w) >= LONG_WORD) / len(words),
    }


def bands():
    """(low, high) per metric, from her own published posts. Returns None if
    the corpus is empty - no corpus means no target, not a made-up one."""
    pieces = sorted(CORPUS.glob("*.md")) + sorted(CORPUS.glob("*.txt"))
    if len(pieces) < 2:
        return None
    rows = [measure(p.read_text(encoding="utf-8")) for p in pieces]
    out = {}
    for key in ("avg_sentence", "short_pct", "abstract_pct", "long_pct"):
        values = [r[key] for r in rows]
        out[key] = (min(values), max(values))
    # Length is the one target that is NOT taken from the corpus. Her posts
    # run 429-538 words; she asked on 23 September 2026 for shorter than that,
    # so it is a setting, and the drafting prompt is given the same numbers.
    out["words"] = word_target()
    return out


def word_target():
    def get(name, fallback):
        try:
            return int(os.environ.get(name, fallback))
        except ValueError:
            return int(fallback)
    return get("TARGET_WORDS_MIN", 300), get("TARGET_WORDS_MAX", 450)


# A metric she should not exceed, versus one she should not fall below.
_CEILING = {"abstract_pct", "long_pct", "avg_sentence"}
_FLOOR = {"short_pct"}

_LABEL = {
    "avg_sentence": "average sentence, in words",
    "short_pct": "sentences of 8 words or fewer",
    "abstract_pct": "abstract nouns (-tion, -ment, -ity)",
    "long_pct": "words of 11 letters or more",
    "words": "total words",
}


def compare(text):
    """[(metric, value, low, high, verdict)]. Verdict is OK, TOO HIGH or TOO
    LOW - a band, not a score. Tolerance is 10% outside her own range, because
    her four posts are a small sample and their edges are not a law."""
    band = bands()
    got = measure(text)
    if not band:
        return [], got
    rows = []
    for key, (low, high) in band.items():
        value = got[key]
        verdict = "OK"
        if key in _CEILING and value > high * 1.1:
            verdict = "TOO HIGH"
        elif key in _FLOOR and value < low * 0.9:
            verdict = "TOO LOW"
        elif key == "words":
            # Length is her explicit instruction, so it is held exactly - no
            # 10% grace on a number she chose.
            verdict = "TOO HIGH" if value > high else ("TOO LOW" if value < low else "OK")
        rows.append((key, value, low, high, verdict))
    return rows, got


def report(text):
    rows, got = compare(text)
    lines = [
        "# Style measurement (mechanical, no model call)",
        "",
        "Her own published LinkedIn posts set the range. This is counting, not",
        "an opinion about the writing.",
        "",
        "| Measure | This draft | Her range | |",
        "|---|---|---|---|",
    ]
    if not rows:
        lines.append("| (no corpus/linkedin/ - no range to compare against) | | | |")
    for key, value, low, high, verdict in rows:
        fmt = "{:.0f}" if key == "words" else "{:.1f}"
        lines.append(f"| {_LABEL[key]} | {fmt.format(value)} | "
                     f"{fmt.format(low)}-{fmt.format(high)} | {verdict} |")
    lines += ["", f"{got['words']} words, {got['paragraphs']} paragraphs, "
                  f"{got['sentences']} sentences.", ""]
    bad = [r for r in rows if r[4] != "OK"]
    if bad:
        lines.append("OUT OF RANGE: " + ", ".join(f"{_LABEL[k]} {v}" for k, _, _, _, v in
                                                  [(r[0], r[1], r[2], r[3], r[4]) for r in bad]))
    else:
        lines.append("Every measure sits inside her own range.")
    return "\n".join(lines) + "\n"


def failures(text):
    """Just the metrics that are out of range, for the message back to her."""
    rows, _ = compare(text)
    return [(_LABEL[k], value, low, high, verdict)
            for k, value, low, high, verdict in rows if verdict != "OK"]
