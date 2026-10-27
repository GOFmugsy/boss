# boss

A keyboard-driven terminal app for keeping performance notes on the people you manage.
Leave it running in a terminal tab, jot a note the moment something happens, tag the
people involved, attach screenshots, and export everything as Markdown when review season
comes around.

```
┌ Team ──────────────┐┌ Alice Smith (12) ────────────────────────────────────────────┐
│ All notes       31 ││ When         Kind       Who          Note                    │
│ Unassigned       2 ││ Today 14:03  + Win      @alice       Ran a great retro  #fac… │
│────────────────────││ Oct 21 10:12 ! Concern  @alice @bob  Missed handoff on the…  │
│ Alice Smith     12 │└──────────────────────────────────────────────────────────────┘
│ @alice · Senior Eng│┌ Detail ──────────────────────────────────────────────────────┐
│ Bob Jones        9 ││ + Win  Monday October 26 2026, 14:03  Alice Smith (@alice)    │
│ @bob               ││ Ran a great retro, everyone left with clear owners.          │
└────────────────────┘│ ▣ 20261026-140301-a1b2c3.png  (o to open)  [image preview]   │
```

## Features

- **Quick capture.** Press `n` and start typing. New notes open in insert mode, tagged to
  whoever is selected in the team list.
- **Tag people and topics.** Write `@handle` anywhere in a note to tag a person and
  `#topic` to add a tag. The editor's people field autocompletes handles.
- **Note kinds:** Note, Win, Concern, Feedback given, Goal, 1:1.
- **Screenshots.** Grab a screen region (`s`, or `ctrl+t` in the editor), paste an image
  from the clipboard (`p` / `ctrl+y`), or attach a file (`ctrl+o`; you can drag a file
  into the terminal). Images are copied into the data directory, previewed inline when
  the terminal supports it, and opened in your viewer with `o`.
