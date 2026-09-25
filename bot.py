#!/usr/bin/env python3
"""Telegram bot: her notes in, a LinkedIn post out.

Long polling, so there is no public URL, no webhook and nothing to deploy. It
runs while this script runs. Standard library only.

    python bot.py

What it does with a message:

    plain text        saved as a note. Nothing is spent. Nothing is drafted.
    a voice note      transcribed by Gemini, shown to her, then saved as a note.
    /draft            scores every open note 0-5 against her three
                      parameters, sends a reason back for anything below the
                      threshold, then researches and drafts the top one.
    /draft 7          skips the ranking and drafts note 7.
    /notes            lists the open notes.
    /skip 3           takes note 3 out of the ranking.
    /revise <what>    a new version of the current draft.
    /approve          writes the final text to disk and prints it clean.
    /post             prints the current draft again, on its own, to copy.
    /whoami           the Telegram user id, for ALLOWED_USER_IDS.

It never posts to LinkedIn. It hands her text; she posts it.
"""

import html
import json
import os
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pipeline
import store
import style
import verify
from gemini import ModelError

ROOT = Path(__file__).resolve().parent
POLL_TIMEOUT = 50          # seconds Telegram holds the long poll open
TELEGRAM_LIMIT = 3500      # real limit is 4096; leave room for our wrapper

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    # Windows consoles default to cp1252 and crash on a rupee sign.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


# --- .env -------------------------------------------------------------------

def load_env():
    """A five-line dotenv. Real environment variables win, so a key exported
    in the shell is not silently overridden by a stale file."""
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


# --- Telegram ---------------------------------------------------------------

def api(method, **params):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not set. Copy .env.example to "
                         ".env and paste the token from @BotFather.")
    url = f"https://api.telegram.org/bot{token}/{method}"
    body = json.dumps(params).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=POLL_TIMEOUT + 15) as r:
        data = json.loads(r.read().decode("utf-8"))
    if not data.get("ok"):
        raise RuntimeError(f"telegram {method}: {data}")
    return data.get("result")


def send(chat_id, text, plain=False):
    """Telegram caps a message at 4096 characters, and a post plus its sources
    goes past that. Split on blank lines so a paragraph is never cut."""
    for chunk in _chunks(text):
        params = {"chat_id": chat_id, "text": chunk,
                  "disable_web_page_preview": True}
        if not plain:
            params["parse_mode"] = "HTML"
        try:
            api("sendMessage", **params)
        except Exception:
            # A stray < or & in a draft must not swallow her post.
            api("sendMessage", chat_id=chat_id, text=chunk,
                disable_web_page_preview=True)


def _chunks(text, limit=TELEGRAM_LIMIT):
    out, current = [], ""
    for para in text.split("\n\n"):
        if len(current) + len(para) + 2 > limit:
            if current:
                out.append(current.strip())
            while len(para) > limit:
                out.append(para[:limit])
                para = para[limit:]
            current = para + "\n\n"
        else:
            current += para + "\n\n"
    if current.strip():
        out.append(current.strip())
    return out or [text[:limit]]


def code(text):
    """Monospace block - what she long-presses to copy in one tap."""
    return "<pre>" + html.escape(text) + "</pre>"


# --- access -----------------------------------------------------------------

def allowed(user_id):
    raw = (os.environ.get("ALLOWED_USER_IDS") or "").strip()
    if not raw:
        return True          # first run: the bot prints ids so she can fill it
    ids = {p.strip() for p in raw.split(",") if p.strip()}
    return str(user_id) in ids


# --- commands ---------------------------------------------------------------

