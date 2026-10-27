"""Inline image previews (Kitty graphics / Sixel / Unicode blocks via textual-image)."""

from __future__ import annotations

import os
from pathlib import Path

from rich.text import Text
from textual.widget import Widget
from textual.widgets import Static

# textual-image probes the terminal when imported, so this module must be imported before
# the app starts (boss.app imports it at module level).
ImageWidget = None
if not os.environ.get("BOSS_NO_IMAGES"):
    try:
        from textual_image.widget import Image as ImageWidget  # type: ignore[no-redef]
    except Exception:  # optional dependency
        ImageWidget = None


def image_preview(path: Path, classes: str = "preview") -> Widget:
    """A widget showing the image, or a short placeholder if it can't be shown."""
    if not path.exists():
        return Static(Text(f"missing file: {path}", style="red"), classes=classes)
    if ImageWidget is not None:
        try:
            return ImageWidget(path, classes=classes)
        except Exception:
            pass
    return Static(Text(f"▣ {path.name}  (o opens it; install boss[images] for previews)", style="dim"), classes=classes)
