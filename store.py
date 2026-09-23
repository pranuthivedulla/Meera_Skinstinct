"""Notes and drafts on disk. No database.

data/notes.json      every note she has sent, with its status
data/state.json      which draft each chat is currently working on
data/drafts/<id>/    one folder per draft, every step written as it completes:
                     note.md, 02-research.md, 02a-link-check.md,
                     03-draft-v1.md, 04-voice-check-v1.md, ... , APPROVED.md

Everything is a readable file on purpose. When a draft comes out wrong, the
answer to "which step went wrong" has to be openable without running anything.
"""

import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DRAFTS = DATA / "drafts"
NOTES_FILE = DATA / "notes.json"
STATE_FILE = DATA / "state.json"

# A note's status. NEW is fair game for ranking; DRAFTED and SKIPPED are not
# offered again unless she asks for them by number.
NEW, DRAFTED, SKIPPED = "NEW", "DRAFTED", "SKIPPED"


def _read(path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


# --- notes ------------------------------------------------------------------

def load_notes():
    return _read(NOTES_FILE, [])


def add_note(text, chat_id):
    notes = load_notes()
    note = {
        "id": (max((n["id"] for n in notes), default=0) + 1),
        "text": text.strip(),
        "chat_id": chat_id,
        "added": time.strftime("%Y-%m-%d %H:%M"),
        "status": NEW,
    }
    notes.append(note)
    _write(NOTES_FILE, notes)
    return note


def get_note(note_id):
    return next((n for n in load_notes() if n["id"] == note_id), None)


def set_status(note_id, status):
    notes = load_notes()
    for n in notes:
        if n["id"] == note_id:
            n["status"] = status
    _write(NOTES_FILE, notes)


def open_notes():
    return [n for n in load_notes() if n["status"] == NEW]


# --- drafts -----------------------------------------------------------------

def draft_dir(draft_id):
    d = DRAFTS / str(draft_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def new_draft(note):
    draft_id = time.strftime("%Y%m%d-%H%M%S") + f"-note{note['id']}"
    d = draft_dir(draft_id)
    (d / "note.md").write_text(
        f"# Note {note['id']}\n\nAdded {note['added']}\n\n{note['text']}\n",
        encoding="utf-8")
    return draft_id


def save_step(draft_id, name, text):
    (draft_dir(draft_id) / f"{name}.md").write_text(text, encoding="utf-8")


def read_step(draft_id, name):
    p = draft_dir(draft_id) / f"{name}.md"
    return p.read_text(encoding="utf-8") if p.exists() else None


def next_version(draft_id):
    existing = list(draft_dir(draft_id).glob("03-draft-v*.md"))
    return len(existing) + 1


# --- per-chat state ---------------------------------------------------------

def load_state():
    return _read(STATE_FILE, {})


def set_current(chat_id, draft_id, version):
    state = load_state()
    state[str(chat_id)] = {"draft_id": draft_id, "version": version}
    _write(STATE_FILE, state)


def get_current(chat_id):
    return load_state().get(str(chat_id))
