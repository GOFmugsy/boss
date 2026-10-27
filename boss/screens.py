"""Modal screens: note editor, team member form, confirmations and help."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.suggester import Suggester
from rich.text import Text
from textual.widgets import (
    Button, Footer, Input, Label, Markdown, OptionList, Select, SelectionList, Static, TextArea,
)
from textual.widgets.option_list import Option
from textual.widgets.selection_list import Selection

from .desktop import DesktopError, is_image, parse_paths, paste_image, recent_images, screenshot_dirs
from .preview import image_preview
from .store import KINDS, Evidence, Note, Store, extract_mentions, normalize_handle
from .vim import VimTextArea


@dataclass
class StagedEvidence:
    """An image chosen in the editor that becomes evidence when the note is written."""

    path: Path
    move: bool = False  # True for temporary files (clipboard) that should be moved, not copied
    caption: str = ""


def short_when(stamp: datetime) -> str:
    today = datetime.now(stamp.tzinfo).date()
    if stamp.date() == today:
        return f"Today {stamp:%H:%M}"
    return f"{stamp:%b %d %H:%M}" if stamp.year == today.year else f"{stamp:%Y-%m-%d}"

# Ex commands understood by the editor: (shortest abbreviation, full name), as in Vim.
EX_COMMANDS = [
    ("w", "write"), ("wq", "wq"), ("wqa", "wqall"), ("x", "xit"), ("xa", "xall"),
    ("exi", "exit"), ("up", "update"), ("wa", "wall"), ("q", "quit"), ("qa", "qall"),
    ("quita", "quitall"), ("e", "edit"), ("r", "read"),
]
EX_RE = re.compile(r"^([A-Za-z]+)(!?)\s*(.*)$")


def resolve_ex(word: str) -> str | None:
    """Expand an abbreviated ex command (``wri`` -> ``write``) like Vim does."""
    for short, full in EX_COMMANDS:
        if full.startswith(word) and len(word) >= len(short):
            return full
    return None


class HandleSuggester(Suggester):
    """Completes the last ``@handle`` typed in a space separated list."""

    def __init__(self, handles: list[str]) -> None:
        super().__init__(use_cache=False, case_sensitive=True)
        self.handles = handles

    async def get_suggestion(self, value: str) -> str | None:
        head, _, last = value.rpartition(" ")
        partial = last.lstrip("@").lower()
        if not partial:
            return None
        for handle in self.handles:
            if handle.startswith(partial) and handle != partial:
                return f"{head} @{handle}".lstrip() if head else f"@{handle}"
        return None


def parse_handles(value: str) -> list[str]:
    return [normalize_handle(part) for part in value.replace(",", " ").split() if part.strip("@")]


def clean_path(value: str) -> Path:
    """A single path typed, pasted or drag-and-dropped into the terminal."""
    paths = parse_paths(value)
    return paths[0] if paths else Path(value.strip()).expanduser()


def check_images(paths: list[Path]) -> tuple[list[Path], list[str]]:
    """Split paths into usable images and error messages."""
    good, errors = [], []
    for path in paths:
        if not path.is_file():
            errors.append(f"not a file: {path}")
        elif not is_image(path):
            errors.append(f"not an image: {path.name}")
        else:
            good.append(path)
    return good, errors


class NoteEditor(Screen[int | None]):
    """Full-screen note editor with a Vim-style body.

    Saving follows Vim: ``:w`` writes and keeps editing, ``:wq``/``:x``/``ZZ`` write and
    close, ``:q`` refuses to drop unsaved changes (E37) and ``:q!`` discards them.
    The screen dismisses with the id of the saved note (or ``None`` if nothing was saved).
    """

    BINDINGS = [
        Binding("ctrl+s", "save_and_close", "Save & close", priority=True),
        Binding("ctrl+c", "cancel", "Cancel", priority=True),
        Binding("ctrl+e", "recent_screenshots", "Screenshots", priority=True),
        Binding("ctrl+o", "attach_file", "Import file", priority=True),
        Binding("ctrl+l", "link_existing", "Link evidence", priority=True),
        Binding("ctrl+y", "paste_image", "Paste image", priority=True),
        Binding("x,delete", "remove_evidence", "Unlink", show=False),
    ]

    def __init__(
        self,
        store: Store,
        note: Note | None = None,
        *,
        handles: list[str] | None = None,
        kind: str = "note",
        staged: list[StagedEvidence] | None = None,
        link: list[Evidence] | None = None,
    ) -> None:
        super().__init__()
        self.store = store
        self.note = note
        self.saved_id: int | None = note.id if note else None
        self._linked: list[Evidence] = list(note.evidence) if note else []  # saved links
        self._unlink: list[int] = []  # saved links to remove on write
        self._link: list[Evidence] = list(link or [])  # library evidence to link on write
        self._new: list[StagedEvidence] = list(staged or [])  # files to import on write
        self._team = [m.handle for m in store.members()]
        if note:
            mentioned = set(extract_mentions(note.body))
            handles = [m.handle for m in note.members if m.handle not in mentioned]
            kind = note.kind
        self._saved_state = (note.body if note else "", kind, " ".join(f"@{h}" for h in handles or []))

    def compose(self) -> ComposeResult:
        body, kind, who = self._saved_state
        with Vertical(id="editor"):
            yield Static(id="editor-title")
            with Horizontal(id="editor-meta"):
                yield Input(
                    who,
                    placeholder="@handle  (→ accepts completion; @mentions in the text also count)",
                    id="who",
                    suggester=HandleSuggester(self._team),
                )
                yield Select(
                    [(label, key) for key, label, _, _ in KINDS], value=kind, allow_blank=False, id="kind"
                )
            yield VimTextArea(body, id="body", start_in_insert=self.note is None)
            yield OptionList(id="evidence")
            yield Static(self._hint(), id="editor-hint")
        yield Footer()

    def _hint(self) -> str:
        team = " ".join(f"@{h}" for h in self._team) or "(no team yet — add people from the main screen)"
        return (
            f"[b]Team:[/] {team}\n"
            "[dim]#words become tags · :w save · :wq / :x / ZZ save & close · :q close · :q! discard\n"
            "Evidence: ^E screenshots folder · ^O import file · ^L link from library · ^Y clipboard · "
            "tab to the list: enter captions, x unlinks[/]"
        )

    def on_mount(self) -> None:
        self._refresh_evidence()
        self._refresh_title()
        self.query_one("#body", VimTextArea).focus()

    @property
    def body(self) -> VimTextArea:
        return self.query_one("#body", VimTextArea)

    def _refresh_title(self) -> None:
        if self.note:
            title = f"Note #{self.note.id} · {self.note.created:%a %b %d %Y, %H:%M}"
        else:
            title = "New note"
        self.query_one("#editor-title", Static).update(title + (" [b yellow]\\[+][/]" if self.dirty else ""))

    @on(TextArea.Changed)
    @on(Input.Changed, "#who")
    @on(Select.Changed, "#kind")
    def _changed(self) -> None:
        self._refresh_title()

    # -------------------------------------------------------------- evidence

    def _evidence_items(self) -> list[tuple[str, str, Path]]:
        """(option id, label, image path) for everything that will be linked after a write."""
        items = []
        for e in self._linked:
            if e.id not in self._unlink:
                items.append((f"e:{e.id}", e.title, e.path))
        for e in self._link:
            items.append((f"l:{e.id}", f"{e.title}  [dim](link pending)[/]", e.path))
        for i, staged in enumerate(self._new):
            label = staged.caption or staged.path.name
            items.append((f"n:{i}", f"{label}  [dim](import pending)[/]", staged.path))
        return items

    def _refresh_evidence(self) -> None:
        options = self.query_one("#evidence", OptionList)
        highlighted = options.highlighted
        options.clear_options()
        items = self._evidence_items()
        options.add_options([Option(f"▣ {label}", id=option_id) for option_id, label, _ in items])
        options.display = bool(items)
        options.border_title = f"Evidence ({len(items)})"
        if items:
            options.highlighted = min(highlighted or 0, len(items) - 1)
        if self.is_mounted:
            self._refresh_title()

    def _highlighted_evidence(self) -> str | None:
        options = self.query_one("#evidence", OptionList)
        if self.focused is not options or options.highlighted is None:
            return None
        return options.get_option_at_index(options.highlighted).id

    def action_remove_evidence(self) -> None:
        """Unlink the highlighted evidence from this note (the library keeps it)."""
        option_id = self._highlighted_evidence()
        if not option_id:
            return
        kind, _, value = option_id.partition(":")
        if kind == "e":
            self._unlink.append(int(value))
        elif kind == "l":
            self._link = [e for e in self._link if e.id != int(value)]
        else:
            staged = self._new.pop(int(value))
            if staged.move:
                staged.path.unlink(missing_ok=True)
        self._refresh_evidence()
        if not self.query_one("#evidence", OptionList).option_count:
            self.body.focus()

    @on(OptionList.OptionSelected, "#evidence")
    def edit_caption(self, event: OptionList.OptionSelected) -> None:
        kind, _, value = (event.option.id or "").partition(":")
        if kind == "n":
            staged = self._new[int(value)]

            def set_caption(caption: str | None) -> None:
                if caption is not None:
                    staged.caption = caption.strip()
                    self._refresh_evidence()

            self.app.push_screen(
                PromptScreen(f"Caption for {staged.path.name}", "@handle and #tag work here too", staged.caption),
                set_caption,
            )
        else:
            evidence = self.store.evidence(int(value))
            if evidence:
                self.app.push_screen(EvidenceForm(self.store, evidence), self._reload_linked)

    def _reload_linked(self, changed: bool | None = None) -> None:
        if changed:
            self._linked = [e for e in (self.store.evidence(x.id) for x in self._linked) if e]
            self._link = [e for e in (self.store.evidence(x.id) for x in self._link) if e]
            self._refresh_evidence()

    def stage_files(self, paths: list[Path], move: bool = False) -> None:
        good, errors = check_images(paths)
        for error in errors:
            self.body.echo(f"E484: {error}", error=True)
        if not good:
            return
        self._new.extend(StagedEvidence(path, move) for path in good)
        self._refresh_evidence()
        if not errors:
            names = ", ".join(f'"{p.name}"' for p in good[:2]) + (f" +{len(good) - 2}" if len(good) > 2 else "")
            self.body.echo(f"{names} added as evidence (unsaved)")

    def action_recent_screenshots(self) -> None:
        self.app.push_screen(ScreenshotPicker(self.store), lambda paths: paths and self.stage_files(paths))

    def action_link_existing(self) -> None:
        exclude = {e.id for e in self._linked if e.id not in self._unlink} | {e.id for e in self._link}

        def chosen(ids: list[int] | None) -> None:
            if ids:
                restored = [i for i in ids if i in self._unlink]
                self._unlink = [i for i in self._unlink if i not in restored]
                self._link += [e for e in (self.store.evidence(i) for i in ids if i not in restored) if e]
                self._refresh_evidence()
                self.body.echo(f"{len(ids)} evidence item(s) linked (unsaved)")

        self.app.push_screen(EvidencePicker(self.store, exclude=exclude), chosen)

    @work(exclusive=True, group="clipboard")
    async def action_paste_image(self) -> None:
        try:
            path = await paste_image(self.store.new_staging_path())
        except DesktopError as error:
            self.notify(str(error), severity="error", timeout=8)
            return
        if path is None:
            self.body.echo("No image on the clipboard (⌃⌘⇧4 copies a screenshot to it)", error=True)
        else:
            self.stage_files([path], move=True)

    def action_attach_file(self, value: str = "") -> None:
        def attach(value: str | None) -> None:
            if value:
                self.stage_files(parse_paths(value))

        if value:
            attach(value)
        else:
            self.app.push_screen(
                PromptScreen("Import evidence", "Image path(s) — tip: drag files from Finder into the terminal"),
                attach,
            )

    # ---------------------------------------------------------------- saving

    def _current(self) -> tuple[str, str, str]:
        return (
            self.body.text,
            str(self.query_one("#kind", Select).value),
            self.query_one("#who", Input).value,
        )

    @property
    def dirty(self) -> bool:
        """Like Vim's 'modified' flag: anything changed since the last write."""
        return self._current() != self._saved_state or bool(self._new or self._link or self._unlink)

    def write(self) -> bool:
        """Save the note to the database (``:w``). Returns False if there was nothing to write."""
        body, kind, who = self._current()
        if not self.dirty and self.saved_id is not None:
            self.body.echo(self._written_message(body))  # nothing changed; don't bump "edited"
            return True
        has_evidence = bool(self._new or self._link) or any(e.id not in self._unlink for e in self._linked)
        if not body.strip() and not has_evidence:
            self.body.echo("E32: Nothing to write — the note is empty", error=True)
            return False
        note, unknown = self.store.save_note(
            body.strip("\n"), kind=kind, handles=parse_handles(who), note_id=self.saved_id
        )
        for staged in self._new:
            try:
                evidence, more_unknown, _ = self.store.import_evidence(
                    staged.path, caption=staged.caption, move=staged.move
                )
            except OSError as error:
                self.notify(f"Could not import {staged.path}: {error}", severity="error")
                continue
            unknown += [h for h in more_unknown if h not in unknown]
            self.store.link_evidence(note.id, evidence.id)
        for evidence in self._link:
            self.store.link_evidence(note.id, evidence.id)
        for evidence_id in self._unlink:
            self.store.unlink_evidence(note.id, evidence_id)
        self.note = self.store.note(note.id)
        self.saved_id = note.id
        self._linked = list(self.note.evidence) if self.note else []
        self._new, self._link, self._unlink = [], [], []
        self._saved_state = (body, kind, who)
        self._refresh_evidence()
        message = self._written_message(body)
        if unknown:
            message += " · not on your team: " + " ".join(f"@{h}" for h in unknown)
        self.body.echo(message)
        return True

    def _written_message(self, body: str) -> str:
        return f'"note #{self.saved_id}" {body.count(chr(10)) + 1}L, {len(body.encode())}B written'

    def write_file(self, target: str, force: bool) -> bool:
        """``:w FILE`` writes the note text to a file, as Vim writes the buffer."""
        path = clean_path(target)
        if path.is_dir():
            self.body.echo(f'E502: "{path}" is a directory', error=True)
            return False
        if path.exists() and not force:
            self.body.echo("E13: File exists (add ! to override)", error=True)
            return False
        text = self.body.text
        text = text if text.endswith("\n") else text + "\n"
        try:
            path.write_text(text)
        except OSError as error:
            self.body.echo(f'E212: Can\'t open file for writing: {error.strerror or error}', error=True)
            return False
        self.body.echo(f'"{path}" {text.count(chr(10))}L, {len(text.encode())}B written')
        return True

    def close(self, discard: bool = False) -> None:
        """Leave the editor. Unsaved clipboard images are deleted when discarding."""
        if discard:
            for staged in self._new:
                if staged.move:
                    staged.path.unlink(missing_ok=True)
        self.dismiss(self.saved_id)

    def quit(self, force: bool) -> None:
        if self.dirty and not force:
            self.body.echo("E37: No write since last change (add ! to override)", error=True)
            return
        self.close(discard=True)

    def revert(self) -> None:
        """``:e!`` — throw away changes since the last write."""
        body, kind, who = self._saved_state
        self.body.set_text(body)
        self.query_one("#kind", Select).value = kind
        self.query_one("#who", Input).value = who
        for staged in self._new:
            if staged.move:
                staged.path.unlink(missing_ok=True)
        self._new, self._link, self._unlink = [], [], []
        self._refresh_evidence()
        self.body.echo("Reverted to the last saved version")

    def action_save_and_close(self) -> None:
        if not self.dirty and self.saved_id is not None:
            self.close()
        elif self.write():
            self.close()

    def action_cancel(self) -> None:
        if self.focused is self.body:
            # Vim users press ctrl+c to leave insert mode; in normal mode Vim explains how to quit.
            self.body._dispatch("<C-c>")
            return
        if not self.dirty:
            self.close(discard=True)
            return
        self.app.push_screen(
            ConfirmScreen("Discard unsaved changes?", "Anything not written with :w will be lost."),
            lambda yes: self.close(discard=True) if yes else None,
        )

    @on(VimTextArea.Command)
    def on_ex_command(self, message: VimTextArea.Command) -> None:
        command = message.command.strip()
        match = EX_RE.match(command)
        name = resolve_ex(match.group(1)) if match else None
        bang, arg = (match.group(2) == "!", match.group(3).strip()) if match else (False, "")
        if name in ("write", "wall"):
            self.write_file(arg, bang) if arg else self.write()
        elif name in ("wq", "wqall"):
            if arg and not self.write_file(arg, bang):
                return
            if (arg and not self.dirty) or self.write():
                self.close()
        elif name in ("xit", "exit", "xall"):
            if not self.dirty or self.write():
                self.close()
        elif name == "update":
            if self.dirty:
                self.write()
        elif name in ("quit", "qall", "quitall"):
            self.quit(force=bang)
        elif name == "edit":
            if arg:
                self.body.echo("boss edits one note at a time — use :r FILE or :attach PATH", error=True)
            elif bang:
                self.revert()
            elif self.dirty:
                self.body.echo("E37: No write since last change (add ! to override)", error=True)
        elif name == "read":
            self._read_file(arg)
        else:
            self._app_command(command)

    def _read_file(self, arg: str) -> None:
        """``:r FILE`` inserts a text file below the cursor; images are added as evidence instead."""
        if not arg:
            self.body.echo("E32: No file name", error=True)
            return
        path = clean_path(arg)
        if not path.is_file():
            self.body.echo(f'E484: Can\'t open file "{path}"', error=True)
        elif is_image(path):
            self.stage_files([path])
        else:
            text = path.read_text(errors="replace").rstrip("\n")
            row = self.body.cursor_location[0]
            line_end = (row, len(self.body.document.get_line(row)))
            self.body._snapshot()
            self.body.insert("\n" + text, line_end)
            self.body.cursor_location = (row + 1, 0)

    def _app_command(self, command: str) -> None:
        name, _, arg = command.partition(" ")
        if name in ("recent", "screenshots", "shots"):
            self.action_recent_screenshots()
        elif name in ("paste", "pasteimg"):
            self.action_paste_image()
        elif name in ("attach", "import", "evidence"):
            self.action_attach_file(arg.strip())
        elif name == "link":
            self.action_link_existing()
        elif name == "kind" and arg.strip() in [key for key, *_ in KINDS]:
            self.query_one("#kind", Select).value = arg.strip()
        else:
            self.body.echo(f"E492: Not an editor command: {command}", error=True)

    @on(Input.Submitted, "#who")
    def who_submitted(self) -> None:
        self.body.focus()


