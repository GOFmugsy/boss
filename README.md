# boss

A keyboard-driven terminal app for keeping performance notes on the people you manage.
Leave it running in a terminal tab, jot a note the moment something happens, tag the
people involved, back it up with screenshots as evidence, and export everything as Markdown
when review season comes around.

```
┌ Team ──────────────┐┌ Alice Smith (12) ────────────────────────────────────────────┐
│ All notes       31 ││ When         Kind       Who          Note                 ▣ │
│ Unassigned       2 ││ Today 14:03  + Win      @alice       Ran a great retro…   1 │
│ Evidence        14 ││ Oct 21 10:12 ! Concern  @alice @bob  Missed handoff on…     │
│   Unlinked       3 ││ Oct 20 09:00 ◇ 1:1      @alice       ⊘ Family matter, …     │
│────────────────────│└──────────────────────────────────────────────────────────────┘
│ Alice Smith     12 │┌ Detail ──────────────────────────────────────────────────────┐
│ @alice · Senior Eng││ + Win  Monday October 26 2026, 14:03  Alice Smith (@alice)    │
│ Bob Jones        9 ││ Ran a great retro, everyone left with clear owners.          │
│ @bob               ││ Evidence (1) — o opens, L links more                         │
└────────────────────┘│ ▣ Today 13:58  Retro board with owners  [image preview]      │
```

## Features

- **Quick capture.** Press `n` and start typing. New notes open in insert mode, tagged to
  whoever is selected in the team list.
- **Tag people and topics.** Write `@handle` anywhere in a note to tag a person and
  `#topic` to add a tag. The editor's people field autocompletes handles.
