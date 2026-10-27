"""A Textual ``TextArea`` with modal, Vim-style editing.

Supported (with counts where Vim allows them):

* Modes: normal, insert, visual (``v``), visual line (``V``), ``:`` command line,
  ``/`` and ``?`` search.
* Motions: ``h j k l w W b B e E ge gE 0 ^ _ $ | gg G { } f F t T ; , % n N +
  - <CR>`` plus arrow keys, ``<C-d> <C-u> <C-f> <C-b>``.
* Operators: ``d c y > < g~ gu gU`` combined with any motion or text object,
  and doubled (``dd``, ``cc``, ``yy``, ``>>`` ...).
* Text objects: ``iw aw iW aW i" a" i' a' i` a` i( a( ib ab i[ a[ i{ a{ iB aB
  i< a< ip ap``.
* Actions: ``i a I A o O s S C D Y x X p P r J ~ u <C-r> . zz zt zb ZZ ZQ``.
* Insert mode: ``<C-w>`` ``<C-u>`` ``<C-h>``/backspace, Markdown bullet continuation.
* Ex commands ``:w :q :q! :wq :x :<line>``; unknown commands are forwarded to the
  application via :class:`VimTextArea.Command`.
"""

from __future__ import annotations

import sys
from bisect import bisect_right
from enum import Enum

from textual import events
from textual.content import Content
from textual.message import Message
from textual.widgets import TextArea
from textual.widgets.text_area import Selection

INCOMPLETE = object()
INVALID = object()

OPEN_CLOSE = {
    "(": ("(", ")"), ")": ("(", ")"), "b": ("(", ")"),
    "[": ("[", "]"), "]": ("[", "]"),
    "{": ("{", "}"), "}": ("{", "}"), "B": ("{", "}"),
    "<": ("<", ">"), ">": ("<", ">"),
}
BRACKET_PAIRS = {"(": ")", "[": "]", "{": "}", ")": "(", "]": "[", "}": "{"}

SPECIAL_KEYS = {
    "escape": "<esc>",
    "ctrl+left_square_bracket": "<esc>",
    "enter": "<cr>",
    "backspace": "<bs>",
    "ctrl+h": "<bs>",
    "delete": "<del>",
    "tab": "<tab>",
    "left": "<left>",
    "right": "<right>",
    "up": "<up>",
    "down": "<down>",
    "home": "<home>",
    "end": "<end>",
    "pageup": "<C-b>",
    "pagedown": "<C-f>",
}

# Keys swallowed in normal/visual mode so TextArea's emacs-ish bindings don't fire.
NORMAL_SPECIALS = {
    "<esc>", "<cr>", "<bs>", "<del>", "<left>", "<right>", "<up>", "<down>",
    "<home>", "<end>", "<C-r>", "<C-d>", "<C-u>", "<C-f>", "<C-b>", "<C-e>",
    "<C-y>", "<C-n>", "<C-p>", "<C-j>", "<C-w>", "<C-k>", "<C-v>", "<C-z>",
    "<C-a>", "<C-x>", "<C-c>",
}

# Motions that keep the remembered column.
VERTICAL = {"j", "k", "<down>", "<up>", "<C-n>", "<C-p>", "<C-j>", "<C-d>", "<C-u>", "<C-f>", "<C-b>"}

# Normal-mode shorthands expanded to operator + motion.
ALIASES = {
    "x": ["d", "l"],
    "<del>": ["d", "l"],
    "X": ["d", "h"],
    "s": ["c", "l"],
    "S": ["c", "c"],
    "C": ["c", "$"],
    "D": ["d", "$"],
    "Y": ["y", "y"],
}

BULLETS = ("- [ ] ", "- [x] ", "- ", "* ", "+ ")


class Mode(Enum):
    NORMAL = "NORMAL"
    INSERT = "INSERT"
    VISUAL = "VISUAL"
    VISUAL_LINE = "VISUAL LINE"
    COMMAND = "COMMAND"


def key_name(event: events.Key) -> str:
    """Translate a Textual key event into Vim-ish notation."""
    if event.key in SPECIAL_KEYS:
        return SPECIAL_KEYS[event.key]
    if event.is_printable and event.character and len(event.character) == 1:
        return event.character
    if event.key.startswith("ctrl+") and len(event.key) == 6:
        return f"<C-{event.key[5]}>"
    return f"<{event.key}>"


def _cls(ch: str, big: bool) -> int:
    if ch.isspace():
        return 0
    if big:
        return 1
    if ch.isalnum() or ch == "_":
        return 2
    return 3


def _read_count(keys: list[str], i: int) -> tuple[int, int]:
    digits = ""
    while i < len(keys) and keys[i].isdigit() and not (keys[i] == "0" and not digits):
        digits += keys[i]
        i += 1
    return (int(digits) if digits else 0), i


def _read_operator(keys: list[str], i: int):
    k = keys[i]
    if k in ("d", "c", "y", ">", "<"):
        return k, i + 1
    if k == "g":
        if i + 1 >= len(keys):
            return INCOMPLETE, i
        if keys[i + 1] in ("~", "u", "U"):
            return "g" + keys[i + 1], i + 2
    return None, i


class Buffer:
    """A snapshot of the document with offset <-> (row, col) helpers."""

    def __init__(self, text: str) -> None:
        self.s = text
        self.lines = text.split("\n")
        self.starts: list[int] = []
        offset = 0
        for line in self.lines:
            self.starts.append(offset)
            offset += len(line) + 1

    def off(self, row: int, col: int) -> int:
        return self.starts[row] + col

    def loc(self, off: int) -> tuple[int, int]:
        off = max(0, min(off, len(self.s)))
        row = bisect_right(self.starts, off) - 1
        return row, off - self.starts[row]

    def row(self, off: int) -> int:
        return self.loc(off)[0]

    def line_start(self, row: int) -> int:
        return self.starts[row]

    def line_end(self, row: int) -> int:
        return self.starts[row] + len(self.lines[row])

    def first_nonblank(self, row: int) -> int:
        line = self.lines[row]
        return self.starts[row] + (len(line) - len(line.lstrip(" \t")))

    def last_row(self) -> int:
        return len(self.lines) - 1

    def is_blank(self, row: int) -> bool:
        return not self.lines[row].strip()


