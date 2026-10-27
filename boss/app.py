"""The boss Textual application."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Input, Markdown, OptionList, Static
from textual.widgets.data_table import ColumnKey
from textual.widgets.option_list import Option

from .capture import CaptureError, capture_region, open_path, paste_image
from .export import notes_to_markdown
from .screens import ConfirmScreen, HelpScreen, MemberForm, NoteEditor
from .store import KIND_LABELS, KIND_MARKERS, KIND_STYLES, Member, Note, Store

# textual-image must be imported before the app starts so it can probe the terminal.
ImageWidget = None
if not os.environ.get("BOSS_NO_IMAGES"):
    try:
        from textual_image.widget import Image as ImageWidget  # type: ignore[no-redef]
    except Exception:  # optional dependency
        ImageWidget = None

ALL, UNASSIGNED = "all", "unassigned"
MAX_PREVIEWS = 3


def when(note: Note) -> str:
    created = note.created
    today = datetime.now(created.tzinfo).date()
    if created.date() == today:
        return f"Today {created:%H:%M}"
    if created.year == today.year:
        return f"{created:%b %d %H:%M}"
    return f"{created:%Y-%m-%d}"


def kind_text(kind: str) -> Text:
    return Text(f"{KIND_MARKERS.get(kind, '•')} {KIND_LABELS.get(kind, kind)}", style=KIND_STYLES.get(kind, ""))


class NotesTable(DataTable):
    """A DataTable whose Note column takes the remaining width instead of scrolling sideways."""

    def fit_note_column(self) -> None:
        column = self.columns.get(ColumnKey("note"))
        if column is None or not self.size.width:
            return
        fixed = sum(c.width for key, c in self.columns.items() if key.value != "note")
        padding = 2 * self.cell_padding * len(self.columns)
        width = max(20, self.scrollable_content_region.width - fixed - padding - 1)
        if column.width != width or column.auto_width:
            column.auto_width = False
            column.width = width
            self._require_update_dimensions = True
            self.refresh()

    def on_resize(self) -> None:
        self.call_after_refresh(self.fit_note_column)


class MainScreen(Screen):
    BINDINGS = [
        Binding("n", "new_note", "New note"),
        Binding("s", "screenshot_note", "Screenshot"),
        Binding("p", "paste_note", "Paste image"),
        Binding("e", "edit_note", "Edit"),
        Binding("d", "delete_note", "Delete"),
        Binding("o", "open_attachments", "Open image"),
        Binding("slash", "search", "Filter"),
        Binding("a", "add_member", "Add person"),
        Binding("w", "export", "Export"),
        Binding("question_mark", "help", "Help"),
        Binding("r", "edit_member", "Edit person", show=False),
        Binding("A", "toggle_archive", "Archive person", show=False),
        Binding("H", "toggle_show_archived", "Show archived", show=False),
        Binding("j", "move(1)", show=False),
        Binding("k", "move(-1)", show=False),
        Binding("g", "jump(False)", show=False),
        Binding("G", "jump(True)", show=False),
        Binding("h", "focus_pane('team')", show=False),
        Binding("l", "focus_pane('notes')", show=False),
        Binding("escape", "clear_search", show=False),
    ]

    def __init__(self, store: Store) -> None:
        super().__init__()
        self.store = store
        self.view = ALL
        self.query_text = ""
        self.show_archived = False
        self._members: dict[int, Member] = {}
        self._notes: dict[int, Note] = {}
        self._shown_note: tuple[int, str] | None = None
        self._version = store.data_version()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="layout"):
            with Vertical(id="sidebar"):
                yield OptionList(id="team")
            with Vertical(id="main"):
                yield Input(placeholder="filter: text  #tag  @handle  kind:win", id="search")
                yield NotesTable(id="notes", cursor_type="row", zebra_stripes=True)
                with VerticalScroll(id="detail"):
                    yield Static(id="detail-meta")
                    yield Markdown(id="detail-body")
                    yield Vertical(id="detail-images")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#team", OptionList).border_title = "Team"
        table = self.query_one("#notes", DataTable)
        table.border_title = "Notes"
        table.add_column("When", key="when", width=12)
        table.add_column("Kind", key="kind", width=10)
        table.add_column("Who", key="who", width=14)
        table.add_column("Note", key="note")
        table.add_column("▣", key="att", width=2)
        self.query_one("#search", Input).display = False
        self.query_one("#detail", VerticalScroll).border_title = "Detail"
        self.reload()
        table.focus()
        self.set_interval(2.0, self._check_external_changes)

    # -------------------------------------------------------------- loading

    def _check_external_changes(self) -> None:
        """Pick up notes added by ``boss add`` / ``boss quick`` from another process."""
        version = self.store.data_version()
        if version != self._version:
            self._version = version
            self.reload()

    def reload(self, select_note: int | None = None) -> None:
        self._refresh_team()
        self._refresh_notes(select_note)

    def _refresh_team(self) -> None:
        team = self.query_one("#team", OptionList)
        members = self.store.members(include_inactive=self.show_archived)
        self._members = {m.id: m for m in members}
        counts = self.store.note_counts()
        if self.view.startswith("m:") and int(self.view[2:]) not in self._members:
            self.view = ALL
        total = sum(1 for _ in self.store.conn.execute("SELECT 1 FROM notes"))
        options: list[Option | None] = [
            Option(Text.assemble("All notes ", (str(total), "dim")), id=ALL),
            Option(Text.assemble("Unassigned ", (str(counts.get(None, 0)), "dim")), id=UNASSIGNED),
            None,
        ]
        for member in members:
            label = Text(member.label, style="" if member.active else "dim strike")
            label.append(f" {counts.get(member.id, 0)}", style="dim")
            label.append(f"\n@{member.handle}" + (f" · {member.role}" if member.role else ""), style="dim")
            options.append(Option(label, id=f"m:{member.id}"))
        if not members:
            options.append(Option(Text("Press a to add\nyour first person", style="dim italic"), disabled=True))
        with team.prevent(OptionList.OptionHighlighted):
            team.clear_options()
            team.add_options(options)
            try:
                team.highlighted = team.get_option_index(self.view)
            except Exception:
                team.highlighted = 0
        people = len([m for m in members if m.active])
        self.app.sub_title = f"{people} {'person' if people == 1 else 'people'} · {total} notes"

    def _view_notes(self) -> list[Note]:
        if self.view == UNASSIGNED:
            return self.store.notes(unassigned=True, query=self.query_text)
        if self.view.startswith("m:"):
            return self.store.notes(int(self.view[2:]), query=self.query_text)
        return self.store.notes(query=self.query_text)

    def _view_title(self) -> str:
        if self.view.startswith("m:"):
            member = self._members.get(int(self.view[2:]))
            return member.label if member else "Notes"
        return "Unassigned notes" if self.view == UNASSIGNED else "All notes"

    def _refresh_notes(self, select_note: int | None = None) -> None:
        table = self.query_one("#notes", DataTable)
        if select_note is None:
            current = self.current_note()
            select_note = current.id if current else None
            previous_row = table.cursor_row
        else:
            previous_row = 0
        notes = self._view_notes()
        self._notes = {note.id: note for note in notes}
        table.clear()
        for note in notes:
            who = " ".join(f"@{m.handle}" for m in note.members) or "—"
            title = note.title
            tags = " ".join(f"#{t}" for t in note.tags)
            row_title = Text(title, no_wrap=True, overflow="ellipsis")
            if tags and tags not in title:
                row_title.append(f"  {tags}", style="dim")
            table.add_row(
                when(note),
                kind_text(note.kind),
                Text(who, overflow="ellipsis", no_wrap=True),
                row_title,
                Text(str(len(note.attachments)), style="cyan") if note.attachments else "",
                key=str(note.id),
            )
        table.fit_note_column()
        title = self._view_title()
        if self.query_text:
            title += f" · filter: {self.query_text}"
        table.border_title = f"{title} ({len(notes)})"
        if notes:
            ids = list(self._notes)
            row = ids.index(select_note) if select_note in self._notes else min(previous_row, len(ids) - 1)
            table.move_cursor(row=max(0, row))
        self._show_detail(self.current_note())

    def current_note(self) -> Note | None:
        table = self.query_one("#notes", DataTable)
        if not table.row_count:
            return None
        try:
            key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
        except Exception:
            return None
        return self._notes.get(int(key.value)) if key.value else None

    def current_member(self) -> Member | None:
        if self.view.startswith("m:"):
            return self._members.get(int(self.view[2:]))
        return None

    def _show_detail(self, note: Note | None) -> None:
        signature = (note.id, note.updated_at) if note else None
        if signature == self._shown_note:
            return
        self._shown_note = signature
        meta = self.query_one("#detail-meta", Static)
        body = self.query_one("#detail-body", Markdown)
        images = self.query_one("#detail-images", Vertical)
        images.remove_children()
        if note is None:
            empty = "No notes match this filter." if self.query_text else "No notes yet — press [b]n[/] to write one."
            meta.update(empty)
            body.update("")
            return
        people = ", ".join(f"{m.label} (@{m.handle})" for m in note.members) or "unassigned"
        line = Text.assemble(kind_text(note.kind), "  ", (f"{note.created:%A %B %d %Y, %H:%M}", "bold"), "  ", people)
        if note.tags:
            line.append("  " + " ".join(f"#{t}" for t in note.tags), style="cyan")
        if note.updated_at != note.created_at:
            line.append(f"\nedited {datetime.fromisoformat(note.updated_at):%b %d %H:%M}", style="dim")
        meta.update(line)
        body.update(note.body)
        widgets = []
        for attachment in note.attachments[:MAX_PREVIEWS]:
            caption = Static(Text.assemble(("▣ ", "cyan"), attachment.path.name, ("  (o to open)", "dim")), classes="caption")
            widgets.append(caption)
            if ImageWidget is not None and attachment.path.exists():
                try:
                    widgets.append(ImageWidget(attachment.path, classes="preview"))
                except Exception:
                    pass
            elif not attachment.path.exists():
                widgets.append(Static(Text("missing file", style="red")))
        if len(note.attachments) > MAX_PREVIEWS:
            widgets.append(Static(Text(f"+{len(note.attachments) - MAX_PREVIEWS} more", style="dim")))
        if widgets:
            images.mount_all(widgets)

    # --------------------------------------------------------------- events

    @on(OptionList.OptionHighlighted, "#team")
    def team_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if event.option.id and event.option.id != self.view:
            self.view = event.option.id
            self._refresh_notes(select_note=-1)

    @on(OptionList.OptionSelected, "#team")
    def team_selected(self) -> None:
        self.query_one("#notes").focus()

    @on(DataTable.RowHighlighted, "#notes")
    def row_highlighted(self) -> None:
        self._show_detail(self.current_note())

    @on(DataTable.RowSelected, "#notes")
    def row_selected(self) -> None:
        self.action_edit_note()

    @on(Input.Changed, "#search")
    def search_changed(self, event: Input.Changed) -> None:
        self.query_text = event.value.strip()
        self._refresh_notes()

    @on(Input.Submitted, "#search")
    def search_submitted(self) -> None:
        self.query_one("#notes").focus()

    # -------------------------------------------------------------- actions

    def action_move(self, delta: int) -> None:
        widget = self.focused
        if isinstance(widget, (DataTable, OptionList)):
            widget.action_cursor_down() if delta > 0 else widget.action_cursor_up()
        elif isinstance(widget, VerticalScroll):
            widget.scroll_relative(y=delta * 3)

    def action_jump(self, end: bool) -> None:
        widget = self.focused
        if isinstance(widget, DataTable) and widget.row_count:
            widget.move_cursor(row=widget.row_count - 1 if end else 0)
        elif isinstance(widget, OptionList):
            widget.action_last() if end else widget.action_first()

    def action_focus_pane(self, pane: str) -> None:
        self.query_one("#team" if pane == "team" else "#notes").focus()

    def action_search(self) -> None:
        search = self.query_one("#search", Input)
        search.display = True
        search.focus()

    def action_clear_search(self) -> None:
        search = self.query_one("#search", Input)
        if search.value or search.display:
            search.value = ""
            search.display = False
            self.query_one("#notes").focus()

    def action_help(self) -> None:
        self.app.push_screen(HelpScreen())

    # notes

    def _open_editor(self, note: Note | None = None, staged: list[tuple[Path, bool]] | None = None) -> None:
        member = self.current_member()
        handles = [member.handle] if member and note is None else []
        editor = NoteEditor(self.store, note, handles=handles, staged=staged)
        self.app.push_screen(editor, self._editor_closed)

    def _editor_closed(self, note_id: int | None) -> None:
        """The editor saves notes itself (``:w``); here we just show the result."""
        if note_id is None:
            return
        note = self.store.note(note_id)
        if note is None:
            return
        if self.view.startswith("m:") and int(self.view[2:]) not in {m.id for m in note.members}:
            self.view = ALL
        self._shown_note = None
        self.reload(select_note=note_id)

    def action_new_note(self) -> None:
        self._open_editor()

    def action_edit_note(self) -> None:
        note = self.current_note()
        if note:
            self._open_editor(self.store.note(note.id))

    def action_delete_note(self) -> None:
        note = self.current_note()
        if not note:
            return

        def confirmed(yes: bool | None) -> None:
            if yes:
                self.store.delete_note(note.id)
                self.reload()
                self.notify("Note deleted", timeout=2)

        detail = note.title[:60] + (f" (+{len(note.attachments)} screenshots)" if note.attachments else "")
        self.app.push_screen(ConfirmScreen("Delete this note?", detail), confirmed)

    @work(exclusive=True, group="capture")
    async def action_screenshot_note(self) -> None:
        self.notify("Select a region of the screen…", timeout=3)
        try:
            path = await capture_region(self.store.new_staging_path())
        except CaptureError as error:
            self.notify(str(error), severity="error", timeout=8)
            return
        if path is None:
            self.notify("Screenshot cancelled", severity="warning")
            return
        self._open_editor(staged=[(path, True)])

    @work(exclusive=True, group="capture")
    async def action_paste_note(self) -> None:
        try:
            path = await paste_image(self.store.new_staging_path())
        except CaptureError as error:
            self.notify(str(error), severity="error", timeout=8)
            return
        if path is None:
            self.notify("No image on the clipboard", severity="warning")
            return
        self._open_editor(staged=[(path, True)])

    def action_open_attachments(self) -> None:
        note = self.current_note()
        if not note or not note.attachments:
            self.notify("This note has no screenshots", severity="warning")
            return
        for attachment in note.attachments[:6]:
            open_path(attachment.path)

    def action_export(self) -> None:
        notes = self._view_notes()
        if not notes:
            self.notify("Nothing to export", severity="warning")
            return
        member = self.current_member()
        slug = member.handle if member else self.view
        self.store.exports_dir.mkdir(parents=True, exist_ok=True)
        path = self.store.exports_dir / f"{slug}-{datetime.now():%Y%m%d-%H%M}.md"
        title = f"Notes: {self._view_title()}" + (f" (filter: {self.query_text})" if self.query_text else "")
        path.write_text(notes_to_markdown(title, notes, path.parent))
        self.notify(f"Exported {len(notes)} notes to {path}", timeout=10)

    # team

    def action_add_member(self) -> None:
        added: list[Member] = []

        def submit(name: str, handle: str, role: str) -> str | None:
            try:
                added.append(self.store.add_member(handle, name, role))
            except ValueError as error:
                return str(error)
            return None

        def done(ok: bool | None) -> None:
            if ok and added:
                self.view = f"m:{added[0].id}"
                self.reload(select_note=-1)
                self.notify(f"Added @{added[0].handle}", timeout=2)

        self.app.push_screen(MemberForm("Add team member", submit), done)

    def action_edit_member(self) -> None:
        member = self.current_member()
        if not member:
            self.notify("Highlight a person in the team list first", severity="warning")
            return

        def submit(name: str, handle: str, role: str) -> str | None:
            try:
                self.store.update_member(member.id, handle, name, role)
            except ValueError as error:
                return str(error)
            return None

        form = MemberForm("Edit team member", submit, name=member.name, handle=member.handle, role=member.role)
        self.app.push_screen(form, lambda ok: self.reload() if ok else None)

    def action_toggle_archive(self) -> None:
        member = self.current_member()
        if not member:
            self.notify("Highlight a person in the team list first", severity="warning")
            return
        if not member.active:
            self.store.set_member_active(member.id, True)
            self.reload()
            self.notify(f"Restored @{member.handle}")
            return

        def confirmed(yes: bool | None) -> None:
            if yes:
                self.store.set_member_active(member.id, False)
                self.reload()
                self.notify(f"Archived @{member.handle} (H shows archived people)")

        self.app.push_screen(
            ConfirmScreen(f"Archive {member.label}?", "They leave the team list; their notes are kept."),
            confirmed,
        )

    def action_toggle_show_archived(self) -> None:
        self.show_archived = not self.show_archived
        self.reload()
        self.notify("Showing archived people" if self.show_archived else "Hiding archived people", timeout=2)


class BossApp(App):
    TITLE = "boss"
    ENABLE_COMMAND_PALETTE = False
    CSS_PATH = "boss.tcss"
    BINDINGS = [Binding("ctrl+q", "quit", "Quit", priority=True)]

    def __init__(self, store: Store) -> None:
        super().__init__()
        self.store = store

    def on_mount(self) -> None:
        self.store.clean_staging()

    def get_default_screen(self) -> Screen:
        return MainScreen(self.store)
