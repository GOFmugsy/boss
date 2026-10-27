"""Screenshot capture, clipboard images and opening files with the OS viewer."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
from pathlib import Path


# Hotkey launchers (Shortcuts.app, Raycast, skhd...) run with a minimal PATH, so look in the
# usual macOS/Homebrew locations as well.
EXTRA_PATHS = ["/usr/sbin", "/usr/bin", "/opt/homebrew/bin", "/usr/local/bin"]


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


class CaptureError(RuntimeError):
    """Raised when no usable screenshot / clipboard tool is available."""


def _region_commands(dest: Path) -> list[list[str]]:
    if sys.platform == "darwin":
        # -i interactive (drag a region, or press space to pick a window), -x no sound
        return [["screencapture", "-i", "-x", str(dest)]]
    return [
        ["gnome-screenshot", "-a", "-f", str(dest)],
        ["spectacle", "-r", "-b", "-n", "-o", str(dest)],
        ["maim", "-s", str(dest)],
        ["scrot", "-s", "-o", str(dest)],
        ["import", str(dest)],  # ImageMagick: click-and-drag
    ]


async def _run(*args: str, stdout=None) -> int:
    process = await asyncio.create_subprocess_exec(
        *args, stdout=stdout or asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
    )
    return await process.wait()


def _captured(dest: Path) -> Path | None:
    return dest if dest.exists() and dest.stat().st_size > 0 else None


async def capture_region(dest: Path) -> Path | None:
    """Let the user select a screen region. Returns ``None`` if they cancelled."""
    if sys.platform.startswith("linux") and shutil.which("grim") and shutil.which("slurp"):
        process = await asyncio.create_subprocess_exec(
            "slurp", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
        geometry, _ = await process.communicate()
        if process.returncode != 0:
            return None
        await _run("grim", "-g", geometry.decode().strip(), str(dest))
        return _captured(dest)
    if sys.platform.startswith("linux") and shutil.which("flameshot"):
        with dest.open("wb") as handle:
            process = await asyncio.create_subprocess_exec(
                "flameshot", "gui", "--raw", stdout=handle, stderr=asyncio.subprocess.DEVNULL
            )
            await process.wait()
        return _captured(dest)
    for command in _region_commands(dest):
        if executable := tool(command[0]):
            await _run(executable, *command[1:])
            return _captured(dest)
    raise CaptureError(
        "No screenshot tool found (macOS: screencapture; Linux: grim+slurp, flameshot, "
        "gnome-screenshot, spectacle, maim or scrot)"
    )


async def paste_image(dest: Path) -> Path | None:
    """Save the image on the clipboard to ``dest``. Returns ``None`` if there isn't one."""
    if sys.platform == "darwin":
        if pngpaste := tool("pngpaste"):
            await _run(pngpaste, str(dest))
            return _captured(dest)
        script = [
            "set png to (the clipboard as «class PNGf»)",
            f'set f to open for access (POSIX file "{dest}") with write permission',
            "write png to f",
            "close access f",
        ]
        args = [arg for line in script for arg in ("-e", line)]
        await _run(tool("osascript") or "osascript", *args)
        return _captured(dest)
    for command in (
        ["wl-paste", "--no-newline", "--type", "image/png"],
        ["xclip", "-selection", "clipboard", "-t", "image/png", "-o"],
    ):
        if shutil.which(command[0]):
            with dest.open("wb") as handle:
                process = await asyncio.create_subprocess_exec(
                    *command, stdout=handle, stderr=asyncio.subprocess.DEVNULL
                )
                code = await process.wait()
            if code == 0 and _captured(dest):
                return dest
            dest.unlink(missing_ok=True)
            return None
    try:
        from PIL import ImageGrab  # optional

        image = ImageGrab.grabclipboard()
    except Exception:
        image = None
    if image is not None and hasattr(image, "save"):
        image.save(dest, "PNG")
        return dest
    raise CaptureError("No clipboard image tool found (Linux: install wl-clipboard or xclip)")


def open_path(path: Path) -> None:
    """Open a file with the desktop's default application."""
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