# ---------------------------------------------------------------- word motions


def next_word_start(s: str, i: int, big: bool) -> int:
    n = len(s)
    if i >= n:
        return n
    c = _cls(s[i], big)
    if c:
        while i < n and _cls(s[i], big) == c:
            i += 1
    while i < n and s[i].isspace():
        if s[i] == "\n" and i + 1 < n and s[i + 1] == "\n":
            return i + 1  # empty line counts as a word
        i += 1
    return i


def word_end(s: str, i: int, big: bool) -> int:
    n = len(s)
    i += 1
    while i < n and s[i].isspace():
        i += 1
    if i >= n:
        return max(0, n - 1)
    c = _cls(s[i], big)
    while i + 1 < n and _cls(s[i + 1], big) == c:
        i += 1
    return i


def prev_word_start(s: str, i: int, big: bool) -> int:
    i -= 1
    while i > 0 and s[i].isspace():
        if s[i] == "\n" and s[i - 1] == "\n":
            return i
        i -= 1
    if i <= 0:
        return 0
    c = _cls(s[i], big)
    while i > 0 and _cls(s[i - 1], big) == c:
        i -= 1
    return i


def prev_word_end(s: str, i: int, big: bool) -> int:
    if i >= len(s):
        i = len(s) - 1
    if i <= 0:
        return 0
    c = _cls(s[i], big)
    if c:
        while i > 0 and _cls(s[i], big) == c:
            i -= 1
    while i > 0 and s[i].isspace():
        if s[i] == "\n" and s[i - 1] == "\n":
            return i
        i -= 1
    return i


