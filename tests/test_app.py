import os
from pathlib import Path

import pytest

from boss.app import BossApp, MainScreen
from boss.screens import (
    ConfirmScreen, EvidenceForm, EvidencePicker, NoteEditor, NotePicker, ScreenshotPicker,
)
from boss.store import Store
from boss.vim import Mode, VimTextArea

KEYS = {" ": "space", "@": "at", "#": "number_sign", ":": "colon", "!": "exclamation_mark"}


async def type_text(pilot, text: str) -> None:
    for ch in text:
        await pilot.press(KEYS.get(ch, ch))


@pytest.fixture
def store(tmp_path: Path) -> Store:
    store = Store(tmp_path / "data")
    store.add_member("alice", "Alice Smith", "Senior Engineer")
    store.add_member("bob", "Bob Jones")
    return store


async def test_new_note_from_member_view_is_tagged(store):
    app = BossApp(store)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("h", "j", "j", "j", "j")  # All, Unassigned, Evidence, Unlinked, Alice
        await pilot.pause()
        assert app.screen.current_member().handle == "alice"
        await pilot.press("n")
        await pilot.pause()
        assert isinstance(app.screen, NoteEditor)
        body = app.screen.query_one(VimTextArea)
        assert body.mode is Mode.INSERT
        await type_text(pilot, "great demo #demo")
        await pilot.press("escape")
        await type_text(pilot, ":wq")
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, MainScreen)
        [note] = store.notes()
        assert note.body == "great demo #demo"
        assert [m.handle for m in note.members] == ["alice"]
        assert note.tags == ["demo"]


async def ex(pilot, command: str) -> None:
    await pilot.press("escape")
    await type_text(pilot, ":" + command)
    await pilot.press("enter")
    await pilot.pause()


async def test_vim_write_keeps_editing_and_quit_rules(store):
    app = BossApp(store)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("n")
        await type_text(pilot, "first draft")
        editor = app.screen

        await ex(pilot, "q")  # unsaved changes: refuse like Vim's E37
        assert app.screen is editor
        assert "E37" in str(editor.body.border_subtitle)

        await ex(pilot, "w")  # writes, stays open
        assert app.screen is editor and not editor.dirty
        [note] = store.notes()
        assert note.body == "first draft"
        assert "written" in str(editor.body.border_subtitle)

        await pilot.press("A")
        await type_text(pilot, " v2")
        await ex(pilot, "w")  # second :w updates the same note
        assert [n.body for n in store.notes()] == ["first draft v2"]

        await ex(pilot, "q")  # nothing unsaved: closes
        assert isinstance(app.screen, MainScreen)
        assert app.screen.current_note().id == note.id


async def test_vim_quit_bang_discards_and_x_writes(store):
    note, _ = store.save_note("original")
    app = BossApp(store)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("e")
        await pilot.press("A")
        await type_text(pilot, " changed")
        await ex(pilot, "q!")
        assert isinstance(app.screen, MainScreen)
        assert store.note(note.id).body == "original"
        updated = store.note(note.id).updated_at

        await pilot.press("e")
        await ex(pilot, "x")  # unmodified: closes without writing
        assert isinstance(app.screen, MainScreen)
        assert store.note(note.id).updated_at == updated

        await pilot.press("e")
        await pilot.press("A")
        await type_text(pilot, "!")
        await pilot.press("escape", "Z", "Z")
        await pilot.pause()
        assert isinstance(app.screen, MainScreen)
        assert store.note(note.id).body == "original!"


async def test_vim_revert_and_write_file(store, tmp_path):
    app = BossApp(store)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("n")
        await type_text(pilot, "keep me")
        await ex(pilot, "w")
        await pilot.press("A")
        await type_text(pilot, " junk")
        await ex(pilot, "e!")
        editor = app.screen
        assert editor.body.text == "keep me" and not editor.dirty

        target = tmp_path / "out.md"
        await ex(pilot, f"w {target}")
        assert target.read_text() == "keep me\n"
        await ex(pilot, f"w {target}")  # exists: needs !
        assert "E13" in str(editor.body.border_subtitle)
        await ex(pilot, f"w! {target}")
        assert "written" in str(editor.body.border_subtitle)

        await ex(pilot, "wri")  # abbreviations work
        await ex(pilot, "frobnicate")
        assert "E492" in str(editor.body.border_subtitle)
        await ex(pilot, "wq")
        assert isinstance(app.screen, MainScreen)


async def test_empty_note_is_not_written(store):
    app = BossApp(store)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("n")
        await ex(pilot, "w")
        assert "E32" in str(app.screen.body.border_subtitle)
        await ex(pilot, "q")
        assert isinstance(app.screen, MainScreen)
        assert store.notes() == []


