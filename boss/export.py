"""Render notes and their evidence as Markdown (for review prep, sharing, or backups)."""

from __future__ import annotations

import os
from collections import Counter
from datetime import datetime
from pathlib import Path

from .store import KIND_LABELS, Evidence, Note


def _link(evidence: Evidence, out_dir: Path | None) -> str:
    """Image links are relative to ``out_dir`` when given, otherwise absolute."""
    if out_dir:
        return Path(os.path.relpath(evidence.path, out_dir)).as_posix()
    return evidence.path.as_posix()


def _evidence_block(evidence: Evidence, out_dir: Path | None) -> list[str]:
    caption = evidence.caption.strip() or evidence.source_name or evidence.path.name
    lines = [f"![{caption.splitlines()[0]}]({_link(evidence, out_dir)})", ""]
    meta = f"*Evidence #{evidence.id} · taken {evidence.taken:%b %d, %Y %H:%M}*"
    lines += [f"{meta}  ", caption, ""] if evidence.caption.strip() else [meta, ""]
    return lines


def notes_to_markdown(
    title: str,
    notes: list[Note],
    out_dir: Path | None = None,
    *,
    extra_evidence: list[Evidence] | None = None,
) -> str:
    """Render ``notes`` oldest first, grouped by month, each followed by its evidence.

    ``extra_evidence`` (e.g. screenshots tagged to a person but not linked to any of these
    notes) is listed at the end.
    """
    extra_evidence = extra_evidence or []
    evidence_count = len({e.id for n in notes for e in n.evidence}) + len(extra_evidence)
    lines = [
        f"# {title}",
        "",
        f"_Exported {datetime.now():%Y-%m-%d %H:%M} · {len(notes)} notes · {evidence_count} evidence_",
        "",
    ]
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
        for evidence in note.evidence:
            lines += _evidence_block(evidence, out_dir)
    if extra_evidence:
        lines += ["## Other evidence", "", "_Not linked to any of the notes above._", ""]
        for evidence in sorted(extra_evidence, key=lambda e: e.taken_at):
            lines += _evidence_block(evidence, out_dir)
    return "\n".join(lines).rstrip() + "\n"