class VimTextArea(TextArea):
    """A TextArea that edits like Vim."""

    DEFAULT_CSS = """
    VimTextArea {
        border-subtitle-align: left;
    }
    VimTextArea:focus.-insert {
        border: tall $success;
    }
    VimTextArea:focus.-visual {
        border: tall $warning;
    }
    VimTextArea:focus.-command {
        border: tall $accent;
    }
    """

    class ModeChanged(Message):
        def __init__(self, area: "VimTextArea", mode: Mode) -> None:
            super().__init__()
            self.area = area
            self.mode = mode

    class Command(Message):
        """An ex command (``:w``, ``:q``, ``:shot`` ...) entered by the user."""

        def __init__(self, area: "VimTextArea", command: str) -> None:
            super().__init__()
            self.area = area
            self.command = command

    def __init__(self, text: str = "", *, start_in_insert: bool = False, **kwargs) -> None:
        kwargs.setdefault("soft_wrap", True)
        super().__init__(text.replace("\r\n", "\n"), **kwargs)
        self.indent_width = 2
        self.mode = Mode.NORMAL
        self._start_in_insert = start_in_insert
        self._pending: list[str] = []
        self._register: tuple[str, bool] = ("", False)
        self._undo: list[tuple[str, int]] = []
        self._redo: list[tuple[str, int]] = []
        self._want_col: int | None = None
        self._last_find: tuple[str, str] | None = None
        self._last_search: tuple[str, bool] | None = None
        self._last_change: list[str] = []
        self._recording: list[str] | None = None
        self._replaying = False
        self._anchor = 0
        self._vcursor = 0
        self._cmd_prefix = ":"
        self._cmd_text = ""
        self._cmd_return = Mode.NORMAL
        self._message = ""
        self._message_is_error = False

    # ------------------------------------------------------------ lifecycle

    def on_mount(self) -> None:
        if self._start_in_insert:
            self._set_mode(Mode.INSERT)
        self._refresh_status()

    def set_text(self, text: str) -> None:
        """Replace the whole document and reset Vim state."""
        self.load_text(text.replace("\r\n", "\n"))
        self._undo.clear()
        self._redo.clear()

    # ----------------------------------------------------------- utilities

    def _buf(self) -> Buffer:
        return Buffer(self.text)

    def _cursor(self, buf: Buffer) -> int:
        if self.mode in (Mode.VISUAL, Mode.VISUAL_LINE):
            return self._vcursor
        row, col = self.cursor_location
        return buf.off(row, col)

    def _clamp_normal(self, buf: Buffer, off: int) -> int:
        row, col = buf.loc(off)
        return buf.off(row, min(col, max(0, len(buf.lines[row]) - 1)))

    def _set_cursor(self, off: int, buf: Buffer | None = None, *, insert: bool = False) -> None:
        buf = buf or self._buf()
        off = max(0, min(off, len(buf.s)))
        if not insert:
            off = self._clamp_normal(buf, off)
        self.cursor_location = buf.loc(off)

    def _replace(self, buf: Buffer, a: int, b: int, text: str) -> None:
        self.replace(text, buf.loc(a), buf.loc(b), maintain_selection_offset=False)

    def _snapshot(self) -> None:
        buf = self._buf()
        self._undo.append((buf.s, self._cursor(buf)))
        if len(self._undo) > 500:
            self._undo.pop(0)
        self._redo.clear()

    def _restore(self, src: list, dst: list, count: int) -> None:
        for _ in range(count):
            current = self.text
            while src and src[-1][0] == current:
                src.pop()  # skip no-op snapshots
            if not src:
                break
            text, cursor = src.pop()
            buf = self._buf()
            dst.append((current, self._cursor(buf)))
            self._replace(buf, 0, len(buf.s), text)
            self._set_cursor(cursor)

    def undo(self) -> None:  # type: ignore[override]
        self._restore(self._undo, self._redo, 1)

    def redo(self) -> None:  # type: ignore[override]
        self._restore(self._redo, self._undo, 1)

    def _set_mode(self, mode: Mode) -> None:
        if mode is self.mode:
            return
        self.mode = mode
        for name in ("-insert", "-visual", "-command"):
            self.remove_class(name)
        if mode is Mode.INSERT:
            self.add_class("-insert")
        elif mode in (Mode.VISUAL, Mode.VISUAL_LINE):
            self.add_class("-visual")
        elif mode is Mode.COMMAND:
            self.add_class("-command")
        self.post_message(self.ModeChanged(self, mode))

    def echo(self, message: str, *, error: bool = False) -> None:
        """Show a message in the status line until the next key press (like Vim's echo area)."""
        self._message = message
        self._message_is_error = error
        self._refresh_status()

    def _refresh_status(self) -> None:
        if self._message and self.mode is Mode.NORMAL and not self._pending:
            style = "$error" if self._message_is_error else "$foreground"
            self.border_subtitle = Content.styled(self._message, style)
            return
        if self.mode is Mode.COMMAND:
            self.border_subtitle = f"{self._cmd_prefix}{self._cmd_text}▏"
            return
        label = "" if self.mode is Mode.NORMAL else f"-- {self.mode.value} --"
        pending = "".join(self._pending)
        self.border_subtitle = " ".join(p for p in (label, pending) if p) or "NORMAL"

    def _copy_to_register(self, text: str, linewise: bool, *, yank: bool = False) -> None:
        self._register = (text, linewise)
        if yank:
            try:
                self.app.copy_to_clipboard(text)
            except Exception:
                pass

    # ------------------------------------------------------------- events

    async def _on_key(self, event: events.Key) -> None:
        self._restart_blink()
        if self.read_only:
            return
        if self._dispatch(key_name(event)):
            event.stop()
            event.prevent_default()

    async def _on_mouse_down(self, event: events.MouseDown) -> None:
        if self.mode in (Mode.VISUAL, Mode.VISUAL_LINE, Mode.COMMAND):
            self._set_mode(Mode.NORMAL)
            self._pending = []
            self._refresh_status()
        await super()._on_mouse_down(event)

    async def _on_paste(self, event: events.Paste) -> None:
        if self.mode is not Mode.INSERT:
            self._snapshot()
        await super()._on_paste(event)

    def action_undo(self) -> None:
        self.undo()

    def action_redo(self) -> None:
        self.redo()

    def _dispatch(self, key: str) -> bool:
        """Feed a single key into the state machine. Returns True if consumed."""
        self._message = ""
        if self.mode is Mode.INSERT:
            handled = self._insert_key(key)
        elif self.mode is Mode.COMMAND:
            handled = self._cmdline_key(key)
        else:
            handled = self._normal_key(key)
        self._refresh_status()
        return handled

    # ---------------------------------------------------------- insert mode

    def _enter_insert(self, off: int) -> None:
        self._set_mode(Mode.INSERT)
        self._set_cursor(off, insert=True)

    def _insert_key(self, key: str) -> bool:
        buf = self._buf()
        start, end = self.selection
        a, b = sorted((buf.off(*start), buf.off(*end)))
        cur = buf.off(*end)
        row, col = buf.loc(cur)
        if key in ("<esc>", "<C-c>"):
            if self._recording is not None:
                self._last_change = self._recording + ["<esc>"]
                self._recording = None
            self._set_mode(Mode.NORMAL)
            self._set_cursor(cur - 1 if col > 0 else cur, buf)
            self._want_col = None
            return True
        if key == "<cr>":
            line = buf.lines[row]
            indent = line[: len(line) - len(line.lstrip(" \t"))]
            rest = line.lstrip(" \t")
            bullet = next((p for p in BULLETS if rest.startswith(p)), "")
            if bullet and rest.strip() == bullet.strip() and col == len(line):
                # Enter on an empty bullet ends the list.
                self._replace(buf, buf.line_start(row), buf.line_end(row), "")
                self._set_cursor(buf.line_start(row), insert=True)
            else:
                if bullet.startswith("- [x]"):
                    bullet = "- [ ] "
                insertion = "\n" + indent + bullet
                self._replace(buf, a, b, insertion)
                self._set_cursor(a + len(insertion), insert=True)
        elif key == "<bs>":
            if a != b:
                self._replace(buf, a, b, "")
                self._set_cursor(a, insert=True)
            elif cur > 0:
                self._replace(buf, cur - 1, cur, "")
                self._set_cursor(cur - 1, insert=True)
        elif key == "<del>":
            if cur < len(buf.s):
                self._replace(buf, cur, cur + 1, "")
                self._set_cursor(cur, insert=True)
        elif key == "<tab>":
            spaces = " " * (self.indent_width - col % self.indent_width)
            self._replace(buf, a, b, spaces)
            self._set_cursor(a + len(spaces), insert=True)
        elif key == "<C-w>":
            if col == 0:
                target = max(0, cur - 1)
            else:
                target = max(prev_word_start(buf.s, cur, False), buf.line_start(row))
            self._replace(buf, target, cur, "")
            self._set_cursor(target, insert=True)
        elif key == "<C-u>":
            fnb = buf.first_nonblank(row)
            target = fnb if cur > fnb else buf.line_start(row)
            self._replace(buf, target, cur, "")
            self._set_cursor(target, insert=True)
        elif key in ("<left>", "<right>", "<up>", "<down>", "<home>", "<end>"):
            if key == "<left>":
                target = max(buf.line_start(row), cur - 1)
            elif key == "<right>":
                target = min(buf.line_end(row), cur + 1)
            elif key == "<home>":
                target = buf.line_start(row)
            elif key == "<end>":
                target = buf.line_end(row)
            else:
                new_row = max(0, min(buf.last_row(), row + (1 if key == "<down>" else -1)))
                target = buf.off(new_row, min(col, len(buf.lines[new_row])))
            self._set_cursor(target, buf, insert=True)
        elif len(key) == 1:
            self._replace(buf, a, b, key)
            self._set_cursor(a + 1, insert=True)
        else:
            return False
        if self._recording is not None:
            self._recording.append(key)
        return True

    # -------------------------------------------------------- command line

    def _enter_cmdline(self, prefix: str) -> None:
        self._cmd_return = self.mode
        self._cmd_prefix = prefix
        self._cmd_text = ""
        self._set_mode(Mode.COMMAND)

    def _cmdline_key(self, key: str) -> bool:
        if key in ("<esc>", "<C-c>") or (key == "<bs>" and not self._cmd_text):
            self._leave_cmdline()
        elif key == "<bs>":
            self._cmd_text = self._cmd_text[:-1]
        elif key == "<C-u>":
            self._cmd_text = ""
        elif key == "<cr>":
            prefix, text = self._cmd_prefix, self._cmd_text
            self._leave_cmdline()
            if prefix == ":":
                self._run_ex(text.strip())
            elif text or self._last_search:
                if text:
                    self._last_search = (text, prefix == "/")
                self._search_motion_execute(reverse=False)
        elif len(key) == 1:
            self._cmd_text += key
        elif key.startswith("<C-") or key in ("<tab>", "<shift+tab>"):
            return False  # let app shortcuts (ctrl+s etc.) through
        return True

    def _leave_cmdline(self) -> None:
        mode = self._cmd_return
        self._set_mode(mode if mode in (Mode.VISUAL, Mode.VISUAL_LINE) else Mode.NORMAL)
        if self.mode is not Mode.NORMAL:
            self._show_visual()

    def _run_ex(self, cmd: str) -> None:
        if not cmd:
            return
        if self.mode is not Mode.NORMAL:
            self._exit_visual()
        if cmd.isdigit():
            buf = self._buf()
            row = max(0, min(int(cmd) - 1, buf.last_row()))
            self._set_cursor(buf.first_nonblank(row), buf)
            return
        if cmd in ("noh", "nohl", "nohlsearch"):
            return
        self.post_message(self.Command(self, cmd))

    def _search(self, buf: Buffer, start: int, pattern: str, forward: bool) -> int | None:
        hay = buf.s if any(c.isupper() for c in pattern) else buf.s.lower()
        needle = pattern if any(c.isupper() for c in pattern) else pattern.lower()
        if forward:
            pos = hay.find(needle, start + 1)
            if pos == -1:
                pos = hay.find(needle)
        else:
            pos = hay.rfind(needle, 0, max(0, start))
            if pos == -1:
                pos = hay.rfind(needle)
        return None if pos == -1 else pos

    def _search_motion_execute(self, reverse: bool) -> None:
        buf = self._buf()
        target = self._search_target(buf, self._cursor(buf), 1, reverse)
        if target is None:
            self.notify(f"Pattern not found: {self._last_search[0] if self._last_search else ''}", severity="warning")
            return
        self._move_to(buf, target)

    def _search_target(self, buf: Buffer, cur: int, n: int, reverse: bool) -> int | None:
        if not self._last_search:
            return None
        pattern, forward = self._last_search
        if reverse:
            forward = not forward
        pos: int | None = cur
        for _ in range(n):
            pos = self._search(buf, pos, pattern, forward)
            if pos is None:
                return None
        return pos

    # ---------------------------------------------------------- normal mode

    def _normal_key(self, key: str) -> bool:
        if len(key) != 1 and key not in NORMAL_SPECIALS:
            if self._pending:
                return True
            return False  # tab, ctrl+s ... belong to the app
        if key == "<C-c>" and not self._pending and self.mode is Mode.NORMAL:
            self._message = "Type  :q  and press <Enter> to close the note"
            return True
        if key in ("<esc>", "<C-c>") and (self._pending or self.mode is not Mode.NORMAL):
            if not self._pending:
                self._exit_visual()
            self._pending = []
            return True
        self._pending.append(key)
        runner = self._run_normal if self.mode is Mode.NORMAL else self._run_visual
        try:
            result = runner(self._pending)
        except Exception as error:  # never let an editing bug crash the app
            self.log.error(f"vim: {error!r}")
            result = INVALID
        if result is INCOMPLETE:
            return True
        self._pending = []
        return True

    def _move_to(self, buf: Buffer, target: int, vertical: bool = False) -> None:
        if self.mode in (Mode.VISUAL, Mode.VISUAL_LINE):
            self._vcursor = max(0, min(target, max(0, len(buf.s) - 1) if buf.s else 0))
            self._show_visual(buf)
        else:
            self._set_cursor(target, buf)
        if not vertical:
            self._want_col = None

    def _finish_change(self, keys: list[str]) -> None:
        if self._replaying:
            return
        if self.mode is Mode.INSERT:
            self._recording = list(keys)
        else:
            self._last_change = list(keys)

    def _run_normal(self, keys: list[str]):
        buf = self._buf()
        cur = self._cursor(buf)
        count, i = _read_count(keys, 0)
        if i >= len(keys):
            return INCOMPLETE
        n = count or 1
        k = keys[i]
        last = i == len(keys) - 1

        if k in ALIASES and last:
            prefix = [str(count)] if count else []
            return self._run_normal(prefix + ALIASES[k])

        op, j = _read_operator(keys, i)
        if op is INCOMPLETE:
            return INCOMPLETE
        if op:
            count2, j = _read_count(keys, j)
            if j >= len(keys):
                return INCOMPLETE
            n = (count or 1) * (count2 or 1)
            has_count = bool(count or count2)
            k2 = keys[j]
            if k2 == op[-1] and j == len(keys) - 1:
                row = buf.row(cur)
                last_row = min(row + n - 1, buf.last_row())
                self._apply_op(op, buf, buf.line_start(row), buf.line_end(last_row), True, cur)
            elif k2 in ("i", "a") and op:
                if j + 1 >= len(keys):
                    return INCOMPLETE
                if j + 2 != len(keys):
                    return INVALID
                obj = self._text_object(buf, cur, k2, keys[j + 1], n)
                if obj is None:
                    return INVALID
                self._apply_op(op, buf, obj[0], obj[1], obj[2], cur)
            else:
                motion = self._motion(buf, cur, keys, j, n, has_count, op)
                if motion is INCOMPLETE or motion is INVALID:
                    return motion
                target, kind = motion
                if kind == "line":
                    r1, r2 = sorted((buf.row(cur), buf.row(target)))
                    self._apply_op(op, buf, buf.line_start(r1), buf.line_end(r2), True, cur)
                else:
                    a, b = sorted((cur, target))
                    if kind == "incl":
                        b = b + 1 if not (b < len(buf.s) and buf.s[b] == "\n") else b
                    b = min(b, len(buf.s))
                    self._apply_op(op, buf, a, b, False, cur)
            if op != "y":
                self._finish_change(keys)
            return None

        # --- actions
        if k in ("i", "a", "I", "A", "o", "O"):
            if not last:
                return INVALID
            self._snapshot()
            row = buf.row(cur)
            if k == "i":
                self._enter_insert(cur)
            elif k == "a":
                self._enter_insert(min(cur + 1, buf.line_end(row)))
            elif k == "I":
                self._enter_insert(buf.first_nonblank(row))
            elif k == "A":
                self._enter_insert(buf.line_end(row))
            else:
                line = buf.lines[row]
                indent = line[: len(line) - len(line.lstrip(" \t"))]
                if k == "o":
                    rest = line.lstrip(" \t")
                    bullet = next((p for p in BULLETS if rest.startswith(p)), "")
                    if bullet.startswith("- [x]"):
                        bullet = "- [ ] "
                    insertion = "\n" + indent + bullet
                    self._replace(buf, buf.line_end(row), buf.line_end(row), insertion)
                    self._enter_insert(buf.line_end(row) + len(insertion))
                else:
                    self._replace(buf, buf.line_start(row), buf.line_start(row), indent + "\n")
                    self._enter_insert(buf.line_start(row) + len(indent))
            self._finish_change(keys)
            return None
        if k in ("p", "P"):
            if not last:
                return INVALID
            self._put(buf, cur, n, after=k == "p")
            self._finish_change(keys)
            return None
        if k == "J":
            if not last:
                return INVALID
            self._join(buf, buf.row(cur), max(2, n))
            self._finish_change(keys)
            return None
        if k == "r":
            if last:
                return INCOMPLETE
            ch = keys[i + 1]
            if len(ch) != 1:
                return INVALID
            row, col = buf.loc(cur)
            line = buf.lines[row]
            if col + n > len(line):
                return INVALID
            self._snapshot()
            self._replace(buf, cur, cur + n, ch * n)
            self._set_cursor(cur + n - 1)
            self._finish_change(keys)
            return None
        if k == "~":
            if not last:
                return INVALID
            row = buf.row(cur)
            end = min(cur + n, buf.line_end(row))
            if end > cur:
                self._snapshot()
                self._replace(buf, cur, end, buf.s[cur:end].swapcase())
                self._set_cursor(min(end, buf.line_end(row) - 1))
            self._finish_change(keys)
            return None
        if k == "u" and last:
            self._restore(self._undo, self._redo, n)
            return None
        if k == "<C-r>" and last:
            self._restore(self._redo, self._undo, n)
            return None
        if k == "<C-z>" and last:
            self._restore(self._undo, self._redo, n)
            return None
        if k == "." and last:
            if not self._last_change:
                return None
            self._pending = []
            self._replaying = True
            try:
                for _ in range(n):
                    for key in self._last_change:
                        self._dispatch(key)
            finally:
                self._replaying = False
            return None
        if k in ("v", "V") and last:
            self._anchor = self._vcursor = cur
            self._set_mode(Mode.VISUAL if k == "v" else Mode.VISUAL_LINE)
            self._show_visual(buf)
            return None
        if k in (":", "/", "?") and last:
            self._enter_cmdline(k)
            return None
        if k == "z":
            if last:
                return INCOMPLETE
            sub = keys[i + 1]
            row = buf.row(cur)
            height = self.scrollable_content_region.height
            if sub == "z":
                self.scroll_to(y=max(0, row - height // 2), animate=False)
            elif sub == "t":
                self.scroll_to(y=row, animate=False)
            elif sub == "b":
                self.scroll_to(y=max(0, row - height + 1), animate=False)
            return None
        if k == "Z":
            if last:
                return INCOMPLETE
            sub = keys[i + 1]
            if sub == "Z":
                self.post_message(self.Command(self, "x"))
            elif sub == "Q":
                self.post_message(self.Command(self, "q!"))
            return None
        if k in ("<C-e>", "<C-y>") and last:
            self.scroll_relative(y=n if k == "<C-e>" else -n, animate=False)
            return None

        motion = self._motion(buf, cur, keys, i, n, bool(count), None)
        if motion is INCOMPLETE or motion is INVALID:
            return motion
        target, kind = motion
        self._move_to(buf, target, vertical=keys[i] in VERTICAL)
        return None

    # -------------------------------------------------------------- motions

    def _motion(self, buf: Buffer, cur: int, keys: list[str], i: int, n: int, has_count: bool, op):
        """Return ``(target, kind)`` with kind in ``excl``/``incl``/``line``."""
        s = buf.s
        k = keys[i]
        rest = len(keys) - i - 1
        row, col = buf.loc(cur)
        line_len = len(buf.lines[row])

        def done(target: int, kind: str, used: int = 1):
            if rest != used - 1:
                return INVALID
            return target, kind

        if k in ("h", "<left>", "<bs>"):
            return done(max(buf.line_start(row), cur - n), "excl")
        if k in ("l", "<right>", " "):
            limit = line_len if op else max(0, line_len - 1)
            return done(buf.off(row, min(col + n, limit)), "excl")
        if k in VERTICAL:
            height = max(1, self.scrollable_content_region.height)
            step = {"<C-d>": height // 2, "<C-u>": height // 2, "<C-f>": height, "<C-b>": height}.get(k, n)
            down = k in ("j", "<down>", "<C-n>", "<C-j>", "<C-d>", "<C-f>")
            new_row = max(0, min(buf.last_row(), row + (step if down else -step)))
            if self._want_col is None:
                self._want_col = col
            new_len = len(buf.lines[new_row])
            new_col = min(self._want_col, new_len if op else max(0, new_len - 1))
            if k in ("<C-d>", "<C-u>", "<C-f>", "<C-b>"):
                self.scroll_relative(y=step if down else -step, animate=False)
            return done(buf.off(new_row, new_col), "line")
        if k in ("+", "<cr>", "-"):
            new_row = max(0, min(buf.last_row(), row + (n if k != "-" else -n)))
            return done(buf.first_nonblank(new_row), "line")
        if k in ("0", "<home>"):
            return done(buf.line_start(row), "excl")
        if k == "^":
            return done(buf.first_nonblank(row), "excl")
        if k == "_":
            return done(buf.first_nonblank(min(buf.last_row(), row + n - 1)), "line")
        if k in ("$", "<end>"):
            new_row = min(buf.last_row(), row + n - 1)
            self._want_col = sys.maxsize
            target = buf.line_end(new_row) - (0 if op or not buf.lines[new_row] else 1)
            result = done(max(buf.line_start(new_row), target), "incl" if not op else "excl")
            return result
        if k == "|":
            return done(buf.off(row, min(n - 1, max(0, line_len - 1))), "excl")
        if k in ("w", "W"):
            big = k == "W"
            if op == "c" and cur < len(s) and not s[cur].isspace():
                # "cw" behaves like "ce" (and changes a lone last char of a word)
                at_word_end = cur + 1 >= len(s) or _cls(s[cur + 1], big) != _cls(s[cur], big)
                target = cur
                for step in range(n):
                    if not (step == 0 and at_word_end):
                        target = word_end(s, target, big)
                return done(target, "incl")
            target = prev = cur
            for _ in range(n):
                prev = target
                target = next_word_start(s, target, big)
            if op:
                # an operator never lets "w" swallow the line break of the last word
                newline = s.find("\n", prev, target)
                if newline > cur:
                    target = newline
            elif target >= len(s):
                target = max(0, len(s) - 1)
            return done(target, "excl")
        if k in ("b", "B"):
            target = cur
            for _ in range(n):
                target = prev_word_start(s, target, k == "B")
            return done(target, "excl")
        if k in ("e", "E"):
            target = cur
            for _ in range(n):
                target = word_end(s, target, k == "E")
            return done(target, "incl")
        if k == "g":
            if rest == 0:
                return INCOMPLETE
            sub = keys[i + 1]
            if sub == "g":
                target_row = min(n - 1, buf.last_row()) if has_count else 0
                return done(buf.first_nonblank(target_row), "line", 2)
            if sub in ("e", "E"):
                target = cur
                for _ in range(n):
                    target = prev_word_end(s, target, sub == "E")
                return done(target, "incl", 2)
            if sub == "_":
                new_row = min(buf.last_row(), row + n - 1)
                line = buf.lines[new_row]
                return done(buf.line_start(new_row) + max(0, len(line.rstrip()) - 1), "incl", 2)
            return INVALID
        if k == "G":
            target_row = min(n - 1, buf.last_row()) if has_count else buf.last_row()
            return done(buf.first_nonblank(target_row), "line")
        if k in ("{", "}"):
            r = row
            step = 1 if k == "}" else -1
            edge = buf.last_row() if k == "}" else 0
            for _ in range(n):
                while r != edge and buf.is_blank(r):
                    r += step
                while r != edge and not buf.is_blank(r):
                    r += step
            if not buf.is_blank(r) and k == "}":
                return done(buf.line_end(r) if op else max(buf.line_start(r), buf.line_end(r) - 1), "excl")
            return done(buf.line_start(r), "excl")
        if k in ("f", "F", "t", "T"):
            if rest == 0:
                return INCOMPLETE
            ch = keys[i + 1]
            if len(ch) != 1:
                return INVALID
            self._last_find = (k, ch)
            target = self._find_char(buf, cur, k, ch, n)
            if target is None:
                return INVALID
            return done(target, "incl" if k in ("f", "t") else "excl", 2)
        if k in (";", ","):
            if not self._last_find:
                return INVALID
            kind, ch = self._last_find
            if k == ",":
                kind = {"f": "F", "F": "f", "t": "T", "T": "t"}[kind]
            target = self._find_char(buf, cur, kind, ch, n, repeat=True)
            if target is None:
                return INVALID
            return done(target, "incl" if kind in ("f", "t") else "excl")
        if k == "%":
            target = self._match_bracket(s, cur, buf.line_end(row))
            if target is None:
                return INVALID
            return done(target, "incl")
        if k in ("n", "N"):
            target = self._search_target(buf, cur, n, reverse=k == "N")
            if target is None:
                return INVALID
            return done(target, "excl")
        return INVALID

    def _find_char(self, buf: Buffer, cur: int, kind: str, ch: str, n: int, repeat: bool = False) -> int | None:
        row, col = buf.loc(cur)
        line = buf.lines[row]
        pos = col
        for step in range(n):
            if kind in ("f", "t"):
                start = pos + 1
                if kind == "t" and (repeat or step) and pos + 1 < len(line) and line[pos + 1] == ch:
                    start = pos + 2
                found = line.find(ch, start)
                if found == -1:
                    return None
                pos = found
            else:
                end = pos
                if kind == "T" and (repeat or step) and pos - 1 >= 0 and line[pos - 1] == ch:
                    end = pos - 1
                found = line.rfind(ch, 0, end)
                if found == -1:
                    return None
                pos = found
            if kind == "t":
                pos_out = pos - 1
            elif kind == "T":
                pos_out = pos + 1
            else:
                pos_out = pos
        return buf.off(row, pos_out)

    def _match_bracket(self, s: str, cur: int, line_end: int) -> int | None:
        i = cur
        while i < line_end and s[i] not in BRACKET_PAIRS:
            i += 1
        if i >= len(s) or s[i] not in BRACKET_PAIRS:
            return None
        ch = s[i]
        other = BRACKET_PAIRS[ch]
        step = 1 if ch in "([{" else -1
        depth = 0
        j = i
        while 0 <= j < len(s):
            if s[j] == ch:
                depth += 1
            elif s[j] == other:
                depth -= 1
                if depth == 0:
                    return j
            j += step
        return None

    # --------------------------------------------------------- text objects

    def _text_object(self, buf: Buffer, cur: int, ia: str, ch: str, n: int):
        s = buf.s
        row = buf.row(cur)
        ls, le = buf.line_start(row), buf.line_end(row)
        if ch in ("w", "W"):
            if ls == le:
                return None
            big = ch == "W"
            cur = min(cur, le - 1)
            c = _cls(s[cur], big)
            a = cur
            while a > ls and _cls(s[a - 1], big) == c:
                a -= 1
            b = cur + 1
            while b < le and _cls(s[b], big) == c:
                b += 1
            for _ in range(n - 1):
                if b >= le:
                    break
                c2 = _cls(s[b], big)
                while b < le and _cls(s[b], big) == c2:
                    b += 1
            if ia == "a":
                if c == 0:
                    if b < le:
                        c2 = _cls(s[b], big)
                        while b < le and _cls(s[b], big) == c2:
                            b += 1
                elif b < le and s[b] in " \t":
                    while b < le and s[b] in " \t":
                        b += 1
                else:
                    while a > ls and s[a - 1] in " \t":
                        a -= 1
            return a, b, False
        if ch in ('"', "'", "`"):
            line = buf.lines[row]
            col = cur - ls
            positions = [p for p, c in enumerate(line) if c == ch and (p == 0 or line[p - 1] != "\\")]
            pairs = list(zip(positions[0::2], positions[1::2]))
            chosen = next((p for p in pairs if p[0] <= col <= p[1]), None)
            chosen = chosen or next((p for p in pairs if p[0] > col), None)
            if not chosen:
                return None
            p, q = chosen
            if ia == "i":
                return ls + p + 1, ls + q, False
            end = q + 1
            while end < len(line) and line[end] in " \t":
                end += 1
            return ls + p, ls + end, False
        if ch in OPEN_CLOSE:
            o, c = OPEN_CLOSE[ch]
            start = self._find_open(s, min(cur, len(s) - 1), o, c)
            for _ in range(n - 1):
                if start is None or start == 0:
                    break
                start = self._find_open(s, start - 1, o, c)
            if start is None:
                return None
            depth = 0
            end = None
            for j in range(start, len(s)):
                if s[j] == o:
                    depth += 1
                elif s[j] == c:
                    depth -= 1
                    if depth == 0:
                        end = j
                        break
            if end is None:
                return None
            if ia == "a":
                return start, end + 1, False
            a, b = start + 1, end
            if a < len(s) and s[a] == "\n":
                a += 1
                close_row = buf.row(end)
                if not s[buf.line_start(close_row):end].strip():
                    b = buf.line_start(close_row)
            return a, max(a, b), False
        if ch == "p":
            blank = buf.is_blank(row)
            r1 = r2 = row
            while r1 > 0 and buf.is_blank(r1 - 1) == blank:
                r1 -= 1
            while r2 < buf.last_row() and buf.is_blank(r2 + 1) == blank:
                r2 += 1
            if ia == "a":
                while r2 < buf.last_row() and buf.is_blank(r2 + 1) != blank:
                    r2 += 1
            return buf.line_start(r1), buf.line_end(r2), True
        return None

    @staticmethod
    def _find_open(s: str, pos: int, o: str, c: str) -> int | None:
        depth = 0
        i = pos
        while i >= 0:
            if s[i] == c and i != pos:
                depth += 1
            elif s[i] == o:
                if depth == 0:
                    return i
                depth -= 1
            i -= 1
        return None

    # ------------------------------------------------------------ operators

    def _apply_op(self, op: str, buf: Buffer, a: int, b: int, linewise: bool, cur: int) -> None:
        s = buf.s
        if linewise:
            r1, r2 = buf.row(a), buf.row(b)
            text = s[a:b] + "\n"
        else:
            r1, r2 = buf.row(a), buf.row(max(a, b - 1))
            text = s[a:b]
        if op == "y":
            self._copy_to_register(text, linewise, yank=True)
            if linewise:
                self._set_cursor(cur if r1 <= buf.row(cur) <= r2 else a, buf)
            else:
                self._set_cursor(a, buf)
            return
        if op in ("d", "c"):
            self._snapshot()
            self._copy_to_register(text, linewise)
            if op == "c":
                if linewise:
                    line = buf.lines[r1]
                    indent = line[: len(line) - len(line.lstrip(" \t"))]
                    self._replace(buf, a, b, indent)
                    self._enter_insert(a + len(indent))
                else:
                    self._replace(buf, a, b, "")
                    self._enter_insert(a)
                return
            if linewise:
                if b < len(s):
                    self._replace(buf, a, b + 1, "")
                elif a > 0:
                    self._replace(buf, a - 1, b, "")
                else:
                    self._replace(buf, a, b, "")
                new = self._buf()
                self._set_cursor(new.first_nonblank(min(r1, new.last_row())), new)
            else:
                self._replace(buf, a, b, "")
                self._set_cursor(a)
            return
        if op in (">", "<"):
            self._snapshot()
            unit = " " * self.indent_width
            lines = buf.lines[r1 : r2 + 1]
            if op == ">":
                new_lines = [unit + line if line else line for line in lines]
            else:
                new_lines = []
                for line in lines:
                    strip = len(line) - len(line.lstrip(" "))
                    new_lines.append(line[min(strip, self.indent_width):])
            self._replace(buf, buf.line_start(r1), buf.line_end(r2), "\n".join(new_lines))
            new = self._buf()
            self._set_cursor(new.first_nonblank(r1), new)
            return
        if op in ("g~", "gu", "gU"):
            if linewise:
                b = buf.line_end(r2)
            self._snapshot()
            chunk = s[a:b]
            chunk = {"g~": chunk.swapcase, "gu": chunk.lower, "gU": chunk.upper}[op]()
            self._replace(buf, a, b, chunk)
            self._set_cursor(a)

    def _put(self, buf: Buffer, cur: int, n: int, after: bool) -> None:
        text, linewise = self._register
        if not text:
            return
        self._snapshot()
        row = buf.row(cur)
        if linewise:
            body = text * n
            if after:
                at = buf.line_end(row)
                self._replace(buf, at, at, "\n" + body[:-1])
                new = self._buf()
                self._set_cursor(new.first_nonblank(row + 1), new)
            else:
                at = buf.line_start(row)
                self._replace(buf, at, at, body)
                new = self._buf()
                self._set_cursor(new.first_nonblank(row), new)
        else:
            body = text * n
            at = min(cur + 1, buf.line_end(row)) if after and buf.lines[row] else cur
            self._replace(buf, at, at, body)
            self._set_cursor(at + len(body) - 1)

    def _join(self, buf: Buffer, row: int, count: int) -> None:
        last = min(buf.last_row(), row + count - 1)
        if last == row:
            return
        self._snapshot()
        joined = buf.lines[row].rstrip()
        join_col = len(joined)
        for r in range(row + 1, last + 1):
            nxt = buf.lines[r].lstrip()
            join_col = len(joined)
            if nxt and joined and not nxt.startswith(")"):
                joined += " " + nxt
            else:
                joined += nxt
        self._replace(buf, buf.line_start(row), buf.line_end(last), joined)
        self._set_cursor(buf.line_start(row) + join_col)

    # --------------------------------------------------------- visual mode

    def _visual_range(self, buf: Buffer) -> tuple[int, int, bool]:
        a, c = sorted((self._anchor, self._vcursor))
        if self.mode is Mode.VISUAL_LINE:
            return buf.line_start(buf.row(a)), buf.line_end(buf.row(c)), True
        return a, min(c + 1, len(buf.s)), False

    def _show_visual(self, buf: Buffer | None = None) -> None:
        buf = buf or self._buf()
        anchor, cursor = self._anchor, self._vcursor
        if self.mode is Mode.VISUAL_LINE:
            ar, cr = buf.row(anchor), buf.row(cursor)
            if cr >= ar:
                self.selection = Selection((ar, 0), (cr, len(buf.lines[cr])))
            else:
                self.selection = Selection((ar, len(buf.lines[ar])), (cr, 0))
        elif cursor >= anchor:
            self.selection = Selection(buf.loc(anchor), buf.loc(cursor))
        else:
            self.selection = Selection(buf.loc(min(anchor + 1, len(buf.s))), buf.loc(cursor))

    def _exit_visual(self, cursor: int | None = None) -> None:
        if self.mode not in (Mode.VISUAL, Mode.VISUAL_LINE):
            return
        target = self._vcursor if cursor is None else cursor
        self._set_mode(Mode.NORMAL)
        self._set_cursor(target)

    def _run_visual(self, keys: list[str]):
        buf = self._buf()
        cur = self._vcursor
        count, i = _read_count(keys, 0)
        if i >= len(keys):
            return INCOMPLETE
        n = count or 1
        k = keys[i]
        last = i == len(keys) - 1
        a, b, linewise = self._visual_range(buf)

        if k in ("v", "V") and last:
            target = Mode.VISUAL if k == "v" else Mode.VISUAL_LINE
            if self.mode is target:
                self._exit_visual()
            else:
                self._set_mode(target)
                self._show_visual(buf)
            return None
        if k == "o" and last:
            self._anchor, self._vcursor = self._vcursor, self._anchor
            self._show_visual(buf)
            return None
        if k in (":", "/", "?") and last:
            self._enter_cmdline(k)
            return None
        if k in ("X", "D", "Y", "C", "S", "R") and last:
            r1, r2 = buf.row(a), buf.row(max(a, b - 1))
            op = {"X": "d", "D": "d", "Y": "y", "C": "c", "S": "c", "R": "c"}[k]
            self._set_mode(Mode.NORMAL)
            self._apply_op(op, buf, buf.line_start(r1), buf.line_end(r2), True, a)
            return None
        ops = {"d": "d", "x": "d", "<del>": "d", "y": "y", "c": "c", "s": "c",
               ">": ">", "<": "<", "~": "g~", "u": "gu", "U": "gU"}
        if k in ops and last:
            self._set_mode(Mode.NORMAL)
            self._apply_op(ops[k], buf, a, b, linewise, a)
            return None
        if k == "J" and last:
            self._set_mode(Mode.NORMAL)
            r1, r2 = buf.row(a), buf.row(max(a, b - 1))
            self._join(buf, r1, max(2, r2 - r1 + 1))
            return None
        if k in ("p", "P") and last:
            text, reg_linewise = self._register
            self._set_mode(Mode.NORMAL)
            self._snapshot()
            if linewise and not reg_linewise:
                text = text + "\n"
            if reg_linewise and not linewise:
                text = "\n" + text
            if linewise:
                text = text[:-1] if text.endswith("\n") else text
            self._copy_to_register(buf.s[a:b] + ("\n" if linewise else ""), linewise)
            self._replace(buf, a, b, text)
            self._set_cursor(a)
            return None
        if k == "r":
            if last:
                return INCOMPLETE
            ch = keys[i + 1]
            if len(ch) != 1:
                return INVALID
            self._set_mode(Mode.NORMAL)
            self._snapshot()
            chunk = "".join(c if c == "\n" else ch for c in buf.s[a:b])
            self._replace(buf, a, b, chunk)
            self._set_cursor(a)
            return None
        if k in ("i", "a"):
            if last:
                return INCOMPLETE
            obj = self._text_object(buf, cur, k, keys[i + 1], n)
            if obj is None:
                return INVALID
            oa, ob, obj_linewise = obj
            if obj_linewise and self.mode is Mode.VISUAL:
                self._set_mode(Mode.VISUAL_LINE)
            self._anchor = min(oa, self._anchor) if self._anchor != self._vcursor else oa
            self._vcursor = max(oa, ob - 1)
            self._show_visual(buf)
            return None
        motion = self._motion(buf, cur, keys, i, n, bool(count), None)
        if motion is INCOMPLETE or motion is INVALID:
            return motion
        target, kind = motion
        self._move_to(buf, target, vertical=k in VERTICAL)
        return None
