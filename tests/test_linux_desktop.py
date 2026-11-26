import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from linux_desktop import APP_ID, APP_ICON, register_desktop_icon


@unittest.skipUnless(sys.platform.startswith("linux"), "Linux desktop integration")
class LinuxDesktopTests(unittest.TestCase):
    def test_portable_launcher_updates_its_path_and_uses_a_persistent_icon(self):
        with tempfile.TemporaryDirectory() as directory:
            data_home = Path(directory)
            entry = data_home / "applications" / f"{APP_ID}.desktop"
            icon = data_home / APP_ID / "app-icon.png"
            with (
                patch.dict(os.environ, {"XDG_DATA_HOME": directory}),
                patch.object(sys, "frozen", True, create=True),
                patch.object(sys, "executable", str(data_home / "CH75K 100%")),
            ):
                register_desktop_icon()
                self.assertIn('Exec="/bin/sh" "-c" ', entry.read_text())
                self.assertIn(f'"{data_home}/CH75K 100%%"\n', entry.read_text())
                self.assertIn(f"StartupWMClass={APP_ID}\n", entry.read_text())
                self.assertIn(f"Icon={icon}\n", entry.read_text())
                self.assertEqual(icon.read_bytes(), APP_ICON.read_bytes())
                original = (entry.stat().st_ino, icon.stat().st_ino)
                register_desktop_icon()
                self.assertEqual((entry.stat().st_ino, icon.stat().st_ino), original)
                with patch.object(sys, "executable", str(data_home / "moved" / "CH75K")):
                    register_desktop_icon()
                self.assertIn(f'Exec="{data_home}/moved/CH75K"\n', entry.read_text())
                self.assertEqual(icon.stat().st_ino, original[1])

    def test_existing_user_launcher_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            data_home = Path(directory)
            entry = data_home / "applications" / f"{APP_ID}.desktop"
            entry.parent.mkdir()
            personal_entry = "[Desktop Entry]\nType=Application\nName=Personal shortcut\nExec=ch75k\n"
            entry.write_text(personal_entry)
            with patch.dict(os.environ, {"XDG_DATA_HOME": directory}):
                register_desktop_icon()
            self.assertEqual(entry.read_text(), personal_entry)
            self.assertFalse((data_home / APP_ID).exists())