- **Vim editing and Vim saving.** The editor supports modes, motions, operators, text
  objects, counts, visual mode, search, undo/redo and `.` repeat. Saving works the way it
  does in Vim (see [Saving notes](#saving-notes)). Press `?` in the app for the full list.
- **Filter** with `/`: free text, `#tag`, `@handle` and `kind:win` can be combined.
- **Export** the current view to Markdown with `w`, or use `boss export alice`.
- **Live refresh.** Notes added from another terminal or a global hotkey (see below)
  appear in the running app within two seconds.
- Local only: one SQLite file plus an attachments folder.

## Install (macOS + zsh)

Requires Python 3.10+ (`python3 --version`; `brew install python` if needed).

```zsh
cd ~/repos/boss
./scripts/install-macos.zsh --zshrc     # venv, ~/.local/bin/boss, and two lines in ~/.zshrc
exec zsh                                # reload the shell
```

Without `--zshrc` the script only prints the lines, so you can add them to `~/.zshrc`
yourself:

```zsh
export PATH="$HOME/.local/bin:$PATH"     # only if ~/.local/bin isn't on PATH already
eval "$(boss init zsh)"                  # after compinit, if your .zshrc calls it
```

`boss init zsh` sets up:

- **Tab completion** for commands, options, note kinds, and your team's handles. This
  includes `@handle` inside note text: `boss add @al<Tab>` → `@alice`.
- **`alias boss='noglob boss'`**, so zsh doesn't treat `?`, `*` or `[ ]` in a note as
  file globs. Without it, `boss add great job today?` fails with `no matches found`.

On Linux or another setup: `python3 -m venv .venv && .venv/bin/pip install -e '.[images]'`,
then put `.venv/bin` on your PATH. `pipx install '.[images]'` also works.

### Writing notes from zsh

zsh treats a few characters in note text specially:

| You type | zsh does | Use instead |
|---|---|---|
| `boss add nice work #win` | drops `#win` as a comment (`interactivecomments`) | `boss add nice work -t win` or quote it |
| `boss add "shipped it!"` | history expansion on `!` inside double quotes | single quotes: `boss add 'shipped it!'` |
| `boss add today?` | glob, error "no matches found" | fixed by the `noglob` alias above |
| `boss add @bob's demo` | unmatched quote | `boss add` with no text (opens your editor) |

The simplest way is to run **`boss add`** with no text. It opens `$VISUAL`/`$EDITOR`
(vim by default), the same way `git commit` does, so the shell never sees your note.

## Keeping it running

Run it in a dedicated tmux session so it is always one keystroke away (macOS doesn't
ship tmux: `brew install tmux`):

```zsh
tmux new -As boss boss
```

Or keep it in a pinned tab or window of Terminal, iTerm2, Ghostty or WezTerm. iTerm2,
Ghostty, WezTerm and Kitty also show screenshot previews inline; Terminal.app shows a
lower-resolution block preview.

### Global hotkey capture

`boss quick` opens a native dialog (AppleScript on macOS, zenity/kdialog on Linux), saves
what you type, and exits. Bind it to a global shortcut and you can capture a note from any
app without switching to the terminal:

```sh
boss quick                  # text only
boss quick --shot           # select a screen region first, then type the note
boss quick -k win           # pre-set the note kind
```

On macOS you can do this with a Shortcuts.app "Run Shell Script" action, Raycast, Alfred,
Hammerspoon or skhd. Use the full path printed by `which boss`.

## Saving notes

The editor saves the way Vim does:

| Command | Effect |
|---|---|
| `:w` | Save and keep editing. The note is created on the first `:w`; the status line shows `"note #12" 3L, 120B written` |
| `:wq`, `:x`, `ZZ` | Save and close (`:x`/`ZZ` only write if something changed) |
| `:q` | Close. If anything is unsaved it refuses with `E37: No write since last change (add ! to override)` |
| `:q!`, `ZQ` | Close and discard unsaved changes, including unsaved screenshots |
| `:e!` | Revert to the last saved version |
| `:up[date]` | Save only if something changed |
| `:w FILE`, `:w! FILE` | Write the note text to a file (`E13` if it exists and you didn't use `!`) |
| `:wq FILE` | Write to the file, save the note, and close |
| `:r FILE` | Insert a text file below the cursor; image files are attached instead |

Abbreviations and the "all" forms work too: `:wri`, `:quit`, `:exi`, `:wa`, `:qa!`,
`:wqa`, `:xa`. The title shows `[+]` while there are unsaved changes, as Vim's status line
does. An empty note can't be written (`E32`).

`ctrl+c` works as it does in Vim. In insert mode it returns to normal mode, and in normal
mode it tells you to type `:q`. Outside the text area (for example in the people field) it
cancels, asking first if there are unsaved changes. `ctrl+s` saves and closes in one
keystroke.

`boss add` without text follows the `git commit` convention with your own vim: `:wq`
saves the note, while `:q!`, `:cq`, or leaving it empty cancels.

## Command line

```sh
boss                                        # open the TUI
boss add                                    # write the note in $EDITOR (vim): :wq saves, :q! cancels
boss add '@alice unblocked the release #ownership' -k win
boss add @bob fixed the flaky tests -t quality   # -t adds #quality without quoting
boss add -s '@bob dashboard mock'          # capture a screenshot and attach it
boss add -p '@bob design feedback'          # attach the clipboard image
boss add -a ~/Desktop/chart.png '@alice Q3 numbers'
echo "long note…" | boss add - -m alice     # read the body from stdin
boss list alice --since 2026-07-01
boss export alice --since 2026-01-01 -o alice-h1.md
boss team add carol "Carol Danvers" --role "Staff Engineer"
boss team archive bob                       # hides bob from the list; notes are kept
boss where                                  # print the data directory
```

## Keys

**Main screen:** `n` new · `s` screenshot note · `p` clipboard image note · `enter`/`e`
edit · `d` delete · `o` open images · `/` filter · `j`/`k` `g`/`G` move · `h`/`l` switch
pane · `a` add person · `r` edit person · `A` archive/restore · `H` show archived · `w`
export · `?` help · `ctrl+q` quit

**Editor:** `:w` save · `:wq` / `:x` / `ZZ` / `ctrl+s` save and close · `:q` close
(refuses if unsaved) · `:q!` discard · `:e!` revert · `ctrl+t` / `:shot` screenshot ·
`ctrl+y` / `:paste` clipboard image · `ctrl+o` / `:attach PATH` file · `tab` from normal
mode moves to the people field, kind selector, and attachment list (`x` removes the
highlighted attachment)

**Vim:** `h j k l w b e W B E ge 0 ^ $ _ | gg G { } f F t T ; , % n N + -`, `ctrl+d/u/f/b`,
operators `d c y > < gu gU g~` with any motion or text object (`iw aw iW aW i" a" i' a'
i( a( i[ a[ i{ a{ i< a< ip ap`), `dd cc yy >> <<`, `i a I A o O x X s S C D Y p P r J ~
u ctrl+r .`, visual `v`/`V`, search `/` `?`, insert-mode `ctrl+w`/`ctrl+u`, and counts
throughout. Markdown bullets (`- `, `* `, `- [ ] `) continue when you press Enter.

## Data

Data lives in `$BOSS_HOME`, defaulting to `~/.local/share/boss`:

```
boss.db          SQLite: members, notes, tags, attachment index
attachments/     screenshots, by year/month
exports/         Markdown exports
```

The directory is created as private to your user (mode 700). Back up this directory and you have backed up everything. These are sensitive HR notes,
so keep it on an encrypted disk (FileVault/LUKS) and out of synced or shared folders
unless that is what you intend.

## Platform notes

- **macOS:** screenshots use the built-in `/usr/sbin/screencapture`. Tools are looked up by
  absolute path, so `boss quick --shot` works from Shortcuts.app or Raycast, which run
  commands with a minimal PATH. The first capture needs Screen
  Recording permission for your terminal app (System Settings → Privacy & Security). The
  clipboard uses `osascript`, or `pngpaste` if it is installed.
- **Linux:** screenshots use whichever of `grim`+`slurp`, `flameshot`, `gnome-screenshot`,
  `spectacle`, `maim`, `scrot` or ImageMagick `import` is installed. The clipboard uses
  `wl-paste` or `xclip`.
- **Inline previews** use the Kitty graphics protocol or Sixel (Kitty, Ghostty, WezTerm,
  iTerm2, recent foot/xterm), and fall back to Unicode blocks elsewhere. Set
  `BOSS_NO_IMAGES=1` to turn previews off.

## Development

```sh
.venv/bin/pip install -e '.[images,dev]'
.venv/bin/pytest
```
