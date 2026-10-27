"""SQLite-backed storage for team members, notes, tags and screenshots."""

from __future__ import annotations

import os
import re
import shutil
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

MENTION_RE = re.compile(r"(?<![\w@])@([A-Za-z0-9][A-Za-z0-9_.-]*)")
TAG_RE = re.compile(r"(?<![\w#&/])#([A-Za-z][A-Za-z0-9_-]*)")
HANDLE_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")

# (key, label, rich style, marker)
KINDS = [
    ("note", "Note", "", "•"),
    ("win", "Win", "green", "+"),
    ("concern", "Concern", "red", "!"),
    ("feedback", "Feedback given", "cyan", "»"),
    ("goal", "Goal", "magenta", "◎"),
    ("1on1", "1:1", "yellow", "◇"),
]
KIND_LABELS = {key: label for key, label, _, _ in KINDS}
KIND_STYLES = {key: style for key, _, style, _ in KINDS}
KIND_MARKERS = {key: marker for key, _, _, marker in KINDS}

SCHEMA = """
CREATE TABLE IF NOT EXISTS members (
    id INTEGER PRIMARY KEY,
    handle TEXT NOT NULL UNIQUE COLLATE NOCASE,
    name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY,
    body TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'note',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS note_members (
    note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    member_id INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    PRIMARY KEY (note_id, member_id)
);
CREATE TABLE IF NOT EXISTS note_tags (
    note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    tag TEXT NOT NULL COLLATE NOCASE,
    PRIMARY KEY (note_id, tag)
);
CREATE TABLE IF NOT EXISTS attachments (
    id INTEGER PRIMARY KEY,
    note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS notes_created ON notes(created_at);
CREATE INDEX IF NOT EXISTS note_members_member ON note_members(member_id);
"""


def default_root() -> Path:
    if env := os.environ.get("BOSS_HOME"):
        return Path(env).expanduser()
    base = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return Path(base) / "boss"


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def normalize_handle(handle: str) -> str:
    return handle.strip().lstrip("@").rstrip(".-").lower()


def extract_mentions(text: str) -> list[str]:
    seen: dict[str, None] = {}
    for match in MENTION_RE.finditer(text):
        seen[normalize_handle(match.group(1))] = None
    return list(seen)


def extract_tags(text: str) -> list[str]:
    seen: dict[str, None] = {}
    for match in TAG_RE.finditer(text):
        seen[match.group(1).lower()] = None
    return list(seen)


@dataclass
class Member:
    id: int
    handle: str
    name: str
    role: str
    active: bool

    @property
    def label(self) -> str:
        return self.name or self.handle


@dataclass
class Attachment:
    id: int
    note_id: int
    path: Path
    created_at: str


@dataclass
class Note:
    id: int
    body: str
    kind: str
    created_at: str
    updated_at: str
    members: list[Member] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    attachments: list[Attachment] = field(default_factory=list)

    @property
    def created(self) -> datetime:
        return datetime.fromisoformat(self.created_at)

    @property
    def title(self) -> str:
        for line in self.body.splitlines():
            line = line.strip().lstrip("#>*-+ ").strip()
            if line:
                return line
        return "(screenshot)" if self.attachments else "(empty)"

    def matches(self, query: str) -> bool:
        """Every whitespace-separated term must match (``#tag``, ``@handle``, ``kind:win``, or text)."""
        body = self.body.lower()
        for term in query.lower().split():
            if term.startswith("#") and len(term) > 1:
                if not any(tag.startswith(term[1:]) for tag in self.tags):
                    return False
            elif term.startswith("@") and len(term) > 1:
                handle = term[1:]
                if not any(m.handle.startswith(handle) for m in self.members):
                    return False
            elif term.startswith("kind:"):
                if not self.kind.startswith(term[5:]):
                    return False
            elif term not in body:
                return False
        return True