async def test_ctrl_c_leaves_insert_mode_then_explains(store):
    app = BossApp(store)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("n")
        await type_text(pilot, "draft")
        editor = app.screen
        await pilot.press("ctrl+c")
        assert editor.body.mode is Mode.NORMAL and app.screen is editor
        await pilot.press("ctrl+c")
        assert ":q" in str(editor.body.border_subtitle) and app.screen is editor
        # from another field ctrl+c cancels, confirming first because the note is unsaved
        await pilot.press("shift+tab", "shift+tab")
        await pilot.press("ctrl+c")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.press("y")
        await pilot.pause()
        assert isinstance(app.screen, MainScreen) and store.notes() == []


async def test_ctrl_s_saves_and_closes(store):
    app = BossApp(store)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("n")
        await type_text(pilot, "quick one")
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert isinstance(app.screen, MainScreen)
        assert [n.body for n in store.notes()] == ["quick one"]


async def test_edit_delete_and_filter(store):
    store.save_note("@alice shipped it #delivery", kind="win")
    store.save_note("@bob late again #reliability", kind="concern")
    app = BossApp(store)
    async with app.run_test(size=(120, 40)) as pilot:
        screen = app.screen
        assert screen.query_one("#items").row_count == 2
        await pilot.press("slash")
        await type_text(pilot, "#deliv")
        await pilot.pause()
        assert screen.query_one("#items").row_count == 1
        await pilot.press("enter", "escape")
        await pilot.pause()
        assert screen.query_one("#items").row_count == 2

        # edit the first (newest) note: append text in normal mode
        await pilot.press("g", "e")
        await pilot.pause()
        assert isinstance(app.screen, NoteEditor)
        await pilot.press("A")
        await type_text(pilot, " oops")
        await pilot.press("escape", "ctrl+s")
        await pilot.pause()
        assert store.notes()[0].body == "@bob late again #reliability oops"

        await pilot.press("g", "d", "y")
        await pilot.pause()
        assert [n.body for n in store.notes()] == ["@alice shipped it #delivery"]


async def test_external_notes_appear_live(store, tmp_path):
    app = BossApp(store)
    async with app.run_test(size=(120, 40)) as pilot:
        assert app.screen.query_one("#items").row_count == 0
        other = Store(store.root)  # e.g. `boss add` from another terminal
        other.save_note("@bob quick one")
        other.close()
        await pilot.pause(2.5)
        assert app.screen.query_one("#items").row_count == 1


def make_png(path: Path, color=(200, 30, 30)) -> Path:
    from PIL import Image

    Image.new("RGB", (32, 16), color).save(path)
    return path


@pytest.fixture
def shots(tmp_path, monkeypatch) -> Path:
    """A fake macOS screenshots folder with two screenshots."""
    folder = tmp_path / "Desktop"
    folder.mkdir()
    make_png(folder / "Screenshot 2026-10-27 at 9.41.03\u202fAM.png", (10, 200, 10))
    newest = make_png(folder / "Screenshot 2026-10-27 at 9.45.00\u202fAM.png", (10, 10, 200))
    os.utime(newest, (newest.stat().st_atime, newest.stat().st_mtime + 60))
    (folder / "notes.txt").write_text("not an image")
    monkeypatch.setenv("BOSS_SCREENSHOTS_DIR", str(folder))
    return folder


async def test_export_includes_evidence_and_delete_keeps_it(store, tmp_path):
    note, _ = store.save_note("@alice whiteboard session", kind="1on1")
    evidence, _, _ = store.import_evidence(make_png(tmp_path / "board.png"), caption="the whiteboard")
    store.link_evidence(note.id, evidence.id)
    app = BossApp(store)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("w")
        await pilot.pause()
        [export] = list(store.exports_dir.glob("*.md"))
        text = export.read_text()
        assert "whiteboard session" in text and "../evidence/" in text and "the whiteboard" in text
        await pilot.press("d", "y")
        await pilot.pause()
        assert store.notes() == []
        assert evidence.path.exists() and store.evidence_counts() == (1, 1)  # kept, now unlinked


async def test_screenshots_folder_into_new_note(store, shots):
    app = BossApp(store)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("s")
        await pilot.pause()
        assert isinstance(app.screen, ScreenshotPicker)
        assert len(app.screen.items) == 2  # the .txt is ignored; newest first
        assert "9.45.00" in app.screen.items[0][0].name
        await pilot.press("space", "down", "space", "enter")
        await pilot.pause()
        editor = app.screen
        assert isinstance(editor, NoteEditor) and len(editor._new) == 2
        await type_text(pilot, "@alice demo screenshots")
        await ex(pilot, "wq")
        [note] = store.notes()
        assert len(note.evidence) == 2
        assert all(e.path.is_relative_to(store.root) for e in note.evidence)
        assert all(p.exists() for p in shots.glob("*.png"))  # originals untouched