class MemberForm(ModalScreen[bool]):
    BINDINGS = [Binding("escape", "dismiss(False)", "Cancel")]

    def __init__(
        self,
        title: str,
        submit: Callable[[str, str, str], str | None],
        *,
        name: str = "",
        handle: str = "",
        role: str = "",
    ) -> None:
        super().__init__()
        self.form_title = title
        self.submit = submit
        self.initial = (name, handle, role)
        self._handle_touched = bool(handle)

    def compose(self) -> ComposeResult:
        name, handle, role = self.initial
        with Vertical(classes="dialog"):
            yield Label(self.form_title, classes="dialog-title")
            yield Input(name, placeholder="Full name", id="name")
            yield Input(handle, placeholder="handle, used as @handle in notes", id="handle")
            yield Input(role, placeholder="Role / level (optional)", id="role")
            yield Label("", id="form-error")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Save", variant="primary", id="save")
                yield Button("Cancel", id="cancel")

    @on(Input.Changed, "#name")
    def suggest_handle(self, event: Input.Changed) -> None:
        if not self._handle_touched:
            first = event.value.strip().split(" ")[0].lower() if event.value.strip() else ""
            handle = self.query_one("#handle", Input)
            with handle.prevent(Input.Changed):
                handle.value = "".join(c for c in first if c.isalnum() or c in "._-")

    @on(Input.Changed, "#handle")
    def handle_edited(self) -> None:
        self._handle_touched = True

    @on(Input.Submitted)
    def next_field(self, event: Input.Submitted) -> None:
        if event.input.id == "role":
            self._save()
        else:
            self.focus_next()

    @on(Button.Pressed, "#save")
    def _save(self) -> None:
        name = self.query_one("#name", Input).value
        handle = self.query_one("#handle", Input).value
        role = self.query_one("#role", Input).value
        error = self.submit(name, handle or name.split(" ")[0], role)
        if error:
            self.query_one("#form-error", Label).update(f"[red]{error}[/]")
        else:
            self.dismiss(True)

    @on(Button.Pressed, "#cancel")
    def _cancel(self) -> None:
        self.dismiss(False)


