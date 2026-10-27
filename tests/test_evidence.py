import os
import sqlite3
from pathlib import Path

import pytest
from PIL import Image

from boss.cli import main
from boss.desktop import parse_paths, recent_images
from boss.export import notes_to_markdown
from boss.store import Store


def png(path: Path, color=(200, 30, 30)) -> Path:
    Image.new("RGB", (32, 16), color).save(path)
    return path


@pytest.fixture
def store(tmp_path) -> Store:
    store = Store(tmp_path / "data")
    store.add_member("alice", "Alice Smith")
    store.add_member("bob", "Bob Jones")
    return store


def test_import_copies_dedupes_and_tags(store, tmp_path):
    source = png(tmp_path / "Screenshot 2026-10-27 at 9.41.03 AM.png")
    evidence, unknown, created = store.import_evidence(source, caption="@alice chart #metrics", handles=["@zed"])
    assert created and unknown == ["zed"] and source.exists()
    assert evidence.path.is_relative_to(store.root / "evidence") and evidence.path.exists()
    assert [m.handle for m in evidence.members] == ["alice"] and evidence.tags == ["metrics"]
    assert evidence.source_name == source.name

    copy = tmp_path / "renamed.png"
    copy.write_bytes(source.read_bytes())
    again, _, created = store.import_evidence(copy, caption="second look", handles=["bob"])
    assert not created and again.id == evidence.id
    assert "second look" in again.caption and {m.handle for m in again.members} == {"alice", "bob"}
    assert store.evidence_counts() == (1, 1)


def test_clipboard_style_import_moves_the_temp_file(store):
    temp = png(store.new_staging_path())
    evidence, _, _ = store.import_evidence(temp, move=True)
    assert not temp.exists() and evidence.path.exists()


def test_link_unlink_and_people_via_notes(store, tmp_path):
    evidence, _, _ = store.import_evidence(png(tmp_path / "a.png"))
    note1, _ = store.save_note("@alice demo")
    note2, _ = store.save_note("@bob follow-up")
    store.link_evidence(note1.id, evidence.id)
    store.link_evidence(note2.id, evidence.id)
    store.link_evidence(note2.id, evidence.id)  # idempotent
    evidence = store.evidence(evidence.id)
    assert evidence.note_ids == [note1.id, note2.id]
    assert {m.handle for m in evidence.people} == {"alice", "bob"} and evidence.members == []
    bob = store.member_by_handle("bob")
    assert [e.id for e in store.evidence_list(bob.id)] == [evidence.id]
    assert [e.id for e in store.note(note1.id).evidence] == [evidence.id]
    store.unlink_evidence(note1.id, evidence.id)
    assert store.evidence(evidence.id).note_ids == [note2.id]
    assert store.evidence_list(unlinked=True) == []


def test_deleting_notes_keeps_evidence_deleting_evidence_unlinks(store, tmp_path):
    evidence, _, _ = store.import_evidence(png(tmp_path / "a.png"))
    note, _ = store.save_note("x")
    store.link_evidence(note.id, evidence.id)
    store.delete_note(note.id)
    assert store.evidence(evidence.id).note_ids == [] and evidence.path.exists()
    note, _ = store.save_note("y")
    store.link_evidence(note.id, evidence.id)
    store.delete_evidence(evidence.id)
    assert store.note(note.id).evidence == [] and not evidence.path.exists()


def test_filters_search_captions(store, tmp_path):
    evidence, _, _ = store.import_evidence(png(tmp_path / "a.png"), caption="latency dashboard #perf")
    note, _ = store.save_note("@bob shipped it")
    store.link_evidence(note.id, evidence.id)
    assert [e.id for e in store.evidence_list(query="#perf @bob")] == [evidence.id]
    assert [n.id for n in store.notes(query="latency")] == [note.id]  # captions are searchable from notes