async def test_editor_discard_and_unlink_evidence(store, tmp_path):
    note, _ = store.save_note("@bob fixed prod")
    evidence, _, _ = store.import_evidence(make_png(tmp_path / "graph.png"), caption="error graph")
    store.link_evidence(note.id, evidence.id)
    app = BossApp(store)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("e")
        await pilot.pause()
        editor = app.screen
        editor.stage_files([make_png(tmp_path / "extra.png", (1, 2, 3))])
        assert editor.dirty
        await ex(pilot, "q")
        assert app.screen is editor  # E37
        await ex(pilot, "q!")
        assert len(store.note(note.id).evidence) == 1

        await pilot.press("e")
        await pilot.pause()
        editor = app.screen
        editor.query_one("#evidence").focus()
        await pilot.press("x")
        assert editor.dirty
        editor.body.focus()
        await ex(pilot, "wq")
        assert store.note(note.id).evidence == []
        assert store.evidence(evidence.id) is not None  # still in the library


async def test_evidence_view_import_caption_and_link(store, shots):
    note, _ = store.save_note("@alice led the incident review", kind="win")
    app = BossApp(store)
    async with app.run_test(size=(120, 40)) as pilot:
        screen = app.screen
        await pilot.press("h", "j", "j", "j", "j", "l")  # Alice, back to the list
        await pilot.press("v")  # Alice's evidence
        await pilot.pause()
        assert screen.mode == "evidence" and screen.query_one("#items").row_count == 0

        await pilot.press("s")
        await pilot.pause()
        await pilot.press("enter")  # newest screenshot
        await pilot.pause()
        [evidence] = store.evidence_list()
        assert [m.handle for m in evidence.members] == ["alice"]  # tagged from the view
        assert screen.query_one("#items").row_count == 1

        await pilot.press("e")
        await pilot.pause()
        assert isinstance(app.screen, EvidenceForm)
        await type_text(pilot, "timeline she drew #incident")
        await pilot.press("enter")
        await pilot.pause()
        evidence = store.evidence(evidence.id)
        assert evidence.caption == "timeline she drew #incident" and evidence.tags == ["incident"]

        await pilot.press("L")
        await pilot.pause()
        assert isinstance(app.screen, NotePicker)
        await type_text(pilot, "incident")
        await pilot.press("enter")
        await pilot.pause()
        assert store.evidence(evidence.id).note_ids == [note.id]

        # importing the same screenshot again is recognised
        await pilot.press("s")
        await pilot.pause()
        assert "imported" in str(app.screen.items[0][1])
        await pilot.press("enter")
        await pilot.pause()
        assert store.evidence_counts() == (1, 0)


async def test_link_library_evidence_to_note_from_notes_view(store, tmp_path):
    note, _ = store.save_note("@bob great design doc")
    for i in range(2):
        store.import_evidence(make_png(tmp_path / f"doc{i}.png", (i, 50, 50)), caption=f"page {i}")
    app = BossApp(store)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("L")
        await pilot.pause()
        assert isinstance(app.screen, EvidencePicker)
        await pilot.press("space", "down", "space", "enter")
        await pilot.pause()
        assert len(store.note(note.id).evidence) == 2
        assert app.screen.query_one("#items").get_row_at(0)[-1].plain == "2"


async def test_new_note_from_evidence(store, tmp_path):
    evidence, _, _ = store.import_evidence(make_png(tmp_path / "x.png"), caption="@bob dashboard")
    app = BossApp(store)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("v", "n")
        await pilot.pause()
        editor = app.screen
        assert isinstance(editor, NoteEditor) and editor._link[0].id == evidence.id
        assert "@bob" in editor.query_one("#who").value
        await type_text(pilot, "built the dashboard")
        await ex(pilot, "wq")
        [note] = store.notes()
        assert [e.id for e in note.evidence] == [evidence.id] and [m.handle for m in note.members] == ["bob"]


async def test_add_member_form(store):
    app = BossApp(store)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("a")
        await type_text(pilot, "Carol Danvers")
        await pilot.press("enter", "enter")  # handle auto-filled as "carol"
        await type_text(pilot, "EM")
        await pilot.press("enter")
        await pilot.pause()
        carol = store.member_by_handle("carol")
        assert carol and carol.name == "Carol Danvers" and carol.role == "EM"
        assert app.screen.view == f"m:{carol.id}"


def test_store_mentions_and_unknown(store):
    note, unknown = store.save_note("Pairing: @alice and @Bob. cc @zed #pairing #Mentoring", handles=["@alice"])
    assert sorted(m.handle for m in note.members) == ["alice", "bob"]
    assert unknown == ["zed"]
    assert note.tags == ["mentoring", "pairing"]
    assert store.notes(query="@bo #pair")[0].id == note.id
    assert store.notes(query="kind:win") == []
    assert store.note_counts()[None] == 0