class ConfirmScreen(ModalScreen[bool]):
    BINDINGS = [
        Binding("y,enter", "dismiss(True)", "Yes"),
        Binding("n,escape", "dismiss(False)", "No"),
    ]

    def __init__(self, question: str, detail: str = "") -> None:
        super().__init__()
        self.question = question
        self.detail = detail

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self.question, classes="dialog-title")
            if self.detail:
                yield Label(self.detail)
            yield Label("[b]y[/] yes   [b]n[/] no", classes="dialog-hint")


class PromptScreen(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "dismiss(None)", "Cancel")]

    def __init__(self, title: str, placeholder: str = "", value: str = "") -> None:
        super().__init__()
        self.prompt_title = title
        self.placeholder = placeholder
        self.value = value

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self.prompt_title, classes="dialog-title")
            yield Input(self.value, placeholder=self.placeholder)
            yield Label("[b]enter[/] ok   [b]esc[/] cancel", classes="dialog-hint")

    @on(Input.Submitted)
    def submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)


class ImagePicker(ModalScreen[list]):
    """Pick one or more images from a list, with a live preview of the highlighted one.

    space toggles a selection; enter takes the selected items (or the highlighted one).
    """

    BINDINGS = [
        Binding("enter", "choose", "Choose", priority=True),
        Binding("escape", "dismiss([])", "Cancel"),
    ]

    def __init__(self, title: str, items: list[tuple[object, Text, Path]], empty: str) -> None:
        super().__init__()
        self.picker_title = title
        self.items = items
        self.paths = {i: path for i, (_, _, path) in enumerate(items)}
        self.empty = empty

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog picker" if self.items else "dialog"):
            yield Label(self.picker_title, classes="dialog-title")
            if not self.items:
                yield Static(self.empty)
            else:
                with Horizontal(classes="picker-body"):
                    yield SelectionList[int](
                        *[Selection(label, i) for i, (_, label, _) in enumerate(self.items)], id="choices"
                    )
                    yield VerticalScroll(id="picker-preview")
            yield Label("[b]space[/] select several  [b]enter[/] choose  [b]esc[/] cancel", classes="dialog-hint")

    def on_mount(self) -> None:
        if self.items:
            self._preview(0)

    @on(SelectionList.SelectionHighlighted)
    def highlighted(self, event: SelectionList.SelectionHighlighted) -> None:
        self._preview(event.selection.value)

    def _preview(self, index: int) -> None:
        pane = self.query_one("#picker-preview", VerticalScroll)
        pane.remove_children()
        path = self.paths[index]
        pane.mount(Static(Text(path.name, style="bold")), image_preview(path, "picker-image"))

    def action_choose(self) -> None:
        if not self.items:
            self.dismiss([])
            return
        choices = self.query_one("#choices", SelectionList)
        indexes = list(choices.selected) or ([choices.highlighted] if choices.highlighted is not None else [])
        self.dismiss([self.items[i][0] for i in sorted(indexes)])


