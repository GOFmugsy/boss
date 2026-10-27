"""Desktop integration: finding screenshots you took, the clipboard, dialogs, opening files.

boss never takes screenshots itself. Take them with the OS (macOS: ⌘⇧4 / ⌘⇧5) and import
them as evidence: from the screenshots folder, by path / drag-and-drop, or from the clipboard.
"""

from __future__ import annotations

import asyncio
import os
import shlex
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".tif", ".tiff", ".bmp"}

# Hotkey launchers (Shortcuts.app, Raycast, skhd...) run with a minimal PATH, so look in the
# usual macOS/Homebrew locations as well.
EXTRA_PATHS = ["/usr/sbin", "/usr/bin", "/opt/homebrew/bin", "/usr/local/bin"]


class DesktopError(RuntimeError):
    """Raised when a needed OS tool isn't available."""


def tool(name: str) -> str | None:
    """Absolute path of a command-line tool, or None."""
    found = shutil.which(name)
    if found:
        return found
    for folder in EXTRA_PATHS:
        candidate = Path(folder) / name
        if candidate.is_file():
            return str(candidate)
    return None


def is_image(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_SUFFIXES


# ------------------------------------------------------------ screenshots folder


def screenshot_dirs() -> list[Path]:
    """Folders where the OS saves screenshots, most likely first.

    ``BOSS_SCREENSHOTS_DIR`` (``:``-separated) overrides detection. On macOS the location
    chosen in the Screenshot app (``defaults read com.apple.screencapture location``) is used,
    falling back to the Desktop.
    """
    if env := os.environ.get("BOSS_SCREENSHOTS_DIR"):
        return [Path(p).expanduser() for p in env.split(":") if p]
    home = Path.home()
    candidates: list[Path] = []
    if sys.platform == "darwin":
        defaults = tool("defaults")
        if defaults:
            result = subprocess.run(
                [defaults, "read", "com.apple.screencapture", "location"], capture_output=True, text=True
            )
            if result.returncode == 0 and result.stdout.strip():
                candidates.append(Path(result.stdout.strip()).expanduser())
        candidates += [home / "Desktop", home / "Pictures" / "Screenshots"]
    else:
        candidates += [home / "Pictures" / "Screenshots", home / "Pictures", home / "Desktop"]
    seen: list[Path] = []
    for folder in candidates:
        if folder.is_dir() and folder not in seen:
            seen.append(folder)
    return seen


def recent_images(folders: list[Path] | None = None, *, days: int = 30, limit: int = 100) -> list[Path]:
    """Images in the screenshot folders modified in the last ``days``, newest first."""
    cutoff = (datetime.now() - timedelta(days=days)).timestamp()
    found: list[tuple[float, Path]] = []
    for folder in folders if folders is not None else screenshot_dirs():
        try:
            entries = list(folder.iterdir())
        except OSError:
            continue
        for path in entries:
            if not is_image(path) or path.name.startswith("."):
                continue
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            if mtime >= cutoff and path.is_file():
                found.append((mtime, path))
    found.sort(key=lambda item: item[0], reverse=True)
    return [path for _, path in found[:limit]]


def latest_screenshot() -> Path | None:
    images = recent_images(limit=1)
    return images[0] if images else None


# ------------------------------------------------------------------ file paths


def parse_paths(text: str) -> list[Path]:
    """Paths typed, pasted or drag-and-dropped into the terminal (one or many).

    Terminals paste dropped files as shell-escaped (``My\\ Shot.png``) or quoted paths,
    separated by spaces; ``file://`` URLs are accepted too.
    """
    text = text.strip()
    if not text:
        return []
    if Path(text).expanduser().exists():  # a single path containing unescaped spaces
        return [Path(text).expanduser()]
    try:
        parts = shlex.split(text)
    except ValueError:
        parts = [text]
    paths = []
    for part in parts:
        if part.startswith("file://"):
            part = part[len("file://"):]
        paths.append(Path(part).expanduser())
    return paths


# -------------------------------------------------------------------- clipboard


async def _run(*args: str) -> int:
    process = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
    )
    return await process.wait()


def _saved(dest: Path) -> Path | None:
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    dest.unlink(missing_ok=True)
    return None


async def paste_image(dest: Path) -> Path | None:
    """Save the image currently on the clipboard (e.g. from ⌃⌘⇧4) to ``dest``.

    Returns ``None`` if the clipboard holds no image.
    """
    if sys.platform == "darwin":
        if pngpaste := tool("pngpaste"):
            await _run(pngpaste, str(dest))
            return _saved(dest)
        script = [
            "set png to (the clipboard as «class PNGf»)",
            f'set f to open for access (POSIX file "{dest}") with write permission',
            "write png to f",
            "close access f",
        ]
        await _run(tool("osascript") or "osascript", *[arg for line in script for arg in ("-e", line)])
        return _saved(dest)
    for command in (
        ["wl-paste", "--no-newline", "--type", "image/png"],
        ["xclip", "-selection", "clipboard", "-t", "image/png", "-o"],
    ):
        if executable := tool(command[0]):
            with dest.open("wb") as handle:
                process = await asyncio.create_subprocess_exec(
                    executable, *command[1:], stdout=handle, stderr=asyncio.subprocess.DEVNULL
                )
                code = await process.wait()
            if code != 0:
                dest.unlink(missing_ok=True)
                return None
            return _saved(dest)
    raise DesktopError("No clipboard image tool found (Linux: install wl-clipboard or xclip)")


# ------------------------------------------------------------- opening, dialogs


def open_path(path: Path) -> None:
    """Open a file with the desktop's default application (Preview on macOS)."""
    if sys.platform == "darwin":
        command = [tool("open") or "open", str(path)]
    elif sys.platform == "win32":
        command = ["cmd", "/c", "start", "", str(path)]
    else:
        command = ["xdg-open", str(path)]
    subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def prompt_text(title: str, message: str) -> str | None:
    """Ask for a line of text with a native GUI dialog (for global-hotkey quick capture)."""
    if sys.platform == "darwin":
        script = (
            f'text returned of (display dialog "{message}" default answer "" '
            f'with title "{title}" buttons {{"Cancel", "Save"}} default button "Save")'
        )
        result = subprocess.run([tool("osascript") or "osascript", "-e", script], capture_output=True, text=True)
        return result.stdout.rstrip("\n") if result.returncode == 0 else None
    for command in (
        ["zenity", "--entry", f"--title={title}", f"--text={message}"],
        ["kdialog", "--title", title, "--inputbox", message],
    ):
        if shutil.which(command[0]):
            result = subprocess.run(command, capture_output=True, text=True)
            return result.stdout.rstrip("\n") if result.returncode == 0 else None
    try:
        return input(f"{message} ")
    except EOFError:
        return None
