"""Command line entry point.

``boss`` with no arguments launches the TUI. Sub-commands allow quick capture from
any shell, script or global hotkey while the TUI keeps running (it refreshes live).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from .desktop import DesktopError, is_image, latest_screenshot, paste_image, prompt_text
from .export import notes_to_markdown
from .store import KIND_LABELS, Store


def _store(args: argparse.Namespace) -> Store:
    return Store(Path(args.home) if args.home else None)


TEMPLATE_MARK = "# boss:"


def edit_in_editor(store: Store, initial: str = "") -> str | None:
    """Write a note in $VISUAL/$EDITOR (vim by default), like ``git commit``.

    Save and quit (``:wq``/``ZZ``) to keep the note. Quitting without saving, leaving it
    empty, or ``:cq`` (non-zero exit) cancels.
    """
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or ("vim" if shutil.which("vim") else "vi")
    team = " ".join(f"@{m.handle}" for m in store.members()) or "(none yet — boss team add HANDLE NAME)"
    template = (
        f"{initial}\n\n"
        f"{TEMPLATE_MARK} Write your note above. @handle tags a person, #word adds a tag.\n"
        f"{TEMPLATE_MARK} Team: {team}\n"
        f"{TEMPLATE_MARK} :wq saves the note · :q! or an empty note cancels · lines like these are removed.\n"
    )
    store.staging_dir.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="note-", suffix=".md", dir=store.staging_dir)  # private (0600)
    path = Path(name)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(template)
        before = path.stat().st_mtime_ns
        code = subprocess.call([*shlex.split(editor), str(path)])
        if code != 0:
            print(f"{editor} exited with status {code}", file=sys.stderr)
            return None
        if path.stat().st_mtime_ns == before:
            return None  # quit without writing (:q / :q!)
        lines = [line for line in path.read_text().splitlines() if not line.startswith(TEMPLATE_MARK)]
        text = "\n".join(lines).strip("\n")
        return text if text.strip() else None
    finally:
        path.unlink(missing_ok=True)


def _with_tags(body: str, tags: list[str] | None) -> str:
    """``-t win`` is a shell-safe way to write ``#win`` (zsh treats an unquoted #word as a comment)."""
    extra = " ".join(f"#{tag.lstrip('#')}" for tag in tags or [] if tag.strip("# "))
    return f"{body.rstrip()} {extra}".strip() if extra else body


def _gather_images(store: Store, args: argparse.Namespace) -> list[tuple[Path, bool]] | None:
    """Images named by --latest / --paste / --attach / FILE arguments as (path, move)."""
    files: list[tuple[Path, bool]] = []
    if getattr(args, "latest", False):
        path = latest_screenshot()
        if path is None:
            print("No recent image in your screenshots folder (take one with ⌘⇧4)", file=sys.stderr)
            return None
        files.append((path, False))
    if getattr(args, "paste", False):
        try:
            path = asyncio.run(paste_image(store.new_staging_path()))
        except DesktopError as error:
            print(error, file=sys.stderr)
            return None
        if path is None:
            print("No image on the clipboard", file=sys.stderr)
            return None
        files.append((path, True))
    for name in [*(getattr(args, "attach", None) or []), *(getattr(args, "files", None) or [])]:
        path = Path(name).expanduser()
        if not path.is_file() or not is_image(path):
            print(f"Not an image file: {path}", file=sys.stderr)
            return None
        files.append((path, False))
    return files


def _save_with_evidence(store: Store, args: argparse.Namespace, body: str) -> int:
    body = _with_tags(body, getattr(args, "tag", None))
    files = _gather_images(store, args)
    if files is None:
        return 1
    existing = []
    for evidence_id in getattr(args, "evidence", None) or []:
        evidence = store.evidence(evidence_id)
        if evidence is None:
            print(f"No evidence #{evidence_id}", file=sys.stderr)
            return 1
        existing.append(evidence)
    if not body.strip() and not files and not existing:
        print("Nothing to save", file=sys.stderr)
        return 1
    note, unknown = store.save_note(body, kind=args.kind, handles=args.member or [])
    for path, move in files:
        evidence, _, _ = store.import_evidence(path, move=move)
        existing.append(evidence)
    for evidence in existing:
        store.link_evidence(note.id, evidence.id)
    who = " ".join(f"@{m.handle}" for m in note.members) or "unassigned"
    extra = f", {len(existing)} evidence" if existing else ""
    print(f"Saved note #{note.id} ({KIND_LABELS[note.kind]}, {who}{extra})")
    if unknown:
        print(f"warning: not on your team: {', '.join('@' + h for h in unknown)}", file=sys.stderr)
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    store = _store(args)
    body = " ".join(args.text)
    if body == "-":
        body = sys.stdin.read()
    elif args.edit or (
        not body and sys.stdin.isatty() and not (args.latest or args.paste or args.attach or args.evidence)
    ):
        edited = edit_in_editor(store, body)
        if edited is None:
            print("Note cancelled — nothing saved", file=sys.stderr)
            return 1
        body = edited
    return _save_with_evidence(store, args, body)