class ScreenshotPicker(ImagePicker):
    """Recent images from the screenshots folder (macOS: where ⌘⇧4 saves)."""

    def __init__(self, store: Store) -> None:
        folders = screenshot_dirs()
        items = []
        for path in recent_images(folders):
            stamp = datetime.fromtimestamp(path.stat().st_mtime).astimezone()
            imported = store.evidence_by_source(path.resolve()) is not None
            label = Text.assemble(
                (f"{short_when(stamp):<13}", "dim"), ("✓ imported  " if imported else "", "green"), path.name
            )
            items.append((path, label, path))
        where = ", ".join(str(f).replace(str(Path.home()), "~") for f in folders) or "(none found)"
        super().__init__(
            f"Import screenshots as evidence · {where}",
            items,
            "No images from the last 30 days in your screenshots folder.\n"
            "Take one with ⌘⇧4 (or ⌘⇧5), or set BOSS_SCREENSHOTS_DIR to another folder.",
        )


class EvidencePicker(ImagePicker):
    """Evidence already in the library (unlinked items first)."""

    def __init__(self, store: Store, exclude: set[int] | None = None) -> None:
        library = [e for e in store.evidence_list() if e.id not in (exclude or set())]
        library.sort(key=lambda e: (bool(e.note_ids), -e.taken.timestamp()))
        items = []
        for e in library:
            label = Text.assemble((f"{short_when(e.taken):<13}", "dim"), e.title)
            people = " ".join(f"@{m.handle}" for m in e.people)
            if people:
                label.append(f"  {people}", style="cyan")
            label.append(f"  {len(e.note_ids)} notes" if e.note_ids else "  unlinked", style="dim" if e.note_ids else "yellow")
            items.append((e.id, label, e.path))
        super().__init__("Link evidence from the library", items, "The evidence library is empty.")