HELP = """What I do with what you send me.

<b>Just type a note</b> - a sentence or a paragraph. I save it. Nothing is spent and nothing is drafted until you ask.

<b>Or send a voice note</b> - I transcribe it, show you the transcript so you can check the ingredient names, and save that as the note.

<b>/draft</b> - I score every open note out of 5 against three things: is it a new angle you have not posted on LinkedIn before, does it name an emotion, does it correct a common misconception. Anything below 3 gets a short message saying why, and no draft. The top note above the line gets researched and written.
<b>/draft 7</b> - skip the ranking, draft note 7.
<b>/notes</b> - the open notes.
<b>/skip 3</b> - take note 3 out of the ranking.
<b>/revise make it shorter, drop the second paragraph</b> - a new version.
<b>/post</b> - print the current draft again, clean, to copy.
<b>/approve</b> - save the final text.
<b>/whoami</b> - your Telegram user id.

I never post to LinkedIn. I hand you the text; you post it.
I never invent a Skinstinct figure - where one is needed you will see [DATA NEEDED: ...] and it is yours to fill."""


def cmd_notes(chat_id):
    notes = store.open_notes()
    if not notes:
        send(chat_id, "No open notes. Send me one - a sentence is enough.")
        return
    lines = [f"<b>{len(notes)} open note(s)</b>", ""]
    for n in notes:
        text = n["text"].replace("\n", " ")
        if len(text) > 160:
            text = text[:157] + "..."
        lines.append(f"<b>{n['id']}</b> ({n['added']}) {html.escape(text)}")
    send(chat_id, "\n".join(lines))


def cmd_skip(chat_id, arg):
    try:
        note_id = int(arg)
    except (TypeError, ValueError):
        send(chat_id, "Which one? /skip 3")
        return
    if not store.get_note(note_id):
        send(chat_id, f"No note {note_id}.")
        return
    store.set_status(note_id, store.SKIPPED)
    send(chat_id, f"Note {note_id} is out of the ranking.")


def score_summary(rows, n_linkedin):
    """One line per note. The three columns are her three parameters, so a
    score always arrives with what produced it."""
    cut = pipeline.draft_threshold()
    lines = ["<b>Scores</b>  (new angle / emotion named / misconception)", ""]
    for r in rows:
        mark = "→ draft" if r["score"] >= cut else "rejected"
        lines.append(f"<b>{r['score']}/5</b> note {r['id']} — {r['a']}/{r['b']}/{r['c']}"
                     f" — {mark}")
        lines.append(f"    <i>{html.escape(r['reason'])}</i>")
    lines.append("")
    if n_linkedin:
        lines.append(f"Novelty checked against {n_linkedin} published LinkedIn "
                     f"post(s). Newsletters do not count against it.")
    else:
        lines.append("No LinkedIn posts in corpus/ — the new-angle column is a "
                     "guess. Run import_corpus.py.")
    return "\n".join(lines)


