"""Mechanical link checking. No model call.

Lifted from research-chain's run.py, where it exists because a live run cited
two dead links and a .jpg as the source of a quote, and the model-based
verifier marked all three VERIFIED. A model grading text cannot tell a 404
from a real page. This opens them.

DEAD means the page does not exist. BLOCKED means it could not be opened and
may well exist - news sites routinely refuse automated requests, and treating
that as "no source" would call real reporting fabricated.
"""

import concurrent.futures
import re
import time
import urllib.error
import urllib.parse
import urllib.request

# Gemini's grounding redirects are a search-API artefact. They expire, and no
# one can cite one in a post. Recorded, never fetched.
SKIP_HOSTS = ("vertexaisearch.cloud.google.com",)
MAX_LINKS = 40
LINK_TIMEOUT = 12
DEAD_CODES = (404, 410)
_NO_SUCH_HOST = ("getaddrinfo", "name or service not known", "nodename nor servname",
                 "no address associated", "name resolution")

_DATE_PATTERNS = [
    r'property=["\']article:published_time["\']\s+content=["\']([^"\']+)',
    r'content=["\']([^"\']+)["\']\s+property=["\']article:published_time',
    r'"datePublished"\s*:\s*"([^"]+)"',
    r'name=["\'](?:date|pubdate|publish-date|DC.date.issued)["\']\s+content=["\']([^"\']+)',
    r'<time[^>]+datetime=["\']([^"\']+)',
]


def extract_urls(text):
    """Every http(s) link in a piece of text, in order, deduplicated."""
    seen, urls = set(), []
    for raw in re.findall(r"https?://[^\s<>\"'\)\]\|]+", text or ""):
        url = raw.rstrip(".,;:")
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def page_date(html):
    """The page's own publication date, or None. Placeholder years are
    rejected: pages ship 1970-01-01 in their meta tags."""
    this_year = time.localtime().tm_year
    for pattern in _DATE_PATTERNS:
        for m in re.finditer(pattern, html, re.I):
            value = m.group(1).strip()[:25]
            year = re.match(r"(\d{4})", value)
            if year and 2000 <= int(year.group(1)) <= this_year + 1:
                return value
    return None


def check_link(url, timeout=LINK_TIMEOUT):
    """GET, not HEAD - plenty of sites answer HEAD with 405."""
    def result(state, code, date=None, note=""):
        return {"url": url, "state": state, "code": code, "date": date, "note": note}

    if any(host in url for host in SKIP_HOSTS):
        return result("NOT CHECKED", "skipped", note="search-API redirect, not fetched")
    req = urllib.request.Request(url, method="GET", headers={
        "User-Agent": "Mozilla/5.0 (skinstinct link check)",
        "Accept": "text/html,*/*",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            ctype = r.headers.get("Content-Type", "")
            final = r.geturl()
            if (urllib.parse.urlparse(url).path.strip("/")
                    and not urllib.parse.urlparse(final).path.strip("/")):
                return result("BLOCKED", r.status,
                              note=f"bounced to the homepage ({final}) - removed or refused")
            body = r.read(300_000).decode("utf-8", "replace") if "html" in ctype else ""
            note = "" if final == url else f"redirected to {final}"
            if not body:
                # An image or a PDF opening proves the file is there, not that
                # a claim can be read off it.
                note = (f"not a readable web page ({ctype.split(';')[0] or 'unknown type'})"
                        + (f"; {note}" if note else ""))
            return result("LOADS", r.status, page_date(body) if body else None, note)
    except urllib.error.HTTPError as e:
        if e.code in DEAD_CODES:
            return result("DEAD", e.code, note="page does not exist")
        return result("BLOCKED", e.code, note="the site refused the request - the page may exist")
    except Exception as e:  # noqa: BLE001 - DNS, TLS, timeout
        if any(marker in str(e).lower() for marker in _NO_SUCH_HOST):
            return result("DEAD", type(e).__name__, note="the domain does not exist")
        return result("BLOCKED", type(e).__name__, note=str(e)[:80])


def check_all(urls):
    urls = [u for u in urls][:MAX_LINKS]
    if not urls:
        return []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        return list(pool.map(check_link, urls))


def report(results):
    """A table the drafting step is handed as fact. It outranks whatever the
    research said about its own sources."""
    listed = [r for r in results if r["state"] != "NOT CHECKED"]
    skipped = len(results) - len(listed)
    lines = [
        "# Link check (mechanical, no model call)",
        "",
        f"Fetched from this machine on {time.strftime('%Y-%m-%d %H:%M')}. "
        "This is a measurement, not a judgement.",
        "",
        "| Link | HTTP | Result | Date on the page | Note |",
        "|---|---|---|---|---|",
    ]
    for r in listed:
        lines.append(f"| {r['url']} | {r['code']} | {r['state']} | {r['date'] or '-'} | {r['note']} |")
    if not listed:
        lines.append("| (none) | - | - | - | the research cited no fetchable link |")
    dead = [r for r in listed if r["state"] == "DEAD"]
    lines += ["", f"LINKS: {len(listed)} | DEAD: {len(dead)} | "
                  f"BLOCKED: {len([r for r in listed if r['state'] == 'BLOCKED'])} | "
                  f"SEARCH REDIRECTS NOT CHECKED: {skipped}", ""]
    lines.append("## Dead links - a claim resting on one of these is FABRICATED")
    lines += ([f"- {r['url']} ({r['code']})" for r in dead] or ["None."])
    return "\n".join(lines) + "\n"


def usable(results):
    """Links a draft is allowed to stand on: opened, and a readable page."""
    return [r for r in results
            if r["state"] == "LOADS" and "not a readable web page" not in r["note"]]