class Store:
    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or default_root()).expanduser()
        if not self.root.exists():
            # performance notes are private: keep the folder readable by you only
            self.root.mkdir(parents=True, mode=0o700)
        self.attachments_dir = self.root / "attachments"
        self.staging_dir = self.root / "staging"
        self.exports_dir = self.root / "exports"
        self.conn = sqlite3.connect(self.root / "boss.db", timeout=10)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def data_version(self) -> int:
        """Changes whenever *another* connection (e.g. ``boss add``) commits."""
        return self.conn.execute("PRAGMA data_version").fetchone()[0]

    # -------------------------------------------------------------- members

    def members(self, include_inactive: bool = False) -> list[Member]:
        sql = "SELECT * FROM members"
        if not include_inactive:
            sql += " WHERE active = 1"
        rows = self.conn.execute(sql + " ORDER BY active DESC, lower(name), handle").fetchall()
        return [self._member(row) for row in rows]

    def member_by_handle(self, handle: str) -> Member | None:
        row = self.conn.execute(
            "SELECT * FROM members WHERE handle = ?", (normalize_handle(handle),)
        ).fetchone()
        return self._member(row) if row else None

    def add_member(self, handle: str, name: str = "", role: str = "") -> Member:
        handle = self._valid_handle(handle)
        try:
            with self.conn:
                cursor = self.conn.execute(
                    "INSERT INTO members (handle, name, role, created_at) VALUES (?, ?, ?, ?)",
                    (handle, name.strip() or handle, role.strip(), now()),
                )
        except sqlite3.IntegrityError:
            raise ValueError(f"@{handle} already exists") from None
        return Member(cursor.lastrowid, handle, name.strip() or handle, role.strip(), True)

    def update_member(self, member_id: int, handle: str, name: str, role: str) -> None:
        handle = self._valid_handle(handle)
        try:
            with self.conn:
                self.conn.execute(
                    "UPDATE members SET handle = ?, name = ?, role = ? WHERE id = ?",
                    (handle, name.strip() or handle, role.strip(), member_id),
                )
        except sqlite3.IntegrityError:
            raise ValueError(f"@{handle} already exists") from None

    def set_member_active(self, member_id: int, active: bool) -> None:
        with self.conn:
            self.conn.execute("UPDATE members SET active = ? WHERE id = ?", (int(active), member_id))

    def note_counts(self) -> dict[int | None, int]:
        """Note count per member id; ``None`` holds the count of unassigned notes."""
        counts: dict[int | None, int] = {
            row[0]: row[1]
            for row in self.conn.execute(
                "SELECT member_id, COUNT(*) FROM note_members GROUP BY member_id"
            )
        }
        counts[None] = self.conn.execute(
            "SELECT COUNT(*) FROM notes WHERE id NOT IN (SELECT note_id FROM note_members)"
        ).fetchone()[0]
        return counts

    @staticmethod
    def _valid_handle(handle: str) -> str:
        handle = normalize_handle(handle)
        if not HANDLE_RE.match(handle):
            raise ValueError("Handles use letters, digits, '.', '_' or '-' (e.g. jsmith)")
        return handle

    @staticmethod
    def _member(row: sqlite3.Row) -> Member:
        return Member(row["id"], row["handle"], row["name"], row["role"], bool(row["active"]))

    # ---------------------------------------------------------------- notes

    def notes(
        self,
        member_id: int | None = None,
        *,
        unassigned: bool = False,
        query: str = "",
        since: str | None = None,
        until: str | None = None,
    ) -> list[Note]:
        where, params = [], []
        if member_id is not None:
            where.append("id IN (SELECT note_id FROM note_members WHERE member_id = ?)")
            params.append(member_id)
        if unassigned:
            where.append("id NOT IN (SELECT note_id FROM note_members)")
        if since:
            where.append("created_at >= ?")
            params.append(since)
        if until:
            where.append("created_at < ?")
            params.append(until)
        sql = "SELECT * FROM notes"
        if where:
            sql += " WHERE " + " AND ".join(where)
        rows = self.conn.execute(sql + " ORDER BY created_at DESC, id DESC", params).fetchall()
        notes = self._hydrate(rows)
        if query.strip():
            notes = [note for note in notes if note.matches(query)]
        return notes

    def note(self, note_id: int) -> Note | None:
        rows = self.conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchall()
        notes = self._hydrate(rows)
        return notes[0] if notes else None

    def save_note(
        self,
        body: str,
        *,
        kind: str = "note",
        handles: list[str] | tuple[str, ...] = (),
        note_id: int | None = None,
    ) -> tuple[Note, list[str]]:
        """Create or update a note. Returns the note and any unknown @handles."""
        wanted = list(dict.fromkeys(normalize_handle(h) for h in [*handles, *extract_mentions(body)] if h.strip("@ ")))
        member_ids, unknown = [], []
        for handle in wanted:
            member = self.member_by_handle(handle)
            if member:
                member_ids.append(member.id)
            else:
                unknown.append(handle)
        kind = kind if kind in KIND_LABELS else "note"
        stamp = now()
        with self.conn:
            if note_id is None:
                cursor = self.conn.execute(
                    "INSERT INTO notes (body, kind, created_at, updated_at) VALUES (?, ?, ?, ?)",
                    (body, kind, stamp, stamp),
                )
                note_id = cursor.lastrowid
            else:
                self.conn.execute(
                    "UPDATE notes SET body = ?, kind = ?, updated_at = ? WHERE id = ?",
                    (body, kind, stamp, note_id),
                )
                self.conn.execute("DELETE FROM note_members WHERE note_id = ?", (note_id,))
                self.conn.execute("DELETE FROM note_tags WHERE note_id = ?", (note_id,))
            self.conn.executemany(
                "INSERT OR IGNORE INTO note_members (note_id, member_id) VALUES (?, ?)",
                [(note_id, member_id) for member_id in member_ids],
            )
            self.conn.executemany(
                "INSERT OR IGNORE INTO note_tags (note_id, tag) VALUES (?, ?)",
                [(note_id, tag) for tag in extract_tags(body)],
            )
        note = self.note(note_id)
        assert note is not None
        return note, unknown

    def delete_note(self, note_id: int) -> None:
        note = self.note(note_id)
        with self.conn:
            self.conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        if note:
            for attachment in note.attachments:
                attachment.path.unlink(missing_ok=True)

    def tags(self) -> list[tuple[str, int]]:
        return [
            (row[0], row[1])
            for row in self.conn.execute(
                "SELECT lower(tag), COUNT(*) FROM note_tags GROUP BY lower(tag) ORDER BY 2 DESC, 1"
            )
        ]

    # ---------------------------------------------------------- attachments

    def new_staging_path(self, suffix: str = ".png") -> Path:
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        return self.staging_dir / f"{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}{suffix}"

    def clean_staging(self, max_age_hours: float = 24) -> None:
        if not self.staging_dir.exists():
            return
        cutoff = datetime.now().timestamp() - max_age_hours * 3600
        for path in self.staging_dir.iterdir():
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)

    def add_attachment(self, note_id: int, source: Path, *, move: bool = False) -> Attachment:
        source = Path(source).expanduser()
        if not source.is_file():
            raise FileNotFoundError(source)
        stamp = datetime.now()
        folder = self.attachments_dir / f"{stamp:%Y}" / f"{stamp:%m}"
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / f"{stamp:%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}{source.suffix.lower() or '.png'}"
        if move:
            shutil.move(str(source), dest)
        else:
            shutil.copy2(source, dest)
        relative = dest.relative_to(self.root).as_posix()
        with self.conn:
            cursor = self.conn.execute(
                "INSERT INTO attachments (note_id, path, created_at) VALUES (?, ?, ?)",
                (note_id, relative, now()),
            )
            self.conn.execute("UPDATE notes SET updated_at = ? WHERE id = ?", (now(), note_id))
        return Attachment(cursor.lastrowid, note_id, dest, now())

    def remove_attachment(self, attachment_id: int) -> None:
        row = self.conn.execute("SELECT path FROM attachments WHERE id = ?", (attachment_id,)).fetchone()
        with self.conn:
            self.conn.execute("DELETE FROM attachments WHERE id = ?", (attachment_id,))
        if row:
            (self.root / row["path"]).unlink(missing_ok=True)

    # -------------------------------------------------------------- helpers

    def _hydrate(self, rows: list[sqlite3.Row]) -> list[Note]:
        if not rows:
            return []
        notes = {
            row["id"]: Note(row["id"], row["body"], row["kind"], row["created_at"], row["updated_at"])
            for row in rows
        }
        marks = ",".join("?" * len(notes))
        ids = list(notes)
        for row in self.conn.execute(
            f"SELECT nm.note_id, m.* FROM note_members nm JOIN members m ON m.id = nm.member_id "
            f"WHERE nm.note_id IN ({marks}) ORDER BY lower(m.name)",
            ids,
        ):
            notes[row["note_id"]].members.append(self._member(row))
        for row in self.conn.execute(
            f"SELECT note_id, tag FROM note_tags WHERE note_id IN ({marks}) ORDER BY tag", ids
        ):
            notes[row["note_id"]].tags.append(row["tag"])
        for row in self.conn.execute(
            f"SELECT * FROM attachments WHERE note_id IN ({marks}) ORDER BY id", ids
        ):
            notes[row["note_id"]].attachments.append(
                Attachment(row["id"], row["note_id"], self.root / row["path"], row["created_at"])
            )
        return list(notes.values())
