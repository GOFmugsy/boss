"""The boss Textual application."""

from __future__ import annotations

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

from .desktop import DesktopError, open_path, parse_paths, paste_image
from .export import notes_to_markdown
from .preview import image_preview  # imported early so textual-image can probe the terminal
from .screens import (
    ConfirmScreen, EvidenceForm, EvidencePicker, HelpScreen, MemberForm, NoteEditor, NotePicker,
    PromptScreen, ScreenshotPicker, StagedEvidence, check_images, short_when,
)
from .store import KIND_LABELS, KIND_MARKERS, KIND_STYLES, Evidence, Member, Note, Store

ALL, UNASSIGNED, EVIDENCE, UNLINKED = "all", "unassigned", "evidence", "unlinked"
NOTES_MODE, EVIDENCE_MODE = "notes", "evidence"
MAX_PREVIEWS = 3


def when(note: Note) -> str:
    return short_when(note.created)


def kind_text(kind: str) -> Text:
    return Text(f"{KIND_MARKERS.get(kind, '•')} {KIND_LABELS.get(kind, kind)}", style=KIND_STYLES.get(kind, ""))


def handles_text(members: list[Member]) -> Text:
    return Text(" ".join(f"@{m.handle}" for m in members) or "—", overflow="ellipsis", no_wrap=True)


class ItemsTable(DataTable):
    """A DataTable whose ``main`` column takes the remaining width instead of scrolling sideways."""

    def fit_main_column(self) -> None:
        column = self.columns.get(ColumnKey("main"))
        if column is None or not self.size.width:
            return
        fixed = sum(c.width for key, c in self.columns.items() if key.value != "main")
        padding = 2 * self.cell_padding * len(self.columns)
        width = max(20, self.scrollable_content_region.width - fixed - padding - 1)
        if column.width != width or column.auto_width:
            column.auto_width = False
            column.width = width
            self._require_update_dimensions = True
            self.refresh()

    def on_resize(self) -> None:
        self.call_after_refresh(self.fit_main_column)


