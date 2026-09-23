"""The only place that talks to the Gemini API.

Lifted from research-chain's run.py (Interactions API, Google Search
grounding) with one deliberate change: failures raise ModelError instead of
calling sys.exit. A long-running bot must survive a bad call and tell her
about it, not die in the middle of the night.
"""

import json
import os
import time
import urllib.error
import urllib.request

API_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"


class ModelError(RuntimeError):
    """A call failed after its retries. Carries a message fit to send to her."""


def model_name():
    return os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")


def call_model(prompt, search=True, retries=3, timeout=300):
    """Returns (text, citations). `search=False` turns grounding off for the
    steps that must reason only about text they were handed - ranking and the
    voice checklist. A grader that can search is a grader that can wander."""
    payload = {"model": model_name(), "input": prompt}
    if search:
        payload["tools"] = [{"type": "google_search"}]
    body = json.dumps(payload).encode("utf-8")

    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise ModelError("GEMINI_API_KEY is not set. Put it in .env.")

    last = None
    for attempt in range(retries):
        req = urllib.request.Request(API_URL, data=body, method="POST", headers={
            "x-goog-api-key": key,
            "Content-Type": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return parse_response(json.loads(r.read().decode("utf-8")))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            last = f"HTTP {e.code}: {detail}"
            if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(5 * (attempt + 1))
                continue
            raise ModelError(last)
        except urllib.error.URLError as e:
            last = f"network error: {e}"
            if attempt < retries - 1:
                time.sleep(5 * (attempt + 1))
                continue
            raise ModelError(last)
    raise ModelError(last or "call failed")


def parse_response(data):
    """Text and citations out of the steps[] structure. Defensive on purpose -
    a shape change should degrade, not crash."""
    chunks, cites = [], {}
    for step in data.get("steps", []):
        if step.get("type") != "model_output":
            continue
        for part in step.get("content", []):
            if part.get("type") != "text":
                continue
            chunks.append(part.get("text", ""))
            for ann in part.get("annotations", []):
                if ann.get("type") == "url_citation" and ann.get("url"):
                    cites[ann["url"]] = ann.get("title") or ann["url"]

    text = "\n".join(c for c in chunks if c).strip()
    if not text:
        raise ModelError("the model returned no text - "
                         + json.dumps(data)[:300])
    return text, cites