def cmd_quick(args: argparse.Namespace) -> int:
    store = _store(args)
    team = " ".join(f"@{m.handle}" for m in store.members())
    body = prompt_text("boss", f"Quick note — tag people with @handle, topics with #tag.\\n{team}")
    if body is None:
        return 1
    return _save_with_evidence(store, args, body)


def cmd_list(args: argparse.Namespace) -> int:
    store = _store(args)
    member_id = None
    if args.handle:
        member = store.member_by_handle(args.handle)
        if not member:
            print(f"Unknown person @{args.handle}", file=sys.stderr)
            return 1
        member_id = member.id
    notes = store.notes(member_id, query=args.filter or "", since=args.since)[: args.limit]
    for note in notes:
        who = " ".join(f"@{m.handle}" for m in note.members) or "-"
        images = f" [{len(note.evidence)} evidence]" if note.evidence else ""
        images += " [hidden]" if note.hidden else ""
        print(f"#{note.id:<5} {note.created:%Y-%m-%d %H:%M}  {KIND_LABELS[note.kind]:<14} {who:<20} {note.title}{images}")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    store = _store(args)
    member_id, title = None, "All notes"
    if args.handle:
        member = store.member_by_handle(args.handle)
        if not member:
            print(f"Unknown person @{args.handle}", file=sys.stderr)
            return 1
        member_id, title = member.id, f"Notes: {member.label}"
    notes = store.notes(member_id, query=args.filter or "", since=args.since, until=args.until)
    extra = []
    if member_id is not None:
        linked = {e.id for n in notes for e in n.evidence}
        extra = [
            e for e in store.evidence_list(member_id, query=args.filter or "", since=args.since, until=args.until)
            if e.id not in linked
        ]
    out = Path(args.output).expanduser() if args.output else None
    text = notes_to_markdown(
        title, notes, out.parent.resolve() if out else None, extra_evidence=extra, include_hidden=args.include_hidden
    )
    hidden = 0 if args.include_hidden else sum(note.hidden for note in notes)
    if out:
        out.write_text(text)
        print(f"Wrote {len(notes) - hidden} notes to {out}")
    else:
        sys.stdout.write(text)
    if hidden:
        print(f"{hidden} note(s) tagged #hide left out (--include-hidden to include them)", file=sys.stderr)
    return 0


def cmd_team(args: argparse.Namespace) -> int:
    store = _store(args)
    try:
        if args.action == "add":
            if not args.handle:
                print("usage: boss team add HANDLE [NAME...] [--role ROLE]", file=sys.stderr)
                return 1
            member = store.add_member(args.handle, " ".join(args.name), args.role or "")
            print(f"Added @{member.handle} ({member.name})")
            return 0
        if args.action in ("archive", "restore"):
            member = store.member_by_handle(args.handle or "")
            if not member:
                print(f"Unknown person @{args.handle}", file=sys.stderr)
                return 1
            store.set_member_active(member.id, args.action == "restore")
            print(f"{args.action.title()}d @{member.handle}")
            return 0
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1
    if args.handles:
        for member in store.members():
            print(member.handle)
        return 0
    counts = store.note_counts()
    for member in store.members(include_inactive=True):
        status = "" if member.active else "  (archived)"
        role = f" — {member.role}" if member.role else ""
        count = counts.get(member.id, 0)
        print(f"@{member.handle:<16} {member.name}{role}  [{count} note{'' if count == 1 else 's'}]{status}")
    return 0


