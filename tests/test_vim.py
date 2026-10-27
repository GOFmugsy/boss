import re

import pytest
from textual.app import App, ComposeResult

from boss.vim import Mode, VimTextArea

TOKEN = re.compile(r"<[^>]+>|.", re.S)


class VimApp(App):
    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text
        self.commands: list[str] = []

    def compose(self) -> ComposeResult:
        yield VimTextArea(self.text, id="ed")

    def on_vim_text_area_command(self, message: VimTextArea.Command) -> None:
        self.commands.append(message.command)


def feed(area: VimTextArea, keys: str) -> None:
    for key in TOKEN.findall(keys):
        area._dispatch(key)


CASES = [
    # (initial text, cursor (row, col), keys, expected text, expected cursor or None)
    ("hello world", (0, 0), "dw", "world", (0, 0)),
    ("hello world", (0, 0), "cwbye<esc>", "bye world", (0, 2)),
    ("hello world", (0, 0), "x", "ello world", (0, 0)),
    ("hello world", (0, 0), "3x", "lo world", None),
    ("hello world", (0, 0), "D", "", None),
    ("hello world", (0, 6), "d$", "hello ", (0, 5)),
    ("hello world", (0, 0), "$", "hello world", (0, 10)),
    ("hello world", (0, 10), "0", "hello world", (0, 0)),
    ("  indented", (0, 9), "^", "  indented", (0, 2)),
    ("one two three", (0, 0), "2w", "one two three", (0, 8)),
    ("one two three", (0, 0), "e", "one two three", (0, 2)),
    ("one two three", (0, 12), "b", "one two three", (0, 8)),
    ("one two three", (0, 0), "d2w", "three", None),
    ("foo bar\nbaz", (0, 4), "dw", "foo \nbaz", None),
    ("a\nb\nc", (0, 0), "dd", "b\nc", (0, 0)),
    ("a\nb\nc", (2, 0), "dd", "a\nb", (1, 0)),
    ("a\nb\nc", (0, 0), "2dd", "c", None),
    ("a\nb\nc", (0, 0), "dj", "c", None),
    ("a\nb\nc", (1, 0), "yyp", "a\nb\nb\nc", (2, 0)),
    ("a\nb\nc", (1, 0), "yyP", "a\nb\nb\nc", (1, 0)),
    ("a\nb\nc", (0, 0), "ddp", "b\na\nc", (1, 0)),
    ("abc", (0, 0), "xp", "bac", (0, 1)),
    ("a\nb\nc", (0, 0), "G", "a\nb\nc", (2, 0)),
    ("a\nb\nc", (2, 0), "gg", "a\nb\nc", (0, 0)),
    ("a\nb\nc", (0, 0), "2G", "a\nb\nc", (1, 0)),
    ("a\nb\nc", (0, 0), "dG", "", None),
    ("say (hello there) ok", (0, 7), "di(", "say () ok", None),
    ("say (hello there) ok", (0, 7), "da(", "say  ok", None),
    ('x = "quoted text" y', (0, 8), 'ci"new<esc>', 'x = "new" y', None),
    ('x = "quoted text" y', (0, 0), 'di"', 'x = "" y', None),
    ("foo bar baz", (0, 5), "diw", "foo  baz", None),
    ("foo bar baz", (0, 5), "daw", "foo baz", None),
    ("foo bar baz", (0, 5), "yiwP", "foo barbar baz", None),
    ("hello", (0, 0), "~~", "HEllo", (0, 2)),
    ("hello world", (0, 0), "gUiw", "HELLO world", None),
    ("hello world", (0, 0), "gUU", "HELLO WORLD", None),
    ("HELLO", (0, 0), "guu", "hello", None),
    ("hello", (0, 0), "rj", "jello", None),
    ("a,b,c", (0, 0), "f,", "a,b,c", (0, 1)),
    ("a,b,c", (0, 0), "f,;", "a,b,c", (0, 3)),
    ("a,b,c", (0, 0), "t,", "a,b,c", (0, 0)),
    ("a,b,c", (0, 0), "dt,", ",b,c", None),
    ("a,b,c", (0, 0), "df,", "b,c", None),
    ("a,b,c", (0, 4), "F,", "a,b,c", (0, 3)),
    ("a,b,c", (0, 0), "2f,", "a,b,c", (0, 3)),
    ("a,b,c", (0, 0), "ct,X<esc>", "X,b,c", None),
    ("line one\nline two", (0, 0), "J", "line one line two", (0, 8)),
    ("x", (0, 0), "ohello<esc>", "x\nhello", (1, 4)),
    ("x", (0, 0), "Ohello<esc>", "hello\nx", (0, 4)),
    ("abc", (0, 1), "ix<esc>", "axbc", (0, 1)),
    ("abc", (0, 1), "ax<esc>", "abxc", (0, 2)),
    ("  abc", (0, 4), "Ix<esc>", "  xabc", None),
    ("abc", (0, 0), "Ax<esc>", "abcx", None),
    ("abc def", (0, 4), "Cxyz<esc>", "abc xyz", None),
    ("abc def", (0, 4), "Sxyz<esc>", "xyz", None),
    ("abc", (0, 0), "sX<esc>", "Xbc", None),
    ("hello", (0, 0), "xu", "hello", None),
    ("hello", (0, 0), "xxuu<C-r>", "ello", None),
    ("hello", (0, 0), "ciwbye<esc>u", "hello", None),
    ("a b c d", (0, 0), "dw..", "d", None),
    ("a b c d", (0, 0), "dw2.", "d", None),
    ("foo foo foo", (0, 0), "cwbar<esc>w.w.", "bar bar bar", None),
    ("abc", (0, 0), ">>", "  abc", None),
    ("    abc", (0, 0), "<<", "  abc", None),
    ("a\nb", (0, 0), ">j", "  a\n  b", None),
    ("hello world", (0, 0), "vd", "ello world", None),
    ("hello world", (0, 0), "vey", "hello world", (0, 0)),
    ("hello world", (0, 0), "veyP", "hellohello world", None),
    ("hello world", (0, 0), "vlld", "lo world", None),
    ("hello world", (0, 0), "veU", "HELLO world", None),
    ("a\nb\nc", (0, 0), "Vjd", "c", None),
    ("a\nb\nc", (0, 0), "Vjy", "a\nb\nc", None),
    ("a\nb\nc", (1, 0), "Vy<esc>P", "a\nb\nb\nc", None),
    ("hello world", (0, 3), "viwd", " world", None),
    ("hello world", (0, 0), "vecbye<esc>", "bye world", None),
    ("p1\np1\n\np2", (0, 0), "}", "p1\np1\n\np2", (2, 0)),
    ("p1\np1\n\np2", (3, 0), "{", "p1\np1\n\np2", (2, 0)),
    ("p1\np1\n\np2", (0, 0), "dap", "p2", None),
    ("f(a(b)c)", (0, 1), "%", "f(a(b)c)", (0, 7)),
    ("find me here, find", (0, 0), "/find<cr>", "find me here, find", (0, 14)),
    ("find me here, find", (0, 0), "/find<cr>n", "find me here, find", (0, 0)),
    ("abc def", (0, 0), "/def<cr>D", "abc ", None),
    ("- item", (0, 6), "a<cr>next<esc>", "- item\n- next", None),
    ("- item", (0, 6), "a<cr><cr>done<esc>", "- item\ndone", None),
    ("- item", (0, 0), "onext<esc>", "- item\n- next", None),
    ("one two", (0, 7), "a<C-w>x<esc>", "one x", None),
    ("hello", (0, 0), "2yl$p", "hellohe", None),
    ("ab\ncd", (0, 1), "jk", "ab\ncd", (0, 1)),
    ("abcdef\nx\nabcdef", (0, 4), "jj", "abcdef\nx\nabcdef", (2, 4)),
    ("a b", (0, 0), "3dw", "", None),
    ("", (0, 0), "dd", "", None),
    ("", (0, 0), "x", "", None),
    ("", (0, 0), "p", "", None),
    ("hello", (0, 0), "d0", "hello", None),
    ("x\n\ny", (1, 0), "D", "x\n\ny", None),
    ("x\n\ny", (1, 0), "x", "x\n\ny", None),
]