class NotePicker(ModalScreen[int | None]):
    """Choose a note to link evidence to. Returns a note id, -1 for a new note, or None."""

    BINDINGS = [Binding("escape", "dismiss(None)", "Cancel")]

    def __init__(self, store: Store, exclude: set[int] | None = None, title: str = "Link to which note?") -> None:
        super().__init__()
        self.store = store
        self.exclude = exclude or set()
        self.picker_title = title

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog picker"):
            yield Label(self.picker_title, classes="dialog-title")
            yield Input(placeholder="filter: text  #tag  @handle  kind:win", id="note-filter")
            yield OptionList(id="note-choices")
            yield Label("[b]↑↓[/] move  [b]enter[/] link  [b]esc[/] cancel", classes="dialog-hint")

    def on_mount(self) -> None:
        self._fill("")
        self.query_one("#note-filter").focus()

    @on(Input.Changed, "#note-filter")
    def filter_changed(self, event: Input.Changed) -> None:
        self._fill(event.value)

    def _fill(self, query: str) -> None:
        options = self.query_one("#note-choices", OptionList)
        options.clear_options()
        options.add_option(Option(Text("+ New note with this evidence", style="green"), id="-1"))
        for note in self.store.notes(query=query)[:200]:
            if note.id in self.exclude:
                continue
            who = " ".join(f"@{m.handle}" for m in note.members)
            label = Text.assemble((f"{short_when(note.created):<13}", "dim"), note.title[:70], (f"  {who}", "cyan"))
            options.add_option(Option(label, id=str(note.id)))
        options.highlighted = 1 if options.option_count > 1 and query else 0

    @on(Input.Submitted, "#note-filter")
    def submitted(self) -> None:
        options = self.query_one("#note-choices", OptionList)
        if options.highlighted is not None:
            self.dismiss(int(options.get_option_at_index(options.highlighted).id or "-1"))

    def on_key(self, event) -> None:
        if event.key in ("down", "up") and self.focused is self.query_one("#note-filter"):
            options = self.query_one("#note-choices", OptionList)
            options.action_cursor_down() if event.key == "down" else options.action_cursor_up()
            event.stop()

    @on(OptionList.OptionSelected, "#note-choices")
    def selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(int(event.option.id or "-1"))