def deliver(chat_id, draft_id, version):
    """The draft, its voice check and its sources. Three messages on purpose:
    the post is alone in its own block so it can be copied without the
    commentary attached to it."""
    raw = store.read_step(draft_id, f"03-draft-v{version}") or ""
    post, sources = pipeline.split_post(raw)
    check = store.read_step(draft_id, f"04-voice-check-v{version}") or ""
    verdict, failed = pipeline.parse_verdict(check)

    send(chat_id, f"<b>Draft v{version}</b>  ({pipeline.word_count(post)} words)")
    send(chat_id, code(post))

    gaps = pipeline.placeholders(post)
    tail = []

    # Blockers first, above everything. A dead citation or a date no source
    # carries is fabrication, and she should see it before she reads the post.
    link_report = store.read_step(draft_id, "02a-link-check") or ""
    _, dead, hard_dates, soft_dates = verify.report(post, sources, link_report)
    if dead or hard_dates:
        tail.append("<b>DO NOT POST YET</b>")
        for url, why in dead:
            tail.append(f"  cites a source that does not exist: {html.escape(url[:90])}")
        for phrase, why, _ in hard_dates:
            tail.append(f"  says \"{html.escape(phrase)}\" but {html.escape(why)}")
        tail.append("")
    if soft_dates:
        # Not a blocker. A source that refused an automated request may well
        # be real, and calling that fabrication would be its own error.
        tail.append("<b>Check these yourself</b>")
        for phrase, why, _ in soft_dates:
            tail.append(f"  \"{html.escape(phrase)}\" - {html.escape(why[:150])}")
        tail.append("")

    # Measured, not judged: her own published posts set the range.
    off = style.failures(post)
    if off:
        tail.append("<b>Reads unlike her on:</b>")
        for label, value, low, high, sense in off:
            tail.append(f"  {label}: <b>{value:.1f}</b> vs her {low:.1f}-{high:.1f}"
                        f" ({sense.lower()})")
        tail.append("")
    tail.append(f"<b>Voice check: {verdict}</b>")
    rows = pipeline.failed_checks(check)
    if rows:
        # A bare "failed checks: 6, 9" makes her go and look up what 6 is.
        # Say what each number means, in the checker's own words.
        tail.append(f"{len(rows)} of 17 checks failed:")
        for number, question, evidence in rows:
            tail.append("")
            tail.append(f"<b>{number}.</b> {html.escape(question)}")
            if evidence:
                tail.append(f"    ↳ {html.escape(evidence[:300])}")
    elif failed:
        # The verdict named failures but the table could not be read back.
        tail.append(f"Failed checks: {failed} (could not read the table for "
                    f"what they mean - open 04-voice-check-v{version}.md)")
    if gaps:
        tail.append("")
        tail.append(f"<b>{len(gaps)} figure(s) I will not invent - yours to fill:</b>")
        tail += [html.escape(g) for g in gaps]
    if sources:
        tail += ["", "<b>Sources</b>", html.escape(sources[:1500])]
    tail += ["", "/revise &lt;what to change&gt;   /approve   /post"]
    send(chat_id, "\n".join(tail))


def cmd_draft(chat_id, arg):
    if arg:
        try:
            note_id = int(arg)
        except ValueError:
            send(chat_id, "Which note? /draft 7, or just /draft to rank them all.")
            return
        note = store.get_note(note_id)
        if not note:
            send(chat_id, f"No note {note_id}.")
            return
        send(chat_id, f"Drafting note {note_id}, skipping the ranking.")
        row = None
    else:
        notes = store.open_notes()
        if not notes:
            send(chat_id, "No open notes to score. Send me one first.")
            return
        cut = pipeline.draft_threshold()
        send(chat_id, f"Scoring {len(notes)} note(s) out of 5. Nothing is "
                      f"drafted below {cut}.")
        scoring, n_linkedin = pipeline.score(notes)
        rows = pipeline.parse_scores(scoring, {n["id"] for n in notes})
        if not rows:
            send(chat_id, "I could not read the score table back. Nothing "
                          "drafted. /draft &lt;number&gt; picks one yourself.")
            return

        drafted, rejected = pipeline.decide(rows)
        send(chat_id, score_summary(rows, n_linkedin))

        # Every rejected note gets its own line back, with the reason. A note
        # that scores 2 and is never mentioned again looks like a note that
        # was lost.
        for r in rejected:
            note = store.get_note(r["id"])
            store.set_status(r["id"], store.SKIPPED)
            send(chat_id, f"<b>Note {r['id']} scored {r['score']}/5 - no draft.</b>\n"
                          f"{html.escape(r['reason'])}\n\n"
                          f"New angle: {r['a']} | Emotion named: {r['b']} | "
                          f"Misconception: {r['c']}\n\n"
                          f"<i>{html.escape((note['text'] if note else '')[:200])}</i>\n\n"
                          f"/draft {r['id']} drafts it anyway.")
        if not drafted:
            send(chat_id, f"Nothing scored {pipeline.draft_threshold()} or "
                          f"above. No research call was made, so nothing was "
                          f"spent on drafting.")
            return

        row = drafted[0]
        note = store.get_note(row["id"])
        if len(drafted) > 1:
            others = ", ".join(f"{r['id']} ({r['score']}/5)" for r in drafted[1:])
            send(chat_id, f"Taking note {row['id']} at {row['score']}/5. Also "
                          f"above the line: {others}. /draft &lt;number&gt; for those.")
        else:
            send(chat_id, f"Taking note {row['id']} at {row['score']}/5.")

    draft_id, version = pipeline.run_note(note, progress=lambda m: send(chat_id, m),
                                          score_row=row)
    store.set_current(chat_id, draft_id, version)
    deliver(chat_id, draft_id, version)


