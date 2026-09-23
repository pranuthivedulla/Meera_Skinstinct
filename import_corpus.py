#!/usr/bin/env python3
"""Turn her published pieces into corpus/ files the scorer can read.

    python import_corpus.py "C:\\path\\to\\seed data.pdf"

The corpus decides parameter A - whether a note is a new angle. The split
matters and is not cosmetic:

    corpus/linkedin/      novelty IS checked against these
    corpus/newsletters/   novelty is NOT checked against these

Her decision, 23 September 2026: a newsletter goes to subscribers and a
LinkedIn post to industry peers, so saying a newsletter's idea on LinkedIn
reaches a new audience rather than repeating herself. Newsletters are
available material; only LinkedIn posts constrain a new LinkedIn post.

Needs pypdf for a PDF (pip install pypdf). Plain .txt or .md files can be
dropped into corpus/linkedin/ and corpus/newsletters/ by hand instead - this
script is a convenience, not a dependency of the bot.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CORPUS = ROOT / "corpus"

# Piece markers in the seed data: "-- linkedin_post_001 --", "-- newsletter_003 --"
MARKER = re.compile(r"(linkedin_post|newsletter)[_\s-]*(\d{1,3})", re.I)


def pdf_text(path):
    try:
        import pypdf
    except ImportError:
        raise SystemExit("pip install pypdf, or drop .txt files into corpus/ by hand.")
    reader = pypdf.PdfReader(str(path))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def unwrap(text):
    """This PDF extracts one word per line. Rejoin them, then keep real
    paragraph breaks."""
    text = text.replace("\r\n", "\n")
    text = re.sub(r"\n[ \t]*\n", " ", text)       # word \n space \n word
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_pieces(text):
    """(kind, number, body) per piece, in document order."""
    marks = list(MARKER.finditer(text))
    pieces = []
    for i, m in enumerate(marks):
        start = m.end()
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        body = text[start:end]
        body = body.lstrip(" -–—\u2500\u2501\u2014")
        body = re.sub(r"^[\s\u2500-\u257f]+", "", body)
        # Drop the trailing rule/banner that introduces the next section.
        body = re.sub(r"[\u2500-\u257f]{5,}.*$", "", body, flags=re.S).strip()
        kind = "linkedin" if m.group(1).lower().startswith("linkedin") else "newsletters"
        if len(body) > 200:
            pieces.append((kind, int(m.group(2)), body))
    return pieces


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    src = Path(" ".join(sys.argv[1:]).strip('"'))
    if not src.exists():
        raise SystemExit(f"no such file: {src}")

    raw = pdf_text(src) if src.suffix.lower() == ".pdf" else src.read_text(encoding="utf-8")
    pieces = split_pieces(unwrap(raw))
    if not pieces:
        raise SystemExit("found no linkedin_post_NNN or newsletter_NNN markers.")

    for kind in ("linkedin", "newsletters"):
        (CORPUS / kind).mkdir(parents=True, exist_ok=True)

    counts = {"linkedin": 0, "newsletters": 0}
    for kind, number, body in pieces:
        name = f"{'linkedin_post' if kind == 'linkedin' else 'newsletter'}_{number:03d}.md"
        (CORPUS / kind / name).write_text(body + "\n", encoding="utf-8")
        counts[kind] += 1
        print(f"  {kind}/{name}  {len(body.split())} words")

    print(f"\n{counts['linkedin']} LinkedIn post(s) - novelty IS checked against these")
    print(f"{counts['newsletters']} newsletter(s) - novelty is NOT checked against these")


if __name__ == "__main__":
    main()