- **Note kinds:** Note, Win, Concern, Feedback given, Goal, 1:1.
- **Screenshots as evidence.** Take screenshots the usual way (⌘⇧4 / ⌘⇧5), then add them
  to boss as evidence: pick them from your screenshots folder (`s`), import files (`i`;
  drag them in from Finder), or paste from the clipboard (`p`). Each piece of evidence has
  its own caption, @people and #tags, and can be **linked to any number of notes** (`L`).
  See [Evidence](#evidence).
- **Private notes with `#hide`.** Tag a note `#hide` and it stays in boss but is left out
  of summaries and exports. See [Hiding notes from summaries](#hiding-notes-from-summaries).
- **Vim editing and Vim saving.** The editor supports modes, motions, operators, text
  objects, counts, visual mode, search, undo/redo and `.` repeat. Saving works the way it
  does in Vim (see [Saving notes](#saving-notes)). Press `?` in the app for the full list.
- **Filter** with `/`: free text, `#tag`, `@handle` and `kind:win` can be combined.
- **Export** the current view to Markdown with `w`, or use `boss export alice`.
- **Live refresh.** Notes added from another terminal or a global hotkey (see below)
  appear in the running app within two seconds.
- Local only: one SQLite file plus an evidence folder.

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
  file globs. Without it, `boss add great job today?` fails with `no matches found`. The
  trade-off: file patterns aren't expanded either. To import several screenshots, drag
  them into the terminal, use `s` in the app, or bypass the alias with
  `command boss evidence add ~/Desktop/Screenshot*.png`.

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
Ghostty, WezTerm and Kitty also show evidence previews inline; Terminal.app shows a
lower-resolution block preview.

### Global hotkey capture

`boss quick` opens a native dialog (AppleScript on macOS, zenity/kdialog on Linux), saves
what you type, and exits. Bind it to a global shortcut and you can capture a note from any
app without switching to the terminal:

```sh
boss quick                  # text only
boss quick --latest         # attach your newest screenshot (take it with ⌘⇧4 first)
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
| `:q!`, `ZQ` | Close and discard unsaved changes, including evidence added since the last `:w` |
| `:e!` | Revert to the last saved version |
| `:up[date]` | Save only if something changed |
| `:w FILE`, `:w! FILE` | Write the note text to a file (`E13` if it exists and you didn't use `!`) |
| `:wq FILE` | Write to the file, save the note, and close |
| `:r FILE` | Insert a text file below the cursor; image files are added as evidence instead |

Abbreviations and the "all" forms work too: `:wri`, `:quit`, `:exi`, `:wa`, `:qa!`,
`:wqa`, `:xa`. The title shows `[+]` while there are unsaved changes, as Vim's status line
does. An empty note can't be written (`E32`).

`ctrl+c` works as it does in Vim. In insert mode it returns to normal mode, and in normal
mode it tells you to type `:q`. Outside the text area (for example in the people field) it
cancels, asking first if there are unsaved changes. `ctrl+s` saves and closes in one
keystroke.

`boss add` without text follows the `git commit` convention with your own vim: `:wq`
saves the note, while `:q!`, `:cq`, or leaving it empty cancels.

## Evidence

boss never takes screenshots itself. Use macOS as usual (⌘⇧4 for a region, ⌘⇧5 for more
options, ⌃⌘⇧4 to copy to the clipboard), then bring the image into boss:

| From | Main screen | Note editor | Command line |
|---|---|---|---|
| Screenshots folder | `s` | `ctrl+e` / `:recent` | `--latest` |
| Any image file (drag from Finder) | `i` | `ctrl+o` / `:attach PATH…` | `-a FILE`, `boss evidence add FILE…` |
| Clipboard | `p` | `ctrl+y` / `:paste` | `--paste` |
| Evidence already in boss | `L` | `ctrl+l` / `:link` | `-E ID`, `boss evidence link ID NOTE` |

The screenshots folder is whatever macOS uses (System Settings, or ⌘⇧5 → Options → Save
to), with the Desktop as the default. Set `BOSS_SCREENSHOTS_DIR` to use another folder. The
picker lists the last 30 days, newest first, with a preview. `space` selects several and
`enter` imports them. Files you've already imported are marked `✓ imported`.

How it fits together:

- **Evidence is copied into boss.** The original screenshot is left where it was.
  Importing the same image twice reuses the existing evidence instead of making a duplicate.
- **Link evidence to notes** in either direction. On a note, `L` picks evidence from the
  library. On a piece of evidence, `L` picks a note, or starts a new one. One screenshot
  can support several notes.
- **Caption and tag evidence** with `e` in the evidence view (or `enter` in the editor's
  evidence list). `@handle` and `#tag` work in captions.
- **`v` switches** between notes and evidence for everyone or for the selected person. A
  person's evidence includes images tagged to them and images linked to their notes.
  **Unlinked** in the sidebar collects evidence that doesn't support a note yet.
- **Importing from the notes view** starts a new note with the images. **Importing from the
  evidence view** adds them to the library, tagged to the selected person, so you can
  caption and link them later.
- Deleting a note keeps its evidence (it just becomes unlinked). Deleting evidence removes
  the file and unlinks it from every note.
- Exports include each note's evidence (image, caption, date). Exporting a person also
  adds an "Other evidence" section for their evidence that isn't linked to an exported note.

## Hiding notes from summaries

`#hide` is a special tag. A note tagged `#hide` (anywhere in its text, or `-t hide` on the
command line) is kept in boss and still appears in the app, marked `⊘`. It is left out of
**summaries**:

- Markdown exports (`w` in the app, `boss export`), including the counts at the top
- evidence that only supports hidden notes, which also stays out of the export's
  "Other evidence" section

Evidence can be hidden on its own by putting `#hide` in its caption. Evidence linked to both
a hidden note and a visible one still appears with the visible note.

Use it for things you need to remember but that don't belong in a review, such as a
personal or medical matter. When an export leaves hidden notes out, the app's message
says how many; the export file itself never mentions them. To find hidden notes, filter
with `/#hide`. To include them again, remove the tag. For a complete backup export, run
`boss export --include-hidden`. `boss list` shows `[hidden]` next to hidden notes.

## Command line

```sh
boss                                        # open the TUI
boss add                                    # write the note in $EDITOR (vim): :wq saves, :q! cancels
boss add '@alice unblocked the release #ownership' -k win
boss add @bob fixed the flaky tests -t quality   # -t adds #quality without quoting
boss add --latest '@bob dashboard mock'     # newest screenshot (⌘⇧4) as evidence
boss add -p '@bob design feedback'          # clipboard image as evidence
boss add -a ~/Desktop/chart.png '@alice Q3 numbers'
boss add -E 7 'follow-up on the outage'     # link evidence #7 that's already in boss
boss add '@alice family emergency, out until Monday' -t hide   # kept out of exports
echo "long note…" | boss add - -m alice     # read the body from stdin
boss list alice --since 2026-07-01          # note ids are shown as #12
boss export alice --since 2026-01-01 -o alice-h1.md   # leaves out #hide notes
boss export alice --include-hidden          # everything, e.g. for a backup

boss evidence add ~/Desktop/timeline.png -m alice -c 'incident timeline'
boss evidence add --latest -n 12            # import and link to note #12
boss evidence list --unlinked               # evidence that doesn't support a note yet
boss evidence link 7 12                     # evidence #7 supports note #12
boss evidence edit 7 -c 'Q3 revenue chart' -t metrics
boss evidence open 7

boss team add carol "Carol Danvers" --role "Staff Engineer"
boss team archive bob                       # removes bob from the team list; notes are kept
boss where                                  # print the data directory
```

## Keys

**Main screen:** `n` new note · `v` notes/evidence · `s` screenshots folder · `i` import
files · `p` clipboard · `L` link · `enter`/`e` edit · `d` delete · `o` open evidence · `/`
filter · `j`/`k` `g`/`G` move · `h`/`l` switch pane · `a` add person · `r` edit person ·
`A` archive/restore · `H` show archived · `w` export · `?` help · `ctrl+q` quit

**Editor:** `:w` save · `:wq` / `:x` / `ZZ` / `ctrl+s` save and close · `:q` close
(refuses if unsaved) · `:q!` discard · `:e!` revert · `ctrl+e` / `:recent` screenshots
folder · `ctrl+o` / `:attach PATH…` files · `ctrl+l` / `:link` library evidence · `ctrl+y`
/ `:paste` clipboard · `tab` from normal mode moves to the people field, kind selector,
and evidence list (`enter` captions, `x` unlinks)

**Vim:** `h j k l w b e W B E ge 0 ^ $ _ | gg G { } f F t T ; , % n N + -`, `ctrl+d/u/f/b`,
operators `d c y > < gu gU g~` with any motion or text object (`iw aw iW aW i" a" i' a'
i( a( i[ a[ i{ a{ i< a< ip ap`), `dd cc yy >> <<`, `i a I A o O x X s S C D Y p P r J ~
u ctrl+r .`, visual `v`/`V`, search `/` `?`, insert-mode `ctrl+w`/`ctrl+u`, and counts
throughout. Markdown bullets (`- `, `* `, `- [ ] `) continue when you press Enter.

## Data

Data lives in `$BOSS_HOME`, defaulting to `~/.local/share/boss`:

```
boss.db          SQLite: members, notes, tags, evidence and links
evidence/        evidence images, by year/month taken
exports/         Markdown exports
```

The directory is created as private to your user (mode 700). Back up this directory and
you have backed up everything. These are sensitive HR notes, so keep it on an encrypted
disk (FileVault/LUKS) and out of synced or shared folders unless that is what you intend.
`#hide` only affects exports; hidden notes are stored like any other note.

Databases from before evidence existed are upgraded automatically: old per-note
attachments become evidence linked to the same notes.

## Platform notes

- **macOS:** the screenshots folder is read from
  `defaults read com.apple.screencapture location` (Desktop if unset). The clipboard uses
  `osascript`, or `pngpaste` if it is installed. `o` opens evidence in Preview. Tools are
  looked up by absolute path, so `boss quick --latest` works from Shortcuts.app or Raycast,
  which run commands with a minimal PATH. boss needs no Screen Recording permission.
- **Linux:** the screenshots folder is `~/Pictures/Screenshots`, `~/Pictures` or
  `~/Desktop`. The clipboard uses `wl-paste` or `xclip`.
- **Inline previews** use the Kitty graphics protocol or Sixel (Kitty, Ghostty, WezTerm,
  iTerm2, recent foot/xterm), and fall back to Unicode blocks elsewhere. Set
  `BOSS_NO_IMAGES=1` to turn previews off.

## Development

```sh
.venv/bin/pip install -e '.[images,dev]'
.venv/bin/pytest
```