def cmd_revise(chat_id, instruction):
    current = store.get_current(chat_id)
    if not current:
        send(chat_id, "No draft open. /draft first.")
        return
    if not instruction.strip():
        send(chat_id, "Tell me what to change: /revise cut the third paragraph")
        return
    version = pipeline.run_revision(current["draft_id"], current["version"],
                                    instruction,
                                    progress=lambda m: send(chat_id, m))
    store.set_current(chat_id, current["draft_id"], version)
    deliver(chat_id, current["draft_id"], version)


def cmd_post(chat_id):
    current = store.get_current(chat_id)
    if not current:
        send(chat_id, "No draft open. /draft first.")
        return
    raw = store.read_step(current["draft_id"], f"03-draft-v{current['version']}") or ""
    post, _ = pipeline.split_post(raw)
    send(chat_id, code(post))


def cmd_approve(chat_id):
    current = store.get_current(chat_id)
    if not current:
        send(chat_id, "No draft open. /draft first.")
        return
    draft_id, version = current["draft_id"], current["version"]
    raw = store.read_step(draft_id, f"03-draft-v{version}") or ""
    post, sources = pipeline.split_post(raw)
    gaps = pipeline.placeholders(post)
    store.save_step(draft_id, "APPROVED", (
        f"# Approved {time.strftime('%Y-%m-%d %H:%M')}\n\n"
        f"From draft v{version}.\n\n## Post\n\n{post}\n\n## Sources\n\n{sources}\n"))
    msg = [f"Saved to data/drafts/{draft_id}/APPROVED.md",
           "", "Paste it into LinkedIn yourself - I do not post."]
    if gaps:
        msg.insert(1, f"Careful: {len(gaps)} [DATA NEEDED] placeholder(s) are "
                      f"still in it.")
    send(chat_id, "\n".join(msg))


# --- dispatch ---------------------------------------------------------------

def download_file(file_id):
    """Telegram keeps the file; getFile gives a path, and the path is fetched
    from a different host to the API itself."""
    info = api("getFile", file_id=file_id)
    path = info["file_path"]
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    url = f"https://api.telegram.org/file/bot{token}/{path}"
    with urllib.request.urlopen(url, timeout=120) as r:
        return r.read()


def voice_note_text(chat_id, message):
    """A voice note or an audio file becomes text, then it is an ordinary
    note. Returns None if there was nothing to transcribe."""
    media = message.get("voice") or message.get("audio")
    mime = media.get("mime_type") or "audio/ogg"
    seconds = media.get("duration", 0)
    send(chat_id, f"Voice note, {seconds}s. Transcribing ...")
    try:
        audio = download_file(media["file_id"])
    except Exception as e:  # noqa: BLE001
        send(chat_id, f"Could not download the audio: {html.escape(str(e))[:200]}")
        return None
    text = pipeline.transcribe(audio, mime)
    if not text or text.strip().upper() == "NO SPEECH":
        send(chat_id, "I could not hear any speech in that.")
        return None
    # She sees the transcript before it is saved, because an ingredient name
    # heard wrong becomes a post built on the wrong ingredient.
    send(chat_id, "<b>Transcript</b>\n" + html.escape(text))
    return text