class MainScreen(Screen):
    BINDINGS = [
        Binding("n", "new_note", "New note"),
        Binding("s", "screenshots", "Screenshots"),
        Binding("i", "import_files", "Import"),
        Binding("L", "link", "Link"),
        Binding("v", "toggle_mode", "Notes/Evidence"),
        Binding("e", "edit", "Edit"),
        Binding("d", "delete", "Delete"),
        Binding("o", "open_evidence", "Open"),
        Binding("slash", "search", "Filter"),
        Binding("question_mark", "help", "Help"),
        Binding("p", "paste", "Paste image", show=False),
        Binding("a", "add_member", "Add person", show=False),
        Binding("w", "export", "Export", show=False),
        Binding("r", "edit_member", "Edit person", show=False),
        Binding("A", "toggle_archive", "Archive person", show=False),
        Binding("H", "toggle_show_archived", "Show archived", show=False),
        Binding("j", "move(1)", show=False),
        Binding("k", "move(-1)", show=False),
        Binding("g", "jump(False)", show=False),
        Binding("G", "jump(True)", show=False),
        Binding("h", "focus_pane('team')", show=False),
        Binding("l", "focus_pane('items')", show=False),
        Binding("escape", "clear_search", show=False),
    ]

    def __init__(self, store: Store) -> None:
        super().__init__()
        self.store = store
        self.view = ALL
        self.mode = NOTES_MODE
        self.query_text = ""
        self.show_archived = False
        self._members: dict[int, Member] = {}
        self._notes: dict[int, Note] = {}
        self._evidence: dict[int, Evidence] = {}
        self._columns_mode: str | None = None
        self._shown: tuple | None = None
        self._version = store.data_version()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="layout"):
            with Vertical(id="sidebar"):
                yield OptionList(id="team")
            with Vertical(id="main"):
                yield Input(placeholder="filter: text  #tag  @handle  kind:win", id="search")
                yield ItemsTable(id="items", cursor_type="row", zebra_stripes=True)
                with VerticalScroll(id="detail"):
                    yield Static(id="detail-meta")
                    yield Markdown(id="detail-body")
                    yield Vertical(id="detail-images")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#team", OptionList).border_title = "Team"
        self.query_one("#search", Input).display = False
        self.query_one("#detail", VerticalScroll).border_title = "Detail"
        self.reload()
        self.query_one("#items").focus()
        self.set_interval(2.0, self._check_external_changes)

    # -------------------------------------------------------------- loading

    def _check_external_changes(self) -> None:
        """Pick up notes/evidence added by ``boss add`` etc. from another process."""
        version = self.store.data_version()
        if version != self._version:
            self._version = version
            self.reload()

    def reload(self, select: int | None = None) -> None:
        self._refresh_team()
        self._refresh_items(select)

    def _refresh_team(self) -> None:
        team = self.query_one("#team", OptionList)
        members = self.store.members(include_inactive=self.show_archived)
        self._members = {m.id: m for m in members}
        counts = self.store.note_counts()
        if self.view.startswith("m:") and int(self.view[2:]) not in self._members:
            self.view = ALL
        total = self.store.conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
        evidence_total, unlinked = self.store.evidence_counts()
        options: list[Option | None] = [
            Option(Text.assemble("All notes ", (str(total), "dim")), id=ALL),
            Option(Text.assemble("Unassigned ", (str(counts.get(None, 0)), "dim")), id=UNASSIGNED),
            Option(Text.assemble("Evidence ", (str(evidence_total), "dim")), id=EVIDENCE),
            Option(Text.assemble("  Unlinked ", (str(unlinked), "yellow" if unlinked else "dim")), id=UNLINKED),
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
        self.app.sub_title = (
            f"{people} {'person' if people == 1 else 'people'} · {total} notes · {evidence_total} evidence"
        )

    def _member_id(self) -> int | None:
        return int(self.view[2:]) if self.view.startswith("m:") else None

    def _view_notes(self) -> list[Note]:
        return self.store.notes(self._member_id(), unassigned=self.view == UNASSIGNED, query=self.query_text)

    def _view_evidence(self) -> list[Evidence]:
        return self.store.evidence_list(self._member_id(), unlinked=self.view == UNLINKED, query=self.query_text)

    def _view_title(self) -> str:
        if self.view.startswith("m:"):
            member = self._members.get(int(self.view[2:]))
            name = member.label if member else "Notes"
            return f"{name} · evidence" if self.mode == EVIDENCE_MODE else name
        return {
            ALL: "All notes", UNASSIGNED: "Unassigned notes", EVIDENCE: "Evidence", UNLINKED: "Unlinked evidence",
        }[self.view]

    def _set_columns(self, table: ItemsTable) -> None:
        if self._columns_mode == self.mode:
            return
        self._columns_mode = self.mode
        table.clear(columns=True)
        if self.mode == NOTES_MODE:
            table.add_column("When", key="when", width=12)
            table.add_column("Kind", key="kind", width=10)
            table.add_column("Who", key="who", width=14)
            table.add_column("Note", key="main")
            table.add_column("▣", key="count", width=2)
        else:
            table.add_column("Taken", key="when", width=12)
            table.add_column("Who", key="who", width=14)
            table.add_column("Evidence", key="main")
            table.add_column("Notes", key="count", width=5)

    def _refresh_items(self, select: int | None = None) -> None:
        table = self.query_one("#items", ItemsTable)
        previous_row = table.cursor_row
        if select is None:
            current = self.current_item()
            select = current.id if current else None
        mode_changed = self._columns_mode != self.mode
        self._set_columns(table)
        table.clear()
        if self.mode == NOTES_MODE:
            notes = self._view_notes()
            self._notes = {note.id: note for note in notes}
            for note in notes:
                title = Text(note.title, no_wrap=True, overflow="ellipsis")
                tags = " ".join(f"#{t}" for t in note.tags)
                if tags and tags not in note.title:
                    title.append(f"  {tags}", style="dim")
                count = Text(str(len(note.evidence)), style="cyan") if note.evidence else ""
                table.add_row(when(note), kind_text(note.kind), handles_text(note.members), title, count, key=str(note.id))
            ids = list(self._notes)
        else:
            items = self._view_evidence()
            self._evidence = {e.id: e for e in items}
            for e in items:
                title = Text(e.title, no_wrap=True, overflow="ellipsis")
                if not e.caption:
                    title.stylize("dim")
                count = Text(str(len(e.note_ids)), style="cyan") if e.note_ids else Text("—", style="yellow")
                table.add_row(short_when(e.taken), handles_text(e.people), title, count, key=str(e.id))
            ids = list(self._evidence)
        table.fit_main_column()
        title = self._view_title() + (f" · filter: {self.query_text}" if self.query_text else "")
        table.border_title = f"{title} ({len(ids)})"
        if ids:
            if select in ids:
                row = ids.index(select)
            else:
                row = 0 if mode_changed or select == -1 else min(previous_row, len(ids) - 1)
            table.move_cursor(row=max(0, row))
        self._show_detail()

    def current_item(self) -> Note | Evidence | None:
        table = self.query_one("#items", ItemsTable)
        if not table.row_count:
            return None
        try:
            key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
        except Exception:
            return None
        if not key.value:
            return None
        source = self._notes if self.mode == NOTES_MODE else self._evidence
        return source.get(int(key.value))

    def current_note(self) -> Note | None:
        item = self.current_item()
        return item if isinstance(item, Note) else None

    def current_evidence(self) -> Evidence | None:
        item = self.current_item()
        return item if isinstance(item, Evidence) else None

    def current_member(self) -> Member | None:
        member_id = self._member_id()
        return self._members.get(member_id) if member_id is not None else None

    def _show_detail(self, force: bool = False) -> None:
        item = self.current_item()
        signature = (self.mode, item.id, item.updated_at, len(getattr(item, "note_ids", getattr(item, "evidence", [])))) if item else None
        if signature == self._shown and not force:
            return
        self._shown = signature
        meta = self.query_one("#detail-meta", Static)
        body = self.query_one("#detail-body", Markdown)
        images = self.query_one("#detail-images", Vertical)
        images.remove_children()
        if item is None:
            if self.query_text:
                meta.update("Nothing matches this filter.")
            elif self.mode == NOTES_MODE:
                meta.update("No notes yet — press [b]n[/] to write one.")
            else:
                meta.update(
                    "No evidence here yet. Take a screenshot with [b]⌘⇧4[/], then press [b]s[/] to pick it "
                    "from your screenshots folder, [b]i[/] to import a file, or [b]p[/] to paste from the clipboard."
                )
            body.update("")
            return
        if isinstance(item, Note):
            self._show_note(item, meta, body, images)
        else:
            self._show_evidence(item, meta, body, images)

    def _show_note(self, note: Note, meta: Static, body: Markdown, images: Vertical) -> None:
        people = ", ".join(f"{m.label} (@{m.handle})" for m in note.members) or "unassigned"
        line = Text.assemble(kind_text(note.kind), "  ", (f"{note.created:%A %B %d %Y, %H:%M}", "bold"), "  ", people)
        if note.tags:
            line.append("  " + " ".join(f"#{t}" for t in note.tags), style="cyan")
        if note.updated_at != note.created_at:
            line.append(f"\nedited {datetime.fromisoformat(note.updated_at):%b %d %H:%M}", style="dim")
        meta.update(line)
        body.update(note.body)
        widgets = []
        if note.evidence:
            widgets.append(Static(Text(f"Evidence ({len(note.evidence)}) — o opens, L links more", style="bold"), classes="caption"))
        for evidence in note.evidence[:MAX_PREVIEWS]:
            caption = Text.assemble(("▣ ", "cyan"), (short_when(evidence.taken), "dim"), "  ", evidence.title)
            widgets += [Static(caption, classes="caption"), image_preview(evidence.path)]
        if len(note.evidence) > MAX_PREVIEWS:
            widgets.append(Static(Text(f"+{len(note.evidence) - MAX_PREVIEWS} more", style="dim")))
        if widgets:
            images.mount_all(widgets)

    def _show_evidence(self, evidence: Evidence, meta: Static, body: Markdown, images: Vertical) -> None:
        people = ", ".join(f"{m.label} (@{m.handle})" for m in evidence.people) or "nobody tagged"
        line = Text.assemble(("▣ Evidence", "cyan"), "  ", (f"taken {evidence.taken:%A %B %d %Y, %H:%M}", "bold"), "  ", people)
        if evidence.tags:
            line.append("  " + " ".join(f"#{t}" for t in evidence.tags), style="cyan")
        line.append(f"\n{evidence.source_name or evidence.path.name}", style="dim")
        meta.update(line)
        notes = [n for n in (self.store.note(i) for i in evidence.note_ids) if n]
        caption = evidence.caption or "_No caption — press **e** to describe what this shows._"
        if notes:
            linked = "\n".join(f"- {short_when(n.created)} · {KIND_LABELS.get(n.kind, n.kind)} · {n.title}" for n in notes)
            text = f"{caption}\n\n**Supports {len(notes)} note{'s' if len(notes) != 1 else ''}**\n\n{linked}"
        else:
            text = f"{caption}\n\n**Not linked to a note yet** — press **L** to link it, or **n** to write a note about it."
        body.update(text)
        images.mount(image_preview(evidence.path))

    # --------------------------------------------------------------- events

    @on(OptionList.OptionHighlighted, "#team")
    def team_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if event.option.id and event.option.id != self.view:
            previous, self.view = self.view, event.option.id
            if self.view in (EVIDENCE, UNLINKED):
                self.mode = EVIDENCE_MODE
            elif self.view in (ALL, UNASSIGNED) or not previous.startswith("m:"):
                # people open on their notes; `v` on a person carries over between people
                self.mode = NOTES_MODE
            self._refresh_items(select=-1)

    @on(OptionList.OptionSelected, "#team")
    def team_selected(self) -> None:
        self.query_one("#items").focus()

    @on(DataTable.RowHighlighted, "#items")
    def row_highlighted(self) -> None:
        self._show_detail()

    @on(DataTable.RowSelected, "#items")
    def row_selected(self) -> None:
        self.action_edit()

    @on(Input.Changed, "#search")
    def search_changed(self, event: Input.Changed) -> None:
        self.query_text = event.value.strip()
        self._refresh_items()

    @on(Input.Submitted, "#search")
    def search_submitted(self) -> None:
        self.query_one("#items").focus()

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
        self.query_one("#team" if pane == "team" else "#items").focus()

    def action_search(self) -> None:
        search = self.query_one("#search", Input)
        search.display = True
        search.focus()

    def action_clear_search(self) -> None:
        search = self.query_one("#search", Input)
        if search.value or search.display:
            search.value = ""
            search.display = False
            self.query_one("#items").focus()

    def action_help(self) -> None:
        self.app.push_screen(HelpScreen())

    def action_toggle_mode(self) -> None:
        """Switch between notes and evidence for the current person (or everyone)."""
        if self.mode == NOTES_MODE:
            self.mode = EVIDENCE_MODE
            if self.view in (ALL, UNASSIGNED):
                self.view = EVIDENCE
        else:
            self.mode = NOTES_MODE
            if self.view in (EVIDENCE, UNLINKED):
                self.view = ALL
        self._refresh_team()
        self._refresh_items(select=-1)

    # notes

    def _open_editor(
        self,
        note: Note | None = None,
        staged: list[StagedEvidence] | None = None,
        link: list[Evidence] | None = None,
    ) -> None:
        handles: list[str] = []
        if note is None:
            member = self.current_member()
            handles = [member.handle] if member else []
            for evidence in link or []:
                handles += [m.handle for m in evidence.members if m.handle not in handles]
        editor = NoteEditor(self.store, note, handles=handles, staged=staged, link=link)
        self.app.push_screen(editor, self._editor_closed)

    def _editor_closed(self, note_id: int | None) -> None:
        """The editor saves notes itself (``:w``); here we just show the result."""
        if note_id is None:
            return
        note = self.store.note(note_id)
        if note is None:
            return
        if self.mode == NOTES_MODE:
            if self.view.startswith("m:") and int(self.view[2:]) not in {m.id for m in note.members}:
                self.view = ALL
            elif self.view == UNASSIGNED and note.members:
                self.view = ALL
            self.reload(select=note_id)
        else:
            self.reload()
        self._show_detail(force=True)

    def action_new_note(self) -> None:
        evidence = self.current_evidence()
        self._open_editor(link=[evidence] if evidence else None)

    def action_edit(self) -> None:
        item = self.current_item()
        if isinstance(item, Note):
            self._open_editor(self.store.note(item.id))
        elif isinstance(item, Evidence):
            fresh = self.store.evidence(item.id)
            if fresh:
                self.app.push_screen(EvidenceForm(self.store, fresh), lambda ok: ok and self._after_change())

    def _after_change(self, select: int | None = None) -> None:
        self.reload(select)
        self._show_detail(force=True)

    def action_delete(self) -> None:
        item = self.current_item()
        if isinstance(item, Note):
            detail = item.title[:60]
            if item.evidence:
                detail += f"\nIts {len(item.evidence)} evidence item(s) stay in the evidence library."

            def delete_note(yes: bool | None) -> None:
                if yes:
                    self.store.delete_note(item.id)
                    self._after_change()
                    self.notify("Note deleted", timeout=2)

            self.app.push_screen(ConfirmScreen("Delete this note?", detail), delete_note)
        elif isinstance(item, Evidence):
            detail = item.title[:60] + "\nThe image file is deleted"
            detail += f" and unlinked from {len(item.note_ids)} note(s)." if item.note_ids else "."

            def delete_evidence(yes: bool | None) -> None:
                if yes:
                    self.store.delete_evidence(item.id)
                    self._after_change()
                    self.notify("Evidence deleted", timeout=2)

            self.app.push_screen(ConfirmScreen("Delete this evidence?", detail), delete_evidence)

    def action_open_evidence(self) -> None:
        item = self.current_item()
        paths = [e.path for e in item.evidence] if isinstance(item, Note) else [item.path] if item else []
        if not paths:
            self.notify("This note has no evidence — press L to link some", severity="warning")
            return
        for path in paths[:6]:
            open_path(path)

    # evidence

    def _import(self, paths: list[Path], move: bool = False) -> None:
        """Notes view: start a note with the images. Evidence view: add them to the library."""
        good, errors = check_images(paths)
        for error in errors:
            self.notify(error, severity="error")
        if not good:
            return
        if self.mode == NOTES_MODE:
            # start a new note with the images (L links evidence to an existing note)
            self._open_editor(staged=[StagedEvidence(path, move) for path in good])
            return
        member = self.current_member()
        handles = [member.handle] if member else []
        imported, existing, first = 0, 0, None
        for path in good:
            evidence, _, created = self.store.import_evidence(path, handles=handles, move=move)
            first = first or evidence
            imported += created
            existing += not created
        message = f"Imported {imported} item(s) as evidence"
        if existing:
            message += f"; {existing} already in the library"
        self.notify(message + " — e to caption, L to link to a note", timeout=5)
        if self.view == UNLINKED and first and first.note_ids:
            self.view = EVIDENCE
        self._after_change(first.id if first else None)

    def action_screenshots(self) -> None:
        self.app.push_screen(ScreenshotPicker(self.store), lambda paths: paths and self._import(paths))

    def action_import_files(self) -> None:
        self.app.push_screen(
            PromptScreen("Import evidence", "Image path(s) — tip: drag files from Finder into the terminal"),
            lambda value: value and self._import(parse_paths(value)),
        )

    @work(exclusive=True, group="clipboard")
    async def action_paste(self) -> None:
        try:
            path = await paste_image(self.store.new_staging_path())
        except DesktopError as error:
            self.notify(str(error), severity="error", timeout=8)
            return
        if path is None:
            self.notify("No image on the clipboard (⌃⌘⇧4 copies a screenshot to it)", severity="warning")
            return
        self._import([path], move=True)

    def action_link(self) -> None:
        """Notes view: link library evidence to this note. Evidence view: link this evidence to a note."""
        item = self.current_item()
        if isinstance(item, Note):
            def link_to_note(ids: list[int] | None) -> None:
                for evidence_id in ids or []:
                    self.store.link_evidence(item.id, evidence_id)
                if ids:
                    self.notify(f"Linked {len(ids)} evidence item(s) to the note", timeout=3)
                    self._after_change(item.id)

            exclude = {e.id for e in item.evidence}
            self.app.push_screen(EvidencePicker(self.store, exclude=exclude), link_to_note)
        elif isinstance(item, Evidence):
            def link_evidence(note_id: int | None) -> None:
                if note_id is None:
                    return
                if note_id == -1:
                    self._open_editor(link=[item])
                    return
                self.store.link_evidence(note_id, item.id)
                self.notify("Evidence linked to the note", timeout=3)
                self._after_change(item.id)

            self.app.push_screen(NotePicker(self.store, exclude=set(item.note_ids)), link_evidence)
        else:
            self.notify("Nothing highlighted to link", severity="warning")

    def action_export(self) -> None:
        notes = self._view_notes()
        member = self.current_member()
        extra: list[Evidence] = []
        if member:
            linked = {e.id for n in notes for e in n.evidence}
            extra = [e for e in self.store.evidence_list(member.id) if e.id not in linked]
        if not notes and not extra:
            self.notify("Nothing to export", severity="warning")
            return
        slug = member.handle if member else self.view
        self.store.exports_dir.mkdir(parents=True, exist_ok=True)
        path = self.store.exports_dir / f"{slug}-{datetime.now():%Y%m%d-%H%M}.md"
        name = member.label if member else ("Unassigned notes" if self.view == UNASSIGNED else "All notes")
        title = f"Notes: {name}" + (f" (filter: {self.query_text})" if self.query_text else "")
        path.write_text(notes_to_markdown(title, notes, path.parent, extra_evidence=extra))
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
                self.reload(select=-1)
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
