"""Regression tests for installing the Dock icon before AppKit activation."""

from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch


class MacWindowIconTests(unittest.TestCase):
    @unittest.skipIf(sys.platform == "win32", "AppKit host module requires os.uname")
    def test_native_app_uses_dock_icon_before_activation_in_both_launch_modes(self):
        import macos_window

        app = object()
        image = object()
        cocoa = Mock()
        cocoa.cls.side_effect = lambda name: name
        cocoa.text.side_effect = lambda text: text
        cocoa.send.side_effect = lambda receiver, method, *args: {
            "sharedApplication": app,
            "initWithContentsOfFile:": image,
        }.get(method, Mock())

        for frozen in (False, True):
            with (
                self.subTest(frozen=frozen),
                patch.object(sys, "frozen", frozen, create=True),
                patch.object(macos_window, "_Cocoa", return_value=cocoa),
            ):
                cocoa.send.reset_mock()
                window = macos_window._Window(Path("mapper.html"), Mock(), Mock())
                window.run()

                calls = cocoa.send.call_args_list
                methods = [call.args[1] for call in calls]
                load = methods.index("initWithContentsOfFile:")
                install = methods.index("setApplicationIconImage:")
                self.assertEqual(calls[load].args[-1][1], str(macos_window._APP_ICON))
                self.assertEqual(calls[install].args[0], app)
                self.assertEqual(calls[install].args[-1][1], image)
                self.assertLess(load, install)
                self.assertLess(install, methods.index("setActivationPolicy:"))
                self.assertLess(install, methods.index("finishLaunching"))