def handle(message):
    chat_id = message["chat"]["id"]
    user = message.get("from", {})

    if not allowed(user.get("id")):
        send(chat_id, "Not an allowed user.")
        print(f"rejected user {user.get('id')} ({user.get('username')})", flush=True)
        return

    text = (message.get("text") or "").strip()
    if not text and (message.get("voice") or message.get("audio")):
        text = voice_note_text(chat_id, message) or ""
        if not text:
            return
        note = store.add_note(text, chat_id)
        send(chat_id, f"Noted as <b>{note['id']}</b> from the voice note. "
                      f"{len(store.open_notes())} open. Correct it by sending "
                      f"the fixed version as text, then /skip {note['id']}.")
        return
    if not text:
        send(chat_id, "I can read text and voice notes. Not photos or videos.")
        return

    command, _, arg = text.partition(" ")
    command = command.lower().lstrip("/") if text.startswith("/") else ""
    arg = arg.strip()

    if command in ("start", "help"):
        send(chat_id, HELP)
    elif command == "whoami":
        send(chat_id, f"Your Telegram user id is <b>{user.get('id')}</b>. Put it "
                      f"in ALLOWED_USER_IDS in .env and restart me.")
    elif command == "notes":
        cmd_notes(chat_id)
    elif command == "skip":
        cmd_skip(chat_id, arg)
    elif command == "draft":
        cmd_draft(chat_id, arg)
    elif command == "revise":
        cmd_revise(chat_id, arg)
    elif command == "post":
        cmd_post(chat_id)
    elif command == "approve":
        cmd_approve(chat_id)
    elif command:
        send(chat_id, f"I do not know /{html.escape(command)}. /help")
    else:
        note = store.add_note(text, chat_id)
        open_count = len(store.open_notes())
        send(chat_id, f"Noted as <b>{note['id']}</b>. {open_count} open. "
                      f"/draft when you want one written.")


# --- the loop ---------------------------------------------------------------

_busy = set()
_busy_lock = threading.Lock()


def handle_in_thread(message):
    """A run takes minutes. Polling has to keep going, or a note she sends
    meanwhile is answered ten minutes late. One run at a time per chat."""
    chat_id = message["chat"]["id"]
    with _busy_lock:
        if chat_id in _busy:
            send(chat_id, "Still working on the last one. One at a time.")
            return
        _busy.add(chat_id)

    def work():
        try:
            handle(message)
        except ModelError as e:
            send(chat_id, f"The model call failed: {html.escape(str(e))[:500]}")
        except Exception as e:  # noqa: BLE001 - the loop must not die
            traceback.print_exc()
            send(chat_id, f"Something broke: {html.escape(type(e).__name__)}: "
                          f"{html.escape(str(e))[:300]}")
        finally:
            with _busy_lock:
                _busy.discard(chat_id)

    threading.Thread(target=work, daemon=True).start()


def main():
    load_env()
    if not os.environ.get("TELEGRAM_BOT_TOKEN"):
        raise SystemExit("TELEGRAM_BOT_TOKEN is not set. Copy .env.example to "
                         ".env and paste the token from @BotFather.")
    if not os.environ.get("GEMINI_API_KEY"):
        print("! GEMINI_API_KEY is not set - notes will save, /draft will fail.", flush=True)

    me = api("getMe")
    print(f"@{me.get('username')} is listening. Window: "
          f"{pipeline.window_months()} months. Ctrl-C to stop.", flush=True)
    if not (os.environ.get("ALLOWED_USER_IDS") or "").strip():
        print("! ALLOWED_USER_IDS is empty - anyone who finds the bot can use "
              "your API key. Send /whoami and fill it in.", flush=True)

    offset = None
    while True:
        try:
            updates = api("getUpdates", timeout=POLL_TIMEOUT, offset=offset,
                          allowed_updates=["message"])
        except KeyboardInterrupt:
            raise
        except Exception as e:  # noqa: BLE001 - a dropped connection is normal
            print(f"poll failed: {e}", flush=True)
            time.sleep(5)
            continue

        for update in updates or []:
            offset = update["update_id"] + 1
            message = update.get("message")
            if message:
                print(f"< {message.get('from', {}).get('username')}: "
                      f"{(message.get('text') or '')[:80]}", flush=True)
                handle_in_thread(message)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nstopped.", flush=True)
