from pathlib import Path

import pytest

from boss.app import BossApp, MainScreen
from boss.screens import ConfirmScreen, NoteEditor
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
        await pilot.press("h", "j", "j")  # All -> Unassigned -> Alice
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
        assert screen.query_one("#notes").row_count == 2
        await pilot.press("slash")
        await type_text(pilot, "#deliv")
        await pilot.pause()
        assert screen.query_one("#notes").row_count == 1
        await pilot.press("enter", "escape")
        await pilot.pause()
        assert screen.query_one("#notes").row_count == 2

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
        assert app.screen.query_one("#notes").row_count == 0
        other = Store(store.root)  # e.g. `boss add` from another terminal
        other.save_note("@bob quick one")
        other.close()
        await pilot.pause(2.5)
        assert app.screen.query_one("#notes").row_count == 1


async def test_attachments_and_export(store, tmp_path):
    image = tmp_path / "shot.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    note, _ = store.save_note("@alice whiteboard session", kind="1on1")
    attachment = store.add_attachment(note.id, image)
    assert attachment.path.exists() and attachment.path.is_relative_to(store.root)
    app = BossApp(store)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("w")
        await pilot.pause()
        [export] = list(store.exports_dir.glob("*.md"))
        text = export.read_text()
        assert "whiteboard session" in text and "../attachments/" in text
        await pilot.press("d", "y")
        await pilot.pause()
        assert not attachment.path.exists()


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
