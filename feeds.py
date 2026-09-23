"""Industry news, fetched mechanically. No model call.

Two kinds of source, and the difference matters for what a draft may cite:

DIRECT FEEDS (EXTRA_FEEDS in .env) are ordinary RSS. Their item links are the
publisher's own article URLs, so they can be cited in a post and the link
check can open them.

GOOGLE NEWS is discovery only. Its item links are news.google.com redirects
that carry a base64 blob, not the article. They answer 200 with a redirect
page, so the link check would call a dead article LOADS. They are handed to
the research step as LEADS - a headline, a date and a publisher name to go
and find - and are never offered as citations.

An RSS item carries a real publication date. That is the point of doing this
mechanically rather than asking a model what is recent: recency becomes a
fact instead of a claim.
"""

import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

GOOGLE_NEWS = ("https://news.google.com/rss/search"
               "?q={query}+when:{days}d&hl=en-IN&gl=IN&ceid=IN:en")
TIMEOUT = 15
MAX_ITEMS = 12


def extra_feeds():
    """Feeds she trusts, newline or comma separated in .env. These CAN be
    cited - their links are the publisher's own."""
    raw = (os.environ.get("EXTRA_FEEDS") or "").strip()
    return [f.strip() for f in re.split(r"[,\n]", raw) if f.strip()]


def _fetch(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (skinstinct feed reader)",
        "Accept": "application/rss+xml,application/xml,text/xml,*/*",
    })
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read()


def _items(xml_bytes, citable):
    out = []
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return out
    # RSS <item> and Atom <entry>, without pulling in a dependency.
    nodes = root.iter("item")
    entries = [n for n in root.iter("{http://www.w3.org/2005/Atom}entry")]
    for node in list(nodes) + entries:
        def text(tag, atom=None):
            el = node.find(tag)
            if el is None and atom:
                el = node.find(atom)
            return (el.text or "").strip() if el is not None and el.text else ""

        link = text("link", "{http://www.w3.org/2005/Atom}link")
        if not link:
            el = node.find("{http://www.w3.org/2005/Atom}link")
            link = el.get("href", "") if el is not None else ""
        title = text("title", "{http://www.w3.org/2005/Atom}title")
        if not title:
            continue
        raw_date = (text("pubDate") or text("{http://purl.org/dc/elements/1.1/}date")
                    or text("{http://www.w3.org/2005/Atom}updated"))
        source_el = node.find("source")
        out.append({
            "title": title,
            "link": link,
            "date": _normalise(raw_date),
            "source": (source_el.text or "").strip() if source_el is not None
                      and source_el.text else _host(link),
            "citable": citable,
        })
    return out


def _host(url):
    try:
        return urllib.parse.urlparse(url).netloc or "unknown"
    except ValueError:
        return "unknown"


def _normalise(raw):
    """RFC 822 or ISO, both appear. Returns YYYY-MM-DD, or the raw string if
    it cannot be read - never a guess."""
    if not raw:
        return ""
    try:
        return parsedate_to_datetime(raw).strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        pass
    m = re.match(r"(\d{4}-\d{2}-\d{2})", raw)
    return m.group(1) if m else raw[:25]


def keywords(note_text, limit=6):
    """A search query from the note itself. Deliberately dumb: the long,
    specific words are the ones worth searching, and a model is not needed to
    pick them out."""
    stop = {"the", "and", "that", "this", "with", "from", "they", "them", "their",
            "have", "has", "was", "were", "for", "but", "not", "you", "your",
            "our", "its", "what", "when", "which", "than", "then", "there",
            "about", "into", "most", "more", "just", "even", "because", "been",
            "would", "could", "should", "people", "thing", "things"}
    words = re.findall(r"[A-Za-z][A-Za-z-]{3,}", note_text.lower())
    seen, picked = set(), []
    for w in sorted(words, key=len, reverse=True):
        if w in stop or w in seen:
            continue
        seen.add(w)
        picked.append(w)
        if len(picked) == limit:
            break
    return picked


def gather(note_text, days=180):
    """Everything mechanical we can find for this note. Returns (items,
    errors) - a feed that is down is reported, never silently empty."""
    items, errors = [], []

    # Google News ANDs the terms, so the five longest words in a note find
    # nothing at all. Drop a term at a time until something comes back; stop
    # at two, below which the results stop being about the note.
    terms = keywords(note_text)
    for width in range(len(terms), 1, -1):
        query = urllib.parse.quote_plus(" ".join(terms[:width]) or "skincare India")
        try:
            found = _items(_fetch(GOOGLE_NEWS.format(query=query, days=days)), False)
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
            errors.append(f"news.google.com: {type(e).__name__} {str(e)[:60]}")
            break
        if found:
            items += found
            break

    for url in extra_feeds():
        try:
            items += _items(_fetch(url), True)
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
            errors.append(f"{_host(url)}: {type(e).__name__} {str(e)[:60]}")

    # Newest first. An item with no readable date sorts last rather than being
    # dropped - it may still be the lead worth chasing.
    items.sort(key=lambda i: i["date"] if re.match(r"\d{4}-\d{2}-\d{2}", i["date"] or "")
               else "0000-00-00", reverse=True)
    return items[:MAX_ITEMS], errors


def report(items, errors, days):
    lines = [
        "# Industry feed (mechanical, no model call)",
        "",
        f"Fetched {time.strftime('%Y-%m-%d %H:%M')}, looking back {days} days. "
        "Dates come from the feed itself, not from a model.",
        "",
        "| Date | Headline | Source | Citable? |",
        "|---|---|---|---|",
    ]
    for i in items:
        lines.append(f"| {i['date'] or '-'} | {i['title']} | {i['source']} | "
                     f"{'YES - cite this link' if i['citable'] else 'NO - lead only'} |")
    if not items:
        lines.append("| - | nothing came back | - | - |")
    lines += ["", "## Links", ""]
    for i in items:
        lines.append(f"- {'CITABLE' if i['citable'] else 'LEAD ONLY'}: {i['title']} "
                     f"-> {i['link']}")
    if errors:
        lines += ["", "## Feeds that did not answer", ""] + [f"- {e}" for e in errors]
    lines += ["", "A LEAD ONLY row is a Google News redirect. Do not cite it. "
                  "Find the publisher's own article and cite that."]
    return "\n".join(lines) + "\n"
