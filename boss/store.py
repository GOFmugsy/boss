"""SQLite-backed storage for team members, notes, tags and evidence (screenshots)."""

from __future__ import annotations

import hashlib
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
CREATE TABLE IF NOT EXISTS evidence (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL,             -- relative to the data directory
    caption TEXT NOT NULL DEFAULT '',
    source_name TEXT NOT NULL DEFAULT '',
    source_path TEXT NOT NULL DEFAULT '',
    sha256 TEXT NOT NULL DEFAULT '',
    taken_at TEXT NOT NULL,         -- modification time of the original file
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS note_evidence (
    note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    evidence_id INTEGER NOT NULL REFERENCES evidence(id) ON DELETE CASCADE,
    PRIMARY KEY (note_id, evidence_id)
);
CREATE TABLE IF NOT EXISTS evidence_members (
    evidence_id INTEGER NOT NULL REFERENCES evidence(id) ON DELETE CASCADE,
    member_id INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    PRIMARY KEY (evidence_id, member_id)
);
CREATE TABLE IF NOT EXISTS evidence_tags (
    evidence_id INTEGER NOT NULL REFERENCES evidence(id) ON DELETE CASCADE,
    tag TEXT NOT NULL COLLATE NOCASE,
    PRIMARY KEY (evidence_id, tag)
);
CREATE INDEX IF NOT EXISTS evidence_sha ON evidence(sha256);
CREATE INDEX IF NOT EXISTS note_evidence_evidence ON note_evidence(evidence_id);
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


def _matches(query: str, text: str, tags: list[str], members: list[Member], kind: str = "") -> bool:
    """Every whitespace-separated term must match (``#tag``, ``@handle``, ``kind:win``, or text)."""
    text = text.lower()
    for term in query.lower().split():
        if term.startswith("#") and len(term) > 1:
            if not any(tag.startswith(term[1:]) for tag in tags):
                return False
        elif term.startswith("@") and len(term) > 1:
            if not any(m.handle.startswith(term[1:]) for m in members):
                return False
        elif term.startswith("kind:"):
            if not kind.startswith(term[5:]):
                return False
        elif term not in text:
            return False
    return True


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass
class Evidence:
    """A screenshot (or other image) kept as evidence, linkable to any number of notes."""

    id: int
    path: Path
    caption: str
    source_name: str
    taken_at: str
    created_at: str
    updated_at: str
    members: list[Member] = field(default_factory=list)  # tagged directly on the evidence
    tags: list[str] = field(default_factory=list)
    note_ids: list[int] = field(default_factory=list)
    note_members: list[Member] = field(default_factory=list)  # people on the linked notes

    @property
    def taken(self) -> datetime:
        return datetime.fromisoformat(self.taken_at)

    @property
    def title(self) -> str:
        return self.caption.strip().splitlines()[0] if self.caption.strip() else self.source_name or self.path.name

    @property
    def people(self) -> list[Member]:
        """Everyone the evidence relates to: tagged directly or via a linked note."""
        seen: dict[int, Member] = {m.id: m for m in self.members}
        for member in self.note_members:
            seen.setdefault(member.id, member)
        return list(seen.values())

    def matches(self, query: str) -> bool:
        return _matches(query, f"{self.caption} {self.source_name}", self.tags, self.people)


@dataclass
class Note:
    id: int
    body: str
    kind: str
    created_at: str
    updated_at: str
    members: list[Member] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)

    @property
    def created(self) -> datetime:
        return datetime.fromisoformat(self.created_at)

    @property
    def title(self) -> str:
        for line in self.body.splitlines():
            line = line.strip().lstrip("#>*-+ ").strip()
            if line:
                return line
        return "(evidence)" if self.evidence else "(empty)"

    def matches(self, query: str) -> bool:
        captions = " ".join(e.caption for e in self.evidence)
        return _matches(query, f"{self.body} {captions}", self.tags, self.members, self.kind)


