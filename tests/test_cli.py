import os
import stat
import subprocess
from pathlib import Path

import pytest

from boss.cli import main
from boss.store import Store


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "data"
    monkeypatch.setenv("BOSS_HOME", str(root))
    Store(root).add_member("alice", "Alice Smith")
    return root


def fake_editor(tmp_path: Path, body: str, exit_code: int = 0, write: bool = True) -> str:
    """A stand-in for vim: optionally prepends text to the file (like typing then :wq)."""
    script = tmp_path / "fake-editor"
    action = f'printf "%s\\n" {body!r} | cat - "$f" > "$f.new" && mv "$f.new" "$f"' if write else ":"
    script.write_text(f'#!/bin/sh\nfor f; do :; done  # file is the last argument\n{action}\nexit {exit_code}\n')
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)


def notes(home: Path) -> list[str]:
    return [n.body for n in Store(home).notes()]


def test_add_opens_editor_like_git_commit(home, tmp_path, monkeypatch):
    monkeypatch.setenv("EDITOR", fake_editor(tmp_path, "@alice wrote the RFC #design"))
    assert main(["add", "--edit", "-k", "win"]) == 0
    assert notes(home) == ["@alice wrote the RFC #design"]  # template lines removed


@pytest.mark.parametrize("exit_code,write", [(0, False), (1, True)])
def test_editor_quit_without_saving_or_cq_cancels(home, tmp_path, monkeypatch, exit_code, write):
    monkeypatch.setenv("EDITOR", fake_editor(tmp_path, "nope", exit_code=exit_code, write=write))
    assert main(["add", "--edit"]) == 1
    assert notes(home) == []
    assert not list((home / "staging").glob("note-*"))  # temp file cleaned up


def test_editor_command_with_arguments(home, tmp_path, monkeypatch):
    monkeypatch.setenv("VISUAL", fake_editor(tmp_path, "from visual") + " --wait")
    monkeypatch.setenv("EDITOR", "false")
    assert main(["add", "-e"]) == 0
    assert notes(home) == ["from visual"]


def test_tag_flag_is_shell_safe(home):
    assert main(["add", "@alice", "great", "job", "today?", "-t", "win", "-t", "#q4"]) == 0
    assert notes(home) == ["@alice great job today? #win #q4"]
    assert Store(home).notes()[0].tags == ["q4", "win"]


def test_init_zsh_is_valid_zsh(capsys):
    assert main(["init", "zsh"]) == 0
    script = capsys.readouterr().out
    assert "alias boss='noglob boss'" in script and "compdef _boss boss" in script
    if os.path.exists("/bin/zsh"):
        subprocess.run(["/bin/zsh", "-n"], input=script, text=True, check=True)


def test_team_handles_for_completion(home, capsys):
    assert main(["team", "--handles"]) == 0
    assert capsys.readouterr().out.split() == ["alice"]


def test_data_dir_is_private(tmp_path):
    root = tmp_path / "fresh"
    Store(root)
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
