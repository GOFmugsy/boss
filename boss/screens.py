"""Modal screens: note editor, team member form, confirmations and help."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.suggester import Suggester
from textual.widgets import Button, Footer, Input, Label, Markdown, OptionList, Select, Static, TextArea
from textual.widgets.option_list import Option

from .capture import CaptureError, capture_region, paste_image
from .store import KINDS, Note, Store, extract_mentions, normalize_handle
from .vim import VimTextArea

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".tif", ".tiff", ".bmp"}

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
    """Accept paths typed, pasted or drag-and-dropped into the terminal."""
    value = value.strip()
    if len(value) > 1 and value[0] == value[-1] and value[0] in "'\"":
        value = value[1:-1]
    if value.startswith("file://"):
        value = value[len("file://"):]
    return Path(value.replace("\\ ", " ")).expanduser()


class NoteEditor(Screen[int | None]):
    """Full-screen note editor with a Vim-style body.

    Saving follows Vim: ``:w`` writes and keeps editing, ``:wq``/``:x``/``ZZ`` write and
    close, ``:q`` refuses to drop unsaved changes (E37) and ``:q!`` discards them.
    The screen dismisses with the id of the saved note (or ``None`` if nothing was saved).
    """

    BINDINGS = [
        Binding("ctrl+s", "save_and_close", "Save & close", priority=True),
        Binding("ctrl+c", "cancel", "Cancel", priority=True),
        Binding("ctrl+t", "screenshot", "Screenshot", priority=True),
        Binding("ctrl+y", "paste_image", "Paste image", priority=True),
        Binding("ctrl+o", "attach_file", "Attach file", priority=True),
        Binding("x,delete", "remove_attachment", "Remove", show=False),
    ]

    def __init__(
        self,
        store: Store,
        note: Note | None = None,
        *,
        handles: list[str] | None = None,
        kind: str = "note",
        staged: list[tuple[Path, bool]] | None = None,
    ) -> None:
        super().__init__()
        self.store = store
        self.note = note
        self.saved_id: int | None = note.id if note else None
        self._existing = list(note.attachments) if note else []
        self._removed: list[int] = []
        self._new: list[tuple[Path, bool]] = list(staged or [])  # (path, move-into-store)
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
            yield OptionList(id="attachments")
            yield Static(self._hint(), id="editor-hint")
        yield Footer()

    def _hint(self) -> str:
        team = " ".join(f"@{h}" for h in self._team) or "(no team yet — add people from the main screen)"
        return (
            f"[b]Team:[/] {team}\n"
            "[dim]#words become tags · :w save · :wq / :x / ZZ save & close · :q close · :q! discard · "
            ":shot :paste :attach PATH[/]"
        )

    def on_mount(self) -> None:
        self._refresh_attachments()
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

    # ---------------------------------------------------------- attachments

    def _refresh_attachments(self) -> None:
        options = self.query_one("#attachments", OptionList)
        options.clear_options()
        items = [
            Option(f"▣ {a.path.name}", id=f"a:{a.id}") for a in self._existing if a.id not in self._removed
        ]
        items += [Option(f"▣ {path.name}  [dim](unsaved)[/]", id=f"n:{i}") for i, (path, _) in enumerate(self._new)]
        options.add_options(items)
        options.display = bool(items)
        options.border_title = f"Attachments ({len(items)})"
        if self.is_mounted:
            self._refresh_title()

    def action_remove_attachment(self) -> None:
        options = self.query_one("#attachments", OptionList)
        if self.focused is not options or options.highlighted is None:
            return
        option_id = options.get_option_at_index(options.highlighted).id or ""
        kind, _, value = option_id.partition(":")
        if kind == "a":
            self._removed.append(int(value))
        else:
            path, move = self._new.pop(int(value))
            if move:
                path.unlink(missing_ok=True)
        self._refresh_attachments()
        if not options.option_count:
            self.body.focus()

    def _add_file(self, path: Path, move: bool) -> None:
        self._new.append((path, move))
        self._refresh_attachments()
        self.body.echo(f'"{path.name}" attached (unsaved)')

    @work(exclusive=True, group="capture")
    async def action_screenshot(self) -> None:
        self.notify("Select a region of the screen…", timeout=3)
        try:
            path = await capture_region(self.store.new_staging_path())
        except CaptureError as error:
            self.notify(str(error), severity="error", timeout=8)
            return
        if path is None:
            self.notify("Screenshot cancelled", severity="warning")
        else:
            self._add_file(path, True)

    @work(exclusive=True, group="capture")
    async def action_paste_image(self) -> None:
        try:
            path = await paste_image(self.store.new_staging_path())
        except CaptureError as error:
            self.notify(str(error), severity="error", timeout=8)
            return
        if path is None:
            self.notify("No image on the clipboard", severity="warning")
        else:
            self._add_file(path, True)

    def action_attach_file(self, value: str = "") -> None:
        def attach(value: str | None) -> None:
            if not value:
                return
            path = clean_path(value)
            if path.is_file():
                self._add_file(path, False)
            else:
                self.body.echo(f'E484: Can\'t open file "{path}"', error=True)

        if value:
            attach(value)
        else:
            self.app.push_screen(
                PromptScreen("Attach file", "Path to an image (tip: drag a file into the terminal)"), attach
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
        return self._current() != self._saved_state or bool(self._new) or bool(self._removed)

    def write(self) -> bool:
        """Save the note to the database (``:w``). Returns False if there was nothing to write."""
        body, kind, who = self._current()
        if not self.dirty and self.saved_id is not None:
            self.body.echo(self._written_message(body))  # nothing changed; don't bump "edited"
            return True
        has_attachments = bool(self._new) or any(a.id not in self._removed for a in self._existing)
        if not body.strip() and not has_attachments:
            self.body.echo("E32: Nothing to write — the note is empty", error=True)
            return False
        note, unknown = self.store.save_note(
            body.strip("\n"), kind=kind, handles=parse_handles(who), note_id=self.saved_id
        )
        for path, move in self._new:
            try:
                self.store.add_attachment(note.id, path, move=move)
            except OSError as error:
                self.notify(f"Could not attach {path}: {error}", severity="error")
        for attachment_id in self._removed:
            self.store.remove_attachment(attachment_id)
        self.note = self.store.note(note.id)
        self.saved_id = note.id
        self._existing = list(self.note.attachments) if self.note else []
        self._new, self._removed = [], []
        self._saved_state = (body, kind, who)
        self._refresh_attachments()
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
        """Leave the editor. Unsaved screenshots are deleted when discarding."""
        if discard:
            for path, move in self._new:
                if move:
                    path.unlink(missing_ok=True)
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
        for path, move in self._new:
            if move:
                path.unlink(missing_ok=True)
        self._new, self._removed = [], []
        self._refresh_attachments()
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
        """``:r FILE`` inserts a text file below the cursor; images are attached instead."""
        if not arg:
            self.body.echo("E32: No file name", error=True)
            return
        path = clean_path(arg)
        if not path.is_file():
            self.body.echo(f'E484: Can\'t open file "{path}"', error=True)
        elif path.suffix.lower() in IMAGE_SUFFIXES:
            self._add_file(path, False)
        else:
            text = path.read_text(errors="replace").rstrip("\n")
            row = self.body.cursor_location[0]
            line_end = (row, len(self.body.document.get_line(row)))
            self.body._snapshot()
            self.body.insert("\n" + text, line_end)
            self.body.cursor_location = (row + 1, 0)

    def _app_command(self, command: str) -> None:
        name, _, arg = command.partition(" ")
        if name in ("shot", "screenshot"):
            self.action_screenshot()
        elif name in ("paste", "pasteimg"):
            self.action_paste_image()
        elif name == "attach":
            self.action_attach_file(arg.strip())
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


HELP = """\
# boss — keyboard reference