class Store:
    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or default_root()).expanduser()
        if not self.root.exists():
            # performance notes are private: keep the folder readable by you only
            self.root.mkdir(parents=True, mode=0o700)
        self.evidence_dir = self.root / "evidence"
        self.staging_dir = self.root / "staging"
        self.exports_dir = self.root / "exports"
        self.conn = sqlite3.connect(self.root / "boss.db", timeout=10)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        """Version 1 stored per-note ``attachments``; turn them into linked evidence."""
        has_attachments = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'attachments'"
        ).fetchone()
        if not has_attachments:
            return
        with self.conn:
            for row in self.conn.execute("SELECT * FROM attachments ORDER BY id").fetchall():
                path = self.root / row["path"]
                sha = file_sha256(path) if path.is_file() else ""
                cursor = self.conn.execute(
                    "INSERT INTO evidence (path, source_name, sha256, taken_at, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (row["path"], path.name, sha, row["created_at"], row["created_at"], row["created_at"]),
                )
                self.conn.execute(
                    "INSERT OR IGNORE INTO note_evidence (note_id, evidence_id) VALUES (?, ?)",
                    (row["note_id"], cursor.lastrowid),
                )
            self.conn.execute("DROP TABLE attachments")

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
        member_ids, unknown = self._resolve_handles([*handles, *extract_mentions(body)])
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
        """Delete a note. Its evidence stays in the library (just unlinked)."""
        with self.conn:
            self.conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))

    def tags(self) -> list[tuple[str, int]]:
        return [
            (row[0], row[1])
            for row in self.conn.execute(
                "SELECT lower(tag), COUNT(*) FROM note_tags GROUP BY lower(tag) ORDER BY 2 DESC, 1"
            )
        ]

    # ------------------------------------------------------------- evidence

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

    def evidence_by_source(self, source: Path) -> Evidence | None:
        """Evidence previously imported from this exact file (path), if any."""
        row = self.conn.execute(
            "SELECT id FROM evidence WHERE source_path = ? ORDER BY id DESC LIMIT 1", (str(source),)
        ).fetchone()
        return self.evidence(row["id"]) if row else None

    def import_evidence(
        self,
        source: Path,
        *,
        caption: str = "",
        handles: list[str] | tuple[str, ...] = (),
        move: bool = False,
    ) -> tuple[Evidence, list[str], bool]:
        """Copy an image into the evidence library.

        Returns ``(evidence, unknown_handles, created)``. Importing a file whose contents are
        already in the library returns the existing evidence (``created`` is False), adding
        any new caption/people to it.
        """
        source = Path(source).expanduser()
        if not source.is_file():
            raise FileNotFoundError(source)
        sha = file_sha256(source)
        existing = self.conn.execute("SELECT id FROM evidence WHERE sha256 = ?", (sha,)).fetchone()
        if existing:
            if move:
                source.unlink(missing_ok=True)
            evidence = self.evidence(existing["id"])
            assert evidence is not None
            unknown: list[str] = []
            if caption.strip() or handles:
                merged_caption = evidence.caption or caption
                if caption.strip() and evidence.caption and caption.strip() not in evidence.caption:
                    merged_caption = f"{evidence.caption}\n{caption.strip()}"
                merged = [m.handle for m in evidence.members] + list(handles)
                evidence, unknown = self.update_evidence(evidence.id, caption=merged_caption, handles=merged)
            return evidence, unknown, False

        taken = datetime.fromtimestamp(source.stat().st_mtime).astimezone()
        folder = self.evidence_dir / f"{taken:%Y}" / f"{taken:%m}"
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / f"{taken:%Y%m%d-%H%M%S}-{sha[:8]}{source.suffix.lower() or '.png'}"
        if move:
            shutil.move(str(source), dest)
        else:
            shutil.copy2(source, dest)
        stamp = now()
        source_path = "" if move else str(source.resolve())
        with self.conn:
            cursor = self.conn.execute(
                "INSERT INTO evidence (path, source_name, source_path, sha256, taken_at, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (dest.relative_to(self.root).as_posix(), "" if move else source.name, source_path, sha,
                 taken.isoformat(timespec="seconds"), stamp, stamp),
            )
        evidence, unknown = self.update_evidence(cursor.lastrowid, caption=caption, handles=handles, touch=False)
        return evidence, unknown, True

    def update_evidence(
        self,
        evidence_id: int,
        *,
        caption: str,
        handles: list[str] | tuple[str, ...] = (),
        touch: bool = True,
    ) -> tuple[Evidence, list[str]]:
        """Set the caption and tagged people; ``@handle``/``#tag`` in the caption count too."""
        member_ids, unknown = self._resolve_handles([*handles, *extract_mentions(caption)])
        with self.conn:
            if touch:
                self.conn.execute(
                    "UPDATE evidence SET caption = ?, updated_at = ? WHERE id = ?", (caption.strip(), now(), evidence_id)
                )
            else:
                self.conn.execute("UPDATE evidence SET caption = ? WHERE id = ?", (caption.strip(), evidence_id))
            self.conn.execute("DELETE FROM evidence_members WHERE evidence_id = ?", (evidence_id,))
            self.conn.execute("DELETE FROM evidence_tags WHERE evidence_id = ?", (evidence_id,))
            self.conn.executemany(
                "INSERT OR IGNORE INTO evidence_members (evidence_id, member_id) VALUES (?, ?)",
                [(evidence_id, member_id) for member_id in member_ids],
            )
            self.conn.executemany(
                "INSERT OR IGNORE INTO evidence_tags (evidence_id, tag) VALUES (?, ?)",
                [(evidence_id, tag) for tag in extract_tags(caption)],
            )
        evidence = self.evidence(evidence_id)
        assert evidence is not None
        return evidence, unknown

    def link_evidence(self, note_id: int, evidence_id: int) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO note_evidence (note_id, evidence_id) VALUES (?, ?)", (note_id, evidence_id)
            )

    def unlink_evidence(self, note_id: int, evidence_id: int) -> None:
        with self.conn:
            self.conn.execute(
                "DELETE FROM note_evidence WHERE note_id = ? AND evidence_id = ?", (note_id, evidence_id)
            )

    def delete_evidence(self, evidence_id: int) -> None:
        """Remove evidence from the library (and every note it was linked to) and delete its file."""
        evidence = self.evidence(evidence_id)
        with self.conn:
            self.conn.execute("DELETE FROM evidence WHERE id = ?", (evidence_id,))
        if evidence:
            evidence.path.unlink(missing_ok=True)

    def evidence(self, evidence_id: int) -> Evidence | None:
        rows = self.conn.execute("SELECT * FROM evidence WHERE id = ?", (evidence_id,)).fetchall()
        items = self._hydrate_evidence(rows)
        return items[0] if items else None

    def evidence_list(
        self,
        member_id: int | None = None,
        *,
        unlinked: bool = False,
        query: str = "",
        since: str | None = None,
        until: str | None = None,
    ) -> list[Evidence]:
        """Evidence, newest first. ``member_id`` matches people tagged directly or via a linked note."""
        where, params = [], []
        if member_id is not None:
            where.append(
                "(id IN (SELECT evidence_id FROM evidence_members WHERE member_id = ?) OR id IN "
                "(SELECT ne.evidence_id FROM note_evidence ne JOIN note_members nm ON nm.note_id = ne.note_id "
                "WHERE nm.member_id = ?))"
            )
            params += [member_id, member_id]
        if unlinked:
            where.append("id NOT IN (SELECT evidence_id FROM note_evidence)")
        if since:
            where.append("taken_at >= ?")
            params.append(since)
        if until:
            where.append("taken_at < ?")
            params.append(until)
        sql = "SELECT * FROM evidence" + (" WHERE " + " AND ".join(where) if where else "")
        items = self._hydrate_evidence(self.conn.execute(sql + " ORDER BY taken_at DESC, id DESC", params).fetchall())
        return [item for item in items if item.matches(query)] if query.strip() else items

    def evidence_counts(self) -> tuple[int, int]:
        """(total evidence, evidence not linked to any note)."""
        total = self.conn.execute("SELECT COUNT(*) FROM evidence").fetchone()[0]
        unlinked = self.conn.execute(
            "SELECT COUNT(*) FROM evidence WHERE id NOT IN (SELECT evidence_id FROM note_evidence)"
        ).fetchone()[0]
        return total, unlinked

    # -------------------------------------------------------------- helpers

    def _resolve_handles(self, handles) -> tuple[list[int], list[str]]:
        wanted = list(dict.fromkeys(normalize_handle(h) for h in handles if h.strip("@ ")))
        member_ids, unknown = [], []
        for handle in wanted:
            member = self.member_by_handle(handle)
            if member:
                member_ids.append(member.id)
            else:
                unknown.append(handle)
        return member_ids, unknown

    def _members_for(self, table: str, key: str, ids: list[int]) -> dict[int, list[Member]]:
        result: dict[int, list[Member]] = {}
        if not ids:
            return result
        marks = ",".join("?" * len(ids))
        for row in self.conn.execute(
            f"SELECT t.{key} AS owner, m.* FROM {table} t JOIN members m ON m.id = t.member_id "
            f"WHERE t.{key} IN ({marks}) ORDER BY lower(m.name)",
            ids,
        ):
            result.setdefault(row["owner"], []).append(self._member(row))
        return result

    def _hydrate_evidence(self, rows: list[sqlite3.Row]) -> list[Evidence]:
        if not rows:
            return []
        items = {
            row["id"]: Evidence(
                row["id"], self.root / row["path"], row["caption"], row["source_name"],
                row["taken_at"], row["created_at"], row["updated_at"],
            )
            for row in rows
        }
        ids = list(items)
        marks = ",".join("?" * len(ids))
        for owner, members in self._members_for("evidence_members", "evidence_id", ids).items():
            items[owner].members = members
        for row in self.conn.execute(
            f"SELECT evidence_id, tag FROM evidence_tags WHERE evidence_id IN ({marks}) ORDER BY tag", ids
        ):
            items[row["evidence_id"]].tags.append(row["tag"])
        links = self.conn.execute(
            f"SELECT evidence_id, note_id FROM note_evidence WHERE evidence_id IN ({marks}) ORDER BY note_id", ids
        ).fetchall()
        note_people = self._members_for("note_members", "note_id", list({row["note_id"] for row in links}))
        for row in links:
            item = items[row["evidence_id"]]
            item.note_ids.append(row["note_id"])
            for member in note_people.get(row["note_id"], []):
                if all(m.id != member.id for m in item.note_members):
                    item.note_members.append(member)
        return list(items.values())

    def _hydrate(self, rows: list[sqlite3.Row]) -> list[Note]:
        if not rows:
            return []
        notes = {
            row["id"]: Note(row["id"], row["body"], row["kind"], row["created_at"], row["updated_at"])
            for row in rows
        }
        ids = list(notes)
        marks = ",".join("?" * len(ids))
        for owner, members in self._members_for("note_members", "note_id", ids).items():
            notes[owner].members = members
        for row in self.conn.execute(
            f"SELECT note_id, tag FROM note_tags WHERE note_id IN ({marks}) ORDER BY tag", ids
        ):
            notes[row["note_id"]].tags.append(row["tag"])
        links = self.conn.execute(
            f"SELECT note_id, evidence_id FROM note_evidence WHERE note_id IN ({marks})", ids
        ).fetchall()
        if links:
            evidence_ids = list({row["evidence_id"] for row in links})
            evidence_marks = ",".join("?" * len(evidence_ids))
            by_id = {
                e.id: e
                for e in self._hydrate_evidence(
                    self.conn.execute(f"SELECT * FROM evidence WHERE id IN ({evidence_marks})", evidence_ids).fetchall()
                )
            }
            for row in links:
                notes[row["note_id"]].evidence.append(by_id[row["evidence_id"]])
            for note in notes.values():
                note.evidence.sort(key=lambda e: (e.taken_at, e.id))
        return list(notes.values())
