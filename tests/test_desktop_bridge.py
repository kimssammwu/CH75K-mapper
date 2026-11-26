import json
from pathlib import Path
from queue import Queue
import sys
from tempfile import TemporaryDirectory
from time import monotonic, sleep
from types import SimpleNamespace
import unittest
from unittest.mock import call, patch

from ch75k import DeviceSnapshot
from desktop_app import DesktopBridge
from hid_common import HidrawAccessError, HidrawError
from settings import MACRO_END
from test_settings_link import BASE, factory_image


PROFILE = {"keys": {"bottom-center": "Ctrl+C, @120, Ctrl+V"}}


class DesktopBridgeTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(TemporaryDirectory())
        self.draft = Path(directory) / "draft.json"
        self.bridge = DesktopBridge(self.draft)
        self.addCleanup(self.bridge.close)
        self.sound = self.enterContext(patch("desktop_app._play_connection_sound"))
        self.request_id = 0
        self.progress = []

    def send(self, endpoint, data=None):
        self.request_id += 1
        request_id = self.request_id
        self.bridge.dispatch(json.dumps({"id": request_id, "endpoint": endpoint, "data": data or {}}))
        deadline = monotonic() + 2
        while monotonic() < deadline:
            replies, _ = self.bridge.poll()
            for reply_id, reply in replies:
                if reply_id == request_id:
                    if "progress" not in reply:
                        return reply
                    self.progress.append(reply["progress"])
            sleep(0.005)
        self.fail(f"No response to {endpoint}")

    def test_preview_and_draft_round_trip(self):
        self.assertIn("Ctrl+C, @120, Ctrl+V", self.send("preview", {"profile": PROFILE})["message"])
        self.assertEqual(self.send("draft-load"), {"profile": {"keys": {}}, "revision": 0})
        self.assertEqual(self.send("draft", {"profile": PROFILE, "revision": 2}), {})
        self.assertEqual(self.send("draft", {"profile": {"keys": {}}, "revision": 1}), {})
        self.assertEqual(self.send("draft-load"), {"profile": PROFILE, "revision": 2})
        self.assertEqual(json.loads(self.draft.read_text()), PROFILE)

    def test_connection_sound_plays_only_when_a_device_becomes_present(self):
        devices = [("/dev/hidraw8", "CH75K")]
        played = Queue()
        self.sound.side_effect = lambda: played.put(True)
        with patch("desktop_app.find_devices", side_effect=[[], devices, devices, [], devices, devices]):
            self.send("devices")
            self.sound.assert_not_called()
            self.send("devices")
            self.assertTrue(played.get(timeout=1))
            self.send("devices")
            self.send("devices")
            self.sound.assert_called_once()
            self.send("devices")
            self.assertTrue(played.get(timeout=1))
            self.send("devices")
            self.assertEqual(self.sound.call_count, 2)

    def test_device_operations_require_exact_confirmation_and_current_device(self):
        devices = [("/dev/hidraw9", "CH75K")]
        with (
            patch("desktop_app.find_devices", return_value=devices),
            patch("desktop_app.read_profile", return_value="device setup") as read,
            patch("desktop_app.apply_profile", return_value="applied") as apply,
        ):
            self.assertEqual(self.send("devices"), {"devices": [{"id": devices[0][0], "label": "CH75K"}]})
            self.assertIn("error", self.send("read", {"device": devices[0][0], "confirmed": 1}))
            self.assertIn("error", self.send("apply", {"device": "/dev/hidraw0", "confirmed": True, "profile": PROFILE}))
            read.assert_not_called()
            apply.assert_not_called()
            confirmed = {"device": devices[0][0], "confirmed": True}
            self.assertEqual(self.send("read", confirmed), {"message": "device setup"})
            self.assertEqual(self.send("apply", {**confirmed, "profile": PROFILE}), {"message": "applied"})
            read.assert_called_once_with(devices[0][0])
            apply.assert_called_once_with(devices[0][0], PROFILE)

    def test_quit_acknowledges_before_closing_and_unknown_endpoint_is_denied(self):
        self.assertIn("error", self.send("unknown"))
        self.assertIn("종료", self.send("quit")["message"])
        self.assertTrue(self.bridge.stopped.wait(1))
        self.assertEqual(self.bridge.poll(), ([], True))

    def test_reviewed_changes_are_the_only_hardware_write_and_token_is_single_use(self):
        path = Path("/dev/hidraw9")
        device = SimpleNamespace(path=path)
        image = bytes(factory_image()[BASE:MACRO_END])
        snapshot = DeviceSnapshot(device, BASE, image)
        editor = {"keys": {"top-left": "Esc", "bottom-center": "Ctrl+C"}}
        arguments = {"device": str(path), "confirmed": True, "profile": editor}
        with (
            patch("desktop_app.find_devices", return_value=[(str(path), "CH75K")]),
            patch("desktop_app.capture_profile", return_value=snapshot) as capture,
            patch("desktop_app.apply_profile", return_value="saved") as apply,
        ):
            self.assertIn("error", self.send("sync-apply", arguments))
            comparison = self.send("compare", arguments)
            capture.assert_called_once_with(str(path))
            apply.assert_not_called()
            self.assertEqual(comparison["changes"], {"keys": {"top-left": "Esc"}})
            write = {**arguments, "token": comparison["token"]}
            self.assertIn("error", self.send("sync-apply", {
                **write, "profile": {"keys": {"top-left": "F13"}},
            }))
            apply.assert_not_called()
            self.assertEqual(self.send("sync-apply", write), {"message": "saved"})
            apply.assert_called_once_with(device, comparison["changes"], expected=snapshot,
                                          preserve_slots_for=frozenset())
            self.assertIn("error", self.send("sync-apply", write))
            apply.assert_called_once()

    def test_disconnect_invalidates_a_pending_comparison(self):
        path = Path("/dev/hidraw9")
        device = SimpleNamespace(path=path)
        snapshot = DeviceSnapshot(device, BASE, bytes(factory_image()[BASE:MACRO_END]))
        arguments = {"device": str(path), "confirmed": True, "profile": {"keys": {"top-left": "Esc"}}}
        with (
            patch("desktop_app.find_devices", side_effect=[[(str(path), "CH75K")], [], [(str(path), "CH75K")]]),
            patch("desktop_app.capture_profile", return_value=snapshot),
            patch("desktop_app.apply_profile") as apply,
        ):
            comparison = self.send("compare", arguments)
            self.assertEqual(self.send("devices"), {"devices": []})
            self.assertIn("error", self.send("sync-apply", {**arguments, "token": comparison["token"]}))
            apply.assert_not_called()

    @unittest.skipIf(sys.platform == "win32", "The Linux hidraw permission helper uses POSIX APIs.")
    def test_linux_permission_retries_read_or_apply_once_with_original_device(self):
        device_path = "/dev/hidraw8"
        device = SimpleNamespace(path=Path(device_path))
        for endpoint, operation, arguments in (
            ("read", "read_profile", ()),
            ("apply", "apply_profile", (PROFILE,)),
        ):
            with (
                self.subTest(endpoint=endpoint),
                patch("desktop_app.sys.platform", "linux"),
                patch("desktop_app.find_devices", return_value=[(device_path, "CH75K")]),
                patch(f"desktop_app.{operation}", side_effect=[HidrawAccessError(device), "done"]) as run,
                patch("linux_permissions.request_hid_access") as authorize,
            ):
                self.progress.clear()
                self.assertEqual(self.send(endpoint, {"device": device_path, "confirmed": True, "profile": PROFILE}),
                                 {"message": "done"})
                self.assertEqual(run.call_args_list, [call(device_path, *arguments), call(device, *arguments)])
                authorize.assert_called_once_with(device)
                self.assertEqual(len(self.progress), 1)
                self.assertIn("시스템 인증", self.progress[0])

    @unittest.skipIf(sys.platform == "win32", "The Linux hidraw permission helper uses POSIX APIs.")
    def test_permission_cancellation_and_workspace_errors_do_not_retry(self):
        device_path = "/dev/hidraw8"
        device = SimpleNamespace(path=Path(device_path))
        arguments = {"device": device_path, "confirmed": True, "profile": PROFILE}
        with (
            patch("desktop_app.sys.platform", "linux"),
            patch("desktop_app.find_devices", return_value=[(device_path, "CH75K")]),
            patch("desktop_app.apply_profile", side_effect=HidrawAccessError(device)) as apply,
            patch("linux_permissions.request_hid_access", side_effect=HidrawError("시스템 인증을 취소했습니다.")) as authorize,
        ):
            self.assertIn("인증을 취소", self.send("apply", arguments)["error"])
            apply.assert_called_once_with(device_path, PROFILE)
            authorize.assert_called_once_with(device)
            authorize.reset_mock()
            apply.reset_mock(side_effect=True)
            apply.side_effect = PermissionError("백업 파일 권한 오류")
            self.assertIn("백업 파일 권한 오류", self.send("apply", arguments)["error"])
            apply.assert_called_once_with(device_path, PROFILE)
            authorize.assert_not_called()

    @unittest.skipIf(sys.platform == "win32", "The Linux hidraw permission helper uses POSIX APIs.")
    def test_linux_open_error_after_authorization_does_not_prompt_again(self):
        device_path = "/dev/hidraw8"
        device = SimpleNamespace(path=Path(device_path))
        with (
            patch("desktop_app.sys.platform", "linux"),
            patch("desktop_app.find_devices", return_value=[(device_path, "CH75K")]),
            patch("desktop_app.read_profile", side_effect=HidrawAccessError(device)) as read,
            patch("linux_permissions.request_hid_access") as authorize,
        ):
            self.assertIn("접근 권한", self.send("read", {"device": device_path, "confirmed": True})["error"])
            self.assertEqual(read.call_args_list, [call(device_path), call(device)])
            authorize.assert_called_once_with(device)

    @unittest.skipIf(sys.platform == "win32", "The Linux hidraw permission helper uses POSIX APIs.")
    def test_elevated_rule_matches_the_manual_install_rule(self):
        from linux_permissions import _ROOT_PROGRAM, _RULE

        rules = Path(__file__).resolve().parents[1] / "70-ch75k-native.rules"
        self.assertEqual(_RULE, rules.read_bytes())
        compile(_ROOT_PROGRAM, "CH75K privileged routine", "exec")