def test_migrates_v1_attachments(tmp_path):
    root = tmp_path / "old"
    (root / "attachments/2026/10").mkdir(parents=True)
    png(root / "attachments/2026/10/old.png")
    db = sqlite3.connect(root / "boss.db")
    db.executescript("""
        CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'note',
                            created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE attachments (id INTEGER PRIMARY KEY, note_id INTEGER NOT NULL, path TEXT NOT NULL,
                                  created_at TEXT NOT NULL);
        INSERT INTO notes VALUES (1, 'old note', 'win', '2026-10-01T10:00:00-07:00', '2026-10-01T10:00:00-07:00');
        INSERT INTO attachments VALUES (1, 1, 'attachments/2026/10/old.png', '2026-10-01T10:00:00-07:00');
    """)
    db.commit()
    db.close()
    store = Store(root)
    [evidence] = store.note(1).evidence
    assert evidence.path.exists() and evidence.note_ids == [1]
    assert not store.conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'attachments'").fetchone()
    Store(root)  # idempotent


def test_export_lists_other_evidence(store, tmp_path):
    evidence, _, _ = store.import_evidence(png(tmp_path / "a.png"), caption="unlinked proof")
    text = notes_to_markdown("Notes: Alice", [], tmp_path, extra_evidence=[evidence])
    assert "## Other evidence" in text and "unlinked proof" in text and "data/evidence/" in text


# ------------------------------------------------------------------- desktop


def test_parse_dragged_paths(tmp_path):
    spaced = png(tmp_path / "My Shot 1.png")
    other = png(tmp_path / "b.png")
    escaped = str(spaced).replace(" ", "\\ ")
    assert parse_paths(f"{escaped} {other}") == [spaced, other]  # Terminal.app / iTerm2 drag-and-drop
    assert parse_paths(f"'{spaced}'") == [spaced]
    assert parse_paths(str(spaced)) == [spaced]  # pasted without escaping
    assert parse_paths(f"file://{other}") == [other]
    assert parse_paths("  ") == []


def test_recent_images_newest_first(tmp_path):
    old = png(tmp_path / "old.png")
    new = png(tmp_path / "new.jpg")
    os.utime(new, (new.stat().st_atime, new.stat().st_mtime + 100))
    os.utime(old, (old.stat().st_atime, old.stat().st_mtime - 90 * 86400))  # too old
    (tmp_path / ".hidden.png").write_bytes(new.read_bytes())
    (tmp_path / "doc.pdf").write_text("x")
    assert recent_images([tmp_path]) == [new]


# ----------------------------------------------------------------------- CLI


def test_cli_evidence_workflow(tmp_path, monkeypatch, capsys):
    root = tmp_path / "data"
    monkeypatch.setenv("BOSS_HOME", str(root))
    folder = tmp_path / "Desktop"
    folder.mkdir()
    monkeypatch.setenv("BOSS_SCREENSHOTS_DIR", str(folder))
    Store(root).add_member("alice", "Alice")
    shot = png(folder / "Screenshot 1.png")

    assert main(["add", "@alice", "nailed", "the", "demo", "--latest"]) == 0
    note = Store(root).notes()[0]
    assert [e.source_name for e in note.evidence] == ["Screenshot 1.png"]

    other = png(tmp_path / "chart.png", (0, 0, 255))
    assert main(["evidence", "add", str(other), "-c", "Q3 chart", "-m", "alice", "-t", "q3"]) == 0
    capsys.readouterr()
    assert main(["evidence", "list", "--unlinked"]) == 0
    listing = capsys.readouterr().out
    assert "Q3 chart #q3" in listing and "unlinked" in listing
    evidence_id = int(listing.split()[0].lstrip("#"))

    assert main(["evidence", "link", str(evidence_id), str(note.id)]) == 0
    assert main(["evidence", "edit", str(evidence_id), "-c", "Q3 revenue chart"]) == 0
    store = Store(root)
    assert sorted(e.id for e in store.note(note.id).evidence) == sorted([evidence_id, note.evidence[0].id])
    assert store.evidence(evidence_id).caption == "Q3 revenue chart"
    assert main(["add", "follow-up", "-E", str(evidence_id)]) == 0
    assert evidence_id in [e.id for e in Store(root).notes()[0].evidence]
    assert shot.exists()

    capsys.readouterr()
    assert main(["export", "alice"]) == 0
    assert "Q3 revenue chart" in capsys.readouterr().out


def test_cli_has_no_screenshot_capture(capsys):
    with pytest.raises(SystemExit):
        main(["add", "--shot", "x"])
    assert "unrecognized arguments: --shot" in capsys.readouterr().err