class EvidenceForm(ModalScreen[bool]):
    """Caption and tag a piece of evidence; shows which notes it supports."""

    BINDINGS = [Binding("escape", "dismiss(False)", "Cancel"), Binding("ctrl+s", "save", "Save", priority=True)]

    def __init__(self, store: Store, evidence: Evidence) -> None:
        super().__init__()
        self.store = store
        self.evidence = evidence

    def compose(self) -> ComposeResult:
        e = self.evidence
        mentioned = set(extract_mentions(e.caption))
        who = " ".join(f"@{m.handle}" for m in e.members if m.handle not in mentioned)
        notes = [n for n in (self.store.note(i) for i in e.note_ids) if n]
        with Vertical(classes="dialog evidence-form"):
            yield Label(f"Evidence #{e.id} · {e.taken:%a %b %d %Y, %H:%M}", classes="dialog-title")
            yield image_preview(e.path, "form-image")
            yield Input(e.caption, placeholder="Caption — what does this show? (@handle and #tag work)", id="caption")
            yield Input(
                who, placeholder="@people (besides those on linked notes)", id="people",
                suggester=HandleSuggester([m.handle for m in self.store.members()]),
            )
            linked = "\n".join(f"  • {short_when(n.created)}  {n.title[:60]}" for n in notes) or "  (none — press L on the main screen to link it)"
            yield Static(f"[b]Linked notes[/]\n{linked}\n[dim]{e.source_name or e.path.name}[/]")
            yield Label("[b]enter[/] / [b]ctrl+s[/] save   [b]esc[/] cancel", classes="dialog-hint")

    def on_mount(self) -> None:
        self.query_one("#caption").focus()

    @on(Input.Submitted)
    def action_save(self) -> None:
        caption = self.query_one("#caption", Input).value
        people = parse_handles(self.query_one("#people", Input).value)
        _, unknown = self.store.update_evidence(self.evidence.id, caption=caption, handles=people)
        if unknown:
            self.notify("Not on your team yet: " + ", ".join(f"@{h}" for h in unknown), severity="warning")
        self.dismiss(True)