def _print_evidence(evidence) -> None:
    who = " ".join(f"@{m.handle}" for m in evidence.people) or "-"
    notes = ",".join(f"#{i}" for i in evidence.note_ids) or "unlinked"
    print(f"#{evidence.id:<5} {evidence.taken:%Y-%m-%d %H:%M}  {who:<20} notes: {notes:<10} {evidence.title}")


def cmd_evidence(args: argparse.Namespace) -> int:
    store = _store(args)
    action = args.evidence_action
    if action == "add":
        files = _gather_images(store, args)
        if files is None:
            return 1
        if not files:
            print("usage: boss evidence add FILE... (or --latest / --paste)", file=sys.stderr)
            return 1
        note = None
        if args.note is not None and (note := store.note(args.note)) is None:
            print(f"No note #{args.note}", file=sys.stderr)
            return 1
        caption = _with_tags(args.caption or "", args.tag)
        for path, move in files:
            evidence, unknown, created = store.import_evidence(
                path, caption=caption, handles=args.member or [], move=move
            )
            if note:
                store.link_evidence(note.id, evidence.id)
            print(("Imported" if created else "Already in library:") + f" evidence #{evidence.id}"
                  + (f", linked to note #{note.id}" if note else "") + f" ({path.name})")
            if unknown:
                print(f"warning: not on your team: {', '.join('@' + h for h in unknown)}", file=sys.stderr)
        return 0
    if action == "list":
        member_id = None
        if args.handle:
            member = store.member_by_handle(args.handle)
            if not member:
                print(f"Unknown person @{args.handle}", file=sys.stderr)
                return 1
            member_id = member.id
        for evidence in store.evidence_list(member_id, unlinked=args.unlinked, query=args.filter or ""):
            _print_evidence(evidence)
        return 0
    evidence = store.evidence(args.id)
    if evidence is None:
        print(f"No evidence #{args.id}", file=sys.stderr)
        return 1
    if action in ("link", "unlink"):
        if store.note(args.note_id) is None:
            print(f"No note #{args.note_id}", file=sys.stderr)
            return 1
        (store.link_evidence if action == "link" else store.unlink_evidence)(args.note_id, evidence.id)
        print(f"{action.title()}ed evidence #{evidence.id} {'to' if action == 'link' else 'from'} note #{args.note_id}")
        return 0
    if action == "edit":
        caption = evidence.caption if args.caption is None else args.caption
        handles = [m.handle for m in evidence.members] + (args.member or [])
        evidence, unknown = store.update_evidence(evidence.id, caption=_with_tags(caption, args.tag), handles=handles)
        _print_evidence(evidence)
        if unknown:
            print(f"warning: not on your team: {', '.join('@' + h for h in unknown)}", file=sys.stderr)
        return 0
    if action == "open":
        from .desktop import open_path

        open_path(evidence.path)
        return 0
    return 1


def cmd_init(args: argparse.Namespace) -> int:
    sys.stdout.write((Path(__file__).parent / "boss.zsh").read_text())
    return 0


