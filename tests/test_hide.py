"""The special #hide tag keeps notes in boss but out of summaries (exports)."""

from pathlib import Path

import pytest
from PIL import Image

from boss.app import BossApp
from boss.cli import main
from boss.export import for_summary, notes_to_markdown
from boss.store import Store


def png(path: Path, color=(200, 30, 30)) -> Path:
    Image.new("RGB", (32, 16), color).save(path)
    return path


@pytest.fixture
def store(tmp_path) -> Store:
    store = Store(tmp_path / "data")
    store.add_member("alice", "Alice Smith")
    return store


@pytest.fixture
def scenario(store, tmp_path):
    """A visible and a hidden note for Alice, with evidence linked in every combination."""
    visible, _ = store.save_note("@alice shipped the migration #delivery", kind="win")
    hidden, _ = store.save_note("@alice is dealing with a family matter #HIDE", kind="1on1")
    only_hidden, _, _ = store.import_evidence(png(tmp_path / "a.png", (1, 0, 0)), caption="private doctor note")
    shared, _, _ = store.import_evidence(png(tmp_path / "b.png", (2, 0, 0)), caption="migration dashboard")
    tagged, _, _ = store.import_evidence(png(tmp_path / "c.png", (3, 0, 0)), caption="draft chart #hide")
    loose, _, _ = store.import_evidence(png(tmp_path / "d.png", (4, 0, 0)), caption="@alice demo recording")
    store.link_evidence(hidden.id, only_hidden.id)
    store.link_evidence(hidden.id, shared.id)
    store.link_evidence(visible.id, shared.id)
    store.link_evidence(visible.id, tagged.id)
    return store, visible, hidden


def test_hidden_flags(scenario):
    store, visible, hidden = scenario
    assert store.note(hidden.id).hidden and not store.note(visible.id).hidden  # case-insensitive
    by_caption = {e.caption: e for e in store.evidence_list()}
    assert by_caption["private doctor note"].hidden_from_summaries
    assert not by_caption["migration dashboard"].hidden_from_summaries  # also supports a visible note
    assert by_caption["draft chart #hide"].hidden and by_caption["draft chart #hide"].hidden_from_summaries
    assert not by_caption["@alice demo recording"].hidden_from_summaries
    # still listed everywhere in boss itself, and findable with the tag
    assert len(store.notes()) == 2
    assert [n.id for n in store.notes(query="#hide")] == [hidden.id]


def test_export_leaves_hidden_out(scenario, tmp_path):
    store, visible, hidden = scenario
    alice = store.member_by_handle("alice")
    notes = store.notes(alice.id)
    linked = {e.id for n in notes for e in n.evidence}
    extra = [e for e in store.evidence_list(alice.id) if e.id not in linked]
    text = notes_to_markdown("Notes: Alice", notes, tmp_path, extra_evidence=extra)
    assert "shipped the migration" in text and "migration dashboard" in text and "demo recording" in text
    for secret in ("family matter", "doctor note", "draft chart", "· 1:1", "1:1: 1"):
        assert secret not in text
    assert "· 1 notes ·" in text  # summary counts ignore hidden notes

    everything = notes_to_markdown("Notes: Alice", notes, tmp_path, extra_evidence=extra, include_hidden=True)
    assert "family matter" in everything and "draft chart" in everything


def test_evidence_of_hidden_note_does_not_leak_into_other_evidence(scenario):
    store, visible, hidden = scenario
    alice = store.member_by_handle("alice")
    # e.g. a date filter excluded the hidden note, so its evidence would be "other evidence"
    extra = [e for e in store.evidence_list(alice.id)]
    text = notes_to_markdown("x", [], extra_evidence=extra)
    assert "doctor note" not in text and "draft chart" not in text and "demo recording" in text


def test_for_summary_does_not_modify_the_notes(scenario):
    store, visible, _ = scenario
    notes = store.notes()
    for_summary(notes)
    assert len([n for n in notes if n.id == visible.id][0].evidence) == 2


def test_cli_export_and_list(scenario, tmp_path, monkeypatch, capsys):
    store, _, _ = scenario
    monkeypatch.setenv("BOSS_HOME", str(store.root))
    assert main(["export", "alice"]) == 0
    out, err = capsys.readouterr()
    assert "family matter" not in out and "1 note(s) tagged #hide left out" in err
    assert main(["export", "alice", "--include-hidden"]) == 0
    out, err = capsys.readouterr()
    assert "family matter" in out and err == ""
    assert main(["list"]) == 0
    assert "[hidden]" in capsys.readouterr().out


async def test_tui_marks_hidden_and_export_skips_it(scenario):
    store, _, hidden = scenario
    app = BossApp(store)
    async with app.run_test(size=(120, 40)) as pilot:
        table = app.screen.query_one("#items")
        titles = [table.get_row_at(i)[3].plain for i in range(table.row_count)]
        assert any(t.startswith("⊘ ") and "family matter" in t for t in titles)
        table.move_cursor(row=[i for i, t in enumerate(titles) if t.startswith("⊘")][0])
        await pilot.pause()
        assert "hidden from summaries" in str(app.screen.query_one("#detail-meta").render())
        await pilot.press("w")
        await pilot.pause()
        [export] = list(store.exports_dir.glob("*.md"))
        assert "family matter" not in export.read_text()