## Main screen
| Key | Action |
|---|---|
| `n` | New note (tagged to the selected person) |
| `s` | New note from a **screenshot** (select a screen region) |
| `p` | New note from an image on the **clipboard** |
| `enter` / `e` | Edit the highlighted note |
| `d` | Delete the highlighted note |
| `o` | Open the note's screenshots in your image viewer |
| `/` | Filter: free text, `#tag`, `@handle`, `kind:win` (esc clears) |
| `j` `k` / `g` `G` | Move down / up, top / bottom |
| `h` `l` / `tab` | Jump between the team list and the notes |
| `a` | Add a team member · `r` edit · `A` archive/restore |
| `H` | Show / hide archived people |
| `w` | Export the current view to Markdown (for reviews) |
| `ctrl+q` | Quit |

## Note editor
| Key | Action |
|---|---|
| `:w` | Save and keep editing |
| `:wq` `:x` `ZZ` or `ctrl+s` | Save and close |
| `:q` | Close (refuses with E37 if there are unsaved changes) |
| `:q!` `ZQ` | Close and discard unsaved changes |
| `:e!` | Revert to the last saved version |
| `:w FILE` / `:r FILE` | Write the note to a file / insert a file (images are attached) |
| `ctrl+c` | Leave insert mode (like Vim); from other fields, cancel |
| `ctrl+t` or `:shot` | Attach a screenshot |
| `ctrl+y` or `:paste` | Attach the clipboard image |
| `ctrl+o` or `:attach PATH` | Attach an image file |
| `tab` (in normal mode) | Move to people / kind / attachments |

Write `@handle` anywhere in a note to tag a person and `#topic` to add a tag.

## Vim support in the editor
New notes start in **insert** mode; `Esc` (or `ctrl+[`) returns to **normal** mode.

* **Motions** `h j k l` `w b e W B E ge` `0 ^ $ _ |` `gg G` `{ }` `f F t T ; ,` `%` `n N` `+ -` `ctrl+d/u/f/b` — all take counts
* **Operators** `d c y > < gu gU g~` + any motion or text object; `dd cc yy >> <<`
* **Text objects** `iw aw iW aW` `i" a" i' a'` `i( a( i[ a[ i{ a{ i< a<` `ip ap`
* **Editing** `i a I A o O` `x X s S C D Y` `p P` `r` `J` `~` `u` `ctrl+r` `.`
* **Visual** `v` / `V`, then a motion or text object and `d y c > < ~ u U J p r o`
* **Search** `/text` `?text`, then `n` / `N` (smart-case)
* **Insert mode** `ctrl+w` delete word · `ctrl+u` delete to line start · Markdown bullets continue on Enter
* **Ex** `:w :q :q! :wq :x :<line>` `zz zt zb`

_Press esc or q to close._
"""


class HelpScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape,q,question_mark", "dismiss", "Close")]

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="dialog help"):
            yield Markdown(HELP)