@pytest.mark.parametrize("text,cursor,keys,expected,expected_cursor", CASES)
async def test_vim_keys(text, cursor, keys, expected, expected_cursor):
    app = VimApp(text)
    async with app.run_test() as pilot:
        area = app.query_one(VimTextArea)
        area.cursor_location = cursor
        feed(area, keys)
        await pilot.pause()
        assert area.text == expected
        if expected_cursor is not None:
            assert area.cursor_location == expected_cursor
        assert area.mode is Mode.NORMAL


async def test_ex_commands_are_posted():
    app = VimApp("x")
    async with app.run_test() as pilot:
        area = app.query_one(VimTextArea)
        feed(area, ":wq<cr>:recent<cr>ZZ")
        await pilot.pause()
        assert app.commands == ["wq", "recent", "x"]


async def test_real_key_events():
    """Drive the widget through Textual's real key pipeline."""
    app = VimApp("hello world")
    async with app.run_test() as pilot:
        area = app.query_one(VimTextArea)
        area.focus()
        await pilot.press("w", "c", "w", "t", "h", "e", "r", "e", "escape", "dollar_sign", "x", "0", "x")
        await pilot.pause()
        assert area.text == "ello ther"
        # backspace / ctrl+w in normal mode must not edit the text
        await pilot.press("backspace", "ctrl+w", "ctrl+u")
        assert area.text == "ello ther"
        await pilot.press("i", "backspace")
        assert area.mode is Mode.INSERT


async def test_start_in_insert():
    app = VimApp("")

    class InsertApp(VimApp):
        def compose(self):
            yield VimTextArea("", start_in_insert=True)

    app = InsertApp("")
    async with app.run_test() as pilot:
        area = app.query_one(VimTextArea)
        area.focus()
        await pilot.press("h", "i", "escape")
        assert area.text == "hi"
        assert area.mode is Mode.NORMAL
