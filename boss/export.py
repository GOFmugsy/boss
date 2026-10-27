"""Render notes as Markdown (for review prep, sharing, or backups)."""

from __future__ import annotations

import os
from collections import Counter
from datetime import datetime
from pathlib import Path

from .store import KIND_LABELS, Note


def notes_to_markdown(title: str, notes: list[Note], out_dir: Path | None = None) -> str:
    """Render ``notes`` oldest-first, grouped by month.

    Image links are relative to ``out_dir`` when given, otherwise absolute.
    """
    lines = [f"# {title}", "", f"_Exported {datetime.now():%Y-%m-%d %H:%M} · {len(notes)} notes_", ""]
    if notes:
        counts = Counter(note.kind for note in notes)
        summary = ", ".join(f"{KIND_LABELS.get(kind, kind)}: {count}" for kind, count in counts.most_common())
        first, last = notes[-1].created, notes[0].created
        lines += [f"**{first:%b %d, %Y} – {last:%b %d, %Y}** · {summary}", ""]
    month = None
    for note in reversed(notes):
        created = note.created
        if f"{created:%Y-%m}" != month:
            month = f"{created:%Y-%m}"
            lines += [f"## {created:%B %Y}", ""]
        who = " ".join(f"@{m.handle}" for m in note.members)
        heading = f"### {created:%a %b %d, %H:%M} · {KIND_LABELS.get(note.kind, note.kind)}"
        lines += [heading + (f" · {who}" if who else ""), "", note.body.strip(), ""]
        for attachment in note.attachments:
            target = (
                Path(os.path.relpath(attachment.path, out_dir)).as_posix()
                if out_dir
                else attachment.path.as_posix()
            )
            lines += [f"![{attachment.path.name}]({target})", ""]
    return "\n".join(lines).rstrip() + "\n"