def cmd_where(args: argparse.Namespace) -> int:
    print(_store(args).root)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="boss",
        description="Performance notes for your team. Run without a command to open the TUI.",
    )
    parser.add_argument("--home", help="data directory (default: $BOSS_HOME or ~/.local/share/boss)")
    sub = parser.add_subparsers(dest="command")

    def evidence_flags(p: argparse.ArgumentParser) -> None:
        p.add_argument("-l", "--latest", action="store_true", help="add your newest screenshot (⌘⇧4) as evidence")
        p.add_argument("-p", "--paste", action="store_true", help="add the image on the clipboard as evidence")

    def note_flags(p: argparse.ArgumentParser) -> None:
        p.add_argument("-m", "--member", action="append", metavar="HANDLE", help="tag a person (repeatable)")
        p.add_argument("-k", "--kind", default="note", choices=list(KIND_LABELS), help="type of note")
        p.add_argument("-t", "--tag", action="append", metavar="TAG", help="add #TAG (repeatable; shell-safe)")
        evidence_flags(p)
        p.add_argument("-a", "--attach", action="append", metavar="FILE", help="add an image file as evidence")
        p.add_argument("-E", "--evidence", action="append", type=int, metavar="ID", help="link existing evidence")

    add = sub.add_parser(
        "add", help="add a note; with no text it opens $EDITOR (vim), '-' reads stdin"
    )
    add.add_argument("text", nargs="*", help="e.g. '@alice ran a great retro #facilitation' (single quotes in zsh)")
    note_flags(add)
    add.add_argument("-e", "--edit", action="store_true", help="write/finish the note in $EDITOR")
    add.set_defaults(func=cmd_add)

    quick = sub.add_parser("quick", help="pop up a native dialog to write a note (bind to a global hotkey)")
    note_flags(quick)
    quick.set_defaults(func=cmd_quick)

    ls = sub.add_parser("list", help="print recent notes")
    ls.add_argument("handle", nargs="?")
    ls.add_argument("-f", "--filter", help="text, #tag, @handle, kind:win")
    ls.add_argument("--since", help="YYYY-MM-DD")
    ls.add_argument("-n", "--limit", type=int, default=50)
    ls.set_defaults(func=cmd_list)

    export = sub.add_parser("export", help="export notes as Markdown (e.g. for a performance review)")
    export.add_argument("handle", nargs="?")
    export.add_argument("-o", "--output", help="file to write (default: stdout)")
    export.add_argument("-f", "--filter", help="text, #tag, @handle, kind:win")
    export.add_argument("--since", help="YYYY-MM-DD")
    export.add_argument("--until", help="YYYY-MM-DD (exclusive)")
    export.add_argument(
        "--include-hidden", action="store_true", help="include notes/evidence tagged #hide (e.g. for backups)"
    )
    export.set_defaults(func=cmd_export)

    team = sub.add_parser("team", help="list or manage team members")
    team.add_argument("action", nargs="?", choices=["list", "add", "archive", "restore"], default="list")
    team.add_argument("handle", nargs="?")
    team.add_argument("name", nargs="*")
    team.add_argument("--role")
    team.add_argument("--handles", action="store_true", help=argparse.SUPPRESS)  # for shell completion
    team.set_defaults(func=cmd_team)

    evidence = sub.add_parser("evidence", help="manage evidence (screenshots) and link it to notes")
    ev = evidence.add_subparsers(dest="evidence_action", required=True)
    ev_add = ev.add_parser("add", help="import image(s) into the evidence library")
    ev_add.add_argument("files", nargs="*", metavar="FILE")
    evidence_flags(ev_add)
    ev_add.add_argument("-c", "--caption", help="what the evidence shows (@handle / #tag work)")
    ev_add.add_argument("-m", "--member", action="append", metavar="HANDLE", help="tag a person (repeatable)")
    ev_add.add_argument("-t", "--tag", action="append", metavar="TAG", help="add #TAG (repeatable)")
    ev_add.add_argument("-n", "--note", type=int, metavar="NOTE_ID", help="link to this note")
    ev_list = ev.add_parser("list", help="list evidence")
    ev_list.add_argument("handle", nargs="?")
    ev_list.add_argument("-u", "--unlinked", action="store_true", help="only evidence not linked to a note")
    ev_list.add_argument("-f", "--filter", help="text, #tag, @handle")
    for name, text in (("link", "link evidence to a note"), ("unlink", "unlink evidence from a note")):
        p = ev.add_parser(name, help=text)
        p.add_argument("id", type=int, metavar="EVIDENCE_ID")
        p.add_argument("note_id", type=int, metavar="NOTE_ID")
    ev_edit = ev.add_parser("edit", help="caption or tag evidence")
    ev_edit.add_argument("id", type=int, metavar="EVIDENCE_ID")
    ev_edit.add_argument("-c", "--caption")
    ev_edit.add_argument("-m", "--member", action="append", metavar="HANDLE")
    ev_edit.add_argument("-t", "--tag", action="append", metavar="TAG")
    ev_open = ev.add_parser("open", help="open evidence in your image viewer")
    ev_open.add_argument("id", type=int, metavar="EVIDENCE_ID")
    evidence.set_defaults(func=cmd_evidence)

    init = sub.add_parser("init", help='print zsh integration: add  eval "$(boss init zsh)"  to ~/.zshrc')
    init.add_argument("shell", choices=["zsh"])
    init.set_defaults(func=cmd_init)

    where = sub.add_parser("where", help="print the data directory")
    where.set_defaults(func=cmd_where)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "since", None):
        datetime.fromisoformat(args.since)  # validate early
    if args.command is None:
        from .app import BossApp

        BossApp(_store(args)).run()
        return 0
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