HELP = """\
# boss — keyboard reference

## Main screen
| Key | Action |
|---|---|
| `n` | New note (tagged to the selected person; in evidence view, linked to the highlighted evidence) |
| `enter` / `e` | Edit the highlighted note, or caption/tag the highlighted evidence |
| `d` | Delete the highlighted note (its evidence stays in the library) or evidence |
| `v` | Switch between **notes** and **evidence** (for everyone or the selected person) |
| `s` | Import from your **screenshots folder** (where ⌘⇧4 saves) |
| `i` | Import image file(s) — drag files from Finder into the prompt |
| `p` | Import the image on the **clipboard** (⌃⌘⇧4 copies a screenshot) |
| `L` | **Link**: evidence → a note, or library evidence → the highlighted note |
| `o` | Open evidence in Preview |
| `/` | Filter: free text, `#tag`, `@handle`, `kind:win` (esc clears) |
| `j` `k` / `g` `G` | Move down / up, top / bottom |
| `h` `l` / `tab` | Jump between the team list and the list |
| `a` | Add a team member · `r` edit · `A` archive/restore · `H` show archived |
| `w` | Export the current view (with evidence) to Markdown |
| `ctrl+q` | Quit |

In the **notes** view, importing (`s` `i` `p`) starts a new note with the images.
In the **evidence** view, it adds them to the library (tagged to the selected person) —
then press `e` to caption them and `L` to link them to notes. *Unlinked* in the team list
collects evidence that doesn't support a note yet.

boss doesn't take screenshots itself: use ⌘⇧4 / ⌘⇧5, then import.

## Note editor
| Key | Action |
|---|---|
| `:w` | Save and keep editing |
| `:wq` `:x` `ZZ` or `ctrl+s` | Save and close |
| `:q` | Close (refuses with E37 if there are unsaved changes) |
| `:q!` `ZQ` | Close and discard unsaved changes |
| `:e!` | Revert to the last saved version |
| `:w FILE` / `:r FILE` | Write the note to a file / insert a file (images become evidence) |
| `ctrl+e` or `:recent` | Add evidence from your screenshots folder |
| `ctrl+o` or `:attach PATH…` | Add image file(s) as evidence |
| `ctrl+l` or `:link` | Link evidence already in the library |
| `ctrl+y` or `:paste` | Add the clipboard image as evidence |
| `tab` (normal mode) | Move to people / kind / evidence list (`enter` captions, `x` unlinks) |
| `ctrl+c` | Leave insert mode (like Vim); from other fields, cancel |

Evidence changes are saved with the note (`:w`). Write `@handle` anywhere in a note or
caption to tag a person and `#topic` to add a tag.

## Vim support in the editor
New notes start in **insert** mode; `Esc` (or `ctrl+[` / `ctrl+c`) returns to **normal** mode.

* **Motions** `h j k l` `w b e W B E ge` `0 ^ $ _ |` `gg G` `{ }` `f F t T ; ,` `%` `n N` `+ -` `ctrl+d/u/f/b` — all take counts
* **Operators** `d c y > < gu gU g~` + any motion or text object; `dd cc yy >> <<`
* **Text objects** `iw aw iW aW` `i" a" i' a'` `i( a( i[ a[ i{ a{ i< a<` `ip ap`
* **Editing** `i a I A o O` `x X s S C D Y` `p P` `r` `J` `~` `u` `ctrl+r` `.`
* **Visual** `v` / `V`, then a motion or text object and `d y c > < ~ u U J p r o`
* **Search** `/text` `?text`, then `n` / `N` (smart-case)
* **Insert mode** `ctrl+w` delete word · `ctrl+u` delete to line start · Markdown bullets continue on Enter

_Press esc or q to close._
"""


class HelpScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape,q,question_mark", "dismiss", "Close")]

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="dialog help"):
            yield Markdown(HELP)
