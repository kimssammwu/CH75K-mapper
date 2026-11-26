"""Publish CH75K's icon and identity to freedesktop application launchers."""

from __future__ import annotations

import os
from pathlib import Path
import sys
from tempfile import NamedTemporaryFile


APP_ID = "local.ch75k.mapper"
APP_ICON = Path(__file__).with_name("assets") / "app-icon.png"
_MANAGED = "X-CH75K-Managed=true"


def _desktop_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace("\t", "\\t").replace("\r", "\\r")


def _desktop_command(*command: str) -> str:
    # Desktop entries prohibit '=' in the executable token, and GLib checks
    # its existence before expanding '%%'. Pass these paths as arguments to
    # a fixed command so the path itself is never evaluated as shell code.
    if any(char in command[0] for char in "=%"):
        command = ("/bin/sh", "-c", 'exec "$@"', APP_ID, *command)
    arguments = []
    for argument in command:
        quoted = "".join("\\" + char if char in '\\"$`' else char
                         for char in argument.replace("%", "%%"))
        arguments.append('"' + quoted + '"')
    return _desktop_value(" ".join(arguments))


def _write_if_changed(path: Path, value: bytes) -> None:
    try:
        if path.read_bytes() == value:
            return
    except FileNotFoundError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, prefix="." + path.name, delete=False) as temporary:
        candidate = Path(temporary.name)
        try:
            temporary.write(value)
            temporary.close()
            candidate.replace(path)
        finally:
            candidate.unlink(missing_ok=True)


def register_desktop_icon() -> None:
    """Register a user-local desktop entry before the first window is mapped."""
    try:
        data_home = Path(os.environ.get("XDG_DATA_HOME", ""))
        if not data_home.is_absolute():
            data_home = Path.home() / ".local" / "share"
        entry = data_home / "applications" / f"{APP_ID}.desktop"
        # Only CH75K's managed entry may be replaced on a later launch.
        if entry.is_file() and _MANAGED.encode() not in entry.read_bytes().splitlines():
            return
        icon = data_home / APP_ID / APP_ICON.name
        executable = str(Path(sys.executable).absolute())
        command = (executable,)
        if not getattr(sys, "frozen", False):
            command += (str(Path(__file__).with_name("desktop_app.py").absolute()),)
        entry_text = (
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=CH75K Studio\n"
            f"Exec={_desktop_command(*command)}\n"
            f"TryExec={_desktop_value(executable)}\n"
            f"Icon={_desktop_value(str(icon))}\n"
            f"StartupWMClass={APP_ID}\n"
            "Terminal=false\n"
            "Categories=Utility;\n"
            f"{_MANAGED}\n"
        )
        _write_if_changed(icon, APP_ICON.read_bytes())
        _write_if_changed(entry, entry_text.encode("utf-8"))
    except (OSError, UnicodeError):
        # A read-only home or a missing asset should not prevent the window opening.
        pass
